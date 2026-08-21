# PR-D-10 — Clonagem de editais via similaridade semântica (NLP)

- **Pré-registro**: bateria D-10
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: aprovado; implementação e exploratório executados
  (2026-08-21, ver Revisões). P2 **refutada** no regime sintético nas 5
  seeds — refinamento de adjudicação proposto em **PR-D-10b**
  (2026-08-21, decisão final humana pendente); execução oficial da
  bateria e amostra real (P6b/P8) pendentes
- **Alvo**: sinal `notice_clone` (novo `SignalType` — **inexistente hoje**:
  `signals.py` declara `SEMANTIC_GAP` mas não `NOTICE_CLONE`; a adição é
  passo de implementação deste registro) — clonagem/direcionamento de
  edital entre publicações do mesmo território, computado sobre o corpus
  de diários oficiais do piloto Recife (bronze `querido_diario/files/`),
  com emissão best-effort no `task_detect`. Semântica de referência a
  implementar em `src/capiba/detection/notice_clone.py` (pura, encoder
  injetável); o protótipo `detect_clone` de
  `src/capiba/detection/nlp_operators.py` (cosseno sobre embeddings
  `paraphrase-multilingual-MiniLM-L12-v2`, limiar default 0,85 com
  comparação estrita `>`, sem disciplina de nulos nem veto de reedição) é
  **substituído**, não reutilizado.
- **Configuração**: `experiments/detect/D-10.json` (declarativa, seeds e
  âncoras inclusas; esqueleto criado junto a este registro, sem execução)

## 1. Pergunta

O operador `notice_clone`, na semântica declarada na seção 3 (segmentação,
limiar estrito, veto de reedição e disciplina de nulos pré-registrados),
(i) reproduz **exatamente** os clones plantados no regime sintético —
inclusive as disciplinas "reedição nunca sinaliza" e "minuta padronizada
não sinaliza" —, (ii) segmenta edições reais do piloto com cobertura útil,
e (iii) sustenta precisão editorialmente aceitável numa amostra real
anotada de pares sinalizados?

## 2. Regime medido e limitações (obrigatório)

- Dois regimes: **sintético exato** (offline, padrão D-01..D-09) para a
  semântica e a segmentação, mais **amostra real anotada** do piloto
  Recife para cobertura de segmentação e precisão prática. A anotação
  editorial de pares é verdade de terreno parcial (limitação declarada:
  mede concordância com o anotador, não clonagem comprovada).
- **O texto bronze é a edição inteira do diário**, não o aviso individual.
  É o que a fonte entrega (contrato confirmado em
  `ingestion/crawler_querido_diario.py` e na spec
  `dags/pipelines/daily_querido_diario.yaml`): metadados do endpoint
  `/gazettes` (`territory_id`, `date`, `edition`, `is_extra_edition`,
  `url` do PDF, `txt_url`) mais o **texto plano integral** extraído do
  PDF, sem marcação estrutural, persistido com nome determinístico
  `<territory_id>-<date>-<sha256(url)[:12]>.txt`. A segmentação em
  unidades (avisos/editais/extratos) por marcadores estruturais
  ("EDITAL", "AVISO DE LICITAÇÃO", "EXTRATO", número de processo) é parte
  do desenho e sua taxa de erro é medida (P6); sinais sobre edições
  inteiras não fazem sentido jornalístico. **Não há silver de
  documentos** — a bateria lê os arquivos texto do bronze diretamente.
- **Dependência de acúmulo temporal**: o bronze QD começou a acumular em
  2026-08-18; a janela móvel de 365 dias (§ 3) só se preenche com meses de
  ingestão. P6/P7 são medidos sobre o bronze acumulado na data da execução
  (amostra pequena por desenho, declarada); em produção, a cobertura
  histórica cresce com o acúmulo — limitação declarada, não refutável.
- **Clonagem é indício, não evidência.** Minutas padronizadas (TCU,
  modelos de secretaria, atas de registro de preços) são clones
  legítimos — falso positivo estrutural declarado e medido com controle
  próprio (N3, § 4). O sinal alimenta triagem editorial, nunca acusação
  direta.
