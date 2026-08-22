"""
EV ChargeOps — Motor de rateio

Lê as sessões já carregadas em fct_sessoes e calcula fct_invoices para
cada usuário/mês, aplicando as fórmulas dos 3 planos de cobrança e os
casos excepcionais definidos na Sprint 01 (Frente 3):

  pay_per_use          -> fatura = duracao_total_min * rate_per_min
                           (usuário sem sessão no mês: sem cobrança, sem fatura)

  assinatura            -> fatura = monthly_fee + (duracao_total_min * rate_per_min_discounted)
                           (usuário sem sessão no mês: cobra só a taxa fixa)

  pacote_condominial     -> fatura = (fixed_cost_monthly / nº usuários do pacote no mês)
                                       + (duracao_total_min * rate_per_min_discounted)

Casos excepcionais:
  - Sessão interrompida: cobrada normalmente pelo tempo/kWh até o corte
    (já reflete isso o valor de duration_min/kwh_delivered gravado na sessão).
  - Sessão com anomaly_flag=true: entra no cálculo normalmente, mas a
    fatura inteira do usuário naquele mês é marcada com status='revisao'
    em vez de 'pendente', para checagem manual antes do envio.

Gera fct_invoices para todo o período já presente em fct_sessoes (mar-ago/2026).

Uso:
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_DB=evchargeops
    export POSTGRES_USER=evchargeops
    export POSTGRES_PASSWORD=<sua senha>
    python scripts/06_billing_engine.py
"""

import os

import psycopg2
from psycopg2.extras import execute_values, RealDictCursor

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evchargeops"),
    "user": os.getenv("POSTGRES_USER", "evchargeops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}


def fetch_plans(conn) -> dict:
    """Carrega as tarifas de cada plano num dict indexado por plan_type."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT plan_type, rate_per_min, rate_per_min_discounted,
                   monthly_fee, fixed_cost_monthly
            FROM dim_plans
            WHERE active = TRUE
        """)
        return {row["plan_type"]: row for row in cur.fetchall()}


def fetch_session_aggregates(conn) -> list[dict]:
    """Agrega as sessões por usuário/mês/plano — a granularidade que a
    fatura precisa. Também carrega se há alguma sessão anômala no mês
    (para marcar a fatura como 'revisao')."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT
                s.user_id,
                d.ref_month,
                s.plan_type,
                SUM(s.duration_min) AS total_duration_min,
                SUM(s.kwh_delivered) AS total_kwh,
                BOOL_OR(s.anomaly_flag) AS has_anomaly
            FROM fct_sessoes s
            JOIN dim_dates d ON d.session_date = s.session_date
            GROUP BY s.user_id, d.ref_month, s.plan_type
            ORDER BY d.ref_month, s.user_id
        """)
        return cur.fetchall()


def fetch_all_users_with_plans(conn) -> list[dict]:
    """Todos os usuários ativos e seu plano — necessário para o cenário
    'usuário sem sessão no mês', que não aparece na agregação de sessões
    mas ainda pode precisar de fatura (caso assinatura/pacote, que cobram
    taxa fixa independentemente do uso)."""
    with conn.cursor(cursor_factory=RealDictCursor) as cur:
        cur.execute("""
            SELECT user_id, plan_type
            FROM dim_users
            WHERE active = TRUE
        """)
        return cur.fetchall()


def fetch_distinct_months(conn) -> list:
    with conn.cursor() as cur:
        cur.execute("SELECT DISTINCT ref_month FROM dim_dates ORDER BY ref_month")
        return [row[0] for row in cur.fetchall()]


def calculate_invoice_amount(plan_type: str, plans: dict, duration_min: float,
                              condominio_users_count: int) -> float:
    """Aplica a fórmula do plano. condominio_users_count só é usado para
    pacote_condominial (rateio igualitário do custo fixo)."""
    plan = plans[plan_type]

    if plan_type == "pay_per_use":
        return round(float(duration_min) * float(plan["rate_per_min"]), 2)

    if plan_type == "assinatura":
        fixed = float(plan["monthly_fee"])
        variable = float(duration_min) * float(plan["rate_per_min_discounted"])
        return round(fixed + variable, 2)

    if plan_type == "pacote_condominial":
        fixed_share = float(plan["fixed_cost_monthly"]) / max(1, condominio_users_count)
        variable = float(duration_min) * float(plan["rate_per_min_discounted"])
        return round(fixed_share + variable, 2)

    raise ValueError(f"Plano desconhecido: {plan_type}")


