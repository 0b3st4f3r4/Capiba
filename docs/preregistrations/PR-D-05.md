# PR-D-05 — Red flags de aditivos contratuais (O2)

- **Pré-registro**: bateria D-05
- **Criado em**: 2026-08-19
- **Última atualização**: 2026-08-21
- **Status**: executado — sintético 5/5; etapa real (2026-08-21,
  `docs/results/R-D-05.md` § 4): P6 satisfeita, P7 refutada e corrigida,
  **P8 refutada** (cobertura 23,54% < 50%) → forma corrigida em
  `docs/preregistrations/PR-D-05b.md`
- **Alvo**: mart gold `contract_amendments` (uma linha por contrato silver)
  com flags de aditivo computadas das **observações bronze** do contrato
  (payload de `/v1/contratos`, já ingerido em `raw_pncp`, mais a nova fonte
  `/v1/contratos/atualizacao` em `raw_pncp_contract_updates`), agregados por
  fornecedor e por órgão. Semântica de referência em
  `src/capiba/detection/amendments.py` (a implementar).
- **Configuração**: `experiments/detect/D-05.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

As flags de aditivo na semântica declarada na seção 3 são computadas
**exatamente** sobre sequências plantadas de observações de contrato
(inclusive fronteiras e disciplina de nulos), e o mart resultante satisfaz
os invariantes de integridade sobre o volume real acumulado?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético exato** (offline, como D-01/D-04) para a semântica,
  mais regime de **saneamento em dados reais** (invariantes estruturais
  sobre o bronze/silver) — sem predições de distribuição sobre dados
  reais, que seriam não-falsificáveis a priori.
- **O payload é um snapshot do estado atual, não um histórico.** O
  endpoint `/v1/contratos` devolve o contrato como está hoje
  (`valorAcumulado`, `numeroRetificacao`, vigência correntes). O bronze
  do backfill 2026-01-01 → 2026-08-18, crawlado em 2026-08, carrega o
  estado acumulado **até a data do crawl** — a flag de valor já é
  computável retroativamente, mas o diff temporal entre observações (flag
  de prazo) só ganha sentido com a fonte de atualização ativa ao longo do
  tempo. A bateria mede a fidelidade da semântica; a cobertura temporal
  real é limitação declarada, medida como estatística descritiva no
  R-D-05.
- **`valorAcumulado` como proxy de aditivo de valor.** A semântica
  adotada é a documentada pelo PNCP (valor acumulado após alterações
  contratuais). Se o campo agregar outra coisa (ex.: empenhos), a
  predição real P8 mede a cobertura e a refutação é informativa — a
  alternativa cara (endpoint de termos por contrato, um request por
  contrato) fica como plano B documentado.
- **Aditivo de prazo só por diff de observações** — o snapshot não traz a
  vigência original. `numeroRetificacao` é registrado como descritor, mas
  **não** é flag (retificação ≠ aditivo; semântica insuficientemente
  estável para acusar).
- **As flags de aditivo NÃO entram no CRI do PR-D-04** (composição
  registrada como as 3 flags originais); uma eventual integração exige
  PR-D-04b de calibração.
- Atas de registro de preço (`/v1/atas`) e instrumentos de cobrança
  ficam fora deste registro.

## 3. Semântica das flags de aditivo (declarada)

Entrada: a sequência de observações bronze de um contrato
(`numeroControlePNCP`), ordenada pela data de ingestão (`dt` da linha
bronze), cada observação com `valorInicial`, `valorAcumulado`,
`dataVigenciaInicio`, `dataVigenciaFim`. Flags binárias por contrato
(1 = suspeito; NULL = dado insuficiente):

- **`f_value_amendment`** — aditivo de valor: a **última** observação com
  `valorAcumulado` presente tem `valorAcumulado > valorInicial`, com
  `valorInicial` > 0 tomado da **primeira** observação que o informa.
  Igualdade computa 0. NULL quando `valorInicial` ou `valorAcumulado`
  faltam em todas as observações ou não são positivos/parseáveis.
- **`f_term_extension`** — aditivo de prazo: a **última** observação com
  `dataVigenciaFim` presente tem vigência fim **estritamente posterior**
  à da **primeira** observação com `dataVigenciaFim` presente. Igualdade
  (ou observação única) computa 0. NULL quando nenhuma observação informa
  `dataVigenciaFim` parseável.

Descritores não-flag (reportados no mart, sem papel de flag):
`numeroRetificacao` máximo observado, número de observações, razão
`valorAcumulado / valorInicial` da última observação (4 casas; NULL sob
as mesmas condições da flag de valor) — insumos de calibração futura.

## 4. Desenho

População sintética por seed (estrutura idêntica nas 5 seeds; a seed só
randomiza campos neutros — ids, datas base dentro das janelas plantadas,
valores mantendo as razões plantadas). Casos plantados:

- **A1** — snapshot único, `valorAcumulado == valorInicial` → (0, 0).
- **A2** — snapshot único, `valorAcumulado = 1,2 × valorInicial` →
  `f_value_amendment` = 1, `f_term_extension` = 0.
- **A3** — duas observações, vigência fim estendida em 180 dias, valor
  inalterado → (0, 1).
- **A4** — fronteira de valor: `valorAcumulado == valorInicial` após duas
  observações → 0 (igualdade não dispara).
- **A5** — duas observações, valor e prazo aumentados → (1, 1).
- **A6** — `valorAcumulado` ausente em todas as observações →
  `f_value_amendment` NULL (demais flags conforme os campos presentes).
- **A7** — `valorInicial` zero ou ausente → `f_value_amendment` NULL.
- **A8** — campos malformados (`"n/a"`, `"abc"`) → flags NULL, sem erro
  de execução.
- **A9** — ordem importa: duas observações em que a **primeira** tem
  valor alto e a **última** tem `valorAcumulado == valorInicial` →
  `f_value_amendment` = 0 (a última observação é soberana).

## 5. Predições (numéricas, falsificáveis)

Âncoras exatas — o cômputo é determinístico sobre estrutura plantada;
qualquer desvio refuta. Veredito por seed; a predição falha se divergir
em qualquer uma das 5 seeds.

- **P1 — vetor de flags exato.** Cada caso A1–A9 produz exatamente o
  vetor declarado na seção 4. *Refutada* com qualquer divergência.
- **P2 — fronteira de valor.** A4 (razão 1,0) → 0; A2 (1,2) → 1.
  *Refutada* com qualquer inversão.
- **P3 — fronteira de prazo.** A3 (extensão de 180 dias) → 1; A1
  (vigência inalterada) → 0. *Refutada* com qualquer inversão.
- **P4 — disciplina de nulos.** A6/A7 → `f_value_amendment` NULL; A8 →
  flags NULL sem exceção. *Refutada* se algum nulo computar 0/1 ou se
  houver erro de parsing.
- **P5 — determinismo e ordem.** A mesma seed reproduz bit a bit as
  mesmas flags; A9 prova que a última observação é soberana (0 apesar da
  primeira alta). *Refutada* com qualquer divergência.
- **P6 — cardinalidade (dados reais).** Cada contrato silver aparece
  **exatamente uma vez** no mart `contract_amendments`; a contagem iguala
  a da silver `contracts`. *Refutada* com duplicata ou ausência.
- **P7 — domínio (dados reais).** Toda flag ∈ {0, 1, NULL} e toda razão
  > 0 quando presente; zero linhas fora do domínio. *Refutada* com
  qualquer violação.
- **P8 — viabilidade do campo (dados reais).** A parcela de contratos
  silver com payload bronze PNCP cuja observação informa `valorAcumulado`
  e `valorInicial` positivos é **≥ 50%** (a chamada ao vivo de
  2026-08-19 mostrou os campos presentes no payload de `/v1/contratos`).
  *Refutada* abaixo disso — indicando campo esparsamente preenchido, e o
  desenho muda (plano B: endpoint de termos por contrato).

## 6. Controles e invariantes

- Controles internos: A1 (tudo 0) e A8 (parsing) são os baselines de
  ausência de falso positivo e de robustez; não há baseline externo — a
  semântica da seção 3 é a referência.
- Invariante de soberania temporal: recomputar as flags com as
  observações embaralhadas e reordenadas pela data de ingestão produz o
  mesmo resultado (a ordenação é por `dt`, não pela ordem de leitura).
- Invariante de monotonicidade documental: toda mudança de semântica
  (novos limiares, `numeroRetificacao` virando flag, inclusão no CRI)
  exige PR de refinamento (`PR-D-05b`/`PR-D-04b`).

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds e P6–P8 satisfeitas
sobre o volume real. Qualquer refutação é publicada em
`docs/results/R-D-05.md` com a causa investigada, e a forma corrigida
vira `PR-D-05b.md` antes de nova execução. O sucesso habilita — não
autoriza automaticamente — a agregação por órgão/fornecedor no gold e a
exposição via O11.

## 8. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Fonte nova**: crawler `fetch_contract_updates` (`/v1/contratos/
   atualizacao`, mesma paginação/retry de `crawler_pncp.py`), fonte
   `pncp_contract_updates` no `SOURCE_REGISTRY`, spec
   `dags/pipelines/daily_pncp_updates.yaml` com destino bronze-only —
   o silver não é tocado (as flags leem o bronze).
2. **Semântica**: `src/capiba/detection/amendments.py` puro e
   determinístico, testado unitariamente (rápido).
3. **Bateria**: runner `battery_amendments.py` (dispatch
   `"runner": "amendments"` no `scripts/detect_battery.py`) lendo
   `experiments/detect/D-05.json`, gravando `results/detect/D-05/
   <seed>.jsonl`; teste de regime `@pytest.mark.slow`.
4. **dbt**: mart `contract_amendments` (unnest das observações bronze de
   `raw_pncp` + `raw_pncp_contract_updates` por `numeroControlePNCP`,
   join silver) + agregados por fornecedor/órgão + data tests P6–P8.
   A etapa real executa quando o backfill estiver concluído.

## Revisões

- 2026-08-19: criação (rascunho para revisão humana), após chamada ao
  vivo ao endpoint `/v1/contratos` confirmando os campos `valorInicial`,
  `valorAcumulado` e `numeroRetificacao` no payload.
