# Painel do Gestor — Fórmulas DAX (Etapa 8c, parte 2)

Medidas calculadas para os visuais especificados em `painel_gestor_especificacao.md`.
Nomes de tabela usam o prefixo `public ` (com espaço), conforme o Power BI importou
do Postgres neste projeto — ajuste se você renomear as tabelas depois.

Como usar: crie cada medida em `Modelagem → Nova medida`, com a tabela de destino
sugerida entre colchetes no início de cada bloco. Cole o conteúdo do bloco de código
na caixa de fórmulas.

---

## Aba 1 — Visão Geral

### Total de sessões
Tabela sugerida: `public fct_sessoes`
```dax
Total de Sessões = COUNTROWS('public fct_sessoes')
```

### Total de kWh entregue
Tabela sugerida: `public fct_sessoes`
```dax
Total kWh Entregue = SUM('public fct_sessoes'[kwh_delivered])
```

### Faturamento total
Tabela sugerida: `public fct_invoices`
```dax
Faturamento Total = SUM('public fct_invoices'[total_amount])
```

### Usuários ativos
Tabela sugerida: `public dim_users`
```dax
Usuários Ativos = CALCULATE(
    COUNTROWS('public dim_users'),
    'public dim_users'[active] = TRUE
)
```

### Sessões com anomalia
Tabela sugerida: `public fct_sessoes`
```dax
Sessões com Anomalia = CALCULATE(
    COUNTROWS('public fct_sessoes'),
    'public fct_sessoes'[anomaly_flag] = TRUE
)
```

### Taxa de anomalia (%)
Tabela sugerida: `public fct_sessoes`
```dax
Taxa de Anomalia % = DIVIDE([Sessões com Anomalia], [Total de Sessões], 0)
```
<p style="font-family: sans-serif; font-size: 13px; color: #555; border-left: 3px solid #ccc; padding-left: 10px;">
Formate esta medida como percentual na aba <b>Modelagem</b> (ícone "%") para exibir
corretamente no cartão de KPI.
</p>

---

## Aba 2 — Sessões

### Sessões concluídas
Tabela sugerida: `public fct_sessoes`
```dax
Sessões Concluídas = CALCULATE(
    COUNTROWS('public fct_sessoes'),
    'public fct_sessoes'[status] = "concluida"
)
```

### Sessões interrompidas
Tabela sugerida: `public fct_sessoes`
```dax
Sessões Interrompidas = CALCULATE(
    COUNTROWS('public fct_sessoes'),
    'public fct_sessoes'[status] = "interrompida"
)
```

### Duração média de sessão (min)
Tabela sugerida: `public fct_sessoes`
```dax
Duração Média (min) = AVERAGE('public fct_sessoes'[duration_min])
```

### kWh médio por sessão
Tabela sugerida: `public fct_sessoes`
```dax
kWh Médio por Sessão = AVERAGE('public fct_sessoes'[kwh_delivered])
```

### % de sessões em fim de semana
Tabela sugerida: `public fct_sessoes` (requer relacionamento ativo com `public dim_dates`)
```dax
% Sessões Fim de Semana = 
VAR SessoesFimDeSemana =
    CALCULATE(
        COUNTROWS('public fct_sessoes'),
        'public dim_dates'[is_weekend] = TRUE
    )
RETURN
    DIVIDE(SessoesFimDeSemana, [Total de Sessões], 0)
```
<p style="font-family: sans-serif; font-size: 13px; color: #555; border-left: 3px solid #ccc; padding-left: 10px;">
Esta medida depende do relacionamento <code>fct_sessoes.session_date → dim_dates.session_date</code>
estar ativo no modelo (ver especificação de relacionamentos).
</p>

---

## Aba 3 — Faturamento

### Número de faturas pendentes
Tabela sugerida: `public fct_invoices`
```dax
Faturas Pendentes = CALCULATE(
    COUNTROWS('public fct_invoices'),
    'public fct_invoices'[status] = "pendente"
)
```

### Número de faturas em revisão
Tabela sugerida: `public fct_invoices`
```dax
Faturas em Revisão = CALCULATE(
    COUNTROWS('public fct_invoices'),
    'public fct_invoices'[status] = "revisao"
)
```

