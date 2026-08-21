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

# Agregado 6579 = Estimativas da População (tabela SIDRA 6579).
# Variável 9324 = "População residente estimada (Pessoas)" — confirmado
# na documentação do agregado.
# IMPORTANTE: o separador de múltiplos IDs dentro de um mesmo nível
# geográfico é VÍRGULA (N6[id1,id2,id3]), não pipe. O pipe (|) é usado
# apenas para combinar NÍVEIS diferentes na mesma consulta (ex: N7|N6).
# A primeira versão deste script usava '|' entre municípios, o que gerava
# erro 500 no servidor do IBGE.
AGREGADOS_URL = (
    "https://servicodados.ibge.gov.br/api/v3/agregados/6579/periodos/-1"
    "/variaveis/9324?localidades=N6[{municipio_ids}]"
)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"

# Estados de maior concentração de frota elétrica, conforme Sprint 01
# (SP, DF, RJ) — usados como amostra inicial de exploração.
UFS_AMOSTRA = ["SP", "DF", "RJ"]


def fetch_municipios(uf: str) -> list[dict]:
    url = LOCALIDADES_URL.format(uf=uf)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def fetch_populacao(municipio_ids: list[int]) -> dict:
    ids_str = ",".join(str(i) for i in municipio_ids)
    url = AGREGADOS_URL.format(municipio_ids=ids_str)
    response = requests.get(url, timeout=30)
    response.raise_for_status()
    return response.json()


def summarize_municipio_fields(municipios: list[dict], uf: str) -> None:
    if not municipios:
        print(f"Nenhum município retornado para {uf}.")
        return

    sample = municipios[0]
    print(f"\n[{uf}] Total de municípios: {len(municipios)}")
    print(f"[{uf}] Campos do primeiro município ({sample.get('nome')}):")
    print(json.dumps(sample, ensure_ascii=False, indent=2)[:800])


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    all_municipios = {}

    for uf in UFS_AMOSTRA:
        print(f"\nBuscando municípios de {uf}...")
        municipios = fetch_municipios(uf)
        all_municipios[uf] = municipios
        summarize_municipio_fields(municipios, uf)

    output_file = OUTPUT_DIR / "ibge_municipios_sample.json"
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(all_municipios, f, ensure_ascii=False, indent=2)
    print(f"\nDados brutos de localidades salvos em: {output_file}")

    # Teste pontual da API de Agregados usando os 3 primeiros municípios de SP.
    # NOTA: o ID de agregado/variável acima é um ponto de partida — validar
    # a resposta real e ajustar se necessário (a documentação SIDRA tem
    # centenas de agregados; pode ser preciso trocar por um mais específico
    # para densidade demográfica).
    try:
        sample_ids = [m["id"] for m in all_municipios["SP"][:3]]
        print(f"\nTestando API de Agregados (população) para IDs: {sample_ids}...")
        populacao = fetch_populacao(sample_ids)
        pop_file = OUTPUT_DIR / "ibge_populacao_sample.json"
        with open(pop_file, "w", encoding="utf-8") as f:
            json.dump(populacao, f, ensure_ascii=False, indent=2)
        print(f"Dados brutos de população salvos em: {pop_file}")
        print(json.dumps(populacao, ensure_ascii=False, indent=2)[:1000])
    except requests.HTTPError as e:
        print(f"\nAVISO: chamada à API de Agregados falhou ({e}).")
        print("O agregado/variável usado no script é um ponto de partida — "
              "consulte https://servicodados.ibge.gov.br/api/docs/agregados?versao=3 "
              "para confirmar o ID correto antes de seguir para a Etapa 4.")


if __name__ == "__main__":
    main()