def build_invoices(session_aggregates: list[dict], all_users: list[dict],
                    plans: dict, months: list) -> list[dict]:
    # Índice de agregados por (user_id, ref_month) para lookup rápido.
    agg_index = {(row["user_id"], row["ref_month"]): row for row in session_aggregates}

    # Conta quantos usuários distintos têm sessão no pacote_condominial,
    # por mês — usado para ratear o custo fixo.
    condominio_users_by_month: dict = {}
    for row in session_aggregates:
        if row["plan_type"] == "pacote_condominial":
            condominio_users_by_month.setdefault(row["ref_month"], set()).add(row["user_id"])

    invoices = []
    invoice_counter = 1

    for month in months:
        for user in all_users:
            user_id = user["user_id"]
            plan_type = user["plan_type"]
            agg = agg_index.get((user_id, month))

            has_usage = agg is not None
            duration_min = float(agg["total_duration_min"]) if has_usage else 0.0
            total_kwh = float(agg["total_kwh"]) if has_usage else 0.0
            has_anomaly = bool(agg["has_anomaly"]) if has_usage else False

            # Cenário excepcional: usuário sem uso no mês.
            #   - pay_per_use: sem cobrança -> sem fatura.
            #   - assinatura / pacote_condominial: cobra a taxa fixa mesmo
            #     sem uso (conforme regra da Sprint 01).
            if not has_usage and plan_type == "pay_per_use":
                continue

            condominio_count = len(condominio_users_by_month.get(month, set())) or 1
            total_amount = calculate_invoice_amount(
                plan_type, plans, duration_min, condominio_count
            )

            status = "revisao" if has_anomaly else "pendente"

            invoices.append({
                "invoice_id": f"I{invoice_counter:06d}",
                "user_id": user_id,
                "ref_month": month,
                "plan_type": plan_type,
                "total_kwh": round(total_kwh, 3),
                "total_amount": total_amount,
                "status": status,
            })
            invoice_counter += 1

    return invoices


def load_invoices(conn, invoices: list[dict]) -> None:
    if not invoices:
        print("Nenhuma fatura para inserir.")
        return

    columns = ["invoice_id", "user_id", "ref_month", "plan_type",
               "total_kwh", "total_amount", "status"]
    records = [[inv[c] for c in columns] for inv in invoices]
    placeholders = ", ".join(columns)
    query = f"INSERT INTO fct_invoices ({placeholders}) VALUES %s ON CONFLICT DO NOTHING"

    with conn.cursor() as cur:
        execute_values(cur, query, records)
    conn.commit()
    print(f"fct_invoices: {len(records)} faturas inseridas.")


def print_summary(invoices: list[dict]) -> None:
    print(f"\nTotal de faturas geradas: {len(invoices)}")

    by_plan = {}
    by_status = {}
    total_amount = 0.0
    for inv in invoices:
        by_plan[inv["plan_type"]] = by_plan.get(inv["plan_type"], 0) + 1
        by_status[inv["status"]] = by_status.get(inv["status"], 0) + 1
        total_amount += inv["total_amount"]

    print("\nFaturas por plano:")
    for plan, count in by_plan.items():
        print(f"  {plan}: {count}")

    print("\nFaturas por status:")
    for status, count in by_status.items():
        print(f"  {status}: {count}")

    print(f"\nValor total faturado (todos os meses): R$ {total_amount:,.2f}")


def main() -> None:
    print("Conectando ao Postgres em", f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        print("\nCarregando planos, sessões agregadas e usuários...")
        plans = fetch_plans(conn)
        session_aggregates = fetch_session_aggregates(conn)
        all_users = fetch_all_users_with_plans(conn)
        months = fetch_distinct_months(conn)

        print(f"  {len(plans)} planos, {len(session_aggregates)} combinações usuário/mês/plano com uso, "
              f"{len(all_users)} usuários, {len(months)} meses distintos.")

        print("\nCalculando faturas...")
        invoices = build_invoices(session_aggregates, all_users, plans, months)

        print("\nInserindo em fct_invoices...")
        load_invoices(conn, invoices)

        print_summary(invoices)

    finally:
        conn.close()


if __name__ == "__main__":
    main()