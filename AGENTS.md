# AGENTS.md — Capiba

## Projeto

Capiba — **C**ruzamento e **A**nálise de **P**adrões e **I**ndícios em **B**ases **A**bertas.

Motor de detecção de fraude via dados abertos, a serviço do
**jornalismo de dados comunitário** (processo editorial em
`docs/jornalismo_dados.md`). A missão de longo prazo é
construir **comunidades de dados** — empresas, clientes e instituições
públicas compartilhando dados para formar inteligência soberana em
território nacional, como alternativa comunitária ao imperialismo de dados
e à hipervigilância global (ver README.md, "Objetivo").

Stack: Python 3.13, FastAPI, Airflow,
ArangoDB (multi-modelo), MinIO, scikit-learn + spaCy, Grafana,
Keycloak (SSO/OIDC de todas as UIs, realm `capiba`),
Marquez (catálogo de dados/lineage via OpenLineage),
Trino (SQL sobre o lake; fonte de dados do Grafana para os marts gold),
Apache Iceberg (tabelas Parquet no MinIO) com catálogo REST Lakekeeper e
dbt (dbt-trino) para os marts gold — e para os modelos de serving
(`dbt/models/serving/`) materializados no PostgreSQL DWH via catálogo Trino
`dwh`. Redis (cache do monitor de qualidade e dos hot paths da API) vem
habilitado por padrão; tudo degrada graciosamente sem ele
(`redis.enabled: false` para economizar recursos).
Observabilidade: Prometheus (`prometheus.enabled`, TSDB em emptyDir —
dev; retenção 7d) scrapeando kubelet/cAdvisor do k3s e o Kepler
(`kepler.enabled`, DaemonSet, métricas de energia na porta 28282), com
dashboards como código em `charts/capiba/dashboards/*.json` montados via
ConfigMap no provider de arquivos do Grafana. O runner publica métricas
por step de cada run na tabela gold `platform_metrics`
(`lake.write_platform_metrics`, best-effort) e o pipeline declarativo
`hourly_pod_usage` (fonte `pod_usage` — metrics-server in-cluster,
`kubectl top` fora; fórmula `metrics_collect`) alimenta os marts
`pod_usage_hourly`/`platform_cost_daily` (requests na seed dbt
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

Layout: código em `src/capiba/` por vertical (ingestion, detection, quality,
evidence, db, api, notification, pipeline, config, transformations). DAGs em
`dags/`: pipelines de ingestão são declarativos — specs YAML em
`dags/pipelines/*.yaml` (fontes, janela temporal, fórmula, validações,
destinos e post steps, sem código Python) resolvidos pelos registries de
`src/capiba/pipeline/registry.py` e executados pelo runner
(`src/capiba/pipeline/runner.py`, fórmulas `contracts_default`,
`file_dump`, `metrics_collect` e `entities_collect`); `dags/pipeline_factory.py` gera uma DAG
por spec no parse do
Airflow (`daily_ingestion` a partir de `daily_contracts.yaml`,
`monthly_federal_revenue` a partir de `monthly_federal_revenue.yaml`,
`hourly_pod_usage` a partir de `hourly_pod_usage.yaml`,
`weekly_sanctions` a partir de `weekly_sanctions.yaml`).
A fórmula `file_dump`, quando a spec declara `lake_silver`/`arangodb_graph`
e a fonte tem parser em `DUMP_PARSER_REGISTRY` (ex.: `federal_revenue` →
`src/capiba/ingestion/cnpj.py`), ganha uma etapa `normalize_<fonte>`
streaming: parse chunked dos ZIPs Empresas/Estabelecimentos/Socios para as
tabelas silver Iceberg `companies`/`establishments`/`partners`
(opt-in via `FEDERAL_REVENUE_FILES`), e o destino `arangodb_graph` carrega
os vértices `companies`/`partners` e arestas `partner_of` no grafo
(`bulk_upsert_cnpj`, em lote, a partir do silver).
A fórmula `entities_collect` cobre listas de entidades snapshot (fontes
`ceis`/`cnep` do Portal da Transparência — crawler `fetch_sanctions`,
modelo `Sanction` em `src/capiba/ingestion/sanctions.py`): etapa
`normalize_<fonte>` por fonte escreve na tabela silver da entidade
registrada no `ENTITY_NORMALIZER_REGISTRY` (hoje `sanctions`); os wrappers
Airflow dessa fórmula vivem em `src/capiba/pipeline/entity_tasks.py`.
`lake_maintenance.py` (semanal: expire_snapshots + optimize via Trino)
permanece uma DAG imperativa. Transformações nomeadas ficam em
`src/capiba/transformations/` (um módulo por transformação, expondo
`transform(records, **params)`).
Post steps (`dbt_run`, `detect`) são **pulados em runs de backfill**
(`task_post_step` levanta `AirflowSkipException` quando `run_type ==
"backfill"` — reprocessam as tabelas inteiras, O(n²) e pesado em memória);
após um backfill, dispare uma run regular para reconstruir marts e sinais.
Alertas best-effort por e-mail (`src/capiba/notification/alerts.py`, wrapper
síncrono do `NotificationDispatcher` async) disparam do `task_detect`
(sinais ≥ `NOTIFICATION_ALERT_SCORE`, default 0.7) e do
`task_validate_pipeline` (relatório inválido ou erro de normalização > 5%);
no-op quando `NOTIFICATION_RECIPIENTS` está vazio, nunca derrubam a task.
O `task_detect` também emite o sinal de grafo `collusion_network`
(`detect_collusion` sobre o ArangoDB, best-effort) com limiar
`DETECTION_COLLUSION_MIN_WINS` (default 3, placeholder de calibração validado
pela bateria D-02; PR-D-03 calibrará em volume real) e score binário 1.0;
a cadeia de titularidade é exposta na API em `GET /v1/graph/ownership/{cnpj}`.
O `task_validate_pipeline` também alimenta o `QualityMonitor`
(`record_batch`, best-effort) e o `NotificationScheduler` (relatórios
periódicos com as métricas reais do monitor) é iniciado no lifespan da API
somente quando `NOTIFICATION_RECIPIENTS` está configurado.
A vertical `evidence` é exposta pela API no router `/v1/evidence`
(upload multipart, listagem por contrato, download por SHA-256; storage
MinIO instanciado sob demanda via `get_storage()`). O `task_detect` também
grava, best-effort, os pacotes de evidência reproduzíveis por sinal (O9,
`src/capiba/evidence/packages.py`): um pacote de lote por run (linhas
silver + `source_rows_sha256` + janela + versão) e um manifesto por sinal
com a chave do O10 e `batch_sha256`, servidos por
`GET /v1/signals/{key}/evidence`; `reproduce_signal` reexecuta
`detect_fraud_signals` sobre as linhas do pacote para conferir o score.
A triagem editorial de sinais (O10) vive em `src/capiba/db/triage.py`
(coleção ArangoDB `signal_reviews`, chave estável
`{entity_type}:{entity_id}:{signal_type}`; `pending_review` →
`confirmed`/`rejected`/`published`, revisor obrigatório, motivo no
descarte, `published` terminal): o `task_detect` registra sinais novos
como `pending_review` (best-effort) e a API expõe `/v1/triage`
(listagem, transição e relatório de precisão por operador — rótulos
para o ML supervisionado). A interface humana é a página `/triage` do
portal (fila por estado, formulários de transição com revisor da sessão
SSO ou do campo, banner de erro no lugar de páginas 4xx; CSS
compartilhado em `api/static/portal.css`).
Projeto dbt em `dbt/` (profile `capiba`, dbt-trino sobre o catálogo gold;
marts Iceberg no bucket capiba-gold).
O lake usa tabelas Iceberg (via `src/capiba/pipeline/lake.py` + pyiceberg)
catalogadas pelo Lakekeeper; offline, aponte `ICEBERG_CATALOG_URI` para
`sqlite:///...` com `ICEBERG_LOCAL_WAREHOUSE`. Testes que exigem
ArangoDB (ou outra infra externa) são marcados `@pytest.mark.integration` e pulados por
padrão; ative com `CAPIBA_INTEGRATION=1`. Testes de regime/calibração
(baterias de detecção, ex.: `tests/test_detect_battery.py`) são marcados
`@pytest.mark.slow` e também pulados por padrão; ative com `CAPIBA_SLOW=1`
(o CI e `make test-cov`/`make test-slow` habilitam). Testes BDD (Gherkin, pytest-bdd)
ficam em `tests/bdd/` — features em `tests/bdd/features/*.feature`.

Experimentos de detecção (novos sinais, calibração de limiares) seguem
doutrina de pré-registro (adaptada do programa Tanajura): predição numérica
falsificável com critérios de sucesso **e de refutação** em
`docs/preregistrations/PR-D-*.md` antes de qualquer execução, configuração
declarativa em `experiments/detect/*.json` (seeds inclusas), resultados —
inclusive negativos — publicados em `docs/results/R-D-*.md`. Detalhes em
`docs/preregistrations/README.md`.

Governança de dados (mapeamento DAMA-DMBOK para os componentes, papéis de
data steward/custodian, régua regulatória LGPD/LAI e visão de federação):
`docs/governanca.md`.

Jornalismo de dados a serviço da comunidade (processo editorial — obter,
compreender, verificar, documentar, analisar, confirmar, publicar —
mapeado para os componentes da plataforma): `docs/jornalismo_dados.md`.
O backlog de evolução orientado a essa missão (itens dimensionados por
sessão, com critério de aceitação) está em `docs/oportunidades.md`.

Acesso às UIs sem port-forward: ingress Traefik (DaemonSet, hostPorts
8088/8443 — a porta 80 é do Apache do host) em
`https://<serviço>.capiba.local:8443` (api, grafana, marquez, iceberg,
minio, s3, trino, airflow); o `scripts/setup.sh` mapeia os hosts no
`/etc/hosts` e verifica as ferramentas de cluster (docker + grupo docker,
kubectl, helm). O certificado é self-signed (wildcard `*.capiba.local`,
gerado por `scripts/gen-certs.sh` no secret `capiba-tls`) — o browser pede
exceção de segurança. HTTP na 8088 segue respondendo (sem redirect, pois
o redirect de entrypoint não carrega a porta externa). CI em
`.github/workflows/ci.yml` (ruff, mypy, pytest com piso de cobertura de
85% — também aplicado pelo hook `pytest-cov` do pre-commit).

SSO: Keycloak (chart, realm `capiba`) é o IdP OIDC de todas as UIs — portal
capiba-dashboard na API (`/`, `src/capiba/api/portal.py`), Grafana, Airflow
(FAB OAuth), MinIO Console, Lakekeeper UI e Headlamp. Usuário dev:
`capiba`/`capiba-sso` (`keycloak.devUser`), ressincronizado a cada
`make helm-upgrade` pelo hook `templates/keycloak/job-sync-user.yaml` (o
`--import-realm` só cria o realm no primeiro boot). O issuer é HTTPS em
`https://keycloak.capiba.local:8443` (os pods confiam no cert self-signed via
CA `capiba-tls` montada pela chart como `SSL_CERT_FILE`);
um rewrite de CoreDNS (`scripts/cluster.sh`, passo 4) resolve esse host para
o ClusterIP do Traefik (pinado na instalação e reutilizado depois — o
clusterIP é imutável), e backchannels de máquina usam o
DNS de serviço `capiba-keycloak:8080`. Clientes de máquina do lake (Trino,
pyiceberg, `init_buckets.py`) usam o client `capiba-services`
(client_credentials). Fallbacks locais: MinIO root, Grafana admin
(`grafana.auth`) e token do Headlamp (`make dashboard-token`).

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
O hook consulta `rtk rewrite` — a fonte única de reescrita do próprio RTK —
e bloqueia o comando original com a forma RTK como sugestão; o agente
reemite o comando já reescrito. É fail-open: sem `rtk` na máquina ou sem o
arquivo no projeto, nada é bloqueado.

O Kimi só registra hooks no `~/.kimi/config.toml` global (não há config de
projeto para hooks). Em cada máquina, basta uma entrada apontando para o
caminho relativo — o working directory do hook é o projeto da sessão, então
ela vale para qualquer checkout que tenha o arquivo:

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "/usr/bin/python3 .kimi/hooks/rtk-rewrite.py"
timeout = 10
```

### Command Rewriting Rules

Comandos com equivalente RTK (bloqueados pelo hook até serem reemitidos na
forma reescrita). Reduzem o consumo de tokens em 60-90% nos comandos de dev
mais comuns:

| Original | Rewritten | Reduction |
|---|---|---|
| `ls` | `rtk ls` | ~70% |
| `tree` | `rtk ls` | ~70% |
| `cat <file>` | `rtk read <file>` | ~80% |
| `head <file>` | `rtk read <file>` | ~80% |
| `tail <file>` | `rtk read <file>` | ~80% |
| `grep <pattern>` | `rtk grep <pattern>` | ~75% |
| `rg <pattern>` | `rtk grep <pattern>` | ~75% |
| `git status` | `rtk git status` | ~85% |
| `git log` | `rtk git log` | ~90% |
| `git diff` | `rtk git diff` | ~80% |
| `git add` | `rtk git add` | ~95% |
| `git commit` | `rtk git commit` | ~95% |
| `git push` | `rtk git push` | ~95% |
| `pytest` | `rtk pytest` | ~90% |
| `ruff check` | `rtk ruff check` | ~80% |
| `docker ps` | `rtk docker ps` | ~70% |
| `docker logs` | `rtk docker logs` | ~80% |

### Explicit RTK Commands

When Kimi built-in tools (`Read`, `Grep`, `Glob`) are used, the bash hook does not
apply. Use these explicit RTK commands instead:

```bash
rtk read <file>              # Smart file reading
rtk read <file> -l aggressive # Signatures only (strips bodies)
rtk smart <file>             # 2-line heuristic code summary
rtk grep <pattern> <path>    # Grouped search results
rtk find <pattern> <path>    # Compact find results
rtk diff <file1> <file2>     # Condensed diff
```

### Analytics

```bash
rtk gain              # Summary stats
rtk gain --graph      # ASCII graph (last 30 days)
rtk gain --history    # Recent command history
rtk gain --daily      # Day-by-day breakdown
rtk discover          # Find missed savings opportunities
```

### Configuration

`~/.config/rtk/config.toml`:

```toml
[hooks]
exclude_commands = []

[tee]
enabled = true
mode = "failures"
```

### Notes

- RTK only intercepts bash tool calls. Kimi built-in tools bypass the hook.
- For compact output from built-in tools, use shell commands or explicit `rtk` calls.
- RTK ships no tokenizer. Token estimates are `bytes / 4`.
- Percentages are reductions in bash output, not reductions in total bill.
