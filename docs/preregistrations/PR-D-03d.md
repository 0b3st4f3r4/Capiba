# PR-D-03d — Refinamento do collusion_network: emissão ranqueada com orçamento editorial explícito (top-K declarado sobre a derivação bloqueada)

- **Pré-registro**: bateria D-03d (refinamento de `PR-D-03c.md`, veredito
  `refuted` — hipótese de escala — em `docs/results/R-D-03c.md`; cadeia
  D-03 → D-03b → D-03c)
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: aprovado; executado — veredito `success`
  (`docs/results/R-D-03d.md`)
- **Alvo**: viabilidade **editorial e operacional** conjunta da emissão do
  sinal `collusion_network` em volume real — hoje inviável nos dois eixos:
  a derivação exata estoura a guarda de pares e a memória nos pontos
  permissivos (R8 da D-03c), e mesmo nos pontos que o blocking viabiliza o
  conjunto qualificado (16.196–126.827 pares na grade real da D-03c)
  excede em ordens de grandeza a capacidade de triagem observada
  (809.220 sinais `collusion_network` pendentes e **zero** revisões
  humanas concluídas — relatório de volume do gate P1 da D-11,
  PR-D-11 §Revisões, 2026-08-21)
- **Configuração**: `experiments/detect/D-03d.json` (declarativa, seeds
  inclusas)

## 1. Direção escolhida (decisão registrada, sujeita a revisão humana)

O encaminhamento pré-registrado no R-D-03c §3 oferecia dois caminhos —
amostragem de recall aproximado declarado (a) ou mudança de semântica (b)
— e o R-D-03b §3, um terceiro: score graduado com emissão top-k ou
revisão do orçamento editorial. Este registro **adota a direção (c),
combinação**: manter o **blocking de recall exato** da D-03c (provado
correto — equivalência bit a bit sintética e real, R1–R7) como mecanismo
de derivação e trocar a **semântica de emissão** do sinal: de "conjunto
completo de pares qualificados" para **emissão ranqueada com truncamento
declarado top-K**, com orçamento editorial explícito (K = 500, backlog;
incremento ≤ 20/dia — os mesmos números do orçamento da D-03b).

Justificativa contra as alternativas:

- **Por que não (a) amostragem de recall aproximado**: o gargalo que a
  amostragem atacaria — o custo computacional da derivação — **já está
  resolvido nos pontos restritivos** pelo blocking da D-03c: (4,3) e
  (5,3) projetam 493.338 e 249.735 incidências bloqueadas, abaixo da
  guarda, com derivação sub-segundo (R-D-03c §1). Amostrar sobre esses
  pontos não reduz o problema que liga — a fila de triagem — e ainda
  quebraria a reprodução exata do pacote de evidência `graph_batch`
  (uma amostra não é re-derivável deterministicamente do snapshot sem
  registrar a amostra inteira). A amostragem fica registrada como
  alternativa **não adotada**; se a presente bateria for refutada por
  escala (T5/T6), ela é a candidata a PR-D-03e.
- **Por que não (b) na forma de elevar pisos** (`min_wins`/`min_buyers`
  efetivos): a grade real mostra cauda longa e alvo móvel — o grafo
  cresceu ~2,7× em dois dias (238.291 → 634.022 arestas elegíveis entre
  a D-03b e a D-03c) e mesmo (5,3) qualifica 16.196 pares, ~32× o
  orçamento de 500. Elevar pisos é recalibrar um corte duro contra uma
  população em crescimento, sem atacar o estoque acumulado; cada
  recalibração exigiria nova bateria. A direção (b) é adotada apenas na
  sua forma de **emissão ranqueada**, absorvida em (c).
