# Análise de APIs de fontes de dados

> **Propósito:** mapear as APIs públicas que alimentam o lago — endpoints, exigências técnicas e estado atual da integração de cada fonte no Capiba.
> **Quando consultar:** antes de adicionar uma fonte nova, ajustar um crawler ou diagnosticar falha de ingestão.
> **Relacionados:** `docs/ingestao.md`, `docs/api.md`, `docs/arquitetura.md`.
> **Sincronizado com:** specs em `dags/pipelines/*.yaml` e módulos de `src/capiba/ingestion/` — 2026-08-21.

Este documento mapeia as portas de entrada do mundo exterior: as APIs públicas de onde saem os contratos, as sanções e os cadastros que alimentam o lago. Para cada fonte, os endpoints úteis, as exigências técnicas e o estado atual da integração no Capiba.

## 1. PNCP (Portal Nacional de Contratações Públicas)

### URL base
```
https://pncp.gov.br/api/consulta
```

### Endpoints de consulta pública relevantes

| Endpoint | O que retorna | Parâmetros obrigatórios |
|----------|---------------|------------------------|
| `GET /v1/contratacoes/publicacao` | Contratações por data de publicação | `dataInicial`, `dataFinal`, `codigoModalidadeContratacao`, `pagina` |
| `GET /v1/contratos` | Contratos/empenhos por data de publicação | `dataInicial`, `dataFinal`, `pagina` |
| `GET /v1/contratos/atualizacao` | Contratos por data de atualização | `dataInicial`, `dataFinal`, `pagina` |
| `GET /v1/atas` | Atas de registro de preço | `dataInicial`, `dataFinal`, `pagina` |

### Requisitos técnicos

A consulta pública não pede autenticação. As datas viajam no formato `yyyyMMdd` (ex.: `20260813`) e a paginação começa em 1, com limite de 50 registros por página para contratações e de 500 para contratos e atas. Não há rate limit documentado oficialmente, mas a boa convivência manda recuar com backoff exponencial. Quando não há dados, a resposta é um honesto `204 No Content`. A estabilidade é baixa: timeout e retry não são opcionais, e o crawler já os implementa.

### Estado no Capiba

O crawler `crawler_pncp.py` bate em `/v1/contratos` paginando até a última página, com retry e backoff exponencial centralizados no helper `fetch_page` de `src/capiba/ingestion/_http.py`, que trata o `204` como página vazia e o `429` como sinal de espera. A execução manual da DAG `daily_pncp` confirmou que o endpoint devolve contratos com fornecedor e valores. Estado atual: a DAG **diária** `daily_pncp` (spec `dags/pipelines/daily_pncp.yaml`, 06:00 UTC, janela `previous_day`, fórmula `contracts_default`, destinos bronze/silver/grafo) está **ativa** em produção. O crawler também cobre `GET /v1/contratos/atualizacao` (`fetch_contract_updates`), consumido pela DAG **bronze-only** `daily_pncp_updates` (06:41 UTC, mesma fórmula e janela, destino só `lake_bronze`) para capturar contratos aditivados após a publicação original — as flags de aditivo do PR-D-05 leem esse bronze; o silver não é tocado. Não há credenciais a configurar.

Os termos contratuais (aditivos) ficam **fora** do grupo `consulta`: `GET /v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos` vive no grupo transacional `pncp` (URL base `https://pncp.gov.br/api/pncp`, config `PNCP_TERMS_API_URL`), é público (verificado ao vivo em 2026-08-21) e lista apenas os termos vigentes — `204` quando o contrato não tem termos. É a fonte autoritativa do plano B de PR-D-05b ("houve aditivo formal?"), consumida por `fetch_contract_terms` com checkpoint por contrato no bronze (`persist_contract_terms`). Estado: **piloto** — a DAG `pilot_pncp_terms` (spec `dags/pipelines/pilot_pncp_terms.yaml`, fórmula `terms_collect`, bronze-only + relatório gold) **não tem schedule**: é uma sonda de disparo manual sobre uma coorte dirigida por parâmetros (`include_flagged` lê os `f_value_amendment = 1` do mart `contract_amendments`; `siafi_codes` restringe ao município-piloto Recife, SIAFI 2531).

## 2. Portal da Transparência (CGU)

### URL base
```
https://api.portaldatransparencia.gov.br/api-de-dados
```

### Endpoints relevantes

