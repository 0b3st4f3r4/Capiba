# PR-D-12 — Piloto de screening de PEPs via yente/OpenSanctions (O3, terceira fatia)

- **Pré-registro**: bateria D-12
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: executada em 2026-08-21 — **refutada** (P3 ∧ P4 falharam no
  benchmark; refutação limpa declarada em § 7). Resultados em
  `docs/results/R-D-12.md`; piloto arquivado sem sinal novo.
- **Alvo**: sinal `pep_supplier_match` (novo `SignalType`, hipótese
  computada — **nunca** factual) — match entre **fornecedores pessoa
  física** dos contratos silver e a coleção **PEPs** da OpenSanctions
  (dataset brasileiro prioritário `br_pep`, CGU), via API de matching
  **yente** (query-by-example FtM, endpoint `/match/<dataset>`,
  algoritmo `logic-v2`, threshold declarado). O yente fala o mesmo
  modelo FtM do grafo do Capiba (`src/capiba/db/ftm.py`), o que torna a
  integração barata: o payload de consulta é o mesmo formato que já
  exportamos. O gap de PEPs estava declarado fora de escopo em PR-D-06
  § 2 e PR-D-06b § 2; esta fatia o endereça como **piloto delimitado**.
- **Configuração**: `experiments/detect/D-12.json` (declarativa, seeds
  inclusas; criada junto com este registro, antes de qualquer execução)

## 1. Pergunta

O screening de PEPs via yente, na configuração declarada na seção 3,
(i) reproduz **exatamente** as consultas FtM esperadas no regime
sintético, (ii) **supera o matcher local** de D-06b no regime
documentless do benchmark OpenSanctions Pairs (precisão ≥ 0,85 **com**
revocação ≥ 0,55, contra 0,925/0,296 medidos em R-D-06b), e (iii)
sustenta precisão editorial ≥ 0,80 com carga de triagem comportável
(≤ 10% dos fornecedores PF alertados) sobre os fornecedores reais do
piloto?

## 2. Regime medido e limitações (obrigatório)

- Três regimes: **sintético exato** (offline; semântica do adapter
  FtM — Capiba → yente —, com respostas do yente em fixture), **benchmark
  real** OpenSanctions Pairs (mesmo arquivo plano de D-06b/D-07,
  `pairs-20251209.json.gz`; amostra reservoir estratificada
  1.000+1.000 com seed **61**, distinta das de D-06b/D-07/D-07b —
  19/23/37/41/53) e **amostra real anotada** (fornecedores PF da silver
  `contracts` × `br_pep`, anotação editorial cega — verdade de terreno
  parcial: mede concordância com o anotador, não identidade comprovada,
  mesma limitação declarada de PR-D-10 § 2).
- **O benchmark é adjacente ao fornecedor.** O OS Pairs é publicado pela
  própria OpenSanctions como dado de treinamento; o algoritmo escolhido
  (`logic-v2`, rule-based) não é treinado nesses pares — ao contrário do
  `regression-v1`, explicitamente evitado por isso —, mas a proximidade
  permanece: as métricas de P3/P4 são uma **banda superior** da
  performance esperada. O regime decisivo do piloto é P6 (amostra real
  brasileira anotada).
- **Regime documentless dominante.** O `br_pep` (CGU) é essencialmente
  nome + cargo, sem documento na maioria dos registros (hipótese medida
  por P1). Sem documento, não há veto documental nem regime doc-assistido:
  o screening é **nome-only puro**, o regime mais caro em falso positivo
  editorial (homônimos — modo de falha já medido em R-D-06b). O próprio
  guia do fornecedor alerta que bases de PEP têm muitos nomes comuns e
  matches frequentes (fonte 2, § Fontes).
- **PEP ≠ ilícito.** Ser pessoa politicamente exposta é condição
  estatutária, não infração; o sinal indica apenas "fornecedor é
  homônimo candidato a PEP" e **sempre** entra como `pending_review`.
  Falso positivo editorial é caro (expor um cidadão comum como "PEP" é
  dano reputacional sem contrapartida): por isso a métrica primária do
  regime real é **precisão**, não revocação, e o sinal nunca é
  publicado sem triagem humana (O10).
