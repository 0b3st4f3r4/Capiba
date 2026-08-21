# PR-D-08b — Troca de fonte da prestação de contas TSE: espelho Base dos Dados (O8, refinamento de fonte)

- **Pré-registro**: bateria D-08b (refinamento de **fonte** de D-08 — a
  semântica do sinal `political_connection` não muda)
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para revisão humana — nenhuma execução
- **Alvo**: desbloquear a ingestão real das silvers `campaign_donations` e
  `candidacies`, mudas desde 2026-08-20 porque **todo o domínio
  `*.tse.jus.br` está geo-bloqueado** (Akamai 403) a partir do IP de
  desenvolvimento — reconfirmado em 2026-08-21 (ver seção 2). O sinal
  `political_connection` está validado no sintético (R-D-08, 7/7), mas o
  regime de volume real e P8 seguem pendentes da ingestão.
- **Configuração**: `experiments/detect/D-08b.json` (declarativa; runner
  `"tse_parity"` a implementar na execução, após aprovação)

## 1. Pergunta

O espelho **Base dos Dados** (`br_tse_eleicoes`) reproduz a prestação de
contas eleitorais 2024 do TSE com fidelidade suficiente para alimentar as
silvers `campaign_donations`/`candidacies` — medido por **paridade
numérica falsificável** contra o dump oficial do TSE numa amostra
declarada — de modo que o sinal `political_connection` (semântica intacta
de PR-D-08 § 3) possa executar no regime de volume real?

## 2. Regime medido e limitações (obrigatório)

- **Este registro troca a fonte, não o sinal.** Nenhum gate, limiar ou
  score de PR-D-08 § 3 é alterado; a bateria sintética D-08 (P1–P7) segue
  verde e é a guarda de regressão. O que muda é a **proveniência** das
  linhas silver e a fonte do gate do eleito (seção 3).
- **Bloqueio TSE reconfirmado em 2026-08-21**: `curl` com HEAD e GET
  (Range 0–1023, User-Agent de browser Firefox/Chrome) retorna **403**
  para `cdn.tse.jus.br` (ambos os ZIPs), para o portal
  `dadosabertos.tse.jus.br` (API CKAN) e para a API REST do
  `divulgacandcontas.tse.jus.br`. O bloqueio é de domínio/IP (Akamai),
  **não contornável por User-Agent ou caminho alternativo**; o cluster
  local (k3s) compartilha o mesmo IP de saída, logo não resolve por si.
- **Regime medido**: paridade offline entre a silver derivada do espelho
  e o dump oficial TSE (`receitas_candidatos_2024_BRASIL.csv` +
  `consulta_cand_2024_BRASIL.csv`) obtido **uma única vez via IP
  brasileiro** (download manual ou esteira com egresso no Brasil) e
  depositado no bronze como referência congelada de auditoria
  (`tse/reference/dt=<data>/`), nunca como fonte do pipeline. A bateria
  lê bronze + silver, sem rede.
