# AGENTS.md — Capiba

## Projeto

Capiba — **C**ruzamento e **A**nálise de **P**adrões e **I**ndícios em **B**ases
**A**bertas. Motor de detecção de fraude via dados abertos a serviço do
jornalismo de dados comunitário. Missão e narrativa de longo prazo: README.md,
"Objetivo"; mapa da documentação de processo: README.md, "Documentação".

Stack: Python 3.13, FastAPI, Airflow, ArangoDB (multi-modelo), MinIO,
scikit-learn + spaCy, Grafana, Keycloak (SSO/OIDC de todas as UIs, realm
`capiba`), Marquez (OpenLineage), Trino (SQL sobre o lake; fonte do Grafana),
Iceberg (Parquet no MinIO, catálogo REST Lakekeeper), dbt (dbt-trino) para
marts gold e serving (`dbt/models/serving/`, materializados no PostgreSQL DWH
via catálogo Trino `dwh`). Redis (cache do quality monitor e hot paths da API)
por padrão; tudo degrada sem ele (`redis.enabled: false`). Observabilidade:
Prometheus (`prometheus.enabled`, emptyDir, retenção 7d) e Kepler
(`kepler.enabled`, energia na 28282); dashboards como código em
`charts/capiba/dashboards/*.json`. O runner publica métricas por step na gold
`platform_metrics` (`lake.write_platform_metrics`, best-effort); o pipeline
`hourly_pod_usage` (fonte `pod_usage` — metrics-server in-cluster, `kubectl
top` fora; fórmula `metrics_collect`) alimenta
`pod_usage_hourly`/`platform_cost_daily` (requests na seed
`dbt/seeds/requests.csv` — regerar quando o values.yaml mudar).

Comandos:

```bash
make install            # venv + deps de dev + Airflow
make test               # pytest rápido (lentos pulados — CAPIBA_SLOW=1)
make test-slow          # só as baterias lentas (marker slow)
make test-cov           # suíte completa com cobertura (piso 85%; inclui baterias)
make lint               # ruff check
make typecheck          # mypy + basedpyright
make security           # bandit (src/)
make format             # ruff format
make publish-artifacts  # publica src/ + dags/ + dbt/ no MinIO (capiba-artifacts)
make init-buckets       # cria buckets MinIO + warehouses Iceberg (idempotente)
make dbt-run            # constrói os marts gold (com port-forwards ativos)
make dbt-test           # testes dbt de fontes/modelos
make dbt-docs           # gera e serve o catálogo dbt
make port-forward       # port-forwards do cluster (stop/status:
                        # make port-forward-stop / port-forward-status)
make ingest-mock        # pipeline offline com fontes mock, persistindo no lake
make build-airflow      # rebuild da imagem do Airflow (se as deps mudarem)
make rollout-airflow    # reinicia o Airflow após publicar mudanças em src/
make cluster-start      # cria/inicia cluster k3s, Traefik, chart Capiba, Headlamp
                        # (compila e importa capiba/api e capiba/airflow)
make cluster-stop       # para o cluster (não remove dados)
make cluster-remove     # para e remove o cluster (destrutivo)
make cluster-status     # pods do namespace capiba
make bump-version VERSION=0.2.0  # versão em pyproject, chart, Makefile e API
make dashboard-token    # token do Headlamp (http://localhost:4466)
```

O Airflow sincroniza código, DAGs e dbt do MinIO (init container + sidecar,
intervalo `airflow.artifacts.syncIntervalSeconds`), sem rebuild: mudanças em
DAGs/`dbt/` são capturadas pelo sidecar; em `src/` pedem `kubectl rollout
restart deploy/capiba-airflow -n capiba` após publicar.

## Layout e pipelines

