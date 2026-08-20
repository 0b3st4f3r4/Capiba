# Análise de APIs de fontes de dados

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

O crawler `crawler_pncp.py` bate em `/v1/contratos` paginando até a última página, com retry e backoff exponencial centralizados no helper `fetch_page` de `src/capiba/ingestion/_http.py`, que trata o `204` como página vazia e o `429` como sinal de espera. A execução manual da DAG `daily_pncp` confirmou que o endpoint devolve contratos com fornecedor e valores. O crawler também cobre `GET /v1/contratos/atualizacao` (`fetch_contract_updates`), consumido pela DAG bronze-only `daily_pncp_updates` para capturar contratos aditivados após a publicação original. Não há credenciais a configurar.

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

O crawler `crawler_transparency.py` envia o header `chave-api-dados` a partir da variável `TRANSPARENCY_API_KEY`, que mora no `.env` e é injetada no chart pelo `scripts/helm-upgrade.sh` (via `--set global.transparencyApiKey`, secret `capiba-secrets`) a cada `make helm-upgrade`; sem ela, o pipeline falha com log claro. Os endpoints `GET /ceis`, `GET /cnep` e `GET /ceaf` já têm pipeline próprio: a DAG `weekly_sanctions` (spec `dags/pipelines/weekly_sanctions.yaml`, fórmula `entities_collect`) coleta as três listas semanalmente com `fetch_sanctions`, paginando até a primeira página vazia, grava os payloads brutos nas tabelas bronze `raw_ceis`/`raw_cnep`/`raw_ceaf` e os registros normalizados (modelo `Sanction`) na tabela silver `sanctions`.

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

Não pede autenticação. O dump é um snapshot fixo por ciclo eleitoral, republicado ao longo do julgamento das contas — não é indexado por mês; o ano do ciclo é configurável (`TSE_ELECTION_YEAR`, default 2024).

### Estado no Capiba

A DAG `monthly_tse` (spec `dags/pipelines/monthly_tse.yaml`, fórmula `file_dump`) baixa os ZIPs para o bronze (`tse/files/`, manifesto na tabela `raw_tse`) e normaliza em streaming (downloader `crawler_tse.py`, parser `src/capiba/ingestion/tse.py`) para as silvers `campaign_donations` e `candidacies` — insumo do sinal `political_connection`. Os documentos dos doadores ficam completos no silver; o mascaramento é preocupação do mart gold (LGPD).

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

O crawler `crawler_querido_diario.py` (`fetch_gazettes`, `download_gazette_text`, `text_file_name`) alimenta a DAG `daily_querido_diario` (spec `dags/pipelines/daily_querido_diario.yaml`, fórmula `documents_collect`): município-piloto Recife (IBGE 2611606), metadados no bronze (`raw_querido_diario`) + texto extraído de cada diário como arquivo bronze (nome determinístico, skip-existing no retry), validação declarada `gazette_rules`. O corpus é matéria-prima para os sinais de NLP (`semantic_gap`, `detect_clone`).

## 6. API interna do Capiba

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

## 7. Resumo de gaps e próximos passos recomendados

As fontes estão ligadas e o lago recebe água de todas elas; o que falta é apertar a torneira.

| Prioridade | Tarefa | Motivação |
|------------|--------|-----------|
| ~~Alta~~ | ~~Configurar `TRANSPARENCY_API_KEY`~~ | **Feito**: a chave está no `.env` e o `scripts/helm-upgrade.sh` a injeta no chart a cada upgrade |
| Média | Habilitar os dumps completos da Receita (`FEDERAL_REVENUE_FILES`) | Por padrão só as tabelas de referência pequenas são baixadas; Empresas/Estabelecimentos/Socios alimentam o silver e o grafo |
| Média | Aproveitar o cache Redis existente nos crawls | Redis já vem habilitado por padrão (monitor de qualidade e hot paths da API); falta aplicá-lo às chamadas das APIs externas para reduzir a dependência da instabilidade delas |
