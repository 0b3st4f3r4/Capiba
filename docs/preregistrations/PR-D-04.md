# PR-D-04 — Red flags de contrato e CRI determinístico (Fazekas & Kocsis, O1)

- **Pré-registro**: bateria D-04
- **Criado em**: 2026-08-19
- **Última atualização**: 2026-08-19
- **Status**: registrado, aprovado em 2026-08-19, não executado
- **Alvo**: mart gold de red flags por contrato e CRI composto
  **determinístico** (média das flags não nulas), materializado via dbt a
  partir do `payload_json` do bronze (`raw_pncp`) e dos contratos silver.
  O `compute_cri` supervisionado (`src/capiba/detection/ml_models.py`,
  RandomForest) está **fora do escopo** — sem job de treino nem rótulos
  suficientes (a fila do O10 acabou de abrir); seu uso é objeto de um PR
  futuro, após o gap de ML supervisionado.
- **Configuração**: `experiments/detect/D-04.json` (declarativa, seeds
  inclusas; a criar junto com a implementação, após aprovação)

## 1. Pergunta

As red flags de contrato do CRI de Fazekas & Kocsis, na semântica
declarada na seção 3, são computadas **exatamente** sobre casos plantados
(inclusive fronteiras de limiar), e o mart gold resultante satisfaz os
invariantes de integridade sobre o volume real acumulado pelo backfill?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético exato** (offline, como D-01) para a semântica das
  flags e do composto, mais regime de **saneamento em dados reais**
  (invariantes estruturais sobre o bronze/silver do backfill
  2026-01-01 → 2026-08-18) — sem predições de distribuição sobre dados
  reais, que seriam não-falsificáveis a priori.
- O regime sintético **não prova** que as flags correspondam a corrupção
  real: mede fidelidade à semântica declarada, não validade externa. A
  literatura (Fazekas & Kocsis) dá o lastro teórico; a validação contra
  casos reais conhecidos exige rótulos externos, fora desta bateria.
- **"Proposta única" permanece proxy por modalidade.** O PNCP não entrega
  o número de propostas na fonte ingerida hoje; `dispensa`/`inexigibilidade`
  são processos estruturalmente sem disputa (mesma adaptação já registrada
  no PR-D-01b). O flag real de proposta única exige nova fonte
  (`contratacoes/propostas` do PNCP), fora deste registro.
- **Aditivos estão FORA** — são o O2 e dependem da fonte PNCP
  `contratos/atualizacao`, ainda não ingerida; terão PR próprio.
- **Razão valor final/estimado limitada à presença dos campos**: contratos
  sem `valorInicialCompra` ou `valorTotalHomologado` no payload recebem
  flag **nula** (dado ausente ≠ flag zero) e saem do denominador do CRI —
  a bateria mede essa disciplina de nulos; não mede a cobertura real dos
  campos, que é publicada como estatística descritiva no R-D-04.

## 3. Semântica das red flags (declarada)

Flags binárias por contrato (1 = suspeito; NULL = dado insuficiente), com
fonte e limiar declarados:

- **`f_non_competitive`** — modalidade não competitiva
  (`is_non_competitive(modality)`, silver): dispensa/inexigibilidade.
  Nunca nula **quando a modalidade é conhecida**; modalidade ausente ou
  `not_informed` computa como NULL (dado insuficiente — ver a emenda
  datada em Revisões, que alinha esta semântica ao caso K8/P4).
- **`f_short_window`** — janela de submissão curta:
  `dataEncerramentoProposta − dataAberturaProposta < 7 days` (payload
  bronze). Limiar de 7 dias é **placeholder declarado**, calibrável em PR
  posterior com a distribuição real; a fronteira é exata: 6 dias flag 1,
  7 dias flag 0. NULL quando qualquer das datas falta.
- **`f_price_ratio`** — valor final acima do estimado:
  `valorTotalHomologado > valorInicialCompra`, ambos > 0 (payload bronze).
  Igualdade (razão = 1,0) computa 0. NULL quando qualquer valor falta ou
  não é positivo.

**CRI por contrato** — média aritmética das flags **não nulas** do
contrato (0–1); contrato com todas as flags nulas tem CRI NULL. O mart
gold expõe as flags por contrato e agrega por fornecedor e por órgão
(médias e contagens), servindo de entrada para o O11 (saída pública) e
para o dataset de rótulos do ML supervisionado.

## 4. Desenho

População sintética por seed (estrutura idêntica nas 5 seeds; a seed só
randomiza campos neutros — ids, nomes, datas dentro das janelas
plantadas, valores mantendo as razões plantadas):

- **Caso base** `K1`: todas as flags 0 (modalidade `pregao`, janela 10
  dias, razão 0,9) → CRI 0,0.
- **Caso cheio** `K2`: todas as flags 1 (modalidade `dispensa`, janela 6
  dias, razão 1,2) → CRI 1,0.
