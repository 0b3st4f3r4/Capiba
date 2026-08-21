# Arquitetura do Capiba

> **Propósito:** visão de alto nível da plataforma — camadas, stack técnica, deploy no k3s, SSO e persistência.
> **Quando consultar:** para entender onde cada componente mora e como se conecta antes de mexer em infra, pipeline ou API.
> **Relacionados:** `docs/apis_fontes.md`, `docs/ingestao.md`, `docs/governanca.md`.
> **Sincronizado com:** `AGENTS.md` (seção Stack), `charts/capiba/values.yaml` e specs em `dags/pipelines/` — 2026-08-21.

## Visão geral

Capiba é um motor de detecção de farsa institucional que transforma
canais de dados públicos em sinais de corrupção, a serviço do
**jornalismo de dados comunitário**: os sinais existem para embasar
investigações de interesse público, não como fim em si mesmos. O processo
editorial completo (obter, compreender, verificar, documentar, analisar,
confirmar e publicar) está mapeado para os componentes da plataforma em
`docs/jornalismo_dados.md`.

No limite, o objetivo é construir **comunidades de dados**: empresas,
clientes e instituições públicas compartilham dados para produzir
**inteligência soberana em território nacional**, uma alternativa
comunitária às plataformas estrangeiras de imperialismo de dados e
hipervigilância global. Os protocolos de compartilhamento federado
(privacidade diferencial, federated learning, zero-knowledge proofs) da
seção de roadmap sustentam essa visão: contribuir sem abrir mão da
soberania sobre os próprios dados.

## Camadas

```mermaid
flowchart TB
    Camada_Coordenacao["Camada de Coordenação<br/>protocolos abertos, APIs, webhooks"]

    subgraph Ecossistema_Capiba["Ecossistema Capiba"]
        direction TB

        subgraph Fontes["Fontes de Dados"]
            direction LR
            A["Dados públicos<br/>licitações<br/>Portal Transparência / PNCP"]
            B["Dados públicos<br/>receitas<br/>Receita Federal"]
            C["Dados públicos<br/>eleitorais<br/>TSE<br/>[roadmap]"]
            D["Dados privados<br/>cadastros, CNPJ<br/>via LGPD / DP<br/>[roadmap]"]
        end

        Motor["Motor Capiba<br/>engenharia de dados"]

        subgraph Consumidores["Consumidores"]
            direction LR
            E["Auditoria<br/>TCU"]
            F["Ministério Público<br/>ação"]
            G["Sociedade<br/>visão"]
        end
    end

    Camada_Coordenacao --> Ecossistema_Capiba
    Fontes --> Motor
    Motor --> Consumidores
```

Fontes com pipeline hoje: PNCP (pipelines diários `daily_pncp` e
`daily_pncp_updates`, este bronze-only) e Portal da Transparência (pipeline
mensal `monthly_transparency`), e
Receita Federal (pipeline mensal `monthly_federal_revenue`, que baixa o dump
CNPJ para o bronze e, quando os arquivos Empresas/Estabelecimentos/Socios
estão habilitados via `FEDERAL_REVENUE_FILES`, normaliza em streaming para
as tabelas silver `companies`/`establishments`/`partners` e carrega o grafo
ArangoDB com vértices FtM `companies`/`persons` e arestas
`ownership`/`directorship`). As
listas de sanções CEIS/CNEP/CEAF do Portal da Transparência entram pelo pipeline
semanal `weekly_sanctions` (fórmula `entities_collect`, tabela silver
`sanctions`). O TSE entra pelo pipeline mensal `monthly_tse` (fórmula
`file_dump`, tabelas silver `campaign_donations` e `candidacies`) a partir
de uma âncora congelada no bronze (`tse/reference/`, upload manual — o CDN
do TSE bloqueia clientes CLI) e os diários oficiais de Recife pelo diário
`daily_querido_diario` (fórmula `documents_collect`, via Querido
Diário/OKBR). Completam o quadro a telemetria interna `hourly_pod_usage`
(fórmula `metrics_collect`, uso de CPU/memória dos pods) e a sonda-piloto
`pilot_pncp_terms` (fórmula `terms_collect`, sem schedule — disparo manual
sobre coorte por parâmetros). Dados privados via LGPD/DP seguem visão de
longo prazo, sem implementação.

## Protocolos de compartilhamento (roadmap)

A tabela abaixo descreve a visão de compartilhamento federado; nenhum desses
protocolos está implementado no código atual.

| Protocolo             | Função                                     |
| --------------------- | ------------------------------------------ |
| Differential privacy  | Agregados ruidosos, não dados brutos       |
| Federated learning    | Modelos treinados localmente               |
| Zero-knowledge proofs | Prova de compliance sem revelar identidade |
| APIs abertas          | Consumo de sinais por qualquer instituição |

