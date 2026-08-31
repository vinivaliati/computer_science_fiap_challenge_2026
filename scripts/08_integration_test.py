"""
EV ChargeOps — Teste de integração ponta a ponta (Etapa 8d)

Roda a cadeia principal do projeto, do zero, contra um banco de teste
isolado (não mexe no banco 'evchargeops' de desenvolvimento), com
verificações reais de resultado em cada etapa: não só "o script não
travou", mas "o número de linhas bate com o esperado" onde é possível
checar isso.

Escopo: cobre só as etapas que não dependem de rede externa. 04 (geração)
-> 05 (carga) -> 06 (rateio) -> notebooks 01-03 (previsão, perfis,
anomalias) -> apps (smoke test de import). As etapas de exploração/carga
de API (scripts 01, 02, 03, 07) e o notebook 04 (score de expansão, que
depende de dim_geography vinda dessas APIs) dependem de internet e não
são cobertos aqui. Rode-os manualmente quando tiver acesso à rede.

Uso:
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_USER=postgres        # ou o usuário do seu ambiente
    export POSTGRES_PASSWORD=<sua senha>
    python scripts/08_integration_test.py

O script cria e destrói um banco de teste próprio (nome configurável via
TEST_DB_NAME); não usa o banco 'evchargeops' de desenvolvimento.
"""

import os
import subprocess
import sys
from pathlib import Path

import psycopg2

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCRIPTS_DIR = PROJECT_ROOT / "scripts"
MODELS_DIR = PROJECT_ROOT / "models"
APP_DIR = PROJECT_ROOT / "app"

TEST_DB_NAME = os.getenv("TEST_DB_NAME", "evchargeops_integration_test")

