# Operação — deploy e runbook do cluster local

> **Propósito:** runbook completo de deploy e operação do ambiente local
> (cluster k3s + chart Capiba + SSO) para operadores humanos.
> **Quando consultar:** subir/parar/remover o cluster, acessar as UIs,
> configurar SSO, verificar backups e observabilidade, diagnosticar pods.
> **Relacionados:** `docs/operacao_lake.md` (idempotência, retry, memória e
> baseline de recursos do lake), `charts/capiba/README.md` (referência do
> chart), `README.md` (visão geral).
> **Sincronizado com:** `charts/capiba` + `scripts/cluster.sh` — 2026-08-21.

## Ciclo de vida do cluster k3s

O ambiente roda em **k3s nativo no host** (sem VM), gerenciado por
`scripts/cluster.sh` via alvos do Makefile:

- `make cluster-start` — cria/inicia o cluster, o Traefik, o chart Capiba e o
  Headlamp. **Idempotente**: reexecutar só aplica o que falta.
- `make cluster-stop` — para o serviço k3s (`systemctl stop k3s`). Dados
  preservados.
- `make cluster-remove` — **destrutivo**: para e desinstala o k3s
  (`k3s-uninstall.sh`), apaga kubeconfigs. Os dados `hostPath` em `data/` e
  `services/` ficam intactos.
- `make cluster-status` — lista os pods do namespace `capiba`.

### O que o `cluster.sh start` faz, passo a passo

1. **k3s** — instala via `get.k3s.io` se ausente (pinar a release com
   `INSTALL_K3S_VERSION`, ex. `v1.36.3+k3s1`, quando o canal estiver fora —
   sem ele o instalador cai num release "stable" inexistente e dá 404) e sobe
   o serviço (`systemctl enable --now k3s`).
2. **kubeconfig** — torna `/etc/rancher/k3s/k3s.yaml` legível, copia para
   `~/.kube/config` (contexto `default`); um kubeconfig prévio é salvo em
   `~/.kube/config.bak-pre-capiba`.
3. **Traefik** — `helm upgrade --install` do chart oficial como **DaemonSet**
   (um pod por nó, `maxSurge=0`), com hostPorts **8088 (web) e 8443
   (websecure)** — a porta 80 do host pertence ao Apache. O Service é
   `ClusterIP` com IP **pinado** (reusa o existente; default `10.43.0.50`),
   porque o clusterIP é imutável e os passos seguintes dependem dele.
   Atenção: desde o chart traefik-41.x a chave é `service.spec.type` — a
   antiga `service.type` é ignorada e vira `LoadBalancer`, fazendo o svclb do
   k3s ocupar as hostPorts e bloquear o DaemonSet.
4. **CoreDNS rewrite** — aplica o ConfigMap `coredns-custom` (`capiba.server`)
   mapeando `keycloak.capiba.local` e `s3.capiba.local` para o ClusterIP
   pinado do Traefik e reinicia o CoreDNS. No host, esses nomes resolvem para
   `127.0.0.1` (`/etc/hosts`); dentro do cluster, os pods alcançam o mesmo
   issuer/endereço pelo Traefik.
5. **Imagens** — o chart usa imagens locais `capiba/api` e `capiba/airflow`
   (tag = `version` do `pyproject.toml`, `pullPolicy: IfNotPresent`), mas o
   k3s tem containerd próprio: o script compila no Docker o que faltar
   (caindo para `sudo docker` fora do grupo) e importa no k3s com
   `k3s ctr images import`.
6. **Chart Capiba** — cria o namespace, roda `scripts/gen-certs.sh` e
   `scripts/helm-upgrade.sh` (que injeta `global.dataPath`,
   `global.servicesDataPath`, `global.transparencyApiKey` e todos os segredos
   do `.env` via `--set`), e espera os deployments ficarem disponíveis.
7. **Headlamp** — instala o dashboard no namespace `headlamp` com OIDC
   (client público `headlamp`, PKCE, issuer
   `https://keycloak.capiba.local:8443/realms/capiba`), cria a ServiceAccount
   `headlamp-admin` com `cluster-admin` e o secret de token (fallback de
   login: `make dashboard-token`).

### Certificado TLS wildcard

`scripts/gen-certs.sh` gera um cert **self-signed wildcard** para
`*.capiba.local` (SAN `*.capiba.local` + `capiba.local`, válido por 825 dias)
e o grava como secret `capiba-tls` no namespace `capiba`. Idempotente: pula
se o secret já existir. Como é self-signed, o browser pede exceção no
primeiro acesso. Em deploys reais, substitua por cert de CA (mkcert ou
similar).

