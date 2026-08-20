# PR-D-03b — Refinamento do collusion_network: co-ocorrência entre compradores

- **Pré-registro**: bateria D-03b (refinamento de `PR-D-03.md`, veredito
  `inconclusive` em `docs/results/R-D-03.md`)
- **Criado em**: 2026-08-19
- **Última atualização**: 2026-08-19
- **Status**: executado em 2026-08-19 — veredito **inconclusive**
  (`docs/results/R-D-03b.md`): Q1–Q8 confirmadas; nenhum ponto da grade
  {3,4,5} × {2,3} coube no orçamento de triagem; refinamento seguinte em
  PR-D-03c
- **Alvo**: semântica refinada do sinal `collusion_network` — par de
  fornecedores com ≥ `min_wins` vitórias cada no mesmo comprador, em
  **≥ `min_buyers` compradores distintos** — calibrada sobre o grafo real
  acumulado (152.669 arestas `won` elegíveis, 98.495 pares
  (comprador, fornecedor) — R-D-03), mais a extensão correspondente do
  pacote de evidência `graph_batch`
- **Configuração**: `experiments/detect/D-03b.json` (declarativa, seeds
  inclusas; esboço, revisada junto com este PR)

## 1. Pergunta

A semântica refinada por co-ocorrência (seção 3) recupera exatamente os
pares itinerantes plantados no regime sintético, e admite um ponto
`(min_wins, min_buyers)` dentro do orçamento de triagem editorial no
regime real — ao contrário da semântica de comprador único, refutada como
calibrável na D-03?

## 2. Regime medido e limitações (obrigatório)

- Mesma disciplina da D-03: regime **real descritivo + decisão
  pré-registrada** — nenhuma contagem real é predita (não-falsificável a
  priori); pré-registram-se a regra de decisão, invariantes falsificáveis
  e âncoras exatas sintéticas do operador refinado e da evidência.
- A D-03 mediu a distribuição de vitórias por (comprador, fornecedor),
  **não** a co-ocorrência de pares entre compradores — o volume real da
  semântica refinada é desconhecido na data deste registro.
- A co-ocorrência aproxima o padrão editorial de "cartel itinerante"
  (o mesmo par dividindo vitórias em órgãos distintos), mas **não prova
  conluio real** — sem rótulos externos, a triagem humana do O10 segue
  sendo o filtro. Fornecedores grandes e generalistas (ex.: distribuidoras
  nacionais) podem co-ocorrer legitimamente — limitação declarada.
- O score segue **binário 1,0**; score graduado (caminho 3 do R-D-03) e a
  dominância do par no comprador (caminho 2) ficam **fora** deste
  refinamento — uma mudança por vez, para isolar o efeito da co-ocorrência.
- O grafo societário segue vazio na data; `trace_ownership` fora do escopo.
- A evidência `graph_batch` muda apenas no bloco `reproduction`
  (`min_buyers`); o snapshot `{buyer, supplier, wins}` já é suficiente
  para re-derivar os pares sob a nova regra. Pacotes emitidos com a
  semântica antiga (`min_buyers` ausente ⇒ 1) reproduzem pela regra antiga
  — compatibilidade declarada.

## 3. Semântica refinada (declarada)

`detect_collusion(db, min_wins, min_buyers)` — para cada comprador *b*,
seja *S_b* o conjunto de fornecedores com ≥ `min_wins` arestas `won` para
contratos de *b* (idem D-02/D-03). Seja *P_b* = C(*S_b*, 2) os pares de
*b*. A saída é o conjunto de pares `{s1, s2}` que aparecem em **≥
`min_buyers`** conjuntos *P_b* distintos, cada par anotado com a lista
ordenada de compradores em que é elegível. `min_buyers = 1` reduz
**exatamente** à semântica da D-02/D-03 (controle degenerado, verificado
na predição Q1).

## 4. Regra de calibração (declarada a priori)

- **Candidatos**: `(min_wins, min_buyers) ∈ {3, 4, 5} × {2, 3}`,
  percorridos na ordem lexicográfica com `min_buyers` **mais externo**:
  (3,2), (4,2), (5,2), (3,3), (4,3), (5,3) — prefere-se a evidência mais
  fraca que ainda cabe no orçamento.
