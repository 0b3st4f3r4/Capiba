# PR-D-02 — Validação dos operadores de grafo (semântica adaptada ao grafo real)

- **Pré-registro**: bateria D-02
- **Criado em**: 2026-08-18
- **Última atualização**: 2026-08-18
- **Status**: rascunho registrado, não executado (aguardando aprovação humana)
- **Alvo**: `detect_collusion` e `trace_ownership`
  (`src/capiba/detection/graphs.py`), na semântica **adaptada** definida na
  seção 3 — a versão a implementar após a aprovação deste registro
- **Configuração**: `experiments/detect/D-02.json` (declarativa, seeds
  inclusas; esboço, revisada junto com este PR)

## 1. Pergunta

Os operadores de grafo, na semântica adaptada ao grafo que de fato existe
(vértices `contracts`/`suppliers`/`buyers`/`companies`/`partners`; arestas
`won`, `partner_of`, `owns`), recuperam a verdade de terreno plantada com
precisão e revocação exatas?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético sobre infra real**: o grafo é montado num ArangoDB de
  bateria (integração — a bateria não roda offline como D-01), populado
  exclusivamente com vértices e arestas sintéticos declarados na config.
  A bateria mede a *fidelidade dos operadores à sua semântica declarada*;
  não prova poder de detecção sobre dados reais de licitação ou de quadro
  societário.
- O regime sintético **não prova**: comportamento em grafos densos reais
  (milhões de arestas `won`/`partner_of`), ruído de dados reais (CNPJs
  inconsistentes, duplicatas, órgãos sem código SIAFI), nem que a
  semântica adaptada (pares que alternam vitórias no mesmo comprador)
  corresponde a conluio real — isso exigiria rótulos externos, fora do
  alcance desta bateria.
- **`anomalous_geography` está FORA do escopo do D-02.** O operador
  depende de `latitude`/`longitude` em `suppliers` e `bids`, que nenhuma
  fonte popula hoje; sua validação exige uma fonte de geocodificação
  futura e fica para um PR posterior.
- A coleção `bids` e as arestas `participates`/`owns` existem vazias em
  produção; a bateria planta `owns` diretamente (a derivação de `owns` a
  partir de sócios PJ na normalização RFB é etapa posterior, fora deste
  registro).

## 3. Semântica adaptada dos operadores sob teste

O scaffold atual não consulta o grafo real: `detect_collusion` itera a
coleção `bids` (vazia) e `trace_ownership` atravessa OUTBOUND a partir de
`companies/<cnpj>` num grafo cuja única aresta outbound de `companies`
seria `owns` (vazia). A bateria testa a versão **adaptada**, assim
definida:

- **`detect_collusion(db, min_wins)`** — para cada comprador *b* (ligação
  via atributo `buyer` do documento `contracts`, já que não existe aresta
  contrato→comprador), seja *S_b* o conjunto de fornecedores com ≥
  `min_wins` arestas `won` para contratos de *b*. A saída é o conjunto de
  pares `{s1, s2} ⊆ S_b`, `s1 ≠ s2` — fornecedores que alternam vitórias
  no mesmo comprador. Determinística e exata por construção.
- **`trace_ownership(cnpj, max_depth, db)`** — caminhos **simples** (sem
  vértice repetido) OUTBOUND a partir de `companies/<cnpj>` sobre a aresta
  `owns`, de profundidade 1..`max_depth`. Retorna a lista de caminhos
  (sequências de `_key`), sem duplicatas.

## 4. Desenho

População sintética por seed (estrutura do grafo idêntica em todas as
seeds; a seed só randomiza campos neutros — nomes, datas, valores):

**Conluio (`won` sobre contratos agrupados por comprador):**

- Comprador plantado `PLANT-B`: fornecedores `C1`, `C2`, `C3` com **4
  vitórias cada** (≥ `min_wins` = 3) → os 3 pares do grupo em conluio.
- Fornecedor fronteira `C4`: **2 vitórias** em `PLANT-B` (< `min_wins`) —
  não entra em nenhum par com `min_wins` = 3; entra com `min_wins` = 2.
- Controle mesmo-comprador `CTRL-B1`: 3 fornecedores com **2 vitórias
  cada** — nenhum atinge `min_wins`; zero pares.
- Controle comprador-solo: 4 fornecedores, cada um com **4 vitórias** num
  comprador exclusivo (`SOLO-B1..B4`) — cada comprador tem um único
  fornecedor elegível; zero pares.

