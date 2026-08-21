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
Prometheus (`prometheus.enabled`) e Kepler (`kepler.enabled`); dashboards como
código em `charts/capiba/dashboards/*.json`. O pipeline `hourly_pod_usage`
(fonte `pod_usage`; fórmula `metrics_collect`) alimenta
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
db, api, notification, pipeline, config). Pipelines de
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
  Com parser no `DUMP_PARSER_REGISTRY`, ganha `normalize_<fonte>` streaming:
  ZIPs RFB → silvers `companies`/`establishments`/`partners` (opt-in via
  `FEDERAL_REVENUE_FILES`) e `rfb_municipalities` (elo da geo do fornecedor).
  O destino `arangodb_graph` carrega o grafo FtM (vértices
  `companies`/`persons`, arestas `ownership`/`directorship`) via
  `bulk_upsert_cnpj` e, ao final, grava arestas `same_as` best-effort
  (resolução de entidades, `detection/entities.py`, limiar
  `DETECTION_ENTITY_THRESHOLD` default 0,85), sem colapsar vértices.
- `entities_collect` — snapshots de sanções (`ceis`/`cnep`/`ceaf`;
  `fetch_sanctions` em `ingestion/sanctions.py`; wrappers em
  `pipeline/entity_tasks.py`) → silver `sanctions`. O crawl persiste
  **checkpoint por página** no bronze — retry retoma da próxima página.
- `documents_collect` — diários com janela (`querido_diario`, `fetch_gazettes`;
  wrappers em `pipeline/document_tasks.py`): metadados ao bronze + textos em
  `<fonte>/files/dt=<run>/` com nome determinístico — retry pula os já
  persistidos, falhas de download best-effort. Sem normalização silver.
- `metrics_collect` — telemetria (pod_usage).
- `terms_collect` — termos contratuais PNCP (`fetch_contract_terms` em
  `ingestion/crawler_pncp.py`, `PNCP_TERMS_API_URL`; wrapper em
  `pipeline/term_tasks.py`): **checkpoint por contrato** no bronze — retry pula
  os já persistidos, falhas best-effort. Bronze-only; as flags de termo
  (`compute_term_flags` em `detection/amendments.py`) só entram no mart após o
  veredito de Q4.

Ingestão TSE (`ingestion/tse.py`): sem download — o CDN bloqueia clientes CLI
via Akamai Bot Manager (403). Os dumps
`prestacao_de_contas_eleitorais_candidatos_<ano>.zip` e
`consulta_cand_<ano>.zip` vivem como **âncora congelada** no bronze
`capiba-bronze/tse/reference/` (upload manual via browser; sha256 no R-D-08b);
`download_tse_dump` resolve a âncora pelo ano da run
(`params.year`/`TSE_ELECTION_YEAR`) e falha com mensagem clara se ela não
existir. Multi-ano: o normalize deleta por (dt, `election_year`); demais
fontes deletam a partição inteira. Normalize streaming grava as silvers
`campaign_donations` e `candidacies`; documentos completos no silver —
mascaramento é preocupação do mart gold (PR-D-08 §2).

### Idempotência, retry e memória — regras duras

Detalhe operacional completo (lições de volume real, 2026-08):
`docs/operacao_lake.md`.

- Download ao bronze por arquivo, com skip dos já persistidos no retry;
  normalize append-por-chunk com DELETE prévio da partição `dt=<run_date>`.
- Silver `contracts`: **upsert-por-id** (DELETE Trino + append); gold
  `fraud_signals`: **replace-por-partição**.
- `table.refresh()` obrigatório entre DELETE Trino e append pyiceberg.
- DELETE+append não é atômico → DAGs da factory com `max_active_runs=1`;
  backfills SEMPRE com `--max-active-runs 1 --run-on-latest-version`.
- `lake.read_silver_contracts` lê via **Trino** no cluster (o scan pyiceberg
  não decodifica os delete files do Trino).
- Tasks pesadas (crawls, normalizes, destinos lake/grafo, `detect`) rodam no
  **pool `heavy_lake` (1 slot)**, via `HEAVY_POOL`;
  `AIRFLOW__CORE__PARALLELISM: "8"`.
- Leitura de `establishments` sempre **seletiva**
  (`lake.read_establishments_for_cnpjs`) — nunca materializar a tabela RFB
  completa.

## Geografia

Referência em `ingestion/geography.py`: CSV vendored
`ingestion/reference/municipios.csv` (kelvins/Municipios-Brasileiros, MIT;
proveniência em `src/capiba/ingestion/reference/README.md`),
lookups puros por (nome, UF) normalizado e por IBGE. Silver `municipalities`
carregada por `lake.load_municipalities` (idempotente por conteúdo). Na
persistência, `buyers` ganha ibge/lat/long via (city, uf) e `suppliers` via
cadeia silver `establishments` (TOM) → `rfb_municipalities` → referência —
best-effort, funções puras injetáveis (`geography.buyer_geo_fields`,
`geography.build_supplier_geo_index`).

