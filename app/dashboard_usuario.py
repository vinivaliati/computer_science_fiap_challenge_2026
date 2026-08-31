"""
EV ChargeOps — App do Usuário (Etapa 8b)

MVP sem autenticação: um seletor de usuário simula "visualizar o perfil de um
morador específico". A seção de recomendação de plano é pensada do ponto de
vista do GESTOR — não é "você economizaria", é "este usuário é candidato a
tal plano, porque pertence a tal perfil de consumo".

Pré-requisito: os notebooks 01 (previsão) e 02 (perfis) precisam ter rodado
pelo menos uma vez, gerando os artefatos em models/output/. O Postgres
também precisa estar populado (sessões, faturas, planos).

Uso:
    streamlit run app/dashboard_usuario.py
"""

import os
from pathlib import Path

import joblib
import pandas as pd
import psycopg2
import streamlit as st

MODELS_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "models" / "output"

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evchargeops"),
    "user": os.getenv("POSTGRES_USER", "evchargeops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

st.set_page_config(page_title="EV ChargeOps — Perfil do Usuário", layout="wide")


# ────────────────────────────────────────────
# Carregamento (com cache)
# ────────────────────────────────────────────

@st.cache_resource
def get_connection():
    return psycopg2.connect(**DB_CONFIG)


@st.cache_data
def load_users() -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT u.user_id, u.name, u.unit, u.plan_type, u.point_id,
               COUNT(v.vehicle_id) AS n_vehicles
        FROM dim_users u
        LEFT JOIN dim_vehicles v ON v.user_id = u.user_id
        GROUP BY u.user_id, u.name, u.unit, u.plan_type, u.point_id
        ORDER BY u.name
    """
    return pd.read_sql(query, conn)


@st.cache_data
def load_user_sessions(user_id: str) -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT s.session_id, s.session_date, d.ref_month, s.duration_min,
               s.kwh_delivered, s.status, s.anomaly_flag
        FROM fct_sessoes s
        JOIN dim_dates d ON d.session_date = s.session_date
        WHERE s.user_id = %(user_id)s
        ORDER BY s.session_date
    """
    return pd.read_sql(query, conn, params={"user_id": user_id})


@st.cache_data
def load_user_invoices(user_id: str) -> pd.DataFrame:
    conn = get_connection()
    query = """
        SELECT invoice_id, ref_month, total_kwh, total_amount, status
        FROM fct_invoices
        WHERE user_id = %(user_id)s
        ORDER BY ref_month
    """
    return pd.read_sql(query, conn, params={"user_id": user_id})


@st.cache_data
def load_plans() -> pd.DataFrame:
    conn = get_connection()
    return pd.read_sql("SELECT * FROM dim_plans", conn)


@st.cache_resource
def load_forecast_models():
    kwh_path = MODELS_OUTPUT_DIR / "consumo_kwh_previsao.joblib"
    fatura_path = MODELS_OUTPUT_DIR / "fatura_valor_previsao.joblib"
    if not (kwh_path.exists() and fatura_path.exists()):
        return None
    return {"model_kwh": joblib.load(kwh_path), "model_fatura": joblib.load(fatura_path)}


@st.cache_resource
def load_cluster_data():
    clusters_path = MODELS_OUTPUT_DIR / "perfis_uso_clusters.csv"
    if not clusters_path.exists():
        return None
    return pd.read_csv(clusters_path)


# ────────────────────────────────────────────
# Simulação de fatura por plano (mesmas fórmulas do motor de rateio)
# ────────────────────────────────────────────

def simulate_invoice(plan_row: pd.Series, duration_total_min: float, n_users_pacote: int = 1) -> float:
    """Reaplica as fórmulas de sql/06_billing_engine.py para simular quanto
    o usuário pagaria em um plano diferente do atual, dado o mesmo padrão
    de uso (duração total mensal)."""
    plan_type = plan_row["plan_type"]

    if plan_type == "pay_per_use":
        return round(duration_total_min * float(plan_row["rate_per_min"]), 2)

    if plan_type == "assinatura":
        fixed = float(plan_row["monthly_fee"])
        variable = duration_total_min * float(plan_row["rate_per_min_discounted"])
        return round(fixed + variable, 2)

    if plan_type == "pacote_condominial":
        fixed_share = float(plan_row["fixed_cost_monthly"]) / max(1, n_users_pacote)
        variable = duration_total_min * float(plan_row["rate_per_min_discounted"])
        return round(fixed_share + variable, 2)

    return float("nan")


# ────────────────────────────────────────────
# Seção 1 — Cabeçalho do usuário
# ────────────────────────────────────────────

def render_header(user_row: pd.Series) -> None:
    st.write("### Perfil do usuário")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Nome", user_row["name"])
    col2.metric("Unidade", user_row["unit"])
    col3.metric("Plano atual", user_row["plan_type"].replace("_", " ").title())
    col4.metric("Veículos cadastrados", int(user_row["n_vehicles"]))


# ────────────────────────────────────────────
# Seção 2 — Fatura atual
# ────────────────────────────────────────────

def render_invoice_section(invoices_df: pd.DataFrame) -> None:
    st.write("### Fatura")
    if invoices_df.empty:
        st.info("Nenhuma fatura registrada para este usuário no período.")
        return

    latest = invoices_df.iloc[-1]
    col1, col2, col3 = st.columns(3)
    col1.metric("Mês de referência", pd.to_datetime(latest["ref_month"]).strftime("%B/%Y"))
    col2.metric("Valor da fatura mais recente", f"R$ {latest['total_amount']:.2f}")

    status_label = {"pendente": "Pendente", "paga": "Paga", "revisao": "Em revisão"}.get(latest["status"], latest["status"])
    col3.metric("Status", status_label)

    if latest["status"] == "revisao":
        st.warning(
            "Esta fatura está em revisão — o modelo de detecção de anomalias (aba "
            "correspondente no Dashboard de IA) sinalizou pelo menos uma sessão com "
            "consumo fora do padrão esperado neste mês."
        )


# ────────────────────────────────────────────
# Seção 3 — Histórico de sessões
# ────────────────────────────────────────────

def render_history_section(sessions_df: pd.DataFrame) -> None:
    st.write("### Histórico de consumo")
    if sessions_df.empty:
        st.info("Nenhuma sessão registrada para este usuário.")
        return

    monthly = sessions_df.groupby("ref_month").agg(
        kwh_total=("kwh_delivered", "sum"),
        n_sessoes=("session_id", "count"),
    ).reset_index()
    monthly["ref_month"] = pd.to_datetime(monthly["ref_month"]).dt.strftime("%b/%Y")

    st.bar_chart(monthly.set_index("ref_month")["kwh_total"])

    with st.expander("Ver sessões detalhadas"):
        st.dataframe(
            sessions_df[["session_date", "duration_min", "kwh_delivered", "status", "anomaly_flag"]],
            width='stretch', hide_index=True,
        )


# ────────────────────────────────────────────
# Seção 4 — Previsão do próximo mês
# ────────────────────────────────────────────

def render_forecast_section(sessions_df: pd.DataFrame, user_row: pd.Series, plans_df: pd.DataFrame) -> None:
    st.write("### Previsão para o próximo mês")

    models = load_forecast_models()
    if models is None:
        st.warning(
            "Modelo de previsão não encontrado em `models/output/`. Rode "
            "`models/01_model_consumption_forecast.ipynb` antes de usar esta seção."
        )
        return

    if sessions_df.empty:
        st.info("Sem histórico suficiente para gerar previsão.")
        return

    # Usa o último mês completo como base da previsão.
    last_month = sessions_df["ref_month"].max()
    last_month_data = sessions_df[sessions_df["ref_month"] == last_month]

    point_query_conn = get_connection()
    point_power = pd.read_sql(
        "SELECT power_kw FROM dim_points WHERE point_id = %(point_id)s",
        point_query_conn, params={"point_id": user_row["point_id"]},
    )
    power_kw = float(point_power["power_kw"].iloc[0]) if not point_power.empty else 11.0

    model_input = pd.DataFrame([{
        "kwh_mes_atual": last_month_data["kwh_delivered"].sum(),
        "duracao_media_min": last_month_data["duration_min"].mean(),
        "n_sessoes_mes": len(last_month_data),
        "tendencia_kwh": 0.0,
        "power_kw": power_kw,
        "plan_type": user_row["plan_type"],
    }])

    pred_kwh = models["model_kwh"].predict(model_input)[0]
    pred_fatura = models["model_fatura"].predict(model_input)[0]

    col1, col2 = st.columns(2)
    col1.metric("Consumo previsto (kWh)", f"{pred_kwh:.1f}")
    col2.metric("Fatura prevista (R$)", f"{pred_fatura:.2f}")
    st.caption(
        "Estimativa baseada no padrão de uso do último mês registrado, usando o "
        "modelo de regressão linear (ver Dashboard de IA para detalhes)."
    )


# ────────────────────────────────────────────
# Seção 5 — Recomendação de plano (visão do gestor)
# ────────────────────────────────────────────

CLUSTER_DESCRIPTIONS = {
    # Preenchido dinamicamente com base no perfil médio de cada cluster,
    # comparado à mediana geral -- ver build_cluster_description().
}


def build_cluster_description(cluster_id: int, clusters_df: pd.DataFrame) -> str:
    cluster_data = clusters_df[clusters_df["cluster"] == cluster_id]
    overall_median_kwh = clusters_df["kwh_medio_sessao"].median()
    cluster_kwh = cluster_data["kwh_medio_sessao"].mean()

    overall_median_freq = clusters_df["n_sessoes_total"].median()
    cluster_freq = cluster_data["n_sessoes_total"].mean()

    consumo_label = "alto consumo por sessão" if cluster_kwh > overall_median_kwh else "baixo consumo por sessão"
    freq_label = "alta frequência de uso" if cluster_freq > overall_median_freq else "uso esporádico"

    return f"{consumo_label}, {freq_label}"


def render_recommendation_section(user_row: pd.Series, sessions_df: pd.DataFrame, plans_df: pd.DataFrame) -> None:
    st.write("### Recomendação de plano (visão do gestor)")
    st.caption(
        "Esta seção simula o que este usuário pagaria em cada plano, dado o padrão de "
        "uso real, e contextualiza com o perfil de comportamento (cluster) — para apoiar "
        "a decisão comercial de oferecer upgrade/downgrade de plano."
    )

    clusters_df = load_cluster_data()

    if sessions_df.empty:
        st.info("Sem histórico suficiente para simular planos.")
        return

    # Duração total mensal média, usada como base de comparação entre planos.
    monthly_duration = sessions_df.groupby("ref_month")["duration_min"].sum()
    avg_monthly_duration = monthly_duration.mean()

    # Número médio de usuários ativos no pacote condominial por mês --
    # necessário para simular corretamente o rateio do custo fixo (mesma
    # lógica do motor de rateio real, sql/06_billing_engine.py: o
    # fixed_cost_monthly é dividido pelos usuários que tiveram sessão
    # NAQUELE mês, não pelo total cadastrado no plano -- por isso usamos a
    # média mensal como aproximação representativa, não a contagem fixa de
    # dim_users, que pode divergir do que ocorreu em meses específicos.
    conn = get_connection()
    n_pacote_query = """
        SELECT d.ref_month, COUNT(DISTINCT s.user_id) as n
        FROM fct_sessoes s
        JOIN dim_dates d ON d.session_date = s.session_date
        WHERE s.plan_type = 'pacote_condominial'
        GROUP BY d.ref_month
    """
    n_pacote_result = pd.read_sql(n_pacote_query, conn)
    n_users_pacote = max(1, round(n_pacote_result["n"].mean())) if not n_pacote_result.empty else 1

    simulations = []
    for _, plan_row in plans_df.iterrows():
        valor_simulado = simulate_invoice(plan_row, avg_monthly_duration, n_users_pacote)
        simulations.append({
            "plan_type": plan_row["plan_type"],
            "valor_simulado": valor_simulado,
        })
    sim_df = pd.DataFrame(simulations)

    current_plan_value = sim_df[sim_df["plan_type"] == user_row["plan_type"]]["valor_simulado"].iloc[0]
    sim_df["diferenca_vs_atual"] = sim_df["valor_simulado"] - current_plan_value

    st.write("#### Simulação de fatura mensal em cada plano")
    st.caption(
        f"Baseado na duração média mensal de uso: {avg_monthly_duration:.0f} minutos/mês. "
        f"A simulação do pacote condominial usa o rateio pela média de "
        f"{n_users_pacote} usuários ativos por mês nesse plano — o valor real "
        f"varia mês a mês conforme quantos usuários efetivamente carregam no período."
    )

    display_df = sim_df.copy()
    display_df["plan_type"] = display_df["plan_type"].str.replace("_", " ").str.title()
    display_df.columns = ["Plano", "Valor simulado (R$)", "Diferença vs. plano atual (R$)"]
    st.dataframe(display_df, width='stretch', hide_index=True)

    best_plan = sim_df.loc[sim_df["valor_simulado"].idxmin()]
    economia = current_plan_value - best_plan["valor_simulado"]

    if best_plan["plan_type"] != user_row["plan_type"] and economia > 1.0:
        st.success(
            f"**Candidato a mudança de plano.** No padrão de uso atual, o plano "
            f"**{best_plan['plan_type'].replace('_', ' ').title()}** resultaria em uma "
            f"fatura R$ {economia:.2f}/mês menor do que o plano atual "
            f"({user_row['plan_type'].replace('_', ' ').title()})."
        )
    else:
        st.info("O plano atual já é o mais vantajoso para este padrão de uso.")

    if clusters_df is not None:
        user_cluster_row = clusters_df[clusters_df["user_id"] == user_row["user_id"]]
        if not user_cluster_row.empty:
            cluster_id = int(user_cluster_row["cluster"].iloc[0])
            description = build_cluster_description(cluster_id, clusters_df)
            st.write("#### Contexto de comportamento")
            st.markdown(
                f"Este usuário pertence ao **cluster {cluster_id}**, caracterizado por "
                f"**{description}** (ver aba Perfis de Uso no Dashboard de IA para a "
                f"comparação completa entre clusters)."
            )
        else:
            st.caption(
                "Usuário não encontrado nos dados de clustering — rode "
                "`models/02_model_usage_profiles.ipynb` novamente se os dados mudaram."
            )
    else:
        st.caption(
            "Modelo de perfis não encontrado em `models/output/`. Rode "
            "`models/02_model_usage_profiles.ipynb` para habilitar o contexto de cluster."
        )


# ────────────────────────────────────────────
# Layout principal
# ────────────────────────────────────────────

st.title("EV ChargeOps — Perfil do Usuário")
st.caption(
    "visualizar seu perfil (simula a visão que o app do morador teria)."
)

try:
    users_df = load_users()
except Exception as e:
    st.error(
        f"Não foi possível conectar ao banco de dados: {e}\n\n"
        "Confirme se o Postgres está rodando e as variáveis de ambiente "
        "(POSTGRES_HOST, POSTGRES_PORT, etc.) estão configuradas."
    )
    st.stop()

selected_name = st.selectbox(
    "Selecionar usuário",
    users_df["name"].tolist(),
)
user_row = users_df[users_df["name"] == selected_name].iloc[0]

st.divider()

render_header(user_row)

sessions_df = load_user_sessions(user_row["user_id"])
invoices_df = load_user_invoices(user_row["user_id"])
plans_df = load_plans()

st.divider()
render_invoice_section(invoices_df)

st.divider()
render_history_section(sessions_df)

st.divider()
render_forecast_section(sessions_df, user_row, plans_df)

st.divider()
render_recommendation_section(user_row, sessions_df, plans_df)