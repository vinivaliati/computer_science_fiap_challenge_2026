"""
EV ChargeOps — Geração do dataset simulado de sessões

Objetivo: gerar um dataset sintético de sessões de recarga cobrindo 6 meses
(março–agosto/2026), múltiplos usuários, os 3 planos de cobrança e os
cenários excepcionais descritos na Sprint 01:
  - Sessão interrompida
  - Usuário sem uso no mês
  - Dois veículos na mesma unidade
  - Consumo fora do padrão (anomalia)

Escopo (definido com o time):
  - 150 usuários
  - 4 pontos de recarga (simulando condomínios/prédios diferentes)
  - Distribuição realista de planos: maioria pay-per-use, menos em
    assinatura individual, poucos no pacote condominial
  - Período: 2026-03-01 a 2026-08-31

Saída: CSVs brutos em data/raw/simulado/ — ainda não no formato do star
schema (isso é trabalho da Etapa 5, transformação e carga).

Uso:
    python scripts/04_generate_sessions.py
"""

import random
from datetime import date, datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
from faker import Faker

fake = Faker("pt_BR")

RANDOM_SEED = 42
random.seed(RANDOM_SEED)
np.random.seed(RANDOM_SEED)
Faker.seed(RANDOM_SEED)

OUTPUT_DIR = Path(__file__).resolve().parent.parent / "data" / "raw" / "simulado"

N_USERS = 150
N_POINTS = 4
PERIOD_START = date(2026, 3, 1)
PERIOD_END = date(2026, 8, 31)

# Distribuição realista de planos: a maioria paga por uso avulso, uma fatia
# menor assina um plano fixo, e pouquíssimos estão num pacote condominial
# (que exige contrato do condomínio inteiro, não é adesão individual).
PLAN_WEIGHTS = {
    "pay_per_use": 0.65,
    "assinatura": 0.25,
    "pacote_condominial": 0.10,
}

POINT_MODELS = ["GoodWe HCA G2"] * N_POINTS
POINT_LOCATIONS = [
    "Residencial Jardim das Palmeiras - Garagem G1",
    "Edifício Bela Vista - Subsolo 2",
    "Condomínio Parque das Águas - Vaga Coletiva",
    "Campus FIAP - Estacionamento Docente",
]
POINT_POWER_KW = [22.0, 11.0, 22.0, 7.4]


def generate_points() -> pd.DataFrame:
    rows = []
    for i in range(N_POINTS):
        rows.append({
            "point_id": f"P{i+1:03d}",
            "model": POINT_MODELS[i],
            "location": POINT_LOCATIONS[i],
            "power_kw": POINT_POWER_KW[i],
            "protocol": random.choice(["OCPP 1.6", "OCPP 2.0"]),
            "status": "ativo",
            "installed_at": fake.date_between(start_date=date(2025, 1, 1), end_date=date(2026, 1, 1)),
        })
    return pd.DataFrame(rows)


def generate_users(points_df: pd.DataFrame) -> pd.DataFrame:
    plan_choices = list(PLAN_WEIGHTS.keys())
    plan_probs = list(PLAN_WEIGHTS.values())

    rows = []
    for i in range(N_USERS):
        user_id = f"U{i+1:04d}"
        plan_type = np.random.choice(plan_choices, p=plan_probs)
        # Usuário é vinculado a um ponto de recarga (seu condomínio/prédio).
        point_id = random.choice(points_df["point_id"].tolist())
        # ~8% das unidades têm dois veículos cadastrados (cenário excepcional).
        has_second_vehicle = random.random() < 0.08

        rows.append({
            "user_id": user_id,
            "name": fake.name(),
            "email": fake.email(),
            "unit": f"{random.randint(1, 20)}{random.choice('ABCD')}",
            "rfid_tag": f"RFID{i+1:05d}",
            "active": True,
            "user_type": "morador",
            "plan_type": plan_type,
            "point_id": point_id,
            "has_second_vehicle": has_second_vehicle,
        })
    return pd.DataFrame(rows)


def month_range(start: date, end: date) -> list[date]:
    months = []
    current = date(start.year, start.month, 1)
    while current <= end:
        months.append(current)
        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)
    return months


def random_session_datetime(month_start: date) -> datetime:
    """Gera um horário de sessão com viés realista: mais provável à noite
    (quando o morador chega em casa) e nos dias úteis."""
    if month_start.month == 12:
        next_month = date(month_start.year + 1, 1, 1)
    else:
        next_month = date(month_start.year, month_start.month + 1, 1)
    days_in_month = (next_month - month_start).days
    day_offset = random.randint(0, days_in_month - 1)
    session_date = month_start + timedelta(days=day_offset)

    # Horário de início: pico entre 18h e 23h (chegada em casa à noite),
    # com uma cauda menor pela manhã (quem carrega antes de sair).
    hour_weights = np.array([
        0.5, 0.3, 0.2, 0.2, 0.2, 0.3,  # 0-5h
        0.8, 1.2, 1.0, 0.6, 0.5, 0.5,  # 6-11h
        0.5, 0.5, 0.5, 0.5, 0.6, 0.8,  # 12-17h
        1.5, 2.5, 2.8, 2.2, 1.5, 0.9,  # 18-23h
    ])
    hour = np.random.choice(range(24), p=hour_weights / hour_weights.sum())
    minute = random.randint(0, 59)

    return datetime(session_date.year, session_date.month, session_date.day, hour, minute)


