#

<img src="logo.svg" alt="Capiba">

**C**ruzamento e **A**nálise de **P**adrões e **I**ndícios em **B**ases **A**bertas.

Motor de detecção de farsa institucional via dados abertos, a serviço do jornalismo de dados comunitário.

## Objetivo

Transformar canais de dados públicos em motores de detecção de corrupção e farsa em quaisquer instituições da sociedade civil. Coordenação via internet entre instituições públicas, privadas ou de caráter misto, para abertura e compartilhamento de dados com finalidade cooperativa na produção de investigações, denúncias e monitoramentos de interesse comum.

O produto final é **jornalismo de dados a serviço da comunidade**: coleta, verificação, cruzamento e análise reproduzíveis de bases abertas, publicados como investigações de interesse público que o cidadão consegue entender. A plataforma organiza a evidência; a narrativa e a apuração são trabalho editorial. O método completo está em `docs/jornalismo_dados.md`.

No limite, o projeto constrói **comunidades de dados**: empresas, clientes e instituições públicas compartilhando dados para formar **inteligência soberana em território nacional**, uma alternativa própria e comunitária às plataformas estrangeiras de imperialismo de dados e hipervigilância global.

## Stack

A plataforma fala Python 3.13 e nasce de um trabalho agêntico: o Kimi Code CLI escreve o código, o RTK comprime a conversa e o Zed é o editor onde tudo se assenta.

O coração é um lago. O MinIO guarda os dados brutos e as evidências; o Apache Iceberg dá forma de tabela aos Parquets, com o Lakekeeper como catálogo; o Trino pergunta em SQL e o dbt (dbt-trino) transforma as respostas em marts gold. Ao lado do lago, o ArangoDB tece o grafo de relações entre empresas e pessoas, e o Redis segura o cache do monitor de qualidade e dos hot paths da API, desaparecendo sem drama quando desabilitado.

Quem mantém a casa em ordem: o Airflow orquestra os pipelines, o Marquez cataloga a linhagem via OpenLineage, o Grafana dá rosto aos números, e Prometheus e Kepler vigiam o consumo e a energia do cluster. A FastAPI serve os sinais de risco e o portal capiba-dashboard; o Keycloak (realm `capiba`) é a porta de entrada única, um login para todas as UIs. Tudo isso mora num k3s local com dashboard Headlamp e ingress Traefik; uma imagem de API com PyTorch GPU (`Dockerfile.gpu`, `make build-gpu`) está disponível para quando houver workload que a justifique.

O PostgreSQL tem dois papéis. É o metastore do Airflow, do Grafana, do Keycloak, do Lakekeeper e do Marquez; e é também o DWH de serving da aplicação, o database `dwh` para onde o dbt materializa os modelos de `dbt/models/serving/` através do catálogo Trino `dwh`, cópias dos marts gold para consumo de baixa latência fora do lago.

## Setup

```bash
./scripts/setup.sh
```

Após o deploy (`scripts/helm-upgrade.sh`), as UIs ficam acessíveis via ingress em `https://<serviço>.capiba.local:8443` (api, grafana, marquez, iceberg, minio, s3, trino, airflow); o `setup.sh` mapeia os hosts no `/etc/hosts`. Todas as credenciais e segredos do ambiente local (SSO, bancos, MinIO, clientes OIDC, chaves de sessão) são lidos do `.env` pelo `scripts/helm-upgrade.sh`. O login é unificado via SSO em `https://keycloak.capiba.local:8443`, com o usuário dev `capiba`/`capiba-sso` por padrão, configurável no `.env`. O certificado é self-signed (`scripts/gen-certs.sh`, secret `capiba-tls`), então o browser pede exceção de segurança; HTTP na porta 8088 continua disponível como alternativa. Clientes de linha de comando (dbt, pyiceberg) usam `make port-forward`.

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

O código mora em `src/capiba/`, cortado em verticais que espelham a vida de um dado na plataforma. A `ingestion` sai ao mundo e busca os dados públicos; a `quality` confere e registra a linhagem do que chega; a `detection` procura padrões com estatística, ML, grafos e NLP e emite os sinais `sanctioned_supplier`, `sanctioned_name_match`, `political_connection`, `anomalous_geography` e `collusion_network`; a `evidence` guarda as provas; a `db` conversa com o ArangoDB; a `api` devolve os sinais de risco em REST; a `notification` grita quando algo importa; o `pipeline` amarra as tarefas de ingestão. O `config.py` lê a configuração do ambiente.

Os pipelines de ingestão são declarativos: specs YAML em `dags/pipelines/` que o `pipeline_factory.py` transforma em DAGs do Airflow. São elas `daily_pncp` (contratos do PNCP, diário), `monthly_transparency` (contratos do Portal da Transparência, mensal), `daily_pncp_updates` (atualizações de contratos do PNCP, bronze-only), `monthly_federal_revenue` (dump CNPJ da Receita), `weekly_sanctions` (sanções CEIS/CNEP/CEAF do Portal da Transparência), `hourly_pod_usage` (uso de CPU e memória dos pods), `daily_querido_diario` (diários oficiais de Recife, via Querido Diário/OKBR) e `monthly_tse` (prestação de contas eleitorais do TSE). Duas DAGs seguem imperativas: a `lake_maintenance.py`, manutenção das tabelas Iceberg via Trino, e a `gold_detection.py`, rebuild diário dos marts gold e detecção de sinais sobre todo o silver. Os marts gold vivem no projeto dbt em `dbt/`, o chart Helm da stack completa em `charts/capiba/`, os testes por vertical slice em `tests/`, os utilitários de linha de comando em `scripts/` e a documentação em `docs/`.

Os sinais passam por triagem editorial (`/v1/triage`, página `/triage` do portal) antes da publicação, que dispara alertas por e-mail aos assinantes do município (`/v1/subscriptions`). Uma API pública sem autenticação (`/v1/public/marts`) serve os marts liberados por uma allowlist LGPD fail-closed, exportados em CSV e Parquet versionados para o bucket `capiba-public` pelo post step `export_public_marts` da `gold_detection`.

## Documentação

A documentação vive em `docs/` e conta a plataforma de vários ângulos: o método editorial em `jornalismo_dados.md`, o backlog de evolução em `oportunidades.md`, a arquitetura do sistema em `arquitetura.md`, o catálogo de operadores de detecção em `operadores.md`, a especificação da API de sinais em `api.md`, o pipeline de ingestão em `ingestao.md`, a análise das APIs de fontes externas em `apis_fontes.md`, a governança de dados em `governanca.md` e as lacunas conhecidas em `gaps.md`. Os experimentos de detecção seguem doutrina de pré-registro em `docs/preregistrations/` e publicam seus resultados, inclusive os negativos, em `docs/results/`.

## Licença

AGPL-3.0 + Cláusula Adicional de Disponibilização Pública

Toda reprodução do Capiba deve ter seu código-fonte completo
disponibilizado ao público na internet, sem omissões, nem distorções.

Veja [LICENSE.md](LICENSE.md) para texto completo.
