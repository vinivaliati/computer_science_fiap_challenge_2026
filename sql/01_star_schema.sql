-- ============================================================
-- EV ChargeOps — Star Schema
-- Sprint 02 · Etapa 4 (revisão 2)
--
-- Histórico de decisões em relação ao desenho inicial (SVG da Sprint 01):
--   - dim_dates única (grão diário) carrega também ref_month, em vez de
--     uma dim_months separada — fct_invoices agrega por ref_month.
--   - dim_points (carregador físico, dado do piloto) e dim_geography
--     (contexto agregado por município, vindo de RENAVAM/IBGE) seguem
--     como dimensões separadas — granularidades diferentes.
--   - dim_plans adicionada: o motor de rateio (Etapa 6) precisa das
--     tarifas de cada plano estruturadas, não hardcoded no código.
--   - dim_vehicles adicionada: promove vehicle_type (BEV/PHEV) de atributo
--     solto em dim_users para uma dimensão própria, 1 linha por veículo —
--     resolve o caso de usuário com 2 veículos, cada um com seu próprio tipo.
--
-- 2 tabelas fato + 6 dimensões:
--   dim_dates, dim_users, dim_vehicles, dim_points, dim_plans, dim_geography
-- ============================================================

-- ────────────────────────────────────────────
-- DIMENSÕES
-- ────────────────────────────────────────────

-- Grão diário. Alimenta fct_sessoes (via session_date) e fct_invoices
-- (via ref_month — o primeiro dia do mês da mesma linha).
CREATE TABLE dim_dates (
    session_date    DATE PRIMARY KEY,
    ref_month       DATE NOT NULL,       -- primeiro dia do mês desta data (ex: 2026-03-01)
    day_of_week     VARCHAR(15) NOT NULL,
    month_name      VARCHAR(15) NOT NULL,
    year            INT NOT NULL,
    quarter         INT NOT NULL,
    is_weekend      BOOLEAN NOT NULL
);

-- Tarifas e regras de cada plano de cobrança, conforme as fórmulas
-- definidas na Sprint 01 (Frente 3):
--   pay_per_use          -> fatura = duracao_min * rate_per_min
--   assinatura            -> fatura = monthly_fee + (duracao_min * rate_per_min_discounted)
--   pacote_condominial    -> fatura = fixed_cost_monthly + (duracao_min * rate_per_min), rateado
CREATE TABLE dim_plans (
    plan_type               VARCHAR(30) PRIMARY KEY,  -- pay_per_use / assinatura / pacote_condominial
    description              VARCHAR(150),
    rate_per_min             DECIMAL(8,4),   -- tarifa cheia por minuto (pay_per_use e pacote)
    rate_per_min_discounted  DECIMAL(8,4),   -- tarifa com desconto (assinatura)
    monthly_fee              DECIMAL(8,2),   -- taxa fixa mensal (assinatura)
    fixed_cost_monthly       DECIMAL(10,2),  -- custo fixo de infraestrutura (pacote condominial)
    active                   BOOLEAN NOT NULL DEFAULT TRUE
);

CREATE TABLE dim_users (
    user_id             VARCHAR(20) PRIMARY KEY,
    name                VARCHAR(120) NOT NULL,
    email               VARCHAR(150) NOT NULL,
    unit                VARCHAR(20),          -- unidade/apto no condomínio
    rfid_tag            VARCHAR(50),
    active              BOOLEAN NOT NULL DEFAULT TRUE,
    user_type           VARCHAR(20),           -- ex: morador, visitante, corporativo
    plan_type           VARCHAR(30) NOT NULL REFERENCES dim_plans(plan_type),
    point_id            VARCHAR(20)            -- ponto de recarga habitual do usuário
);

-- 1 linha por veículo (não por usuário) — suporta o cenário de unidade
-- com 2 veículos, cada um com seu próprio tipo BEV/PHEV.
CREATE TABLE dim_vehicles (
    vehicle_id      VARCHAR(20) PRIMARY KEY,   -- ex: U0002-V1, U0002-V2
    user_id         VARCHAR(20) NOT NULL REFERENCES dim_users(user_id),
    vehicle_type    VARCHAR(10),               -- BEV / PHEV — heurística confirmada
                                                -- na exploração do RENAVAM (Etapa 2)
    is_primary      BOOLEAN NOT NULL DEFAULT TRUE  -- V1 = primário, V2 = secundário
);

