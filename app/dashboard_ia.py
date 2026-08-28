"""
EV ChargeOps — Dashboard de IA (Etapa 8a)

App Streamlit com 4 abas, uma por modelo treinado na Etapa 7:
  - Previsão de consumo (regressão linear)
  - Perfis de uso (K-Means)
  - Detecção de anomalias (Z-score)
  - Score de expansão (índice composto)

Pré-requisito: os 4 notebooks em models/ precisam ter rodado pelo menos uma
vez, gerando os artefatos em models/output/. Rode-os antes de abrir este app
(ver README de cada notebook para instruções).

Uso:
    streamlit run app/dashboard_ia.py
"""

from pathlib import Path

import joblib
import numpy as np
import pandas as pd
import plotly.express as px
import streamlit as st

MODELS_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "models" / "output"

st.set_page_config(page_title="EV ChargeOps — Dashboard de IA", layout="wide")


# ────────────────────────────────────────────
# Carregamento de artefatos (com cache)
# ────────────────────────────────────────────

def _artifact_path(filename: str) -> Path:
    return MODELS_OUTPUT_DIR / filename


def _missing_artifact_message(filename: str, notebook: str) -> None:
    st.error(
        f"Artefato **{filename}** não encontrado em `models/output/`.\n\n"
        f"Rode o notebook `models/{notebook}` pelo menos uma vez antes de "
        f"usar esta aba (ele salva o artefato automaticamente na última célula)."
    )


@st.cache_resource
def load_consumo_models():
    kwh_path = _artifact_path("consumo_kwh_previsao.joblib")
    fatura_path = _artifact_path("fatura_valor_previsao.joblib")
    training_path = _artifact_path("consumo_training_data.csv")
    if not (kwh_path.exists() and fatura_path.exists() and training_path.exists()):
        return None
    return {
        "model_kwh": joblib.load(kwh_path),
        "model_fatura": joblib.load(fatura_path),
        "training_data": pd.read_csv(training_path),
    }


@st.cache_resource
def load_perfis_model():
    model_path = _artifact_path("perfis_uso_kmeans.joblib")
    clusters_path = _artifact_path("perfis_uso_clusters.csv")
    if not (model_path.exists() and clusters_path.exists()):
        return None
    return {
        "pipeline": joblib.load(model_path),
        "clusters_data": pd.read_csv(clusters_path),
    }


@st.cache_resource
def load_anomalias_model():
    model_path = _artifact_path("deteccao_anomalias_zscore.joblib")
    scores_path = _artifact_path("deteccao_anomalias_scores.csv")
    if not (model_path.exists() and scores_path.exists()):
        return None
    return {
        "model": joblib.load(model_path),
        "scores_data": pd.read_csv(scores_path),
    }


@st.cache_resource
def load_expansao_model():
    model_path = _artifact_path("score_expansao_modelo.joblib")
    municipios_path = _artifact_path("score_expansao_municipios.csv")
    if not (model_path.exists() and municipios_path.exists()):
        return None
    return {
        "model": joblib.load(model_path),
        "municipios_data": pd.read_csv(municipios_path),
    }


# ────────────────────────────────────────────
# Aba 1 — Previsão de consumo
# ────────────────────────────────────────────