| Endpoint | O que retorna | Parâmetros comuns |
|----------|---------------|-------------------|
| `GET /contratos` | Contratos firmados | `dataInicio`, `dataFim`, `pagina`, `codigoOrgao` |
| `GET /contratos/{id}` | Detalhes de um contrato | `id` |
| `GET /licitacoes` | Licitações do Poder Executivo Federal | `dataInicio`, `dataFim`, `pagina`, `codigoOrgao` |
| `GET /licitacoes/{id}` | Detalhes de uma licitação | `id` |
| `GET /despesas/documentos` | Empenhos, liquidações, pagamentos | `dataInicio`, `dataFim`, `pagina` |
| `GET /ceis` | Empresas inidoneas/suspensas | `cnpjSancionado`, `pagina` |
| `GET /cnep` | Empresas punidas | `cnpjSancionado`, `pagina` |
| `GET /ceaf` | Expulsões da administração federal (documento mascarado) | `pagina` |

### Requisitos técnicos

Aqui a porta tem chave: a autenticação é obrigatória, feita por token gratuito obtido no cadastro em `https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email` e enviado no header `chave-api-dados: SEU_TOKEN`; sem token, as requisições são bloqueadas. As datas seguem `DD/MM/AAAA`, a paginação começa em 1 com máximo de 15.000 registros por página, e o rate limit é de 30 requisições por minuto com token. Em horários de pico, a instabilidade aparece.

### Estado no Capiba

O crawler `crawler_transparency.py` envia o header `chave-api-dados` a partir da variável `TRANSPARENCY_API_KEY`, que mora no `.env` e é injetada no chart pelo `scripts/helm-upgrade.sh` (via `--set global.transparencyApiKey`, secret `capiba-secrets`) a cada `make helm-upgrade`; sem ela, o pipeline falha com log claro. Dois pipelines ativos consomem a fonte: a DAG **mensal** `monthly_transparency` (spec `dags/pipelines/monthly_transparency.yaml`, dia 2 às 07:00 UTC, janela `previous_month`, fórmula `contracts_default`, destinos bronze/silver/grafo) coleta os contratos federais; e a DAG **semanal** `weekly_sanctions` (spec `dags/pipelines/weekly_sanctions.yaml`, terças 03:22 UTC, janela `all` — snapshot completo, fórmula `entities_collect`) coleta `GET /ceis`, `GET /cnep` e `GET /ceaf` com `fetch_sanctions`, paginando até a primeira página vazia, grava os payloads brutos nas tabelas bronze `raw_ceis`/`raw_cnep`/`raw_ceaf` (checkpoint por página — o retry retoma da próxima) e os registros normalizados (modelo `Sanction`) na tabela silver `sanctions`.

## 3. Receita Federal / Dados Abertos de CNPJ

### Fontes conhecidas

| Fonte | URL | Observação |
|-------|-----|------------|
| SERPRO+ (Nextcloud) | `https://arquivos.receitafederal.gov.br/index.php/s/YggdBLfdninEJX9/download` | Oficial; requer montagem da URL com `path` e `files`. |
| Dados Abertos RF | `https://dadosabertos.rfb.gov.br/CNPJ/dados_abertos_cnpj/` | Quando disponível; arquivos CSV em ZIP. |

### Arquivos disponíveis (mensal)

| Arquivo | Conteúdo |
|---------|----------|
| `Empresas0.zip` a `Empresas9.zip` | Dados cadastrais das empresas (CNPJ raiz) |
| `Estabelecimentos0.zip` a `Estabelecimentos9.zip` | Dados dos estabelecimentos (CNPJ completo) |
| `Socios0.zip` a `Socios9.zip` | Quadro societário |
| `Simples.zip` | Dados do Simples Nacional |
| `Cnaes.zip` | Tabela de CNAEs |
| `Municipios.zip`, `Naturezas.zip`, `Paises.zip`, `Qualificacoes.zip`, `Motivos.zip` | Tabelas de domínio |

### Requisitos técnicos

Não pede autenticação. Os arquivos são CSV dentro de ZIPs, separados por ponto-e-vírgula, encoding `latin1`, somando vários GB por mês, com atualização mensal.

### Estado no Capiba

