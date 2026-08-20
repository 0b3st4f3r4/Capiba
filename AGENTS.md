# AGENTS.md — Capiba

## Projeto

Capiba — **C**ruzamento e **A**nálise de **P**adrões e **I**ndícios em **B**ases **A**bertas.

Motor de detecção de fraude via dados abertos, a serviço do
**jornalismo de dados comunitário** (processo editorial em
`docs/jornalismo_dados.md`). A missão de longo prazo é construir
**comunidades de dados** — empresas, clientes e instituições públicas
compartilhando dados para formar inteligência soberana em território
nacional, como alternativa comunitária ao imperialismo de dados e à
hipervigilância global (ver README.md, "Objetivo").

Stack: Python 3.13, FastAPI, Airflow, ArangoDB (multi-modelo), MinIO,
scikit-learn + spaCy, Grafana, Keycloak (SSO/OIDC de todas as UIs, realm
`capiba`), Marquez (catálogo/lineage via OpenLineage), Trino (SQL sobre o
lake; fonte do Grafana para os marts gold), Apache Iceberg (Parquet no
MinIO) com catálogo REST Lakekeeper, dbt (dbt-trino) para os marts gold e
para os modelos de serving (`dbt/models/serving/`, materializados no
PostgreSQL DWH via catálogo Trino `dwh`). Redis (cache do quality monitor
e dos hot paths da API) vem habilitado por padrão; tudo degrada
graciosamente sem ele (`redis.enabled: false`). Observabilidade:
Prometheus (`prometheus.enabled`, TSDB em emptyDir, retenção 7d)
scrapeando kubelet/cAdvisor e Kepler (`kepler.enabled`, métricas de
energia na 28282); dashboards como código em `charts/capiba/dashboards/*.json`
via ConfigMap no provider de arquivos do Grafana. O runner publica
métricas por step na gold `platform_metrics` (`lake.write_platform_metrics`,
best-effort); o pipeline `hourly_pod_usage` (fonte `pod_usage` —
metrics-server in-cluster, `kubectl top` fora; fórmula `metrics_collect`)
alimenta `pod_usage_hourly`/`platform_cost_daily` (requests na seed
`dbt/seeds/requests.csv` — regerar quando o values.yaml mudar).

Comandos:

```bash
make install            # venv + deps de dev + Airflow
make test               # pytest rápido (unitários; baterias lentas puladas — CAPIBA_SLOW=1)
make test-slow          # só as baterias lentas (testes de regime, marker slow)
make test-cov           # suíte completa com cobertura (piso de 85%; inclui baterias)
make lint               # ruff check
make typecheck          # mypy + basedpyright
make security           # bandit (análise de segurança em src/)
make format             # ruff format
make publish-artifacts  # publica src/ + dags/ + dbt/ no MinIO (bucket capiba-artifacts)
make init-buckets       # cria buckets do MinIO + warehouses Iceberg (idempotente)
make dbt-run            # constrói os marts gold (dbt, com port-forwards ativos)
make dbt-test           # testes dbt de fontes/modelos
make dbt-docs           # gera e serve o catálogo dbt (marts gold + lineage)
make port-forward       # sobe port-forwards dos serviços do cluster (stop/status via
                        # make port-forward-stop / port-forward-status)
make ingest-mock        # pipeline offline com fontes mock, persistindo no lake
make build-airflow      # rebuild da imagem do Airflow (necessário se as deps mudarem)
make rollout-airflow    # reinicia o deploy do Airflow após publicar mudanças em src/
make cluster-start      # cria/inicia o cluster k3s nativo, Traefik, chart Capiba e Headlamp
                        # (compila e importa as imagens capiba/api e capiba/airflow no k3s)
make cluster-stop       # para o cluster k3s (não remove dados)
make cluster-remove     # para e remove o cluster k3s (destrutivo)
make cluster-status     # lista os pods do namespace capiba
make bump-version VERSION=0.2.0  # atualiza a versão em pyproject, chart, Makefile e API
make dashboard-token    # token de login do Headlamp (http://localhost:4466)
```

O Airflow sincroniza código, DAGs e o projeto dbt do MinIO (init container +
sidecar, intervalo `airflow.artifacts.syncIntervalSeconds`), sem rebuild de
imagem: mudanças em DAGs e `dbt/` são capturadas pelo sidecar; mudanças em
`src/` pedem `kubectl rollout restart deploy/capiba-airflow -n capiba` após
publicar.

