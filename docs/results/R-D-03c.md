# R-D-03c — Blocking de recall exato do collusion_network: derivação correta e ~7× mais barata, ainda fora dos limites operacionais (refutada — hipótese de escala)

- **Resultado**: bateria D-03c (pré-registro
  `docs/preregistrations/PR-D-03c.md`, refinamento da D-03b — veredito
  `inconclusive` em `docs/results/R-D-03b.md`)
- **Executada em**: 2026-08-21
- **Regime**: Partes A/A-stress/C sintéticas sobre ArangoDB de bateria
  descartável (`capiba_d03c_battery`, criado e derrubado pelo runner) +
  Parte B, varredura **read-only** sobre o grafo real em janela de
  congelamento (634.022 arestas `won`, estável antes/depois da medição;
  317.067 pares (comprador, fornecedor) exportados; cobertura siafi 100%)
- **Saída bruta**: `results/detect/D-03c/` (5 seeds + `real_sweep.json` +
  `summary_synthetic.json` + `summary.json`)
- **Veredito**: **refuted (hipótese de escala)** — R1–R7 e R9 `success`;
  R8 `refuted`: o blocking reduz a derivação ~7× em incidências, tempo e
  memória, mas a projeção bloqueada em (3,2) e (3,3) segue acima da
  guarda `DETECTION_COLLUSION_MAX_PAIRS` (1.000.000) e o pico de heap da
  derivação bloqueada em (3,2) estoura o orçamento de 256 MiB

## 1. Números

Partes A/A-stress/C (sintéticas, âncoras exatas, 5 seeds — população
idêntica à D-03b, 59 contratos/seed, mais o comprador stress `BIG-B` com
200 fornecedores exclusivos ×3 vitórias):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| R1 — equivalência bit a bit + âncoras | bloqueada ≡ não bloqueada em {(3,1),(3,2),(4,2),(2,2)}; 5 pares em (3,1); `{X1,X2}` em (3,2); vazio em (4,2); `{X1,X2}`+`{Y1,Y2}` em (2,2) | exato nas 5 seeds | success |
| R2 — projeções exatas | não bloqueada 6/3/13 (w=3/4/2); bloqueada 6/2/0/4 em (3,1)/(3,2)/(4,2)/(2,2) | exato nas 5 seeds | success |
| R3 — dupla contagem bloqueada | projeção aritmética = incidências materializadas | exato nas 5 seeds, nos 4 pontos | success |
| R4 — evidência `graph_batch` | match=true via reprodução não bloqueada **e** bloqueada; adulterado → integrity=false, match=false | exato nas 5 seeds | success |
| R5 — âncoras de escala (stress) | não bloqueada w=3 = 19.906; bloqueada (3,2) = 2; pares (3,2) = `{X1,X2}`; bloqueada ≤ não bloqueada em tempo; varredura stress < 60 s | exato nas 5 seeds (bloqueada ~10–30× mais rápida; stress < 4 s/seed) | success |

Parte B (varredura real, 2026-08-21, janela de congelamento verificada):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| R6 — equivalência real | bloqueada ≡ não bloqueada nos 6 pontos {3,4,5}×{2,3} | conjuntos ordenados idênticos nos 6 pontos | success |
| R7 — dupla contagem real | projeção bloqueada = incidências | exato nos 6 pontos | success |
| R8 — viabilidade operacional | varredura < 600 s; pico bloqueado (3,2) < 256 MiB e ≤ não bloqueado; projeção bloqueada < 1M em todos os pontos | varredura 160,8 s ✓; pico bloqueado (3,2) = 329,4 MiB ≤ 2.546,7 MiB ✓ mas **≥ 256 MiB** ✗; projeções bloqueadas (3,2) = **2.096.013** e (3,3) = **1.118.233** ≥ 1M ✗ | **refuted** |
| R9 — emissão ordenada determinística | 2 execuções byte-idênticas; top-500 prefixo exato | byte-idêntica; 270.390 sinais ordenados em (3,2) | success |

