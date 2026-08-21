# Fontes externas descobertas da exploração (Etapa 2)

Este documento registra o que a exploração real das APIs (scripts `01_explore_ocm.py`,
`02_explore_ibge.py`, `03_explore_renavam.py`) revelou sobre formato, comportamento e
limitações de cada fonte. O objetivo é não ter que redescobrir isso rodando tudo de novo
quando chegarmos na Etapa 4 (star schema) e na Etapa 5 (transformação e carga).

## Open Charge Map

- Endpoint: `GET https://api.openchargemap.io/v3/poi/?countrycode=BR`
- Funciona sem API key (rate limit reduzido); key gratuita recomendada para uso contínuo.
- Campos confirmados como úteis para `dim_points` / `dim_geography`:
  - `AddressInfo.Town`, `AddressInfo.StateOrProvince` (**atenção**: `StateOrProvince` veio
    `None` em pelo menos um POI real não confiar como sempre preenchido)
  - `AddressInfo.Latitude` / `Longitude`
  - `Connections[].PowerKW`, `Connections[].ConnectionType.Title`
  - `StatusType.Title` (ex: "Operational")
  - `DateLastStatusUpdate`

## IBGE Localidades

- Endpoint: `GET /api/v1/localidades/estados/{UF}/municipios` sem API key.
- Retorna hierarquia completa: município → microrregião → mesorregião → UF → região.
- Confirmado funcionando para SP (645 municípios), DF (1), RJ (92).

## IBGE Agregados (população)

- Endpoint: `GET /api/v3/agregados/6579/periodos/-1/variaveis/9324?localidades=N6[...]`
- Agregado `6579` = Estimativas da População (tabela SIDRA 6579).
- Variável `9324` = "População residente estimada (Pessoas)".
- **Pegadinha confirmada em produção**: o separador de múltiplos IDs de município dentro
  de `N6[...]` é **vírgula**, não pipe. `N6[id1|id2]` retorna erro 500 do servidor;
  `N6[id1,id2]` funciona. O pipe é reservado para combinar *níveis* geográficos diferentes
  na mesma consulta (ex: `N7|N6`), não para separar itens do mesmo nível.
- Confirmado funcionando após a correção retorna população 2025 por município
  (ex: Adamantina/SP: 35.673 habitantes).

## RENAVAM (substitui ABVE)

### Por que a troca

A fonte original do plano (ABVE `abve-data`) só disponibiliza dados de frota via
painéis de BI embutidos em iframe (Power BI/Looker), sem HTML tabular, sem API pública e
sem CSV de download. Scraping simples (`requests` + `BeautifulSoup`) não retorna nenhum
dado real a página estática não contém a informação, só o container do iframe.

RENAVAM (Ministério dos Transportes, portal CKAN) foi escolhida como substituta por
oferecer dado aberto real: frota de veículos por UF, município, marca/modelo e ano,
atualizada mensalmente, disponível como download direto.

### Formato real confirmado

- Portal: `dados.transportes.gov.br/dataset/registro-nacional-de-veiculos-automotores-renavam`
- Um arquivo por mês, chega como `.zip` contendo um único arquivo (extensão observada como
  `.TXT`, mas com conteúdo delimitado renomear para `.csv` não muda o parsing).
- **Separador: `;`**
- **Encoding: `UTF-8`** não latin-1. Tentar latin-1 primeiro não lança erro, só produz
  mojibake silencioso nos acentos (`Município` → `MunicÃ­pio`). O script de exploração
  agora detecta isso e rejeita a tentativa automaticamente.
- Colunas: `UF` (nome por extenso, ex: "ACRE", não sigla) `;` `Município` (nome, sem
  código IBGE) `;` `Marca Modelo` (campo combinado, ex: `BYD/DOLPHIN MINI GL5EV`) `;`
  `Ano Fabricação Veículo CRV` `;` `Qtd. Veículos` (vem como texto com espaço à frente,
  ex: `" 1200.0"` precisa de `.str.strip()` antes de converter para float).
- URL de download muda a cada mês sem padrão previsível precisa navegar o portal
  manualmente para pegar o link do mês mais recente na Etapa 5.

### Identificação de veículos elétricos (BEV/PHEV)

Não existe coluna de combustível/energia. O tipo de eletrificação é identificável pelo
próprio nome do modelo, por convenção (não é um padrão oficial documentado, foi inferido
comparando o dataset com modelos elétricos reais vendidos no Brasil em 2026):

- **BEV (100% elétrico)**: sufixo `EV` ou `EUV` no nome
  (ex: `GL5EV`, `310EV`, `GS EV`, `SPARK EUV`)
- **PHEV (híbrido plug-in)**: sufixo `DM` "Dual Mode", nomenclatura da BYD
  (ex: `SONG PRO GL DM`, `KING GS DM`)
- Fallback por nome de marca/modelo para montadoras sem essa convenção de sufixo
  (ex: BMW iX, BMW i3, Nissan Leaf).

**Cuidado**: a primeira versão da heurística usava `"IX"` como keyword solta, o que
gerava falso positivo em `CHEV/ONIX` (carro a combustão comum) por substring match.
Corrigido usando `\b` (word boundary) no regex. Qualquer keyword nova adicionada a essa
lista deve ser testada contra o dataset real antes de confiar no resultado substrings
curtas são perigosas.

### Decisão de escopo: BEV + PHEV juntos

O EV ChargeOps considera **BEV e PHEV como frota-alvo**, não só BEV. Critério: o que
importa para a plataforma é se o veículo pode gerar uma sessão de recarga no carregador
GoodWe (ou seja, se carrega via plugue), não se é 100% elétrico. Um PHEV plugado gera
consumo, tempo de sessão e cobrança do mesmo jeito que um BEV.

O tipo (BEV/PHEV) deve ser preservado como **atributo**, não usado como filtro de
exclusão permite análises futuras (ex: "sessões de PHEV são mais curtas que as de BEV")
sem perder o dado.

## Limitações conhecidas (a resolver na Etapa 4/5)

- A amostra usada para validar a lógica de exploração foi de 20.000 linhas do RENAVAM
  (limite do script de exploração) não representa o volume real da frota nacional.
  A Etapa 5 (carga) deve processar o arquivo completo.
- `UF` no RENAVAM vem por extenso ("ACRE"); IBGE usa sigla ("AC"). Precisa de um de-para
  na transformação.
- `Município` no RENAVAM vem como texto livre, sem código IBGE vai exigir reconciliação
  por nome (com risco de divergência de acentuação/grafia) com a tabela de localidades do
  IBGE para virar `geo_id` em `dim_geography`.
- A heurística de BEV/PHEV por nome de modelo não é uma fonte oficial de verdade pode
  ter falso negativo para marcas/modelos elétricos não cobertos pela lista de fallback.
  Revisar a lista de valores únicos do dataset completo antes de fechar a transformação.