- **Sem vigência computável no piloto.** O `br_pep` cobre agentes no
  cargo ou que o ocuparam nos últimos 5 anos (fonte 5); não derivamos
  janela de exercício por contrato — o filtro temporal é decisão
  editorial na triagem, declarado.
- **Fora de escopo**: screening dos vértices `persons` do grafo (sócios
  × PEPs) — PR próprio, habilitado por este; match de fornecedores PJ
  contra PEPs (PJ não é PEP; o vínculo PJ↔PEP passa pelo grafo, fora);
  enriquecimento do grafo com posições FtM do `br_pep` (`Position`/
  `Occupancy`); uso da API hospedada paga em produção (decisão de deploy,
  § 8).
- **LGPD**: o `br_pep` é dado público oficial (CGU/Portal da
  Transparência, CC BY-NC 4.0 no bulk — fonte 6); o sinal carrega nome e
  cargo públicos e o CPF do fornecedor **nunca** é enviado a serviço
  externo no piloto (backend self-hosted; ver decisão em aberto, § 8).

## 3. Semântica do piloto (declarada)

- **Adapter Capiba → yente** (`detection/pep_screening.py`, a
  implementar): para cada fornecedor PF distinto da silver `contracts`
  (entity_id = CPF; nome = `legal_name`), constrói a consulta FtM
  `{"schema": "Person", "properties": {"name": [legal_name],
  "idNumber": [cpf], "nationality": ["br"]}}` — o mesmo formato do
  exportador `db/ftm.py`. Fornecedores PJ e sem nome não consultam.
- **Matching**: `POST /match/<dataset>` com `algorithm=logic-v2`
  (recomendado pelo fornecedor; `logic-v1` está "superseeded" —
  fonte 3), `threshold=0.7` (default documentado — fonte 4), dataset
  `br_pep` (piloto brasileiro; a coleção `peps` inteira é variável de
  refinamento, não do piloto).
- **Emissão**: um sinal `pep_supplier_match` por fornecedor PF ×
  dataset com ao menos um candidato retornado; `score` = maior score
  entre os candidatos; `details` com ids OpenSanctions dos candidatos,
  nomes/cargos retornados e scores individuais. Prioridade editorial,
  não factual: não existe caminho exato contra `br_pep` (sem documento),
  então não há regra de prioridade como a de D-06b.
- **Backend do piloto**: yente **self-hosted** (open source, MIT —
  fontes 7/8) alimentado pelo bulk `entities.ftm.json` do `br_pep`
  (CC BY-NC 4.0, uso não-comercial — fonte 6), snapshot **pinado na
  config antes de qualquer score computado** (emenda datada restrita ao
  pin). A API hospedada (pay-as-you-go, 30 dias de trial — fonte 1) é a
  alternativa registrada; a escolha final é decisão humana na aprovação
  (§ 8), não durante a bateria.
- Versão do yente pinada na config: **`5.5.0`** (última estável no PyPI,
  conferida em 2026-08-21 — emenda datada em Revisões); threshold,
  algoritmo e dataset vivem na config (`D-12.json`), nunca só em código.

## 4. Desenho

**Regime sintético** (por seed; estrutura idêntica nas 5 seeds, que só
randomizam campos neutros) — mede o adapter, com yente **stubado** por
fixture de resposta (o piloto não testa o yente no sintético; testa que
o Capiba fala FtM corretamente):

- **Q1** — fornecedor PF com CPF e nome → consulta `Person` com
  `name`, `idNumber` e `nationality=br`, exatamente.
- **Q2** — fornecedor PF sem CPF → consulta `Person` nome-only (sem
  `idNumber`).