A governança de dados que prepara a plataforma para essa federação (mapeamento
DAMA-DMBOK, papéis, régua regulatória LGPD/LAI) está documentada em
`docs/governanca.md`.

## Stack técnica

A casa se mantém em ordem pelo Apache Airflow, que orquestra pipelines de
ingestão declarativos: specs YAML em `dags/pipelines/*.yaml`, uma DAG gerada
por spec pelo factory `dags/pipeline_factory.py` (detalhes em
`docs/ingestao.md`). Às specs soma-se a DAG imperativa `gold_detection.py`
(diária, 08:00 UTC): rebuild dos marts via `dbt_run`, `detect` sobre todo o
silver e post step `export_public_marts` — é a run final a disparar após um
backfill. O storage mora no ArangoDB (multi-modelo, banco de grafos e
documentos da aplicação) e no MinIO, com o Redis segurando o cache do monitor de
qualidade e dos hot paths da API: habilitado por padrão, e tudo degrada
graciosamente quando ele sai de cena. O ML é scikit-learn com spaCy, a API
é FastAPI — e serve também o portal editorial capiba-dashboard (`/`, com a
página de triagem `/triage`, protegidos pelo SSO) — e a visualização é
Grafana, com SSO via Keycloak e datasource Trino provisionado.

O coração é um lago Apache Iceberg, tabelas Parquet no MinIO com catálogo
REST Lakekeeper sobre PostgreSQL. O Trino pergunta em SQL sobre o catálogo
Iceberg REST (um catálogo por warehouse: bronze/silver/gold) e é a fonte de
dados do Grafana para os marts; expõe ainda o catálogo `dwh` (connector
PostgreSQL), usado pelo dbt para materializar os modelos de serving no DW.
As transformações silver→gold são dbt (dbt-trino, executadas pelo Trino
sobre o catálogo gold), e a linhagem é catalogada pelo Marquez (lineage via
OpenLineage, metastore no PostgreSQL do chart), com o dbt docs documentando
os marts gold.

O PostgreSQL do chart é o único banco relacional e tem dois papéis:
metastore do Airflow, do Grafana, do Keycloak, do Lakekeeper e do Marquez,
e também DW complementar (database `dwh`, criado por job hook idempotente),
que recebe os modelos de serving de `dbt/models/serving/`
(`serving_supplier_stats`, `serving_municipality_daily`) como camada de
baixa latência para consumo direto, separada do lake analítico.

Quem vigia a casa: o Prometheus (retenção 7d, TSDB em emptyDir, perfil dev)
scrapeia o kubelet/cAdvisor do k3s e o Kepler (DaemonSet, estimativa de
energia por pod na porta 28282), com dashboards como código em
`charts/capiba/dashboards/` (infra, energia, ingestão e custos) montados no
Grafana via ConfigMap. E a porta de entrada única é o Keycloak (realm
`capiba`): portal da API, Grafana, Airflow, MinIO Console, Lakekeeper UI e
Headlamp compartilham o mesmo login OIDC, enquanto os clientes de máquina
(Trino, pyiceberg) usam o client `capiba-services` (client_credentials).

## Deploy no k3s

O ambiente local roda em um cluster k3s nativo, exposto pelo ingress Traefik
nos hostPorts `8088` (HTTP) e `8443` (HTTPS, certificado self-signed para
`*.capiba.local`). Todos os serviços compartilham o namespace `capiba`.

```mermaid
flowchart TB
    subgraph Host["Host / k3s cluster"]
        subgraph Traefik["Ingress Traefik"]
            http[":8088 HTTP"]
            https[":8443 HTTPS"]
        end

        subgraph capiba["namespace: capiba"]
            api["capiba-api"]
            airflow["capiba-airflow"]
            trino["capiba-trino"]
            minio["capiba-minio"]
            lakekeeper["capiba-iceberg-catalog<br/>Lakekeeper"]
            postgres[("capiba-postgresql")]
            arango[("capiba-arangodb")]
            redis[("capiba-redis")]
            keycloak["capiba-keycloak"]
            grafana["capiba-grafana"]
            marquez["capiba-marquez"]
            prometheus["capiba-prometheus"]
            kepler["capiba-kepler<br/>DaemonSet"]
        end
    end

    usuario(("Usuário / Browser"))
    usuario --> https
    https --> api & grafana & airflow & keycloak & marquez
    api --> arango
    api --> redis
    trino --> lakekeeper --> postgres
    airflow --> minio & postgres
    grafana --> trino
    prometheus --> kepler
    grafana --> prometheus
```

## Fluxo de autenticação SSO

O Keycloak atua como IdP OIDC para o portal da API e para as UIs do cluster.
Como os pods não confiam no certificado self-signed do Traefik na porta `8443`,
o backchannel de metadados usa o issuer interno/plain HTTP
(`KEYCLOAK_ISSUER`), enquanto o navegador redireciona pelo ingress público
HTTPS (`KEYCLOAK_PUBLIC_ISSUER`).

