# PR-D-08 — Conexão política: doadores de campanha × fornecedores do ente apoiado (O8)

- **Pré-registro**: bateria D-08
- **Criado em**: 2026-08-20
- **Última atualização**: 2026-08-20
- **Status**: rascunho para revisão humana (não executado)
- **Alvo**: sinal `political_connection` (novo `SignalType`) — match por
  documento entre doadores de campanha (prestação de contas eleitorais do
  TSE) e fornecedores do silver `contracts`, exigindo coincidência temporal
  (contrato assinado dentro do mandato do eleito apoiado, após a posse) e
  concentração (participação mínima do doador-fornecedor nas receitas do
  ente na janela do mandato). Backlog O8 (`docs/oportunidades.md`); gap 9
  de `docs/gaps.md`. Semântica de referência em
  `src/capiba/detection/political.py` (a implementar, fase 2).
- **Configuração**: `experiments/detect/D-08.json` (declarativa, seeds
  inclusas; esqueleto criado junto a este registro, sem execução)

## 1. Pergunta

O cruzamento doador × fornecedor, na semântica declarada na seção 3
(gates de documento, temporalidade e concentração pré-registrados),
(i) reproduz **exatamente** os sinais plantados no regime sintético —
inclusive as disciplinas "doação lícita não é indício por si" (contrato
antes da posse não sinaliza), "candidato derrotado não sinaliza" e
"nome não é documento" — e (ii) permanece deterministicamente reproduzível
entre seeds e execuções?

## 2. Regime medido e limitações (obrigatório)

- Dois regimes: **sintético exato** (offline, padrão D-01..D-07) e
  **volume real exploratório declarado** (após a ingestão do dump 2024 e
  com o silver `contracts` acumulado). No real, **não há predição dura de
  contagem** — o número de matches depende da cobertura municipal do
  silver hoje (Recife e PNCP federal não cruzam com eleição municipal);
  mede-se e publica-se a contagem e o invariante estrutural P8, sem banda
  a priori (lição de D-03/D-03b: bandas sem âncora viraram
  inconclusividade).
- **Doação lícita não é indício por si.** O sinal não alega ilicitude nem
  quid pro quo; é um apontador de triagem editorial (O10), com score e
  `details` que carregam os valores que o motivaram. O critério de
  refutação central é comportamental: qualquer sinal emitido sem match por
  documento, sem coincidência temporal ou sem concentração mínima refuta
  a bateria.
- **CPF completo na origem.** O dump traz `NR_CPF_CNPJ_DOADOR` completo
  (11/14 dígitos — ver seção 4, fonte). Dado pessoal sob LGPD: o silver
  pode guardá-lo para o match determinístico, mas os marts publicáveis
  devem agregar/mascarar (padrão `masked_document` do CEAF, D-06b). A
  classificação LGPD do mart é item da fase 2, não deste registro.
- **CDN do TSE bloqueado a partir deste IP** (Akamai, 403 em qualquer
  caminho — verificado em 2026-08-20, incluindo caminho inexistente de
  controle, logo o bloqueio é de acesso, não de arquivo). A URL canônica é
  confirmada por terceiros (seção 4), mas a verificação ao vivo do dump
  (tamanho, ETag, estrutura interna) fica para a fase 2, a partir do
  cluster ou de IP brasileiro.
- **Fora de escopo (v1)**: match de **sócios** de fornecedores (grafo
  `ownership`/`directorship`, O4) contra doadores — refinamento futuro
  `PR-D-08b`; doações a partidos/diretórios como destino direto (o v1 usa
  o **doador originário** quando a doação a candidato veio via partido);
  eleições gerais 2022/2026; fuzzy de nome (nome nunca é evidência aqui);
  despesas de campanha (fornecedores de campanha que viram fornecedores do
  ente — outro cruzamento, outro PR).
- **Escopo do eleito**: v1 só considera candidato **eleito a prefeito**
  (executivo municipal, eleição 2024). Vereador eleito não sinaliza no v1
  (declarado; a influência do legislativo sobre o executivo é tese
  editorial, não gate determinístico).

## 3. Semântica do sinal (declarada)

`political.py` puro e determinístico, sobre as tabelas silver
`campaign_donations` (nova, seção 4) e `contracts`:

- **Match por documento exato**: `donor_document` (11 dígitos = CPF,
  14 = CNPJ) igual a `supplier.cpf` ou `supplier.cnpj`. Quando a doação
  declara doador originário (`NR_CPF_CNPJ_DOADOR_ORIGINARIO` preenchido),
  o match usa o originário; caso contrário, o doador direto. Nome nunca é
  evidência (disciplina D-06/S7 preservada).
