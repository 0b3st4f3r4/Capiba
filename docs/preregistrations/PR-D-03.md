# PR-D-03 — Calibração do `collusion_network` em volume real (min_wins + evidência reproduzível)

- **Pré-registro**: bateria D-03
- **Criado em**: 2026-08-19
- **Última atualização**: 2026-08-19
- **Status**: aprovado e **executado** em 2026-08-19 — veredito
  `inconclusive` (P1–P7 `success`; P8: nenhum candidato dentro do
  orçamento de triagem); resultado em `docs/results/R-D-03.md`
- **Alvo**: calibração do limiar `DETECTION_COLLUSION_MIN_WINS` (default 3,
  placeholder validado apenas no regime sintético da D-02) sobre o **grafo
  real acumulado** — contagem descritiva de 2026-08-19: 156.282 contratos,
  52.220 fornecedores, 150.680 arestas `won`, 7.424 compradores distintos
  por `buyer.siafi_code` — mais a **reprodutibilidade do pacote de
  evidência** do sinal `collusion_network` (dívida O9: hoje
  `reproducible: false` em `src/capiba/evidence/packages.py`)
- **Configuração**: `experiments/detect/D-03.json` (declarativa, seeds
  inclusas; esboço, revisada junto com este PR)

## 1. Pergunta

Sobre o grafo real acumulado, qual o menor `min_wins` dentre os candidatos
declarados (seção 3) que mantém o volume de pares `collusion_network`
dentro do orçamento de triagem editorial pré-registrado — e o pacote de
evidência do sinal passa a se reproduzir exatamente a partir de um
snapshot de elegibilidade content-addressed?

## 2. Regime medido e limitações (obrigatório)

- Regime **real descritivo + decisão pré-registrada**: a distribuição real
  de vitórias por (comprador, fornecedor) é desconhecida a priori e
  **nenhuma contagem real é predita** (seria não-falsificável). O que se
  pré-registra é (i) a regra de decisão do limiar, (ii) invariantes
  estruturais e operacionais falsificáveis sobre a varredura real e
  (iii) âncoras exatas sintéticas do runner de calibração e da evidência.
- A calibração mede **viabilidade operacional e orçamento de triagem**;
  **não** prova que os pares elegíveis correspondem a conluio real — isso
  exigiria rótulos externos, fora do alcance desta bateria (a triagem
  humana do O10 é o filtro editorial).
- O grafo societário está **vazio** na data deste registro
  (`companies`/`partners`/`partner_of`/`owns` = 0): `trace_ownership` e a
  calibração de `max_depth` ficam **fora do escopo**; o candidato natural
  é um PR posterior, após a carga RFB no grafo.
- A reprodução da evidência cobre a **derivação dos pares a partir do
  snapshot de elegibilidade** (linhas `{buyer, supplier, wins}` com
  `wins ≥ min_wins`). A agregação AQL que produz o snapshot é guardada
  pelos testes da D-02 e pelo invariante de dupla contagem (seção 6), não
  pela reprodução — limitação declarada: o snapshot não permite auditar
  fornecedores **abaixo** do limiar (ausentes do pacote por construção).
- O score segue **binário 1,0** — score graduado é refinamento posterior
  (`PR-D-03b` ou PR próprio), fora deste registro.

## 3. Regra de calibração (declarada a priori)

- **Candidatos**: `min_wins ∈ {3, 4, 5, 6, 8, 10}`. Piso 3 declarado a
  priori: `min_wins = 2` exige apenas duas vitórias no mesmo comprador —
  recorrência fraca demais para sustentar uma alegação editorial; não é
  revisitado nesta bateria.
- **Orçamento de triagem** (O10: chave estável por par, revisor humano por
  sinal; o backlog é carga única, o incremento é diário):
  - `B_backlog = 500` pares totais no grafo completo (≈ 25 dias úteis de
    fila a 20 revisões/dia para zerar a carga inicial);
  - `B_daily = 20` pares/dia, estimado como
    `(pares_grafo_completo − pares_sem_últimos_30_dias) / 30`, onde a
    exclusão filtra contratos com `signature_date` nos últimos 30 dias em
    relação à data da varredura.
