# Análise de APIs de Fontes de Dados — Capiba

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

- **Autenticação:** não necessária para consulta pública.
- **Formato de data:** `yyyyMMdd` (ex: `20260813`).
- **Paginação:** página começa em 1. Limite de 50 registros por página para contratações; 500 para contratos/atas.
- **Rate limit:** não documentado oficialmente, mas recomenda-se backoff exponencial.
- **Resposta vazia:** retorna `204 No Content` quando não há dados.
- **Estabilidade:** instável; timeout e retry são necessários. O crawler já implementa retry com backoff.

### Estado no Capiba

- O crawler `crawler_pncp.py` usa `/v1/contratos` e já implementa paginação e retry.
- A execução manual da DAG `daily_ingestion` confirmou que o endpoint retorna contratos com fornecedor e valores.
- Não há credenciais a configurar.

---

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

### Requisitos técnicos

- **Autenticação:** obrigatória. Token gratuito via cadastro em `https://portaldatransparencia.gov.br/api-de-dados/cadastrar-email`.
- **Header:** `chave-api-dados: SEU_TOKEN`.
- **Formato de data:** `DD/MM/AAAA`.
- **Rate limit:** 30 requisições/minuto com token; sem token as requisições são bloqueadas.
- **Paginação:** `pagina` começa em 1; máximo de 15.000 registros por página.
- **Disponibilidade:** pode apresentar instabilidade em horários de pico.

### Estado no Capiba

- O crawler `crawler_transparency.py` envia o header `chave-api-dados` a partir da variável `TRANSPARENCY_API_KEY`.
- A variável é injetada no chart Helm via `global.transparencyApiKey` (secret `capiba-secrets`).
- Os endpoints `GET /ceis` e `GET /cnep` já têm pipeline: a DAG `weekly_sanctions`
  (spec `dags/pipelines/weekly_sanctions.yaml`, fórmula `entities_collect`)
  coleta as duas listas semanalmente (`fetch_sanctions`, paginação até página
  vazia), grava os payloads brutos nas tabelas bronze `raw_ceis`/`raw_cnep` e
  os registros normalizados (modelo `Sanction`) na tabela silver `sanctions`.
- A chave está configurada: `TRANSPARENCY_API_KEY` no `.env` (lida por `scripts/helm-upgrade.sh` e injetada no chart via `--set global.transparencyApiKey` a cada `make helm-upgrade`). Sem a chave, o pipeline falha com log claro.

---

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

- **Autenticação:** não necessária.
- **Formato:** CSV dentro de ZIPs, separado por ponto-e-vírgula, encoding `latin1`.
- **Volume:** grande (vários GB por mês).
- **Atualização:** mensal.

### Estado no Capiba

- O crawler `crawler_federal_revenue.py` baixa os múltiplos ZIPs do compartilhamento
  SERPRO+ (`download_cnpj_dump`, `extract_cnpj_zip`, `parse_cnpj_csv`).
- A URL base é configurável via `FEDERAL_REVENUE_BASE_URL`; o mês de referência
  é passado como parâmetro `reference_month` para `download_cnpj_dump`.
- **Integrado ao pipeline**: a DAG `monthly_federal_revenue` (spec
  `dags/pipelines/monthly_federal_revenue.yaml`, fórmula `file_dump`) baixa os
  ZIPs para o bronze (arquivos + manifesto na tabela `raw_federal_revenue`),
  normaliza Empresas/Estabelecimentos/Socios em streaming para as tabelas
  silver `companies`/`establishments`/`partners` (parser em
  `src/capiba/ingestion/cnpj.py`, opt-in via `FEDERAL_REVENUE_FILES`) e carrega
  os vértices/arestas no grafo ArangoDB (destino `arangodb_graph`).
- Não há credenciais a configurar, mas a URL pode precisar de ajuste se a
  Receita Federal alterar o compartilhamento.

---

## 4. API interna do Capiba

### Estado atual

| Endpoint | Status |
|----------|--------|
| `GET /health` | Funcional (retorna `{"status": "ok"}`) |
| `GET /v1/signals/{cnpj}` | Funcional — consulta ArangoDB e roda operadores de detecção |
| `GET /v1/ranking/municipalities` | Funcional — agregação AQL por município |

Contratos, scores, pesos e códigos de erro estão documentados em
`docs/api.md`.

---

## 5. Resumo de gaps e próximos passos recomendados

| Prioridade | Tarefa | Motivação |
|------------|--------|-----------|
| ~~Alta~~ | ~~Configurar `TRANSPARENCY_API_KEY`~~ | **Feito** — a chave está no `.env` e o `scripts/helm-upgrade.sh` a injeta no chart a cada upgrade |
| Média | Habilitar os dumps completos da Receita (`FEDERAL_REVENUE_FILES`) | Por padrão só as tabelas de referência pequenas são baixadas; Empresas/Estabelecimentos/Socios alimentam o silver e o grafo |
| Média | Aproveitar o cache Redis existente nos crawls | Redis já está habilitado por padrão (monitor de qualidade e hot paths da API); falta aplicá-lo às chamadas das APIs externas para reduzir a dependência da instabilidade delas |
