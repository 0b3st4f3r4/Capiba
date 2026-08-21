# PR-D-03c — Refinamento do collusion_network: escala algorítmica da derivação de pares (blocking de recall exato)

- **Pré-registro**: bateria D-03c (refinamento de `PR-D-03b.md`, veredito
  `inconclusive` em `docs/results/R-D-03b.md`; veredito da D-03 em
  `docs/results/R-D-03.md`)
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para revisão humana
- **Alvo**: viabilidade **operacional** da derivação de pares
  `collusion_network` sob a semântica refinada de co-ocorrência
  (`min_buyers ≥ 2`, PR-D-03b §3) em volume real — hoje bloqueada pela
  explosão combinatória da derivação (projeção de 9,6M pares em 2026-08-21
  OOMKillou o pod do Airflow na primeira run real; guarda
  `DETECTION_COLLUSION_MAX_PAIRS` = 1.000.000 via
  `graphs.projected_pair_count` em `src/capiba/pipeline/tasks.py`, que na
  prática **desliga o sinal em produção** sob os defaults atuais)
- **Configuração**: `experiments/detect/D-03c.json` (declarativa, seeds
  inclusas)

## 1. Pergunta

Um **blocking de recall exato** sobre o snapshot de elegibilidade
(predicado declarado na seção 3, análogo ao prefilter vetorizado exato do
screening fuzzy — `detection/screening_fuzzy.py`, cota superior que descarta
pares que provavelmente não qualificam, com equivalência bit a bit guardada
por teste) reduz o espaço de pares projetados, o tempo de wall-clock e a
memória de pico da derivação refinada para dentro dos limites operacionais
declarados (seção 6, R8), **sem remover nenhum par verdadeiro** — equivalência
bit a bit contra a derivação não bloqueada, nos regimes sintético e real?

## 2. Regime medido e limitações (obrigatório)

- Mesma disciplina da D-03/D-03b: regime **real descritivo + decisão
  pré-registrada** — nenhuma contagem real é predita a priori
  (não-falsificável); pré-registram-se âncoras exatas sintéticas,
  invariantes falsificáveis (equivalência, dupla contagem, monotonicidade)
  e limites operacionais (tempo, memória, guarda de pares).
- **Escopo declarado — escala, não orçamento editorial.** A D-03b mostrou
  que o constraint que liga na calibração é o backlog de triagem (1.397
  pares no menor ponto da grade contra orçamento de 500 — R-D-03b §2);
  esta bateria **não** reabre a calibração nem o orçamento editorial. O
  que se pré-registra é a hipótese de escala: tornar a derivação refinada
  computável e segura em volume real. A pergunta editorial (backlog maior,
  dominância do par, top-k como semântica) segue **em aberto** — decisão
  humana, fora deste registro.
- O blocking é **vácuo em `min_buyers = 1`** (todo fornecedor elegível
  passa no predicado): a semântica de comprador único (default de
  produção) **não** é resgatada por este refinamento — sua projeção de
  9,6M pares permanece acima da guarda. O escopo é viabilizar a semântica
  refinada (`min_buyers ≥ 2`); promover defaults em `config.py` exige
  decisão humana posterior, condicionada ao R-D-03c e à questão editorial
  acima.
- **Amostragem por comunidade no grafo não é adotada** (caminho sugerido
  no estudo de origem): qualquer amostragem viola o critério de
  equivalência desta bateria (nenhum par verdadeiro removido) e quebraria
  a reprodução exata do pacote de evidência `graph_batch`. Fica registrada
  como alternativa não adotada; se o blocking falhar (R8 refutada), ela é
  candidata a PR-D-03d **com semântica aproximada declarada**.
- **Top-k como semântica do sinal não é adotado**: truncar a emissão
  remove pares verdadeiros da fila e muda o sinal (hoje conjunto completo,
  score binário 1,0). O que esta bateria pré-registra é apenas a
  **ordenação determinística de emissão** (seção 5, descriptor de
  prioridade de triagem) — o conjunto emitido é inalterado.
- O grafo não está estacionário (R-D-03b §2: arestas elegíveis cresceram
  ~56% intradiário durante o backfill). A varredura real exige janela de
  congelamento; se o grafo mudar durante a medição, a run é descartada
  (precedente: R-D-03b §4) e a bateria, reexecutada — não contada como
  refutação.
- A bateria mede a derivação **em memória no runner** (Python), não o plano
  de execução AQL: a agregação de elegibilidade já roda no servidor em
  segundos (24–64 s nas varreduras D-03/D-03b) e não é o gargalo.