Código em `src/capiba/` por vertical (ingestion, detection, quality, evidence,
db, api, notification, pipeline, config, transformations). Pipelines de
ingestão são **declarativos**: specs YAML em `dags/pipelines/*.yaml` (fontes,
janela, fórmula, validações, destinos, post steps) resolvidos por
`pipeline/registry.py` e executados pelo runner (`pipeline/runner.py`);
`dags/pipeline_factory.py` gera uma DAG por spec. Specs ativas: `daily_pncp` e
`monthly_transparency` (contratos, separados por fonte — falha isolada e rate
limits independentes, sem post steps), `daily_pncp_updates`
(`/v1/contratos/atualizacao`, bronze-only — flags de aditivo do PR-D-05 leem o
bronze; o silver não é tocado), `monthly_federal_revenue`, `weekly_sanctions`,
`hourly_pod_usage`, `daily_querido_diario` (diários oficiais de Recife, IBGE
2611606, Querido Diário/OKBR), `monthly_tse` (snapshot fixo da prestação de
contas eleitorais, ano via `params.year`/`TSE_ELECTION_YEAR`;
`reference_month` não se aplica) e `pilot_pncp_terms` (sonda-piloto do
PR-D-05b, sem schedule — disparo manual; coorte dirigida por parâmetros:
`include_flagged` lê os `f_value_amendment = 1` do mart
`contract_amendments` + `siafi_codes`, Recife 2531).

Fórmulas do runner:

- `contracts_default` — crawl+normalize de contratos (PNCP, transparência).
- `file_dump` — dumps multi-arquivo (`federal_revenue` → `ingestion/cnpj.py`).
  Com `lake_silver`/`arangodb_graph` declarados e parser no
  `DUMP_PARSER_REGISTRY`, ganha `normalize_<fonte>` streaming: ZIPs
  Empresas/Estabelecimentos/Socios → silvers
  `companies`/`establishments`/`partners` (opt-in via
  `FEDERAL_REVENUE_FILES`); `Municipios.zip` → silver `rfb_municipalities`
  (TOM → nome, elo da geo do fornecedor). O destino `arangodb_graph` carrega o
  grafo FtM: vértices `companies`/`persons`, arestas `ownership`
  ({persons,companies}→companies — sócio PJ vira Company e alimenta
  `trace_ownership`) e `directorship`, classificadas por
  `cnpj.edge_kind_for_qualificacao`, via `bulk_upsert_cnpj` a partir do silver.
  Ao final, resolução de entidades (`detection/entities.py` — D-07/D-07b: nome
  0,6 + documento mascarado 0,3 + faixa etária 0,1, limiar
  `DETECTION_ENTITY_THRESHOLD` default 0,85) grava arestas `same_as`
  (persons↔persons) best-effort, sem colapsar vértices.
- `entities_collect` — snapshots de sanções (`ceis`/`cnep`/`ceaf`; crawler
  `fetch_sanctions`, modelo `Sanction` em `ingestion/sanctions.py`; CEAF traz
  `masked_document`): `normalize_<fonte>` escreve na silver do
  `ENTITY_NORMALIZER_REGISTRY` (hoje `sanctions`); wrappers em
  `pipeline/entity_tasks.py`. O crawl (`task_crawl_entities`) persiste
  **checkpoint por página** no bronze
  (`<fonte>/pages/dt=<data>/page-NNNNN.json.gz`) — retry retoma da próxima
  página; 400s esporádicos são transitórios.
- `documents_collect` — documentos datados com janela (`querido_diario`,
  crawler `fetch_gazettes` em `ingestion/crawler_querido_diario.py`):
  `crawl_<fonte>` (metadados ao bronze) + `download_<fonte>_texts`
  (`persist_document_texts`; wrappers em `pipeline/document_tasks.py`) baixa o
  `txt_url` para `<fonte>/files/dt=<run>/` com nome determinístico
  (`text_file_name`) — retry pula textos já persistidos, falhas de download são
  best-effort. Sem normalização silver.
- `metrics_collect` — telemetria (pod_usage).
- `terms_collect` — termos contratuais PNCP (plano B do PR-D-05b;
  `fetch_contract_terms` em `ingestion/crawler_pncp.py`, endpoint do grupo
  transacional `pncp` — `PNCP_TERMS_API_URL`): `crawl_<fonte>` enumera a
  coorte (`lake.read_terms_pilot_cohort`) e `persist_<fonte>_terms`
  (`persist_contract_terms`; wrapper em `pipeline/term_tasks.py`) grava
  **checkpoint por contrato** no bronze
  (`<fonte>/files/dt=<run>/<cnpj>/<ano>/<seq>.json.gz`) — retry pula os já
  persistidos, falhas por contrato são best-effort. Bronze-only, sem
  normalize silver; as flags de termo (`compute_term_flags` em
  `detection/amendments.py`) só entram no mart após o veredito de Q4.