## Detecção, evidência e triagem

Detalhe por sinal (scores, limiares, validações): `docs/operadores.md`;
vereditos das baterias D-*: índice em `docs/preregistrations/README.md`.

Sinais ativos — todos emitidos **best-effort** no `task_detect` (nunca
derrubam a task):

- `sanctioned_supplier` (`detection/screening.py`) — match exato por documento
  contra a silver `sanctions`.
- `sanctioned_name_match` (`detection/screening_fuzzy.py`) — screening fuzzy
  nome + documento mascarado, com prefilter vetorizado exato.
- `political_connection` (`detection/political.py`) — doador de campanha (TSE)
  que vira fornecedor do município na janela do mandato.
- `anomalous_geography` (`detection/geography.py`) — distância sede PJ ×
  município comprador; o `task_detect` carrega `municipalities` antes.
- `collusion_network` (`detection/graphs.py`, sobre o ArangoDB) — emissão
  ranqueada top-`DETECTION_COLLUSION_TOP_K` (default 500) com guarda de escala
  `DETECTION_COLLUSION_MAX_PAIRS`.
- `notice_clone` (`detection/notice_clone.py`) — editais clonados nos textos
  bronze do Querido Diário.
- Screening de PEPs via yente/OpenSanctions foi refutado (D-12): adapter em
  `detection/pep_screening.py`, nenhum sinal novo.

Grafo na API: `GET /v1/graph/ownership/{cnpj}` (aresta FtM `ownership`; CNPJ
normalizado para `cnpj_basico`), `GET /v1/graph/partners/{siafi_code}`
(`partners_of_buyer`), `GET /v1/graph/ftm/{cnpj}` (`db/ftm.py`).

Evidência: router `/v1/evidence` (upload/listagem/download por SHA-256). O
`task_detect` grava, best-effort, pacotes reproduzíveis por sinal
(`evidence/packages.py`) com a chave de triagem, servidos por
`GET /v1/signals/{key}/evidence`; `reproduce_signal` reexecuta
`detect_fraud_signals` sobre o pacote para conferir o score.

Triagem editorial: `db/triage.py` (coleção `signal_reviews`, chave
`{entity_type}:{entity_id}:{signal_type}`; `pending_review` →
`confirmed`/`rejected`/`published`, revisor obrigatório, `published` terminal).
O `task_detect` registra sinais novos como `pending_review`; a API expõe
`/v1/triage` e o portal a página `/triage`. **A fábrica de rótulos está
parada** (medido em 2026-08-21, gate do PR-D-11 refutado): 816.405 sinais na
fila, 100% `pending_review`, zero revisões — 99,1% `collusion_network` com
score binário 1,0 indistinguível. Listagem ordenada e paginada **server-side**
(`list_reviews`/`count_reviews`); destrave: `docs/gaps.md` (Editorial 13).

Assinatura de alertas por município: dispara SOMENTE na transição para
`published` (gancho best-effort em `api/routers/triage.py` →
`notification/subscriptions.py`); o município é resolvido para IBGE via
`details` ou pelo comprador mais frequente dos contratos da entidade; sinais
sem município resolvível não disparam. Coleção `subscriptions`: email,
ibge_code, status e apenas o HASH sha256 do token opaco (o mesmo link confirma
e cancela). Rotas públicas: `POST /v1/subscriptions` (resposta genérica), `GET
/v1/subscriptions/confirm|unsubscribe?token=...`; URL base `PUBLIC_API_URL`.

Alertas internos (`notification/alerts.py`) disparam do `task_detect` (sinais
≥ `NOTIFICATION_ALERT_SCORE`, default 0.7; cap `NOTIFICATION_ALERT_MAX_SIGNALS`,
default 50) e do `task_validate_pipeline` (relatório inválido ou erro de
normalização > 5%); no-op sem `NOTIFICATION_RECIPIENTS`. O
`task_validate_pipeline` também alimenta o `QualityMonitor` (`record_batch`,
best-effort); o `NotificationScheduler` só inicia no lifespan da API com
`NOTIFICATION_RECIPIENTS` configurado.

## Saída pública e pós-steps

