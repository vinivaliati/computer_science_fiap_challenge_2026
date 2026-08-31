"""
EV ChargeOps — Exploração da API IBGE

Objetivo: buscar municípios (localidades) e dados demográficos agregados,
para entender o formato real antes de desenhar dim_geography.

Duas APIs distintas, ambas sem necessidade de API key:

1) Localidades — hierarquia geográfica (UF, município, região)
   Docs: https://servicodados.ibge.gov.br/api/docs/localidades
   Endpoint usado: /api/v1/localidades/estados/SP/municipios

2) Agregados (SIDRA) — dados de população/censo por município
   Docs: https://servicodados.ibge.gov.br/api/docs/agregados?versao=3
   Endpoint usado: /api/v3/agregados/{agregado}/periodos/{periodo}/variaveis/{variavel}

Uso:
    python scripts/02_explore_ibge.py
"""

import json
from pathlib import Path

import requests

LOCALIDADES_URL = "https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios"

# Agregado 6579 = Estimativas da População (SIDRA). Variável 9324 = população.
# Múltiplos IDs usam VÍRGULA (N6[id1,id2]) — pipe (|) causa erro 500 no IBGE.
AGREGADOS_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1"
    "/variaveis/9324?localidades=N6[{municipio_ids}]"
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Estados de maior concentração de frota elétrica, conforme Sprint 01.
UFS_AMOSTRA = ["SP", "DF", "RJ"]


# Busca a lista de municípios de uma UF.
def fetch_municipios(uf: str) -> list[dict]:
    url = LOCALIDADES_URL.format(uf=uf)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# Busca a população estimada de uma lista de municípios (por ID).
def fetch_populacao(municipio_ids: list[int]) -> dict:
    ids_str = ",".join(str(i) for i in municipio_ids)
    url = AGREGADOS_URL.format(municipio_ids=ids_str)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


# Busca municípios das UFs de amostra e salva o JSON bruto combinado.
def fetch_and_save_municipios() -> dict:
    all_municipios = {}
    for uf in UFS_AMOSTRA:
        all_municipios[uf] = fetch_municipios(uf)

    output_file = OUTPUT_DIR / "ibge_municipios_sample.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_municipios, f, ensure_ascii=False, indent=2)
    print(f"Municípios salvos em: {output_file}")

    return all_municipios


# Testa a API de Agregados com 3 municípios de SP e salva o resultado.
# Agregado/variável são ponto de partida validar se a resposta vier estranha.
def fetch_and_save_populacao(all_municipios: dict) -> None:
    sample_ids = [m["id"] for m in all_municipios["SP"][:3]]
    try:
        populacao = fetch_populacao(sample_ids)
        pop_file = OUTPUT_DIR / "ibge_populacao_sample.json"
        with open(pop_file, "w", encoding="utf-8") as f:
            json.dump(populacao, f, ensure_ascii=False, indent=2)
        print(f"População salva em: {pop_file}")
    except requests.HTTPError as e:
        print(f"AVISO: chamada à API de Agregados falhou ({e}). "
              "Confirme agregado/variável em "
              "https://servicodados.ibge.gov.br/api/docs/agregados?versao=3")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_municipios = fetch_and_save_municipios()
    fetch_and_save_populacao(all_municipios)


if __name__ == "__main__":
    main()