- **`semantic_gap` fica fora deste registro**: exige termo de referência ×
  execução contratual, fontes que a plataforma não tem.
  `SignalType.SEMANTIC_GAP` segue sem produtor até que a fonte exista
  (gap crítico 5 de `docs/gaps.md` parcialmente endereçado aqui).
- **Custo computacional**: encode em CPU no pod do Airflow; o corpus
  diário do piloto é de unidades de edições/dia — viável. O backfill
  histórico do QD fica fora deste registro.
- **LGPD**: editais são documentos públicos oficiais; o `details` do sinal
  carrega ids de aviso, datas e similaridade — nenhum dado pessoal além do
  já publicado no diário.

## 3. Semântica do sinal (declarada)

`notice_clone.py` puro e determinístico, sobre os arquivos texto do
bronze, com encoder injetável (default
`sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2`, o mesmo do
protótipo, pinado na config):

- **Unidade de análise**: aviso segmentado da edição, com no mínimo
  **200 caracteres** de texto corrido (abaixo disso, metadado puro — não
  analisável, excluído e contado).
- **Identidade do aviso**: id derivado deterministicamente de
  (território, data da edição, edição, índice do segmento) — a chave de
  triagem é o hash do par ordenado de ids.
- **Par candidato**: aviso novo × avisos históricos do **mesmo
  território** (IBGE), em janela móvel de **365 dias**. Cross-território
  fica fora do v1: minutas estaduais/nacionais padronizadas dominariam
  (refinamento futuro exige PR próprio).
- **Veto de reedição**: mesmo número de processo extraível nos dois
  avisos → retificação/republicação, **nunca sinaliza**. Extração por
  padrões declarados ("Processo nº/n.", formato NUP
  `NNNNN.NNNNNNNN/NNNN-NN` e variações pontuadas); processo ausente de um
  dos lados não é veto nem evidência.
- **Emissão**: `notice_clone` quando a similaridade cosseno máxima do par
  é **estritamente maior** que `DETECTION_NOTICE_CLONE_THRESHOLD`
  (placeholder **0,85** — comparação `>` alinhada ao protótipo e a D-09;
  mudança de limiar ou de comparação exige PR-D-10b). Score =
  `round(max_similarity, 4)`. Um sinal por par; `details` com ids dos
  avisos, datas das edições e similaridade.
- **Disciplina de nulos**: aviso abaixo do mínimo de caracteres, edição
  sem segmento válido ou encoder indisponível → par não computável, nunca
  sinaliza (o protótipo não tinha essa disciplina).
- Limiares e parâmetros vivem na config (`D-10.json`), nunca só em código.

## 4. Desenho

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros). Corpus base: 60 avisos gerados por templates
com slots (órgão, objeto, valores, datas), distribuídos em edições
sintéticas com os marcadores estruturais declarados. Casos plantados
(contagens na config):

- **N0** — 2 pares de **cópia exata** (bit a bit): o aviso histórico é
  idêntico ao novo → **âncora exata**: cosseno de um vetor consigo mesmo
  é 1,0, score 1,0000, rank 1.
- **N1** — 8 clones **verbais**: cópia do aviso-base com troca apenas de
  entidade/empresa/datas/valores (perturbação mínima).
- **N2** — 8 clones **parafraseados**: reordenação de parágrafos +
  sinônimos + abreviações (perturbação forte).
- **N3** — 8 pares de **minuta padronizada**: mesma estrutura formal,
  objeto e valores distintos (controle de falso positivo estrutural).
- **N4** — 4 **reedições**: mesmo número de processo, texto alterado
  (veto — nunca sinalizam).
- **N5** — restante do corpus: avisos de domínios distintos (saúde ×
  obras × TI), controle de ausência de sinal.
- **N6** — 1 edição sintética montada com **12 avisos plantados** e
  marcadores → a segmentação deve recuperar **exatamente 12 unidades**
  (âncora exata da segmentação).

