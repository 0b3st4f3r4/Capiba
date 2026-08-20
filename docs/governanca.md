# Governança de Dados

Este documento descreve como a plataforma Capiba operacionaliza governança
de dados, usando o **DAMA-DMBOK** (Data Management Body of Knowledge) como
régua de referência. O objetivo não é burocratizar: cada área de
conhecimento do DMBOK é mapeada para componentes concretos que já existem
no código, no chart ou nos documentos do repositório, e as lacunas são
ditas explicitamente.

O contexto importa: a missão de longo prazo do Capiba é construir
**comunidades de dados**, empresas, clientes e instituições públicas
compartilhando dados para formar inteligência soberana em território
nacional (ver README.md, "Objetivo"), e a prática que dá sentido a esse
compartilhamento é o **jornalismo de dados a serviço da comunidade** (o
processo editorial completo está em `docs/jornalismo_dados.md`).
Governança, aqui, é a disciplina que torna esse compartilhamento possível
sem perder soberania: contratos declarativos, catálogo, linhagem e
qualidade mensurável são o que permite a uma instituição contribuir dados
com a garantia de saber o que entrou, de onde veio, como foi transformado
e quem consome.

## Mapa DMBOK → plataforma

| Área de conhecimento (DMBOK)     | Componentes no Capiba                                                                 |
| -------------------------------- | ------------------------------------------------------------------------------------- |
| Data Quality                     | `quality/validators.py`, `quality/profiling.py`, `quality/monitor.py`, mart `data_quality_daily`, ruleset `contract_rules` |
| Metadata Management              | Marquez/OpenLineage (inlets/outlets das DAGs), catálogo dbt docs (`make dbt-docs`)    |
| Data Storage & Operations        | Apache Iceberg + Lakekeeper, layout medallion no MinIO, DAG `lake_maintenance`, backups |
| Data Security                    | Keycloak SSO (realm `capiba`), usuários com escopo no MinIO, secrets do chart         |
| Data Integration & Interoperability | Framework declarativo de ingestão (`dags/pipelines/*.yaml` + factory + runner)     |
| Data Warehousing / BI            | Marts gold (dbt-trino), modelos de serving no PostgreSQL DWH, dashboards Grafana      |
| Data Governance                  | Este documento, pré-registros de experimentos (`docs/preregistrations/`), resultados (`docs/results/`) |

As seções abaixo detalham cada mapeamento.

## Data Quality