- **Q3** — fornecedor PJ → **nenhuma** consulta (PJ não é PEP).
- **Q4** — fornecedor sem nome → nenhuma consulta.
- **Q5** — resposta stub com 2 candidatos acima do threshold → 1 sinal,
  `score` = maior score, `details` com ambos os ids.
- **Q6** — resposta stub sem candidatos → nenhum sinal.
- **Q7** — deduplicação: o mesmo fornecedor em N contratos gera 1
  consulta e no máximo 1 sinal.
- **Controle** — 20 fornecedores PF cujo stub nunca retorna candidatos
  → zero sinais.

**Benchmark real**: mesma amostragem de D-06b (reservoir estratificado,
1.000 positivos + 1.000 negativos, seed 61, cacheada em
`results/detect/D-12/pairs_sample.jsonl`, não commitada). Cada par é
pontuado **in-process** pelo comparador do yente (`logic-v2`, versão
pinada — biblioteca open source, sem serviço) sobre as duas entidades
FtM do par, e comparado ao `judgement`; o mesmo cômputo roda para o
matcher local de D-06b (regime nome-only, limiar 0,95) como **controle
interno pareado** — mesma amostra, mesmos pares.

**Amostra real anotada**: screening self-hosted (yente + bulk `br_pep`
pinado) de todos os fornecedores PF distintos da silver `contracts` da
partição vigente na execução. Protocolo de anotação: todos os
fornecedores alertados se ≤ 60, senão amostra estratificada por decil
de score com seed 61 (mínimo 60); anotador editorial cego ao score
compara nome, cargo, município/UF e vínculo público do candidato contra
o fornecedor; rótulo binário (mesma pessoa / pessoa distinta /
indeterminado — indeterminado conta como **negativo** na precisão,
conservador). Carga de triagem = fração de fornecedores PF com ao menos
um candidato.

## 5. Predições (numéricas, falsificáveis)

Veredito por seed no sintético; a predição falha se divergir em qualquer
uma das 5 seeds.

- **P1 — perfil documental do `br_pep`.** Pelo menos **90%** das
  entidades `Person` do snapshot pinado **não** têm identificador
  documental (`idNumber`/`registrationNumber`). *Refutada* abaixo de
  0,90 → o regime doc-assistido ganha relevância e o desenho é revisto
  via `PR-D-12b` antes de seguir.
- **P2 — adapter exato (sintético).** Q1–Q7 e os 20 controles se
  comportam exatamente como declarado na seção 4, nas 5 seeds.
  *Refutada* com qualquer consulta ou sinal a mais ou a menos.
- **P3 — precisão yente no OS Pairs.** Precisão ≥ **0,85** (`logic-v2`,
  threshold 0,7, amostra seed 61). *Refutada* abaixo disso → PR-D-12b
  com a curva precisão×threshold publicada.
- **P4 — revocação yente no OS Pairs (superioridade documentless).**
  Revocação ≥ **0,55** — pelo menos ~2× a revocação 0,296 do matcher
  local medida em R-D-06b. *Refutada* abaixo disso. **P3 ∧ P4 é o
  critério "o yente supera o matcher local no regime documentless"**;
  sua falha conjunta encerra o piloto com veredito negativo (o matcher
  local segue como caminho único de screening nome-only).
- **P5 — determinismo.** Mesma versão yente + mesmo snapshot + mesma
  consulta → scores bit a bit idênticos em repetição; zero divergências.
- **P6 — regime real brasileiro.** Na amostra anotada: precisão
  editorial ≥ **0,80**; e carga de triagem ≤ **10%** dos fornecedores
  PF com ao menos um candidato. *Refutada* se qualquer das duas falhar
  — precisão baixa = custo editorial de falso positivo proibitivo;
  carga alta = triagem inviável (ambas documentadas no R-D-12).
