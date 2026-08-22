"""
EV ChargeOps — Transformação e carga do dataset simulado

Lê os CSVs gerados por 04_generate_sessions.py (data/raw/simulado/) e
carrega no Postgres, na ordem correta de dependência das foreign keys:

    dim_plans -> dim_points -> dim_users -> dim_vehicles -> dim_dates
    -> fct_sessoes

dim_geography e fct_invoices ficam de fora deste script: dim_geography
depende da reconciliação com as APIs externas (Open Charge Map, IBGE,
RENAVAM — Etapa 2), ainda não implementada; fct_invoices é gerada pelo
motor de rateio (Etapa 6), que ainda não existe.

Uso:
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_DB=evchargeops
    export POSTGRES_USER=evchargeops
    export POSTGRES_PASSWORD=changeme   # ou o valor real do seu .env
    python scripts/05_load_star_schema.py
"""

import os
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "simulado"

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evchargeops"),
    "user": os.getenv("POSTGRES_USER", "evchargeops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

DIAS_SEMANA_PT = {
    0: "segunda", 1: "terça", 2: "quarta", 3: "quinta",
    4: "sexta", 5: "sábado", 6: "domingo",
}
MESES_PT = {
    1: "janeiro", 2: "fevereiro", 3: "março", 4: "abril",
    5: "maio", 6: "junho", 7: "julho", 8: "agosto",
    9: "setembro", 10: "outubro", 11: "novembro", 12: "dezembro",
}


def build_dim_dates(sessions_df: pd.DataFrame) -> pd.DataFrame:
    """Deriva dim_dates a partir das datas distintas presentes em
    sessions.csv — não existe um dates.csv próprio, a dimensão de data é
    sempre derivada, nunca fonte primária de dado de negócio."""
    dates = pd.to_datetime(sessions_df["session_date"]).dt.date.unique()
    rows = []
    for d in sorted(dates):
        rows.append({
            "session_date": d,
            "ref_month": d.replace(day=1),
            "day_of_week": DIAS_SEMANA_PT[d.weekday()],
            "month_name": MESES_PT[d.month],
            "year": d.year,
            "quarter": (d.month - 1) // 3 + 1,
            "is_weekend": d.weekday() >= 5,
        })
    return pd.DataFrame(rows)


def load_table(conn, df: pd.DataFrame, table: str, columns: list[str]) -> None:
    """Insere um DataFrame numa tabela via execute_values (mais eficiente
    que INSERT linha a linha). ON CONFLICT DO NOTHING para permitir
    reexecução do script sem duplicar dados (idempotência básica)."""
    if df.empty:
        print(f"  {table}: nada para inserir (DataFrame vazio).")
        return

    records = df[columns].where(pd.notnull(df[columns]), None).values.tolist()
    placeholders = ", ".join(columns)
    query = f"INSERT INTO {table} ({placeholders}) VALUES %s ON CONFLICT DO NOTHING"

    with conn.cursor() as cur:
        execute_values(cur, query, records)
    conn.commit()
    print(f"  {table}: {len(records)} linhas processadas.")


def main() -> None:
    print("Lendo CSVs simulados de:", DATA_DIR)
    plans_df = pd.read_csv(DATA_DIR / "plans.csv")
    points_df = pd.read_csv(DATA_DIR / "points.csv")
    users_df = pd.read_csv(DATA_DIR / "users.csv")
    vehicles_df = pd.read_csv(DATA_DIR / "vehicles.csv")
    sessions_df = pd.read_csv(DATA_DIR / "sessions.csv")

    dates_df = build_dim_dates(sessions_df)

    print("\nConectando ao Postgres em", f"{DB_CONFIG['host']}:{DB_CONFIG['port']}/{DB_CONFIG['dbname']}...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        print("\nCarregando dimensões...")
        load_table(conn, plans_df, "dim_plans", [
            "plan_type", "description", "rate_per_min", "rate_per_min_discounted",
            "monthly_fee", "fixed_cost_monthly", "active",
        ])
        # dim_points ainda não tem geo_id resolvido (depende da Etapa 2/reconciliação
        # com dim_geography, que este script não carrega) — inserido como NULL.
        points_df["geo_id"] = None
        load_table(conn, points_df, "dim_points", [
            "point_id", "model", "location", "power_kw", "protocol",
            "status", "installed_at", "geo_id",
        ])
        load_table(conn, users_df, "dim_users", [
            "user_id", "name", "email", "unit", "rfid_tag", "active",
            "user_type", "plan_type", "point_id",
        ])
        load_table(conn, vehicles_df, "dim_vehicles", [
            "vehicle_id", "user_id", "vehicle_type", "is_primary",
        ])
        load_table(conn, dates_df, "dim_dates", [
            "session_date", "ref_month", "day_of_week", "month_name",
            "year", "quarter", "is_weekend",
        ])

        print("\nCarregando fato...")
        load_table(conn, sessions_df, "fct_sessoes", [
            "session_id", "user_id", "vehicle_id", "point_id", "session_date",
            "plan_type", "duration_min", "kwh_delivered", "status", "anomaly_flag",
        ])

        print("\nCarga concluída com sucesso.")

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fct_sessoes")
            total = cur.fetchone()[0]
            print(f"Total de linhas em fct_sessoes após a carga: {total}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()