def render_previsao_tab():
    st.write("### Previsão de consumo do próximo mês")
    st.caption(
        "Estima o consumo (kWh) e o valor da fatura (R$) do próximo mês, com base "
        "no padrão de uso do mês atual. Ajuste os campos abaixo para simular um perfil."
    )

    artifacts = load_consumo_models()
    if artifacts is None:
        _missing_artifact_message(
            "consumo_kwh_previsao.joblib / fatura_valor_previsao.joblib",
            "01_model_consumption_forecast.ipynb",
        )
        return

    training_data = artifacts["training_data"]
    plan_types = sorted(training_data["plan_type"].dropna().unique())

    with st.container(border=True):
        st.write("#### Padrão de uso no mês atual")

        left_col, right_col = st.columns(2)

        with left_col:
            kwh_mes_atual = st.slider(
                "Consumo no mês atual (kWh)",
                min_value=0.0,
                max_value=float(training_data["kwh_mes_atual"].max()),
                value=float(training_data["kwh_mes_atual"].median()),
            )
            n_sessoes_mes = st.slider(
                "Número de sessões no mês",
                min_value=1,
                max_value=int(training_data["n_sessoes_mes"].max()),
                value=int(training_data["n_sessoes_mes"].median()),
            )
            tendencia_kwh = st.slider(
                "Tendência (kWh a mais/menos vs. mês anterior)",
                min_value=float(training_data["tendencia_kwh"].min()),
                max_value=float(training_data["tendencia_kwh"].max()),
                value=0.0,
            )

        with right_col:
            duracao_media_min = st.slider(
                "Duração média de sessão (min)",
                min_value=0.0,
                max_value=float(training_data["duracao_media_min"].max()),
                value=float(training_data["duracao_media_min"].median()),
            )
            power_kw = st.selectbox(
                "Potência do ponto de recarga habitual (kW)",
                sorted(training_data["power_kw"].unique()),
            )
            plan_type = st.selectbox("Plano de cobrança", plan_types)

    predict_button = st.button("Prever próximo mês", type="primary")

    if predict_button:
        model_input = pd.DataFrame([{
            "kwh_mes_atual": kwh_mes_atual,
            "duracao_media_min": duracao_media_min,
            "n_sessoes_mes": n_sessoes_mes,
            "tendencia_kwh": tendencia_kwh,
            "power_kw": power_kw,
            "plan_type": plan_type,
        }])

        pred_kwh = artifacts["model_kwh"].predict(model_input)[0]
        pred_fatura = artifacts["model_fatura"].predict(model_input)[0]

        col1, col2 = st.columns(2)
        col1.metric("Consumo previsto (kWh)", f"{pred_kwh:.1f}")
        col2.metric("Fatura prevista (R$)", f"{pred_fatura:.2f}")

    with st.expander("ℹ️ Como interpretar esta previsão"):
        st.markdown("""
        O modelo usa **regressão linear**: aprende a relação entre o padrão de uso do
        mês atual e o resultado do mês seguinte, a partir do histórico real de sessões.

        - Os valores previstos são **estimativas**, não garantias funcionam melhor
          quando o padrão de uso do usuário se mantém razoavelmente estável de um mês
          para o outro.
        - A **tendência** (kWh a mais/menos vs. mês anterior) ajuda o modelo a captar
          se o consumo está subindo ou caindo, não só o valor absoluto atual.
        - Este modelo tem precisão moderada nos dados de teste (erro médio de
          aproximadamente 80 kWh e R$ 260 na validação) trate a previsão como uma
          faixa de referência, não um valor exato.
        """)


# ────────────────────────────────────────────
# Aba 2 — Perfis de uso
# ────────────────────────────────────────────

