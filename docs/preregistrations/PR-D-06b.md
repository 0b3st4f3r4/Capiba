# PR-D-06b — Screening fuzzy de sanções por nome + documento mascarado (O3, segunda fatia)

- **Pré-registro**: bateria D-06b
- **Criado em**: 2026-08-20
- **Última atualização**: 2026-08-20
- **Status**: executado em 2026-08-20 — veredito **success** (7/7
  predições; ver `docs/results/R-D-06b.md`)
- **Alvo**: sinal `sanctioned_name_match` (novo `SignalType`) — match
  **fuzzy** entre fornecedores de contratos silver e sanções silver, por
  nome normalizado + documento mascarado, com veto documental; ingestão da
  fonte **CEAF** (expulsões da administração pública federal, CPF mascarado
  na origem — confirmado ao vivo em 2026-08-19 e reconfirmado em
  2026-08-20: `punicao.cpfPunidoFormatado` = `***.435.151-**`). Semântica
  de referência em `src/capiba/detection/screening_fuzzy.py` (a
  implementar). Escopo aprovado na conversa em 2026-08-20: **com CEAF**;
  **sinal separado** do `sanctioned_supplier` factual.
- **Configuração**: `experiments/detect/D-06b.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

O screening fuzzy, na semântica declarada na seção 3 (pesos, limiares e
veto pré-registrados), (i) reproduz **exatamente** os sinais plantados no
regime sintético — inclusive as disciplinas "documento contraditório
veta" e "nome-only só acima do limiar alto" — e (ii) sustenta precisão
≥ 0,85 no benchmark real OpenSanctions Pairs no regime nome-only?

## 2. Regime medido e limitações (obrigatório)

- Dois regimes: **sintético exato** (offline, padrão D-01..D-07) e
  **benchmark real** OpenSanctions Pairs (arquivo plano público
  `pairs-20251209.json.gz`, snapshot 2025-12-09; amostra reservoir
  estratificada 1.000+1.000 com seed declarada, **distinta** das seeds já
  usadas em D-07/D-07b — 19/23/37/41). O benchmark mede o regime
  **nome-only** do matcher fuzzy (vocabulário comum); o regime
  doc-assistido (documento mascarado) é medido no sintético — o OS Pairs
  tem só ~5% de pares com documento bilateral (medido em R-D-07).
- **Exploratório declarado**: as bandas de P6/P7 foram fixadas a partir de
  medição exploratória nome-only sobre a amostra cacheada de D-07 (seed
  19, 2.000 pares): SequenceMatcher sobre nomes normalizados a limiar 0,95
  deu precisão 0,912 e revocação 0,259; os falsos positivos remanescentes
  são pares mesmo-nome/pessoas-distintas (homônimos reais do benchmark,
  sem documento para desempatar). A bateria confirma ou refuta essas
  bandas em **amostras novas**.
- **Match fuzzy é hipótese, não fato** — por isso sinal separado
  (`sanctioned_name_match`, score = similaridade computada), sempre
  sujeito à triagem humana (O10). O sinal factual `sanctioned_supplier`
  (D-06) permanece intocado e tem prioridade: fornecedor com match exato
  de documento não gera sinal fuzzy para a mesma sanção.
- **O benchmark é multilíngue**; o caso de uso real (CEAF × fornecedores
  brasileiros) é monolíngue em português. A precisão medida no OS Pairs é
  uma **banda inferior conservadora** para o regime real — declarado, não
  assumido como igual.
- **Fora de escopo**: match de sócios/representantes (grafo) contra
  sanções — o CEAF no grafo (pessoa expulsa como sócia de fornecedor) é
  habilitado por esta fatia mas tem PR próprio; PEPs/OpenSanctions
  (`yente`) seguem fora; merge físico de entidades segue fora.
- **Vigência CEAF**: o payload não traz início/fim de vigência — declara-se
  `start_date = dataPublicacao` e `end_date = NULL` (aberta). O efeito
  temporal real da expulsão é filtro editorial, não do score.

## 3. Semântica do matcher fuzzy (declarada)

`screening_fuzzy.py` puro e determinístico, reutilizando
`normalize_name`/`name_similarity` de `detection/entities.py`:

- **Features**: similaridade de nome (`SequenceMatcher` sobre formas
  normalizadas: maiúsculas, sem acentos, tokens ordenados) e match de
  documento mascarado (dígitos visíveis do documento mascarado contidos
  no documento completo do fornecedor — mesma regra de
  `documents_match`).
- **Veto documental**: se a sanção tem documento (completo ou mascarado)
  e o fornecedor tem documento completo e eles **contradizem** (dígitos
  visíveis divergentes, ou documentos completos diferentes), **não há
  sinal**, qualquer que seja a similaridade de nome. Documento ausente de
  um dos lados não é contradição.
- **Regime doc-assistido** (documento mascarado presente e compatível):
  score = **0,6 × name_sim + 0,4 × doc_match**; sinal se score ≥ **0,85**.
- **Regime nome-only** (sem evidência documental em algum dos lados):
  score = **name_sim**; sinal se score ≥ **0,95** (limiar alto
  pré-registrado, justificado pelo exploratório da seção 2).
- **Vigência**: idêntica a PR-D-06 § 3 (inclusiva, `end_date` NULL =
  aberta, sem `start_date` = não computável), avaliada na data de
  assinatura do contrato.
- **Emissão**: um sinal `sanctioned_name_match` por fornecedor × lista
  com ao menos um contrato sob sanção vigente que casou; score = maior
  score entre os matches; `details` com ids das sanções, listas, campos
  que casaram (`name_sim`, `doc_match`) e contagem de contratos. Sem
  deduplicação contra o sinal factual além da regra de prioridade acima.
- Limiares e pesos vivem na config da bateria (`D-06b.json`), nunca só em
  código.

## 4. Desenho

**Fonte CEAF (ingestão)**: entrada `ceaf` no `SOURCE_REGISTRY`
(`fetch_sanctions("ceaf")` — o crawler já é genérico sobre o nome da
lista) e no `ENTITY_NORMALIZER_REGISTRY` (`Sanction.from_ceaf`, payload
`punicao`/`pessoa`/`tipoPunicao`/`orgaoLotacao`), ambos alimentando a
silver `sanctions`, que ganha a coluna **`masked_document`** (string,
ex.: `***435151**`; preenchida só pelo CEAF — CEIS/CNEP seguem com
documento completo em `cnpj`/`cpf`). A spec `weekly_sanctions.yaml`
ganha a fonte `ceaf`.

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros). Fornecedores × sanções plantados:

- **F1** — CEAF: mesmo nome, CPF mascarado compatível → sinal
  (doc-assistido, score 1,0).
- **F2** — CEAF: nome com ruído (acento/caso/ordem de tokens), mesmo CPF
  mascarado → sinal doc-assistido.
- **F3** — homônimo: mesmo nome, CPF mascarado **contraditório** → sem
  sinal (veto).
- **F4** — mesmo CPF mascarado, nomes disjuntos → sem sinal.
- **F5** — fornecedor **sem documento**, nome idêntico → sinal nome-only
  (score = 1,0 ≥ 0,95).
- **F6** — fornecedor sem documento, similaridade de nome na faixa
  [0,85 – 0,95) → sem sinal.
- **F7** — CEIS: documento completo divergente, mesmo nome → sem sinal
  (veto; disciplina factual de D-06 preservada no caminho fuzzy).
- **F8** — candidato fuzzy com sanção **não vigente** na assinatura → sem
  sinal.
- **F9** — fornecedor com match exato de documento na mesma sanção →
  prioridade do factual: nenhum sinal fuzzy para essa sanção.
- **Controle** — 20 fornecedores sem sanção alguma → zero sinais.

**Benchmark real**: amostra reservoir estratificada (1.000 positivos +
1.000 negativos, seed declarada na config, distinta das de D-07/D-07b)
sobre o stream do `pairs-20251209.json.gz`, cacheada em
`results/detect/D-06b/pairs_sample.jsonl` (não commitada). Cada par é
pontuado pelo regime nome-only no limiar 0,95 e comparado ao `judgement`.

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds.

- **P1 — conjunto exato de sinais (sintético).** Exatamente F1, F2 e F5
  sinalizam; F3, F4, F6, F7, F8, F9 e os 20 controles não. *Refutada* com
  qualquer sinal a mais ou a menos.
- **P2 — veto documental.** F3 e F7 sem sinal. *Refutada* se documento
  contraditório com nome idêntico sinalizar.
- **P3 — robustez a ruído de nome.** F2 sinaliza. *Refutada* se acento,
  caso ou ordem de tokens impedirem o sinal com documento compatível.
- **P4 — limiar nome-only.** F5 sinaliza, F6 não. *Refutada* com
  qualquer inversão.
- **P5 — determinismo.** A mesma seed reproduz bit a bit os mesmos
  sinais; execuções distintas divergem em zero casos.
- **P6 — precisão nome-only no benchmark real.** Precisão ≥ **0,85** no
  limiar 0,95 sobre a amostra OS Pairs nova (exploratório na seed 19:
  0,912). *Refutada* abaixo disso → PR-D-06c com a curva
  precisão×limiar publicada.
- **P7 — revocação nome-only no benchmark real (banda).** Revocação entre
  **0,15 e 0,45** (exploratório: 0,259). *Refutada* fora da banda, com a
  medida publicada no R-D-06b.
- **P8 — invariante estrutural (pós-integração, dados reais).** Todo
  sinal `sanctioned_name_match` no gold tem score ≥ limiar do regime que
  o gerou e nenhum tem documento completo do fornecedor contraditório ao
  documento da sanção. Verificado por query após uma run do `detect` com
  a feature; o resultado é anexado ao R-D-06b.

## 6. Controles e invariantes

- Controles internos: F3/F7 (veto), F4/F6 (ausência de falso positivo) e
  os 20 pares disjuntos; no benchmark, os 1.000 negativos amostrados são
  o baseline de precisão.
- Invariante de composição: o sinal fuzzy existe se e somente se
  score(par) ≥ limiar do regime ∧ sanção vigente ∧ sem veto — testável
  recomputando das linhas silver (`details` carrega os campos que
  casaram).
- Invariante de monotonicidade: alterar pesos, limiares, o veto ou
  incluir feature nova (ex.: UF de lotação CEAF ↔ UF do fornecedor) exige
  PR de refinamento (`PR-D-06c`) com a justificativa medida.
- A precisão factual do `sanctioned_supplier` (D-06) não pode ser
  afetada: nenhum código do screening exato muda nesta fatia (guarda:
  bateria D-06 segue verde).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds e P6–P7 satisfeitas
sobre a amostra OS Pairs nova (P8 após a integração). Qualquer refutação
é publicada em `docs/results/R-D-06b.md` com a causa investigada e a
forma corrigida vira `PR-D-06c.md` antes de nova execução. O sucesso
habilita — não autoriza automaticamente — o CEAF no grafo (sancionado
como sócio de fornecedor) e a triagem editorial dos sinais fuzzy (O10).

## 8. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Fonte CEAF**: `ceaf` no `SOURCE_REGISTRY`/`ENTITY_NORMALIZER_REGISTRY`
   + `Sanction.from_ceaf` + coluna `masked_document` na silver
   `sanctions` + fonte na spec `weekly_sanctions.yaml`; testes unitários
   do normalizador (payload real anonimizado como fixture).
2. **Matcher**: `src/capiba/detection/screening_fuzzy.py` puro
   (`fuzzy_sanction_signals(contracts, sanctions)`), testado
   unitariamente (rápido); novo `SignalType.SANCTIONED_NAME_MATCH`.
3. **Integração**: `task_detect` emite o sinal fuzzy junto aos demais
   (best-effort; silver `sanctions` ausente nunca derruba a task).
4. **Bateria**: runner `battery_screening_fuzzy.py` (dispatch
   `"runner": "screening_fuzzy"`) lendo `experiments/detect/D-06b.json`:
   sintético + amostra OS Pairs nome-only (reutiliza o sampling de
   `battery_entities`); teste de regime `@pytest.mark.slow`.
5. **Publicação**: `docs/results/R-D-06b.md` com o veredito (inclusive se
   refutada) e P8 anexado após a run real.

## Revisões

- 2026-08-20: criação (rascunho para revisão humana). Payload CEAF
  sondado ao vivo (`/ceaf?pagina=1`): `punicao.cpfPunidoFormatado`
  mascarado, `punicao.nomePunido`, `tipoPunicao.descricao`,
  `orgaoLotacao.nome`, `fundamentacao[]`, `dataPublicacao`; sem
  início/fim de vigência. Bandas de P6/P7 fixadas por exploratório
  nome-only sobre a amostra D-07 (declarado na seção 2). Escopo
  aprovado na conversa: com CEAF; sinal separado.
