"""
EV ChargeOps — Exploração do dataset RENAVAM (Ministério dos Transportes)

Substitui a fonte ABVE, que só disponibiliza dados via paineis BI embutidos
(sem HTML tabular, sem API, sem CSV inviável para scraping simples).

RENAVAM é dado aberto real e estruturado: frota de veículos por UF,
município, marca/modelo e ano, atualizado mensalmente.

Portal: https://dados.transportes.gov.br/dataset/registro-nacional-de-veiculos-automotores-renavam
Formato: um arquivo .zip por mês, contendo CSV(s) de frota por
uf/município/marca/modelo/ano.

Uso:
    python scripts/03_explore_renavam.py
"""

import io
import zipfile
from pathlib import Path

import pandas as pd
import requests

# URL do resource mais recente (julho/2026). O portal publica um novo .zip
# por mês sem padrão de URL — ajustar manualmente ao rodar em mês seguinte.
DOWNLOAD_URL = (
    "https://dados.transportes.gov.br/dataset/12686da0-3d71-4499-b432-d270f785c907"
    "/resource/85150148-6a47-4600-8b6a-061871c980f7"
    "/download/i_frota_por_uf_municipio_marca_e_modelo_ano_julho_2026.zip"
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


# Baixa o ZIP e extrai para data/raw/renavam/. Retorna os arquivos extraídos.
def download_and_extract(url: str) -> list[str]:
    print(f"Baixando: {url}")
    response = requests.get(url, timeout=120)
    response.raise_for_status()

    extract_dir = OUTPUT_DIR / "renavam"
    extract_dir.mkdir(parents=True, exist_ok=True)

    with zipfile.ZipFile(io.BytesIO(response.content)) as zf:
        zf.extractall(extract_dir)
        names = zf.namelist()

    print(f"Extraído para: {extract_dir}")
    return names


# Inspeciona as primeiras linhas em texto puro, sem presumir separador ou
# encoding fallback para arquivos de governo que fogem do padrão RFC.
def summarize_raw_file(file_path: Path) -> None:
    print(f"\nArquivo: {file_path.name} ({file_path.stat().st_size / 1_000_000:.1f} MB)")

    for encoding in ("latin-1", "utf-8", "cp1252"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                first_lines = [next(f) for _ in range(5)]
            print(f"Lido com encoding='{encoding}'. Primeiras 5 linhas:")
            for line in first_lines:
                print(repr(line))
            break
        except (UnicodeDecodeError, StopIteration) as e:
            print(f"Falha com encoding='{encoding}': {e}")
            continue


# Lê o CSV extraído, resume colunas/amostra e identifica veículos elétricos.
#
# Formato confirmado por execução real (RENAVAM jul/2026):
# - Separador ';', encoding UTF-8 (latin-1 falha silenciosamente — não
#   lança exceção, só corrompe acentos).
# - Colunas: UF; Município; Marca Modelo; Ano Fabricação Veículo CRV; Qtd. Veículos
# - 'Marca Modelo' vem combinado (ex: 'BYD/DOLPHIN MINI')
# - 'Qtd. Veículos' vem como texto com espaço à frente (ex: ' 1200.0')
# - Não há coluna de combustível — elétricos são identificados por
#   palavra-chave em 'Marca Modelo'.
def summarize_csv(file_path: Path) -> None:
    attempts = [
        {"sep": ";", "encoding": "utf-8"},
        {"sep": ";", "encoding": "latin-1"},
        {"sep": ",", "encoding": "utf-8"},
        {"sep": "\t", "encoding": "latin-1"},
        {"sep": "|", "encoding": "latin-1"},
    ]

    df = None
    for attempt in attempts:
        try:
            candidate = pd.read_csv(file_path, nrows=20000, low_memory=False, **attempt)
            candidate.columns = [c.strip() for c in candidate.columns]
            # 'Ã' seguido de outro caractere não-ASCII é sinal forte de encoding errado em PT-BR.
            has_mojibake = any("Ã" in str(c) for c in candidate.columns)
            if candidate.shape[1] > 1 and not has_mojibake:
                df = candidate
                print(f"Leitura bem-sucedida com: {attempt}")
                break
        except Exception:
            continue

    if df is None:
        print(f"\nNão foi possível parsear {file_path.name} automaticamente. Inspecionando como texto puro:")
        summarize_raw_file(file_path)
        return

    print(f"Colunas: {list(df.columns)} | Linhas na amostra: {len(df)}")
    print(df.head(5).to_string())

    # 'Qtd. Veículos' costuma vir como texto com espaço/decimal normaliza.
    qtd_col = next((c for c in df.columns if "qtd" in c.lower()), None)
    if qtd_col:
        df[qtd_col] = df[qtd_col].astype(str).str.strip().astype(float)
        print(f"'{qtd_col}' normalizada para float. Soma na amostra: {df[qtd_col].sum():.0f}")

    marca_modelo_col = next((c for c in df.columns if "marca" in c.lower()), None)
    if not marca_modelo_col:
        print("AVISO: coluna de marca/modelo não encontrada — verifique manualmente.")
        return

    # Descoberta em execução real: RENAVAM sinaliza o tipo de eletrificação
    # no próprio nome do modelo, mais confiável que decorar marcas:
    #   - Sufixo 'EV' (ex: 'GL5EV', '310EV', 'GS EV') → BEV (100% elétrico)
    #   - Sufixo 'DM' (ex: 'GL DM', 'GS DM')           → PHEV (BYD "Dual Mode")
    # Fallback de marcas conhecidas cobre montadoras que não usam esse padrão
    # (ex: BMW iX, Nissan Leaf).
    bev_pattern = r"\bEV\b|\dEV\b|EUV\b"
    phev_pattern = r"\bDM\b"
    fallback_pattern = "|".join([
        r"\bDOLPHIN\b", r"\bSEAL\b", r"\bATTO\b",
        r"GWM/ORA", r"\bORA\s?0\d\b",
        r"GEELY/EX\d", r"GAC/AION", r"\bAION\b",
        r"\bE-TRON\b", r"\bZOE\b", r"\bLEAF\b",
        r"MODEL\s?3", r"MODEL\s?Y", r"\bID\.4\b", r"\bID\.3\b",
        r"KONA\s?ELECTRIC", r"\bIONIQ\b",
        r"\bEQA\b", r"\bEQB\b", r"\bEQC\b", r"\bEQS\b",
        r"\bI3\b", r"\bI4\b", r"\bIX\b",
    ])

    modelo_upper = df[marca_modelo_col].astype(str).str.upper()
    is_bev = modelo_upper.str.contains(bev_pattern, na=False, regex=True)
    is_phev = modelo_upper.str.contains(phev_pattern, na=False, regex=True) & ~is_bev
    is_fallback = modelo_upper.str.contains(fallback_pattern, na=False, regex=True) & ~is_bev & ~is_phev

    eletrificados = df[is_bev | is_phev | is_fallback]
    print(
        f"Eletrificados na amostra: {len(eletrificados)}/{len(df)} "
        f"(BEV: {is_bev.sum()}, PHEV: {is_phev.sum()}, fallback: {is_fallback.sum()})"
    )
    if len(eletrificados) > 0:
        print("Modelos BEV — amostra:", list(df[is_bev][marca_modelo_col].unique()[:20]))
        print("Modelos PHEV — amostra:", list(df[is_phev][marca_modelo_col].unique()[:20]))
        if is_fallback.sum() > 0:
            print("Modelos via fallback — amostra:", list(df[is_fallback][marca_modelo_col].unique()[:20]))

    # Escopo: BEV + PHEV entram como frota-alvo (ambos carregam via plugue).
    # Tipo é preservado como atributo, não filtro heurística por nome ainda
    # não é coluna oficial de combustível; revisar valores únicos acima antes da Etapa 4.
    print("\nEscopo: BEV + PHEV = frota-alvo. Heurística por nome revisar antes da Etapa 4.")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extract_dir = OUTPUT_DIR / "renavam"

    # Evita novo download se já extraído numa execução anterior.
    existing_files = list(extract_dir.glob("*")) if extract_dir.exists() else []
    if existing_files:
        print(f"Arquivos já extraídos em {extract_dir}, pulando download.")
        extracted_files = [f.name for f in existing_files]
    else:
        extracted_files = download_and_extract(DOWNLOAD_URL)

    # Arquivos do governo às vezes vêm como .TXT mesmo sendo delimitados aceitar ambos.
    data_files = [f for f in extracted_files if f.lower().endswith((".csv", ".txt"))]
    if not data_files:
        print(f"Nenhum .csv/.txt encontrado no .zip. Arquivos: {extracted_files}")
        return

    for file_name in data_files:
        summarize_csv(OUTPUT_DIR / "renavam" / file_name)


if __name__ == "__main__":
    main()