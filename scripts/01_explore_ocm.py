"""
EV ChargeOps — Exploração da API Open Charge Map (OCM)

Objetivo: buscar pontos de recarga (POIs) no Brasil e entender o formato
real dos dados retornados, antes de desenhar o star schema.

Docs: https://openchargemap.org/site/develop/api
Endpoint: GET https://api.openchargemap.io/v3/poi/

Requer API key gratuita: registre-se em https://openchargemap.org/site/loginprovider
e cole a key abaixo ou exporte como variável de ambiente OCM_API_KEY.

Uso:
    export OCM_API_KEY="sua_key_aqui"
    python scripts/01_explore_ocm.py
"""

import json
import os
from pathlib import Path

import requests

BASE_URL = "https://api.openchargemap.io/v3/poi/"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUTPUT_FILE = OUTPUT_DIR / "ocm_sample_br.json"

API_KEY = os.getenv("OCM_API_KEY", "")


def fetch_sample(country_code: str = "BR", max_results: int = 100) -> list[dict]:
    """Busca uma amostra de POIs de recarga no Brasil."""
    params = {
        "output": "json",
        "countrycode": country_code,
        "maxresults": max_results,
        "compact": "false",  # detalhado — queremos ver todos os campos disponíveis
        "verbose": "false",
    }
    if API_KEY:
        params["key"] = API_KEY

    headers = {"User-Agent": "ev-chargeops-fiap/1.0"}

    response = requests.get(BASE_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


def summarize_fields(pois: list[dict]) -> None:
    """Imprime um resumo dos campos encontrados no primeiro POI, para
    orientar o desenho de dim_points e dim_geography."""
    if not pois:
        print("Nenhum POI retornado. Verifique a API key ou o countrycode.")
        return

    sample = pois[0]
    print(f"\nTotal de POIs retornados: {len(pois)}")
    print("\nCampos de nível superior do primeiro POI:")
    for key in sample.keys():
        value_type = type(sample[key]).__name__
        print(f"  - {key}: {value_type}")

    # Campos que mais provavelmente interessam ao star schema
    print("\nCampos relevantes para dim_points / dim_geography (primeiro POI):")
    print(f"  ID: {sample.get('ID')}")
    print(f"  OperatorInfo: {sample.get('OperatorInfo')}")
    address = sample.get("AddressInfo", {})
    print(f"  AddressInfo.Town: {address.get('Town')}")
    print(f"  AddressInfo.StateOrProvince: {address.get('StateOrProvince')}")
    print(f"  AddressInfo.Latitude/Longitude: {address.get('Latitude')}, {address.get('Longitude')}")
    connections = sample.get("Connections", [])
    if connections:
        print(f"  Connections[0].PowerKW: {connections[0].get('PowerKW')}")
        print(f"  Connections[0].ConnectionType: {connections[0].get('ConnectionType')}")
    print(f"  StatusType: {sample.get('StatusType')}")
    print(f"  DateLastStatusUpdate: {sample.get('DateLastStatusUpdate')}")


def main() -> None:
    if not API_KEY:
        print(
            "AVISO: variável de ambiente OCM_API_KEY não definida.\n"
            "A API pode funcionar sem key com rate limit reduzido, mas "
            "recomenda-se registrar uma key gratuita em:\n"
            "https://openchargemap.org/site/loginprovider\n"
        )

    print("Buscando amostra de pontos de recarga no Brasil...")
    pois = fetch_sample()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pois, f, ensure_ascii=False, indent=2)

    print(f"Dados brutos salvos em: {OUTPUT_FILE}")
    summarize_fields(pois)


if __name__ == "__main__":
    main()