# PR-D-05b — Red flags de aditivos: forma corrigida (plano B)

- **Pré-registro**: refinamento da bateria D-05 (emenda de PR-D-05)
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para revisão humana, não aprovado, não executado
  (a correção 1 abaixo já está aplicada como fix da refutação de P7 —
  este registro a ratifica; a correção 2 NÃO está implementada)
- **Alvo**: descritor `value_ratio` do módulo/mart e nova fonte de
  termos de contrato do PNCP substituindo o proxy `valorAcumulado` para
  as flags de aditivo
- **Contexto**: etapa real da D-05 (`docs/results/R-D-05.md` § 4):
  P7 refutada por arredondamento do descritor; P8 refutada por
  esparsidade do campo (cobertura computável 23,54% < 50%)

## 1. Pergunta

1. O descritor `value_ratio` em precisão plena satisfaz o domínio
   declarado (razão > 0 sempre que presente) sobre o volume real?
2. As flags de aditivo computadas dos **termos registrados** do contrato
   (fonte autoritativa) são exatas no regime sintético e viáveis sobre o
   recorte-piloto real?

## 2. Regime medido e limitações (obrigatório)

- Correção 1 (descritor): semântica inalterada; apenas precisão. A
  evidência é o data test P7 do mart, já verde após o fix
  (`R-D-05` § 4) — nenhuma execução nova.
- Correção 2 (termos): regime **sintético exato** para a semântica nova
  + **sonda real** sobre o recorte-piloto (não o universo de 205 mil
  contratos — um request por contrato tem custo de rate limit; ver § 4).
- **`qualificacaoReajuste` NÃO é flag**: reajuste por índice (IPCA etc.)
  é atualização legal de preço, não aditivo — acusar reajuste seria
  falso positivo estrutural.
- Termos **excluídos** do PNCP (`arquivos/excluidos`, retificações de
  publicação) ficam fora; o endpoint de termos só lista os vigentes.
- A fonte de termos não substitui a fonte de atualização
  (`daily_pncp_updates`): aquela segue alimentando a detecção temporal;
  os termos respondem "houve aditivo formal?", não "quando mudou".

## 3. Semântica corrigida (declarada)

**Descritor `value_ratio`** (ratificação): razão
`valorAcumulado / valorInicial` em **precisão plena** (double), sem
arredondamento — > 0 por construção sempre que os operandos são
positivos. Implementação: `src/capiba/detection/amendments.py` e
`dbt/models/marts/contract_amendments.sql` (cast para double antes da
divisão — a divisão DECIMAL(38,4) do Trino arredonda razões < 10⁻⁴
pela escala do tipo).

**Flags de aditivo por termos** (nova referência, a implementar em
`src/capiba/detection/amendments.py` como função separada, sem tocar a
semântica vigente até aprovação). Entrada: a lista de termos do contrato
(`GET /v1/orgaos/{cnpj}/contratos/{ano}/{sequencial}/termos`, grupo
`pncp` — público, sem auth, verificado ao vivo em 2026-08-21; ausente do
grupo consulta). Por contrato (1 = suspeito; NULL = dado insuficiente):

- **`f_value_amendment_terms`** — existe termo com
  `tipoTermoContratoNome == "Termo Aditivo"`,
  `qualificacaoAcrescimoSupressao == true` e `valorAcrescido > 0`.
  Lista vazia (HTTP 204) computa 0; falha de consulta computa NULL.
- **`f_term_extension_terms`** — existe termo aditivo com
  `qualificacaoVigencia == true` e `prazoAditadoDias > 0`. Mesma
  disciplina de nulos.

Descritores não-flag: quantidade de termos, soma de `valorAcrescido`,
soma de `prazoAditadoDias`, tipos distintos observados.

## 4. Desenho

- **Fonte nova** `pncp_contract_terms`: crawl por contrato com o helper
  `fetch_page` (retry/backoff do `_http.py`), checkpoint por contrato no
  bronze (`pncp_contract_terms/<cnpj>/<ano>/<seq>.json.gz` — retry pula
  os já persistidos), bronze-only como `daily_pncp_updates`.