- **P7 — invariante estrutural (pós-integração, dados reais).** Todo
  sinal `pep_supplier_match` no gold tem score ≥ threshold da config,
  dataset `br_pep`, e status de triagem inicial `pending_review` —
  nenhum sinal PEP nasce `published`. Verificado por query após uma run
  do `detect` com a feature; resultado anexado ao R-D-12 (satisfeito
  por vacuidade se zero sinais, com o zero corroborado como em R-D-06b
  § 4).

## 6. Controles e invariantes

- Controles internos: Q3/Q4/Q6 e os 20 fornecedores controle
  (sintético); os 1.000 negativos do OS Pairs (benchmark); o rótulo
  "indeterminado = negativo" e a cegueira do anotador ao score (regime
  real).
- Controle pareado: o matcher local de D-06b é re-executado sobre a
  mesma amostra seed 61 — P4 compara revocações **na mesma base**; a
  precisão local nessa amostra é republicada no R-D-12 como referência
  (esperado: compatível com 0,925 de R-D-06b, sem predição atada).
- Invariante de composição: o sinal existe se e somente se o yente
  retornou candidato ≥ threshold para a consulta FtM do fornecedor —
  testável re-executando a consulta arquivada em `details`.
- Invariante de monotonicidade: alterar algoritmo, threshold, dataset
  (ex.: coleção `peps` inteira) ou incluir feature nova (ex.: filtro por
  UF do cargo) exige `PR-D-12b` com a justificativa medida.
- Nenhum código do screening de sanções (exato ou fuzzy) muda nesta
  fatia — guarda: baterias D-06/D-06b seguem verdes.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P2 e P5 exatas, P3–P4 satisfeitas no
benchmark e P6 satisfeita na amostra real (P7 após a integração).
Qualquer refutação é publicada em `docs/results/R-D-12.md` com a causa
investigada e a forma corrigida vira `PR-D-12b.md` antes de nova
execução. O sucesso habilita — não autoriza automaticamente — o
screening de sócios do grafo contra PEPs e a discussão de deploy
(self-host permanente × API hospedada). **Refutação limpa declarada**:
se P3 ∧ P4 falharem, a conclusão publicada é "o matching yente não
supera o matcher local no regime documentless brasileiro-relevante" e o
piloto é arquivado sem sinal novo.

## 8. Execução

Após a aprovação humana deste registro — incluindo a **decisão em
aberto do backend** (recomendado: yente self-hosted + bulk CC BY-NC;
alternativa: trial da API hospedada, vedada ao envio de CPF):

1. **Perfil e pin**: download do bulk `br_pep`; P1 medido; snapshot e
   versão do yente pinados por emenda datada à config **antes** de
   qualquer score.
2. **Adapter + sinal**: `detection/pep_screening.py` puro (construção
   de consulta e redução de resposta), novo `SignalType.PEP_SUPPLIER_MATCH`;
   testes unitários com cliente yente stubado (rápidos, sem serviço).
3. **Bateria**: runner `battery_pep_screening.py` (dispatch
   `"runner": "pep_screening"`) lendo `experiments/detect/D-12.json`:
   sintético + OS Pairs in-process (`logic-v2` pinado) + controle
   pareado do matcher local; teste de regime `@pytest.mark.slow`.
4. **Amostra real**: yente self-hosted efêmero (dataset pinado) sobre os
   fornecedores PF da silver; protocolo de anotação da seção 4.
5. **Publicação**: `docs/results/R-D-12.md` com o veredito (inclusive se
   refutada); integração no `task_detect` (best-effort, `pending_review`)
   somente após sucesso, com P7 anexada após a run real.

## Fontes consultadas (2026-08-21)