- **Por que o top-k, rejeitado no PR-D-03c §2, é agora adotado** —
  justificativa contra o R-D-03c, como exigido:
  1. A objeção de 2026-08-21 era de **escopo de hipótese**: a D-03c
     testava escala com **recall exato**; introduzir truncamento ali
     contaminaria a métrica de equivalência bit a bit (o que se queria
     provar era que nenhum par verdadeiro é removido *pela derivação*).
     Essa hipótese foi respondida — o predicado está correto e guardado
     por teste permanente; o que a R8 refutou foi a suficiência
     operacional nos pontos permissivos. A pergunta que sobra é de
     emissão, não de derivação.
  2. A objeção de mérito ("truncar remove pares verdadeiros da fila")
     é respondida pela medição da fila real: 809.220 sinais
     `collusion_network` pendentes com **zero** revisões concluídas
     (D-11, gate P1). Um sinal cuja emissão supera em ordens de
     grandeza qualquer capacidade de revisão humana não tem valor
     editorial como conjunto completo; o truncamento **declarado**
     converte o sinal no que ele de fato pode ser — um instrumento de
     priorização — em vez de manter uma emissão completa que nunca será
     lida.
  3. O truncamento desta proposta é **explícito, determinístico,
     auditável e reversível**: o pacote de evidência continua carregando
     o snapshot **completo** de elegibilidade (não o conjunto truncado),
     mais `top_k` e `qualified_count`; qualquer par truncado é
     re-derivável do pacote, e K é parâmetro de config, não constante
     escondida. A perda de recall é **declarada e medida** (cobertura
     K/qualificados publicada em cada run), não uma remoção silenciosa.
  4. A ordenação já foi validada em produção (R9 da D-03c: byte-idêntica
     em duas execuções, top-500 prefixo exato, 270.390 sinais ordenados
     em (3,2)) — o que muda é apenas promover o descriptor a semântica.

A decisão final entre (a), (b) e (c) é **humana**; este registro
documenta a escolha proposta e sua justificativa para revisão.

## 2. Pergunta

A emissão ranqueada com orçamento editorial explícito — **top-K = 500**
sobre a derivação bloqueada da D-03c, ordenação (`buyer_count`
decrescente, `wins_sum` decrescente, `par` crescente), no **primeiro
ponto da grade** cuja projeção bloqueada cabe na guarda — satisfaz
**simultaneamente** os limites operacionais (projeção < 1.000.000, pico
de heap < 256 MiB, varredura < 600 s) **e** o orçamento editorial
(backlog emitido ≤ 500, incremento ≤ 20/dia) no grafo real, com
**equivalência de prefixo** contra a derivação exata completa no ponto
selecionado e reprodução exata da evidência?

## 3. Regime medido e limitações (obrigatório)

- Mesma disciplina da cadeia D-03: regime **real descritivo + decisão
  pré-registrada** — nenhuma contagem real é predita a priori;
  pré-registram-se âncoras exatas sintéticas, invariantes falsificáveis
  (prefixo, dupla contagem, determinismo, monotonicidade) e limites
  operacionais/editoriais.
- **Mudança de semântica declarada**: o sinal deixa de ser o conjunto
  completo de pares qualificados e passa a ser o **prefixo top-K** do
  conjunto ordenado. O que se perde é declarado: a **cobertura**
  `K / qualificados` é medida e publicada em cada run (na grade real da
  D-03c, seria entre ~0,4% e ~3,1% — espera-se cobertura baixa; ela é o
  preço explícito da revisabilidade, não um efeito colateral).
- **O score do sinal não muda** (binário 1,0). A ordenação é descriptor
  de prioridade de triagem promovido a critério de emissão; score
  graduado a partir do ranking é uma mudança distinta, fora deste
  registro (candidata a refinamento próprio).
- **Não reabre a calibração de `min_wins`/`min_buyers`**: a grade e a
  ordem de decisão são as da D-03b; o que se adiciona é a regra de
  seleção do ponto operável (seção 4) e o truncamento.
- **A guarda `DETECTION_COLLUSION_MAX_PAIRS` e os defaults de produção
  não mudam**; a emissão top-K não é promovida ao `task_detect` por esta
  bateria — promoção exige veredito de sucesso **e** decisão humana
  registrada.
- **O backlog histórico não é saneado por esta bateria**: os 809.220
  sinais pendentes atuais (emitidos sob a semântica antiga, antes da
  guarda) são objeto de decisão operacional separada (revisão, expurgo
  ou reclassificação), fora do escopo deste registro.
- O grafo não está estacionário (crescimento ~2,7× em dois dias). A
  varredura real exige janela de congelamento; se o grafo mudar durante
  a medição, a run é descartada (precedentes: R-D-03b §4, R-D-03c) e a
  bateria, reexecutada — não contada como refutação.