- **Orçamento de triagem** (inalterado da D-03): backlog ≤ **500** pares
  distintos; incremento ≤ **20**/dia, estimado pela janela de 30 dias
  sobre `signature_date` (pares que se qualificam no grafo completo mas
  não sem os contratos dos últimos 30 dias, dividido por 30); janela de 60
  dias como descritor de robustez não ancorado.
- **Regra de decisão**: `w*` = o primeiro candidato na ordem acima que
  satisfaz ambos os orçamentos. Se nenhum satisfaz, a bateria é
  **inconclusiva**, publicada em `docs/results/R-D-03b.md`, e a forma
  corrigida vira `PR-D-03c`.
- O par calibrado **só vira default** (`DETECTION_COLLUSION_MIN_WINS` +
  novo `DETECTION_COLLUSION_MIN_BUYERS`) após o R-D-03b, por decisão
  humana registrada.

## 5. Desenho

**Parte A/C — sintética** (ArangoDB de bateria descartável, integração;
estrutura idêntica nas 5 seeds, seed só randomiza campos neutros).
População plantada (59 contratos/seed), com janelas de data como na D-03:

- `PLANT-B` (recente): C1–C3 ×4 vitórias, C4 ×2 — pares de comprador
  único; nunca qualificam com `min_buyers ≥ 2`.
- **Itinerante**: X1, X2 ×3 vitórias em `IT-B1` (recente) **e** em
  `IT-B2` (antigo) — o par `{X1,X2}` qualifica a partir de `min_buyers = 2`.
- **Fronteira por comprador**: Y1 ×3 em `BD-B1` (recente) e ×3 em `BD-B2`
  (antigo); Y2 ×3 em `BD-B1` mas ×2 em `BD-B2` — o par `{Y1,Y2}` é
  elegível em 1 comprador com w=3 (e em 2 compradores com w=2, controle).
- `CTRL-B1` (antigo): K1–K3 ×2 — abaixo de w=3.
- `SOLO-B1..B4` (antigo): 1 fornecedor exclusivo ×4 cada.

**Parte B — varredura real** (read-only, janela de congelamento): export
da D-03 (linhas `{buyer, supplier, wins}` + wins nas janelas), contagens
aritméticas por candidato sem materializar pares, dupla contagem contra a
agregação AQL que forma os pares no servidor, cobertura siafi, tempo de
varredura, aplicação da regra da seção 4; materialização apenas no
candidato calibrado e sob o orçamento.

**Parte C — evidência**: o bloco `reproduction` do `graph_batch` ganha
`min_buyers`; a reprodução re-deriva os pares do snapshot sob a regra
refinada (default 1 = semântica antiga); adulteração de uma linha segue
quebrando a integridade.

## 6. Predições (numéricas, falsificáveis)

Âncoras exatas por seed (5 seeds) na Parte A/C; invariantes falsificáveis
na Parte B.

- **Q1 — controle degenerado.** Com `(w=3, n=1)` a saída é **exatamente**
  5 pares distintos: 3 de `PLANT-B`, `{X1,X2}` e `{Y1,Y2}` — idêntico à
  semântica D-03 sobre a mesma população. *Refutada* com qualquer
  divergência.
- **Q2 — co-ocorrência exata.** Com `(w=3, n=2)` a saída é **exatamente**
  1 par, `{X1,X2}`, anotado com os compradores `[IT-B1, IT-B2]` (ordem
  alfabética). *Refutada* com qualquer divergência de conjunto ou de
  anotação.
- **Q3 — fronteira de `min_wins` sob co-ocorrência.** Com `(w=4, n=2)` a
  saída é **vazia** (X1/X2 têm 3 vitórias); com `(w=2, n=2)` (controle)
  a saída é **exatamente** `{X1,X2}` e `{Y1,Y2}` (Y2 atinge 2 vitórias em
  ambos os compradores BD). *Refutada* com qualquer divergência.
- **Q4 — incremento de 30 dias exato.** Pares que se qualificam no grafo
  completo mas não sem os últimos 30 dias, com `(w=3, n=2)`: **1**
  (`{X1,X2}` perde `IT-B1` e cai para 1 comprador) — incremento 1/30 ≈
  0,0333/dia. *Refutada* com qualquer divergência.
