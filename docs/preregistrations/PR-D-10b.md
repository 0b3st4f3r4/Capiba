# PR-D-10b — Refinamento da predição P2 (clones verbais) do notice_clone

- **Pré-registro**: bateria D-10b (refinamento de `PR-D-10.md` — P2
  **refutada** na verificação de desenvolvimento das 5 seeds, Revisões de
  2026-08-21; execução oficial da bateria ainda pendente)
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para adjudicação humana — direção (a) proposta com
  justificativa (seção 3); a decisão final é humana. **Nenhuma execução
  neste refinamento**
- **Alvo**: a predição P2 do sinal `notice_clone` (clones verbais, caso
  N1) — re-calibrada para o regime real do encoder pinado. A semântica do
  sinal é **intocada**: limiar estrito 0,85, veto de reedição, disciplina
  de nulos, janela e identidade determinística seguem como no PR-D-10 § 3
- **Configuração**: `experiments/detect/D-10b.json` (nova; `D-10.json`
  preservado intacto como registro histórico da forma refutada —
  doutrina: resultados negativos são resultados)

## 1. Pergunta

A banda de P2 recalibrada para o regime real do encoder — sinal emitido
(score > 0,85, inalterado) **e** rank ≤ 4 entre os históricos candidatos
— reproduz os clones verbais plantados (N1) nas 5 seeds, sem afrouxar as
demais predições (P1, P3–P7), destravando a execução oficial da bateria?

## 2. Regime medido e limitações (obrigatório)

- Mesmo regime sintético exato do PR-D-10 (offline, 5 seeds, encoder
  pinado real em CPU). Nenhum dado real novo entra neste refinamento;
  P6b/P8 (amostra real anotada do piloto Recife) seguem pendentes no
  escopo do PR-D-10.
- **Limitação central — calibração auto-referente.** A banda de P2b é
  ancorada nas mesmas 5 seeds que a execução oficial roda, com o mesmo
  encoder pinado: a execução oficial é uma **reprodução** dessas medidas
  (P7, determinismo bit a bit, verde na verificação de desenvolvimento),
  não uma amostra nova. P2b, portanto, **não mede generalização** — mede
  que a expectativa declarada corresponde ao regime observado do encoder.
  A generalização editorial só é medida por P6b/P8.
- O que P2b **não** prova: rank ≤ 4 não é garantia de precisão editorial
  (medida por P8) nem de disciplina de falsos positivos (medida por P4);
  e clones verbais **reais** mais perturbados que os sintéticos podem
  cair abaixo do limiar de emissão 0,85 e simplesmente não sinalizar —
  perda de revocação declarada, não refutável neste regime.
- **Lacuna de evidência declarada**: a verificação de desenvolvimento das
  5 seeds persistiu medidas brutas apenas da seed 13
  (`results/detect/D-10/exploratory_seed_13.json`); a faixa agregada
  0,86–0,998 e o rank máximo 4 constam das Revisões do PR-D-10
  (2026-08-21). A execução do D-10b persiste as medidas brutas de todas
  as seeds em `results/detect/D-10b/`, corrigindo a lacuna.

## 3. Adjudicação da refutação — direção escolhida

A refutação de P2 (PR-D-10, Revisões de 2026-08-21) levantou duas
direções:

- **(a) Re-calibrar a banda** para o regime real do encoder (rank ≤ k e/ou
  banda de score fixada pelo exploratório), mantendo o limiar de sinal
  0,85.
- **(b) Endurecer o caso/operador** — mascaramento de entidades
  (órgão/unidade) antes do embedding, ou comparação por rank relativo —
  mudança de semântica do sinal, mais cara.

**Escolha proposta: (a).** Justificativa técnica:

1. **A falha medida é de expectativa, não de semântica.** Em todas as 5
   seeds, **todos** os pares N1 sinalizaram (score > 0,85) — o sinal
   recupera os clones verbais. O que caiu foi a banda a priori
   (score ≥ 0,95 e rank 1), derivada da suposição "troca de entidade ≈
   quase-identidade", que o regime real do encoder não confirma: a troca
   de strings longas de órgão/entidade (duas ocorrências no texto)
   derruba o cosseno abaixo de 0,95, e avisos estruturalmente próximos
   (mesmo template de introdução, objeto parcialmente sobreposto)
   superam o par plantado em alguns casos.
2. **O rank não faz parte do sinal emitido.** A triagem editorial consome
   pares sinalizados com score; o rank é métrica de recuperação no
   sintético. Endurecer a semântica (b) para satisfazer uma métrica de
   avaliação inverteria a ordem correta.
3. **Custo e blast radius.** (b) exige pipeline de mascaramento com taxa
   de erro própria, invalida as âncoras N0/N1 existentes, pede novo
   exploratório e reabre P1/P3/P4 — desproporcional antes de evidência de
   que (a) perde sentido editorial.