- K = 500 e o incremento ≤ 20/dia são os valores do orçamento editorial
  da D-03b, adotados como **placeholders pré-registrados**; alterá-los
  exige PR-D-03e com justificativa datada. Se a banca editorial revisar
  o orçamento, a semântica top-K acomoda o novo valor por configuração,
  sem nova bateria de escala — essa robustez é parte da justificativa da
  direção (c).

## 4. Desenho

**Semântica de emissão (declarada a priori)**: no ponto selecionado
`(w*, n*)`, deriva-se o conjunto completo de pares qualificados pela
derivação bloqueada da D-03c; ordena-se por (`buyer_count` decrescente,
`wins_sum` decrescente — soma das vitórias dos dois fornecedores nos
compradores co-elegíveis do par, `par` crescente como desempate
lexicográfico); emite-se o **prefixo de K = 500** pares. O descriptor da
emissão registra `top_k`, `qualified_count` e `coverage =
top_k_efetivo / qualified_count`.

**Regra de seleção do ponto (pré-registrada)**: percorre-se a grade
`{3,4,5} × {2,3}` na ordem da D-03b (`min_buyers` externo, `min_wins`
interno, ambos crescentes: (3,2), (4,2), (5,2), (3,3), (4,3), (5,3)) e
seleciona-se o **primeiro** ponto com projeção bloqueada aritmética
**< 1.000.000** (guarda, sem materialização). A seleção prefere o ponto
menos restritivo operável (máximo recall sob a guarda). Pela medição da
D-03c, os pontos operáveis são (4,2) em diante — 942.187; 489.531;
1.118.233 (reprovado); 493.338; 249.735 —, mas **nenhuma contagem é
predita**: o grafo cresce e a bateria re-mede.

**Incremento editorial do conjunto emitido**: recomputa-se a emissão
top-K **excluindo as vitórias da janela recente** (30 dias,
`recent_wins` do export da cadeia D-03); o incremento diário é
`|E_completo − E_antigo| / 30`, onde `E_antigo` é o top-K do snapshot
sem a janela — pares que entram na emissão por efeito de vitórias
recentes (entrantes novos ou promovidos ao prefixo).

**Parte A — validação sintética** (ArangoDB de bateria descartável,
integração, 5 seeds; população **idêntica** à da D-03b/D-03c — 59
contratos/seed — para manter os controles degenerados comparáveis):
emissão top-K nos pontos de controle `{(3,1), (3,2), (4,2), (2,2)}`,
com K = 1 (truncamento ativo, âncoras T1/T2) e K = 500 (truncamento
vacuoso, equivalência de prefixo contra o conjunto completo ordenado).

**Parte A-stress — sintética escalada** (mesma população **mais** o
comprador `BIG-B` com 200 fornecedores exclusivos ×3 vitórias, bloco
`stress` herdado da D-03c): âncoras de escala da emissão (T4) e
regressão das âncoras de blocking da D-03c.

**Parte B — varredura real** (grafo de produção, **somente leitura**,
janela de congelamento): export de elegibilidade (query da cadeia
D-03), projeções bloqueadas aritméticas nos 6 pontos, seleção do ponto
pela regra acima, derivação bloqueada **materializada** apenas no ponto
selecionado (com `tracemalloc`), emissão top-K, equivalência de prefixo
contra o conjunto completo ordenado no mesmo ponto, dupla contagem
(projeção = incidências), incremento editorial (30 dias e robustez 60
dias), cobertura siafi e a emissão executada **duas vezes** na mesma
janela.

**Parte C — evidência**: o pacote `graph_batch` passa a registrar
`top_k` e `qualified_count` no bloco `reproduction` (além de
`min_buyers`, já registrado; default `top_k = null` = sem truncamento
para pacotes anteriores — compatibilidade retroativa declarada, padrão
da D-03b); o snapshot de elegibilidade segue **completo**. A reprodução
re-deriva, re-ordena e re-trunca deterministicamente e deve casar
exatamente; pacotes das D-03/D-03b/D-03c (sem `top_k`) reproduzem
inalterados.