- **Recorte-piloto** (declarado, não o universo): (a) todos os contratos
  com `f_value_amendment = 1` pelo proxy (556 no volume de 2026-08-20 —
  coorte de controle positivo) e (b) os contratos do município-piloto
  Recife (SIAFI 2531), o mesmo recorte editorial de PR-D-08. Estimativa
  de volume: ~10³ requests — cabe na janela de rate limit do PNCP.
- **Casos sintéticos plantados** (B1–B6): sem termos → (0,0); termo de
  acréscimo R$ 6.840,88 → (1,0); termo de vigência 180 dias → (0,1);
  termo de reajuste puro (`qualificacaoReajuste`, sem acréscimo/vigência)
  → (0,0); lista com termo de supressão (`qualificacaoAcrescimoSupressao`
  com `valorAcrescido` negativo) → não dispara valor; falha de consulta →
  (NULL, NULL).

## 5. Predições (numéricas, falsificáveis)

- **Q1 — vetor exato (sintético).** B1–B6 produzem exatamente os vetores
  declarados (§ 4). *Refutada* com qualquer divergência.
- **Q2 — disciplina de nulos (sintético).** Falha de consulta → NULL;
  204 → 0; reajuste puro nunca dispara. *Refutada* com qualquer inversão.
- **Q3 — viabilidade do endpoint (real, piloto).** Resposta (200/204)
  em **≥ 95%** dos contratos do recorte. *Refutada* abaixo disso (fonte
  instável/incompleta — repensar antes de escalar o crawl).
- **Q4 — concordância proxy→termos (real, coorte de controle).** Entre
  os 556 contratos com flag de valor pelo proxy, **< 50%** terão termo
  de acréscimo correspondente — a refutação de P8 e a sonda anedótica
  (`R-D-05` § 4: flag 1 com zero termos) indicam que `valorAcumulado`
  agrega empenhos. *Refutada* se ≥ 50% — nesse caso o proxy era melhor
  do que o previsto e o plano B perde a justificativa.
- **Q5 — domínio (real).** Flags dos termos ∈ {0,1,NULL}; zero linhas
  fora do domínio no mart. *Refutada* com qualquer violação.

## 6. Controles e invariantes

- Controle interno: B1 (sem termos) e o reajuste puro são os baselines
  de ausência de falso positivo.
- A semântica vigente (proxy `valorAcumulado`) **permanece** até o
  veredito de Q4: se o proxy for refutado como precisão < 50%, o mart
  `contract_amendments` passa a preferir as flags de termos onde a
  consulta existir (proxy como fallback), mudança de mart registrada
  aqui.
- Idempotência do crawl por contrato: checkpoint por arquivo no bronze;
  retry nunca duplica (mesmo padrão de `task_download_source`).

## 7. Critério de encerramento

Refinamento **bem-sucedido** com Q1–Q2 exatas no sintético e Q3/Q5
satisfeitas no piloto; Q4 é medida de concordância com hipótese
declarada — o veredito dela decide se as flags de termos substituem o
proxy no mart. Qualquer refutação é publicada em
`docs/results/R-D-05b.md` com a causa investigada. Nada disso executa
antes da aprovação humana deste registro.

## 8. Execução (após aprovação)

1. Crawler `fetch_contract_terms` + fonte `pncp_contract_terms` no
   registry + spec `dags/pipelines/` bronze-only com o recorte-piloto
   parametrizado.
2. `compute_term_flags` em `src/capiba/detection/amendments.py` (função
   nova; a vigente não muda), testes unitários rápidos.
3. Bateria sintética B1–B6 (`experiments/detect/D-05b.json`, seeds
   inclusas) + teste de regime `@pytest.mark.slow`.
4. Sonda real do piloto + data tests dbt de Q3/Q5; publicação em
   `docs/results/R-D-05b.md`.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana), após a etapa real
  da D-05 refutar P7 (arredondamento do descritor — fix aplicado e aqui
  ratificado) e P8 (cobertura 23,54%), e após a verificação ao vivo do
  endpoint de termos no grupo `pncp` da API PNCP.