- **Gate do eleito**: o destinatário da doação é candidato **eleito a
  prefeito** (situação de totalização "Eleito", de `consulta_cand_2024`),
  e o município da urna (UE) corresponde ao ente comprador do contrato
  (de-para UE TSE ↔ código SIAFI do `buyer`, via seed de-para — decisão
  de implementação da fase 2).
- **Gate temporal**: `signature_date` do contrato ∈
  [`mandate_start`, `mandate_end`] = [2025-01-01, 2028-12-31] (mandato
  municipal eleito em 2024), fronteiras **inclusivas**. Contrato antes da
  posse não sinaliza — é a negação operacional de "doação é indício por
  si".
- **Gate de doação mínima**: soma das doações do doador (originário) à
  campanha do eleito ≥ `min_donation_brl` = **1.000,00**.
- **Gate de concentração**: `share` = valor contratado pelo ente ao
  fornecedor-doador dentro da janela do mandato ÷ valor total contratado
  pelo ente na janela; sinal se `share` ≥ `min_supplier_share` = **0,05**.
- **Score**: `min(1.0, share / 0.25)` — share de 5% pontua 0,2; share
  ≥ 25% satura em 1,0. Âncora exata: share 12,5% → score 0,5.
- **Emissão**: um sinal `political_connection` por (ente, fornecedor,
  eleito); `details` com documento do doador (para triagem, não para o
  mart publicável), total doado, total contratado, share, candidato,
  partido, município e contagens. Limiares vivem na config
  (`D-08.json`), nunca só em código.

## 4. Desenho

**Fonte TSE (pesquisa de viabilidade, 2026-08-20):**

