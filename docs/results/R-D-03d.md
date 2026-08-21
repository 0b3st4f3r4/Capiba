# R-D-03d — Emissão ranqueada top-K do collusion_network: viável operacional e editorialmente (bem-sucedida)

- **Resultado**: bateria D-03d (pré-registro
  `docs/preregistrations/PR-D-03d.md`, refinamento da D-03c — veredito
  `refuted` (hipótese de escala) em `docs/results/R-D-03c.md`; cadeia
  D-03 → D-03b → D-03c)
- **Executada em**: 2026-08-21
- **Regime**: Partes A/A-stress/C sintéticas sobre ArangoDB de bateria
  descartável (`capiba_d03d_battery`, criado e derrubado pelo runner) +
  Parte B, varredura **read-only** sobre o grafo real em janela de
  congelamento (693.261 arestas `won`, estável antes/depois da medição;
  340.331 pares (comprador, fornecedor) exportados; cobertura siafi 100%)
- **Saída bruta**: `results/detect/D-03d/` (5 seeds + `real_sweep.json` +
  `summary_synthetic.json` + `summary.json`)
- **Veredito**: **success** — T1–T9 `success`: a emissão ranqueada
  **top-K = 500 declarada** sobre a derivação bloqueada da D-03c satisfaz
  simultaneamente os limites operacionais (projeção < 1M, pico de heap
  < 256 MiB, varredura < 600 s) e o orçamento editorial (backlog ≤ 500,
  incremento ≤ 20/dia) no ponto selecionado **(5,2)**, com equivalência
  de prefixo bit a bit e determinismo byte-idêntico

## 1. Números

Partes A/A-stress/C (sintéticas, âncoras exatas, 5 seeds — população
idêntica à D-03b/D-03c, 59 contratos/seed, mais o comprador stress
`BIG-B` com 200 fornecedores exclusivos ×3 vitórias):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| T1 — truncamento ativo, desempate `wins_sum` | (2,2) K=1 → `[{X1,X2}]`; `qualified_count` = 2; cobertura 0,5 | exato nas 5 seeds | success |
| T2 — truncamento ativo, desempate `buyer_count` | (3,1) K=1 → `[{X1,X2}]`; `qualified_count` = 5 | exato nas 5 seeds | success |
| T3 — equivalência de prefixo e evidência | K=500 vacuoso ≡ conjunto completo ordenado nos 4 pontos; pacote `graph_batch` com `top_k` reproduz `match = true`; adulterado → `integrity = false`, `match = false`; pacotes legados (sem `top_k`) reproduzem inalterados | exato nas 5 seeds | success |
| T4 — âncoras de escala (stress) | projeção bloqueada (3,2) = 2; emissão (3,2) = `[{X1,X2}]`; emissão (3,1) top-500 = exatamente 500 sobre 19.905 qualificados, primeiro `{X1,X2}`; varredura stress < 60 s | exato nas 5 seeds (stress < 4 s/seed) | success |

Parte B (varredura real, 2026-08-21, janela de congelamento verificada):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| T5 — existência de ponto operável | ao menos um ponto da grade com projeção bloqueada < 1.000.000; primeiro na ordem pré-registrada | **(5,2)** selecionado (610.960); (3,2) = 2.584.123, (4,2) = 1.163.536 e (3,3) = 1.368.892 reprovados | success |
| T6 — viabilidade operacional | varredura < 600 s; pico de heap < 256 MiB; projeção = incidências | varredura **63,5 s**; pico **90,5 MiB**; dupla contagem exata (610.960 = 610.960) | success |
| T7 — orçamento editorial | emitidos ≤ 500; incremento ≤ 20/dia | **500 emitidos** (exato, por construção — verificado); incremento **0,4333/dia** (30d; robustez 60d: 0,2167/dia) | success |
| T8 — equivalência de prefixo | emissão ≡ primeiros K do conjunto completo ordenado | prefixo exato sobre 86.957 pares qualificados | success |
| T9 — determinismo | 2 execuções byte-idênticas | byte-idênticas | success |

