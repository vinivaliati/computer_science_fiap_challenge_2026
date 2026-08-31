# Painel do Gestor — Especificação (Etapa 8c)

Documento de especificação para montagem manual no Power BI (estrutura + visuais +
conexão + joins). As fórmulas DAX ficam para uma etapa seguinte, depois que a
estrutura estiver montada no Figma/Power BI.

## Conexão com a fonte de dados

- **Tipo de fonte**: PostgreSQL (via conector nativo do Power BI — `Obter dados →
  Banco de dados → PostgreSQL`)
- **Servidor**: `localhost` (ou o host do seu `.env`), porta `5432` (ou o valor de
  `POSTGRES_PORT`)
- **Banco de dados**: `evchargeops` (ou o valor de `POSTGRES_DB`)
- **Modo de conectividade recomendado**: **Import** (não DirectQuery) — o volume de
  dados é pequeno (milhares de linhas, não milhões), e o modo Import permite usar
  todos os recursos de modelagem do Power BI sem depender de uma conexão ativa e
  rápida com o Postgres a cada interação do usuário no painel.

## Tabelas a conectar

Conectar as 8 tabelas do star schema (`sql/01_star_schema.sql`):

| Tabela | Tipo | Papel no modelo |
|---|---|---|
| `fct_sessoes` | Fato | Sessões de recarga individuais |
| `fct_invoices` | Fato | Faturas mensais por usuário |
| `dim_dates` | Dimensão | Datas (grão diário, com `ref_month` embutido) |
| `dim_users` | Dimensão | Usuários/moradores |
| `dim_vehicles` | Dimensão | Veículos (1 linha por veículo, não por usuário) |
| `dim_points` | Dimensão | Pontos de recarga físicos |
| `dim_plans` | Dimensão | Planos de cobrança e tarifas |
| `dim_geography` | Dimensão | Contexto de município (não usada neste painel — é
  específica do modelo de score de expansão, aba de IA) |

## Relacionamentos (joins) no modelo do Power BI

Depois de importar as 8 tabelas, o Power BI tenta detectar relacionamentos
automaticamente — **confirme manualmente cada um** na visão de modelo (`Modelagem →
Gerenciar relacionamentos`), porque a detecção automática pode errar a direção do
filtro ou a cardinalidade:

| De (lado "muitos") | Para (lado "um") | Coluna | Cardinalidade | Direção do filtro |
|---|---|---|---|---|
| `fct_sessoes` | `dim_users` | `user_id` | Muitos-para-um | Único (dim → fato) |
| `fct_sessoes` | `dim_vehicles` | `vehicle_id` | Muitos-para-um | Único |
| `fct_sessoes` | `dim_points` | `point_id` | Muitos-para-um | Único |
| `fct_sessoes` | `dim_dates` | `session_date` | Muitos-para-um | Único |
| `fct_sessoes` | `dim_plans` | `plan_type` | Muitos-para-um | Único |
| `fct_invoices` | `dim_users` | `user_id` | Muitos-para-um | Único |
| `fct_invoices` | `dim_plans` | `plan_type` | Muitos-para-um | Único |
| `dim_vehicles` | `dim_users` | `user_id` | Muitos-para-um | Único |
| `dim_points` | `dim_geography` | `geo_id` | Muitos-para-um | Único (pode ficar sem uso neste painel) |

### Atenção especial: `fct_invoices.ref_month` não tem relacionamento direto com `dim_dates`

Como documentado no DDL (`sql/01_star_schema.sql`), `dim_dates` tem grão **diário**
(chave primária é `session_date`), mas `fct_invoices.ref_month` é **mensal**. Uma
relação direta `fct_invoices.ref_month → dim_dates.session_date` criaria uma relação
muitos-para-muitos incorreta (cada mês bate com ~30 linhas de `dim_dates`).

**Solução recomendada no Power BI**: criar uma tabela de datas mensal separada via
Power Query (`Modelagem → Nova tabela` ou uma consulta agrupando `dim_dates` por
`ref_month`), e relacionar `fct_invoices.ref_month` a essa tabela mensal. Isso fica
para a etapa de fórmulas — por ora, ao montar a aba de Faturamento, use os campos de
`fct_invoices` diretamente (o próprio `ref_month` já serve como eixo de tempo em
gráficos, mesmo sem uma dimensão de data mensal dedicada).

---

## Aba 1 — Visão Geral

Objetivo: leitura rápida da saúde operacional do piloto em um único olhar (a aba que
o gestor abre primeiro).

### Visuais

1. **Cartões de KPI** (topo da página, 4-5 cartões lado a lado):
   - Total de sessões no período
   - Total de kWh entregue
   - Faturamento total (R$)
   - Número de usuários ativos
   - Número de sessões marcadas como anomalia (`anomaly_flag = true`)