def render_perfis_tab():
    st.write("### Perfis de uso (clustering)")
    st.caption(
        "Agrupa usuários por padrão de uso — frequência, horário, consumo médio, "
        "duração e proporção de uso em fim de semana."
    )

    artifacts = load_perfis_model()
    if artifacts is None:
        _missing_artifact_message("perfis_uso_kmeans.joblib", "02_model_usage_profiles.ipynb")
        return

    clusters_data = artifacts["clusters_data"]
    n_clusters = clusters_data["cluster"].nunique()

    st.info(
        f"O modelo identificou **{n_clusters} perfis** entre os usuários atuais. "
        "Veja abaixo a distribuição e o perfil médio de cada grupo."
    )

    col1, col2 = st.columns([1, 1])

    with col1:
        fig = px.scatter(
            clusters_data,
            x="n_sessoes_total",
            y="kwh_medio_sessao",
            color=clusters_data["cluster"].astype(str),
            hover_data=["hora_media_inicio", "duracao_media_min", "pct_fim_semana"],
            labels={"color": "Cluster", "n_sessoes_total": "Sessões no período", "kwh_medio_sessao": "kWh médio/sessão"},
            title="Usuários por frequência e consumo médio",
        )
        st.plotly_chart(fig, width='stretch')

    with col2:
        profile = clusters_data.groupby("cluster").agg(
            n_usuarios=("user_id", "count"),
            n_sessoes_total=("n_sessoes_total", "mean"),
            hora_media_inicio=("hora_media_inicio", "mean"),
            kwh_medio_sessao=("kwh_medio_sessao", "mean"),
            duracao_media_min=("duracao_media_min", "mean"),
            pct_fim_semana=("pct_fim_semana", "mean"),
        ).round(2)
        st.dataframe(profile, width='stretch')

    with st.expander("ℹ️ Como interpretar os perfis"):
        st.markdown("""
        O modelo usa **K-Means**: agrupa usuários com padrões de uso parecidos, sem
        conhecer de antemão quais são os grupos eles emergem dos dados.

        - No **gráfico de dispersão**, cada ponto é um usuário; a posição mostra
          frequência de uso (eixo X) e consumo médio por sessão (eixo Y); a cor indica
          o cluster atribuído. Passe o mouse sobre um ponto para ver mais detalhes
          (horário médio, duração, % de uso em fim de semana).
        - Na **tabela de perfil médio**, cada linha resume o comportamento típico
          daquele grupo útil para nomear os clusters em termos de negócio (ex.
          "usuários de carga leve e frequente" vs. "carga pesada e esporádica").
        - **Limitação honesta**: nos dados atuais, a separação entre clusters é
          moderada a variável que mais diferencia os grupos é o consumo médio por
          sessão; horário e frequência variam pouco entre usuários neste piloto. O
          número de clusters (4) foi escolhido por utilidade prática para o gestor,
          não porque os dados apontassem claramente para esse número.
        """)

    st.divider()
    st.write("#### Simular o cluster de um perfil hipotético")

    with st.container(border=True):
        left_col, right_col = st.columns(2)
        with left_col:
            n_sessoes_total = st.slider(
                "Sessões no período",
                min_value=int(clusters_data["n_sessoes_total"].min()),
                max_value=int(clusters_data["n_sessoes_total"].max()),
                value=int(clusters_data["n_sessoes_total"].median()),
            )
            hora_media_inicio = st.slider(
                "Horário médio de início (hora do dia)",
                min_value=0.0, max_value=23.0,
                value=float(clusters_data["hora_media_inicio"].median()),
            )
        with right_col:
            kwh_medio_sessao = st.slider(
                "kWh médio por sessão",
                min_value=float(clusters_data["kwh_medio_sessao"].min()),
                max_value=float(clusters_data["kwh_medio_sessao"].max()),
                value=float(clusters_data["kwh_medio_sessao"].median()),
            )
            duracao_media_min = st.slider(
                "Duração média (min)",
                min_value=float(clusters_data["duracao_media_min"].min()),
                max_value=float(clusters_data["duracao_media_min"].max()),
                value=float(clusters_data["duracao_media_min"].median()),
            )
        pct_fim_semana = st.slider(
            "Proporção de sessões em fim de semana",
            min_value=0.0, max_value=1.0,
            value=float(clusters_data["pct_fim_semana"].median()),
        )

    if st.button("Classificar perfil", type="primary"):
        sample = pd.DataFrame([{
            "n_sessoes_total": n_sessoes_total,
            "hora_media_inicio": hora_media_inicio,
            "kwh_medio_sessao": kwh_medio_sessao,
            "duracao_media_min": duracao_media_min,
            "pct_fim_semana": pct_fim_semana,
        }])
        cluster = artifacts["pipeline"].predict(sample)[0]
        st.metric("Cluster atribuído", f"Cluster {cluster}")


# ────────────────────────────────────────────
# Aba 3 — Detecção de anomalias
# ────────────────────────────────────────────