## Layout e pipelines

Código em `src/capiba/` por vertical (ingestion, detection, quality,
evidence, db, api, notification, pipeline, config, transformations).
Pipelines de ingestão são **declarativos**: specs YAML em
`dags/pipelines/*.yaml` (fontes, janela, fórmula, validações, destinos,
post steps) resolvidos pelos registries de `pipeline/registry.py` e
executados pelo runner (`pipeline/runner.py`); `dags/pipeline_factory.py`
gera uma DAG por spec. Specs ativas: `daily_pncp` e
`monthly_transparency` (contratos, separados por fonte — falha isolada e
rate limits independentes, sem post steps), `daily_pncp_updates`
(`/v1/contratos/atualizacao`, bronze-only — flags de aditivo do PR-D-05
leem o bronze; o silver não é tocado), `monthly_federal_revenue`,
`weekly_sanctions`, `hourly_pod_usage`, `daily_querido_diario`
(diários oficiais de Recife, IBGE 2611606, via Querido Diário/OKBR) e
`monthly_tse` (snapshot fixo da prestação de contas eleitorais, ano
via `params.year`/`TSE_ELECTION_YEAR`, `reference_month` não se aplica).

Fórmulas do runner:

- `contracts_default` — crawl+normalize de contratos (PNCP, transparência).
- `file_dump` — dumps multi-arquivo (ex.: `federal_revenue` →
  `ingestion/cnpj.py`). Com `lake_silver`/`arangodb_graph` declarados e
  parser no `DUMP_PARSER_REGISTRY`, ganha `normalize_<fonte>` streaming:
  ZIPs Empresas/Estabelecimentos/Socios → silvers Iceberg
  `companies`/`establishments`/`partners` (opt-in via
  `FEDERAL_REVENUE_FILES`); o `Municipios.zip` vira a silver
  `rfb_municipalities` (código TOM → nome, elo da geo do fornecedor). O
  destino `arangodb_graph` carrega o grafo FtM: vértices `companies`
  e `persons`, arestas `ownership` ({persons,companies}→companies — sócio
  PJ vira Company e alimenta o `trace_ownership`) e `directorship`,
  classificadas por `cnpj.edge_kind_for_qualificacao`, via
  `bulk_upsert_cnpj` a partir do silver. Ao final da carga, a resolução de
  entidades (`detection/entities.py` — validada por D-07/D-07b: nome
  0,6 + documento mascarado 0,3 + faixa etária 0,1, limiar
  `DETECTION_ENTITY_THRESHOLD` default 0,85) grava arestas `same_as`
  (persons↔persons) best-effort, sem colapsar vértices.
- `entities_collect` — listas snapshot de sanções (`ceis`/`cnep`/`ceaf`;
  crawler `fetch_sanctions`, modelo `Sanction` em `ingestion/sanctions.py`;
  CEAF traz `masked_document`): `normalize_<fonte>` escreve na silver do
  `ENTITY_NORMALIZER_REGISTRY` (hoje `sanctions`); wrappers em
  `pipeline/entity_tasks.py`. O crawl (`task_crawl_entities`) persiste
  **checkpoint por página** no bronze (`<fonte>/pages/dt=<data>/page-NNNNN.json.gz`)
  — retry retoma da próxima página; 400s esporádicos são transitórios.
- `documents_collect` — documentos datados com janela (`querido_diario`,
  crawler `fetch_gazettes` em `ingestion/crawler_querido_diario.py`):
  `crawl_<fonte>` (metadados ao bronze) + `download_<fonte>_texts`
  (`persist_document_texts`; wrappers em `pipeline/document_tasks.py`)
  baixa o `txt_url` de cada diário para `<fonte>/files/dt=<run>/` com nome
  determinístico (`text_file_name`) — retry pula textos já persistidos,
  falhas de download são best-effort. Sem normalização silver.
- `metrics_collect` — telemetria (pod_usage).