- **Q5 — reprodução da evidência exata.** Todo sinal do pacote sintético
  com `min_buyers = 2` reproduz com `match = true`; removendo **uma**
  linha do snapshot, `integrity = false` e `match = false`. *Refutada* com
  qualquer divergência.
- **Q6 — dupla contagem (real).** A contagem de pares por candidato via
  agregação AQL (pares formados no servidor) iguala **exatamente** a
  recomputação em Python a partir das linhas exportadas. *Refutada* com
  qualquer divergência.
- **Q7 — consistência e cobertura (real).** Σ wins do histograma = arestas
  `won` elegíveis; cobertura siafi ≥ 90%. *Inconclusiva* abaixo disso
  (regime degradado).
- **Q8 — viabilidade operacional (real).** Varredura completa < 600 s no
  cluster local; materialização apenas no candidato calibrado e ≤ 500
  pares. *Refutada* por estouro.
- **Q9 — decisão única (real).** A regra da seção 4 produz um único
  candidato; se nenhum qualifica, a bateria é **inconclusiva** (não
  "sucesso").

## 7. Controles e invariantes

- **Monotonicidade**: pares(w, n) não cresce em w nem em n — verificado na
  grade 3×2 real e na sintética; violação refuta o runner.
- **Controle degenerado** (Q1): `min_buyers = 1` reproduz a semântica
  anterior bit a bit — regressão da D-03 embutida na bateria.
- **Congelamento**: nenhuma ingestão durante a Parte B; varredura
  read-only e idempotente (a D-03 mostrou 24 s por varredura).
- **Não materialização**: contagens aritméticas; pares materializados só
  no candidato calibrado, sob orçamento.

## 8. Critério de encerramento

Bateria **bem-sucedida** com Q1–Q5 exatas nas 5 seeds, Q6–Q8 satisfeitas e
Q9 com candidato único. **Inconclusiva** se Q7 degrada o regime ou Q9 não
qualifica candidato — publicada com o mesmo rigor, forma corrigida em
`PR-D-03c`. Refutações de runner (Q1–Q6) investigadas antes de nova
execução. O sucesso habilita — não autoriza automaticamente — os defaults
calibrados em `config.py` e a atualização de `AGENTS.md`/`docs/gaps.md`.

## 9. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Operador**: `detect_collusion`/`collusion_eligibility` ganham
   `min_buyers` (default 1, semântica D-02 preservada) em
   `src/capiba/detection/graphs.py`; `collusion_signals` anota
   `min_buyers` e os compradores do par em `details`.
2. **Runner**: `battery_collusion.py` estendido para a grade (w, n) —
   mesma Parte A/C + Parte B; dispatch inalterado (`"runner": "collusion"`,
   config `D-03b.json`).
3. **Evidência**: `min_buyers` no bloco `reproduction` do `graph_batch` e
   na reprodução (default 1); testes e BDD do O9 atualizados.
4. **Testes de guarda**: bateria como `@pytest.mark.slow` +
   `@pytest.mark.integration`; avaliadores puros offline.
5. **Após o R-D-03b**: defaults calibrados e docs atualizados, por decisão
   humana.

## Revisões

- 2026-08-19: criação (rascunho para revisão humana), na sequência do
  veredito `inconclusive` da D-03 (`docs/results/R-D-03.md`): a semântica
  de comprador único gera 627.592 pares em w=3 e 15.107 em w=10 — fora do
  orçamento de triagem em qualquer candidato. Dos três caminhos
  registrados no R-D-03 §3, este PR adota a **co-ocorrência entre
  compradores** (caminho 1); dominância do par (caminho 2) e score
  graduado (caminho 3) ficam documentados como alternativas não adotadas
  nesta rodada.
- 2026-08-19: aprovação humana registrada; execução conforme seção 9.
- 2026-08-19: execução e publicação em `docs/results/R-D-03b.md` —
  **inconclusive**: a co-ocorrência reduz o volume ~28× (22.173 pares em
  (3,2) contra 627.592 em w=3 da D-03), mas o menor backlog da grade
  (1.397 em (5,3)) segue ~2,8× acima do orçamento de 500; o incremento
  diário já cabe em (4,3)/(5,3) — o constraint que liga é o estoque.
  Primeira varredura real descartada por escrita concorrente no grafo
  (violação da janela de congelamento); reexecução com o grafo estável
  teve dupla contagem exata. Encaminhamento: PR-D-03c.
