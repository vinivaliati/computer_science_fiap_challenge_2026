"""
EV ChargeOps — Exportação de dados para o App do Usuário (Etapa 8b, ajuste)

O app do usuário (app/dashboard_usuario.py) foi publicado no Streamlit
Community Cloud, que não tem acesso ao Postgres local (roda em outra
máquina, na nuvem). Em vez de migrar o banco inteiro para um serviço na
nuvem, este script exporta só os dados que aquele app específico consome,
em CSV, para data/processed/. O Postgres continua sendo a fonte de verdade
para tudo o mais (Power BI, motor de rateio, notebooks).

Limitação assumida: os dados do app do usuário ficam estáticos até este
script rodar de novo -- se o motor de rateio gerar faturas novas ou o
dataset simulado mudar, é preciso reexportar e commitar os CSVs de novo
para o app publicado refletir a mudança.

Uso:
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_DB=evchargeops
    export POSTGRES_USER=evchargeops
    export POSTGRES_PASSWORD=<sua senha>
    python scripts/09_export_user_app_data.py
"""

import os
from pathlib import Path

import pandas as pd
import psycopg2

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "processed"

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evchargeops"),
    "user": os.getenv("POSTGRES_USER", "evchargeops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}


def export_users(conn) -> pd.DataFrame:
    query = """
        SELECT u.user_id, u.name, u.unit, u.plan_type, u.point_id,
               COUNT(v.vehicle_id) AS n_vehicles
        FROM dim_users u
        LEFT JOIN dim_vehicles v ON v.user_id = u.user_id
        GROUP BY u.user_id, u.name, u.unit, u.plan_type, u.point_id
        ORDER BY u.name
    """
    return pd.read_sql(query, conn)


def export_sessions(conn) -> pd.DataFrame:
    # Todas as sessões, não filtradas por usuário -- o app filtra em
    # memória depois de carregar o CSV, já que o volume total (milhares
    # de linhas) é leve o suficiente para isso.
    query = """
        SELECT s.user_id, s.session_id, s.session_date, d.ref_month,
               s.duration_min, s.kwh_delivered, s.status, s.anomaly_flag
        FROM fct_sessoes s
        JOIN dim_dates d ON d.session_date = s.session_date
        ORDER BY s.user_id, s.session_date
    """
    return pd.read_sql(query, conn)


def export_invoices(conn) -> pd.DataFrame:
    query = """
        SELECT user_id, invoice_id, ref_month, total_kwh, total_amount, status
        FROM fct_invoices
        ORDER BY user_id, ref_month
    """
    return pd.read_sql(query, conn)


def export_plans(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT * FROM dim_plans", conn)


def export_points(conn) -> pd.DataFrame:
    return pd.read_sql("SELECT point_id, power_kw FROM dim_points", conn)


def export_pacote_condominial_by_month(conn) -> pd.DataFrame:
    # Mesma lógica usada em render_recommendation_section: número de
    # usuários com sessão no pacote condominial, por mês -- necessário
    # para a simulação de rateio do custo fixo.
    query = """
        SELECT d.ref_month, COUNT(DISTINCT s.user_id) as n
        FROM fct_sessoes s
        JOIN dim_dates d ON d.session_date = s.session_date
        WHERE s.plan_type = 'pacote_condominial'
        GROUP BY d.ref_month
    """
    return pd.read_sql(query, conn)


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    print("Conectando ao Postgres...")
    conn = psycopg2.connect(**DB_CONFIG)

    try:
        exports = {
            "user_app_users.csv": export_users(conn),
            "user_app_sessions.csv": export_sessions(conn),
            "user_app_invoices.csv": export_invoices(conn),
            "user_app_plans.csv": export_plans(conn),
            "user_app_points.csv": export_points(conn),
            "user_app_pacote_condominial_by_month.csv": export_pacote_condominial_by_month(conn),
        }
    finally:
        conn.close()

    print("\nExportando arquivos:")
    for filename, df in exports.items():
        path = OUTPUT_DIR / filename
        df.to_csv(path, index=False)
        print(f"  {filename}: {len(df)} linhas -> {path}")

    print(f"\nExportação concluída em: {OUTPUT_DIR}")
    print(
        "Lembre de commitar esses arquivos junto com o app -- o Streamlit "
        "Community Cloud lê o repositório Git, não o Postgres local."
    )


if __name__ == "__main__":
    main()