Ingestão TSE (parser `ingestion/tse.py`): o download cobre prestação
de contas e `consulta_cand_<ano>.zip` (`TSE_CANDIDATES_BASE_URL`, gate do
eleito); o normalize streaming grava as silvers `campaign_donations`
(`receitas_candidatos_<ano>_BRASIL.csv`) e `candidacies`
(`consulta_cand_<ano>_BRASIL.csv`, coluna `DS_SITUACAO_TOTALIZACAO_TURNO`).
Documentos completos no silver; mascaramento é preocupação do mart gold
(PR-D-08 §2).

Idempotência e retry do lake: o download (`task_download_source`) sobe
cada arquivo ao bronze ao terminar e remove do tempdir; num retry,
arquivos já no bronze (`lake.list_bronze_files`) são pulados e reentram
no manifesto. O normalize (`task_normalize_dump`) é append-por-chunk e,
antes de parsear, DELETA a partição `dt=<run_date>` de cada entidade via
Trino (`lake.delete_silver_entities_partition`; falha aborta sem append)
— retry reprocessa sem duplicar. A escrita no silver `contracts`
(`lake.write_silver`) é **upsert-por-id** (DELETE via Trino dos ids do
lote + append; no catálogo sqlite offline degrada para append puro). Como
DELETE+append não é atômico, as DAGs da factory usam `max_active_runs=1`
(runs sobrepostas duplicavam linhas — observado em dt=2026-08-18/19).
`lake_maintenance.py` (semanal: expire_snapshots + optimize) e
`gold_detection.py` (diária 08:00 UTC: `dbt_run` → `detect` sobre TODO o
silver — é a "run final" após um backfill) seguem DAGs imperativas.
Transformações nomeadas em `src/capiba/transformations/` (um módulo por
transformação, `transform(records, **params)`).

## Geografia

Referência de municípios em `ingestion/geography.py`: CSV vendored
`ingestion/reference/municipios.csv` (kelvins/Municipios-Brasileiros, MIT
— atribuição em `reference/README.md`), lookups puros por (nome, UF)
normalizado e por IBGE (nomes são únicos por UF — de-para do comprador
PNCP determinístico). Silver `municipalities` (ibge_code, name, uf,
siafi_code, latitude, longitude) carregada por `lake.load_municipalities`
(idempotente por conteúdo, sem DAG nova). Na persistência
(`upsert_contract`/`bulk_upsert_contracts`), `buyers` ganha
ibge/lat/long via (city, uf) e `suppliers` ganha lat/long via cadeia
silver `establishments` (TOM) → `rfb_municipalities` → referência
vendored — best-effort, funções puras injetáveis
(`geography.buyer_geo_fields`, `geography.build_supplier_geo_index`).

O sinal `anomalous_geography` (contrato PR-D-09 §3, validado no sintético
pela bateria D-09, `docs/results/R-D-09.md`, 5/5; P6/volume real
pendentes) vive em `detection/geography.py` (puro, sobre o silver):
haversine R = 6371,0 km entre sedes municipais, gate estrito
`DETECTION_GEOGRAPHY_MAX_DISTANCE_KM` (default 100 km), score
`round(min(1.0, distância / DETECTION_GEOGRAPHY_SCORE_REFERENCE), 4)`
(default 1000 km) — placeholders pré-registrados (mudança exige PR-D-09b);
um sinal por (fornecedor PJ, município comprador); PF e pares com elo de
de-para ausente nunca sinalizam. Emissão best-effort no `task_detect`
(carrega `municipalities` idempotente antes). O operador AQL legado
`graphs.anomalous_geography` foi removido (morto — vértices `bid`
inexistentes; decisão em Revisões do PR-D-09).

## Detecção, evidência e triagem

Sinais best-effort emitidos no `task_detect` (nunca derrubam a task):

- `sanctioned_supplier` (match exato por documento) e
  `sanctioned_name_match` (screening fuzzy, `detection/screening_fuzzy.py`
  — validado por D-06b, `docs/results/R-D-06b.md`: veto por documento
  divergente, score 0,6 nome + 0,4 documento, limiares 0,85/0,95), sobre a
  silver `sanctions`.