Ingestão TSE (`ingestion/tse.py`): sem download — o CDN bloqueia clientes
CLI via Akamai Bot Manager (403 mesmo com IP residencial BR, 2026-08-21). Os
dumps `prestacao_de_contas_eleitorais_candidatos_<ano>.zip` e
`consulta_cand_<ano>.zip` vivem como **âncora congelada** no bronze
`capiba-bronze/tse/reference/` (upload manual via browser; sha256 registrado
no R-D-08b); `download_tse_dump` resolve a âncora pelo ano da run
(`params.year`/`TSE_ELECTION_YEAR`) e falha com mensagem clara se ela não
existir. Multi-ano (2024 municipal, 2022 geral): o normalize deleta por
(dt, `election_year`) — `delete_silver_entities_partition(...,
election_year=)`; demais fontes deletam a partição inteira. Normalize
streaming grava as silvers `campaign_donations`
(`receitas_candidatos_<ano>_BRASIL.csv`) e `candidacies`
(`consulta_cand_<ano>_BRASIL.csv`, coluna `DS_SITUACAO_TOTALIZACAO_TURNO`).
Documentos completos no silver; mascaramento é preocupação do mart gold
(PR-D-08 §2).

### Idempotência, retry e memória (lições de volume real, 2026-08)

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
- `lake_maintenance.py` (semanal: expire_snapshots + optimize) e
  `gold_detection.py` (diária 08:00 UTC: `dbt_run` → `detect` sobre TODO o
  silver — é a "run final" após um backfill) são DAGs imperativas.
- Tasks pesadas (crawls `contracts_default`, normalizes, destinos lake/grafo da
  factory e o `detect`) rodam no **pool `heavy_lake` (1 slot)** — picos
  concorrentes OOMKillavam o pod; criado pelo init container `airflow-db-init`
  (`airflow pools set heavy_lake 1`), referenciado via `HEAVY_POOL` em
  `dags/pipeline_factory.py` e `dags/gold_detection.py`.
  `AIRFLOW__CORE__PARALLELISM: "8"` (cada slot idle do LocalExecutor custa
  ~164 MiB; os 32 default eram ~5 GiB de baseline).
- Transformações nomeadas em `src/capiba/transformations/` (um módulo por
  transformação, `transform(records, **params)`).

## Geografia

Referência em `ingestion/geography.py`: CSV vendored
`ingestion/reference/municipios.csv` (kelvins/Municipios-Brasileiros, MIT —
atribuição em `reference/README.md`), lookups puros por (nome, UF) normalizado
e por IBGE (nomes únicos por UF — de-para do comprador PNCP determinístico).
Silver `municipalities` (ibge_code, name, uf, siafi_code, latitude, longitude)
carregada por `lake.load_municipalities` (idempotente por conteúdo). Na
persistência (`upsert_contract`/`bulk_upsert_contracts`), `buyers` ganha
ibge/lat/long via (city, uf) e `suppliers` ganha lat/long via cadeia silver
`establishments` (TOM) → `rfb_municipalities` → referência vendored —
best-effort, funções puras injetáveis (`geography.buyer_geo_fields`,
`geography.build_supplier_geo_index`).

A leitura de `establishments` é sempre **seletiva**
(`lake.read_establishments_for_cnpjs` — IN batches via Trino; offline degrada
para scan streaming com filtro), na persistência e no `task_detect`: a tabela
RFB completa (dezenas de milhões de linhas) materializada OOMKillou o pod
(2026-08-21).

Sinal `anomalous_geography` (contrato PR-D-09 §3, validado no sintético por
D-09 e no volume real por P6 — `docs/results/R-D-09.md`) em
`detection/geography.py` (puro, sobre o silver): haversine R = 6371,0 km entre
sedes municipais, gate estrito `DETECTION_GEOGRAPHY_MAX_DISTANCE_KM` (default
100 km), score `round(min(1.0, distância /
DETECTION_GEOGRAPHY_SCORE_REFERENCE), 4)` (default 1000 km) — placeholders
pré-registrados (mudança exige PR-D-09b); um sinal por (fornecedor PJ,
município comprador); PF e pares sem elo de de-para nunca sinalizam. Emissão
best-effort no `task_detect` (carrega `municipalities` idempotente antes). O
operador AQL legado `graphs.anomalous_geography` foi removido (morto —
vértices `bid` inexistentes; decisão em Revisões do PR-D-09).

## Detecção, evidência e triagem

Sinais best-effort emitidos no `task_detect` (nunca derrubam a task):