- **Regra de decisão**: `w*` = o **menor** candidato tal que
  `pares_totais(w) ≤ 500` **e** `incremento_30d(w) ≤ 20/dia`. Se nenhum
  candidato satisfaz o orçamento, a bateria é **inconclusiva** (refutação
  da calibrabilidade neste regime), publicada com a mesma dignidade em
  `docs/results/R-D-03.md`, e a forma corrigida (novos candidatos ou novo
  orçamento, justificados) vira `PR-D-03b`.
- O valor calibrado **só vira default** em `src/capiba/config.py` após o
  R-D-03, por decisão humana registrada — o sucesso da bateria habilita,
  não autoriza automaticamente.

## 4. Desenho

**Parte A — validação sintética do runner** (ArangoDB de bateria
descartável, integração, mesmo padrão da D-02). População plantada
idêntica à da D-02 (estrutura igual nas 5 seeds; a seed só randomiza
campos neutros — nomes, valores e datas exatas **dentro** das janelas
plantadas): `PLANT-B` com C1–C3 ×4 vitórias e C4 ×2; `CTRL-B1` com
K1–K3 ×2; `SOLO-B1..B4` com um fornecedor exclusivo ×4 cada — 36
contratos. Datas plantadas: os 14 contratos de `PLANT-B` com
`signature_date` nos **últimos 30 dias** da data da varredura; os 22
contratos de controle/solo com **mais de 30 dias**. O runner em modo
contagem deve reproduzir exatamente o histograma, as contagens por
candidato e o incremento declarados nas predições.

**Parte B — varredura real** (grafo de produção, **somente leitura**,
janela de congelamento sem ingestões durante a medição): o runner
(1) mede o histograma de vitórias por (comprador, fornecedor) e a
cobertura de `buyer.siafi_code`; (2) conta pares por candidato em modo
**aritmético** (`C(k_b, 2)` por comprador, sem materializar pares);
(3) mede o incremento de 30 dias e, como descritor de robustez não
ancorado, o de 60 dias; (4) aplica a regra da seção 3; (5) materializa o
conjunto de pares **apenas** no `w*` calibrado e somente se
`pares_totais(w*) ≤ B_backlog`.

**Parte C — evidência reproduzível** (dívida O9): novo pacote
`kind: "graph_batch"` no schema `capiba.signal-package/1` — snapshot de
elegibilidade (linhas `{buyer, supplier, wins}` ordenadas, com
`wins ≥ min_wins`), `min_wins`, versão de código e `snapshot_sha256`;
o manifesto do `collusion_network` passa a referenciar `graph_sha256` e
`reproducible: true`; `reproduce_signal` ganha dispatch por `kind`,
recomputando os pares por combinação sobre o snapshot e comparando o
sinal emitido. O `task_detect` passa a armazenar o pacote de grafo quando
o sinal é emitido (best-effort, como os demais pacotes).

## 5. Predições (numéricas, falsificáveis)

Partes A e C são **âncoras exatas** (estrutura plantada, cômputo
determinístico) — qualquer desvio refuta; veredito por seed, 5 seeds.
Parte B é falsificável por invariante, sem predição de contagem.

- **P1 — histograma sintético exato.** Vitórias por (comprador,
  fornecedor): `{4: 7, 2: 4}` — 7 pares a 4 vitórias (C1–C3 em `PLANT-B`
  + 4 fornecedores-solo) e 4 pares a 2 vitórias (C4 + K1–K3); zero em
  qualquer outro valor. *Refutada* com qualquer divergência.
- **P2 — contagem por candidato exata.** Pares em modo aritmético:
  `w = 3 → 3`; `w = 4 → 3`; `w ∈ {5, 6, 8, 10} → 0`. *Refutada* com
  qualquer divergência.
- **P3 — incremento de 30 dias exato.** Com a janela plantada:
  `pares_recentes(w = 3) = 3` (incremento 0,1/dia) e
  `pares_recentes(w = 2) = 6` (0,2/dia — medido como controle da janela,
  embora w = 2 esteja fora dos candidatos). *Refutada* com qualquer
  divergência.
- **P4 — reprodução da evidência exata.** Todo sinal `collusion_network`
  do pacote sintético reproduz com `match = true`; removendo **uma** linha
  do snapshot, a reprodução retorna `integrity = false` e `match = false`.
  *Refutada* com qualquer divergência.
- **P5 — dupla contagem (real).** A contagem de pares por candidato via
  agregação AQL iguala **exatamente** a recomputação em Python a partir
  das linhas `{buyer, supplier, wins}` exportadas. *Refutada* com
  qualquer divergência — indica bug no runner ou na agregação.