### Número de faturas pagas
Tabela sugerida: `public fct_invoices`
```dax
Faturas Pagas = CALCULATE(
    COUNTROWS('public fct_invoices'),
    'public fct_invoices'[status] = "paga"
)
```
<p style="font-family: sans-serif; font-size: 13px; color: #b8860b; border-left: 3px solid #d4a017; padding-left: 10px; background-color: #fffbea;">
⚠️ Com os dados atuais do piloto, esta medida sempre retorna 0 — o motor de rateio
(<code>scripts/06_billing_engine.py</code>) só gera faturas com status
<code>pendente</code> ou <code>revisao</code>; o status <code>paga</code> é válido no
schema mas ainda não é simulado por nenhum script. A fórmula está correta e pronta
para quando um fluxo de pagamento for implementado.
</p>

### Ticket médio
Tabela sugerida: `public fct_invoices`
```dax
Ticket Médio = AVERAGE('public fct_invoices'[total_amount])
```

### Faturamento por plano (%)
Tabela sugerida: `public fct_invoices` (requer relacionamento com `public dim_plans`)
```dax
% Faturamento do Plano = 
DIVIDE(
    [Faturamento Total],
    CALCULATE([Faturamento Total], ALL('public dim_plans')),
    0
)
```
<p style="font-family: sans-serif; font-size: 13px; color: #555; border-left: 3px solid #ccc; padding-left: 10px;">
Use esta medida em conjunto com um visual segmentado por <code>plan_type</code>
(ex: gráfico de rosca) — o <code>ALL('public dim_plans')</code> remove o filtro de
plano para calcular o percentual em relação ao total geral, não ao subtotal do
próprio segmento.
</p>

---

## Aba 4 — Usuários / Pontos

### Total de usuários
Tabela sugerida: `public dim_users`
```dax
Total de Usuários = COUNTROWS('public dim_users')
```

### Total de veículos cadastrados
Tabela sugerida: `public dim_vehicles`
```dax
Total de Veículos = COUNTROWS('public dim_vehicles')
```

### Usuários com 2 veículos
Tabela sugerida: `public dim_vehicles`
```dax
Usuários com 2 Veículos = 
CALCULATE(
    DISTINCTCOUNT('public dim_vehicles'[user_id]),
    'public dim_vehicles'[is_primary] = FALSE
)
```
<p style="font-family: sans-serif; font-size: 13px; color: #555; border-left: 3px solid #ccc; padding-left: 10px;">
Conta usuários distintos que têm pelo menos um veículo secundário
(<code>is_primary = FALSE</code>) — cada usuário com 2º veículo aparece só uma vez,
mesmo que <code>dim_vehicles</code> tenha 2 linhas para ele.
</p>

### Veículos BEV
Tabela sugerida: `public dim_vehicles`
```dax
Veículos BEV = CALCULATE(
    COUNTROWS('public dim_vehicles'),
    'public dim_vehicles'[vehicle_type] = "BEV"
)
```

### Veículos PHEV
Tabela sugerida: `public dim_vehicles`
```dax
Veículos PHEV = CALCULATE(
    COUNTROWS('public dim_vehicles'),
    'public dim_vehicles'[vehicle_type] = "PHEV"
)
```

### Pontos de recarga ativos
Tabela sugerida: `public dim_points`
```dax
Pontos Ativos = CALCULATE(
    COUNTROWS('public dim_points'),
    'public dim_points'[status] = "ativo"
)
```

### Sessões por ponto (medida para a tabela de utilização)
Tabela sugerida: `public fct_sessoes` (requer relacionamento com `public dim_points`)
```dax
Sessões por Ponto = COUNTROWS('public fct_sessoes')
```
<p style="font-family: sans-serif; font-size: 13px; color: #555; border-left: 3px solid #ccc; padding-left: 10px;">
Adicione esta medida como coluna de valor na tabela de pontos de recarga (aba 4,
visual 4) — o Power BI aplica automaticamente o contexto de filtro de
<code>dim_points</code> por causa do relacionamento, então a mesma medida
<code>COUNTROWS</code> retorna a contagem correta por linha da tabela.
</p>

---

## Observação geral sobre nomes de coluna

As tabelas do Postgres usam nomes em `snake_case` (ex: `kwh_delivered`,
`total_amount`). O Power BI preserva esses nomes ao importar — as fórmulas acima
usam os nomes exatos do schema (`sql/01_star_schema.sql`). Se você renomear colunas
na camada de modelo do Power BI para nomes mais amigáveis (ex: "kWh Entregue" em vez
de "kwh_delivered"), lembre de ajustar as referências nas fórmulas correspondentes.