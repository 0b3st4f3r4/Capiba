# Operação do lake — idempotência, retry e memória

> **Propósito:** detalhe operacional das regras duras de idempotência,
> retry e memória do lake, com o contexto dos incidentes reais.
> **Quando consultar:** antes de alterar download, normalize, destinos do
> lake ou concorrência de tasks pesadas.
> **Relacionados:** `AGENTS.md` (regras duras), `docs/operacao.md`
> (runbook do cluster), `docs/ingestao.md` (arquitetura da ingestão).
> **Sincronizado com:** `src/capiba/pipeline/` — 2026-08-21.

Lições de volume real (2026-08). Extraído do AGENTS.md em 2026-08-21; lá ficam
apenas as regras duras. Este documento guarda o detalhe operacional e o
contexto dos incidentes.

## Download e normalize

- Download (`task_download_source`): sobe cada arquivo ao bronze ao terminar;
  no retry, arquivos já no bronze (`lake.list_bronze_files`) são pulados e
  reentram no manifesto.
- Normalize (`task_normalize_dump`): append-por-chunk; antes, DELETA a partição
  `dt=<run_date>` de cada entidade via Trino
  (`lake.delete_silver_entities_partition`; falha aborta sem append) — retry
  reprocessa sem duplicar.
- Silver `contracts` (`lake.write_silver`): **upsert-por-id** (DELETE Trino +
  append; offline degrada para append puro). Gold `fraud_signals`
  (`lake.write_fraud_signals`): **replace-por-partição**.

## DELETE Trino + append pyiceberg

- DELETE Trino + append pyiceberg exige `table.refresh()` entre os dois — o
  DELETE comita snapshot novo e o handle antigo é rejeitado
  (CatalogCommitConflicts).
- DELETE+append não é atômico → DAGs da factory usam `max_active_runs=1`.
  **Backfills ignoram o `max_active_runs` da DAG** (Airflow 3 usa o
  `--max-active-runs` do backfill, default 10): crie sempre com
  `--max-active-runs 1 --run-on-latest-version`. As TIs pinam pool e versão da
  DAG na criação do dag run — runs de backfills criados antes de uma mudança
  de pool/estrutura **não** herdam a correção.
- O scan pyiceberg **não decodifica os delete files** do Trino ("DecodeArrow of
  DictAccumulator", pyarrow 25.0.1) — `lake.read_silver_contracts` lê via
  **Trino** no cluster (scan local só no catálogo sqlite offline);
  `read_silver_entities` segue pyiceberg streaming (deletes compactados pelo
  optimize semanal; janela entre delete e optimize quebraria a leitura — risco
  em `docs/gaps.md`).

## DAGs imperativas de manutenção

`lake_maintenance.py` (semanal: expire_snapshots + optimize) e
`gold_detection.py` (diária 08:00 UTC: `dbt_run` → `detect` sobre TODO o
silver — é a "run final" após um backfill) são DAGs imperativas.

## Memória: pool `heavy_lake` e paralelismo

Tasks pesadas (crawls `contracts_default`, normalizes, destinos lake/grafo da
factory e o `detect`) rodam no **pool `heavy_lake` (1 slot)** — picos
concorrentes OOMKillavam o pod; criado pelo init container `airflow-db-init`
(`airflow pools set heavy_lake 1`), referenciado via `HEAVY_POOL` em
`dags/pipeline_factory.py` e `dags/gold_detection.py`.
`AIRFLOW__CORE__PARALLELISM: "8"` (cada slot idle do LocalExecutor custa
~164 MiB; os 32 default eram ~5 GiB de baseline).

## Leitura seletiva de `establishments`

A leitura de `establishments` é sempre **seletiva**
(`lake.read_establishments_for_cnpjs` — IN batches via Trino; offline degrada
para scan streaming com filtro), na persistência e no `task_detect`: a tabela
RFB completa (dezenas de milhões de linhas) materializada OOMKillou o pod
(2026-08-21).

## Testes de integração do ciclo lake

`tests/test_lake_integration.py` (`@pytest.mark.integration`) trava o contrato
que quebrou 3x em produção: upsert silver sem duplicata, leitura Trino correta
após delete files, replace-por-partição gold sem tocar outras partições e retry
de normalize interrompido. Para rodar do host: port-forwards ativos +
`SSL_CERT_FILE` apontando para o `tls.crt` do secret `capiba-tls` (o endpoint
S3 vendido pelo Lakekeeper é HTTPS self-signed) + `CAPIBA_INTEGRATION=1`.

## Baseline de recursos (medido 2026-08-21, `kubectl top` × values.yaml)

Snapshot de uma tarde de sexta (não é pico de run — o pipeline
`hourly_pod_usage`/Prometheus guarda a série histórica):

| Serviço | Uso (mem) | Request | Limit | Leitura |
|---|---|---|---|---|
| trino | 2944 Mi | 2 Gi | 3 Gi | **98% do limit** — 3 restarts; próximo bump justificado |
| arangodb | 4670 Mi | 1 Gi | 6 Gi | 78% do limit; request muito abaixo do uso real |
| airflow | 2381 Mi | 2 Gi | 12 Gi | folgado (pod recém-reiniciado; pico é nas runs heavy) |
| minio | 793 Mi | 256 Mi | 1 Gi | request 3x abaixo do uso |
| marquez | 686 Mi | 384 Mi | 768 Mi | 89% do limit — observar |
| keycloak | 447 Mi | 384 Mi | 768 Mi | ok |
| postgresql | 381 Mi | 256 Mi | 2 Gi | ok |
| api / grafana / prometheus / iceberg / kepler / redis | ≤ 210 Mi | — | — | folgados |

Regra da retrospectiva 2026-08-21: próximos bumps de memória saem desta tabela
+ série do Prometheus, não de OOMKill reativo.