## 3. Blocking proposto (declarado a priori)

**Predicado**: um fornecedor `s` só participa da derivação em `(min_wins,
min_buyers)` se é elegível (≥ `min_wins` vitórias) em **≥ `min_buyers`
compradores distintos** — i.e., `|B(s)| ≥ min_buyers`, onde `B(s)` é o
conjunto de compradores das linhas de elegibilidade de `s`. A derivação
bloqueada forma pares apenas dentro de `S_b ∩ A` por comprador, com
`A = {s : |B(s)| ≥ min_buyers}`.

**Recall exato (demonstração)**: se o par `{s1, s2}` qualifica em
`(w, n)`, existe um conjunto de ≥ `n` compradores em que ambos são
elegíveis; logo `|B(s1)| ≥ n` e `|B(s2)| ≥ n`, e ambos passam no
predicado. Contrapositiva: um fornecedor com `|B(s)| < n` co-ocorre com
qualquer outro em no máximo `n − 1` compradores — nenhum par que o contém
qualifica. O predicado portanto **nunca remove par verdadeiro**, para
qualquer snapshot; a equivalência é bit a bit por construção e guardada
por teste (padrão `TestIndexedImplementationEquivalence` do screening
fuzzy). Em `min_buyers = 1` o predicado é a identidade (controle
degenerado, R1/R2).

**Projeção bloqueada aritmética**: `Σ_b C(|S_b ∩ A|, 2)`, computável do
export de elegibilidade **sem materializar pares** — extensão direta de
`projected_pair_count`. Métricas primárias da bateria (seção 5) comparam
projeção antes/depois do blocking em cada ponto da grade.

**Referência de implementação**: o prefilter do screening fuzzy
(`screening_fuzzy.py`) demonstra o padrão exigido — cota superior barata e
vetorizada que descarta candidatos com recall exato provado, equivalência
bit a bit testada, parâmetros na config da bateria. Aqui a cota é ainda
mais simples (contagem de compradores distintos por fornecedor), sem numpy.

## 4. Desenho

**Parte A — validação sintética** (ArangoDB de bateria descartável,
integração, 5 seeds; população **idêntica** à da D-03b — 59
contratos/seed, estrutura fixa, seed só randomiza campos neutros — para
manter o controle degenerado comparável). Em cada ponto de controle
`{(3,1), (3,2), (4,2), (2,2)}`: derivação não bloqueada e bloqueada sobre
o mesmo snapshot; projeções aritméticas antes/depois; reprodução do pacote
`graph_batch` (inalterado — o snapshot de elegibilidade não muda, o
blocking é detalhe de derivação).

**Parte A-stress — sintética escalada** (mesma população da Parte A **mais**
um comprador `BIG-B` com 200 fornecedores exclusivos ×3 vitórias cada, fora
da janela de incremento): âncoras exatas de escala (R5) e comparação de
tempo/memória entre os dois caminhos de derivação na mesma run.

**Parte B — varredura real** (grafo de produção, **somente leitura**,
janela de congelamento sem ingestões): export de elegibilidade (query da
D-03/D-03b), projeções aritméticas não bloqueada e bloqueada por ponto da
grade `{3,4,5} × {2,3}`, derivação **materializada pelos dois caminhos**
em cada ponto (necessária à verificação de equivalência R6 — inviável na
semântica (3,1) pela guarda, que **não** é exercitada na Parte B), tempo
de wall-clock por caminho, memória de pico por caminho (`tracemalloc`),
cobertura siafi e consistência do histograma, e a emissão ordenada
(R9) executada **duas vezes** na mesma janela.

**Parte C — evidência**: o pacote `graph_batch` (snapshot `{buyer,
supplier, wins}` + `min_wins` + `min_buyers`) é **inalterado**; a
reprodução usa a derivação bloqueada e deve casar exatamente com pacotes
emitidos pela derivação não bloqueada (compatibilidade retroativa com os
pacotes da D-03/D-03b, que reproduzem pela mesma regra).

## 5. Métricas primárias

- `projected_pairs_unblocked(w)` e `projected_pairs_blocked(w, n)` —
  aritméticas, sem materialização, por ponto da grade (antes/depois do
  blocking).
- Equivalência por ponto: conjunto ordenado de `(par, compradores)` da
  derivação bloqueada == não bloqueada (bit a bit).
