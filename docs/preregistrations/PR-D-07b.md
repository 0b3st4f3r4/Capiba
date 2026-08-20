# PR-D-07b — Recalibração da banda de revocação no OS Pairs (refinamento de D-07)

- **Pré-registro**: refinamento da bateria D-07 (forma corrigida após a
  refutação de P7 — ver `docs/results/R-D-07.md`, seção 2)
- **Criado em**: 2026-08-20
- **Última atualização**: 2026-08-20
- **Status**: registrado, aprovado em 2026-08-20, não executado
- **Alvo**: o mesmo matcher de `src/capiba/detection/entities.py`
  (**sem mudança de semântica, pesos ou limiar**); muda apenas a
  expectativa declarada sobre a revocação no benchmark OpenSanctions
  Pairs, recalibrada pela estrutura medida da amostra.
- **Configuração**: `experiments/detect/D-07b.json` (a criar após
  aprovação — idêntica à D-07, exceto `recall_band` e `id`)

## 1. Pergunta

Mantido o matcher exatamente como validado em D-07 (P1–P6), a revocação
sobre uma **nova amostra** do OS Pairs (seeds distintas das de D-07) cai
na banda estrutural derivada da taxa medida de documentos bilaterais, e a
precisão se mantém ≥ 0,90?

## 2. Regime medido e limitações (obrigatório)

- Refinamento permitido por PR-D-07 § 6 ("alterar pesos, limiar ou
  incluir feature nova exige PR de refinamento") — aqui **não** se altera
  nada disso: é correção de expectativa mal calibrada, com a medição
  publicada (R-D-07 § 2) como justificativa.
- Derivação da banda: em D-07, 4,8% dos positivos tinham documento
  bilateral e 52% deles mergearam ⇒ revocação estrutural ≈ 0,025. A banda
  corrigida cobre variação amostral em torno desse piso estrutural:
  **[0,00 – 0,10]**. Revocação > 0,10 indicaria mudança de distribuição
  do snapshot ou bug de extração; precisão < 0,90 segue refutando.
- **Fora de escopo** (como em D-07): features nome-only para elevar
  revocação (aliases, país, datas) — pertencem ao screening de sanções
  (PR-D-06b), não ao dedupe de sócios RFB; merge físico de vértices;
  snapshot licenciado novo.
- Generalização limitada: a banda vale para este matcher e este snapshot
  (2025-12-09); um snapshot novo pede re-medida.

## 3. Semântica do matcher

Inalterada — ver PR-D-07 § 3. Nenhum peso, limiar ou feature muda.

## 4. Desenho

Idêntico a D-07, com duas diferenças:

- **Seeds de amostragem do OS Pairs distintas**: 3 amostras reservoir
  estratificadas (1.000 positivos + 1.000 negativos) com seeds 23, 37, 41
  (diferentes da seed 19 de D-07, para não calibrar na própria amostra).
- O regime sintético reroda inalterado (P1–P5 têm de seguir exatos —
  guarda contra regressão acidental).

## 5. Predições (numéricas, falsificáveis)

- **P1–P5 (sintético)**: idênticas a PR-D-07 § 5 — exatas nas 5 seeds.
  *Refutadas* com qualquer desvio (implicaria regressão no matcher).
- **P6 — precisão OS Pairs**: ≥ **0,90** em cada uma das 3 amostras.
  *Refutada* abaixo disso.
- **P7b — revocação OS Pairs (banda estrutural)**: entre **0,00 e 0,10**
  em cada uma das 3 amostras. *Refutada* acima de 0,10 (distribuição do
  snapshot mudou ou extração de features com bug — investigar antes de
  qualquer ajuste).
- **P8 — invariante estrutural**: como em PR-D-07 (verificação sobre o
  grafo real, fora deste runner).

## 6. Controles e invariantes

- Os 1.000 negativos por amostra são o baseline de precisão; a taxa de
  documento bilateral por amostra é reportada no summary (sanidade da
  banda: revocação esperada ≈ taxa_bilateral × taxa_merge_condicional).
- Invariantes de composição e monotonicidade de PR-D-07 § 6 seguem
  valendo.

## 7. Critério de encerramento

Bem-sucedida com P1–P5 exatas, P6 e P7b satisfeitas nas 3 amostras. O
sucesso **fecha a calibração do matcher** para o regime RFB (documento
mascarado sempre presente) e confirma a leitura de R-D-07: revocação
baixa no OS Pairs é propriedade do desenho conservador, não defeito.
Qualquer refutação é publicada em `docs/results/R-D-07b.md`.

## 8. Execução

Após aprovação humana:

1. `experiments/detect/D-07b.json` (cópia da D-07 com `recall_band`
   [0,0, 0,10] e seeds de amostragem 23/37/41 — suporte a múltiplas
   amostras no runner, se a config atual só aceita uma).
2. Execução + `docs/results/R-D-07b.md`.

## Revisões

- 2026-08-20: criação (rascunho) como forma corrigida da P7 refutada em
  D-07, com a medição de R-D-07 § 2 como justificativa.