- **Dump escolhido**: *Prestação de Contas Eleitorais — 2024* (eleições
  **municipais** 2024, as relevantes para o cruzamento com fornecedores
  municipais do `monthly_transparency`), dataset
  `prestacao-de-contas-eleitorais-2024` do portal
  [dadosabertos.tse.jus.br](https://dadosabertos.tse.jus.br/dataset/prestacao-de-contas-eleitorais-2024)
  (recursos: prestação de contas de candidatos, de órgãos partidários,
  CNPJ de campanha, extratos bancários).
- **URL canônica**:
  `https://cdn.tse.jus.br/estatistica/sead/odsele/prestacao_contas/prestacao_de_contas_eleitorais_candidatos_2024.zip`
  (padrão confirmado por terceiros: [pipeline Base dos Dados /
  `br_tse_eleicoes__receitas_candidato`](https://raw.githubusercontent.com/basedosdados/queries-basedosdados/main/models/br_tse_eleicoes/schema.yml)
  e [gist valdeirpsr](https://gist.github.com/valdeirpsr/c7fb5589f463b82fe3b14ee17e4eb2b2),
  que baixa exatamente esse zip). Complemento obrigatório para o gate do
  eleito: `consulta_cand_2024.zip` (situação de totalização).
- **Conteúdo**: o zip traz `receitas_candidatos_2024_BRASIL.csv` (e por
  UF), `despesas_contratadas_candidatos_2024_BRASIL.csv` e
  `despesas_pagas_candidatos_2024_BRASIL.csv`; ISO-8859-1, separador `;`,
  decimal com vírgula. Para o sinal v1 interessa apenas o de receitas.
- **Campos-chave** (leiaute confirmado pelas duas fontes acima):
  `NR_CPF_CNPJ_DOADOR` (**completo**, 11/14 dígitos — o gist o mascara
  localmente por LGPD, logo a origem não mascara), `NM_DOADOR`,
  `NM_DOADOR_RFB` (nome validado na Receita), `DT_RECEITA`, `VR_RECEITA`,
  `DS_ORIGEM_RECEITA`, `CD_FONTE_RECEITA`,
  `NR_CPF_CNPJ_DOADOR_ORIGINARIO` / `NM_DOADOR_ORIGINARIO(_RFB)` (doador
  originário em doação via partido), `SQ_CANDIDATO`, `NM_CANDIDATO`,
  `SG_PARTIDO`, `DS_CARGO`, `NM_UE`, `SG_UF`, `NR_CNPJ_CAMPANHA`.
- **Volume**: ordem de grandeza de milhões de linhas no
  `receitas_candidatos_2024_BRASIL.csv` (eleição municipal com ~5,6 mil
  municípios; a série histórica acumulada 2002–2024 na Base dos Dados
  tem dezenas de milhões). Tamanho do zip **não verificado** — CDN
  inacessível deste IP (limitação da seção 2); a sondagem real é passo
  zero da fase 2.
- **Cadência**: dump snapshot, republicado ao longo do julgamento das
  contas; a spec `monthly_tse.yaml` (fase 2) rebaixa mensalmente e
  reprocessa só se o conteúdo mudar.

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros). Casos plantados (doação × contrato):

- **E1** — PJ: doador CNPJ doa R$ 50.000 a prefeito eleito do município
  X; após a posse, X contrata a empresa com share 0,40 → **sinal**,
  score 1,0 (saturação).
- **E2** — idem E1, contrato assinado **antes da posse** → sem sinal
  (doação lícita não é indício por si).
- **E3** — doador doa a candidato **derrotado**; contratação após 2025 →
  sem sinal.
- **E4** — match e temporalidade ok, share 0,01 (< 0,05) → sem sinal
  (concentração).
- **E5** — PF: doador CPF, share 0,125 → **sinal**, score **0,5 exato**
  (âncora).
- **E6** — doação total R$ 500 (< 1.000) com share alto → sem sinal
  (piso de doação).
- **E7** — doador doa a **vereador eleito** e vira fornecedor do
  executivo → sem sinal (v1: só prefeito).
- **E8** — mesmo nome de doador e fornecedor, **documentos divergentes**
  → sem sinal (nome não é documento).
- **E9** — doação **via partido** (doador direto = diretório, doador
  originário = empresa fornecedora) → **sinal** (match pelo originário).
- **E10** — share **exatamente 0,05** (fronteira inclusiva) → **sinal**,
  score **0,2 exato** (âncora).
- **Controle** — 20 pares doador-fornecedor disjuntos → zero sinais.

**Volume real**: após a ingestão do dump 2024 e uma run do `detect`,
mede-se a contagem de sinais e verifica-se P8 por query no gold;
publica-se no R-D-08 sem banda a priori (exploratório declarado).

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds.

- **P1 — conjunto exato de sinais.** Sinalizam exatamente E1, E5, E9 e
  E10; E2, E3, E4, E6, E7, E8 e os 20 controles não. *Refutada* com
  qualquer sinal a mais ou a menos.
- **P2 — gate temporal.** E2 sem sinal. *Refutada* se contrato anterior à
  posse sinalizar.
- **P3 — gate do eleito.** E3 e E7 sem sinal. *Refutada* se derrotado ou
  vereador sinalizar.
- **P4 — gate de concentração e âncoras de score.** E4 sem sinal; E5
  sinaliza com score 0,5 exato; E10 sinaliza com score 0,2 exato; E1
  satura em 1,0. *Refutada* com qualquer desvio de score > 1e-9 ou
  inversão de veredito.
- **P5 — piso de doação.** E6 sem sinal. *Refutada* se doação abaixo de
  R$ 1.000 sinalizar.
- **P6 — disciplina de documento.** E8 sem sinal; E9 sinaliza pelo
  originário. *Refutada* se nome idêntico com documento divergente
  sinalizar, ou se o match pelo originário falhar.
- **P7 — determinismo.** A mesma seed reproduz bit a bit os mesmos sinais;
  execuções distintas divergem em zero casos.
- **P8 — invariante estrutural (pós-integração, dados reais).** Todo
  sinal `political_connection` no gold tem match exato de documento,
  `signature_date` dentro do mandato, doação ≥ piso e share ≥ limiar.
  Verificado por query após uma run do `detect` com a feature; resultado
  anexado ao R-D-08.

## 6. Controles e invariantes

- Controles internos: E2/E3/E7 (gates temporais e de eleito), E4/E6
  (pisos), E8 (documento) e os 20 pares disjuntos.
- Invariante de composição: o sinal existe se e somente se os cinco gates
  da seção 3 valem — testável recomputando das linhas silver (`details`
  carrega os campos que fundamentam).
- Invariante de monotonicidade: alterar limiares, janela de mandato,
  incluir sócios (grafo), cargos legislativos ou fuzzy exige PR de
  refinamento (`PR-D-08b`) com a justificativa medida.
- Os sinais existentes não podem ser afetados: nenhum código de detecção
  vigente muda nesta fatia (guarda: baterias D-01..D-07b seguem verdes).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P7 exatas nas 5 seeds (P8 após a
integração). Qualquer refutação é publicada em `docs/results/R-D-08.md`
com a causa investigada, e a forma corrigida vira `PR-D-08b.md` antes de
nova execução. O sucesso habilita — não autoriza automaticamente — o
mart gold publicável (com classificação LGPD) e a extensão a sócios via
grafo.

## 8. Execução (fase 2, após aprovação humana deste registro)

Com TDD/BDD usual, no estilo das fontes existentes (`federal_revenue` =
fórmula `file_dump`; sanções = `entities_collect`):

1. **Ingestão**: crawler TSE em `src/capiba/ingestion/crawler_tse.py` +
   parser no `DUMP_PARSER_REGISTRY` (zip → CSV latin-1 chunked) e entradas
   nos registries; silver `campaign_donations` (schema Iceberg em
   `pipeline/lake.py`); spec `dags/pipelines/monthly_tse.yaml` (fórmula
   `file_dump`, bronze-only ou com normalize conforme o padrão).
2. **Sinal**: `src/capiba/detection/political.py` puro +
   `SignalType.POLITICAL_CONNECTION`; emissão no `task_detect`
   (best-effort, padrão dos demais sinais).
3. **Mart gold**: `dbt/models/.../political_connections` (cruzamento
   doações × contratos com o de-para UE↔SIAFI em seed), exposto ao
   Grafana/Trino; classificação LGPD antes de qualquer publicação (O11).
4. **Bateria**: runner `battery_political.py` (dispatch
   `"runner": "political"` em `scripts/detect_battery.py`) lendo
   `experiments/detect/D-08.json`; teste de regime `@pytest.mark.slow`.
5. **Publicação**: `docs/results/R-D-08.md` com o veredito (inclusive se
   refutada) e P8 anexado após a run real.

## Revisões

- 2026-08-20: criação (rascunho para revisão humana). Pesquisa de
  viabilidade da fonte na seção 4: dataset e recursos confirmados no
  portal de dados abertos do TSE; URL canônica e leiaute confirmados por
  terceiros (Base dos Dados, gist valdeirpsr); CDN inacessível deste IP
  (Akamai 403, testado inclusive com caminho inexistente de controle) —
  sondagem real adiada para a fase 2. Limiares (piso R$ 1.000, share 5%,
  saturação 25%) são placeholders de calibração pré-registrados, à espera
  de validação no regime sintético.
- 2026-08-20: implementação do sinal (fatia 2), sem alterar predições.
  Fonte do gate do eleito: dump `consulta_cand_2024.zip` (mesmo CDN,
  diretório `consulta_cand`), coluna `DS_SITUACAO_TOTALIZACAO_TURNO` —
  elegem os status com prefixo "Eleito" ("Eleito", "Eleito por QP",
  "Eleito por média"); leiaute confirmado por terceiros, a confirmar na
  sondagem real da fase 2. Nova entidade silver `candidacies`. O match
  município da urna × ente comprador é feito no sinal pelo par
  (cidade, UF) normalizado (maiúsculas, sem acentos); o de-para UE↔SIAFI
  via seed dbt fica para o mart da fatia 3. A janela do mandato deriva de
  `TSE_ELECTION_YEAR` (posse em 1º de janeiro do ano seguinte, 4 anos).
- 2026-08-20: mart gold `political_connections` (fatia 3), sem alterar
  predições. Fontes: sinais `political_connection` do gold `fraud_signals`
  (última run por doador×eleito) enriquecidos com as silvers
  `campaign_donations`/`candidacies` (partição mais recente — são snapshots
  mensais) e com a seed de-para UE↔SIAFI (`dbt/seeds/ue_siafi_crosswalk.csv`,
  incremental: hoje só o piloto Recife — UE TSE 25313 confirmada pelo
  portal eleitoral do TCU e pelas URLs do divulgacandcontas; SIAFI 2531 da
  lista oficial MDR/gov.br; IBGE 2611606). **Classificação LGPD do mart**:
  documento completo nunca sai do mart — CPF (11 dígitos, dado pessoal) é
  mascarado no padrão CEAF (`***123456**`); CNPJ (14 dígitos) identifica
  empresa, cujas doações são públicas nas fontes TSE/RFB, e é mantido; a
  chave estável é o `signal_id` (sha256 de ano|sequencial|documento),
  compatível com a chave de triagem sem expor o documento. O silver segue
  com documentos completos (decisão da seção 2). Invariantes P8 e de
  mascaramento viraram testes singulares dbt
  (`dbt/tests/political_connections_*.sql`); a âncora de validação offline
  é o `dbt parse` + parsing sqlglot do SQL renderizado (Trino indisponível
  offline nesta máquina).
