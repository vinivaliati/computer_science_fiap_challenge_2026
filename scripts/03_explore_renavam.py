"""
EV ChargeOps — Exploração do dataset RENAVAM (Ministério dos Transportes)

Substitui a fonte ABVE, que só disponibiliza dados via paineis BI embutidos
(sem HTML tabular, sem API, sem CSV — inviável para scraping simples).

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

# URL do resource mais recente disponível no dataset (ajustar para o mês
# corrente sempre que for rodar de novo — o portal publica um novo .zip
# por mês, sem padrão de URL previsível/parametrizável).
# Este é o de julho/2026, o mais recente encontrado na exploração.
DOWNLOAD_URL = (
    "https://dados.transportes.gov.br/dataset/12686da0-3d71-4499-b432-d270f785c907"
    "/resource/85150148-6a47-4600-8b6a-061871c980f7"
    "/download/i_frota_por_uf_municipio_marca_e_modelo_ano_julho_2026.zip"
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"


def download_and_extract(url: str) -> list[str]:
    """Baixa o ZIP e extrai para data/raw/renavam/. Retorna a lista de
    arquivos extraídos."""
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


def summarize_raw_file(file_path: Path) -> None:
    """Inspeciona as primeiras linhas em modo texto puro, sem presumir
    separador ou encoding — útil para arquivos .TXT/.CSV do governo que
    frequentemente fogem do padrão RFC (delimitador ';', encoding
    latin-1, cabeçalho em maiúsculas, etc.)."""
    print(f"\nArquivo: {file_path.name}")
    print(f"Tamanho: {file_path.stat().st_size / 1_000_000:.1f} MB")

    # Tenta alguns encodings comuns em dados abertos do governo brasileiro.
    for encoding in ("latin-1", "utf-8", "cp1252"):
        try:
            with open(file_path, "r", encoding=encoding) as f:
                first_lines = [next(f) for _ in range(5)]
            print(f"\nLido com sucesso usando encoding='{encoding}'")
            print("Primeiras 5 linhas (brutas):")
            for line in first_lines:
                print(repr(line))
            break
        except (UnicodeDecodeError, StopIteration) as e:
            print(f"Falha com encoding='{encoding}': {e}")
            continue


def summarize_csv(file_path: Path) -> None:
    """Lê o arquivo extraído e resume colunas e uma amostra de linhas,
    incluindo uma checagem específica por veículos elétricos.

    Formato confirmado por execução real (RENAVAM jul/2026):
    - Separador: ';'
    - Encoding: UTF-8 (NÃO latin-1 — testar latin-1 primeiro produz
      mojibake silencioso em acentos, sem lançar exceção)
    - Colunas: UF; Município; Marca Modelo; Ano Fabricação Veículo CRV;
      Qtd. Veículos
    - 'Marca Modelo' vem combinado num único campo, ex: 'BYD/DOLPHIN MINI'
    - 'Qtd. Veículos' vem como texto com espaço à frente, ex: ' 1200.0'
    - Não existe coluna de combustível/energia — elétricos precisam ser
      identificados por palavra-chave na coluna Marca Modelo.
    """
    attempts = [
        {"sep": ";", "encoding": "utf-8"},
        {"sep": ";", "encoding": "latin-1"},
        {"sep": ",", "encoding": "utf-8"},
        {"sep": "\t", "encoding": "latin-1"},
        {"sep": "|", "encoding": "latin-1"},
    ]

    df = None
    used_attempt = None
    for attempt in attempts:
        try:
            candidate = pd.read_csv(file_path, nrows=20000, low_memory=False, **attempt)
            candidate.columns = [c.strip() for c in candidate.columns]
            # Heurística: além de mais de 1 coluna, checa se não há
            # caracteres de mojibake óbvios (Ã seguido de outro caractere
            # não-ASCII é um sinal forte de encoding errado em PT-BR).
            has_mojibake = any("Ã" in str(c) for c in candidate.columns)
            if candidate.shape[1] > 1 and not has_mojibake:
                df = candidate
                used_attempt = attempt
                print(f"\nLeitura bem-sucedida com: {attempt}")
                break
            elif has_mojibake:
                print(f"Rejeitado {attempt}: mojibake detectado nas colunas {list(candidate.columns)}")
        except Exception as e:
            print(f"Falha com {attempt}: {e}")
            continue

    if df is None:
        print(
            f"\nNão foi possível parsear {file_path.name} automaticamente. "
            "Inspecionando como texto puro para identificar o formato manualmente:"
        )
        summarize_raw_file(file_path)
        return

    print(f"\nArquivo: {file_path.name}")
    print(f"Colunas encontradas: {list(df.columns)}")
    print(f"Total de linhas (amostra lida): {len(df)}")
    print("\nPrimeiras linhas:")
    print(df.head(5).to_string())

    # 'Qtd. Veículos' costuma vir como texto com espaço/decimal — normaliza.
    qtd_col = next((c for c in df.columns if "qtd" in c.lower()), None)
    if qtd_col:
        df[qtd_col] = df[qtd_col].astype(str).str.strip().astype(float)
        print(f"\nColuna '{qtd_col}' normalizada para float. Soma na amostra: {df[qtd_col].sum():.0f}")

    # Não existe coluna de combustível — identifica elétricos por palavra-chave
    # na coluna combinada 'Marca Modelo'. Lista baseada em marcas/modelos
    # elétricos vendidos no Brasil em 2026 (BYD, GWM, Geely, GAC, Chevrolet EV).
    marca_modelo_col = next((c for c in df.columns if "marca" in c.lower()), None)
    if marca_modelo_col:
        # Descoberta em execução real: o próprio RENAVAM já sinaliza o tipo
        # de eletrificação no nome do modelo, de forma mais confiável que
        # decorar marcas — não precisa de lista de keywords por fabricante:
        #   - Sufixo 'EV' (ex: 'GL5EV', '310EV', 'GS EV') → BEV (100% elétrico)
        #   - Sufixo 'DM' (ex: 'GL DM', 'GS DM')           → PHEV (híbrido
        #     plug-in, "Dual Mode" na nomenclatura BYD)
        # Mantemos também um fallback de marcas conhecidas para montadoras
        # que não usam essa convenção de sufixo (ex: BMW iX, Nissan Leaf).
        bev_pattern = r"\bEV\b|\dEV\b|EUV\b"  # 'GS EV', 'GL5EV'/'310EV', 'SPARK EUV'
        phev_pattern = r"\bDM\b"  # 'GL DM', 'GS DM' (BYD Dual Mode)
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

        eletrificados = df[is_bev | is_phev | is_fallback].copy()
        print(
            f"\nRegistros identificados como eletrificados na amostra: "
            f"{len(eletrificados)} de {len(df)} "
            f"(BEV: {is_bev.sum()}, PHEV: {is_phev.sum()}, outros/fallback: {is_fallback.sum()})"
        )
        if len(eletrificados) > 0:
            print("\nModelos BEV (100% elétrico) — amostra:")
            print(df[is_bev][marca_modelo_col].unique()[:20])
            print("\nModelos PHEV (híbrido plug-in) — amostra:")
            print(df[is_phev][marca_modelo_col].unique()[:20])
            if is_fallback.sum() > 0:
                print("\nModelos via fallback de marca (sem sufixo EV/DM) — amostra:")
                print(df[is_fallback][marca_modelo_col].unique()[:20])
        print(
            "\nDecisão de escopo: EV ChargeOps considera BEV + PHEV como frota-alvo, "
            "já que ambos carregam via plugue e geram sessões reais no carregador. "
            "O tipo (BEV/PHEV) deve ser preservado como atributo, não usado como filtro "
            "de exclusão — permite análises futuras sem perder o dado.\n"
            "AVISO: heurística por padrão de nome, ainda não é uma coluna oficial de "
            "combustível. Revisar a lista de valores únicos acima antes da Etapa 4."
        )
    else:
        print("\nAVISO: coluna de marca/modelo não encontrada — verifique manualmente.")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    extract_dir = OUTPUT_DIR / "renavam"

    # Evita novo download se o arquivo já foi extraído numa execução anterior.
    existing_files = list(extract_dir.glob("*")) if extract_dir.exists() else []
    if existing_files:
        print(f"Arquivos já extraídos encontrados em {extract_dir}, pulando download.")
        extracted_files = [f.name for f in existing_files]
    else:
        extracted_files = download_and_extract(DOWNLOAD_URL)

    print(f"\nArquivos extraídos: {extracted_files}")

    # Arquivos do governo às vezes vêm com extensão .TXT em vez de .CSV,
    # mesmo sendo delimitados internamente — aceitar ambos.
    data_files = [f for f in extracted_files if f.lower().endswith((".csv", ".txt"))]
    if not data_files:
        print(f"Nenhum .csv/.txt encontrado dentro do .zip. Arquivos: {extracted_files}")
        return

    for file_name in data_files:
        summarize_csv(OUTPUT_DIR / "renavam" / file_name)


if __name__ == "__main__":
    main()