def generate_sessions(users_df: pd.DataFrame, points_df: pd.DataFrame) -> pd.DataFrame:
    power_by_point = dict(zip(points_df["point_id"], points_df["power_kw"]))
    months = month_range(PERIOD_START, PERIOD_END)

    sessions = []
    session_counter = 1

    for _, user in users_df.iterrows():
        # Frequência de uso mensal varia por usuário (alguns carregam quase
        # todo dia útil, outros só esporadicamente).
        base_sessions_per_month = max(1, int(np.random.normal(loc=10, scale=4)))

        for month_start in months:
            # Cenário excepcional: usuário sem uso no mês (~7% de chance
            # por usuário-mês, mais provável em planos pay-per-use, já que
            # não têm taxa fixa obrigando o uso).
            skip_probability = 0.10 if user["plan_type"] == "pay_per_use" else 0.03
            if random.random() < skip_probability:
                continue

            n_sessions_this_month = max(0, int(np.random.poisson(base_sessions_per_month)))

            # Se o usuário tem um segundo veículo, gera sessões extras
            # atribuídas a um vehicle_id diferente na mesma unidade.
            vehicle_ids = ["V1"]
            if user["has_second_vehicle"]:
                vehicle_ids.append("V2")

            for _ in range(n_sessions_this_month):
                vehicle_id = random.choice(vehicle_ids)
                start_dt = random_session_datetime(month_start)

                power_kw = power_by_point[user["point_id"]]
                # Duração típica de sessão: 30min a 4h, com maior massa em
                # torno de 1-2h (recarga noturna parcial).
                duration_min = max(10, int(np.random.gamma(shape=3, scale=35)))

                # kWh entregue é proporcional à potência do ponto e à
                # duração, com alguma perda/variação realista.
                theoretical_kwh = power_kw * (duration_min / 60) * random.uniform(0.75, 0.95)

                # Cenário excepcional: sessão interrompida (~4% das sessões).
                # Cobra pelo tempo/kWh até o encerramento; fica marcada para
                # auditoria conforme a regra de negócio da Sprint 01.
                is_interrupted = random.random() < 0.04
                status = "interrompida" if is_interrupted else "concluida"
                if is_interrupted:
                    # Interrompe em algum ponto entre 10% e 70% da sessão planejada.
                    cutoff = random.uniform(0.1, 0.7)
                    duration_min = max(5, int(duration_min * cutoff))
                    theoretical_kwh = theoretical_kwh * cutoff

                # Cenário excepcional: consumo fora do padrão / anomalia
                # (~2% das sessões). Simula erro de medição ou uso atípico
                # (ex: veículo com bateria maior que o normal, falha no
                # medidor). É sinalizado para revisão, não é um erro do
                # gerador — é o cenário que a IA de detecção de anomalias
                # (Etapa 7) deve conseguir capturar.
                is_anomaly = random.random() < 0.02
                if is_anomaly:
                    theoretical_kwh = theoretical_kwh * random.uniform(2.5, 4.0)

                kwh_delivered = round(max(0.1, theoretical_kwh), 3)

                sessions.append({
                    "session_id": f"S{session_counter:06d}",
                    "user_id": user["user_id"],
                    "vehicle_id": vehicle_id,
                    "point_id": user["point_id"],
                    "plan_type": user["plan_type"],
                    "session_date": start_dt.date().isoformat(),
                    "session_datetime": start_dt.isoformat(),
                    "duration_min": duration_min,
                    "kwh_delivered": kwh_delivered,
                    "status": status,
                    "anomaly_flag": is_anomaly,
                })
                session_counter += 1

    return pd.DataFrame(sessions)


def print_summary(users_df: pd.DataFrame, points_df: pd.DataFrame, sessions_df: pd.DataFrame) -> None:
    print(f"\nUsuários gerados: {len(users_df)}")
    print(f"Pontos de recarga gerados: {len(points_df)}")
    print(f"Sessões geradas: {len(sessions_df)}")

    print("\nDistribuição de planos (usuários):")
    print(users_df["plan_type"].value_counts())

    print("\nUsuários com segundo veículo:", users_df["has_second_vehicle"].sum())

    print("\nSessões por status:")
    print(sessions_df["status"].value_counts())

    print("\nSessões marcadas como anomalia:", sessions_df["anomaly_flag"].sum())

    print("\nSessões por mês:")
    sessions_df["month"] = pd.to_datetime(sessions_df["session_date"]).dt.to_period("M")
    print(sessions_df["month"].value_counts().sort_index())

    # Confirma o cenário "usuário sem uso no mês": usuários com menos de 6
    # meses representados em session_date.
    users_months = sessions_df.groupby("user_id")["month"].nunique()
    users_with_gaps = (users_months < 6).sum()
    print(f"\nUsuários com pelo menos 1 mês sem sessões: {users_with_gaps} de {len(users_df)}")


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    points_df = generate_points()
    users_df = generate_users(points_df)
    sessions_df = generate_sessions(users_df, points_df)

    points_df.to_csv(OUTPUT_DIR / "points.csv", index=False)
    users_df.to_csv(OUTPUT_DIR / "users.csv", index=False)
    sessions_df.to_csv(OUTPUT_DIR / "sessions.csv", index=False)

    print(f"Arquivos salvos em: {OUTPUT_DIR}")
    print_summary(users_df, points_df, sessions_df)


if __name__ == "__main__":
    main()