4. **Gatilho de escalonamento declarado**: se a execução do D-10b
   refutar P2b, ou se P8 (precisão editorial real) mostrar a fila de
   triagem dominada por não-clones, a direção (b) vira **PR-D-10c** com a
   justificativa medida.

**A decisão final é humana** (disciplina do PR-D-10 § 7): este registro
propõe (a); a adjudicação é registrada em Revisões antes de qualquer
execução.

## 4. Forma corrigida da predição (P2b)

- **P2b — clones verbais (sintético, banda recalibrada).** Todo par N1
  (i) **sinaliza** — similaridade estritamente acima do limiar 0,85,
  inalterado — e (ii) tem **rank ≤ 4** entre os históricos candidatos do
  aviso novo (rank = 1 + estritamente mais similares, definição do
  runner). *Refutada* com qualquer par N1 não sinalizado ou rank > 4 em
  qualquer uma das 5 seeds.
- **Âncoras empíricas da banda** (base: medidas brutas do exploratório,
  `results/detect/D-10/exploratory_seed_13.json`, seed 13, encoder pinado
  real): scores N1 exatos 0,8892767; 0,9016312; 0,9112767; 0,9166367;
  0,9431736; 0,9628610; 0,9649405; 0,9689297 — mínimo **0,8893**, todos
  acima do limiar 0,85; ranks 2, 1, 1, 1, 1, 3, 2, 1 — máximo **3**. A
  verificação de desenvolvimento das 5 seeds (Revisões do PR-D-10,
  2026-08-21) observou scores 0,86–0,998 e **rank máximo 4**. A banda
  rank ≤ 4 é ancorada **exatamente** no máximo observado — sem folga
  adicional, porque a execução oficial reproduz essas mesmas seeds bit a
  bit (P7 verde); folga simulada seria cosmética, não robustez.
- **O componente score ≥ 0,95 é removido**, não relaxado para outra banda:
  o limiar de emissão 0,85 (inalterado) já é o piso de score de todo
  sinal; um segundo limiar intermediário (ex.: 0,86) não teria função
  editorial nem de avaliação. O score segue reportado como diagnóstico
  nos resultados brutos, fora dos critérios.
- **Demais predições inalteradas**: P1 (âncora N0), P3 (banda 0,75), P4
  (banda 0,10), P5 (veto), P6a (âncora de segmentação), P7
  (determinismo) — todas verdes na verificação de desenvolvimento e
  reavaliadas na execução do D-10b. P6b/P8 seguem como declaradas no
  PR-D-10, pendentes da amostra real.

## 5. O que muda (declarado; implementação após aprovação humana)

- **Config**: novo `experiments/detect/D-10b.json` — cópia declarativa do
  `D-10.json` com `id: "D-10b"`; caso N1 com
  `expected: {"signal": true, "rank_max": 4}` (no lugar de
  `min_score: 0.95` e `rank: 1`); `bands.p2_max_rank: 4` com a
  justificativa ancorada. Seeds inalteradas (13, 29, 41, 59, 71); limiar
  0,85 inalterado; bandas de P3/P4 inalteradas. `D-10.json` **intocado**.
- **Runner**: `battery_notice_clone.py` — apenas a **avaliação** de P2
  passa a ler `rank_max` (refuta com ausência de sinal ou rank > 4);
  `min_score` deixa de ser exigido para N1. `notice_clone.py` (semântica
  do sinal) **intocado**; âncoras e demais avaliações idem.
- **Saída**: `results/detect/D-10b/` com as medidas brutas por seed —
  scores e ranks de todos os pares N1 das 5 seeds (seção 2, lacuna de
  evidência); o teste de regime `@pytest.mark.slow` passa a pinnar o
  veredito esperado do D-10b.
- **Wiring pendente** (herdado do PR-D-10): dispatch `"notice_clone"` em
  `scripts/detect_battery.py`.

## 6. Critério de encerramento

D-10b **bem-sucedida** com P2b satisfeita nas 5 seeds e P1/P3/P4/P5/P6a/P7
confirmadas inalteradas. Refutação de P2b → **PR-D-10c** na direção (b),
com a justificativa medida publicada em `docs/results/R-D-10b.md`. O
sucesso do D-10b destrava a execução oficial remanescente do PR-D-10
(P6b/P8 — amostra real + anotação editorial; P9 pós-integração) —
habilita, não autoriza automaticamente.

## Revisões

- 2026-08-21: criação. Adjudica proposta da refutação de P2 (PR-D-10,
  Revisões de 2026-08-21): direção **(a)** — re-calibração da banda para
  o regime real do encoder (sinal + rank ≤ 4, ancorado nas medidas brutas
  do exploratório seed 13 e na verificação das 5 seeds), limiar de sinal
  0,85 inalterado, semântica do sinal intocada; direção (b) registrada
  como gatilho de PR-D-10c. `experiments/detect/D-10b.json` criado junto.
  **Decisão final humana pendente; nenhuma execução neste refinamento.**