A qualidade de dados tem quatro camadas, todas com artefatos auditáveis.
A primeira é a validação de schema e regras de negócio:
`src/capiba/quality/validators.py` define regras nomeadas com severidade,
e o ruleset `contract_rules` é aplicado a todo pipeline que declare
`validate:` no YAML; as falhas entram no relatório de run (camada gold) e
nas métricas de passo (`platform_metrics`). A segunda é o profiling
estatístico: `src/capiba/quality/profiling.py` gera perfis por coluna
(nulos, min/max, quartis, IQR), base para detectar drift de distribuição
entre janelas. A terceira é o monitor contínuo:
`src/capiba/quality/monitor.py` mantém baselines no Redis e degrada
graciosamente sem ele, enquanto o histórico permanece no lake. A quarta
fecha o ciclo com um humano no laço: o mart
`dbt/models/marts/data_quality_daily.sql` consolida os indicadores por
dia, consumível via Trino/Grafana e revisto por uma pessoa (ver "Papéis e
operação").

## Metadata Management

A linhagem operacional nasce de graça com a orquestração: o Airflow emite
eventos OpenLineage para o Marquez, e a DAG factory
(`dags/pipeline_factory.py`) deriva inlets e outlets diretamente da spec
YAML (fonte pública, buckets Iceberg, grafo, marts), de modo que a
linhagem acompanha a declaração, sem anotação manual paralela. O catálogo
de transformações fica com o projeto dbt (`dbt/`), que documenta modelos,
colunas e testes; `make dbt-docs` gera e serve o catálogo navegável dos
marts gold e de serving.

Lacuna conhecida: não há glossário de negócio formal nem classificação de
sensibilidade por coluna, itens naturais de roadmap para a fase de
federação.

## Data Storage & Operations

O storage segue o layout medallion no MinIO
(`capiba-bronze`/`capiba-silver`/`capiba-gold`), com tabelas Iceberg
(Parquet) catalogadas pelo Lakekeeper, um warehouse por bucket, tudo
provisionado de forma idempotente por `make init-buckets`. A manutenção
do lake é semanal: a DAG `lake_maintenance` executa `expire_snapshots`
(retenção de 7 dias) e `optimize` (compactação) via Trino, os dois knobs
clássicos de storage operations do Iceberg. Os backups rodam num CronJob
`capiba-backup`, com `pg_dump` e `arangodump` diários para o bucket
`capiba-backups`, e os PVs `hostPath` com `Retain` preservam os dados no
host. Por fim, a cópia de auditoria: todo payload bruto fica em
`<fonte>/dt=YYYY-MM-DD/` no bronze antes de qualquer transformação, de
modo que a origem é sempre reconstruível.

## Data Security

O acesso humano passa por uma porta só: o Keycloak (realm `capiba`) é o
IdP OIDC de todas as UIs (portal da API, Grafana, Airflow, MinIO Console,
Lakekeeper UI, Headlamp). Os clientes de máquina (Trino, pyiceberg,
`init_buckets.py`) usam o client `capiba-services` (client_credentials),
separado das credenciais humanas. No object storage vale o menor
privilégio: o MinIO tem usuários com escopo e políticas por bucket
(`minio.scopedUsers`, criados pelo job `job-users.yaml`), o Trino acessa
apenas os buckets do lake, a conexão de logs remotos do Airflow apenas o
bucket de logs, e as credenciais root ficam restritas a admin e
bootstrap. As credenciais vivem em Secrets do chart (injetadas do `.env`
local em dev), nunca em código, e os `.env` reais não entram no git.

## Data Integration & Interoperability

A integração é o ponto mais formalizado da plataforma: pipelines são
**declarativos**. Uma spec YAML em `dags/pipelines/*.yaml` descreve
fontes, janela temporal, fórmula, validações, transformações, destinos e
post steps, sem nenhuma linha de código Python para declarar um pipeline.
O modelo pydantic (`src/capiba/pipeline/spec.py`) valida a spec no parse,
os registries (`src/capiba/pipeline/registry.py`) mapeiam nomes a
implementações e o runner (`src/capiba/pipeline/runner.py`) executa com
métricas por passo.

Isso é governança aplicada à integração: o *contrato* de uma fonte nova é
um artefato versionado, revisável em code review e validável antes de
qualquer execução, e não uma DAG Python que só falha em runtime.
Detalhes em `docs/ingestao.md`.

## Data Warehousing / BI

Os marts gold (Iceberg, via dbt-trino sobre o catálogo `gold`) são
`contracts_daily`, `contracts_by_agency`, `supplier_stats`,
`contract_amendments`, `amendments_by_agency`, `amendments_by_supplier`,
`contract_red_flags`, `red_flags_by_agency`, `red_flags_by_supplier`,
`political_connections`, `data_quality_daily`, `pod_usage_hourly` e
`platform_cost_daily`. Ao lado dos marts, a tabela gold `fraud_signals`
(Iceberg, escrita diretamente pelo `task_detect`, fora do dbt) guarda os
sinais de detecção. Acima deles, os modelos de serving em `dbt/models/serving/`
(`serving_supplier_stats`, `serving_municipality_daily`) são
materializados no PostgreSQL DWH (database `dwh`) através do catálogo
Trino `dwh`, uma camada de baixa latência para consumo direto, separada
do lake analítico. O consumo acontece no Grafana, com datasource Trino
provisionado e dashboards como código em `charts/capiba/dashboards/`. O
subconjunto liberado para consumo público segue uma allowlist
fail-closed (`src/capiba/pipeline/public_export.py`): é exportado em
CSV + Parquet para o bucket `capiba-public` e servido pela API pública
(`/v1/public/marts`, sem auth), e um mart novo sem classificação LGPD
falha o guarda.

## Federação: preparando as comunidades de dados

Nenhum protocolo federado (privacidade diferencial, federated learning,
zero-knowledge) está implementado hoje; são visão de longo prazo
(`docs/arquitetura.md`, "Protocolos de compartilhamento"). Mas o desenho
atual já carrega os pré-requisitos de governança para federar sem abrir
mão da soberania. As fontes são declarativas: uma instituição parceira
ingressa declarando uma spec YAML e registrando uma fonte, e o contrato
de entrada é explícito e versionado, com janela, validações e destinos
declarados. A linhagem vem por construção: inlets e outlets derivados da
própria spec alimentam o Marquez, e a procedência de cada dado é
rastreável ponta a ponta (API pública, bronze, silver, gold, grafo e
serving). Os contratos de dados são pydantic: o schema unificado
`Contract` e as specs validadas no load fazem da fronteira entre "meu
dado" e "dado da comunidade" um contrato executável, não uma convenção
verbal. E o catálogo é aberto: dbt docs e Marquez dão a cada participante
visão do que existe e de como é usado, sem abrir os dados brutos.

A régua para admitir uma fonte federada futura: spec YAML aprovada em
revisão, regras de qualidade declaradas, classificação LGPD explícita
(ver abaixo) e responsável nomeado (data steward da fonte).

## Régua regulatória

**LGPD (Lei 13.709/2018).** As fontes atuais são bases públicas de
contratações, mas contêm dados pessoais (CNPJs de MEI e nomes de sócios
no dump da Receita, por exemplo). A base legal é a execução de políticas
públicas e o legítimo interesse em controle da administração: o
tratamento recai sobre dados já tornados públicos pelo próprio poder
público, para finalidade compatível (transparência e controle social). A
minimização é estrutural: o schema `Contract` carrega apenas os campos
necessários à detecção, o bronze guarda o payload bruto para auditoria e
os marts e o serving expõem agregações. A classificação por artefato é
explícita: a allowlist do export público (`PUBLIC_MARTS` e
`EXCLUDED_MARTS` em `src/capiba/pipeline/public_export.py`) registra a
justificativa de cada mart — `political_connections` entra porque
mascara CPF na origem, `contract_red_flags` fica de fora porque
`supplier_id` pode ser CPF completo. As assinaturas de alerta por
município (`src/capiba/db/subscriptions.py`) tratam o e-mail como dado
pessoal mínimo: a coleção guarda e-mail, município e status, e apenas o
hash sha256 do token de confirmação/cancelamento. O ponto de atenção é
o roadmap: se fontes privadas entrarem, cada uma exige análise de base
legal própria antes de a spec ser aprovada.

**LAI (Lei 12.527/2011).** O Capiba opera *sobre* o que a LAI e os
portais de transparência já publicam; não há coleta de dados
classificados. A plataforma é, em si, um instrumento de efetividade da
transparência ativa.

**Dados abertos governamentais.** As fontes seguem os termos de uso dos
portais (PNCP, Portal da Transparência, Receita Federal): uso
não-comercial de dados públicos, com atribuição. A cópia de auditoria no
bronze preserva o dado como publicado, permitindo contestação e
verificação independente.

## Papéis e operação

Governança sem dono não existe. Os papéis abaixo são leves; num time
pequeno, uma pessoa acumula mais de um, mas as responsabilidades são
fixas.

O **data steward (por fonte)** declara e mantém a spec YAML da sua fonte
(`dags/pipelines/*.yaml`), revisa o mart `data_quality_daily`
correspondente e responde por classificação LGPD e termos de uso;
mudança de spec passa por code review como qualquer código. O **data
custodian (plataforma)** mantém registries, runner, lake e chart,
executa `lake_maintenance`, backups e o rebalanceamento de recursos, e
aprova novos destinos e fórmulas (mudanças no `registry.py`/`runner.py`).
O **revisor de experimentos** aprova pré-registros em
`docs/preregistrations/PR-D-*.md` antes de qualquer bateria de detecção
e exige publicação de resultados, inclusive negativos, em
`docs/results/R-D-*.md` (ver `docs/preregistrations/README.md`). O
**revisor de triagem** confirma, rejeita ou publica sinais na coleção
`signal_reviews` (`src/capiba/db/triage.py`, transições
`pending_review` → `confirmed`/`rejected`/`published`, revisor
obrigatório), apoiado pelos pacotes de evidência reproduzíveis
(`src/capiba/evidence/`); publicar é o gatilho dos alertas por
município. O **mantenedor do projeto** homologa o conjunto (ver
"Processo de desenvolvimento" no AGENTS.md); nenhum push é automático.

Os artefatos operacionalizam os papéis: a spec YAML é a ata do steward,
o pré-registro é a ata do experimento e o `data_quality_daily` é a pauta
de revisão recorrente.

## Métricas de otimização e custo

A plataforma mede a si mesma, dogfooding do próprio framework. A tabela
gold `platform_metrics` guarda o histórico de saúde dos pipelines: o
runner publica, por passo de cada run, duração, linhas de entrada e
saída e erros (`lake.write_platform_metrics`, best-effort). O pipeline
declarativo `hourly_pod_usage` (fonte `pod_usage`, metrics-server
in-cluster) coleta o uso real de CPU e memória por pod, que o mart
`pod_usage_hourly` agrega por hora. O mart `platform_cost_daily` cruza
esse uso medido com os requests declarados na seed
`dbt/seeds/requests.csv` (regerar quando o `values.yaml` mudar) e estima
o custo diário da plataforma. Como complemento de infra, o Prometheus
(kubelet/cAdvisor) e o Kepler (estimativa de energia por pod) alimentam
os dashboards `capiba-infra` e `capiba-energia` no Grafana.

Esses dados são o insumo do auto-ajuste: os requests e limits atuais
(~4 Gi de requests, ~10 Gi de limits) foram calibrados pelo uso medido.
Em versões futuras, o mesmo laço (medir, agregar, cruzar com o
declarado, ajustar a seed e o values) pode ser automatizado, com o
`platform_cost_daily` como função objetivo e a revisão humana como gate.

## Referências

A régua conceitual é o *DAMA-DMBOK: Data Management Body of Knowledge*
(2ª edição, DAMA International). Dentro do repositório, este documento
conversa com `docs/jornalismo_dados.md` (o processo editorial de
jornalismo de dados sobre a plataforma), `docs/arquitetura.md`
(camadas, stack e deploy), `docs/ingestao.md` (o framework declarativo
de ingestão), `docs/preregistrations/README.md` (a doutrina de
pré-registro de experimentos) e `charts/capiba/README.md` (componentes
do chart, recursos e SSO).