-- Carregador físico. Dado do piloto GoodWe HCA G2 / simulação (Etapa 3).
CREATE TABLE dim_points (
    point_id        VARCHAR(20) PRIMARY KEY,
    model           VARCHAR(60) NOT NULL,   -- ex: GoodWe HCA G2
    location        VARCHAR(150),
    power_kw        DECIMAL(5,2),
    protocol        VARCHAR(20),             -- OCPP 1.6 / 2.0
    status          VARCHAR(20),
    installed_at    DATE,
    geo_id          VARCHAR(20)              -- FK para dim_geography — em que
                                              -- município/região este ponto está
);

-- Contexto agregado por município. Fontes: RENAVAM (frota BEV/PHEV),
-- IBGE (população, localidades), Open Charge Map (cobertura de eletropostos).
-- Grão: 1 linha por município — não por ponto de recarga individual.
CREATE TABLE dim_geography (
    geo_id                  VARCHAR(20) PRIMARY KEY,
    city                    VARCHAR(100) NOT NULL,
    state                   VARCHAR(2) NOT NULL,
    ibge_municipio_id       INT,               -- código IBGE (N6), quando reconciliado
    population_estimate     INT,               -- IBGE Agregados (tabela 6579)
    ev_fleet_bev_count      INT,                -- RENAVAM, frota BEV
    ev_fleet_phev_count     INT,                -- RENAVAM, frota PHEV
    chargepoints_count      INT                 -- Open Charge Map, cobertura pública
);

-- ────────────────────────────────────────────
-- FATOS
-- ────────────────────────────────────────────

CREATE TABLE fct_sessoes (
    session_id      VARCHAR(30) PRIMARY KEY,
    user_id         VARCHAR(20) NOT NULL REFERENCES dim_users(user_id),
    vehicle_id      VARCHAR(20) NOT NULL REFERENCES dim_vehicles(vehicle_id),
    point_id        VARCHAR(20) NOT NULL REFERENCES dim_points(point_id),
    session_date    DATE NOT NULL REFERENCES dim_dates(session_date),
    plan_type       VARCHAR(30) NOT NULL REFERENCES dim_plans(plan_type),
                                             -- desnormalizado da dim_users no momento
                                             -- da sessão, para preservar o plano
                                             -- vigente mesmo se o usuário trocar depois
    duration_min    INT NOT NULL,
    kwh_delivered   DECIMAL(8,3) NOT NULL,
    status          VARCHAR(20) NOT NULL,   -- concluida / interrompida
    anomaly_flag    BOOLEAN NOT NULL DEFAULT FALSE
);

CREATE TABLE fct_invoices (
    invoice_id      VARCHAR(30) PRIMARY KEY,
    user_id         VARCHAR(20) NOT NULL REFERENCES dim_users(user_id),
    ref_month       DATE NOT NULL,          -- primeiro dia do mês faturado —
                                             -- referencia dim_dates.ref_month
                                             -- (sem FK direta, pois dim_dates
                                             -- tem grão diário; ver nota abaixo)
    plan_type       VARCHAR(30) NOT NULL REFERENCES dim_plans(plan_type),
    total_kwh       DECIMAL(10,3) NOT NULL,
    total_amount    DECIMAL(10,2) NOT NULL,
    status          VARCHAR(20) NOT NULL    -- pendente / paga / revisao
);

-- ────────────────────────────────────────────
-- ÍNDICES (colunas de junção mais consultadas)
-- ────────────────────────────────────────────

CREATE INDEX idx_fct_sessoes_user      ON fct_sessoes(user_id);
CREATE INDEX idx_fct_sessoes_vehicle   ON fct_sessoes(vehicle_id);
CREATE INDEX idx_fct_sessoes_point     ON fct_sessoes(point_id);
CREATE INDEX idx_fct_sessoes_date      ON fct_sessoes(session_date);
CREATE INDEX idx_fct_invoices_user     ON fct_invoices(user_id);
CREATE INDEX idx_fct_invoices_month    ON fct_invoices(ref_month);
CREATE INDEX idx_dim_dates_ref_month   ON dim_dates(ref_month);
CREATE INDEX idx_dim_points_geo        ON dim_points(geo_id);
CREATE INDEX idx_dim_vehicles_user     ON dim_vehicles(user_id);

-- ────────────────────────────────────────────
-- NOTA SOBRE fct_invoices.ref_month
-- ────────────────────────────────────────────
-- fct_invoices.ref_month não tem FK direta para dim_dates porque dim_dates
-- tem grão diário (PK = session_date) — uma FK exigiria apontar para um dia
-- específico do mês, o que é artificial para uma fatura mensal. A junção
-- correta para análises por mês é:
--   SELECT ... FROM fct_invoices i
--   JOIN dim_dates d ON d.ref_month = i.ref_month
-- (retorna múltiplas linhas de dim_dates por fatura — usar DISTINCT ou
-- agregação ao consultar, ou fazer join com uma subquery que já traga
-- ref_month únicos de dim_dates.)