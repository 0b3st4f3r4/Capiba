# R-D-03b — Refinamento do collusion_network por co-ocorrência: redução de 28×, ainda fora do orçamento (inconclusiva)

- **Resultado**: bateria D-03b (pré-registro
  `docs/preregistrations/PR-D-03b.md`, refinamento da D-03 — veredito
  `inconclusive` em `docs/results/R-D-03.md`)
- **Executada em**: 2026-08-19
- **Regime**: Parte A/C sintética sobre ArangoDB de bateria descartável
  (`capiba_d03b_battery`, criado e derrubado pelo runner) + Parte B,
  varredura **read-only** sobre o grafo real acumulado (238.291 arestas
  `won` elegíveis, 140.859 pares (comprador, fornecedor) distintos)
- **Saída bruta**: `results/detect/D-03b/` (5 seeds + `real_sweep.json` +
  `summary_synthetic.json` + `summary.json`)
- **Veredito**: **inconclusive** — Q1–Q8 `success`; Q9 `inconclusive`:
  **nenhum ponto da grade (min_wins, min_buyers) ∈ {3,4,5} × {2,3}
  satisfaz o orçamento de triagem** (backlog ≤ 500 pares e incremento
  ≤ 20/dia)

## 1. Números

Parte A/C (sintética, âncoras exatas, 5 seeds — população de 59
contratos/seed com par itinerante e par de fronteira):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| Q1 — controle degenerado (3,1) | exatamente 5 pares (≡ semântica D-03) | exato nas 5 seeds | success |
| Q2 — alvo (3,2) | exatamente `{X1,X2}` com compradores `["IT-B1","IT-B2"]` | exato nas 5 seeds | success |
| Q3 — fronteira | (4,2) vazio; controle (2,2) = `{X1,X2}` e `{Y1,Y2}` | exato nas 5 seeds | success |
| Q4 — incremento de 30 dias | recentes(3,2) = 1 (≈ 0,0333/dia) | exato nas 5 seeds | success |
| Q5 — evidência `graph_batch` com `min_buyers` | match=true em todos os sinais; adulterado → integrity=false, match=false | exato nas 5 seeds | success |

Parte B (varredura real, 2026-08-19):

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| Q6 — dupla contagem | AQL = Python, exato | idênticos nos 6 pontos da grade | success |
| Q7 — consistência + cobertura | Σ wins do histograma = arestas elegíveis; siafi ≥ 90% | 238.291 = 238.291; cobertura 100% | success |
| Q8 — viabilidade operacional | varredura < 600 s; sem materialização acima do orçamento | 64,2 s; nenhuma materialização | success |
| Q9 — decisão única | (w*, n*) único dentro do orçamento | **nenhum ponto qualifica** | **inconclusive** |

Grade real medida (ordem pré-registrada de decisão, `min_buyers` externo):

| (min_wins, min_buyers) | pares totais | incremento (30d, /dia) | incremento (60d, /dia) |
|---|---|---|---|
| (3, 2) | 22.173 | 103,8 | 53,9 |
| (4, 2) | 9.677 | 40,2 | 22,5 |
| (5, 2) | 4.844 | 21,6 | 12,5 |
| (3, 3) | 6.454 | 32,0 | 16,9 |
| (4, 3) | 2.754 | 12,9 | 6,9 |
| (5, 3) | 1.397 | 6,4 | 3,5 |

Controle (2,2) (fora dos candidatos): 80.609 pares; 11.293 recentes.

Invariante de monotonicidade confirmada nas duas direções (em `min_wins`
a `min_buyers` fixo e em `min_buyers` a `min_wins` fixo), nas partes
sintética e real.

## 2. Leitura

- **A co-ocorrência funciona na direção prevista, mas não o bastante
  neste volume.** A exigência de ≥ 2 compradores distintos colapsa
  627.592 pares (w=3, semântica D-03) para 22.173 (3,2) — redução de
  ~28× — e ≥ 3 compradores leva a 6.454. Ainda assim, o menor backlog da
  grade, 1.397 pares em (5,3), fica ~2,8× acima do orçamento de 500. A
  refutação é informativa: uma fatia grande da alternância real **é**
  itinerante (repete-se entre órgãos), não só ruído de órgão grande.
