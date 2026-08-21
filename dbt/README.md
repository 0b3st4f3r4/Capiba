# dbt — marts gold e camada serving

> **Propósito**: documentar o projeto dbt do lakehouse Capiba — profile
> dbt-trino, layout gold/serving, materializações, seeds, testes e comandos.
> **Quando consultar**: antes de criar ou alterar um mart, seed ou teste
> dbt; ao regenerar `requests.csv`; ao depurar um `make dbt-run`.
> **Relacionados**: `AGENTS.md` (visão geral do projeto),
> `docs/operadores.md` (sinais de detecção), `docs/operacao_lake.md`
> (operação do lake), `pipeline/public_export.py` (export público dos marts).
> **Sincronizado com**: `dbt/dbt_project.yml`, `dbt/profiles.yml`,
> `dbt/models/**`, `dbt/seeds/_seeds.yml`, `dbt/tests/` — 2026-08-21.

## Profile e conexão

O projeto (`dbt_project.yml`, nome `capiba_lakehouse`) usa o **profile
`capiba`** (`profiles.yml`), **dbt-trino** sobre o Trino gateway. O SQL roda
no Trino, que lê/escreve as tabelas Iceberg dos warehouses medallion via
catálogo REST Lakekeeper. O catálogo alvo default é o **gold**
(`DBT_TRINO_CATALOG`, default `gold`), schema `capiba`; a fonte silver
(`models/staging/sources.yml`, source `lake_silver`) vive no catálogo
`silver`. Auth é `method: none` (header `X-Trino-User` apenas): o Trino
recusa senha em HTTP inseguro e o dbt sempre fala com o serviço HTTP plain
(`capiba-trino:8080` in-cluster ou o port-forward `localhost:8081`). Host,
porta e usuário vêm de `TRINO_HOST`/`TRINO_PORT`/`TRINO_USER` (ver
`.env.example`).

## Gold vs. serving

- **Marts gold** (`models/marts/`) — tabelas **Iceberg** no bucket
  `capiba-gold`, escritas no catálogo gold do Lakekeeper, todas
  `materialized='table'`.
- **Serving** (`models/serving/`) — cópias dos marts **materializadas no
  PostgreSQL DWH** via catálogo Trino `dwh` (`config(database='dwh')`,
  CTAS cross-catálogo suportado pelo dbt-trino). É a camada de baixa
  latência para consumo direto. Se uma versão futura do Trino restringir
  CTAS cross-catálogo, o fallback documentado em `_serving.yml` é manter os
  modelos no catálogo gold e sincronizar ao PostgreSQL via Trino CLI.

### Modelos gold (`models/marts/`)

- `contracts_daily` — volumes diários de contratos (agregado por `dt`).
- `contracts_by_agency` — contratos agregados por órgão comprador.
- `supplier_stats` — histórico de contratos por fornecedor.
- `contract_amendments` — flags de aditivos por contrato (PR-D-05: aditivo
  de valor e extensão de vigência, da sequência de observações bronze).
- `amendments_by_supplier`, `amendments_by_agency` — flags de aditivo
  agregadas por fornecedor e por órgão.
- `contract_red_flags` — red flags determinísticas de contrato (CRI,
  PR-D-04).
- `red_flags_by_supplier`, `red_flags_by_agency` — red flags agregadas.
- `political_connections` — conexões políticas TSE×fornecedores (PR-D-08;
  mascara CPF na origem).
- `pod_usage_hourly` — uso horário de CPU/memória por pod.
- `platform_cost_daily` — custo diário da plataforma (join de
  `pod_usage_hourly` com a seed `requests`).
- `data_quality_daily` — telemetria diária de qualidade de dados.

### Modelos serving (`models/serving/`)

- `serving_supplier_stats` — histórico por fornecedor, servido do DWH.
- `serving_municipality_daily` — volumes diários por município/UF comprador
  (agregado direto da silver `contracts`, pois `contracts_daily` não carrega
  dimensão de município).

## Seeds (`dbt/seeds/`)

- `requests.csv` — requests de CPU/memória por componente da plataforma,
  **transcritos de `charts/capiba/values.yaml`**. O mart
  `platform_cost_daily` faz join com ela via `pod_pattern` (LIKE).
  **Regerar sempre que o values.yaml mudar** e rodar `dbt seed` de novo. O
  `dbt_project.yml` tipa `request_memory_bytes` como `bigint` (2Gi estoura
  INT32 no Trino).
- `ue_siafi_crosswalk.csv` — de/para UE eleitoral TSE × código SIAFI × IBGE
  do município, para o mart `political_connections`. Incremental: hoje só
  Recife (UE 25313, SIAFI 2531, IBGE 2611606). Estender conforme novos
  municípios entram na cobertura silver e rodar `dbt seed`.

## Marts horários e exclusões da `gold_detection`

- `pod_usage_hourly` e `platform_cost_daily` são **horários**: refrescados
  pelo próprio pipeline `hourly_pod_usage` (post step `dbt_run --select
  pod_usage_hourly platform_cost_daily`). A `gold_detection` **sempre os
  exclui** (`_HOURLY_OWNED_MARTS`) — commits dbt concorrentes na mesma
  tabela Iceberg são rejeitados pelo Lakekeeper.
- A `gold_detection` também **exclui automaticamente os marts dependentes
  de TSE** (`political_connections`) enquanto as silvers
  `campaign_donations`/`candidacies` não existirem
  (`lake.silver_table_exists`).

## Testes (`dbt/tests/`)

Testes singulares (SQL) além dos testes genéricos declarados em
`_marts.yml`/`_serving.yml`/`_seeds.yml`:

- `contract_amendments_cardinality.sql`, `contract_amendments_domain.sql`,
  `contract_amendments_field_viability.sql` — invariantes de dados reais
  P6–P8 do PR-D-05.
- `contract_red_flags_bronze_join.sql`, `contract_red_flags_cardinality.sql`,
  `contract_red_flags_domain.sql` — invariantes do mart de red flags.
- `pod_usage_hourly_unique_hour_pod.sql`,
  `platform_cost_daily_unique_dt_pod.sql` — unicidade das chaves temporais.
- `political_connections_gates.sql`,
  `political_connections_no_full_cpf.sql` — gates do PR-D-08 e garantia LGPD
  (sem CPF completo).

## Comandos

```bash
make dbt-run    # constrói os marts gold
make dbt-test   # testes dbt de fontes e modelos
make dbt-docs   # gera e serve o catálogo dbt
```

Todos requerem **port-forwards ativos** (`make port-forward`; status em
`make port-forward-status`) — o dbt fala com `localhost:8081`. No cluster,
`dbt run`/`dbt test` também rodam como post steps das DAGs (`dbt_run`,
pulados em runs de backfill); mudanças em `dbt/` são sincronizadas ao
Airflow pelo sidecar após `make publish-artifacts`.