2. **Gráfico de linha**: sessões por mês (eixo X = `dim_dates.month_name`/`year`,
   eixo Y = contagem de `fct_sessoes`) — mostra a evolução do uso da plataforma ao
   longo do piloto.

3. **Gráfico de rosca (donut)**: distribuição de sessões por status
   (`fct_sessoes.status`: concluída vs. interrompida).

4. **Gráfico de barras horizontais**: sessões por ponto de recarga
   (`dim_points.location`, eixo X = contagem) — mostra qual ponto concentra mais uso.

5. **Segmentador de página (slicer)**: filtro de intervalo de datas
   (`dim_dates.ref_month`), aplicado a toda a aba.

---

## Aba 2 — Sessões

Objetivo: detalhamento operacional das sessões de recarga — o gestor usa esta aba
para investigar padrões de uso e sessões específicas.

### Visuais

1. **Tabela detalhada**: lista de sessões com colunas `session_id`, `session_date`,
   `user_id` (via `dim_users.name`), `point_id` (via `dim_points.location`),
   `duration_min`, `kwh_delivered`, `status`, `anomaly_flag` — com paginação/scroll.

2. **Gráfico de dispersão (scatter)**: `duration_min` (eixo X) vs. `kwh_delivered`
   (eixo Y), colorido por `anomaly_flag` — visualiza os outliers que o modelo de
   detecção de anomalias (Etapa 7c) identificou.

3. **Gráfico de colunas empilhadas**: sessões por dia da semana
   (`dim_dates.day_of_week`), empilhado por `status` — mostra se há padrão de uso
   por dia útil vs. fim de semana.

4. **Mapa de calor (matrix visual)**: horário do dia x dia da semana, com a
   contagem de sessões como valor — identifica horários de pico (**nota**: a hora do
   dia não está persistida em `fct_sessoes.session_date`, que é só `DATE` — será
   necessário importar `data/raw/simulado/sessions.csv`, que tem
   `session_datetime` completo, como uma tabela auxiliar só para este visual, ou
   ajustar o schema para adicionar hora à fato em uma iteração futura).

5. **Segmentadores**: por `point_id`, por `status`, por `anomaly_flag`.

---

## Aba 3 — Faturamento

Objetivo: visão financeira — quanto está sendo faturado, por quem, em qual plano,
com qual status.

### Visuais

1. **Cartões de KPI**: faturamento total, número de faturas pendentes, número de
   faturas em revisão, ticket médio (`total_amount` médio).

2. **Gráfico de colunas**: faturamento total por mês (`fct_invoices.ref_month` no
   eixo X, soma de `total_amount` no eixo Y).

3. **Gráfico de rosca**: faturamento por plano (`dim_plans.plan_type`) — mostra
   qual plano gera mais receita.

4. **Gráfico de barras**: faturamento por status (`pendente` / `paga` / `revisão`)
   — dá visibilidade de inadimplência/pendências.

5. **Tabela**: faturas em status `revisão`, com `user_id` (via `dim_users.name`),
   `ref_month`, `total_amount` — lista de ação para o gestor revisar manualmente
   (ligação direta com o modelo de detecção de anomalias).

6. **Segmentadores**: por `plan_type`, por `status`, por intervalo de `ref_month`.

---

## Aba 4 — Usuários / Pontos

Objetivo: visão cadastral e de capacidade — quem são os usuários, quantos veículos
têm, como estão distribuídos pelos pontos de recarga.

### Visuais

1. **Cartões de KPI**: total de usuários, total de veículos cadastrados, número de
   usuários com 2 veículos, número de pontos de recarga ativos.

2. **Gráfico de barras**: usuários por plano (`dim_plans.plan_type`) — distribuição
   de adesão aos 3 planos.

3. **Gráfico de barras**: veículos por tipo (`dim_vehicles.vehicle_type`: BEV vs.
   PHEV).

4. **Tabela**: pontos de recarga com `dim_points.location`, `power_kw`, `status`,
   e uma coluna calculada de "sessões no período" (via relacionamento com
   `fct_sessoes`) — mostra utilização por ponto, útil para decisão de expansão de
   capacidade.

5. **Segmentador**: por `point_id`, por `plan_type`.

---

## Próximos passos

1. Montar a estrutura de páginas/visuais no Power BI (ou prototipar primeiro no
   Figma, como você mencionou) seguindo esta especificação.
2. Confirmar os relacionamentos na visão de modelo do Power BI, com atenção
   especial ao caso de `fct_invoices.ref_month` (ver nota acima).
3. Voltar para a próxima etapa: fórmulas DAX (medidas calculadas) para os KPIs e
   visuais que precisarem de agregação além de soma/contagem simples (ex: ticket
   médio, taxa de anomalia, % de sessões em fim de semana).