## 5. Métricas primárias

- Projeção bloqueada aritmética por ponto da grade (sem materialização)
  — base da regra de seleção.
- Conjunto emitido (ordenado, ≤ K pares) e cobertura
  `K_efetivo / qualificados`, no ponto selecionado.
- Equivalência de prefixo: emissão == primeiros K do conjunto completo
  ordenado (bit a bit).
- Incremento editorial diário do conjunto emitido (janela 30 dias;
  robustez 60 dias).
- Tempo de wall-clock da varredura e pico de heap Python
  (`tracemalloc`) da derivação materializada no ponto selecionado.
- Determinismo: duas execuções da emissão na mesma janela,
  byte-idênticas.

## 6. Predições (numéricas, falsificáveis)

Partes A/A-stress/C são **âncoras exatas** (estrutura plantada, cômputo
determinístico) — qualquer desvio refuta; veredito por seed, 5 seeds
(`[7, 17, 27, 37, 47]`, as da cadeia). Parte B é falsificável por
invariante e por limite operacional/editorial, sem predição de contagem.

- **T1 — truncamento ativo, desempate por `wins_sum` (sintético,
  exata).** No controle (2,2) com K = 1: o conjunto qualificado é
  exatamente `{X1,X2}` (buyer_count 2, wins_sum 12) e `{Y1,Y2}`
  (buyer_count 2, wins_sum 11); a emissão é **exatamente**
  `[{X1,X2}]`, e o descriptor registra `qualified_count = 2`,
  `top_k = 1`, cobertura 0,5. *Refutada* com qualquer divergência.
- **T2 — truncamento ativo, desempate por `buyer_count` (sintético,
  exata).** Em (3,1) com K = 1: o conjunto qualificado tem **5 pares**
  (âncora da D-03: 3 do comprador plantado, o itinerante, o de
  fronteira); a emissão é **exatamente** `[{X1,X2}]` — único par com
  buyer_count 2 (os demais têm 1). *Refutada* com qualquer divergência.
- **T3 — equivalência de prefixo e evidência (sintético, exata).** Em
  cada ponto de `{(3,1), (3,2), (4,2), (2,2)}` com K = 500 (truncamento
  vacuoso), a emissão é **exatamente** o conjunto completo ordenado.
  Pacote `graph_batch` com `top_k`: reprodução com `match = true` em
  todos os sinais emitidos; removendo **uma** linha do snapshot,
  `integrity = false` e `match = false`; pacotes das baterias
  anteriores (sem `top_k`) reproduzem inalterados. *Refutada* com
  qualquer divergência.
- **T4 — âncoras de escala (stress sintético, exata).** Com `BIG-B`
  plantado: projeção bloqueada em (3,2) = **2** (regressão da D-03c);
  emissão em (3,2) com K = 500 = **exatamente `[{X1,X2}]`** (único par
  qualificado — truncamento vacuoso sob blocking); emissão em (3,1) com
  K = 500 = **exatamente 500 pares** (truncamento ativo sobre 19.905
  pares qualificados), cujo primeiro elemento é `{X1,X2}` (buyer_count
  2 contra 1 dos demais); varredura stress completa **< 60 s** por
  seed. *Refutada* com qualquer divergência de contagem ou estouro de
  tempo.
- **T5 — existência de ponto operável (real).** Ao menos um ponto da
  grade `{3,4,5} × {2,3}` tem projeção bloqueada aritmética
  **< 1.000.000**; o ponto selecionado é o primeiro na ordem
  pré-registrada. *Refutada* se nenhum ponto qualifica — o blocking não
  basta em nenhum ponto e a amostragem de recall aproximado (a) vira
  PR-D-03e.
- **T6 — viabilidade operacional no ponto selecionado (real).**
  Varredura completa **< 600 s**; pico de heap Python da derivação
  materializada no ponto selecionado **< 256 MiB**; dupla contagem:
  projeção bloqueada aritmética = incidências (par, comprador)
  materializadas, exato. *Refutada* por qualquer estouro ou divergência
  — mesmo destino de T5.
