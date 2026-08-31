"""
EV ChargeOps — Exploração da API Open Charge Map (OCM)

Script de exploração inicial: busca uma amostra de pontos de recarga (POIs)
no Brasil para entender o formato dos dados antes de desenhar o star schema
(dim_points, dim_geography etc).

Setup:
    1. Crie uma API key https://openchargemap.org/site/loginprovider
    2. export OCM_API_KEY="sua_key_aqui"
    3. python scripts/01_explore_ocm.py

Docs da API: https://openchargemap.org/site/develop/api
Endpoint usado: GET https://api.openchargemap.io/v3/poi/
"""

import json
import os
from pathlib import Path

import requests

BASE_URL = "https://api.openchargemap.io/v3/poi/"
OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw"
OUTPUT_FILE = OUTPUT_DIR / "ocm_sample_br.json"

API_KEY = os.getenv("OCM_API_KEY", "")


# Busca POIs de recarga na API da OCM e retorna a lista já parseada de JSON.
def fetch_sample(country_code: str = "BR", max_results: int = 100) -> list[dict]:
    params = {
        "output": "json",
        "countrycode": country_code,
        "maxresults": max_results,
        "compact": "false",  # false = retorna todos os campos, não só os essenciais
        "verbose": "false",
    }
    if API_KEY:
        params["key"] = API_KEY

    headers = {"User-Agent": "ev-chargeops-fiap/1.0"}

    response = requests.get(BASE_URL, params=params, headers=headers, timeout=30)
    response.raise_for_status()
    return response.json()


# Extrai do primeiro POI os campos relevantes para o star schema.
def summarize_fields(pois: list[dict]) -> dict:
    if not pois:
        return {}

    sample = pois[0]
    address = sample.get("AddressInfo", {})
    connections = sample.get("Connections", [])

    return {
        # identificação / operador
        "id": sample.get("ID"),
        "operator_info": sample.get("OperatorInfo"),
        # geografia -> dim_geography
        "town": address.get("Town"),
        "state": address.get("StateOrProvince"),
        "latitude": address.get("Latitude"),
        "longitude": address.get("Longitude"),
        # conector -> dim_points (pega só o primeiro conector do POI)
        "power_kw": connections[0].get("PowerKW") if connections else None,
        "connection_type": connections[0].get("ConnectionType") if connections else None,
        # status operacional
        "status_type": sample.get("StatusType"),
        "date_last_status_update": sample.get("DateLastStatusUpdate"),
    }


# Busca a amostra, salva o JSON bruto em data/raw/ e confirma no console.
def main() -> None:
    pois = fetch_sample()

    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(pois, f, ensure_ascii=False, indent=2)

    print(f"{len(pois)} POIs salvos em: {OUTPUT_FILE}")


if __name__ == "__main__":
    main()