- `sanctioned_supplier` (match exato por documento) e `sanctioned_name_match`
  (screening fuzzy, `detection/screening_fuzzy.py` — validado por D-06b,
  `docs/results/R-D-06b.md`: veto por documento divergente, score 0,6 nome +
  0,4 documento, limiares 0,85/0,95), sobre a silver `sanctions`. Pares
  candidatos passam por **prefilter vetorizado exato** (cota superior da ratio
  do SequenceMatcher via interseção de multiset de caracteres; equivalência
  bit-a-bit guardada por `TestIndexedImplementationEquivalence`) — o produto
  cruzado documentless levaria horas em volume real (570M pares). Piloto de
  PEPs via yente/OpenSanctions (`br_pep`) **executado e refutado** por D-12
  (`docs/results/R-D-12.md`: logic-v2 não supera o matcher local no regime
  documentless — precisão 0,8272, revocação 0,517 no OS Pairs); adapter
  testado em `detection/pep_screening.py`, piloto arquivado sem sinal novo.
- `political_connection` (`detection/political.py`, contrato PR-D-08 §3 —
  validado no sintético por D-08, `docs/results/R-D-08.md`, P8/volume real
  pendentes): doador de campanha de prefeito eleito (match exato por documento,
  originário prioritário — nome nunca é evidência) que vira fornecedor do
  município na janela do mandato (derivada de `TSE_ELECTION_YEAR`); piso
  `DETECTION_POLITICAL_MIN_DONATION` (default R$ 1.000), concentração
  `DETECTION_POLITICAL_MIN_SHARE` (default 0,05), score `min(1.0, share /
  DETECTION_POLITICAL_SCORE_REFERENCE)` (default 0,25) — placeholders
  pré-registrados (mudança exige refinamento pré-registrado). A CDN do TSE
  bloqueia por IP (403, não contornável por User-Agent — confirmado em
  2026-08-21); o PR-D-08b pré-registra a troca da fonte para a Base dos
  Dados (`br_tse_eleicoes`, bateria de paridade `tse_parity` — gate do
  eleito migra para `resultados_candidato`; a BD zera o doador
  originário, perda de revocação medida por P5). O mart gold
  `political_connections` publica os sinais enriquecidos com as silvers TSE
  (última partição) e a seed `dbt/seeds/ue_siafi_crosswalk.csv` (incremental,
  piloto Recife: UE 25313, SIAFI 2531); LGPD: CPF mascarado padrão CEAF, CNPJ
  completo, chave `signal_id` sha256.
- `collusion_network` (`detect_collusion` sobre o ArangoDB), limiar
  `DETECTION_COLLUSION_MIN_WINS` (default 3, placeholder validado por D-02;
  calibração em volume real D-03/D-03b **inconclusiva**), score binário.
  Guarda: a derivação de pares é **pulada** quando a projeção (Σ C(n,2) por
  comprador, `graphs.projected_pair_count`) excede
  `DETECTION_COLLUSION_MAX_PAIRS` (default 1.000.000) — 9,6M pares OOMKillaram
  o pod na primeira run real; o snapshot de elegibilidade segue gravado como
  evidência. D-03c (`docs/results/R-D-03c.md`): blocking de recall exato
  (`blocked_supplier_index`, predicado `|B(s)| ≥ min_buyers`) provado
  equivalente bit a bit, mas **refutado** nos pontos permissivos ((3,2)/(3,3)
  seguem acima da guarda); PR-D-03d (`docs/results/R-D-03d.md`, success
  T1–T9) introduziu a emissão ranqueada com orçamento editorial —
  **promovida à produção em 2026-08-21** (decisão humana registrada no PR):
  o `task_detect` deriva bloqueado (`pair_buyers_from_eligibility_blocked`)
  e emite top-`DETECTION_COLLUSION_TOP_K` (default 500; ordenação
  buyer_count/wins_sum desc, par asc) com descriptor `top_k`/`qualified_count`
  no pacote de evidência; backlog legado não reprocessado. Defaults de
  min_wins/min_buyers inalterados — a bateria validou (5,2); promover o
  ponto validado é candidato a PR-D-03e.