Grade real medida (projeções aritméticas, sem materialização, e derivação
materializada pelos dois caminhos):

| (min_wins, min_buyers) | projeção não bloqueada | projeção bloqueada | pares qualificados | tempo não bloq. (s) | tempo bloq. (s) |
|---|---|---|---|---|---|
| (3, 2) | 14.159.459 | **2.096.013** | 270.390 | 40,0 | 5,1 |
| (4, 2) | 6.612.492 | 942.187 | 126.827 | 8,7 | 0,7 |
| (5, 2) | 3.589.111 | 489.531 | 69.958 | 5,4 | 0,3 |
| (3, 3) | 14.159.459 | **1.118.233** | 61.690 | 17,3 | 0,6 |
| (4, 3) | 6.612.492 | 493.338 | 29.135 | 7,6 | 0,5 |
| (5, 3) | 3.589.111 | 249.735 | 16.196 | 4,7 | 0,1 |

Picos de heap (tracemalloc) no ponto traçado (3,2): não bloqueado
2.546,7 MiB; bloqueado 329,4 MiB. Invariante de monotonicidade confirmada
(bloqueada ≤ não bloqueada em todo ponto; não crescente em `min_wins` e
em `min_buyers`), nas partes sintética e real.

## 2. Leitura

- **O predicado está correto — e isso não estava em dúvida por
  construção.** A equivalência bit a bit (R1, R6) e a dupla contagem
  (R3, R7) confirmam a prova de recall exato do PR: nenhum par verdadeiro
  é removido, em volume sintético e real. O guarda permanente
  (`TestBlockedDerivationEquivalence`) entra na suíte rápida.
- **A hipótese de escala está refutada nos pontos que importam.** O
  blocking corta ~6,8× as incidências em (3,2) (14,2M → 2,1M), ~7,8× o
  tempo e ~7,7× o pico de heap — mas 2,1M incidências e 329 MiB seguem
  acima da guarda de 1M e do orçamento de 256 MiB. A redução é
  multiplicativa e constante; o problema é combinatório e grande demais
  em `min_wins = 3`.
- **Nos pontos mais restritivos o blocking já viabiliza a derivação**:
  (4,3) e (5,3) projetam 493.338 e 249.735 incidências bloqueadas —
  abaixo da guarda, com derivação sub-segundo. O gargalo residual é
  exclusivamente `min_wins = 3`.
- **O grafo cresceu ~2,7× desde a D-03b** (238.291 → 634.022 arestas
  `won` elegíveis; 22.173 → 270.390 pares qualificados em (3,2)),
  agravando todos os números absolutos. A janela de congelamento desta
  run foi verificada (contagem de `won` estável antes/depois).
- **A emissão ordenada (R9) é determinística e barata** — descriptor de
  prioridade de triagem utilizável independentemente do veredito de
  escala, sem truncar o conjunto emitido.

## 3. Encaminhamento (PR-D-03c §8)

Refutação da hipótese de escala: o blocking de recall exato **não basta**
nos pontos (3,2)/(3,3). Os caminhos pré-registrados como alternativas não
adotadas tornam-se candidatos a **PR-D-03d**: amostragem por comunidade
com recall aproximado declarado, ou mudança de semântica top-k — ambos
com semântica aproximada declarada a priori. A questão editorial da D-03b
(backlog ~2,8× acima do orçamento no menor ponto da grade) segue em
aberto e é agravada pelo crescimento do grafo. Os defaults de produção
(`DETECTION_COLLUSION_MIN_WINS`, `DETECTION_COLLUSION_MIN_BUYERS`) e a
guarda `DETECTION_COLLUSION_MAX_PAIRS` **não mudam** — a derivação
bloqueada fica implementada e guardada por teste, mas não promovida ao
`task_detect` (o veredito não autoriza a substituição).