Grade real medida (projeções aritméticas, sem materialização fora do
ponto selecionado — guarda de não materialização respeitada):

| (min_wins, min_buyers) | projeção não bloqueada | projeção bloqueada |
|---|---|---|
| (3, 2) | 17.029.400 | 2.584.123 |
| (4, 2) | 8.079.152 | 1.163.536 |
| (5, 2) | 4.308.835 | **610.960** ← ponto selecionado |
| (3, 3) | 17.029.400 | 1.368.892 |
| (4, 3) | 8.079.152 | 614.049 |
| (5, 3) | 4.308.835 | 316.769 |

Emissão no ponto selecionado (5,2): **500 pares** emitidos sobre
**86.957 qualificados** — cobertura declarada **0,575%** (a perda de
recall explícita e medida, preço da revisabilidade — PR-D-03d §3).
Invariante de monotonicidade confirmada (bloqueada ≤ não bloqueada em
todo ponto; não crescente em `min_wins` e em `min_buyers`), nas partes
sintética e real. Consistência: Σ wins do histograma = 693.261 = arestas
`won` elegíveis.

## 2. Leitura

- **A direção (c) se sustenta nos dois eixos.** O blocking da D-03c
  (correto, recall exato) viabiliza a derivação nos pontos restritivos, e
  a emissão top-K declarada converte o conjunto qualificado — 86.957
  pares em (5,2), ~174× o orçamento — em um instrumento de priorização
  de 500 sinais, dentro da capacidade editorial.
- **A rotatividade não é o gargalo.** O incremento do conjunto emitido
  (0,43 par/dia na janela de 30 dias) está ~46× abaixo do teto de 20/dia:
  o prefixo top-K é estável — as vitórias recentes raramente promovem
  pares ao prefixo. A hipótese central (T7) se confirma com folga.
- **O ponto selecionado subiu na grade.** O grafo segue crescendo
  (634.022 → 693.261 arestas elegíveis desde a D-03c): (4,2), operável
  na D-03c (942.187), estourou a guarda (1.163.536) e a regra
  pré-registrada selecionou **(5,2)** — o mecanismo de seleção absorveu o
  crescimento sem recalibração manual, como projetado.
- **A cobertura é baixa e declarada**: 0,575% dos pares qualificados
  entram na fila. É a semântica adotada a priori — o sinal passa a ser o
  prefixo auditável, não o conjunto completo que nunca seria lido
  (809.220 sinais pendentes, zero revisões — D-11, gate P1).
- **A evidência acompanha a mudança de semântica**: o pacote
  `graph_batch` registra `top_k`/`qualified_count`, o snapshot segue
  completo (qualquer par truncado é re-derivável) e a reprodução
  re-trunca deterministicamente — `match = true` nos sinais emitidos,
  adulteração detectada, pacotes legados inalterados (T3).

## 3. Encaminhamento (PR-D-03d §8)

Bateria **bem-sucedida**: o caminho fica **habilitado — não autorizado**
— a promover a semântica top-K ao `task_detect` (ponto (5,2), K = 500),
mediante **decisão humana registrada**, que também decida o destino do
backlog histórico (809.220 sinais emitidos sob a semântica antiga, fora
do escopo desta bateria). Os defaults de produção
(`DETECTION_COLLUSION_MIN_WINS`, `DETECTION_COLLUSION_MIN_BUYERS`) e a
guarda `DETECTION_COLLUSION_MAX_PAIRS` **não mudam** — a emissão top-K
fica implementada e guardada por testes (suíte rápida:
`tests/test_battery_collusion_d.py`), **não promovida** ao `task_detect`
nesta rodada: o PR-D-03d §3 é explícito que a promoção exige veredito de
sucesso **e** decisão humana registrada. Pendência declarada: registro da
decisão humana e, se aprovada, promoção + saneamento do backlog + docs
(`AGENTS.md`, `docs/gaps.md`).
