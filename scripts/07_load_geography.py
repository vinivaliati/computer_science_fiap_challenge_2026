"""
EV ChargeOps — Carga de dim_geography

Resolve o bloqueio identificado após a Etapa 5: dim_geography estava vazia
e dim_points.geo_id era carregado como NULL, porque a exploração das APIs
(Etapa 2) nunca virou carga real no star schema.

Escopo: municípios de SP, RJ, DF, PR, reconciliados por nome normalizado
(maiúsculas, sem acento) entre as 3 fontes:
  - IBGE Localidades: hierarquia de município (fonte da verdade dos nomes/UF)
  - IBGE Agregados: população estimada por município
  - RENAVAM: frota BEV/PHEV por UF/município (heurística de sufixo EV/DM
    confirmada na Etapa 2, ver docs/fontes-externas.md)
  - Open Charge Map: contagem de pontos de recarga por UF (não há
    granularidade de município nos dados livres da API, então esta
    contagem é aplicada por UF)

Estratégia de reconciliação: match EXATO por nome normalizado, não fuzzy.
Fuzzy matching arriscaria juntar municípios de nomes parecidos mas
diferentes (ex: "Santo André" vs "Santo Antônio"). Municípios sem match
completo ficam registrados no relatório final, não silenciosamente
descartados.

Uso:
    export OCM_API_KEY="sua_key"   # ver scripts/01_explore_ocm.py
    export POSTGRES_HOST=localhost
    export POSTGRES_PORT=5432
    export POSTGRES_DB=evchargeops
    export POSTGRES_USER=evchargeops
    export POSTGRES_PASSWORD=<sua senha>
    python scripts/07_load_geography.py
"""

import io
import os
import re
import unicodedata
import zipfile
from pathlib import Path

import pandas as pd
import psycopg2
import requests
from psycopg2.extras import execute_values

DATA_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "geografia"

DB_CONFIG = {
    "host": os.getenv("POSTGRES_HOST", "localhost"),
    "port": os.getenv("POSTGRES_PORT", "5432"),
    "dbname": os.getenv("POSTGRES_DB", "evchargeops"),
    "user": os.getenv("POSTGRES_USER", "evchargeops"),
    "password": os.getenv("POSTGRES_PASSWORD", ""),
}

OCM_API_KEY = os.getenv("OCM_API_KEY", "")

UFS = ["SP", "RJ", "DF", "PR"]

# RENAVAM usa o nome do estado por extenso (maiúsculo), não a sigla.
UF_NOME_EXTENSO = {
    "SP": "SAO PAULO",
    "RJ": "RIO DE JANEIRO",
    "DF": "DISTRITO FEDERAL",
    "PR": "PARANA",
}

# URL do RENAVAM muda todo mês (ver docs/fontes-externas.md), mesma URL
# já validada em 03_explore_renavam.py.
RENAVAM_URL = (
    "https://dados.transportes.gov.br/dataset/12686da0-3d71-4499-b432-d270f785c907"
    "/resource/85150148-6a47-4600-8b6a-061871c980f7"
    "/download/i_frota_por_uf_municipio_marca_e_modelo_ano_julho_2026.zip"
)

# Mesma heurística BEV/PHEV validada na Etapa 2 (ver 03_explore_renavam.py
# e docs/fontes-externas.md para o histórico dos ajustes, incluindo o
# falso positivo "ONIX" corrigido com \b).
BEV_PATTERN = r"\bEV\b|\dEV\b|EUV\b"
PHEV_PATTERN = r"\bDM\b"
FALLBACK_PATTERN = "|".join([
    r"\bDOLPHIN\b", r"\bSEAL\b", r"\bATTO\b",
    r"GWM/ORA", r"\bORA\s?0\d\b",
    r"GEELY/EX\d", r"GAC/AION", r"\bAION\b",
    r"\bE-TRON\b", r"\bZOE\b", r"\bLEAF\b",
    r"MODEL\s?3", r"MODEL\s?Y", r"\bID\.4\b", r"\bID\.3\b",
    r"KONA\s?ELECTRIC", r"\bIONIQ\b",
    r"\bEQA\b", r"\bEQB\b", r"\bEQC\b", r"\bEQS\b",
    r"\bI3\b", r"\bI4\b", r"\bIX\b",
])