- `political_connection` (`detection/political.py`, contrato PR-D-08 §3 —
  validado no sintético por D-08, `docs/results/R-D-08.md`, P8/volume real
  pendentes): doador de campanha de prefeito eleito (match exato por
  documento, originário prioritário — nome nunca é evidência) que vira
  fornecedor do município na janela do mandato (derivada de
  `TSE_ELECTION_YEAR`); piso `DETECTION_POLITICAL_MIN_DONATION` (default
  R$ 1.000), concentração `DETECTION_POLITICAL_MIN_SHARE` (default 0,05),
  score `min(1.0, share / DETECTION_POLITICAL_SCORE_REFERENCE)` (default
  0,25) — placeholders pré-registrados (mudança exige PR-D-08b). O mart
  gold `political_connections` publica os sinais enriquecidos com as
  silvers TSE (última partição) e a seed `dbt/seeds/ue_siafi_crosswalk.csv`
  (incremental, piloto Recife: UE 25313, SIAFI 2531); LGPD: CPF mascarado
  padrão CEAF, CNPJ completo, chave `signal_id` sha256.
- `collusion_network` (`detect_collusion` sobre o ArangoDB) com limiar
  `DETECTION_COLLUSION_MIN_WINS` (default 3, placeholder validado por
  D-02; calibração em volume real D-03/D-03b **inconclusiva** — pares
  explodem no regime real; próximo refinamento PR-D-03c) e score binário.

Grafo na API: `GET /v1/graph/ownership/{cnpj}` (aresta FtM `ownership`;
CNPJ normalizado para `cnpj_basico`), `GET /v1/graph/partners/{siafi_code}`
(`partners_of_buyer`), `GET /v1/graph/ftm/{cnpj}` (`db/ftm.py`).

Evidência: vertical exposta no router `/v1/evidence` (upload
multipart, listagem por contrato, download por SHA-256; storage MinIO sob
demanda via `get_storage()`). O `task_detect` grava, best-effort, pacotes
reproduzíveis por sinal (`evidence/packages.py`): pacote de lote por run
(linhas silver + `source_rows_sha256` + janela + versão) e manifesto por
sinal com a chave de triagem e `batch_sha256`, servidos por
`GET /v1/signals/{key}/evidence`; `reproduce_signal` reexecuta
`detect_fraud_signals` sobre o pacote para conferir o score.

Triagem editorial: `db/triage.py` (coleção `signal_reviews`, chave
`{entity_type}:{entity_id}:{signal_type}`; `pending_review` →
`confirmed`/`rejected`/`published`, revisor obrigatório, `published`
terminal). O `task_detect` registra sinais novos como `pending_review`;
a API expõe `/v1/triage` (listagem, transição, relatório de precisão por
operador — rótulos para o ML). Interface humana: página `/triage` do
portal (CSS em `api/static/portal.css`).

Assinatura de alertas por município: dispara SOMENTE na transição
para `published` (gancho best-effort em `api/routers/triage.py` →
`notification/subscriptions.py`): o município do sinal é resolvido para
IBGE via `details` (city/uf) ou pelo par comprador city/UF mais frequente
dos contratos da entidade (→ `ingestion/geography.py`); sinais sem
município resolvível não disparam (contados em log). Coleção
`subscriptions` (`db/subscriptions.py`): email, ibge_code, status
(pending/confirmed/unsubscribed) e apenas o HASH sha256 do token opaco
permanente (o mesmo link confirma e cancela, entregue no e-mail de
confirmação). Rotas públicas: `POST /v1/subscriptions` (resposta genérica,
sem enumerar), `GET /v1/subscriptions/confirm|unsubscribe?token=...`.
E-mails (um por assinante) usam o template `subscription` do
NotificationDispatcher e linkam o pacote de evidências; URL base
`PUBLIC_API_URL` (default `https://api.{PORTAL_DOMAIN}:8443`).

Alertas internos por e-mail (`notification/alerts.py`, wrapper síncrono
do dispatcher async) disparam do `task_detect` (sinais ≥
`NOTIFICATION_ALERT_SCORE`, default 0.7) e do `task_validate_pipeline`
(relatório inválido ou erro de normalização > 5%); no-op sem
`NOTIFICATION_RECIPIENTS`. O `task_validate_pipeline` também alimenta o
`QualityMonitor` (`record_batch`, best-effort); o `NotificationScheduler`
(relatórios periódicos) só inicia no lifespan da API com
`NOTIFICATION_RECIPIENTS` configurado.

