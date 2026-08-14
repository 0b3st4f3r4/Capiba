# Capiba Helm Chart

Chart Helm para deploy do Capiba em Kubernetes.

## Requisitos

- Kubernetes 1.28+
- Helm 3.13+
- k3s nativo no host (ver `make cluster-start`), minikube ou cluster similar
- Recursos mínimos recomendados: CPU AMD Ryzen 7 7000 Series, GPU NVIDIA GeForce RTX 4050 6 GB, 32 GB de Memória RAM DDR5, Swap de 8 GB em NVMe Drive (Link PCIe 4.0 x4).

## Instalação

```bash
helm upgrade --install capiba ./charts/capiba \
  --namespace capiba \
  --create-namespace
```

## Acesso aos serviços

Após a instalação, use `kubectl port-forward` para acessar os serviços:

```bash
# API Capiba
kubectl port-forward svc/capiba-api 8000:8000 -n capiba

# Grafana
kubectl port-forward svc/capiba-grafana 3000:3000 -n capiba

# Keycloak (SSO)
kubectl port-forward svc/capiba-keycloak 8182:8080 -n capiba

# MinIO Console
kubectl port-forward svc/capiba-minio 9001:9001 -n capiba

# ArangoDB Web UI
kubectl port-forward svc/capiba-arangodb 8529:8529 -n capiba
```

## Componentes

| Componente     | Habilitado por padrão | Descrição                          |
| -------------- | --------------------- | ---------------------------------- |
| api            | Sim                   | API FastAPI do Capiba                     |
| postgresql     | Sim                   | Banco relacional (metastores + DW `dwh` dos modelos de serving) |
| arangodb       | Sim                   | Banco multi-modelo (grafos, docs, vetores, full-text) |
| redis          | Sim                   | Cache do monitor de qualidade e dos hot paths da API (tudo degrada graciosamente sem ele) |
| minio          | Sim                   | Object storage (data lake)                |
| icebergCatalog | Sim                   | Catálogo Iceberg REST (Lakekeeper) sobre PostgreSQL |
| keycloak       | Sim                   | SSO (OIDC) para todas as UIs              |
| grafana        | Sim                   | Visualização e dashboards                 |
| prometheus     | Sim                   | Métricas de infra (kubelet/cAdvisor do k3s + Kepler); TSDB em emptyDir, retenção 7d (dev) |
| kepler         | Sim                   | Estimativa de energia por pod (DaemonSet eBPF, métricas na porta 28282) |
| airflow        | Não                   | Orquestração de pipelines                 |
| marquez        | Sim                   | Catálogo de dados / lineage (Marquez/OpenLineage) |
| trino          | Sim                   | SQL sobre o lake (Iceberg REST); fonte do Grafana |

> Os componentes legados `neo4j`, `qdrant` e `elasticsearch` foram removidos do
> chart e suas pastas de dados em `data/` foram excluídas. O ArangoDB assume as
> funções de grafo, documentos, vetores e full-text. O DataHub também foi
> substituído: pesado demais para um cluster local (Kafka + OpenSearch + GMS);
> o Marquez cobre lineage com um único serviço sobre o PostgreSQL do chart.

## Marquez (catálogo de dados / lineage)