## Acesso às UIs

### Ingress (caminho principal, sem port-forward)

Com `ingress.enabled=true` (padrão), o chart cria Ingresses para o Traefik.
`scripts/setup.sh` mapeia os hosts no `/etc/hosts` (→ `127.0.0.1`):

- `https://api.capiba.local:8443/` — portal + API
- `https://grafana.capiba.local:8443` — dashboards
- `https://marquez.capiba.local:8443` — lineage
- `https://iceberg.capiba.local:8443` — Lakekeeper UI
- `https://minio.capiba.local:8443` — MinIO Console
- `https://s3.capiba.local:8443` — endpoint S3 (usado também pelos storage
  profiles do Lakekeeper, dentro e fora do cluster)
- `https://trino.capiba.local:8443` — UI do Trino
- `https://airflow.capiba.local:8443` — UI do Airflow
- Keycloak: issuer HTTPS `https://keycloak.capiba.local:8443` (ver SSO abaixo)

O **HTTP na 8088 segue respondendo, sem redirect** para HTTPS. O browser
pedirá exceção de certificado (self-signed).

### Port-forwards (clientes de linha de comando)

`make port-forward` (`scripts/port-forward.sh [start|stop|status]`) sobe os
forwards em background (logs em `/tmp/capiba-port-forward-*.log`). Clientes
de máquina que esperam portas fixas continuam dependendo deles:

| Serviço | Porta local |
| --- | --- |
| API | 8000 |
| Grafana | 3000 |
| ArangoDB | 8529 |
| MinIO Console / S3 | 9001 / 9000 |
| Marquez | 3001 (3000 no pod, desviado do Grafana) |
| Trino | 8081 |
| Lakekeeper | 8181 |
| Airflow | 8080 |
| Keycloak | 8182 |
| Headlamp | 4466 (namespace `headlamp`) |

pyiceberg, dbt e `scripts/init_buckets.py` (com `make init-buckets`) rodam do
host contra essas portas; `make dbt-run`/`make dbt-test` também exigem os
port-forwards ativos.

## SSO (Keycloak)

O Keycloak (`capiba-keycloak`) é o IdP OIDC de todas as UIs, com o realm
`capiba` importado de `templates/keycloak/realm-configmap.yaml`.

- **Clients do realm**: `capiba-dashboard` (portal da API, `/`,
  `api/portal.py`), `grafana`, `airflow` (FAB OAuth), `minio` (Console),
  `lakekeeper` (UI), `headlamp` (público, PKCE), `trino` (UI web; o hook de
  sincronização garante o mapper de audience `audience-trino`, sem o qual o
  login morre no callback com "JWT aud claim rejected") e `capiba-services`
  (máquina, `client_credentials` — usado por Trino, pyiceberg e
  `scripts/init_buckets.py`; segredos em `keycloak.clientSecrets`).
- **Usuário dev**: `capiba` / `capiba-sso` (`keycloak.devUser`, do `.env` via
  `KEYCLOAK_DEV_USERNAME/PASSWORD/EMAIL`). Como o `--import-realm` só cria o
  realm no primeiro boot (IGNORE_EXISTING), o hook
  `templates/keycloak/job-sync-user.yaml` (post-install/post-upgrade)
  **ressincroniza o usuário a cada `make helm-upgrade`**: cria/atualiza,
  reseta a senha, desativa o required action `VERIFY_PROFILE`, habilita
  `unmanagedAttributePolicy=ENABLED` (necessário para o atributo `policy` —
  claim `consoleAdmin` do MinIO), concede `realm-management/realm-admin` e
  falha o upgrade se o atributo ou a role não persistirem. Com `realm-admin`,
  o console do realm abre com o usuário dev em
  `https://keycloak.capiba.local:8443/admin/capiba/console`. Demais mudanças
  de realm (clients, scopes) exigem banco novo ou edição manual no console.
- **Console master** (`/admin/master/console`): restrito ao admin bootstrap
  (`keycloak.admin`, secret `capiba-keycloak-secrets`).
- **Issuer**: HTTPS em `https://keycloak.capiba.local:8443`. Os pods confiam
  no cert self-signed porque a CA do secret `capiba-tls` é montada como
  `SSL_CERT_FILE`; o rewrite de CoreDNS (passo 4 do `cluster.sh`) resolve o
  host para o ClusterIP pinado do Traefik. Backchannels de máquina entre
  pods usam o DNS de serviço `capiba-keycloak:8080`.