def render_anomalias_tab():
    st.write("### Detecção de anomalias")
    st.caption(
        "Sinaliza sessões com consumo/duração estatisticamente fora do padrão do "
        "ponto de recarga (Z-score, limiar de 3 desvios-padrão)."
    )

    artifacts = load_anomalias_model()
    if artifacts is None:
        _missing_artifact_message("deteccao_anomalias_zscore.joblib", "03_model_anomaly_detection.ipynb")
        return

    scores_data = artifacts["scores_data"]
    model = artifacts["model"]

    n_anomalias = scores_data["anomaly_predicted"].sum()
    col1, col2, col3 = st.columns(3)
    col1.metric("Sessões analisadas", f"{len(scores_data):,}")
    col2.metric("Sinalizadas como anômalas", f"{n_anomalias:,}")
    col3.metric("Taxa de anomalia", f"{n_anomalias / len(scores_data):.1%}")

    fig = px.scatter(
        scores_data,
        x="duration_min",
        y="kwh_delivered",
        color=scores_data["anomaly_predicted"].map({True: "Anômala", False: "Normal"}),
        color_discrete_map={"Anômala": "#d62728", "Normal": "#1f77b4"},
        facet_col="point_id",
        labels={"duration_min": "Duração (min)", "kwh_delivered": "kWh entregue", "color": "Status"},
        title="Sessões por ponto de recarga — normais vs. anômalas",
    )
    st.plotly_chart(fig, width='stretch')

    with st.expander("ℹ️ Como interpretar as anomalias"):
        st.markdown("""
        O modelo usa **Z-score**: mede quantos desvios-padrão uma sessão está distante
        da média das sessões daquele mesmo ponto de recarga, considerando 3 variáveis
        (kWh entregue, duração e taxa de carregamento kWh/minuto).

        - No **gráfico**, cada painel é um ponto de recarga diferente a comparação é
          sempre feita dentro do mesmo ponto, nunca entre pontos de potências
          diferentes. Pontos **vermelhos** foram sinalizados como anômalos (mais de 3
          desvios-padrão de distância em pelo menos uma das 3 variáveis).
        - Uma sessão marcada como anômala não significa necessariamente fraude ou
          erro pode ser um uso genuinamente atípico. Por isso o processo de negócio
          prevê **revisão manual**, não bloqueio automático da fatura.
        - Na validação com dados conhecidos, este método captura corretamente quase
          todas as anomalias reais, mas também sinaliza algumas sessões normais que
          por acaso têm valores extremos (falso positivo) é um trade-off proposital
          para não deixar passar despercebido nenhum caso genuíno.
        """)

    st.divider()
    st.write("#### Testar uma sessão hipotética")

    point_ids = sorted(model["reference_stats"]["point_id"].unique())

    with st.container(border=True):
        point_id = st.selectbox("Ponto de recarga", point_ids)
        left_col, right_col = st.columns(2)
        with left_col:
            kwh_test = st.number_input("kWh entregue", min_value=0.0, value=20.0, step=1.0)
        with right_col:
            duration_test = st.number_input("Duração (min)", min_value=1.0, value=90.0, step=5.0)

    if st.button("Verificar anomalia", type="primary"):
        ref = model["reference_stats"]
        row = ref[ref["point_id"] == point_id].iloc[0]

        kwh_por_min_test = kwh_test / duration_test

        z_kwh = (kwh_test - row["kwh_delivered_mean"]) / row["kwh_delivered_std"]
        z_duration = (duration_test - row["duration_min_mean"]) / row["duration_min_std"]
        z_taxa = (kwh_por_min_test - row["kwh_por_min_mean"]) / row["kwh_por_min_std"]

        z_max = max(abs(z_kwh), abs(z_duration), abs(z_taxa))
        is_anomaly = z_max > model["z_threshold"]

        if is_anomaly:
            st.error(f"**Anômala** — maior |Z-score| entre as 3 variáveis: {z_max:.2f} (limiar: {model['z_threshold']})")
        else:
            st.success(f"**Normal** — maior |Z-score| entre as 3 variáveis: {z_max:.2f} (limiar: {model['z_threshold']})")