**Exploratório declarado** (mesma disciplina de PR-D-06b; procedimento
fixado aqui, resultados documentados em Revisões **antes** da execução
oficial): (i) sobre o corpus sintético da seed 13 — curva revocação ×
limiar de N2 e taxa de falso positivo de N3/N5, fixando as bandas de
P3/P4; (ii) sobre uma amostra real cacheada de edições do piloto (seed de
amostragem 97, até 30 edições) — cobertura da segmentação e distribuição
de similaridades, fixando/confirmando a banda de P6b; (iii) anotação
piloto editorial dos até 20 pares reais mais similares, fixando a banda
de P8. Scores dependentes do encoder não são computáveis a priori sem
executar o modelo — por isso bandas por exploratório, não âncoras.

**Amostra real (oficial)**: edições de Recife acumuladas no bronze até a
data da execução (≥ a amostra do exploratório), amostradas com a seed 97
declarada na config. **Todos** os pares sinalizados são anotados pelo
editor com rubrica binária declarada: "clone" = o par compartilha
estrutura **e** conteúdo substancial além dos campos padronizados de
minuta (objeto, requisitos e prazos em redação quase literal); minuta
legítima e demais casos = não-clone. Precisão = fração confirmada.

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds. Âncoras exatas com desvio tolerado de 1e-9 (antes do
arredondamento declarado do score).

- **P1 — âncora de duplicata exata (sintético, exato).** Todo par N0
  sinaliza com score **1,0000** e rank 1 entre os históricos. *Refutada*
  com desvio > 1e-9 ou rank ≠ 1.
- **P2 — clones verbais (sintético, banda declarada).** Todo par N1 é
  recuperado com score ≥ 0,95 e rank 1 (o encoder multilingue trata troca
  de entidades como quase-identidade). *Refutada* com qualquer falha.
- **P3 — clones parafraseados (sintético, banda).** Revocação de N2 no
  limiar placeholder ≥ banda fixada pelo exploratório. *Refutada* abaixo
  dela → PR-D-10b com a curva revocação × limiar publicada.
- **P4 — disciplina de falsos positivos (sintético, banda).** Taxa de
  pares N3/N5 sinalizados ≤ banda fixada pelo exploratório. *Refutada*
  acima dela (minutas dominam → o sinal exige filtro estrutural novo).
- **P5 — veto de reedição (sintético, exato).** Zero sinais sobre os
  pares N4. *Refutada* com qualquer sinal.
- **P6 — segmentação.** (a) **sintético, exato**: N6 recupera exatamente
  12 unidades; *refutada* com qualquer outra contagem. (b) **real,
  piloto**: ≥ 90% das edições da amostra geram ao menos uma unidade ≥ 200
  caracteres, e nenhuma unidade ultrapassa 50 KB (falha de split);
  *refutada* fora disso — a segmentação é pré-condição do sinal.
- **P7 — determinismo (sintético, exato).** Mesma seed reproduz bit a bit
  pares e scores; execuções distintas divergem em zero casos. *Refutada*
  com qualquer divergência.
- **P8 — precisão editorial (real, amostra anotada).** Precisão dos
  pares sinalizados ≥ banda fixada pelo exploratório/anotação piloto.
  *Refutada* abaixo dela.
- **P9 — invariante estrutural (pós-integração).** Todo sinal
  `notice_clone` no gold tem score > limiar do regime, par dentro do
  mesmo território e zero sinais sobre pares com mesmo número de
  processo. Verificado por query após uma run do `detect`.

## 6. Controles e invariantes

- Controles internos: N3 (minuta padronizada) e N5 (domínios distintos)
  são os baselines de falso positivo; N4 é o baseline do veto; N0 e N6
  são as âncoras exatas (score unitário, contagem de segmentos).
- Invariante de composição: o sinal existe se e somente se o par é
  mesmo-território, dentro da janela, sem veto de processo e com
  similaridade máxima estritamente acima do limiar — testável recomputando
  a partir dos arquivos bronze (`details` carrega os campos que
  fundamentam).