## Saída pública e pós-steps

A `gold_detection` termina com o post step `export_public_marts`
(`pipeline/public_export.py`): exporta os marts liberados para o bucket
`capiba-public` (`PUBLIC_EXPORT_BUCKET`) em CSV + Parquet + manifest,
versionado `marts/<mart>/dt=<data-da-run>/` (leitura via Trino, escrita
via client MinIO do lake). Allowlist LGPD declarativa e **fail-closed** —
`PUBLIC_MARTS` (justificativa por mart) e `EXCLUDED_MARTS` (`pod_usage_*`,
`platform_cost_*`, `data_quality_*` = telemetria interna;
`contract_red_flags` = `supplier_id` pode ser CPF completo);
`political_connections` entra porque mascara CPF na origem. Mart novo sem
classificação falha o guarda (`tests/test_public_export.py`). API pública
(`api/routers/public.py`, sem auth): `GET /v1/public/marts`,
`GET /v1/public/marts/{name}/{csv|parquet}` (302 presignado, `?dt=` pina)
e `GET /v1/public/methodology` (gerado do `_marts.yml` + specs YAML;
degrada sem dags/ e dbt/ na imagem). Bucket criado pelo
`make init-buckets`; política de leitura pública é decisão de deploy.

Post steps (`dbt_run`, `detect`) são **pulados em runs de backfill**
(`task_post_step` levanta `AirflowSkipException` quando `run_type ==
"backfill"` — reprocessam as tabelas inteiras); após um backfill, dispare
uma run regular. O post step `dbt_run` aceita mapping com `select`
(vazio = projeto todo): pipelines frequentes declaram só os marts
alimentados — o `hourly_pod_usage` roda `--select pod_usage_hourly
platform_cost_daily` (run completo OOMKillava o Trino); o run completo
fica com a `gold_detection`.

## Infra e convenções

Projeto dbt em `dbt/` (profile `capiba`, dbt-trino sobre o catálogo gold;
marts Iceberg no bucket capiba-gold). O lake usa Iceberg via
`pipeline/lake.py` + pyiceberg, catalogado pelo Lakekeeper; offline,
aponte `ICEBERG_CATALOG_URI` para `sqlite:///...` com
`ICEBERG_LOCAL_WAREHOUSE`. Testes que exigem infra externa são marcados
`@pytest.mark.integration` (ative com `CAPIBA_INTEGRATION=1`); testes de
regime/calibração (baterias) são `@pytest.mark.slow` (ative com
`CAPIBA_SLOW=1` — CI e `make test-cov`/`make test-slow` habilitam).
Testes BDD (pytest-bdd) em `tests/bdd/`, features em
`tests/bdd/features/*.feature`.

Experimentos de detecção (novos sinais, calibração) seguem doutrina de
pré-registro (adaptada do programa Tanajura): predição numérica
falsificável com critérios de sucesso **e de refutação** em
`docs/preregistrations/PR-D-*.md` antes de qualquer execução, config
declarativa em `experiments/detect/*.json` (seeds inclusas), resultados —
inclusive negativos — em `docs/results/R-D-*.md`. Detalhes em
`docs/preregistrations/README.md`.

Governança de dados (DAMA-DMBOK, papéis steward/custodian, régua
LGPD/LAI, federação): `docs/governanca.md`. Processo editorial:
`docs/jornalismo_dados.md`. Backlog orientado à missão:
`docs/oportunidades.md`.

Convenção de nomenclatura: os códigos `O*` do backlog (O1..O12) são
exclusivos dos trackers de processo (`docs/oportunidades.md`,
`docs/gaps.md`) e não devem ser citados em código nem em documentação
permanente.

Acesso às UIs sem port-forward: ingress Traefik (DaemonSet, hostPorts
8088/8443 — a porta 80 é do Apache do host) em
`https://<serviço>.capiba.local:8443` (api, grafana, marquez, iceberg,
minio, s3, trino, airflow); `scripts/setup.sh` mapeia os hosts no
`/etc/hosts`. Certificado self-signed (wildcard `*.capiba.local`,
`scripts/gen-certs.sh`, secret `capiba-tls`) — o browser pede exceção.
HTTP na 8088 segue respondendo (sem redirect). CI em
`.github/workflows/ci.yml` (ruff, mypy, pytest com piso de 85% — também
aplicado pelo hook `pytest-cov` do pre-commit).

