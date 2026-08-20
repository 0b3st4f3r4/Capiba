# PR-D-07 — Entity resolution de fornecedores e sócios (O5, primeira fatia)

- **Pré-registro**: bateria D-07
- **Criado em**: 2026-08-20
- **Última atualização**: 2026-08-20
- **Status**: executado em 2026-08-20 — veredito **refuted** (P1–P6
  confirmadas; P7 refutada, revocação 0,025 — causa estrutural, ver
  `docs/results/R-D-07.md`); forma corrigida em `PR-D-07b.md`
- **Alvo**: resolução de entidades — (a) deduplicação de sócios PF entre
  empresas (mesma pessoa em vários quadros societários; hoje cada
  `partner_id` é um hash por empresa, gerando vértices duplicados no
  grafo) e (b) link supplier↔company (contratos, CNPJ 14 dígitos ↔
  Receita, `cnpj_basico`). Merges só entram no grafo **acima do limiar
  pré-registrado** (aresta `same_as`), nunca abaixo. Semântica de
  referência em `src/capiba/detection/entities.py` (a implementar).
- **Configuração**: `experiments/detect/D-07.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

O matcher declarado na seção 3, com os pesos e o limiar pré-registrados,
(i) reproduz **exatamente** os merges plantados no regime sintético
(inclusive as disciplinas "homônimo não é a mesma pessoa" e "nome ruidoso
com mesmo documento é a mesma pessoa"), (ii) sustenta precisão ≥ 0,90 no
benchmark real OpenSanctions Pairs e (iii) garante o invariante
estrutural de que nenhuma aresta `same_as` existe abaixo do limiar?

## 2. Regime medido e limitações (obrigatório)

- Dois regimes: **sintético exato** (offline, padrão D-01..D-06 — âncoras
  exatas sobre merges plantados) e **benchmark real** OpenSanctions Pairs
  (arquivo plano `pairs-20251209.json.gz` em
  `https://data.opensanctions.org/contrib/training/`, snapshot 2025-12-09,
  uso não-comercial;
  amostra determinística por reservoir sampling com seed declarada —
  ver seção 4). O benchmark mede o núcleo **nome + documento** do
  matcher, que é o vocabulário comum aos dois regimes; a faixa etária
  (feature RFB-específica) só é medida no sintético.
- O snapshot licenciado novo ("grouped pairs", delivery token) está FORA;
  usa-se o arquivo plano público. Se a URL mudar, o R-D-07 registra a URL
  e a data efetivas do snapshot usado.
- **Semântica conservadora por desenho**: sem evidência documental, o
  score máximo é o peso do nome (< limiar) — homônimos puros nunca
  mergeam. A revocação sobre o benchmark será necessariamente modesta;
  declara-se a banda esperada (P7) como busca de calibração, não como
  âncora exata.
- **Entity resolution não é verdade editorial**: uma aresta `same_as` é
  uma hipótese computada acima de limiar, sujeita à triagem humana (O10)
  antes de qualquer publicação.
- **Merge físico de vértices está FORA** — a integração grava a aresta
  `same_as` (reversível, auditável); colapso de vértices é decisão
  posterior, com PR próprio se vier.
- Fuzzy por nome para o screening de sanções (CEAF/PEPs) está FORA —
  depende deste matcher, mas é aplicação com PR próprio (PR-D-06b).

## 3. Semântica do matcher (declarada)

`entities.py` puro e determinístico:

- **Normalização de nome**: maiúsculas, sem acentos, sem pontuação,
  espaços colapsados; tokens ordenados. Similaridade de nome =
  `difflib.SequenceMatcher.ratio()` sobre as formas normalizadas.
- **Features de pessoa** (pesos pré-registrados): nome 0,6; documento
  (CPF parcial `***123456**` — dígitos visíveis iguais; CNPJ/CPF completo
  igual) 0,3; faixa etária igual 0,1. Score = soma direta dos pesos das
  features satisfeitas, **sem renormalização** — feature ausente vale
  zero (desenho conservador: nome sozinho tem teto 0,6 < limiar).
- **Link supplier↔company**: determinístico por documento —
  `supplier.cnpj[:8] == company.cnpj_basico` ⇒ match exato (score 1,0);
  sem CNPJ no contrato, não há link nesta fatia.
- **Limiar de merge**: score ≥ **0,85** ⇒ candidato a `same_as`; abaixo,
  nada é gravado. O limiar e os pesos vivem na config da bateria
  (`D-07.json`), nunca só em código.

## 4. Desenho

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros). Sócios plantados em pares de empresas:

- **E1** — mesma pessoa, nome idêntico, mesmo CPF parcial → merge.
- **E2** — mesma pessoa, nome com ruído (acento/caso/ordem de tokens),
  mesmo CPF parcial → merge.
- **E3** — **homônimos**: mesmo nome, CPF parcial diferente → sem merge.
- **E4** — mesmo CPF parcial, nomes disjuntos (coincidência de dígitos
  mascarados) → sem merge (nome 0,0 derruba o score abaixo do limiar).
- **E5** — mesma pessoa, mesmo nome, **sem** CPF parcial → sem merge
  (nome sozinho teto 0,6 < 0,85).