- O placeholder 0,85 vem do protótipo (`detect_clone`); qualquer
  recalibração, mudança de comparação, de encoder, de janela ou inclusão
  de cross-território exige PR de refinamento (`PR-D-10b`) com a
  justificativa medida (monotonicidade documental).
- Embeddings não entram no grafo nem no CRI; o sinal é autônomo na
  triagem (chave estável do par).
- Os sinais existentes não podem ser afetados (guarda: baterias
  D-01..D-09 seguem verdes).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1/P2/P5/P6a/P7 exatas nas 5 seeds, P3/P4
dentro das bandas declaradas e P6b/P8 satisfeitas no piloto (P9 após a
integração). Qualquer refutação é publicada em `docs/results/R-D-10.md`
com a causa investigada, e a forma corrigida vira `PR-D-10b.md` antes de
nova execução. O sucesso habilita — não autoriza automaticamente — a
emissão best-effort no `task_detect` e a expansão para outros
territórios QD.

## 8. Execução (após aprovação humana deste registro)

1. **Segmentação**: `src/capiba/ingestion/gazette_segments.py` (marcadores
   estruturais + extração de número de processo), testada unitariamente
   sobre a edição sintética N6 e edições reais cacheadas.
2. **Sinal**: `src/capiba/detection/notice_clone.py` puro (encoder
   injetável, veto de reedição, disciplina de nulos) +
   `SignalType.NOTICE_CLONE`; testes rápidos. O protótipo `detect_clone`
   de `nlp_operators.py` é removido ou reduzido a wrapper deprecado
   (decisão de implementação, registrada em Revisões).
3. **Exploratório**: procedimento da seção 4 executado e documentado em
   Revisões, fixando as bandas de P3/P4/P6b/P8.
4. **Bateria**: runner `battery_notice_clone.py` (dispatch
   `"runner": "notice_clone"` em `scripts/detect_battery.py`) lendo
   `experiments/detect/D-10.json`, saída em `results/detect/D-10/`;
   teste de regime `@pytest.mark.slow`.
5. **Amostra real + anotação editorial** (P6b/P8); integração best-effort
   no `task_detect` somente após o veredito, reutilizando pacote de
   evidências (`evidence/packages.py`) e triagem (`db/triage.py`); P9
   verificado por query e anexado ao R-D-10.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana), após a primeira
  run real da `daily_querido_diario` (edição de Recife de 2026-08-18 com
  texto persistido no bronze).
- 2026-08-21: reescrita completa, de rascunho a pré-registro pronto para
  execução. Verificações em código: `SignalType` **não** tem
  `NOTICE_CLONE` (adição virou passo explícito de implementação);
  `detect_clone` usa comparação **estrita** `>` e não tem disciplina de
  nulos nem veto — declarado como protótipo a substituir, não referência
  reutilizável; o contrato da fonte foi confirmado no crawler e na spec
  (metadados `/gazettes` + texto plano integral da edição, nome
  determinístico, sem silver de documentos). Mudanças de desenho em
  relação ao rascunho: comparação de limiar declarada **estrita**
  (o rascunho dizia ≥ — alinhamento com o protótipo e com D-09); âncoras
  exatas **N0** (cópia bit a bit → score 1,0000) e **N6** (segmentação
  de edição sintética → exatamente 12 unidades) adicionadas, cumprindo a
  regra de âncoras exatas sempre que computáveis a priori; procedimento
  do exploratório tornado declarativo (seed 13 no sintético, seed de
  amostragem 97 na amostra real, anotação piloto de até 20 pares);
  rubrica binária de anotação editorial declarada; dependência de
  acúmulo temporal do bronze declarada como limitação. Esqueleto
  `experiments/detect/D-10.json` criado junto, sem execução. Bandas de
  P3/P4/P6b/P8 seguem a fixar pelo exploratório documentado antes da
  execução oficial.