O chart inclui o [Marquez](https://marquezproject.ai/) — implementação de
referência do OpenLineage — como catálogo de dados, via templates próprios
(sem subcharts externos). Um único deployment com dois containers no mesmo
pod:

- **API** (`capiba-marquez`, portas 5000/5001) — recebe eventos OpenLineage
- **Web** (porta 3000) — UI, proxy de `/api/v1` para a API em localhost

O metastore usa o **PostgreSQL do próprio chart** (database `marquez`, criado
por um init container idempotente) — nenhum banco adicional, nem Kafka ou
OpenSearch.

O Airflow emite lineage automaticamente: o provider
`apache-airflow-providers-openlineage` (instalado na imagem do Airflow) envia
os eventos das tasks para `OPENLINEAGE_URL` (configurado no configmap do
Airflow apontando para o Marquez).

A UI fica disponível em `http://localhost:3001` via `scripts/port-forward.sh`
(porta local 3001 para não colidir com o Grafana). Para desabilitá-lo:

```bash
helm upgrade --install capiba ./charts/capiba \
  --namespace capiba \
  --set marquez.enabled=false
```

## Trino (SQL sobre o lake)

O chart inclui um [Trino](https://trino.io) single-node (`capiba-trino`,
porta 8080) com um catálogo Iceberg por warehouse do Lakekeeper
(`bronze`, `silver`, `gold` — ver `templates/trino/configmap.yaml`) e o
catálogo `dwh` (connector PostgreSQL), usado pelo dbt para materializar os
modelos de serving (`dbt/models/serving/`) no database `dwh` do PostgreSQL
do chart — criado, com o usuário `dwh`, pelo job hook idempotente
`postgres/job-dwh.yaml` (`postgresql.dwh` no values). O acesso
ao MinIO usa credenciais estáticas injetadas via `${ENV:...}` (o Lakekeeper
também oferece credenciais STS vendidas por tabela).

É a fonte de dados do Grafana para dashboards sobre os marts gold — o
datasource Trino (catálogo `gold`) é provisionado declarativamente pelo
configmap do Grafana (`templates/grafana/`), sem configuração manual. O
login é via SSO (Keycloak); o admin local (`grafana.auth`) segue como
fallback.

UI do Trino em `http://localhost:8081` via `scripts/port-forward.sh`.

## Configuração

Edite `values.yaml` ou use `--set` para customizar:

```bash
helm upgrade --install capiba ./charts/capiba \
  --namespace capiba \
  --set global.storageClass=local-path \
  --set airflow.enabled=true
```

> Na prática, use `scripts/helm-upgrade.sh`, que injeta `global.dataPath`,
> `global.servicesDataPath` e `global.transparencyApiKey` via `--set` a partir
> do `.env` local.

## Persistência

A persistência se divide em duas pastas na raiz do projeto:

- **`data/`** é a raiz de armazenamento do MinIO: cada subdiretório de
  primeiro nível é um bucket. Nada além de buckets do MinIO pode viver em
  `data/`.
- **`services/`** abriga os serviços com estado que não podem rodar
  sobre object storage: **ArangoDB e PostgreSQL** (e Redis, habilitado por
  padrão),
  todos com `PersistentVolumes` do tipo `hostPath` gerenciados pelo chart
  apontando para os respectivos subdiretórios. As pastas `metabase/`,
  `airflow/` e `local-path-storage/` que porventura existam ali são resíduos
  de layouts antigos e não são mais usadas.

O PostgreSQL **não usa mais** o StorageClass `local-path`: assim como o
ArangoDB, passou a usar um PV `hostPath` gerenciado pelo chart em
`services/postgresql`.

## Acesso via ingress (sem port-forwards)

Com `ingress.enabled=true` (padrão), o chart cria Ingresses para o
controlador Traefik (instalado como DaemonSet pelo `make cluster-start`,
hostPorts 8088/8443 — a porta 80 do host pertence ao Apache). Mapeados no
`/etc/hosts` pelo `scripts/setup.sh`, os serviços ficam acessíveis em
`https://<serviço>.capiba.local:8443` (`api`, `grafana`, `marquez`,
`iceberg`, `minio`, `s3`, `trino`, `airflow`; o Keycloak fica em HTTP plano
`http://keycloak.capiba.local:8088` — ver "SSO" abaixo). Com `ingress.tls.enabled`
(padrão), o TLS usa o secret `capiba-tls` (cert self-signed wildcard
gerado por `scripts/gen-certs.sh` — o browser pede exceção). HTTP na 8088
segue respondendo, sem redirect automático. Os port-forwards
(`make port-forward`) continuam disponíveis para clientes de linha de
comando (pyiceberg, dbt) que esperam portas fixas.

## SSO (Keycloak)

O chart inclui um Keycloak (`capiba-keycloak`) como IdP OIDC, com o realm
`capiba` importado de `templates/keycloak/realm-configmap.yaml`. Portal da
API (`/`), Grafana, Airflow, MinIO Console, Lakekeeper UI, Headlamp e a
UI do Trino compartilham o mesmo login; o usuário dev é `capiba`/`capiba-sso`
(`keycloak.devUser`) por padrão. Todas as credenciais e segredos do ambiente
local — Keycloak (admin master, banco, client secrets, usuário dev), bancos
PostgreSQL, MinIO, Lakekeeper, Grafana, Airflow, Marquez, Trino e o portal
Capiba — são lidos do `.env` pelo `scripts/helm-upgrade.sh` e injetados no
chart via `--set`. Como o `--import-realm` só cria o realm no primeiro boot
(estratégia IGNORE_EXISTING), o usuário dev é ressincronizado a cada upgrade
pelo hook `templates/keycloak/job-sync-user.yaml` — mudanças de
`KEYCLOAK_DEV_USERNAME/PASSWORD/EMAIL` no `.env` passam a valer após
`make helm-upgrade`. Demais mudanças de realm (clients, scopes) exigem banco
novo ou edição manual no admin console. Clientes de máquina (Trino, pyiceberg,
`scripts/init_buckets.py`) autenticam com o client `capiba-services`
(client_credentials, `keycloak.clientSecrets`).

O issuer OIDC é **HTTP plano** em `http://keycloak.capiba.local:8088` (o
Traefik expõe as hostPorts 8088/8443 também no Service, com ClusterIP fixo
— pinado na instalação e reutilizado nos upgrades, já que o clusterIP é
imutável), porque os pods não confiam no cert self-signed do ingress. Um
rewrite de CoreDNS (`coredns-custom`, aplicado pelo `scripts/cluster.sh`)
resolve `keycloak.capiba.local` para esse ClusterIP dentro do cluster;
backchannels de máquina usam o DNS de serviço `capiba-keycloak:8080`.

Fallbacks locais (sem SSO): MinIO root (`minio.auth`), Grafana admin
(`grafana.auth`) e token do Headlamp (`make dashboard-token`).

### Buckets do MinIO (`data/`)

```
data/
├── capiba-artifacts/     # código (src/), DAGs e projeto dbt publicados
├── capiba-bronze/        # payloads brutos (<fonte>/dt=YYYY-MM-DD/),
│                         # tabelas Iceberg raw_<fonte> e evidências
│                         # (evidence/<formato>/<origem>/)
├── capiba-silver/        # tabela Iceberg capiba.contracts (normalizada)
├── capiba-gold/          # relatórios de run (JSON.gz) e marts Iceberg (dbt)
├── capiba-airflow-logs/  # logs remotos das tasks do Airflow
└── capiba-backups/       # dumps pg_dump/arangodump (dt=YYYY-MM-DD/)
```

As tabelas Iceberg (Parquet) são catalogadas pelo **Lakekeeper**
(`capiba-iceberg-catalog`, porta 8181), um warehouse por bucket
(`bronze`/`silver`/`gold`), com metadados num database dedicado do
PostgreSQL do chart (criado por init container idempotente).

O layout completo de buckets e os warehouses Iceberg são criados de forma
idempotente por `make init-buckets` (script `scripts/init_buckets.py`).

### Cluster local (k3s nativo)

O caminho principal é um **k3s nativo no host** (sem VM), instalado por
`make cluster-start` — o script instala/inicia o k3s, sobe o controlador
Traefik, deploya o chart Capiba e o dashboard Headlamp
(http://localhost:4466, token via `make dashboard-token`). Como os PVs são
`hostPath` apontando para `data/` e `services/` no host, os dados (MinIO,
PostgreSQL, ArangoDB) são preservados. Para parar o cluster use
`make cluster-stop`; para remover completamente (destrutivo) use
`make cluster-remove`.

> **Legado — Rancher Desktop:** se ainda estiver em uso, os volumes
> `hostPath` exigem mount **9p** (o padrão *reverse-sshfs* corrompe o
> `initdb` do PostgreSQL). Ajuste em `~/.config/rancher-desktop/settings.json`:
> `{ "virtualMachine": { "mount": { "type": "9p" } } }` e reinicie com
> `rdctl shutdown && rdctl start`.

### Caminhos base

Os caminhos base não ficam hardcoded no chart: `global.dataPath` (raiz do
MinIO, `data/`) e `global.servicesDataPath` (`services/`) têm default
vazio e são injetados por `scripts/helm-upgrade.sh` via `--set` — o primeiro
lê `DATA_PATH` do `.env` (com fallback para `<raiz do projeto>/data`) e o
segundo é sempre `<raiz do projeto>/services`. Para instalar manualmente:

```bash
helm upgrade --install capiba ./charts/capiba \
  --namespace capiba \
  --set global.dataPath="/caminho/absoluto/do/projeto/data" \
  --set global.servicesDataPath="/caminho/absoluto/do/projeto/services"
```

### Benefícios

- Dados persistem após reinicialização de pods, cluster ou máquina.
- Buckets do MinIO e bancos ficam acessíveis diretamente no host para
  inspeção e backup.
- PVCs possuem `helm.sh/resource-policy: keep`, então **não são removidos** por
  `helm uninstall`.
- PVs `hostPath` usam `persistentVolumeReclaimPolicy: Retain`, preservando os
  arquivos no host mesmo após exclusão do PVC.

### Backups

O CronJob `capiba-backup` (`backup.enabled`, `backup.schedule` com default
`0 3 * * *`) executa `pg_dump` (bancos do Airflow, Keycloak, Lakekeeper e
Marquez) e `arangodump` (database `capiba`) e envia os dumps para o bucket
`capiba-backups` em `dt=YYYY-MM-DD/{postgresql,arangodb}/`.

### Atenção

Ao migrar para outro computador via FSX, compacte as pastas `data/` e
`services/` inteiras e descompacte no destino com os mesmos caminhos
absolutos definidos em `global.dataPath` e `global.servicesDataPath`. Mesmo
com os volumes em `hostPath`, para PostgreSQL ainda é recomendável exportar e
reimportar o banco como garantia adicional:

```bash
# Exportar no cluster de origem
kubectl exec -i svc/capiba-postgresql -n capiba -- pg_dumpall -U postgres > capiba-postgresql.sql

# Reimportar no cluster de destino (após instalar o chart)
kubectl exec -i svc/capiba-postgresql -n capiba -- psql -U postgres < capiba-postgresql.sql
```

## Recursos

Os valores padrão são calibrados pelo uso real medido do cluster local (~4 Gi de requests, ~10 Gi de limits no pior caso), de forma que toda a stack caiba em máquina junto do SO + k3s. Ajuste `resources` conforme necessário.

A medição contínua é feita pelo próprio chart: o Prometheus (`prometheus.enabled`) scrapeia kubelet/cAdvisor do k3s e o Kepler (`kepler.enabled`, estimativa de energia por pod), e os dashboards como código em `charts/capiba/dashboards/` aparecem no Grafana — `capiba-infra` (CPU/memória por pod), `capiba-energia` (Watts por pod), `capiba-ingestao` (pipelines) e `capiba-custos` (uso real × requests, via Trino sobre os marts `pod_usage_hourly`/`platform_cost_daily`). Use-os para recalibrar `resources` ao adicionar componentes.