- `notice_clone` (`detection/notice_clone.py`, PR-D-10): editais
  clonados/direcionados via NLP sobre os diários do Querido Diário —
  segmentação de edições (`ingestion/gazette_segments.py`), cosseno estrito >
  `DETECTION_NOTICE_CLONE_THRESHOLD` (default 0,85), veto de reedição por
  número de processo, encoder pinado. Exploratório D-10 executado: âncoras
  N0/N6 verdes, bandas P3/P4 fixadas, **P2 refutada** (troca de entidade/órgão
  abaixo de 0,95); **D-10b success 7/7** (P2b: rank ≤ 4,
  `docs/results/R-D-10b.md`) — produtor best-effort no `task_detect`
  (`notice_clone_bronze_signals`, textos bronze do Querido Diário) ativo.

Grafo na API: `GET /v1/graph/ownership/{cnpj}` (aresta FtM `ownership`; CNPJ
normalizado para `cnpj_basico`), `GET /v1/graph/partners/{siafi_code}`
(`partners_of_buyer`), `GET /v1/graph/ftm/{cnpj}` (`db/ftm.py`).

Evidência: router `/v1/evidence` (upload multipart, listagem por contrato,
download por SHA-256; storage MinIO sob demanda via `get_storage()`). O
`task_detect` grava, best-effort, pacotes reproduzíveis por sinal
(`evidence/packages.py`): pacote de lote por run (linhas silver +
`source_rows_sha256` + janela + versão) e manifesto por sinal com a chave de
triagem e `batch_sha256`, servidos por `GET /v1/signals/{key}/evidence`;
`reproduce_signal` reexecuta `detect_fraud_signals` sobre o pacote para
conferir o score.

Triagem editorial: `db/triage.py` (coleção `signal_reviews`, chave
`{entity_type}:{entity_id}:{signal_type}`; `pending_review` →
`confirmed`/`rejected`/`published`, revisor obrigatório, `published` terminal).
O `task_detect` registra sinais novos como `pending_review`; a API expõe
`/v1/triage` (listagem, transição, relatório de precisão por operador —
rótulos para o ML). Interface humana: página `/triage` do portal (CSS em
`api/static/portal.css`). **A fábrica de rótulos está parada** (medido em
2026-08-21, gate do PR-D-11 refutado): 816.405 sinais na fila, 100%
`pending_review`, zero revisões concluídas — 99,1% são `collusion_network`
com score binário 1,0 indistinguível. Destrave da listagem implementado:
`list_reviews` ordena e pagina **server-side** (AQL `SORT score DESC,
last_seen DESC` + `OFFSET/LIMIT`, filtros por status/signal_type/min_score,
`count_reviews` para o total; índice recomendado no docstring, criação
manual) e a página `/triage` ganhou filtros e paginação real; alertas
internos ganharam cap top-K (`NOTIFICATION_ALERT_MAX_SIGNALS`, default 50).
Causas e restante do destrave: `docs/gaps.md` item Editorial 13.

Assinatura de alertas por município: dispara SOMENTE na transição para
`published` (gancho best-effort em `api/routers/triage.py` →
`notification/subscriptions.py`); o município do sinal é resolvido para IBGE
via `details` (city/uf) ou pelo par comprador city/UF mais frequente dos
contratos da entidade (→ `ingestion/geography.py`); sinais sem município
resolvível não disparam (contados em log). Coleção `subscriptions`
(`db/subscriptions.py`): email, ibge_code, status
(pending/confirmed/unsubscribed) e apenas o HASH sha256 do token opaco
permanente (o mesmo link confirma e cancela). Rotas públicas: `POST
/v1/subscriptions` (resposta genérica, sem enumerar), `GET
/v1/subscriptions/confirm|unsubscribe?token=...`. E-mails (um por assinante)
usam o template `subscription` do NotificationDispatcher e linkam o pacote de
evidências; URL base `PUBLIC_API_URL` (default
`https://api.{PORTAL_DOMAIN}:8443`).

Alertas internos por e-mail (`notification/alerts.py`, wrapper síncrono do
dispatcher async) disparam do `task_detect` (sinais ≥
`NOTIFICATION_ALERT_SCORE`, default 0.7) e do `task_validate_pipeline`
(relatório inválido ou erro de normalização > 5%); no-op sem
`NOTIFICATION_RECIPIENTS`. O `task_validate_pipeline` também alimenta o
`QualityMonitor` (`record_batch`, best-effort); o `NotificationScheduler` só
inicia no lifespan da API com `NOTIFICATION_RECIPIENTS` configurado.

## Saída pública e pós-steps