- 2026-08-21: **implementação + exploratório executados** (após aprovação
  humana). Implementação: `SignalType.NOTICE_CLONE` adicionado;
  `src/capiba/ingestion/gazette_segments.py` (segmentação por marcadores
  estruturais normalizados + extração de número de processo NUP/rotulado,
  normalizado a dígitos); `src/capiba/detection/notice_clone.py` (puro,
  encoder injetável, comparação estrita, veto de reedição, disciplina de
  nulos, janela móvel, identidade/chave de triagem determinísticas);
  settings `DETECTION_NOTICE_CLONE_*` em `config.py`; runner
  `src/capiba/detection/battery_notice_clone.py` (gerador sintético
  N0–N6, avaliação P1–P7). Decisão de implementação (§ 8, passo 2): o
  protótipo `detect_clone` de `nlp_operators.py` foi **removido** (sem
  chamadores de produção; substituído por `notice_clone.py`), não
  reduzido a wrapper. Decisão de desenho do gerador: os pares **N0**
  (cópia bit a bit) são plantados **sem número de processo extraível** —
  uma cópia idêntica carregaria o mesmo processo e cairia no veto de
  reedição (a disciplina do veto é coberta por N4); "processo ausente não
  é veto nem evidência" (§ 3). **Exploratório (seed 13, encoder pinado
  real, CPU)**: âncora N0 confirmada (similaridade 1,0, rank 1, nos dois
  pares); âncora N6 confirmada (exatamente 12 unidades); curva revocação
  × limiar de N2: 1,00 até 0,80; 0,875 em 0,85; 0,625 em 0,90; 0,25 em
  0,95 → **banda de P3 fixada em 0,75** (piso conservador sob a medida);
  curva de FP (N3/N5): 0,79 em 0,60; 0,0504 em 0,85; 0,0044 em 0,95 →
  **banda de P4 fixada em 0,10** (teto com folga ~2×). Medidas brutas em
  `results/detect/D-10/exploratory_seed_13.json`; bandas registradas em
  `D-10.json` (`bands`). Verificação de desenvolvimento das 5 seeds
  (encoder real; run de implementação, não a execução oficial): P1, P3,
  P4, P5, P6a e P7 **verdes em todas as seeds** (revocação N2 por seed:
  0,875/0,875/0,875/0,75/1,0; FP: 0,0488–0,0649); **P2 refutada em todas
  as seeds** — scores N1 entre 0,86 e 0,998 (vários < 0,95) e ranks até
  4: a troca de entidade/órgão (string longa, duas ocorrências no texto)
  derruba a similaridade abaixo da banda declarada de 0,95, e avisos
  estruturalmente próximos (mesmo template de introdução, objeto
  parcialmente sobreposto) superam o par plantado em alguns casos. A
  refutação de P2 segue a disciplina do § 7: adjudicação humana e forma
  corrigida em **PR-D-10b** (revisar a banda/score mínimo de P2 ou o
  critério de rank) antes da execução oficial; o teste de regime slow
  pinna o veredito atual. **P6b/P8 não executados nesta fase** (exigem a
  amostra real do bronze e a anotação editorial — bandas pendentes).
  **Wiring pendente**: dispatch `"notice_clone"` em
  `scripts/detect_battery.py` (arquivo compartilhado, não editado nesta
  frente). Integração best-effort no `task_detect` segue fora desta fase
  (§ 8, passo 5: somente após o veredito).
- 2026-08-21: **refinamento PR-D-10b criado** (`PR-D-10b.md` +
  `experiments/detect/D-10b.json`), adjudicando a refutação de P2:
  direção proposta (a) — re-calibração da banda para o regime real do
  encoder (P2b: sinal + rank ≤ 4, ancorado nas medidas brutas do
  exploratório seed 13 e na verificação das 5 seeds; limiar de sinal
  0,85 e semântica do sinal intocados); direção (b) registrada como
  gatilho de PR-D-10c. **Decisão final humana pendente**; este registro
  segue valendo para as demais predições (P1, P3–P9).