# Normaliza nome de município para reconciliação: maiúsculas, sem acento,
# sem espaço duplicado ou nas pontas.
def normalize_name(name: str) -> str:
    if pd.isna(name):
        return ""
    nfkd = unicodedata.normalize("NFKD", str(name))
    without_accents = "".join(c for c in nfkd if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", without_accents.upper().strip())


# ────────────────────────────────────────────
# IBGE — municípios + população
# ────────────────────────────────────────────

def fetch_ibge_municipios(uf: str) -> pd.DataFrame:
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    data = response.json()
    return pd.DataFrame([
        {"ibge_municipio_id": m["id"], "city": m["nome"], "state": uf}
        for m in data
    ])


# Retorna {municipio_id: populacao}. Busca em lotes de 50 IDs, a API
# rejeita URLs muito longas com centenas de IDs juntos.
def fetch_ibge_populacao(municipio_ids: list[int]) -> dict:
    populacao = {}
    batch_size = 50
    for i in range(0, len(municipio_ids), batch_size):
        batch = municipio_ids[i:i + batch_size]
        ids_str = ",".join(str(m) for m in batch)
        url = (
            f"https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1"
            f"/variaveis/9324?localidades=N6[{ids_str}]"
        )
        try:
            response = requests.get(url, timeout=30)
            response.raise_for_status()
            data = response.json()
            for serie in data[0]["resultados"][0]["series"]:
                municipio_id = int(serie["localidade"]["id"])
                valor = list(serie["serie"].values())[0]
                populacao[municipio_id] = int(valor) if valor not in (None, "-", "...") else None
        except (requests.HTTPError, KeyError, IndexError, ValueError) as e:
            print(f"  AVISO: falha ao buscar população do lote {i}-{i+batch_size}: {e}")
    return populacao


# ────────────────────────────────────────────
# RENAVAM — frota BEV/PHEV por município
# ────────────────────────────────────────────

def download_renavam() -> Path:
    extract_dir = DATA_DIR / "renavam"
    existing = list(extract_dir.glob("*")) if extract_dir.exists() else []
    if existing:
        return existing[0]

    response = requests.get(RENAVAM_URL, timeout=180)
    response.raise_for_status()
    extract_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(extract_dir)
        names = zf.namelist()
    return extract_dir / names[0]


def classify_vehicle(marca_modelo: str) -> str | None:
    upper = str(marca_modelo).upper()
    if re.search(BEV_PATTERN, upper):
        return "BEV"
    if re.search(PHEV_PATTERN, upper):
        return "PHEV"
    if re.search(FALLBACK_PATTERN, upper):
        return "BEV"  # fallback de marca é majoritariamente BEV nas montadoras listadas
    return None


# Lê o RENAVAM completo, filtra as UFs de interesse, classifica BEV/PHEV
# e agrega por UF+município normalizado.
def fetch_renavam_fleet(ufs: list[str]) -> pd.DataFrame:
    file_path = download_renavam()

    df = pd.read_csv(file_path, sep=";", encoding="utf-8", low_memory=False)
    df.columns = [c.strip() for c in df.columns]

    uf_nomes = [UF_NOME_EXTENSO[uf] for uf in ufs]
    df = df[df["UF"].str.strip().str.upper().isin(uf_nomes)].copy()

    qtd_col = next(c for c in df.columns if "qtd" in c.lower())
    df[qtd_col] = df[qtd_col].astype(str).str.strip().astype(float)

    marca_col = next(c for c in df.columns if "marca" in c.lower())
    df["vehicle_class"] = df[marca_col].apply(classify_vehicle)
    df = df[df["vehicle_class"].notna()]

    df["city_norm"] = df["Município"].apply(normalize_name)
    uf_inverso = {v: k for k, v in UF_NOME_EXTENSO.items()}
    df["state"] = df["UF"].str.strip().str.upper().map(uf_inverso)

    agg = df.groupby(["state", "city_norm", "vehicle_class"])[qtd_col].sum().reset_index()
    pivot = agg.pivot_table(
        index=["state", "city_norm"], columns="vehicle_class", values=qtd_col, fill_value=0
    ).reset_index()

    for col in ("BEV", "PHEV"):
        if col not in pivot.columns:
            pivot[col] = 0

    return pivot.rename(columns={"BEV": "ev_fleet_bev_count", "PHEV": "ev_fleet_phev_count"})


# ────────────────────────────────────────────
# Open Charge Map — cobertura por UF
# ────────────────────────────────────────────

# Conta POIs por estado. A API não expõe filtro direto por StateOrProvince
# de forma confiável (campo às vezes vem vazio, ver docs/fontes-externas.md),
# então buscamos por país e agregamos usando StateOrProvince quando presente.
def fetch_ocm_coverage_by_state(ufs: list[str]) -> dict:
    params = {
        "output": "json",
        "countrycode": "BR",
        "maxresults": 2000,
        "compact": "true",
    }
    if OCM_API_KEY:
        params["key"] = OCM_API_KEY

    try:
        response = requests.get(
            "https://api.openchargemap.io/v3/poi/",
            params=params,
            headers={"User-Agent": "ev-chargeops-fiap/1.0"},
            timeout=60,
        )
        response.raise_for_status()
        pois = response.json()
    except requests.RequestException as e:
        print(f"  AVISO: falha ao consultar Open Charge Map ({e}). Cobertura ficará zerada.")
        return {uf: 0 for uf in ufs}

    counts = {uf: 0 for uf in ufs}
    uf_por_extenso_norm = {
        normalize_name(v): k for k, v in {
            "SP": "São Paulo", "RJ": "Rio de Janeiro",
            "DF": "Distrito Federal", "PR": "Paraná",
        }.items()
    }

    for poi in pois:
        state_raw = (poi.get("AddressInfo") or {}).get("StateOrProvince")
        if not state_raw:
            continue
        state_norm = normalize_name(state_raw)
        matched_uf = uf_por_extenso_norm.get(state_norm) or (
            state_raw.strip().upper() if state_raw.strip().upper() in ufs else None
        )
        if matched_uf in counts:
            counts[matched_uf] += 1

    return counts


# ────────────────────────────────────────────
# Orquestração
# ────────────────────────────────────────────

def build_dim_geography() -> tuple[pd.DataFrame, dict]:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    report = {"municipios_ibge": 0, "com_populacao": 0, "com_frota_renavam": 0}

    municipios_frames = [fetch_ibge_municipios(uf) for uf in UFS]
    municipios_df = pd.concat(municipios_frames, ignore_index=True)
    municipios_df["city_norm"] = municipios_df["city"].apply(normalize_name)
    report["municipios_ibge"] = len(municipios_df)
    print(f"Municípios (IBGE): {len(municipios_df)} em {UFS}.")

    populacao = fetch_ibge_populacao(municipios_df["ibge_municipio_id"].tolist())
    municipios_df["population_estimate"] = municipios_df["ibge_municipio_id"].map(populacao)
    report["com_populacao"] = municipios_df["population_estimate"].notna().sum()
    print(f"População resolvida: {report['com_populacao']} de {len(municipios_df)} municípios.")

    fleet_df = fetch_renavam_fleet(UFS)
    print(f"Frota RENAVAM: {len(fleet_df)} combinações UF+município com veículo eletrificado.")

    ocm_by_state = fetch_ocm_coverage_by_state(UFS)
    print(f"Cobertura Open Charge Map por UF: {ocm_by_state}")

    # Reconciliação: match exato por (state, city_norm).
    merged = municipios_df.merge(fleet_df, on=["state", "city_norm"], how="left")
    merged["ev_fleet_bev_count"] = merged["ev_fleet_bev_count"].fillna(0).astype(int)
    merged["ev_fleet_phev_count"] = merged["ev_fleet_phev_count"].fillna(0).astype(int)
    report["com_frota_renavam"] = (
        (merged["ev_fleet_bev_count"] > 0) | (merged["ev_fleet_phev_count"] > 0)
    ).sum()

    merged["chargepoints_count"] = merged["state"].map(ocm_by_state).fillna(0).astype(int)
    merged["geo_id"] = "G" + merged["ibge_municipio_id"].astype(str)

    result = merged[[
        "geo_id", "city", "state", "ibge_municipio_id", "population_estimate",
        "ev_fleet_bev_count", "ev_fleet_phev_count", "chargepoints_count",
    ]]

    return result, report


def load_dim_geography(conn, df: pd.DataFrame) -> None:
    columns = [
        "geo_id", "city", "state", "ibge_municipio_id", "population_estimate",
        "ev_fleet_bev_count", "ev_fleet_phev_count", "chargepoints_count",
    ]
    records = df[columns].where(pd.notnull(df[columns]), None).values.tolist()
    query = f"INSERT INTO dim_geography ({', '.join(columns)}) VALUES %s ON CONFLICT DO NOTHING"
    with conn.cursor() as cur:
        execute_values(cur, query, records)
    conn.commit()
    print(f"dim_geography: {len(records)} municípios inseridos.")


# Atualiza dim_points.geo_id com base no sufixo "Cidade/UF" presente em
# dim_points.location (formato gerado por 04_generate_sessions.py, ex:
# "... Jardim das Palmeiras, São Paulo/SP").
#
# Histórico de um bug real: a primeira versão desta função procurava o
# nome de qualquer município cadastrado como substring solta dentro do
# location inteiro. Isso gerou falsos positivos confirmados, como
# "PALMEIRA" (município real do Paraná) casando dentro de "...PALMEIRAS..."
# A correção usa apenas o sufixo explícito "/UF" no fim do location para
# restringir a busca à UF certa, exigindo que o nome do município seja a
# última cidade mencionada antes do "/UF", não uma substring solta.
def link_points_to_geography(conn) -> None:
    with conn.cursor() as cur:
        cur.execute("SELECT point_id, location FROM dim_points WHERE geo_id IS NULL")
        points = cur.fetchall()
        cur.execute("SELECT geo_id, city, state FROM dim_geography")
        geos = cur.fetchall()

    # Índice (state, city_norm) -> geo_id, restrito por UF para evitar
    # colisão entre municípios de estados diferentes.
    geo_index = {(state, normalize_name(city)): geo_id for geo_id, city, state in geos}

    # Padrão: "..., Cidade Nome/UF" no final da string.
    suffix_pattern = re.compile(r"[,\-]\s*([^,\-/]+?)\s*/\s*([A-Z]{2})\s*$")

    updates = []
    unresolved = []
    for point_id, location in points:
        match = suffix_pattern.search((location or "").strip())
        if not match:
            unresolved.append((point_id, location))
            continue
        city_part, state_part = match.groups()
        city_norm = normalize_name(city_part)
        geo_id = geo_index.get((state_part.upper(), city_norm))
        if geo_id:
            updates.append((geo_id, point_id))
        else:
            unresolved.append((point_id, location))

    if updates:
        with conn.cursor() as cur:
            cur.executemany("UPDATE dim_points SET geo_id = %s WHERE point_id = %s", updates)
        conn.commit()
    print(f"dim_points: {len(updates)} de {len(points)} pontos vinculados a um geo_id.")
    if unresolved:
        print("  Não resolvidos (sem sufixo 'Cidade/UF' reconhecível ou fora do escopo):")
        for point_id, location in unresolved:
            print(f"    {point_id}: {location!r}")


def main() -> None:
    result_df, report = build_dim_geography()

    conn = psycopg2.connect(**DB_CONFIG)
    try:
        load_dim_geography(conn, result_df)
        link_points_to_geography(conn)
    finally:
        conn.close()

    print("\n--- Relatório de reconciliação ---")
    print(f"Municípios (IBGE, base): {report['municipios_ibge']}")
    print(f"Com população resolvida: {report['com_populacao']}")
    print(f"Com frota BEV/PHEV (RENAVAM) > 0: {report['com_frota_renavam']}")
    sem_populacao = report["municipios_ibge"] - report["com_populacao"]
    if sem_populacao > 0:
        print(f"AVISO: {sem_populacao} municípios sem população resolvida "
              f"(falha pontual da API de Agregados ou município sem série disponível).")


if __name__ == "__main__":
    main()