- **E6** — faixa etária divergente com mesmo nome e mesmo CPF parcial →
  score 0,9 ≥ 0,85 → merge (faixa etária é evidência fraca, não veto).
- **E7** — fornecedor de contrato com CNPJ 14d ↔ empresa Receita → link
  exato (score 1,0).
- **E8** — fornecedor sem CNPJ → sem link.
- **Controle** — 20 pessoas disjuntas → zero merges.

**Benchmark real**: `pairs-20251209.json.gz` (linhas `{judgement, left, right}`,
entidades FtM-embedadas). Amostra determinística: reservoir sampling
estratificado (1.000 positivos + 1.000 negativos, seed declarada na
config) sobre o stream do arquivo; a amostra é cacheada em
`results/detect/D-07/pairs_sample.jsonl` (não commitada; regenerável
pela bateria). Cada par vira entrada do matcher pelo núcleo nome +
documento (`properties.name` / `idNumber`/`registrationNumber` da
entidade OS). Métricas: precisão e revocação no limiar 0,85, contra o
`judgement` (linhas `unsure`, se houver, são excluídas).

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds.

- **P1 — conjunto exato de merges (sintético).** Exatamente E1, E2 e E6
  mergeam; E3, E4, E5 e os 20 controles não. *Refutada* com qualquer
  merge a mais ou a menos.
- **P2 — disciplina de homônimo.** E3 sem merge. *Refutada* se o nome
  idêntico com documento divergente mergear.
- **P3 — robustez a ruído de nome.** E2 mergea. *Refutada* se acento,
  caso ou ordem de tokens impedirem o merge com mesmo documento.
- **P4 — determinismo.** A mesma seed reproduz bit a bit os mesmos
  scores e merges; execuções distintas divergem em zero pares.
- **P5 — link supplier↔company exato.** E7 linka com score 1,0; E8 não
  linka. Precisão 1,0 do link no sintético. *Refutada* com qualquer
  desvio.
- **P6 — precisão no benchmark real.** Sobre a amostra OS Pairs,
  precisão ≥ **0,90** no limiar 0,85. *Refutada* abaixo disso (limiar
  miscalibrado → PR-D-07b com a curva precisão×limiar publicada).
- **P7 — revocação no benchmark real (banda de calibração).** Revocação
  entre **0,30 e 0,70** — o matcher é conservador (sem documento, teto
  0,6) e os pares OS são ruidosos/multilíngues. *Refutada* fora da banda,
  com a medida publicada no R-D-07 (revocação > 0,70 indicaria amostra
  enviesada ou bug de extração de features).
- **P8 — invariante estrutural (pós-integração).** Toda aresta `same_as`
  gravada no grafo tem `score` ≥ limiar vigente, e nenhum vértice é
  removido ou colapsado pela resolução. *Refutada* com qualquer violação.

## 6. Controles e invariantes

- Controles internos: E3/E4/E5 (ausência de falso positivo) e os 20
  pares disjuntos; no benchmark, os 1.000 negativos amostrados são o
  baseline de precisão.
- Invariante de composição: `same_as` existe se e somente se score(par)
  ≥ limiar — testável recomputando o score a partir das linhas silver
  gravadas na aresta (`source_rows` em `details`).
- Invariante de monotonicidade: alterar pesos, limiar ou incluir feature
  nova (ex.: data de nascimento OS ↔ faixa etária) exige PR de
  refinamento (`PR-D-07b`) com a justificativa medida.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds e P6–P8 satisfeitas
(P6/P7 sobre a amostra OS Pairs; P8 após a integração no grafo). Qualquer
refutação é publicada em `docs/results/R-D-07.md` com a causa investigada
e a forma corrigida vira `PR-D-07b.md` antes de nova execução. O sucesso
habilita — não autoriza automaticamente — a emissão de arestas `same_as`
na carga do grafo e a fatia fuzzy do screening (PR-D-06b).

## 8. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Matcher**: `src/capiba/detection/entities.py` puro
   (`normalize_name`, `score_person_pair`, `link_supplier_company`),
   testado unitariamente (rápido).
2. **Integração no grafo**: coleção de aresta `same_as`
   (persons↔persons) em `db/arangodb.py` + função de resolução
   (`resolve_entities(db, threshold)`) que grava arestas com `score` e
   `details` — best-effort, sem colapsar vértices.
3. **Bateria**: runner `battery_entities.py` (dispatch
   `"runner": "entities"` no `scripts/detect_battery.py`) lendo
   `experiments/detect/D-07.json`: regime sintético + amostragem
   determinística do OS Pairs (download streaming com cache),
   `results/detect/D-07/<seed>.jsonl` + métricas do benchmark no
   `summary.json`; teste de regime `@pytest.mark.slow`.
4. **Publicação**: `docs/results/R-D-07.md` com o veredito e as métricas
   (inclusive se refutada).

## Revisões

- 2026-08-20: criação (rascunho para revisão humana). Escopo aprovado na
  conversa: sócios + link supplier↔company; benchmark OS Pairs já nesta
  bateria (arquivo plano público, amostra determinística).