# ────────────────────────────────────────────
# Aba 4 — Score de expansão
# ────────────────────────────────────────────

def render_expansao_tab():
    st.write("### Score de expansão por município")
    st.caption(
        "Índice composto (0-100) combinando frota BEV/PHEV, população e cobertura "
        "de eletropostos públicos. Quanto maior, maior a atratividade estimada "
        "para expansão do EV ChargeOps."
    )

    artifacts = load_expansao_model()
    if artifacts is None:
        _missing_artifact_message("score_expansao_modelo.joblib", "04_model_expansion_score.ipynb")
        return

    municipios_data = artifacts["municipios_data"]

    fig = px.scatter(
        municipios_data,
        x="population_estimate",
        y="frota_total",
        size="chargepoints_count",
        color="expansion_score_0_100",
        color_continuous_scale=["red", "yellow", "green"],
        hover_name="city",
        hover_data=["state", "cobertura_por_1000_evs"],
        labels={
            "population_estimate": "População estimada",
            "frota_total": "Frota BEV+PHEV",
            "expansion_score_0_100": "Score de expansão",
        },
        title="Municípios por população, frota e score de expansão",
    )
    st.plotly_chart(fig, width='stretch')

    left_col, right_col = st.columns(2)
    with left_col:
        st.write("#### Top 10 — maior atratividade")
        top10 = municipios_data.sort_values("expansion_score_0_100", ascending=False).head(10)
        st.dataframe(
            top10[["city", "state", "frota_total", "population_estimate", "expansion_score_0_100"]],
            width='stretch', hide_index=True,
        )
    with right_col:
        st.write("#### Bottom 10 — menor atratividade")
        bottom10 = municipios_data.sort_values("expansion_score_0_100", ascending=True).head(10)
        st.dataframe(
            bottom10[["city", "state", "frota_total", "population_estimate", "expansion_score_0_100"]],
            width='stretch', hide_index=True,
        )

    with st.expander("ℹ️ Como interpretar o score de expansão"):
        st.markdown("""
        Este não é um modelo preditivo no sentido tradicional é um **índice
        composto**: combina 3 indicadores conhecidos de cada município num único
        número (0 a 100), sem "aprender" um padrão de sucesso (não existe, ainda,
        nenhum caso real de expansão para servir de exemplo).

        - **Frota BEV/PHEV alta** e **população alta** aumentam o score indicam
          mercado potencial maior (mais veículos elétricos, mais moradores e
          condomínios).
        - **Cobertura de eletropostos públicos alta** reduz o score não porque seja
          ruim para a região, mas porque indica que a demanda de recarga já está
          sendo atendida por infraestrutura pública, reduzindo a lacuna que o EV
          ChargeOps preenche em condomínios e prédios privados. A cobertura é medida
          de forma **relativa à frota** (eletropostos por 1.000 veículos), não em
          valor absoluto assim uma cidade grande não é penalizada só por ter mais
          eletropostos em números totais.
        - No **gráfico de bolhas**, o tamanho de cada bolha representa a cobertura de
          eletropostos, e a cor vai do vermelho (menor atratividade) ao verde (maior
          atratividade) passe o mouse sobre uma bolha para ver os números exatos.
        - Trate o score como um **ponto de partida para investigação**, não uma
          decisão automática de expansão fatores locais (custo de instalação,
          parcerias com condomínios, regulação municipal) não estão capturados aqui.
        """)


# ────────────────────────────────────────────
# Layout principal
# ────────────────────────────────────────────

st.title("EV ChargeOps — Dashboard de IA")

tab_previsao, tab_perfis, tab_anomalias, tab_expansao = st.tabs([
    "📈 Previsão de Consumo",
    "👥 Perfis de Uso",
    "🚨 Detecção de Anomalias",
    "🗺️ Score de Expansão",
])

with tab_previsao:
    render_previsao_tab()

with tab_perfis:
    render_perfis_tab()

with tab_anomalias:
    render_anomalias_tab()

with tab_expansao:
    render_expansao_tab()