A `gold_detection` dispara, após o `dbt_run`, o `detect` e o post step
`export_public_marts` em paralelo; o export (`pipeline/public_export.py`) leva
os marts liberados ao bucket `capiba-public` (`PUBLIC_EXPORT_BUCKET`) em CSV +
Parquet + manifest, versionado `marts/<mart>/dt=<data-da-run>/` (leitura Trino,
escrita via client MinIO do lake). Allowlist LGPD declarativa e **fail-closed**
— `PUBLIC_MARTS` (justificativa por mart) e `EXCLUDED_MARTS` (`pod_usage_*`,
`platform_cost_*`, `data_quality_*` = telemetria interna;
`contract_red_flags` = `supplier_id` pode ser CPF completo);
`political_connections` entra porque mascara CPF na origem. Mart novo sem
classificação falha o guarda (`tests/test_public_export.py`); mart liberado mas
**sem tabela gold** (silvers fonte ausentes — ex.: TSE antes do primeiro load)
é pulado com warning (`skipped` no sumário). API pública
(`api/routers/public.py`, sem auth): `GET /v1/public/marts`, `GET
/v1/public/marts/{name}/{csv|parquet}` (302 presignado, `?dt=` pina), `GET
/v1/public/methodology` (gerado do `_marts.yml` + specs YAML; degrada sem dags/
e dbt/ na imagem). Bucket criado pelo `make init-buckets`; política de leitura
pública é decisão de deploy.

Post steps (`dbt_run`, `detect`) são **pulados em runs de backfill**
(`task_post_step` levanta `AirflowSkipException` quando `run_type ==
"backfill"` — reprocessam as tabelas inteiras); após um backfill, dispare uma
run regular. O post step `dbt_run` aceita mapping com `select` (vazio =
projeto todo): pipelines frequentes declaram só os marts alimentados — o
`hourly_pod_usage` roda `--select pod_usage_hourly platform_cost_daily` (run
completo OOMKillava o Trino); o run completo fica com a `gold_detection` — que
**exclui automaticamente os marts dependentes de TSE**
(`political_connections`) enquanto as silvers
`campaign_donations`/`candidacies` não existirem (`lake.silver_table_exists`)
**e sempre exclui os marts horários** (`_HOURLY_OWNED_MARTS`, refrescados pelo
próprio pipeline horário — commits dbt concorrentes na mesma tabela Iceberg
são rejeitados pelo Lakekeeper).

## Infra e convenções

Projeto dbt em `dbt/` (profile `capiba`, dbt-trino sobre o catálogo gold; marts
Iceberg no bucket capiba-gold). Lake: Iceberg via `pipeline/lake.py` +
pyiceberg, catalogado pelo Lakekeeper; offline, `ICEBERG_CATALOG_URI` para
`sqlite:///...` com `ICEBERG_LOCAL_WAREHOUSE`. Testes com infra externa:
`@pytest.mark.integration` (`CAPIBA_INTEGRATION=1`); baterias de
regime/calibração: `@pytest.mark.slow` (`CAPIBA_SLOW=1` — CI e `make
test-cov`/`make test-slow` habilitam). BDD (pytest-bdd) em `tests/bdd/`,
features em `tests/bdd/features/*.feature`.

Teto da suíte rápida (`make test`): **2 minutos** (referência 2026-08-21: ~50
s, 1325 testes). Se estourar, há teste vazando para infra real — investigue.
Causa observada: testes de wiring do `task_detect` sem mock de
`get_capiba_db`/`collusion_eligibility` executam o AQL de elegibilidade no
ArangoDB real quando os port-forwards estão ativos, e a derivação de pares
(`graphs.pair_buyers_from_eligibility`) explode combinatorialmente em volume
real (>10 min a 60% CPU). Todo teste unitário deve mockar clientes de infra
(`get_capiba_db`, `lake`, storage MinIO) — padrão de referência:
`tests/test_pipeline.py` (`@patch` em `capiba.pipeline.tasks.get_capiba_db` e
`collusion_eligibility`).

Experimentos de detecção seguem doutrina de pré-registro: predição numérica
falsificável com critérios de sucesso **e de refutação** em
`docs/preregistrations/PR-D-*.md` antes de qualquer execução, config
declarativa em `experiments/detect/*.json` (seeds inclusas), resultados —
inclusive negativos — em `docs/results/R-D-*.md`. Detalhes em
`docs/preregistrations/README.md`.