- **Defasagem de snapshot declarada**: a Base dos Dados carregou 2024 em
  `receitas_candidato` em ago/2024 ([PR
  #749](https://github.com/basedosdados/queries-basedosdados/pull/749)) e
  re-materializou após o 2º turno em nov/2024 ([PR
  #818](https://github.com/basedosdados/queries-basedosdados/pull/818));
  o crawler da BD ([`crawler/tse_eleicoes/utils.py`](https://github.com/basedosdados/pipelines/blob/main/pipelines/crawler/tse_eleicoes/utils.py))
  só reatualiza se a contagem de linhas crescer, e os flows estão **sem
  agenda** (cron comentado). O espelho é, portanto, um snapshot de
  ~nov/2024: as republicações do dump TSE ao longo do **julgamento** das
  contas (2025+) não estão capturadas. A prestação final (entregue até
  nov/2024) congela as receitas declaradas; o julgamento muda a situação
  das contas, raramente as linhas de receita — mas essa premissa é
  exatamente o que a bateria de paridade mede.
- **Campo perdido no espelho (declarado)**: o tratamento da BD **zera os
  campos do doador originário** (`cpf_cnpj_doador_orig`, `nome_doador_orig`,
  `nome_doador_orig_rf`, `tipo_doador_orig` — constam da lista `vazios`
  do `ReceitasCandidato.form_df_base`). Consequência: no regime real via
  espelho, `donor_origin_document` vem sempre vazio e o match pelo
  originário (caso E9 de PR-D-08) **não ocorre** — degradação de
  **revocação**, nunca de precisão (nome segue sem ser evidência). O
  impacto é medido por P5 (seção 5) com gatilho de refinamento.
- **CPF completo preservado no espelho**: o pipeline da BD **não mascara**
  `NR_CPF_CNPJ_DOADOR` (apenas remove sentinelas `#NULO`/`#NE`/`-1` etc.),
  confirmado no código de tratamento e no modelo dbt
  (`safe_cast(cpf_cnpj_doador as string)`, sem transformação). A decisão
  LGPD de PR-D-08 § 2 permanece: documento completo no silver para o
  match determinístico; mascaramento só no mart gold.
- **Acesso e licença**: dataset público
  [`basedosdados.br_tse_eleicoes`](https://basedosdados.org/dataset/eef764df-bde8-4905-b115-6fc23b6ba9d6)
  via BigQuery (projeto de billing próprio; free tier de 1 TiB/mês de
  consulta — a partição 2024 de `receitas_candidato` é da ordem de
  centenas de MiB) ou pacote Python `basedosdados` com API key.
  [Termos de uso](https://basedosdados.org/terms): a propriedade
  intelectual dos dados é de terceiros (aqui o TSE — dado público) e os
  serviços exclusivos da BD são por assinatura; a atribuição à Base dos
  Dados como via de acesso será registrada na documentação do pipeline.
  A dependência operacional (credencial GCP/API key) é decisão de
  implementação da execução (seção 8), não deste registro.
- **Fora de escopo**: doador originário completo (ver P5); eleições
  gerais; despesas de campanha; atualização contínua do espelho (a
  cadência mensal do `monthly_tse` passa a consultar o espelho, que só
  muda quando a BD reatualiza — reprocessamento segue condicionado a
  mudança de conteúdo); match de sócios via grafo (refinamento próprio).
- **O que esta bateria não prova**: que a cobertura municipal do silver
  `contracts` cruze com a eleição 2024 (limitação já declarada em PR-D-08
  § 2 — sem predição dura de contagem no real); que o espelho permaneça
  atualizado no futuro (sem agenda na BD — risco registrado em
  `docs/gaps.md` na execução).

## 3. Semântica da troca de fonte (declarada)

**Fonte primária (nova)**: Base dos Dados, dataset `br_tse_eleicoes`:

- Silver `campaign_donations` ← tabela `receitas_candidato`
  (`ano = 2024`). Mapeamento declarado para o schema silver vigente
  (`src/capiba/ingestion/tse.py`): `sequencial_prestador_contas` →
  `prestador_sequential`, `sequencial_receita` → `revenue_sequential`
  (logo o `donation_id` = sha256(prestador|receita) é **idêntico** ao
  produzido pelo dump — âncora de paridade), `data_receita` →
  `donation_date`, `valor_receita` → `amount`, `origem_receita` →
  `revenue_origin`, `cpf_cnpj_doador` → `donor_document` (limpeza
  `_clean_document` inalterada), `nome_doador_rf`/`nome_doador` →
  `donor_name` (preferência RFB preservada), `cpf_cnpj_doador_orig` →
  `donor_origin_document` (**sempre vazio no espelho** — seção 2),
  `sequencial_candidato` → `candidate_sequential`, `nome_candidato` →
  `candidate_name`, `sigla_partido` → `party`, `cargo` → `office`,
  `sigla_uf` → `uf`; `ue_name` resolvido de `id_municipio_tse`/`id_municipio`
  (IBGE) via o diretório de municípios da própria BD ou a referência
  geográfica vendored (`ingestion/geography.py`).
- Silver `candidacies` (gate do eleito) ← tabela `resultados_candidato`
  (`ano = 2024`), coluna `resultado`: elegem os status com prefixo
  **"eleito"** após normalização (maiúsculas, sem acentos — cobre
  "eleito", "eleito por qp", "eleito por média"), equivalência declarada
  com `DS_SITUACAO_TOTALIZACAO_TURNO` do `consulta_cand` (PR-D-08,
  Revisões). `ue_code` ← `id_municipio_tse`; `candidate_name` enriquecido
  da tabela `candidatos` por `sequencial_candidato` (a tabela de
  resultados não carrega nome). Nota: `candidatos.situacao` da BD é
  `DS_DETALHE_SITUACAO_CAND` (deferimento do registro), **não** a
  totalização — por isso o gate migra para `resultados_candidato`.
- **Janela de mandato inalterada**: deriva de `TSE_ELECTION_YEAR`
  (posse em 1º de janeiro do ano seguinte, 4 anos) — independente de
  fonte. A troca de fonte não toca o gate temporal.

**Âncora de paridade (referência congelada)**: dump oficial TSE obtido
uma vez via IP brasileiro, parseado pelo `parse_tse_zip` **vigente**
(`src/capiba/ingestion/tse.py`, inalterado neste refinamento) — a
comparação é silver-espelho × parser-oficial-sobre-dump, medindo o
espelho, não o parser.

**Amostra declarada (determinística, sem seed)**: todos os municípios de
**Pernambuco** (185 UEs — inclui o piloto Recife, UE 25313) **mais as
demais 26 capitais** (determinístico: capital por UF na referência
geográfica vendored). ~211 UEs, cobrindo o piloto, capitais grandes e
municípios pequenos.

## 4. Desenho — espelhos avaliados (pesquisa de 2026-08-21)

- **Base dos Dados (escolhida)** — dataset
  [Eleições Brasileiras / `br_tse_eleicoes`](https://basedosdados.org/dataset/eef764df-bde8-4905-b115-6fc23b6ba9d6),
  cobertura 1945–2024; tabelas `receitas_candidato` (2002–2024, 13,4 M
  linhas totais materializadas em ago/2024, 862,4 MiB),
  `candidatos`, `resultados_candidato`. Tratamento open source e
  auditável ([queries-basedosdados](https://github.com/basedosdados/queries-basedosdados/blob/main/models/br_tse_eleicoes/schema.yml),
  [pipelines](https://github.com/basedosdados/pipelines/tree/main/pipelines/crawler/tse_eleicoes));
  CPF/CNPJ do doador completo; identificadores IBGE/TSE de município já
  tratados; instituição estável, com governança e histórico de issues
  público. Ressalvas declaradas: snapshot ~nov/2024 sem agenda, campos
  do originário zerados.
- **CDN/portal/API do próprio TSE** — `cdn.tse.jus.br`,
  `dadosabertos.tse.jus.br`, `divulgacandcontas.tse.jus.br`: 403
  reconfirmado em 2026-08-21 com UA de browser e GET parcial; bloqueio
  de IP, não de rota. Descartado como via deste ambiente; mantido como
  **âncora** via download único por IP brasileiro.
- **CEPESP Data (FGV)** — [cepespdata](https://github.com/GV-CEPESP/cepespdata):
  foco em votação/resultados ("retrato do dia da eleição"); não espelha
  `receitas_candidatos` 2024; projeto não open source com citação
  obrigatória. Descartado para esta fonte.
- **electionsBR (pacote R)** — [electionsbr.com](https://electionsbr.com/):
  baixa do CDN do TSE em tempo de execução — não é espelho; herda o 403.
- **Brasil.IO** — não hospeda prestação de contas eleitorais.
- **GitHub (gist valdeirpsr e similares)** — scripts que baixam do CDN;
  não são espelho de dados.
- **Kaggle/Dataverse** — nenhum espelho auditável e atualizado da
  prestação 2024 localizado; governança insuficiente para fonte de
  detecção.

**Bateria de paridade (D-08b)**: runner `"tse_parity"` (dispatch novo em
`scripts/detect_battery.py`), offline: lê a referência congelada do
bronze, executa `parse_tse_zip` sobre ela, agrega por UE; consulta a
silver derivada do espelho (ou, offline, um extrato Parquet dela
depositado em `results/detect/D-08b/`, não commitado) e agrega pelas
mesmas chaves; computa P1–P6 da seção 5 sobre a amostra declarada.
Teste de regime `@pytest.mark.slow` (`CAPIBA_SLOW=1`).

## 5. Predições (numéricas, falsificáveis)

Sobre a amostra declarada (seção 3), espelho × referência TSE, eleição
2024, todos os cargos (a restrição a prefeito é do sinal, não da fonte):

- **P1 — contagem de receitas por UE.** Divergência relativa ≤ **1%**
  em ≥ **95%** das UEs da amostra, e **nenhuma** UE com divergência >
  **5%**. *Refutada* fora desses limites.
- **P2 — soma de `valor_receita` por UE.** Mesma disciplina de P1
  (≤ 1% em ≥ 95% das UEs; nenhuma > 5%). *Refutada* fora desses limites.
- **P3 — conjunto de doadores por UE.** Jaccard ≥ **0,99** por UE
  (documentos limpos de 11/14 dígitos, `_clean_document`) em ≥ 95% das
  UEs da amostra. *Refutada* abaixo disso.
- **P4 — gate do eleito exato.** O conjunto de candidatos a prefeito com
  `resultado` prefixo "eleito" (`resultados_candidato`) coincide
  **exatamente** (zero divergências) com os status prefixo "Eleito" de
  `DS_SITUACAO_TOTALIZACAO_TURNO` (`consulta_cand_2024`) nas UEs da
  amostra — totalização é fato fechado, não admite deriva. *Refutada*
  com qualquer divergência.
- **P5 — impacto do originário (medida com gatilho).** Sobre a
  **referência TSE**: fração do valor total de receitas com
  `NR_CPF_CNPJ_DOADOR_ORIGINARIO` preenchido, por amostra. Predição:
  ≤ **5%** do valor. Acima de 5% o espelho **não é refutado como fonte**
  (a perda é de revocação, medida e publicada), mas dispara refinamento
  `PR-D-08c` para complemento do originário (via híbrida TSE/BD).
- **P6 — invariante do espelho.** 100% das linhas da silver
  `campaign_donations` derivada do espelho têm `donor_origin_document`
  vazio; nenhuma linha do espelho falha na validação pydantic vigente
  por motivo distinto de documento inválido/ausente (linhas inválidas
  são contadas e publicadas no relatório, como no parser atual).

## 6. Controles e invariantes

- A bateria sintética D-08 (P1–P7) e seus testes de regime seguem verdes
  — a semântica do sinal não muda; qualquer mudança em
  `detection/political.py` neste refinamento é proibida (guarda:
  `tests/test_political.py`, `tests/test_detect_battery_political.py`).
- `parse_tse_zip` vigente é o oráculo da comparação: a mesma referência
  congelada alimenta o parser oficial; divergência mede o espelho, não o
  parser.
- Determinismo: a bateria sobre extratos congelados reproduz bit a bit;
  a amostra é determinística (sem seed).
- Invariante de monotonicidade: alterar tolerâncias, amostra ou mapear
  o originário por outra via exige `PR-D-08c` com a justificativa medida.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P4 dentro das tolerâncias, P5 medido e
publicado (com ou sem gatilho) e P6 satisfeito. Sucesso habilita — não
autoriza automaticamente — a troca da fonte do `monthly_tse` para o
espelho BD e a corrida do regime de volume real de D-08 (P8). Refutação
de qualquer predição é publicada em `docs/results/R-D-08b.md` com a
causa investigada; a forma corrigida vira `PR-D-08c.md` antes de nova
execução.

## 8. Execução (após aprovação humana deste registro)

1. **Âncora**: obter os dois ZIPs 2024 via IP brasileiro, depositar no
   bronze como `tse/reference/` (congelado, fora do schedule), com SHA-256
   registrado no R-D-08b.
2. **Fonte espelho**: crawler/reader da BD (BigQuery ou pacote Python —
   decisão de implementação com credencial fora do repositório),
   normalizador BD→silver respeitando o mapeamento da seção 3 e os
   modelos pydantic vigentes; spec `monthly_tse.yaml` ganha a fonte
   espelho mantendo a fórmula e a semântica de partição.
3. **Bateria**: runner `tse_parity` lendo `experiments/detect/D-08b.json`;
   teste `@pytest.mark.slow`.
4. **Publicação**: `docs/results/R-D-08b.md` com o veredito (inclusive se
   refutada), a medida de P5 e o registro da defasagem de snapshot
   observada; risco de agenda da BD em `docs/gaps.md`.
5. **Run real de D-08** (P8) somente após o sucesso desta bateria.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana). Pesquisa de
  espelhos na seção 4: bloqueio `*.tse.jus.br` reconfirmado ao vivo
  (403 com UA de browser, HEAD e GET parcial, nos três hosts); Base dos
  Dados escolhida — cobertura 2024 confirmada nos PRs #749/#818 do
  queries-basedosdados, CPF do doador completo e originário zerado
  confirmados no código do crawler da BD; CEPESP, electionsBR, Brasil.IO
  e gists avaliados e descartados (não espelham a prestação ou herdam o
  403). Amostra determinística (PE + capitais) e tolerâncias de paridade
  (1%/95%, teto 5%; Jaccard 0,99; gate do eleito exato) declaradas antes
  de qualquer execução.