1. [Getting started with the API — OpenSanctions](https://www.opensanctions.org/docs/api/)
   (endpoint `/match` como núcleo do screening; API hospedada
   pay-as-you-go com trial de 30 dias).
2. [The matching API — OpenSanctions](https://www.opensanctions.org/docs/api/matching/)
   (query-by-example FtM; matching por tipo de dado, não por campo;
   alerta explícito: bases de PEP têm muitos nomes comuns e matches
   frequentes — calibrar threshold e revisão humana).
3. [Matching algorithms — OpenSanctions](https://www.opensanctions.org/matcher/)
   (`logic-v2` recomendado, rule-based; `logic-v1` "superseeded";
   `regression-v1` treinado — evitado aqui pela adjacência com o
   benchmark; pesos de features públicos).
4. [OpenSanctions Test API (yente 5.5.0)](https://api-test.opensanctions.org/)
   (parâmetro `threshold`, default 0.7; versão corrente do yente).
5. [Brazil Politically Exposed Persons — OpenSanctions](https://www.opensanctions.org/datasets/br_pep/)
   (CGU; 106.465 `Person` + 13.506 `Position`; cobre cargos atuais e
   últimos 5 anos; frequência semanal; último processamento
   2026-08-18, v. `20260818021948-guq`; bulk `entities.ftm.json`
   ~106 MB). Veja também a coleção
   [Politically Exposed Persons](https://www.opensanctions.org/datasets/peps/)
   (que inclui ainda Câmara dos Deputados, 2.044, e Senado Federal, 82).
6. [Free and non-commercial use — OpenSanctions](https://www.opensanctions.org/faq/commercial/exemptions/)
   (bulk sob **CC BY-NC 4.0**, sem sign-up; chaves de API gratuitas como
   concessão para trabalho de interesse público).
7. [yente — the matchmaker (documentação self-hosted)](https://yente.followthemoney.tech/)
   (serviço open source de screening FtM que alimenta a API hospedada;
   roda on-premises sem que dados saiam do deployment).
8. [yente — PyPI](https://pypi.org/project/yente/) e
   [opensanctions/yente — GitHub](https://github.com/opensanctions/yente)
   (código aberto; docs migradas para yente.followthemoney.tech na
   v5.0.0).

## Revisões

- 2026-08-21 (execução): bateria executada no setup self-hosted pinado
  (venv isolado `.venv-yente`, OpenSearch 2.19.1 efêmero). P1 (100,00%
  sem documento), P2 (exata nas 5 seeds) e P5 (zero divergências, serviço
  e sintético) satisfeitas; **P3 (precisão 0,8272 < 0,85) e P4 (revocação
  0,517 < 0,55) refutadas** — controle pareado do matcher local na mesma
  amostra seed 61: 0,9297/0,291. Pela refutação limpa de § 7, o piloto é
  arquivado sem sinal novo; P6 não executada por desenho; P7 não se
  aplica. Publicação: `docs/results/R-D-12.md`.
- 2026-08-21 (pins, pré-score): aprovação humana registrada, com a
  decisão de backend **yente self-hosted + bulk CC BY-NC**. Pins fixados
  antes de qualquer score: **yente `5.5.0`** (última versão estável no
  PyPI, conferida em `https://pypi.org/pypi/yente/json` em 2026-08-21 —
  coincide com o valor do rascunho) e **snapshot do `br_pep`
  `20260818021948-guq`** (processado em 2026-08-18; URL versionada
  `https://data.opensanctions.org/artifacts/br_pep/20260818021948-guq/entities.ftm.json`,
  sha1 `4e5ae21c2ea35f5cb5952848ff9ed40df3d1237e`, 111.193.934 bytes,
  `target_count` 106.465 — conferidos no `index.json` do artifact em
  2026-08-21; coincide com a referência observada no rascunho).
  `experiments/detect/D-12.json` atualizado na mesma emenda
  (`yente.version_pin`, `yente.dataset_snapshot`, checksum e tamanho).
- 2026-08-21: criação (rascunho para revisão humana). Origem: estudo de
  gaps que declarou PEPs fora de escopo (correto para o foco
  brasileiro), com a observação de que o yente fala o mesmo modelo FtM
  do grafo (`src/capiba/db/ftm.py`) — integração barata, alto valor
  editorial, exigindo pré-registro. Decisão em aberto registrada na
  seção 8: backend self-hosted × API hospedada. Numeração D-12 (D-11
  reservado fora deste registro).