Governança, backlog e demais documentos de processo: seção "Documentação" do
README.md. Convenção: os códigos `O*` do backlog (O1..O12) são exclusivos dos
trackers de processo (`docs/oportunidades.md`, `docs/gaps.md`) — não citar em
código nem em documentação permanente.

Acesso às UIs sem port-forward: ingress Traefik (DaemonSet, hostPorts
8088/8443 — a porta 80 é do Apache do host) em
`https://<serviço>.capiba.local:8443` (api, grafana, marquez, iceberg, minio,
s3, trino, airflow); `scripts/setup.sh` mapeia os hosts no `/etc/hosts`.
Certificado self-signed (wildcard `*.capiba.local`, `scripts/gen-certs.sh`,
secret `capiba-tls`) — o browser pede exceção. HTTP na 8088 segue respondendo
(sem redirect). CI em `.github/workflows/ci.yml` (isort, ruff, mypy, pytest com
piso de 85% — também aplicado pelo hook `pytest-cov` do pre-commit — e bandit).

SSO: Keycloak é o IdP OIDC de todas as UIs — portal capiba-dashboard na API
(`/`, `api/portal.py`), Grafana, Airflow (FAB OAuth), MinIO Console, Lakekeeper
UI e Headlamp. Usuário dev: `capiba`/`capiba-sso` (`keycloak.devUser`),
ressincronizado a cada `make helm-upgrade` pelo hook
`templates/keycloak/job-sync-user.yaml`, que também lhe concede
`realm-management/realm-admin` — o console do realm
(`https://keycloak.capiba.local:8443/admin/capiba/console`) abre com ele; o
console master (`/admin/master/console`) continua restrito ao admin bootstrap
(`keycloak.admin`). Issuer HTTPS em `https://keycloak.capiba.local:8443` (pods
confiam no cert via CA `capiba-tls` como `SSL_CERT_FILE`); rewrite de CoreDNS
(`scripts/cluster.sh`, passo 4) resolve esse host para o ClusterIP do Traefik
(pinado — clusterIP é imutável); backchannels de máquina usam
`capiba-keycloak:8080`. Clientes de máquina do lake (Trino, pyiceberg,
`init_buckets.py`) usam o client `capiba-services` (client_credentials).
Fallbacks locais: MinIO root, Grafana admin (`grafana.auth`) e token do
Headlamp (`make dashboard-token`).

## Processo de desenvolvimento

Ciclo BDD + TDD em cinco fases. Antes de começar, esclarecer com o usuário:

1. Objetivo concreto e critério de aceitação?
2. Existe teste (unitário, BDD ou dbt) que cubra o comportamento esperado?
3. A mudança envolve integração com infra externa (ArangoDB, MinIO, Trino)?
4. Corrigir o atual vs. refatorar? Limite de escopo?

**Proteção de trabalho em andamento**: antes de tocar em arquivos do
repositório, verifique modificações preexistentes (`git status`, `git diff`).
Se houver alterações não solicitadas na tarefa atual, **pare e questione o
usuário antes de prosseguir** — não formate, corrija, mova, remova ou committe
trabalho em andamento sem autorização explícita.

1. **Investigação** — leia código e testes relacionados antes de propor
   solução; `make test` verde antes de alterar; reproduza falhas e ache a causa
   raiz; para features novas, defina o contrato (API, modelo, DAG, teste BDD)
   antes de implementar.
2. **Testes e desenvolvimento** — BDD: features Gherkin em
   `tests/bdd/features/*.feature`, passos em `tests/bdd/test_*.py` (atualize a
   feature antes do código quando possível). TDD: teste unitário em `tests/`
   antes ou junto com a implementação em `src/capiba/`. **Economia de
   validação**: durante o desenvolvimento rode só os testes do escopo
   (`pytest tests/test_x.py -q`); `make test` ao fechar a rodada. **Não** rode
   `make test-cov` antes de commitar — o hook `pytest-cov` do pre-commit já
   executa a suíte rápida com piso de 85% (baterias lentas ficam para
   `make test-cov`/`make test-slow` sob demanda e CI no push). Integração só
   com `CAPIBA_INTEGRATION=1`, separados dos offline.
3. **Qualidade** — `make lint` sem advertências novas, `make typecheck`,
   `make security`, piso de 85%; comentários/docstrings refletindo o
   comportamento atual; não refatorar fora do escopo.