- **Fallbacks locais (sem SSO)**: MinIO root (`minio.auth`), Grafana admin
  (`grafana.auth`) e token do Headlamp (`make dashboard-token`).

## Backup

O CronJob `capiba-backup` (`templates/backup/cronjob.yaml`; `backup.enabled`,
`backup.schedule` default `0 3 * * *`, `concurrencyPolicy: Forbid`) roda:

1. init container `pg-dump` — `pg_dump` dos bancos do Airflow, Lakekeeper,
   Keycloak e Marquez (os habilitados) para um `emptyDir`;
2. init container `arango-dump` — `arangodump --overwrite true` do database
   `capiba`;
3. container `upload` — `mc mirror` para o bucket **`capiba-backups`** em
   `dt=YYYY-MM-DD/{postgresql,arangodb}/`.

Histórico de jobs: 3 com sucesso, 3 com falha. Verificação:

```bash
kubectl get cronjob capiba-backup -n capiba
kubectl logs -n capiba job/$(kubectl get jobs -n capiba -o name | grep backup | tail -1 | cut -d/ -f2) -c upload
```

Para migrar de máquina, compacte `data/` e `services/` inteiras e
descompacte com os mesmos caminhos absolutos (`global.dataPath` /
`global.servicesDataPath`); para o PostgreSQL, prefira exportar/reimportar
com `pg_dumpall` via `kubectl exec` (procedimento no
`charts/capiba/README.md`, seção "Atenção").

## Observabilidade

- **Prometheus** (`prometheus.enabled`, padrão on) — scrapeia kubelet/cAdvisor
  do k3s e o Kepler; TSDB em `emptyDir`, retenção de 7 dias (dev).
- **Kepler** (`kepler.enabled`, padrão on) — DaemonSet eBPF com estimativa de
  energia por pod (métricas na porta 28282).
- **Dashboards como código** em `charts/capiba/dashboards/*.json`,
  provisionados no Grafana: `capiba-infra` (CPU/memória por pod),
  `capiba-energia` (Watts por pod), `capiba-ingestao` (pipelines) e
  `capiba-custos` (uso real × requests, via Trino sobre os marts
  `pod_usage_hourly`/`platform_cost_daily` — alimentados pelo pipeline
  `hourly_pod_usage`). Use-os para recalibrar `resources` no `values.yaml` ao
  adicionar componentes.
- **Marquez** (`http://localhost:3001` ou ingress) — lineage OpenLineage; o
  Airflow emite eventos automaticamente via `OPENLINEAGE_URL`.

## Troubleshooting

- **OOMKills / pressão de memória** — os defaults de `resources` são
  calibrados pelo uso real medido (~4 Gi de requests, ~10 Gi de limits no
  pior caso), para a stack inteira caber na máquina junto do SO + k3s. O
  detalhe de idempotência, retry e a **baseline de recursos** (incluindo os
  OOMKills conhecidos de tasks pesadas) está em `docs/operacao_lake.md`.
  Antes de aumentar limits às cegas, consulte o dashboard `capiba-infra` e o
  histórico lá.
- **Pods com restarts** — `trino`, `iceberg-catalog` e `keycloak` são os que
  historicamente acumulam restarts. Observe:
  - `kubectl get pods -n capiba` (coluna RESTARTS) e
    `kubectl describe pod <pod> -n capiba` (último estado: OOMKilled? exit
    code?).
  - `keycloak`: o deployment usa strategy `Recreate` e o boot é lento; hooks
    post-upgrade (`job-sync-user`) retentam o login admin por até ~3 min —
    restarts durante upgrade são esperados, mas verifique o job
    (`kubectl logs -n capiba job/capiba-keycloak-sync-dev-user`) se o SSO
    parar após `make helm-upgrade`.
  - `iceberg-catalog` (Lakekeeper): depende do PostgreSQL; falhas de boot em
    cascata após restart do Postgres são comuns — confira
    `kubectl logs -n capiba deploy/capiba-iceberg-catalog`.
  - `trino`: consultas pesadas sobre o lake pressionam memória; OOMKilled aqui
    costuma indicar query grande demais ou limit apertado — cruze com
    `docs/operacao_lake.md` antes de recalibrar.
- **svclb segurando as portas 8088/8443** — sintoma de Traefik instalado como
  `LoadBalancer` (chave `service.type` antiga ignorada pelo chart 41.x);
  corrija com `service.spec.type=ClusterIP` e ClusterIP pinado (passo 3 do
  `cluster.sh`).
- **Browser recusa o certificado** — esperado: self-signed. Aceite a exceção
  ou troque o secret `capiba-tls` por um cert de CA confiável.