- **T7 — orçamento editorial (real, hipótese central).** O conjunto
  emitido tem **≤ 500** pares (exato, por construção — verificado, não
  assumido) e o incremento editorial diário do conjunto emitido (janela
  30 dias, seção 4) é **≤ 20**. *Refutada* se o incremento estoura: a
  rotatividade do prefixo top-K — não o estoque — é o novo gargalo, e o
  encaminhamento é a revisão do orçamento com a banca (decisão
  editorial) ou estabilização do ranking (PR-D-03e), não mais escala.
- **T8 — equivalência de prefixo (real).** No ponto selecionado, a
  emissão é **exatamente** o prefixo de K do conjunto completo ordenado
  (derivação completa viável, pois o ponto passou na guarda).
  *Refutada* com qualquer divergência.
- **T9 — determinismo da emissão (real).** Duas execuções da emissão
  top-K na mesma janela de congelamento produzem saída
  **byte-idêntica**. *Refutada* com qualquer divergência.

## 7. Controles e invariantes

- **Controles degenerados de truncamento** (T1/T2): K = 1 sobre
  populações com 2 e 5 pares qualificados exercita os dois desempates
  da ordenação e o descriptor de cobertura; K = 500 vacuoso (T3) é a
  regressão da emissão completa da D-03c.
- **Regressão do blocking**: as âncoras da D-03c (projeções bloqueadas
  exatas sintéticas, stress = 2 em (3,2)) são re-verificadas; o teste
  de equivalência bit a bit bloqueado × não bloqueado segue na suíte
  rápida, intocado.
- **Monotonicidade**: projeção bloqueada não cresce ao subir `w` ou
  `n`, e ≤ projeção não bloqueada em todo ponto — verificado na grade
  real e na sintética; violação refuta o runner.
- **Consistência e cobertura (real)**: Σ wins do histograma = arestas
  `won` elegíveis; cobertura siafi **≥ 90%**. Abaixo disso a bateria é
  **inconclusiva** (regime degradado), não refutada.
- **Congelamento**: nenhuma ingestão durante a Parte B; se o grafo
  mudar entre queries, a run é descartada e reexecutada (precedentes da
  cadeia).
- **Guarda de não materialização**: fora do ponto selecionado, nenhuma
  derivação é materializada na Parte B — as projeções aritméticas
  bastam à regra de seleção.

## 8. Critério de encerramento

Bateria **bem-sucedida** com T1–T4 exatas nas 5 seeds e T5–T9
satisfeitas: a emissão top-K declarada sobre a derivação bloqueada
satisfaz os limites operacionais **e** o orçamento editorial, e o
caminho fica **habilitado — não autorizado** — a promover a semântica
top-K ao `task_detect` (ponto selecionado, K = 500), mediante decisão
humana registrada que também decida o destino do backlog histórico
(seção 3). **Refutada (escala)** se T5 ou T6 estouram: nenhum ponto da
grade é operável nem com blocking — a amostragem de recall aproximado
declarado (direção (a)) vira PR-D-03e. **Refutada (editorial)** se T7
estoura: o truncamento resolve o estoque mas não a rotatividade — o
próximo passo é a revisão do orçamento com a banca ou a estabilização
do ranking, registrado como PR-D-03e. **Refutada (implementação)** se
T1–T4, T8 ou T9 divergem: bug no runner ou na ordenação; investigar
antes de reexecutar. **Inconclusiva** se o regime real degrada
(cobertura < 90% ou grafo instável sem janela de congelamento viável) —
publicada com o mesmo rigor. Em qualquer veredito, defaults de produção
e guarda **não mudam** sem decisão humana registrada.

## 9. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Emissão top-K**: função pura em `src/capiba/detection/graphs.py` —
   `ranked_emission(pair_buyers, top_k)` (ordenação da seção 4, prefixo
   truncado, descriptor `top_k`/`qualified_count`/cobertura); derivação
   bloqueada e não bloqueada preservadas.
2. **Evidência**: `evidence/packages.py` registra `top_k` e
   `qualified_count` no bloco `reproduction` do pacote `graph_batch`
   (default `null` = sem truncamento, compatibilidade retroativa);
   reprodução re-trunca deterministicamente.