O crawler `crawler_federal_revenue.py` baixa os múltiplos ZIPs do compartilhamento SERPRO+ (`download_cnpj_dump`, `extract_cnpj_zip`, `parse_cnpj_csv`). A URL base é configurável via `FEDERAL_REVENUE_BASE_URL` e o mês de referência chega como parâmetro `reference_month` para `download_cnpj_dump`. A integração já é completa: a DAG `monthly_federal_revenue` (spec `dags/pipelines/monthly_federal_revenue.yaml`, fórmula `file_dump`) baixa os ZIPs para o bronze (arquivos no bucket e manifesto na tabela `raw_federal_revenue`), normaliza Empresas/Estabelecimentos/Socios em streaming para as tabelas silver `companies`/`establishments`/`partners` (parser em `src/capiba/ingestion/cnpj.py`, opt-in via `FEDERAL_REVENUE_FILES`) e o `Municipios.zip` para a silver `rfb_municipalities` (de-para código TOM → município, elo da geografia do fornecedor), e carrega vértices e arestas no grafo ArangoDB (destino `arangodb_graph`). Não há credenciais a configurar, mas a URL pode precisar de ajuste se a Receita Federal mudar o compartilhamento — o default de `FEDERAL_REVENUE_BASE_URL` no código usa a forma WebDAV `https://arquivos.receitafederal.gov.br/public.php/dav/files/YggdBLfdninEJX9`, o mesmo compartilhamento SERPRO+ da tabela acima.

## 4. TSE (Tribunal Superior Eleitoral)

### URL base
```
https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas
https://cdn.tse.jus.br/estatistica/sead/odsele/consulta_cand
```

### Arquivos utilizados (snapshot anual)

| Arquivo | Conteúdo |
|---------|----------|
| `prestacao_de_contas_eleitorais_candidatos_<ano>.zip` | Prestação de contas; o `receitas_candidatos_<ano>_BRASIL.csv` traz as doações de campanha |
| `consulta_cand_<ano>.zip` | Candidaturas e situação de totalização (gate do eleito) |

### Requisitos técnicos

Não pede autenticação, mas **o CDN bloqueia clientes CLI** via Akamai Bot Manager (403, confirmado em 2026-08-21) — os dumps só são obtidos via browser, com IP brasileiro. O dump é um snapshot fixo por ciclo eleitoral, republicado ao longo do julgamento das contas — não é indexado por mês; o ano do ciclo é configurável (`params.year` da run ou `TSE_ELECTION_YEAR`, default 2024).

### Estado no Capiba

**Sem download automático.** Os dumps vivem como **âncora congelada** no bronze, em `capiba-bronze/tse/reference/` (upload único e manual, por ano eleitoral; sha256 registrado no R-D-08b). A DAG mensal `monthly_tse` (spec `dags/pipelines/monthly_tse.yaml`, dia 3 às 06:37 UTC, fórmula `file_dump`) resolve a âncora do ano da run (`download_tse_dump` em `crawler_tse.py`) — falha com instruções de upload se a âncora não existir —, copia os ZIPs para a partição da run (`tse/files/dt=<run>/`, manifesto na tabela `raw_tse`) e normaliza em streaming (parser `src/capiba/ingestion/tse.py`) para as silvers `campaign_donations` e `candidacies` — insumo do sinal `political_connection`. O `reference_month` é aceito mas ignorado pelo resolver. Os documentos dos doadores ficam completos no silver; o mascaramento é preocupação do mart gold (LGPD).

## 5. Querido Diário (OKBR)

### URL base

`https://api.queridodiario.ok.org.br` (projeto Querido Diário, Open Knowledge Brasil, MIT — diários oficiais municipais raspados das prefeituras). A raiz `queridodiario.ok.org.br` é só a SPA de busca; a API vive no subdomínio `api.`.

### Endpoints relevantes

| Endpoint | Uso no Capiba |
|----------|---------------|
| `GET /gazettes` | Lista diários por território e janela de publicação (`territory_ids` — array, **não** `territory_id` —, `published_since`/`published_until`, paginação `size`/`offset`, `sort_by=ascending_date`) |

Cada registro traz `territory_id`/`territory_name`/`state_code`, `date`, `edition`, `is_extra_edition`, `scraped_at`, `url` (PDF original) e `txt_url` (texto puro extraído do PDF — sem estrutura markdown, com artefatos de hifenização).

### Requisitos técnicos

Não pede autenticação. A raspagem dos diários roda de madrugada (~03:50 UTC), então a coleta diária é agendada para depois disso.

### Estado no Capiba