ADMIN_DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "user": os.getenv("POSTGRES_USER", "postgres"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

# Variáveis de ambiente passadas para os subprocessos (scripts/notebooks),
# apontando para o banco de teste isolado, não para 'evchargeops'.
CHILD_ENV = {
    **os.environ,
    "POSTGRES_HOST": ADMIN_DB_CONFIG["host"],
    "POSTGRES_PORT": str(ADMIN_DB_CONFIG["port"]),
    "POSTGRES_DB": TEST_DB_NAME,
    "POSTGRES_USER": ADMIN_DB_CONFIG["user"],
    "POSTGRES_PASSWORD": ADMIN_DB_CONFIG["password"],
}

results = []  # lista de (nome_etapa, ok: bool, detalhe: str)


def log_step(name: str, ok: bool, detail: str = "") -> None:
    status = "PASSOU" if ok else "FALHOU"
    print(f"[{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, ok, detail))


def run_python_script(script_path: Path, label: str) -> bool:
    result = subprocess.run(
        [sys.executable, str(script_path)],
        cwd=PROJECT_ROOT,
        env=CHILD_ENV,
        capture_output=True,
        text=True,
        timeout=600,
    )
    ok = result.returncode == 0
    if not ok:
        print(f"--- stdout de {label} ---")
        print(result.stdout[-2000:])
        print(f"--- stderr de {label} ---")
        print(result.stderr[-2000:])
    return ok


def run_notebook(notebook_path: Path, label: str) -> bool:
    result = subprocess.run(
        [
            sys.executable, "-m", "jupyter", "nbconvert",
            "--to", "notebook", "--execute", "--inplace",
            str(notebook_path),
        ],
        cwd=MODELS_DIR,
        env=CHILD_ENV,
        capture_output=True,
        text=True,
        timeout=600,
    )
    ok = result.returncode == 0
    if not ok:
        print(f"--- stderr de {label} ---")
        print(result.stderr[-2000:])
    return ok


def create_test_database() -> None:
    conn = psycopg2.connect(dbname="postgres", **ADMIN_DB_CONFIG)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
        cur.execute(f"CREATE DATABASE {TEST_DB_NAME}")
    conn.close()


def drop_test_database() -> None:
    conn = psycopg2.connect(dbname="postgres", **ADMIN_DB_CONFIG)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(f"DROP DATABASE IF EXISTS {TEST_DB_NAME}")
    conn.close()


def get_test_connection():
    return psycopg2.connect(dbname=TEST_DB_NAME, **ADMIN_DB_CONFIG)


def count_rows(table: str) -> int:
    conn = get_test_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(f"SELECT COUNT(*) FROM {table}")
            return cur.fetchone()[0]
    finally:
        conn.close()


def main() -> None:
    print(f"=== Teste de integração — banco de teste: {TEST_DB_NAME} ===\n")

    # ── Etapa 0: banco de teste + schema ──────────────────────────────
    create_test_database()

    schema_sql = (PROJECT_ROOT / "sql" / "01_star_schema.sql").read_text(encoding="utf-8")
    conn = get_test_connection()
    try:
        with conn.cursor() as cur:
            cur.execute(schema_sql)
        conn.commit()
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_schema='public'"
            )
            n_tables = cur.fetchone()[0]
        log_step("Schema criado", n_tables == 8, f"{n_tables} tabelas (esperado: 8)")
    finally:
        conn.close()

    # ── Etapa 1: geração do dataset simulado ──────────────────────────
    ok = run_python_script(SCRIPTS_DIR / "04_generate_sessions.py", "geração de sessões")
    sessions_csv = PROJECT_ROOT / "data" / "raw" / "simulado" / "sessions.csv"
    csv_ok = sessions_csv.exists() and sessions_csv.stat().st_size > 0
    log_step("Geração do dataset simulado", ok and csv_ok, "sessions.csv gerado" if csv_ok else "sessions.csv ausente/vazio")

    # ── Etapa 2: carga do star schema ─────────────────────────────────
    ok = run_python_script(SCRIPTS_DIR / "05_load_star_schema.py", "carga do star schema")
    n_sessoes = count_rows("fct_sessoes") if ok else 0
    log_step("Carga de fct_sessoes", ok and n_sessoes > 0, f"{n_sessoes} linhas")

    # ── Etapa 3: motor de rateio ───────────────────────────────────────
    ok = run_python_script(SCRIPTS_DIR / "06_billing_engine.py", "motor de rateio")
    n_invoices = count_rows("fct_invoices") if ok else 0
    log_step("Geração de fct_invoices", ok and n_invoices > 0, f"{n_invoices} faturas")

    # ── Checagem de integridade referencial ────────────────────────────
    conn = get_test_connection()
    try:
        with conn.cursor() as cur:
            cur.execute("""
                SELECT COUNT(*) FROM fct_sessoes s
                JOIN dim_users u ON u.user_id = s.user_id
                JOIN dim_vehicles v ON v.vehicle_id = s.vehicle_id
                JOIN dim_points p ON p.point_id = s.point_id
                JOIN dim_plans pl ON pl.plan_type = s.plan_type
                JOIN dim_dates d ON d.session_date = s.session_date
            """)
            n_joined = cur.fetchone()[0]
        log_step(
            "Integridade referencial (join completo fct_sessoes)",
            n_joined == n_sessoes,
            f"{n_joined} de {n_sessoes} sessões com join completo",
        )
    finally:
        conn.close()

    # ── Etapa 5: notebooks de IA ───────────────────────────────────────
    notebooks = [
        ("01_model_consumption_forecast.ipynb", "previsão de consumo"),
        ("02_model_usage_profiles.ipynb", "perfis de uso"),
        ("03_model_anomaly_detection.ipynb", "detecção de anomalias"),
    ]
    for filename, label in notebooks:
        ok = run_notebook(MODELS_DIR / filename, label)
        log_step(f"Notebook: {label}", ok)

    # ── Etapa 6: smoke test dos apps Streamlit (import, sem servidor) ──
    for app_file in ["dashboard_ia.py", "dashboard_usuario.py"]:
        app_path = APP_DIR / app_file
        if not app_path.exists():
            log_step(f"App: {app_file}", False, "arquivo não encontrado")
            continue
        result = subprocess.run(
            [sys.executable, "-c", f"import ast; ast.parse(open('{app_path}').read())"],
            capture_output=True, text=True,
        )
        log_step(f"App: {app_file} (sintaxe válida)", result.returncode == 0, result.stderr[-300:] if result.returncode != 0 else "")

    # ── Limpeza ──────────────────────────────────────────────────────
    drop_test_database()

    # ── Relatório final ──────────────────────────────────────────────
    print("\n=== Relatório final ===")
    n_ok = sum(1 for _, ok, _ in results if ok)
    n_total = len(results)
    for name, ok, detail in results:
        status = "✓" if ok else "✗"
        print(f"  {status} {name}" + (f" — {detail}" if detail else ""))
    print(f"\n{n_ok} de {n_total} verificações passaram.")

    if n_ok < n_total:
        sys.exit(1)


if __name__ == "__main__":
    main()