SSO: Keycloak é o IdP OIDC de todas as UIs — portal capiba-dashboard na
API (`/`, `api/portal.py`), Grafana, Airflow (FAB OAuth), MinIO Console,
Lakekeeper UI e Headlamp. Usuário dev: `capiba`/`capiba-sso`
(`keycloak.devUser`), ressincronizado a cada `make helm-upgrade` pelo
hook `templates/keycloak/job-sync-user.yaml`. O issuer é HTTPS em
`https://keycloak.capiba.local:8443` (pods confiam no cert via CA
`capiba-tls` como `SSL_CERT_FILE`); um rewrite de CoreDNS
(`scripts/cluster.sh`, passo 4) resolve esse host para o ClusterIP do
Traefik (pinado — clusterIP é imutável), e backchannels de máquina usam
`capiba-keycloak:8080`. Clientes de máquina do lake (Trino, pyiceberg,
`init_buckets.py`) usam o client `capiba-services` (client_credentials).
Fallbacks locais: MinIO root, Grafana admin (`grafana.auth`) e token do
Headlamp (`make dashboard-token`).

## Processo de desenvolvimento

Todo trabalho no código segue um ciclo de BDD + TDD com cinco fases. Antes de
começar, esclarecer com o usuário:

1. Qual o objetivo concreto e o critério de aceitação?
2. Existe teste (unitário, BDD ou dbt) que cubra o comportamento esperado?
3. A mudança envolve integração com infra externa (ArangoDB, MinIO, Trino,
   etc.)?
4. Há preferência entre corrigir o atual vs. refatorar? Qual o limite de escopo?

### Proteção de trabalho em andamento

Antes de executar qualquer ação que toque em arquivos do repositório, verifique
se há modificações preexistentes no working tree (`git status`, `git diff`,
 timestamps de escrita dos arquivos, etc.). Se houver alterações que não foram
solicitadas na tarefa atual, **pare e questione o usuário antes de prosseguir**.
Não formate, corrija, mova, remova ou committe arquivos de trabalho em
andamento sem autorização explícita.

### 1. Investigação

- Leia o código e os testes relacionados antes de propor solução.
- Use `make test` para confirmar o estado atual (verde antes de alterar).
- Para falhas, reproduza o erro e identifique a causa raiz com logs/testes.
- Para features novas, defina o contrato (API, modelo, DAG, teste BDD) antes de
  implementar.

### 2. Testes e desenvolvimento

- **BDD**: features em Gherkin ficam em `tests/bdd/features/*.feature`; passos
  correspondentes em `tests/bdd/test_*.py`. Escreva ou atualize a feature antes
  do código quando possível.
- **TDD**: escreva/adapte o teste unitário em `tests/` antes ou junto com a
  implementação em `src/capiba/`.
- Rode os testes de forma iterativa:
  ```bash
  make test              # vermelho → implemente → verde
  make test-cov          # cobertura mínima de 85%
  make lint && make typecheck
  ```
- **Economia de validação**: durante o desenvolvimento, rode apenas os testes
  do escopo alterado (`pytest tests/test_x.py tests/bdd/test_y.py -q`); use
  `make test` (suíte rápida, sem as baterias lentas) ao fechar a rodada.
  **Não** rode `make test-cov` separadamente antes de commitar — o hook
  `pytest-cov` do pre-commit já executa a suíte rápida com o piso de
  cobertura de 85% (as baterias lentas, marker `slow`, ficam para o
  `make test-cov`/`make test-slow` sob demanda e para o CI no push).
  Rodá-lo antes duplica 20–30 min de baterias lentas sem ganho.
- Testes de integração (`@pytest.mark.integration`) só rodam com
  `CAPIBA_INTEGRATION=1`; mantenha-os separados dos testes offline.

### 3. Avaliação de qualidade

- `make lint` deve passar sem advertências novas.
- `make typecheck` deve passar (mypy + basedpyright).
- `make security` deve passar (bandit em `src/`).
- `make test-cov` deve manter o piso de 85%.
- Revisar comentários e docstrings para que reflitam o comportamento atual.
- Evite refatorar código fora do escopo da tarefa.