4. **Deploy local & commit** — `make cluster-start && make port-forward` se
   preciso; mudanças em DAGs/`dbt/`: `make publish-artifacts`; em `src/`:
   `make rollout-airflow`; valide no ambiente local (port-forwards ou ingress
   `*.capiba.local:8443`); `git add` só dos arquivos do escopo, commit claro,
   sem `git push` automático.
5. **Homologação humana & push manual** — o desenvolvedor revisa o diff, roda
   `make test` e valida; push manual pelo usuário, nunca sem confirmação
   explícita; CI valida isort, ruff, mypy, pytest e bandit após o push.

## Decisões arquiteturais — requerem aprovação explícita

Nunca fazer sem confirmação prévia do usuário:

1. **Não remover a granularidade Airflow-native das DAGs** — a
   `dags/pipeline_factory.py` deve gerar **múltiplas tasks Airflow** (crawl por
   fonte, normalize, validate, destinations, post steps); uma única task `run`
   em background remove o retry por etapa e reexecuta crawl/normalize quando
   apenas `detect` ou `dbt_run` falham.
2. **Não trocar orquestração do Airflow por execução monolítica**, exceto com
   confirmação explícita de que se aceita perder observabilidade e retry
   granular.
3. **Não alterar o modelo de dados ou a semântica de retry sem testes** —
   mudanças em idempotência, duplicatas ou checkpoints exigem testes que provem
   o comportamento de retry/falha.

Em dúvida, pare e pergunte antes de commitar.

## RTK Configuration for Kimi AI

Redução de tokens via hook **block-and-suggest** mantido no projeto:
`.kimi/hooks/rtk-rewrite.py` (PreToolUse sobre `Bash`) — consulta `rtk rewrite`
e bloqueia o comando original com a forma RTK como sugestão; o agente reemite
reescrito. Fail-open: sem `rtk` ou sem o arquivo, nada é bloqueado. O Kimi só
registra hooks no `~/.kimi/config.toml` global; em cada máquina, uma entrada com
caminho relativo (o cwd do hook é o projeto da sessão — vale para qualquer
checkout que tenha o arquivo):

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "/usr/bin/python3 .kimi/hooks/rtk-rewrite.py"
timeout = 10
```

Comandos reescritos (redução de 60-90%): `ls`/`tree` → `rtk ls`;
`cat`/`head`/`tail` → `rtk read`; `grep`/`rg` → `rtk grep`; `git
status`/`log`/`diff`/`add`/`commit`/`push` → `rtk git ...`; `pytest` → `rtk
pytest`; `ruff check` → `rtk ruff check`; `docker ps`/`logs` → `rtk docker
...`. Com as ferramentas built-in do Kimi o hook não se aplica; para saída
compacta use explicitamente `rtk read <file>` (`-l aggressive` = só
assinaturas), `rtk smart`, `rtk grep`, `rtk find`, `rtk diff`. Analytics: `rtk
gain` (`--graph`/`--history`/`--daily`) e `rtk discover`. Config em
`~/.config/rtk/config.toml` (`[hooks] exclude_commands`, `[tee] enabled, mode =
"failures"`). Notas: RTK só intercepta bash; estimativas de token são `bytes /
4`; percentuais são redução na saída bash, não na conta total.

## DeepSeek Harness (dsh) — ferramenta experimental

Harness agêntico open source da DeepSeek (sobre o framework Cordis), ferramenta
de desenvolvimento **opcional e experimental** — não é componente do produto e
não substitui o Kimi Code CLI como driver. `scripts/setup.sh` verifica sua
presença; instalação pinada: `npm install -g @deepseek-ai/dsh@0.1.0-rc.7`
(developer preview com breaking changes — bump sempre deliberado, nunca
`latest`).

Spike (2026-08-20, scratch isolado): diferenciais confirmados — profiles
versionáveis (`package.json` + `cordis.patch.yml` reproduzem a config
byte-idêntica, auditável via `dsh --profile <nome> --dump-config`) e session
log append-only como fonte da verdade (requisição ao modelo 100% reconstruível
do JSONL). Hooks, compaction e sandbox/approval são paridade com o Kimi CLI.
Veredito: não adotar como driver principal; usos pontuais possíveis (runner
headless em CI, ambiente agêntico auditável).

**Atenção — telemetria**: o pacote `session-telemetry-otel` vem montado em modo
`DISABLED` (opt-in via `DSH_TELEMETRY_MODE`); habilitado, exporta logs de
sessão sem redação para endpoint da DeepSeek. Não habilitar — incompatível com
a governança do projeto.