```mermaid
sequenceDiagram
    actor U as Usuário
    participant B as Browser
    participant A as capiba-api
    participant KI as Keycloak<br/>internal HTTP
    participant KP as Keycloak<br/>public HTTPS

    U ->> B: Acessa /
    B ->> A: GET /
    A -->> B: 302 /auth/login
    B ->> A: GET /auth/login
    A ->> KI: fetch OIDC metadata<br/>KEYCLOAK_ISSUER
    KI -->> A: metadata
    A -->> B: redirect para authorize<br/>KEYCLOAK_PUBLIC_ISSUER
    B ->> KP: login + consent
    KP -->> B: authorization code + redirect
    B ->> A: GET /auth/callback?code=...
    A ->> KI: token exchange<br/>KEYCLOAK_ISSUER
    KI -->> A: access_token + id_token
    A -->> B: session cookie + redirect /
```

## Persistência e storage

No host, a persistência se divide em duas pastas na raiz do projeto. A
`data/` é a raiz de armazenamento do MinIO, onde cada subdiretório de
primeiro nível é um bucket; a `services/` abriga os bancos que não rodam
sobre object storage (ArangoDB, PostgreSQL e Redis), via PVs `hostPath`
gerenciados pelo chart.

O MinIO organiza o data lake em buckets no modelo medallion, com tabelas
Iceberg (Parquet) catalogadas pelo Lakekeeper, o REST catalog do cluster,
um warehouse por bucket: `bronze`, `silver` e `gold`. O `capiba-bronze`
guarda os payloads brutos das APIs (`<fonte>/dt=YYYY-MM-DD/`, cópia de
auditoria), as tabelas Iceberg `raw_<fonte>` e os arquivos de evidência
(`evidence/<formato>/<origem>/AAAA/MM/<sha256>.<ext>`, formato ∈
image/document/audio/video/other). O `capiba-silver` abriga a tabela Iceberg
`capiba.contracts` (contratos normalizados, particionada por `dt`) e as
tabelas de entidades `companies`/`establishments`/`partners` (dump CNPJ da
Receita), `sanctions` (listas CEIS/CNEP/CEAF do Portal da Transparência),
`campaign_donations` e `candidacies` (prestação de contas do TSE),
`rfb_municipalities` (de-para TOM→município da Receita) e `municipalities`
(referência geográfica de municípios). O
`capiba-gold` reúne os relatórios por execução
(`reports/<spec>/dt=YYYY-MM-DD/` — ex.: `reports/daily_pncp/`), os marts
Iceberg gerados pelo
dbt (`capiba.contracts_daily`, `capiba.contracts_by_agency`,
`capiba.supplier_stats`, `capiba.data_quality_daily`,
`capiba.pod_usage_hourly`, `capiba.platform_cost_daily`,
`capiba.contract_amendments`, `capiba.amendments_by_agency`,
`capiba.amendments_by_supplier`, `capiba.contract_red_flags`,
`capiba.red_flags_by_agency`, `capiba.red_flags_by_supplier` e
`capiba.political_connections`), a tabela
`capiba.platform_metrics` (métricas por passo de cada run, escritas pelo
runner) e a tabela `capiba.fraud_signals` (sinais nomeados emitidos pelo
post step `detect`: `sanctioned_supplier`, `sanctioned_name_match`,
`political_connection`, `anomalous_geography` e `collusion_network`). O
`detect` e a validação dos pipelines disparam alertas
best-effort por e-mail (`src/capiba/notification/alerts.py`) quando
`NOTIFICATION_RECIPIENTS` está configurado. Os sinais passam por triagem
editorial (`src/capiba/db/triage.py`, rotas `/v1/triage` e página `/triage`
do portal) antes da publicação, que dispara alertas aos assinantes do
município (`src/capiba/db/subscriptions.py`, rotas `/v1/subscriptions`).

Além do lake: `capiba-airflow-logs` recebe os logs remotos das tasks do
Airflow, `capiba-artifacts` recebe o código (`src/`) e as DAGs publicados
por `make publish-artifacts` (sincronizados aos pods do Airflow por init
container e sidecar, sem rebuild de imagem), `capiba-backups` recebe os
dumps diários do CronJob `capiba-backup` (`pg_dump` dos bancos airflow,
keycloak, lakekeeper e marquez, mais `arangodump` do database `capiba`, em
`dt=YYYY-MM-DD/{postgresql,arangodb}/`) e `capiba-public` recebe o export
público dos marts liberados (allowlist LGPD fail-closed em
`src/capiba/pipeline/public_export.py`, CSV e Parquet versionados em
`marts/<mart>/dt=<data>/`), servidos pela API pública sem autenticação
(`/v1/public/marts`).

O layout completo de buckets é criado de forma idempotente por
`make init-buckets`.