### 4. Deploy local (k3s) & commit

- Suba o cluster local se ainda não estiver rodando:
  ```bash
  make cluster-start
  make port-forward
  ```
- Para mudanças que afetam DAGs ou `dbt/`, publique os artefatos:
  ```bash
  make publish-artifacts
  ```
- Para mudanças em `src/`, reinicie o Airflow:
  ```bash
  make rollout-airflow
  ```
- Valide o comportamento no ambiente local (port-forwards ou ingress
  `*.capiba.local:8443`).
- Faça `git add` apenas dos arquivos do escopo e crie o commit com mensagem
  clara (não executamos `git push` automaticamente).

### 5. Homologação humana & push manual

- O desenvolvedor humano revisa o diff, roda `make test` e valida localmente.
- O push para o remoto é feito manualmente pelo usuário; não executamos
  `git push` sem confirmação explícita.
- Após o push, o CI em `.github/workflows/ci.yml` valida ruff, mypy e pytest.

## Decisões arquiteturais — requerem aprovação explícita

As mudanças abaixo nunca devem ser feitas sem confirmação prévia do usuário,
mesmo que pareçam "mais simples" ou "mais limpas":

1. **Não remover a granularidade Airflow-native das DAGs.**
   Os pipelines declarativos em `dags/pipelines/*.yaml` são configuração, mas
   `dags/pipeline_factory.py` deve gerar **múltiplas tasks Airflow** (crawl por
   fonte, normalize, validate, destinations, post steps). Juntar tudo em uma
   única task `run` que executa o pipeline em background remove o retry por
   etapa e reexecuta crawl/normalize quando apenas `detect` ou `dbt_run`
   falham.

2. **Não trocar orquestração do Airflow por execução monolítica.**
   Não substituir tasks/dependências do Airflow por uma função Python única
   chamada em background, exceto se o usuário confirmar explicitamente que
   aceita perder observabilidade e retry granular.

3. **Não alterar o modelo de dados ou a semântica de retry sem testes.**
   Mudanças que afetam idempotência, duplicatas ou checkpoints devem vir
   acompanhadas de testes que provem o comportamento de retry/falha.

Se houver dúvida sobre qualquer uma dessas fronteiras, pare e pergunte ao
usuário antes de commitar.

## RTK Configuration for Kimi AI

A redução de tokens do RTK é aplicada por um hook **block-and-suggest**
mantido no projeto: `.kimi/hooks/rtk-rewrite.py` (PreToolUse sobre `Bash`).
O hook consulta `rtk rewrite` e bloqueia o comando original com a forma RTK
como sugestão; o agente reemite já reescrito. Fail-open: sem `rtk` na
máquina ou sem o arquivo no projeto, nada é bloqueado.

O Kimi só registra hooks no `~/.kimi/config.toml` global (não há config de
projeto). Em cada máquina, basta uma entrada apontando para o caminho
relativo — o working directory do hook é o projeto da sessão, então ela
vale para qualquer checkout que tenha o arquivo:

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "/usr/bin/python3 .kimi/hooks/rtk-rewrite.py"
timeout = 10
```

Comandos reescritos (redução de 60-90%): `ls`/`tree` → `rtk ls`;
`cat`/`head`/`tail` → `rtk read`; `grep`/`rg` → `rtk grep`; `git
status`/`log`/`diff`/`add`/`commit`/`push` → `rtk git ...`; `pytest` →
`rtk pytest`; `ruff check` → `rtk ruff check`; `docker ps`/`logs` →
`rtk docker ...`.

Com as ferramentas built-in do Kimi (`Read`, `Grep`, `Glob`) o hook não se
aplica; para saída compacta, use explicitamente: `rtk read <file>` (`-l
aggressive` = só assinaturas), `rtk smart <file>`, `rtk grep <pat> <path>`,
`rtk find <pat> <path>`, `rtk diff <f1> <f2>`. Analytics: `rtk gain`
(`--graph`/`--history`/`--daily`) e `rtk discover`. Config em
`~/.config/rtk/config.toml` (`[hooks] exclude_commands`, `[tee] enabled,
mode = "failures"`). Notas: RTK só intercepta bash; estimativas de token
são `bytes / 4`; percentuais são redução na saída bash, não na conta total.