Total: 36 contratos sintéticos por seed (14 em `PLANT-B`, 6 em `CTRL-B1`,
16 nos compradores-solo).

**Titularidade (`owns` sobre `companies`):**

- Cadeia plantada `C-A → C-B → C-C → C-D` (profundidade 3).
- Empresa isolada `C-E` (sem arestas).
- Ciclo plantado `C-F ⇄ C-G` (arestas nos dois sentidos).

## 5. Predições (numéricas, falsificáveis)

Todas são **âncoras exatas** — a estrutura plantada é conhecida e os
operadores são determinísticos, logo não há bandas: qualquer desvio
refuta. Veredito por seed; a predição falha se divergir em qualquer uma
das 5 seeds.

- **P1 — revocação do conluio.** Com `min_wins` = 3, os 3 pares plantados
  `{C1,C2}`, `{C1,C3}`, `{C2,C3}` aparecem na saída de `detect_collusion`.
  *Refutada* se qualquer par estiver ausente.
- **P2 — precisão do conluio.** Com `min_wins` = 3, a saída contém
  **exatamente** esses 3 pares: zero pares envolvendo `C4`, zero pares dos
  controles (mesmo-comprador e comprador-solo). *Refutada* com qualquer
  falso positivo.
- **P3 — fronteira de `min_wins`.** Com `min_wins` = 2, a saída contém
  **exatamente 9 pares**: em `PLANT-B`, os 3 de P1 mais `{C4,C1}`,
  `{C4,C2}`, `{C4,C3}` (C(4,2) = 6); em `CTRL-B1`, os 3 pares entre os
  controles `{K1,K2}`, `{K1,K3}`, `{K2,K3}` (C(3,2) = 3); zero nos
  compradores-solo. *Refutada* com qualquer divergência de conjunto.
- **P4 — cadeia de titularidade exata.** `trace_ownership("C-A", 3)`
  retorna **exatamente 3 caminhos**: `[C-A,C-B]`, `[C-A,C-B,C-C]`,
  `[C-A,C-B,C-C,C-D]`. *Refutada* com qualquer divergência de conjunto.
- **P5 — fronteira de profundidade.** `trace_ownership("C-A", 2)` retorna
  **exatamente 2 caminhos** e `C-D` **nunca** aparece. *Refutada* se `C-D`
  aparecer ou a contagem ≠ 2.
- **P6 — isolamento e ciclo.** `trace_ownership("C-E", 3)` retorna lista
  **vazia**; `trace_ownership("C-F", 3)` sobre o ciclo retorna
  **exatamente 1 caminho** `[C-F,C-G]` (caminhos simples: a volta a `C-F`
  é bloqueada). *Refutada* com qualquer divergência.

## 6. Controles e invariantes

- Controles internos: os fornecedores controle (P2/P3) e a empresa
  isolada (P6) são os baselines; não há baseline externo — a semântica
  declarada na seção 3 é a referência.
- Invariante de execução: a união dos pares retornados com `min_wins` = 3
  é subconjunto estrito dos pares com `min_wins` = 2, por seed, sempre
  (monotonicidade do limiar).
- Reprodutibilidade: a mesma seed reproduz o mesmo grafo e a mesma saída
  exata; a bateria roda contra um ArangoDB descartável criado e derrubado
  pelo próprio runner.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P6 exatas nas 5 seeds. Qualquer refutação
é publicada em `docs/results/R-D-02.md` com a causa investigada, e a forma
corrigida vira `PR-D-02b.md` antes de nova execução. O sucesso habilita —
não autoriza automaticamente — a conexão dos operadores ao pipeline (item
do `docs/gaps.md`), que exige decisão separada sobre limiares de produção.

## 8. Execução

O runner de grafos (a implementar com TDD **após** a aprovação deste
registro, provavelmente um modo `--graph` em `scripts/detect_battery.py`
ou módulo irmão) lê `experiments/detect/D-02.json`, monta o grafo
sintético num ArangoDB de bateria (testes marcados
`@pytest.mark.integration`), invoca os operadores adaptados in-process e
grava a saída bruta em `results/detect/D-02/<seed>.jsonl`. Nenhum código
de bateria existe na data deste registro.

## Revisões

- 2026-08-18: criação (rascunho para revisão humana).