- Tempo de wall-clock: varredura total e, na Parte A-stress e na Parte B,
  tempo de cada caminho de derivação na mesma run.
- Memória de pico (heap Python, `tracemalloc`) de cada caminho de
  derivação.
- Emissão ordenada: ranking determinístico por (`buyer_count` decrescente,
  `wins_sum` decrescente — soma das vitórias dos dois fornecedores nos
  compradores co-elegíveis do par, `par` crescente como desempate
  lexicográfico); descriptor `top_k = 500` reportado como prefixo, **sem
  truncamento da emissão**.

## 6. Predições (numéricas, falsificáveis)

Partes A/A-stress/C são **âncoras exatas** (estrutura plantada, cômputo
determinístico) — qualquer desvio refuta; veredito por seed, 5 seeds.
Parte B é falsificável por invariante e por limite operacional, sem
predição de contagem.

- **R1 — equivalência bit a bit (sintético).** Em cada ponto de
  `{(3,1), (3,2), (4,2), (2,2)}`, a derivação bloqueada retorna
  **exatamente** o mesmo conjunto ordenado de pares e a mesma anotação de
  compradores que a não bloqueada (5 pares em (3,1); `{X1,X2}` em (3,2);
  vazio em (4,2); `{X1,X2}` e `{Y1,Y2}` em (2,2)). *Refutada* com qualquer
  divergência.
- **R2 — projeções exatas (sintético).** Não bloqueada: w=3 → **6**, w=4 →
  **3**, w=2 → **13**. Bloqueada: (3,1) → **6** (predicado vacuoso,
  controle degenerado), (3,2) → **2**, (4,2) → **0**, (2,2) → **4**.
  *Refutada* com qualquer divergência.
- **R3 — dupla contagem da projeção bloqueada (sintético).** A projeção
  bloqueada aritmética (`Σ_b C(|S_b ∩ A|, 2)`) iguala **exatamente** a
  contagem de incidências (par, comprador) produzida pela derivação
  bloqueada, em cada ponto. *Refutada* com qualquer divergência — indica
  bug no runner ou no predicado.
- **R4 — reprodução da evidência exata (sintético).** Todo sinal do pacote
  `graph_batch` emitido via derivação bloqueada reproduz com
  `match = true`; removendo **uma** linha do snapshot, `integrity = false`
  e `match = false`. Pacotes das D-03/D-03b (sem blocking) reproduzem
  pela derivação bloqueada com o mesmo resultado. *Refutada* com qualquer
  divergência.
- **R5 — âncoras de escala (stress sintético).** Com `BIG-B` plantado:
  projeção não bloqueada em w=3 = **19.906** (6 da população base +
  C(200, 2) = 19.900); projeção bloqueada em (3,2) = **2** (nenhum
  fornecedor de `BIG-B` é elegível em outro comprador — contribuição
  exata 0); pares qualificados em (3,2) = **1** (`{X1,X2}`); tempo da
  derivação bloqueada **≤** tempo da não bloqueada na mesma run; varredura
  stress completa **< 60 s** por seed. *Refutada* com qualquer divergência
  de contagem ou estouro de tempo.
- **R6 — equivalência real.** Na varredura real, derivação bloqueada ≡ não
  bloqueada (conjunto ordenado idêntico) nos **6** pontos da grade
  `{3,4,5} × {2,3}`. *Refutada* com qualquer divergência.
- **R7 — dupla contagem real.** A projeção bloqueada aritmética iguala
  **exatamente** a contagem de incidências da derivação bloqueada em cada
  ponto da grade. *Refutada* com qualquer divergência.
- **R8 — viabilidade operacional (real, hipótese de escala).** Varredura
  completa **< 600 s** no cluster local; pico de heap Python
  (`tracemalloc`) da derivação bloqueada em (3,2) **< 256 MiB** e **≤**
  pico da não bloqueada na mesma run; projeção bloqueada **< 1.000.000**
  (`DETECTION_COLLUSION_MAX_PAIRS`) em todos os pontos da grade.
  *Refutada* por qualquer estouro — o blocking não basta, e o próximo
  refinamento (amostragem por comunidade com recall aproximado declarado,
  ou mudança de semântica top-k) vira PR-D-03d.
- **R9 — emissão ordenada determinística (real).** Duas execuções da
  emissão ordenada (seção 5) na mesma janela de congelamento produzem
  saída **byte-idêntica**; o descriptor top-500 é prefixo exato do
  conjunto completo ordenado. *Refutada* com qualquer divergência.