A `gold_detection` dispara, após o `dbt_run`, o `detect` e o post step
`export_public_marts` em paralelo; o export (`pipeline/public_export.py`) leva
os marts liberados ao bucket `capiba-public` (`PUBLIC_EXPORT_BUCKET`) em CSV +
Parquet + manifest, versionado `marts/<mart>/dt=<data-da-run>/`. Allowlist
LGPD declarativa e **fail-closed** — `PUBLIC_MARTS` (justificativa por mart) e
`EXCLUDED_MARTS` (`pod_usage_*`, `platform_cost_*`, `data_quality_*` =
telemetria interna; `contract_red_flags` = `supplier_id` pode ser CPF
completo); `political_connections` entra porque mascara CPF na origem. Mart
novo sem classificação falha o guarda (`tests/test_public_export.py`); mart
liberado mas **sem tabela gold** é pulado com warning (`skipped`). API pública
(`api/routers/public.py`, sem auth): `GET /v1/public/marts`, `GET
/v1/public/marts/{name}/{csv|parquet}` (302 presignado, `?dt=` pina), `GET
/v1/public/methodology`. Bucket criado pelo `make init-buckets`; política de
leitura pública é decisão de deploy.

Post steps (`dbt_run`, `detect`) são **pulados em runs de backfill**
(`task_post_step` levanta `AirflowSkipException` quando `run_type ==
"backfill"`); após um backfill, dispare uma run regular. O post step
`dbt_run` aceita mapping com `select` (vazio = projeto todo): pipelines
frequentes declaram só os marts alimentados — o `hourly_pod_usage` roda
`--select pod_usage_hourly platform_cost_daily`; o run completo fica com a
`gold_detection` — que **exclui automaticamente os marts dependentes de TSE**
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

Teto da suíte rápida (`make test`): **2 minutos** (referência 2026-08-21: ~30
s, 1255 testes). Se estourar, há teste vazando para infra real — investigue.
Todo teste unitário deve mockar clientes de infra (`get_capiba_db`, `lake`,
storage MinIO) — padrão de referência: `tests/test_pipeline.py` (`@patch` em
`capiba.pipeline.detect_task.get_capiba_db` e `collusion_eligibility`). A
guarda autouse `_block_real_infra` em `tests/conftest.py` faz clientes reais
(ArangoDB, MinIO, Trino, Redis) falharem rápido fora de
`@pytest.mark.integration`.

Experimentos de detecção seguem doutrina de pré-registro: predição numérica
falsificável com critérios de sucesso **e de refutação** em
`docs/preregistrations/PR-D-*.md` antes de qualquer execução, config
declarativa em `experiments/detect/*.json` (seeds inclusas), resultados —
inclusive negativos — em `docs/results/R-D-*.md`. Detalhes e índice de
baterias em `docs/preregistrations/README.md`.

Governança, backlog e demais documentos de processo: seção "Documentação" do
README.md. Convenção: os códigos `O*` do backlog (O1..O12) são exclusivos dos
trackers de processo (`docs/oportunidades.md`, `docs/gaps.md`) — não citar em
código nem em documentação permanente.

Convenções de documentação: o índice único é `docs/README.md` — doc novo ou
alterado entra nele no mesmo commit (regra de frescor). Todo doc da raiz de
`docs/` carrega o cabeçalho-padrão (blockquote com Propósito / Quando
consultar / Relacionados / Sincronizado com); `docs/preregistrations/*.md` e
`docs/results/*.md` têm formato próprio e ficam de fora.

Acesso às UIs: ingress Traefik (DaemonSet, hostPorts 8088/8443) em
`https://<serviço>.capiba.local:8443`, com `/etc/hosts` mapeado por
`scripts/setup.sh` e cert self-signed `capiba-tls` (browser pede exceção) —
**não dependa de port-forward** para UIs; port-forwards (`make port-forward`)
são só para clientes de máquina (pyiceberg, dbt, `init_buckets.py`). CI em
`.github/workflows/ci.yml` (isort, ruff, mypy, pytest com piso de 85% — também
aplicado pelo hook `pytest-cov` do pre-commit — e bandit).

SSO: Keycloak é o IdP OIDC de todas as UIs, realm `capiba`; usuário dev
`capiba`/`capiba-sso` (`keycloak.devUser`, ressincronizado a cada
`make helm-upgrade`). Regras: issuer OIDC é HTTPS
(`https://keycloak.capiba.local:8443` — pods confiam via CA `capiba-tls` como
`SSL_CERT_FILE`; backchannels de máquina usam `capiba-keycloak:8080`);
clientes de máquina do lake (Trino, pyiceberg, `init_buckets.py`) usam o
client `capiba-services` (client_credentials). Detalhes operacionais e
runbook (ciclo de vida do cluster, clients, hooks, backup, troubleshooting):
`docs/operacao.md`.

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

## Ferramentas do ambiente de desenvolvimento

RTK (redução de tokens via hook block-and-suggest do Kimi) e DeepSeek Harness
(dsh, experimental — telemetria NÃO habilitar): configuração e instruções
práticas em `docs/ambiente_dev.md`.
