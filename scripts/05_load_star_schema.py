"""
EV ChargeOps — Transformação e carga do dataset simulado

Lê os CSVs gerados por 04_generate_sessions.py (data/raw/simulado/) e
carrega no Postgres, na ordem correta de dependência das foreign keys:

    dim_plans -> dim_points -> dim_users -> dim_vehicles -> dim_dates
    -> fct_sessoes

dim_geography e fct_invoices ficam de fora deste script: dim_geography
depende da reconciliação com as APIs externas (Open Charge Map, IBGE,
RENAVAM, Etapa 2), ainda não implementada; fct_invoices é gerada pelo
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


# Deriva dim_dates das datas distintas em sessions.csv. Não existe um
# dates.csv próprio, a dimensão de data é sempre derivada.
def build_dim_dates(sessions_df: pd.DataFrame) -> pd.DataFrame:
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


# Insere um DataFrame numa tabela via execute_values. ON CONFLICT DO
# NOTHING permite reexecutar o script sem duplicar dados.
def load_table(conn, df: pd.DataFrame, table: str, columns: list[str]) -> None:
    if df.empty:
        print(f"  {table}: nada para inserir.")
        return

    records = df[columns].where(pd.notnull(df[columns]), None).values.tolist()
    placeholders = ", ".join(columns)
    query = f"INSERT INTO {table} ({placeholders}) VALUES %s ON CONFLICT DO NOTHING"

    with conn.cursor() as cur:
        execute_values(cur, query, records)
    conn.commit()
    print(f"  {table}: {len(records)} linhas processadas.")


def main() -> None:
    plans_df = pd.read_csv(DATA_DIR / "plans.csv")
    points_df = pd.read_csv(DATA_DIR / "points.csv")
    users_df = pd.read_csv(DATA_DIR / "users.csv")
    vehicles_df = pd.read_csv(DATA_DIR / "vehicles.csv")
    sessions_df = pd.read_csv(DATA_DIR / "sessions.csv")

    dates_df = build_dim_dates(sessions_df)

    conn = psycopg2.connect(**DB_CONFIG)

    try:
        load_table(conn, plans_df, "dim_plans", [
            "plan_type", "description", "rate_per_min", "rate_per_min_discounted",
            "monthly_fee", "fixed_cost_monthly", "active",
        ])
        # geo_id ainda não resolvido (depende da reconciliação com
        # dim_geography, Etapa 2), inserido como NULL por enquanto.
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
        load_table(conn, sessions_df, "fct_sessoes", [
            "session_id", "user_id", "vehicle_id", "point_id", "session_date",
            "plan_type", "duration_min", "kwh_delivered", "status", "anomaly_flag",
        ])

        with conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM fct_sessoes")
            total = cur.fetchone()[0]
            print(f"\nCarga concluída. Total em fct_sessoes: {total}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()