- **O constraint que liga é o backlog, não o incremento.** Em (4,3) e
  (5,3) o incremento de 30 dias já cabe no orçamento diário (12,9 e 6,4
  por dia ≤ 20); o que estoura é o estoque acumulado (2.754 e 1.397 >
  500). Um orçamento de backlog maior — ou um caminho que ataque o
  estoque (dominância, score graduado com top-k) — muda o resultado sem
  mudar a semântica.
- **Cauda longa confirmada também no regime refinado**: 110.867 dos
  140.859 pares (comprador, fornecedor) têm 1 vitória; máx. 512 vitórias
  de um fornecedor num mesmo comprador.
- **O grafo não está estacionário**: as arestas elegíveis cresceram de
  152.669 (D-03, 2026-08-19 de manhã) para 238.291 (à noite do mesmo
  dia), e o incremento de 60 dias é ~52% do de 30 dias em todos os pontos
  — efeito do backfill em curso. Qualquer calibração futura deve re-medir
  após a ingestão estabilizar.
- **Parte C validada na forma refinada**: o pacote `graph_batch` agora
  registra `min_buyers` no bloco `reproduction` (default 1 para pacotes
  anteriores — compatibilidade declarada no PR §2) e reproduz exatamente
  os sinais sob a semântica de co-ocorrência, detectando adulteração.
- O default de produção segue `DETECTION_COLLUSION_MIN_BUYERS = 1`:
  promover os valores calibrados exigiria decisão humana — e a bateria
  não calibrou.

## 3. Encaminhamento (PR-D-03c)

Sem ponto calibrado, o refinamento seguinte vira `PR-D-03c` antes de nova
execução. Caminhos candidatos (a decidir no PR-D-03c, não aqui):

1. **Dominância do par no comprador** (caminho 2 do R-D-03): exigir que
   as vitórias combinadas do par superem uma fração pré-registrada das
   vitórias do comprador — ataca o estoque, não só a recorrência.
2. **Score graduado com emissão top-k** (caminho 3 do R-D-03): substitui
   o corte duro por ranking; exige mudança de semântica do sinal (hoje
   binário 1,0), registrada como opção.
3. **Revisão do orçamento editorial**: o incremento recente cabe em
   (4,3)/(5,3); se a operação de triagem aceitar um backlog inicial maior
   (fila de arranque única), a grade já tem ponto operável — decisão
   editorial, não técnica.

## 4. Guarda permanente

- A bateria virou teste de integração
  (`tests/test_battery_collusion_b.py::test_battery_refined_against_live_arangodb`,
  `@pytest.mark.integration`, `CAPIBA_INTEGRATION=1`): reroda a Parte A/C
  contra um ArangoDB descartável e exige Q1–Q5 `success`.
- Os avaliadores (Q1–Q9), os helpers da grade `(w, n)` e a regra de
  decisão são puros e cobertos por testes offline; a reprodução do pacote
  `graph_batch` com `min_buyers` é guardada por
  `tests/test_evidence_packages.py` e pelo BDD do O9
  (`tests/bdd/features/signal_evidence.feature`, cenário de
  co-ocorrência).
- Nota operacional: a primeira execução da Parte B foi **descartada** —
  escrita concorrente no grafo durante a varredura (as arestas elegíveis
  cresciam entre as queries; Q6 refutada e Σ wins ≠ arestas elegíveis),
  violando a janela de congelamento do PR-D-03 §6. A reexecução com o
  grafo estável completou em 64 s com dupla contagem exata. A varredura é
  read-only e idempotente — segura para rerodar. Antes dela, o port-forward
  do ArangoDB caiu por restart do pod (liveness), como na D-03.

## Revisões

- 2026-08-19: publicação (Q1–Q8 confirmadas; Q9 inconclusiva — a
  co-ocorrência reduz o volume ~28× mas nenhum ponto da grade
  {3,4,5} × {2,3} cabe no orçamento de triagem; refinamento em PR-D-03c).
