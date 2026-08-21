# PR-D-10 — Clonagem de editais via similaridade semântica (NLP)

- **Pré-registro**: bateria D-10
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para revisão humana, não aprovado, não executado
- **Alvo**: sinal `notice_clone` (clonagem de edital/aviso entre
  publicações do mesmo território), computado sobre o corpus de diários
  oficiais do piloto Recife (bronze `querido_diario/files/`), com
  emissão best-effort no `task_detect`. Operador de referência:
  `detect_clone` de `src/capiba/detection/nlp_operators.py` (cosseno
  sobre embeddings `paraphrase-multilingual-MiniLM-L12-v2`).
- **Configuração**: `experiments/detect/D-10.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

O operador `detect_clone` recupera clones plantados de avisos de
licitação com precisão e revocação úteis no regime sintético, e mantém
precisão editorialmente aceitável numa amostra real anotada do piloto?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético exato** (offline) para a semântica, mais **amostra
  real anotada** do piloto Recife para precisão prática — a anotação
  editorial de pares é verdade de terreno parcial (limitação declarada:
  mede concordância com o anotador, não clonagem comprovada).
- **O texto bronze é a edição inteira do diário** (~150 KB), não o
  aviso individual. A segmentação em unidades (avisos/editais/extratos)
  por marcadores estruturais ("EDITAL", "AVISO DE LICITAÇÃO", número de
  processo) é parte do desenho e sua taxa de erro é medida (P6); sinais
  sobre edições inteiras não fazem sentido jornalístico.
- **Clonagem é indício, não evidência.** Minutas padronizadas (TCU,
  modelos de secretaria) são clones legítimos — falso positivo
  estrutural declarado e medido com controle próprio (§ 4). O sinal
  alimenta triagem editorial, nunca acusação direta.
- **`semantic_gap` fica fora deste registro**: exige termo de
  referência × execução contratual, fontes que a plataforma não tem.
  `SignalType.SEMANTIC_GAP` segue sem produtor até que a fonte exista
  (gap crítico 5 de `docs/gaps.md` parcialmente endereçado aqui).
- Custo computacional: encode em CPU no pod do Airflow; o corpus diário
  do piloto é de unidades de edições/dia — viável; o backfill histórico
  do QD fica fora deste registro.

## 3. Semântica do sinal (declarada)

- **Unidade de análise**: aviso segmentado da edição, com no mínimo 200
  caracteres de texto corrido (abaixo disso, metadado puro — não
  analisável).
- **Par candidato**: aviso novo × avisos históricos do **mesmo
  território** (IBGE), em janela móvel de 365 dias. Cross-território
  fica fora: minutas estaduais/nacionais padronizadas dominariam.
- **Veto de reedição**: mesmo número de processo extraível nos dois
  avisos → retificação/republicação, nunca sinaliza.
- **Emissão**: `notice_clone` quando a similaridade cosseno máxima do
  par ≥ `DETECTION_NOTICE_CLONE_THRESHOLD` (placeholder **0,85** —
  mudança exige PR-D-10b); score = similaridade máxima (round 4 casos).
  Chave de triagem: hash do par ordenado de ids dos avisos.
- Semântica de referência a implementar em
  `src/capiba/detection/notice_clone.py` (puro, injetável); o
  `detect_clone` atual de `nlp_operators.py` é protótipo sem disciplina
  de nulos nem veto — a referência nova o substitui.

## 4. Desenho

População sintética por seed (5 seeds; estrutura idêntica, seed
randomiza campos neutros). Corpus base: 60 avisos gerados por templates
com slots (órgão, objeto, valores, datas). Casos plantados:

- **N1** — 8 clones **verbais**: cópia do aviso-base com troca apenas de
  entidade/empresa/datas/valores (perturbação mínima).
- **N2** — 8 clones **parafraseados**: reordenação de parágrafos +
  sinônimos + abreviações (perturbação forte).
- **N3** — 8 pares de **minuta padronizada**: mesma estrutura formal,
  objeto e valores distintos (controle de falso positivo estrutural).
- **N4** — 4 **reedições**: mesmo número de processo, texto alterado
  (veto — nunca sinalizam).
- **N5** — restante: avisos de domínios distintos (saúde × obras × TI),
  controle de ausência de sinal.

Exploratório declarado (mesma disciplina de PR-D-06b): as bandas de
P2/P3/P6 são fixadas por medição exploratória sobre o corpus sintético
gerado e uma amostra real cacheada de edições de Recife, documentadas
na seção de Revisões **antes** da execução oficial da bateria.

## 5. Predições (numéricas, falsificáveis)

- **P1 — clones verbais (sintético, exato).** Todo par N1 é recuperado
  com score ≥ 0,95 e rank 1 entre os históricos. *Refutada* com
  qualquer falha (o encoder multilingue trata troca de entidades como
  quase-identidade).
- **P2 — clones parafraseados (sintético, banda).** Revocação de N2 no
  limiar placeholder ≥ banda fixada pelo exploratório. *Refutada*
  abaixo dela → PR-D-10b com a curva revocação×limiar publicada.
- **P3 — disciplina de falsos positivos (sintético, banda).** Taxa de
  pares N3/N5 sinalizados ≤ banda fixada pelo exploratório. *Refutada*
  acima dela (minutas dominam → o sinal exige filtro estrutural novo).
- **P4 — veto de reedição (sintético, exato).** Zero sinais sobre os
  pares N4. *Refutada* com qualquer sinal.
- **P5 — determinismo (sintético, exato).** Mesma seed reproduz bit a
  bit pares e scores; execuções distintas divergem em zero casos.
  *Refutada* com qualquer divergência.
- **P6 — segmentação (real, piloto).** ≥ 90% das edições de Recife da
  amostra geram ao menos uma unidade ≥ 200 caracteres, e nenhuma unidade
  ultrapassa 50 KB (falha de split). *Refutada* fora disso — a
  segmentação é pré-condição do sinal.
- **P7 — precisão editorial (real, amostra anotada).** Precisão dos
  pares sinalizados na amostra anotada ≥ banda fixada pelo exploratório.
  *Refutada* abaixo dela.
- **P8 — invariante estrutural (pós-integração).** Todo sinal
  `notice_clone` no gold tem score ≥ limiar do regime e par dentro do
  mesmo território; zero sinais sobre pares com mesmo número de
  processo. Verificado por query após uma run do `detect`.

## 6. Controles e invariantes

- Controles internos: N3 (minuta padronizada) e N5 (domínios distintos)
  são os baselines de falso positivo; N4 é o baseline do veto.
- O placeholder 0,85 vem do protótipo (`detect_clone`); qualquer
  recalibração exige PR-D-10b (monotonicidade documental).
- Embeddings não entram no grafo nem no CRI; o sinal é autônomo na
  triagem (chave estável do par).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1/P4/P5 exatas nas 5 seeds, P2/P3 dentro
das bandas declaradas e P6–P8 satisfeitas no piloto. Qualquer refutação
é publicada em `docs/results/R-D-10.md` com a causa investigada, e a
forma corrigida vira `PR-D-10b.md` antes de nova execução. O sucesso
habilita — não autoriza automaticamente — a emissão best-effort no
`task_detect` e a expansão para outros territórios QD.

## 8. Execução (após aprovação humana)

1. Segmentação `src/capiba/ingestion/gazette_segments.py` (marcadores
   estruturais + número de processo), testada unitariamente sobre
   edições reais cacheadas.
2. Semântica `src/capiba/detection/notice_clone.py` (pura, encoder
   injetável; veto de reedição), `SignalType.NOTICE_CLONE`, testes
   rápidos.
3. Bateria `experiments/detect/D-10.json` + runner (dispatch em
   `scripts/detect_battery.py`), saída em `results/detect/D-10/`;
   teste de regime `@pytest.mark.slow`.
4. Amostra real do piloto + anotação editorial (P6/P7); integração
   best-effort no `task_detect` somente após veredito, com pacote de
   evidências (O9) e triagem (O10) reutilizados.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana), após a primeira
  run real da `daily_querido_diario` (edição de Recife de 2026-08-18 com
  texto persistido no bronze). Bandas de P2/P3/P6/P7 a fixar por
  exploratório documentado antes da execução.
