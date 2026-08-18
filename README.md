# Capiba

**C**ruzamento e **A**nálise de **P**adrões e **I**ndícios em **B**ases **A**bertas.

Motor de captura de farsa institucional via dados abertos, a serviço do jornalismo de dados comunitário.

## Objetivo

Transformar canais de dados públicos em motores de detecção de corrupção e farsa em quaisquer instituições da sociedade civil. Coordenação via internet entre instituições públicas, privadas ou de caráter misto, para abertura e compartilhamento de dados com finalidade cooperativa na produção de investigações, denúncias e monitoramentos de interesse comum.

O produto final é **jornalismo de dados a serviço da comunidade**: coleta, verificação, cruzamento e análise reproduzíveis de bases abertas, publicados como investigações de interesse público que o cidadão consegue entender — a plataforma organiza a evidência, a narrativa e a apuração são trabalho editorial. O método completo está em `docs/jornalismo_dados.md`.

No limite, o projeto constrói **comunidades de dados**: empresas, clientes e instituições públicas contribuem com o compartilhamento de dados para formar **inteligência soberana em território nacional** — uma alternativa própria e comunitária às plataformas estrangeiras de imperialismo de dados e hipervigilância global.

## Stack

- **Agente:** Kimi Code CLI
- **Compressão:** RTK (rtk-ai/rtk)
- **Editor:** Zed
- **Linguagem:** Python 3.13+
- **Orquestração:** Apache Airflow
- **Storage:** ArangoDB (multi-modelo), MinIO (data lake e evidências); Redis opcional (cache do monitor de qualidade, desabilitado por padrão)
- **Lakehouse:** Apache Iceberg (Parquet no MinIO) com catálogo REST Lakekeeper; marts gold via dbt (dbt-trino)
- **Query engine:** Trino (SQL sobre o lake; fonte de dados do Grafana)
- **Catálogo de dados:** Marquez (lineage via OpenLineage) + dbt docs
- **ML:** scikit-learn + spaCy
- **API:** FastAPI (inclui o portal capiba-dashboard, login SSO)
- **SSO:** Keycloak (realm `capiba`; um login para todas as UIs)
- **Visualização:** Grafana (datasource Trino provisionado)
- **Cluster local:** k3s nativo (GPU NVIDIA schedulável), dashboard Headlamp, ingress Traefik

PostgreSQL existe no cluster apenas como metastore (Airflow, Grafana,
Keycloak, Lakekeeper e Marquez), não para a aplicação.

## Setup

```bash
./scripts/setup.sh
```

Após o deploy (`scripts/helm-upgrade.sh`), as UIs ficam acessíveis via
ingress em `https://<serviço>.capiba.local:8443` (api, grafana, marquez,
iceberg, minio, trino, airflow) — o `setup.sh` mapeia os hosts no
`/etc/hosts`. Todas as credenciais e segredos do ambiente local (SSO,
bancos, MinIO, clientes OIDC, chaves de sessão) são lidos do `.env` pelo
`scripts/helm-upgrade.sh`. O login é unificado via SSO (Keycloak, realm
`capiba`, em `http://keycloak.capiba.local:8088`; usuário dev
`capiba`/`capiba-sso` por padrão, configurável em `.env`). O certificado é
self-signed (`scripts/gen-certs.sh`, secret `capiba-tls`), então o browser
pede exceção de segurança; HTTP na porta 8088 continua disponível como
alternativa. Clientes de linha de comando (dbt, pyiceberg) usam
`make port-forward`.

## Desenvolvimento agêntico

```bash
# Iniciar sessão
kimi

# Verificar economia de tokens
rtk gain

# Compactar contexto quando pesado
/compact
```

## Estrutura

Código em `src/capiba/`, organizado por vertical:

- `src/capiba/ingestion/` — crawlers e normalização de dados públicos
- `src/capiba/detection/` — operadores estatísticos, ML, grafos, NLP
- `src/capiba/quality/` — validação e linhagem de dados
- `src/capiba/evidence/` — armazenamento de evidências
- `src/capiba/db/` — acesso ao ArangoDB
- `src/capiba/api/` — interface REST para sinais de risco (FastAPI)
- `src/capiba/notification/` — despacho de alertas
- `src/capiba/pipeline/` — tarefas do pipeline de ingestão
- `src/capiba/config.py` — configuração via variáveis de ambiente
- `dags/` — DAGs do Airflow: `daily_ingestion.py` (crawls → silver → marts
  gold → sinais de fraude), `monthly_federal_revenue.py` (dump CNPJ da
  Receita) e `lake_maintenance.py` (manutenção Iceberg via Trino)
- `dbt/` — marts gold (dbt-trino sobre o catálogo Iceberg)
- `charts/capiba/` — chart Helm da stack completa
- `tests/` — testes por vertical slice
- `scripts/` — utilitários CLI
- `docs/` — documentação de arquitetura e operadores

## Documentação

- `docs/jornalismo_dados.md` — método de jornalismo de dados sobre a plataforma
- `docs/oportunidades.md` — backlog de evolução orientado a jornalismo comunitário
- `docs/arquitetura.md` — arquitetura do sistema
- `docs/operadores.md` — catálogo de operadores de detecção
- `docs/api.md` — especificação da API de sinais
- `docs/ingestao.md` — pipeline de ingestão
- `docs/apis_fontes.md` — análise das APIs de fontes externas

## Licença

AGPL-3.0 + Cláusula Adicional de Disponibilização Pública

Toda reprodução do Capiba deve ter seu código-fonte completo
disponibilizado ao público na internet, sem omissões, nem distorções.

Veja [LICENSE.md](LICENSE.md) para texto completo.