O crawler `crawler_querido_diario.py` (`fetch_gazettes`, `download_gazette_text`, `text_file_name`) alimenta a DAG diária `daily_querido_diario` (spec `dags/pipelines/daily_querido_diario.yaml`, 04:41 UTC — após a raspagem noturna —, janela `previous_day`, fórmula `documents_collect`): município-piloto Recife (IBGE 2611606), metadados no bronze (`raw_querido_diario`) + texto extraído de cada diário como arquivo bronze (nome determinístico, skip-existing no retry, falhas de download best-effort), validação declarada `gazette_rules`. O corpus é matéria-prima para os sinais de NLP (`semantic_gap`, `detect_clone`).

## 6. Telemetria da plataforma (pod_usage)

Fonte interna — não é API pública. A fonte `pod_usage` (`src/capiba/ingestion/pod_usage.py`) lê o uso de CPU/memória dos pods do namespace `capiba` pela API do metrics-server no cluster (ou `kubectl top` fora dele, via `fetch_pod_usage`). A DAG **horária** `hourly_pod_usage` (spec `dags/pipelines/hourly_pod_usage.yaml`, minuto 7, fórmula `metrics_collect`) grava o snapshot pontual na tabela bronze `raw_pod_usage` e, como post step, roda o `dbt_run` seletivo dos marts `pod_usage_hourly`/`platform_cost_daily` (requests na seed `dbt/seeds/requests.csv`) — dogfood do framework declarativo e base dos dashboards de custo da plataforma. Telemetria interna: esses marts constam do `EXCLUDED_MARTS` do export público.

## 7. API interna do Capiba

### Estado atual

| Endpoint | O que faz |
|----------|-----------|
| `GET /health` | Health check; retorna `{"status": "ok"}` |
| `GET /v1/signals/{cnpj}` | Consulta o ArangoDB e roda os operadores de detecção |
| `GET /v1/ranking/municipalities` | Agregação AQL por município |
| `GET /v1/graph/ownership/{cnpj}` | Cadeia de titularidade a partir do grafo |
| `GET /v1/graph/partners/{siafi_code}` | Sócios dos fornecedores de um órgão (traversal FtM) |
| `GET /v1/graph/ftm/{cnpj}` | Export do subgrafo da empresa em FtM JSON |
| `POST /v1/evidence` | Upload multipart de evidência |
| `GET /v1/evidence/contract/{contract_id}` | Lista as evidências de um contrato |
| `GET /v1/evidence/{sha256}` | Download de evidência pelo hash |
| `GET /v1/triage/signals` | Fila editorial de sinais |
| `POST /v1/triage/signals/{key}/review` | Transição de triagem (confirmar, rejeitar, publicar) |
| `GET /v1/triage/metrics` | Relatório de precisão por operador |
| `GET /v1/signals/{key}/evidence` | Pacotes de evidência reproduzíveis de um sinal |
| `GET /v1/public/marts` | Lista os marts públicos exportados (sem auth) |
| `GET /v1/public/marts/{name}/{csv,parquet}` | Download do mart público (302 presignado) |
| `GET /v1/public/methodology` | Metodologia gerada do `_marts.yml` + specs de pipeline |
| `POST /v1/subscriptions` | Assinatura de alertas por município (resposta genérica) |
| `GET /v1/subscriptions/confirm` | Confirma a assinatura (token enviado por e-mail) |
| `GET /v1/subscriptions/unsubscribe` | Cancela a assinatura (mesmo token) |

O portal capiba-dashboard (`GET /`, com a página de triagem `/triage`) e o fluxo SSO (`/auth/login`, `/auth/callback`, `/auth/logout`) completam a superfície. Contratos, scores, pesos e códigos de erro estão documentados em `docs/api.md`.

## 8. Resumo de gaps e próximos passos recomendados

As fontes estão ligadas e o lago recebe água de todas elas; o que falta é apertar a torneira.

| Prioridade | Tarefa | Motivação |
|------------|--------|-----------|
| ~~Alta~~ | ~~Configurar `TRANSPARENCY_API_KEY`~~ | **Feito**: a chave está no `.env` e o `scripts/helm-upgrade.sh` a injeta no chart a cada upgrade |
| Média | Habilitar os dumps completos da Receita (`FEDERAL_REVENUE_FILES`) | Por padrão só as tabelas de referência pequenas são baixadas; Empresas/Estabelecimentos/Socios alimentam o silver e o grafo |
| Média | Aproveitar o cache Redis existente nos crawls | Redis já vem habilitado por padrão (monitor de qualidade e hot paths da API); falta aplicá-lo às chamadas das APIs externas para reduzir a dependência da instabilidade delas |
