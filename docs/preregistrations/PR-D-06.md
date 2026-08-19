# PR-D-06 — Screening de sanções por documento exato (O3, primeira fatia)

- **Pré-registro**: bateria D-06
- **Criado em**: 2026-08-19
- **Última atualização**: 2026-08-19
- **Status**: rascunho para revisão humana
- **Alvo**: sinal `sanctioned_supplier` (novo `SignalType`) — match exato
  por CNPJ/CPF entre os fornecedores dos contratos silver e as sanções
  silver (`sanctions`, listas CEIS/CNEP) **vigentes na data de assinatura
  do contrato**. Semântica de referência em
  `src/capiba/detection/screening.py` (a implementar), integrada ao
  `task_detect` sobre o acumulado do silver.
- **Configuração**: `experiments/detect/D-06.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

O screening por documento exato, na semântica declarada na seção 3,
sinaliza **exatamente** os fornecedores plantados (inclusive fronteiras
de vigência e a disciplina "nome não é documento"), e o sinal resultante
satisfaz os invariantes estruturais sobre o volume real acumulado?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético exato** (offline, como D-01/D-04/D-05) para a
  semântica, mais **saneamento em dados reais** (invariantes estruturais
  sobre o silver) — sem predição da contagem real de sinais, que seria
  não-falsificável a priori.
- **Match exato por documento é factual, não probabilístico** — o score é
  binário 1,0 (como `collusion_network`). A bateria mede fidelidade à
  semântica; não mede a *relevância editorial* de uma sanção (uma
  inidoneidade vencida ou de outro órgão jurídico pode não implicar
  fraude — a triagem humana do O10 é o filtro).
- **CEAF está FORA desta fatia**: chamada ao vivo ao endpoint (2026-08-19)
  confirmou que o CPF vem mascarado (`***.435.151-**`) — match exato por
  documento é impossível; o CEAF entra quando o match fuzzy por nome
  existir (PR posterior, com limiar pré-registrado).
- **PEPs e OpenSanctions (`yente` self-hosted) estão FORA** — fonte e
  infra novas, PR próprio. O3 permanece "em andamento" após esta fatia.
- **Fuzzy por nome está FORA** — exige calibração de limiar com pares
  plantados (bateria própria). Nesta fatia, nome idêntico com documento
  diferente **não** sinaliza (declarado e testado).
- **Vigência é a da data de assinatura do contrato** — sanção posterior à
  assinatura não é evidência sobre aquele contrato (pode sinalizar o
  fornecedor em agregados futuros; fora desta fatia).
- Sanções sem `start_date` são **não computáveis** (não sinalizam); sanção
  sem `end_date` é vigência aberta (sinaliza a partir do início).

## 3. Semântica do sinal (declarada)

- **Entidade**: `supplier` (CNPJ ou CPF do fornecedor do contrato silver).
- **Match**: `supplier.cnpj == sanction.cnpj` ou `supplier.cpf ==
  sanction.cpf` (documentos completos, dígitos apenas, como já
  normalizados no silver). Fornecedor sem documento no silver nunca
  sinaliza nesta fatia.
- **Vigência na assinatura**: `sanction.start_date <=
  contract.signature_date <= sanction.end_date`, com `end_date` NULL
  tratado como aberto. Fronteiras inclusivas: assinatura igual ao início
  ou ao fim sinaliza; um dia fora, não.
- **Emissão**: um sinal `sanctioned_supplier` por fornecedor com ao menos
  um contrato sob sanção vigente, score **1,0**, `details` com as listas
  e os ids das sanções que casaram (e a contagem de contratos afetados).
- Sinais por fornecedor, não por contrato (espelha `concentration`); a
  lista de contratos afetados vai em `details`.

## 4. Desenho

População sintética por seed (estrutura idêntica nas 5 seeds; a seed só
randomiza campos neutros — documentos válidos, nomes, valores, datas base
dentro das janelas plantadas). Casos plantados (fornecedor × sanção):

- **S1** — CNPJ sancionado, assinatura dentro da vigência → sinal.
- **S2** — assinatura 1 dia **após** `end_date` → sem sinal.
- **S3** — assinatura **igual** a `end_date` → sinal (fronteira
  inclusiva).
- **S4** — assinatura 1 dia **antes** de `start_date` → sem sinal.
- **S5** — sanção sem `end_date`, assinatura após o início → sinal.
- **S6** — match por **CPF** (pessoa física) → sinal.
- **S7** — mesmo **nome**, documento **diferente** → sem sinal (nome não
  é documento).
- **S8** — fornecedor **sem documento** no silver → sem sinal.
- **S9** — duas sanções, apenas uma vigente na assinatura → sinal, e
  `details` lista **somente** a vigente.
- **S10** — sanção sem `start_date` → sem sinal (não computável).
- **Controle** — 20 fornecedores sem sanção alguma → zero sinais.

## 5. Predições (numéricas, falsificáveis)

Âncoras exatas — o cômputo é determinístico sobre estrutura plantada;
qualquer desvio refuta. Veredito por seed; a predição falha se divergir
em qualquer uma das 5 seeds.

- **P1 — conjunto exato de sinais.** Exatamente os fornecedores de S1,
  S3, S5, S6 e S9 sinalizam (5 sinais por seed), cada um com score 1,0;
  S2, S4, S7, S8, S10 e os 20 controles não sinalizam. *Refutada* com
  qualquer sinal a mais ou a menos.
- **P2 — fronteiras de vigência.** S2 sem sinal, S3 com sinal, S4 sem
  sinal, S5 com sinal. *Refutada* com qualquer inversão.
- **P3 — disciplina de documento.** S7 e S8 sem sinal. *Refutada* se o
  nome ou a ausência de documento produzirem sinal.
- **P4 — details fiéis.** O sinal de S9 lista exatamente a sanção
  vigente (1 id), não a vencida. *Refutada* com qualquer divergência.
- **P5 — determinismo.** A mesma seed reproduz bit a bit os mesmos
  sinais; execuções distintas divergem em zero casos.
- **P6 — invariante estrutural (dados reais).** Todo sinal
  `sanctioned_supplier` no gold `fraud_signals` tem score 1,0 e
  `entity_id` presente como documento na silver `sanctions`. *Refutada*
  com qualquer violação (o sinal só pode nascer do join — uma violação
  indica bug de emissão, não dado ruim).
- **P7 — cobertura de documento (dados reais).** A parcela de contratos
  silver cujo fornecedor tem CNPJ **ou** CPF presente é **≥ 90%** (o
  normalizer já registra "not informed" quando falta — se a cobertura
  real for baixa, a fatia fuzzy ganha prioridade). *Refutada* abaixo
  disso, com a cobertura medida publicada no R-D-06.

## 6. Controles e invariantes

- Controles internos: os 20 fornecedores sem sanção (ausência de falso
  positivo) e S7/S8 (disciplina de documento) são os baselines; não há
  baseline externo — a semântica da seção 3 é a referência.
- Invariante de composição: o sinal de um fornecedor existe se e somente
  se ao menos um contrato seu casa (documento ∧ vigência) com alguma
  sanção — testável recomputando do bronze/silver, linha a linha.
- Invariante de monotonicidade documental: incluir CEAF, PEPs, fuzzy por
  nome ou vigência "na data de hoje" (em vez da assinatura) exige PR de
  refinamento (`PR-D-06b`) com a justificativa medida.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds e P6–P7 satisfeitas
sobre o volume real (após o backfill e uma run do `detect` com a feature).
Qualquer refutação é publicada em `docs/results/R-D-06.md` com a causa
investigada, e a forma corrigida vira `PR-D-06b.md` antes de nova
execução. O sucesso habilita — não autoriza automaticamente — a triagem
editorial dos sinais (O10) e a priorização da fatia fuzzy conforme P7.

## 8. Execução

Após a aprovação humana deste registro, com TDD/BDD usual:

1. **Semântica**: `src/capiba/detection/screening.py` puro e
   determinístico (`sanctioned_supplier_signals(contracts, sanctions)`),
   testado unitariamente (rápido); novo `SignalType.SANCTIONED_SUPPLIER`.
2. **Integração**: `task_detect` passa a ler a silver `sanctions`
   (`read_silver_entities`) e emite o sinal junto aos demais (best-effort,
   sem derrubar a task quando a tabela não existe).
3. **Bateria**: runner `battery_screening.py` (dispatch
   `"runner": "screening"` no `scripts/detect_battery.py`) lendo
   `experiments/detect/D-06.json`, gravando `results/detect/D-06/
   <seed>.jsonl`; teste de regime `@pytest.mark.slow`.
4. **Real**: após o backfill, uma run regular do `gold_detection`
   (detect sobre o acumulado) + verificação de P6/P7 por query sobre o
   gold/silver.

## Revisões

- 2026-08-19: criação (rascunho para revisão humana), após chamada ao
  vivo ao endpoint `/ceaf` confirmando o CPF mascarado (CEAF fora desta
  fatia).