- **P6 — consistência do histograma (real).** A soma dos wins do
  histograma iguala o número de arestas `won` cujo contrato tem
  `buyer.siafi_code` não nulo; a cobertura de siafi (parcela das arestas
  `won` elegíveis) é **≥ 90%**. *Refutada* abaixo disso — regime
  degradado, bateria inconclusiva (o grafo não representa o silver).
- **P7 — viabilidade operacional (real).** A varredura completa
  (histograma + 6 candidatos + incremento) termina em **< 10 minutos** no
  cluster local e **nunca** materializa mais pares que `B_backlog`.
  *Refutada* por estouro de tempo ou materialização acima do orçamento.
- **P8 — decisão única (real).** A regra da seção 3 produz um único `w*`;
  se nenhum candidato satisfaz o orçamento, a bateria é declarada
  **inconclusiva** (não "sucesso") e documentada em R-D-03.

## 6. Controles e invariantes

- **Monotonicidade do limiar** (herdada da D-02): `pares(w)` é não
  crescente em `w` — verificado sobre a varredura real; violação refuta o
  runner.
- **Dupla contagem** (P5) é o controle interno da agregação real: dois
  caminhos independentes (AQL e Python) devem convergir exatamente.
- **Controle temporal**: o incremento de 60 dias é reportado como
  descritor de robustez (não ancorado); divergência qualitativa entre as
  janelas (ex.: incremento concentrado em poucos compradores) é discutida
  no R-D-03.
- **Congelamento**: nenhuma ingestão roda durante a Parte B — a varredura
  é repetível bit a bit enquanto o grafo não muda; a Parte A é reprodutível
  por seed.
- Invariante de **não materialização**: pares só são materializados no
  `w*` calibrado e sob o orçamento (P7) — a varredura não pode OOMKillar
  por explosão combinatória.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P4 exatas nas 5 seeds e P5–P7 satisfeitas,
com P8 produzindo `w*` único. **Inconclusiva** (refutação da
calibrabilidade) se P6 falha por cobertura ou P8 falha por orçamento —
publicada com o mesmo rigor. Refutações de runner (P1–P5) investigadas e
corrigidas via `PR-D-03b` antes de nova execução. O sucesso habilita —
não autoriza automaticamente — a mudança do default em `config.py`, o flip
de `reproducible` para o `collusion_network` e a atualização do
`AGENTS.md`/`docs/gaps.md`.

## 8. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Runner**: `src/capiba/detection/battery_collusion.py` (dispatch
   `"runner": "collusion"` no `scripts/detect_battery.py`, padrão D-02/
   D-05) lendo `experiments/detect/D-03.json`, com modos `count`
   (aritmético) e `materialize` (apenas `w*` sob orçamento); grava a saída
   bruta em `results/detect/D-03/`.
2. **Evidência**: pacote `graph_batch` e reprodução por snapshot em
   `src/capiba/evidence/packages.py`, com testes unitários e atualização
   do BDD do O9 (`tests/bdd/`); o `task_detect` passa a armazenar o
   pacote de grafo ao emitir `collusion_network` (best-effort).
3. **Testes de guarda**: Parte A/C como `@pytest.mark.slow` +
   `@pytest.mark.integration` (ArangoDB de bateria descartável); Parte B
   como execução assistida do runner contra o cluster local, em janela de
   congelamento.
4. **Após o R-D-03**: default calibrado em `config.py`/`.env.example`,
   `reproducible: true` para `collusion_network`, e docs (`AGENTS.md`,
   `docs/gaps.md`) atualizados.

## Revisões

- 2026-08-19: criação (rascunho para revisão humana), após contagem
  descritiva do grafo acumulado (156.282 contratos, 150.680 arestas `won`,
  7.424 compradores siafi; grafo societário vazio) e confirmação de que o
  documento `contracts` carrega `signature_date` (base do incremento de 30
  dias).
- 2026-08-19: aprovação humana registrada; execução conforme seção 8.
- 2026-08-19: bateria executada — runner `battery_collusion.py`, pacote de
  evidência `graph_batch` e varredura real (24 s, 152.669 arestas `won`
  elegíveis). Veredito `inconclusive`: P1–P7 exatas/satisfeitas, P8 sem
  candidato dentro do orçamento (627.592 pares em w=3; 15.107 em w=10) —
  resultado em `docs/results/R-D-03.md`; refinamento vira `PR-D-03b`.
