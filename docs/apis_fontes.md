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
- **Próximo passo:** o usuário deve preencher `global.transparencyApiKey` em `charts/capiba/values.yaml` (ou a variável de ambiente `TRANSPARENCY_API_KEY`) e refazer o `helm upgrade`.

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
- **Ainda não integrado ao pipeline**: nenhuma DAG nem task chama este
  crawler em runtime.
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
| Alta | Configurar `TRANSPARENCY_API_KEY` | Sem token a API do Portal da Transparência bloqueia todas as requisições |
| Média | Integrar `crawler_federal_revenue.py` ao pipeline | Crawler existe mas não tem consumidor em runtime |
| Média | Adicionar cache local e/ou fila para ingestão | Reduzir dependência da instabilidade das APIs externas |