- **Fronteira de janela** `K3`/`K4`: janelas de exatamente 7 e 6 dias
  (demais flags 0).
- **Fronteira de razão** `K5`/`K6`: razão exatamente 1,0 e 1,01.
- **Nulos** `K7`: sem datas de proposta no payload → `f_short_window`
  NULL; CRI = média das demais (`f_non_competitive` = 0,
  `f_price_ratio` = 1 → CRI 0,5).
- **Nulo total** `K8`: sem datas e sem valores no payload, modalidade
  `not_informed` → CRI NULL.
- **Controle de parsing** `K9`: payload com campos malformados (data
  `"n/a"`, valor `"abc"`) → flags correspondentes NULL, sem erro de
  execução.

## 5. Predições (numéricas, falsificáveis)

Âncoras exatas — o cômputo é determinístico sobre estrutura plantada;
qualquer desvio refuta. Veredito por seed; a predição falha se divergir
em qualquer uma das 5 seeds.

- **P1 — vetor de flags exato.** Cada caso `K1`–`K9` produz exatamente o
  vetor de flags declarado na seção 4. *Refutada* com qualquer divergência.
- **P2 — fronteira de janela.** `K3` (7 dias) → `f_short_window` = 0;
  `K4` (6 dias) → 1. *Refutada* com qualquer inversão.
- **P3 — fronteira de razão.** `K5` (razão 1,0) → `f_price_ratio` = 0;
  `K6` (1,01) → 1. *Refutada* com qualquer inversão.
- **P4 — composto com nulos.** `K7` → CRI 0,5; `K8` → CRI NULL.
  *Refutada* com qualquer divergência.
- **P5 — determinismo.** A mesma seed reproduz bit a bit as mesmas flags
  e CRIs; execuções distintas da mesma seed divergem em zero casos.
- **P6 — cardinalidade (dados reais).** Cada contrato silver aparece
  **exatamente uma vez** no mart de flags; a contagem de linhas do mart
  iguala a da silver `contracts`. *Refutada* com duplicata ou ausência.
- **P7 — domínio (dados reais).** Toda flag ∈ {0, 1, NULL} e todo CRI ∈
  [0, 1] ∪ {NULL}; zero linhas fora do domínio. *Refutada* com qualquer
  violação.
- **P8 — junção bronze↔silver (dados reais).** A parcela de contratos
  silver sem payload bronze correspondente é **< 1%**. *Refutada* se ≥
  1% (indicaria perda de linhagem na ingestão, a investigar antes de
  confiar nas flags).

## 6. Controles e invariantes

- Controles internos: `K1` (tudo 0) e `K9` (parsing) são os baselines de
  ausência de falso positivo e de robustez; não há baseline externo — a
  semântica da seção 3 é a referência.
- Invariante de composição: para qualquer contrato, o CRI é idêntico à
  média recomputada das flags não nulas do próprio registro (testável
  linha a linha, em sintético e real).
- Invariante de monotonicidade documental: toda calibração futura do
  limiar de janela (7 dias) ou da razão (1,0) exige PR de refinamento
  (`PR-D-04b`) com a distribuição real como justificativa.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 exatas nas 5 seeds e P6–P8 satisfeitas
sobre o volume real do backfill. Qualquer refutação é publicada em
`docs/results/R-D-04.md` com a causa investigada, e a forma corrigida
vira `PR-D-04b.md` antes de nova execução. O sucesso habilita — não
autoriza automaticamente — a exposição do mart via O11 e o uso das flags
como features do ML supervisionado (gap aberto em `docs/gaps.md`).

## 8. Execução

Após a aprovação humana deste registro: implementação com TDD/BDD usual
(engenharia — modelos dbt do mart, data tests dbt para P6–P8) e o runner
da bateria lendo `experiments/detect/D-04.json` sobre payloads sintéticos
em memória (P1–P5, offline como D-01), gravando a saída bruta em
`results/detect/D-04/<seed>.jsonl`. Nenhum código de bateria existe na
data deste registro. A etapa real (P6–P8) executa quando o backfill
estiver concluído, via `dbt test` sobre os testes declarados no mart.

## Revisões

- 2026-08-19: criação (rascunho para revisão humana).
- 2026-08-19: aprovação humana; início da implementação (engenharia).
- 2026-08-19 (emenda pré-execução): o texto original de §3 declarava
  `f_non_competitive` "nunca nula", com `not_informed` computando 0 —
  em contradição com o caso K8/P4, que exige CRI NULL para contrato
  `not_informed` sem datas e sem valores. A predição registrada (P4) é
  soberana: `f_non_competitive` passa a ser NULL quando a modalidade é
  ausente ou `not_informed`, pelo mesmo princípio das demais flags
  (dado insuficiente ≠ flag zero). Emenda pré-execução, sem reescrita
  do histórico.