## 7. Controles e invariantes

- **Controle degenerado** (R1/R2 em (3,1)): o blocking em `min_buyers = 1`
  é a identidade — regressão da D-03/D-03b embutida na bateria.
- **Monotonicidade**: `projected_blocked(w, n)` não cresce ao subir `w` ou
  `n`, e `projected_blocked(w, n) ≤ projected_unblocked(w)` em todo ponto
  — verificado na grade real e na sintética; violação refuta o runner.
- **Consistência e cobertura (real)**: Σ wins do histograma = arestas
  `won` elegíveis; cobertura siafi **≥ 90%**. Abaixo disso a bateria é
  **inconclusiva** (regime degradado — o grafo não representa o silver),
  não refutada.
- **Congelamento**: nenhuma ingestão durante a Parte B; se o grafo mudar
  entre queries (precedente R-D-03b §4), a run é descartada e reexecutada.
- **Equivalência como guarda permanente**: o teste bit a bit bloqueado ×
  não bloqueado (padrão `TestIndexedImplementationEquivalence`) entra na
  suíte rápida sobre populações sintéticas pequenas, independentemente do
  veredito da Parte B.

## 8. Critério de encerramento

Bateria **bem-sucedida** com R1–R5 exatas nas 5 seeds e R6–R9 satisfeitas:
o blocking de recall exato viabiliza a derivação refinada sob os limites
operacionais, e o caminho fica habilitado — não autorizado — a substituir
a derivação em `detection/graphs.py` (equivalência já guardada por teste).
**Refutada (implementação)** se R1–R4, R6 ou R7 divergem: o predicado ou o
runner estão errados; investigar antes de qualquer reexecução. **Refutada
(hipótese de escala)** se R5 (tempo) ou R8 estouram: o blocking não reduz
o suficiente; PR-D-03d com amostragem de recall aproximado declarado ou
mudança de semântica. **Inconclusiva** se o regime real degrada (cobertura
< 90% ou grafo instável sem janela de congelamento viável) — publicada com
o mesmo rigor. Em qualquer veredito, os defaults de produção
(`DETECTION_COLLUSION_MIN_WINS`, `DETECTION_COLLUSION_MIN_BUYERS`) e a
guarda `DETECTION_COLLUSION_MAX_PAIRS` **não mudam** sem decisão humana
registrada.

## 9. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Blocking**: funções puras em `src/capiba/detection/graphs.py` —
   `blocked_projection(rows, min_buyers)` (aritmética) e derivação
   bloqueada de `pair_buyers_from_eligibility` (predicado da seção 3);
   derivação não bloqueada preservada para o teste de equivalência.
2. **Runner**: `battery_collusion.py` estendido — Parte A-stress, modo de
   dupla derivação com `tracemalloc`, emissão ordenada (seção 5);
   dispatch inalterado (`"runner": "collusion"`, config `D-03c.json`);
   saída bruta em `results/detect/D-03c/`.
3. **Testes de guarda**: equivalência bit a bit na suíte rápida
   (sintético pequeno, offline); Parte A/A-stress/C como
   `@pytest.mark.slow` + `@pytest.mark.integration`; Parte B como execução
   assistida contra o cluster local, em janela de congelamento.
4. **Após o R-D-03c** (somente com veredito de sucesso e decisão humana):
   derivação bloqueada no `task_detect`, docs (`AGENTS.md`,
   `docs/gaps.md`) atualizados. Defaults e guarda de produção: fora deste
   pacote.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana), após o diagnóstico
  de escala em volume real — projeção de 9,6M pares em (3,1) OOMKillou o
  pod do Airflow na primeira run real de 2026-08-21 e a guarda
  `DETECTION_COLLUSION_MAX_PAIRS` passou a pular a derivação (snapshot de
  elegibilidade segue gravado como evidência) — e na sequência dos
  vereditos inconclusivos da D-03 (`R-D-03.md`: semântica de comprador
  único não calibrável) e da D-03b (`R-D-03b.md`: co-ocorrência reduz ~28×,
  backlog ainda ~2,8× acima do orçamento). Dos caminhos sugeridos no
  estudo de origem, este PR adota o **blocking de recall exato**;
  amostragem por comunidade (viola o critério de equivalência) e top-k
  como semântica do sinal (remove pares verdadeiros da fila) ficam
  documentados como alternativas **não adotadas** nesta rodada
  (seção 2). A ordenação determinística de emissão é adotada apenas como
  descriptor de prioridade de triagem.