3. **Runner**: `battery_collusion.py` estendido — modo de emissão
   top-K, regra de seleção do ponto, incremento editorial do conjunto
   emitido, dupla execução de determinismo; dispatch por presença do
   bloco `emission` na config (padrão dos modos anteriores); saída
   bruta em `results/detect/D-03d/`.
4. **Testes de guarda**: truncamento e prefixo exatos na suíte rápida
   (sintético pequeno, offline); Parte A/A-stress/C como
   `@pytest.mark.slow` + `@pytest.mark.integration`; Parte B como
   execução assistida contra o cluster local, em janela de
   congelamento.
5. **Após o R-D-03d** (somente com veredito de sucesso e decisão
   humana): promoção da emissão top-K ao `task_detect`, saneamento do
   backlog histórico, docs (`AGENTS.md`, `docs/gaps.md`) atualizados.
   Defaults e guarda de produção: fora deste pacote.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana), na sequência do
  veredito da D-03c (`R-D-03c.md`: blocking de recall exato correto e
  ~7× mais barato, mas refutado como hipótese de escala nos pontos
  (3,2)/(3,3) — projeções bloqueadas de 2.096.013 e 1.118.233 acima da
  guarda de 1M, pico de 329,4 MiB ≥ 256 MiB) e da medição da fila real
  pela D-11 (809.220 sinais `collusion_network` pendentes, zero
  revisões humanas concluídas — relatório de volume do gate P1,
  2026-08-21). Dos caminhos pré-registrados no R-D-03c §3 e R-D-03b §3,
  este PR adota a **direção (c): blocking + emissão ranqueada com
  orçamento editorial explícito (top-K = 500 declarado)**; a amostragem
  de recall aproximado (a) e a elevação de pisos (b) ficam documentadas
  como alternativas **não adotadas** (seção 1), com a justificativa da
  reintrodução do top-k contra a objeção registrada no PR-D-03c §2. A
  decisão final é humana.
- 2026-08-21: aprovado e **executado** — veredito **success** em
  `docs/results/R-D-03d.md`: T1–T4 exatas nas 5 seeds; T5–T9 satisfeitas
  na varredura real (ponto selecionado **(5,2)** — projeção bloqueada
  610.960 < 1M; (4,2) estourou a guarda com o crescimento do grafo para
  693.261 arestas elegíveis —; varredura 63,5 s, pico 90,5 MiB, dupla
  contagem exata, 500 emitidos sobre 86.957 qualificados — cobertura
  declarada 0,575% —, incremento editorial 0,4333/dia ≤ 20, prefixo
  bit a bit, emissão byte-idêntica em duas execuções). O caminho fica
  **habilitado — não autorizado** — à promoção da semântica top-K ao
  `task_detect` ((5,2), K = 500): pendente de **decisão humana
  registrada**, que também decide o destino do backlog histórico
  (seção 3). Defaults e guarda de produção inalterados.
- 2026-08-21: **decisão humana registrada — promoção autorizada.** A
  semântica top-K é promovida ao `task_detect`: derivação **bloqueada**
  (recall exato, D-03c) + `ranked_emission` com `DETECTION_COLLUSION_TOP_K`
  (default 500, config nova em `src/capiba/config.py`); o pacote
  `graph_batch` de produção grava o descriptor (`top_k`,
  `qualified_count`). **Decisão sobre o backlog**: os 809.220 sinais
  legados **não são reprocessados** — ficam como estão na fila; a próxima
  run já emite top-K. **Divergência registrada (parâmetros de produção)**:
  a bateria validou o ponto (5,2) e o §8 menciona a promoção "no ponto
  selecionado", mas o §3 é explícito que os **defaults de produção de
  `min_wins`/`min_buyers` não mudam** — o PR não fixa o ponto de produção.
  A promoção aplica portanto os defaults vigentes (`MIN_WINS = 3`,
  `MIN_BUYERS = 1`) **apenas com o truncamento top-K**; promover o ponto
  (5,2) validado fica como refinamento futuro (candidato a PR-D-03e),
  pois mudar os pisos exigiria decisão específica. A guarda
  `DETECTION_COLLUSION_MAX_PAIRS` segue inalterada (derivação pulada acima
  de 1M de pares projetados, snapshot preservado como evidência).
