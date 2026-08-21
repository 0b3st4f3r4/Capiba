# PR-D-11 — ML supervisionado sobre rótulos da triagem editorial

- **Pré-registro**: bateria D-11
- **Criado em**: 2026-08-21
- **Última atualização**: 2026-08-21
- **Status**: rascunho para revisão humana, não aprovado, não executado
- **Alvo**: classificador supervisionado Random Forest
  (`src/capiba/detection/ml_models.py`, `train_rf`/`compute_cri` —
  implementado, sem job de treino nem rótulos suficientes; gap aberto 2
  de `docs/gaps.md`) treinado sobre os rótulos humanos da coleção
  `signal_reviews` (`src/capiba/db/triage.py`), comparado ao baseline do
  CRI **determinístico** do PR-D-04 (média das flags não nulas).
- **Configuração**: `experiments/detect/D-11.json` (declarativa, seeds
  inclusas; criada junto com este registro — runner `supervised` a
  registrar em `scripts/detect_battery.py` na implementação, após
  aprovação)

## 1. Pergunta

Um Random Forest treinado sobre os rótulos editoriais da triagem
(`confirmed`/`published` × `rejected`) supera o CRI determinístico do
PR-D-04 em **balanced accuracy** na tarefa de separar fornecedores com
sinal confirmado de fornecedores com sinais rejeitados?

O gargalo declarado do campo é a ausência de ground truth; o Capiba já
opera a "fábrica de rótulos" (triagem humana `pending_review` →
`confirmed`/`rejected`/`published`, com relatório de precisão por
operador em `/v1/triage/metrics`). Esta bateria mede se os rótulos
acumulados sustentam um modelo supervisionado com vantagem real sobre a
regra determinística já em produção — ou se a fábrica ainda não
produziu sinal estatístico suficiente para justificá-lo.

## 2. Regime medido e limitações (obrigatório)

- Regime **real rotulado** (offline, snapshot das golds +
  `signal_reviews`), sem componente sintético: a pergunta é sobre a
  utilidade dos rótulos humanos acumulados, não sobre semântica
  exata de operador.
- **Rótulos editoriais não são verdade judicial.** `confirmed`/
  `published` atestam juízo editorial do revisor sobre o indício, não
  corrupção comprovada; o estudo mede concordância aprendida com a
  triagem, nunca validade externa. Alegações jornalísticas seguem
  exigindo apuração própria.
- **Viés de seleção da fila.** Os revisores veem o score do sinal antes
  de decidir, e a fila não é uma amostra aleatória dos contratos — o
  rótulo não é independente das features. Este viés é declarado, não
  corrigido; ele limita qualquer leitura causal do resultado.
- **A referência 0,931 da literatura não é meta comparável.** O valor
  (Distributed Random Forest, documentado no docstring de
  `compute_cri`) vem de outra população, outras features e outro
  protocolo; entra aqui apenas como contexto histórico do módulo, não
  como banda de sucesso.
- **Features da literatura ≠ features da plataforma.** O `compute_cri`
  espera `single_bid`, `short_submission_window`, `irregular_timeline`,
  `non_competitive`, `high_concentration`; a plataforma entrega hoje as
  flags do PR-D-04 (`f_non_competitive`, `f_short_window`,
  `f_price_ratio` — proposta única real aguarda a fonte
  `contratacoes/propostas` do PNCP). O RF desta bateria treina sobre o
  conjunto **disponível na plataforma** (§ 3), não sobre o vetor do
  docstring.
- O estudo **não habilita** deploy do `compute_cri` supervisionado nem
  altera a composição de risco da API: essas decisões exigem PR de
  refinamento próprio após o veredito.

## 3. Dataset, rótulo e features (declarados)

- **Unidade de análise**: fornecedor PJ (`entity_type` de sinal de
  contrato/fornecedor na `signal_reviews`), não a revisão individual —
  o split é por entidade (controle anti-vazamento, § 6).
- **Rótulo**: positivo = entidade com **pelo menos um** sinal
  `confirmed` ou `published`; negativo = entidade com todos os sinais
  revisados `rejected` (e nenhum pendente). Entidades com revisões ainda
  `pending_review` são **excluídas** do dataset (não são negativos).
- **Features** (snapshot dos marts gold na data da execução, por
  fornecedor): `contracts`, `contracts_with_cri`, `mean_cri`,
  `share_non_competitive`, `share_short_window`, `share_price_ratio`
  (mart `red_flags_by_supplier`), mais score máximo por `signal_type`
  dos sinais emitidos na gold `fraud_signals` (pivotado; ausência = 0,
  com coluna indicadora de presença). Nenhuma feature deriva do status
  editorial, do revisor ou do `reason`.
- **Modelo**: `train_rf` com os defaults declarados
  (`n_estimators: 200`, `max_depth: 10`, `min_samples_split: 5`,
  `class_weight: balanced`, `random_state` = seed da bateria,
  `n_jobs: -1`) — nenhum tuning nesta bateria; busca de
  hiperparâmetros, se justificada, vira PR-D-11b.
- **Baseline**: CRI determinístico do PR-D-04 (média das flags não
  nulas, `mean_cri` do mart) binarizado no limiar **0,5** (placeholder
  declarado, alinhado ao observado no volume real: nenhum contrato com
  CRI ≥ 0,5 no backfill — R-D-04 § 4); fornecedor com `mean_cri` NULL é
  excluído da avaliação do baseline e do RF (disciplina de nulos do
  PR-D-04).
- **Métrica primária**: balanced accuracy (média das recalls por
  classe), no teste held-out, por seed e na média das 5 seeds.

## 4. Desenho

1. **Gate de volume (P1)**: contagem do dataset elegível. Meta mínima
   declarada: **N ≥ 300** fornecedores rotulados, com **≥ 100 por
   classe**; cada `signal_type` que alimenta rótulo ou feature com
   **≥ 30 revisões** (`confirmed` + `rejected`) — abaixo disso o sinal
   é excluído do dataset e a exclusão é reportada. Se o gate falhar, a
   bateria **não executa**: publica-se em `R-D-11.md` apenas o
   relatório de volume por operador/sinal e a data estimada de
   reavaliação.
2. **Split primário estratificado** 80/20 por entidade, estratificado
   pela classe, repetido nas 5 seeds declaradas. Métrica reportada por
   seed e média ± desvio.
3. **Split secundário temporal** (só se a cobertura de `first_seen`
   rotulado for ≥ 60 dias): treino nos 80% mais antigos, teste nos 20%
   mais recentes — análise de robustez temporal, publicada, sem poder
   de refutação da bateria.
4. **Controles** (§ 6): classificador de chance (BA = 0,5), disciplina
   de nulos, anti-vazamento por entidade, determinismo por seed.

Exploratório declarado (mesma disciplina de PR-D-06b/PR-D-10): o
relatório de volume do passo 1 e as estatísticas descritivas do dataset
(balanceamento, cobertura de features, distribuição de `mean_cri` por
classe) são medidos e documentados na seção de Revisões **antes** da
execução oficial das predições P2–P7.

## 5. Predições (numéricas, falsificáveis)

- **P1 — gate de volume (exata).** Dataset elegível com N ≥ 300, ≥ 100
  por classe e ≥ 30 revisões por `signal_type` incluído. *Refutada*
  abaixo de qualquer meta → bateria não executa (ver § 4).
- **P2 — primária: RF supera o baseline.** Balanced accuracy média do
  RF nas 5 seeds ≥ balanced accuracy do CRI determinístico **+ 0,05**,
  **e** RF ≥ CRI em cada uma das 5 seeds individualmente. *Hipótese de
  refutação explícita*: **"o RF não supera o CRI determinístico em
  balanced accuracy"** — confirmada (bateria refutada) se a margem
  média < 0,05 ou se houver inversão em qualquer seed.
- **P3 — utilidade editorial mínima.** Balanced accuracy média do RF
  ≥ 0,80. *Refutada* abaixo disso — mesmo vencendo o baseline, um
  classificador sob 0,80 não agrega à triagem nesta escala de rótulos.
- **P4 — baseline válido.** Balanced accuracy do CRI determinístico
  > 0,5 (acima do acaso). *Refutada* no acaso ou abaixo: o baseline é
  inútil e a comparação de P2 perde sentido — o veredito passa a ser
  sobre P3 em absoluto.
- **P5 — determinismo (exata).** Mesma seed reproduz bit a bit dataset,
  splits, probabilidades e métricas; execuções distintas da mesma seed
  divergem em zero casos. *Refutada* com qualquer divergência.
- **P6 — anti-vazamento (exata).** Interseção treino ∩ teste por
  entidade vazia nas 5 seeds; nenhuma feature com correlação estrutural
  ao status editorial (checagem declarativa: as colunas do dataset são
  exatamente as de § 3). *Refutada* com qualquer entidade sobreposta ou
  coluna fora da lista.
- **P7 — robustez temporal (secundária, quando aplicável).** No split
  temporal, RF ≥ CRI determinístico em balanced accuracy. Publicada em
  qualquer caso; *não refuta a bateria* — divergência forte aqui vira
  justificativa de PR-D-11b (drift temporal).

## 6. Controles e invariantes

- **Baseline de chance**: BA = 0,5 (classificador majoritário/aleatório
  balanceado) é o piso absoluto, abaixo do baseline CRI de P4.
- **Controle de nulos**: fornecedor sem `mean_cri` (todas as flags
  nulas, caso K8 do PR-D-04) não entra nem no treino nem na avaliação —
  a taxa de exclusão é publicada como estatística descritiva.
- **Anti-vazamento**: split por entidade (P6); o snapshot `score`/
  `details` gravado pela triagem nunca entra como feature (o revisor o
  viu — seria vazamento da variável de seleção).
- **Monotonicidade documental**: margem de P2 (0,05), piso de P3
  (0,80), limiar do baseline (0,5) e metas de volume são placeholders
  pré-registrados — mudança exige PR-D-11b com justificativa datada.
- **Sem tuning nesta bateria**: qualquer busca de hiperparâmetros
  sobre o teste invalidaria P2; se explorada, sobre split de validação
  interno ao treino e documentada como exploratória.

## 7. Critério de encerramento

Bateria **bem-sucedida** com P1 satisfeita e P2–P6 confirmadas. Gate
falho (P1) encerra com relatório de volume, sem vencedor declarado.
Refutação de P2 — inclusive pela hipótese explícita "RF não supera o
CRI determinístico" — é resultado publicável com o mesmo rigor em
`docs/results/R-D-11.md` (regra 2 da doutrina): nesse caso o CRI
determinístico segue como mecanismo de score e o gap de ML
supervisionado permanece aberto com a evidência negativa. Sucesso
habilita — não autoriza automaticamente — um PR de refinamento
(PR-D-11b) para tuning e estudo de ciclo de vida do modelo (job de
treino, persistência, versionamento), pré-condição para qualquer uso
operacional do `compute_cri` supervisionado.

## 8. Execução (após aprovação humana)

1. Extração do dataset: snapshot da `signal_reviews` (rótulos por
   entidade) + leitura Trino dos marts `red_flags_by_supplier` e
   `fraud_signals` (features), materializada em
   `results/detect/D-11/dataset.parquet` com hash e data do snapshot.
2. Relatório de volume (P1) e descritivas, documentados em Revisões
   antes das predições.
3. Runner `supervised` em `scripts/detect_battery.py` lendo
   `experiments/detect/D-11.json`: splits, treino via `train_rf`,
   avaliação RF × CRI determinístico × chance, saída bruta em
   `results/detect/D-11/<seed>.jsonl`; teste de regime
   `@pytest.mark.slow` para o determinismo (P5) e anti-vazamento (P6)
   sobre o dataset cacheado.
4. Nenhuma alteração operacional (API, `task_detect`, composição de
   risco) faz parte desta bateria.

## Revisões

- 2026-08-21: criação (rascunho para revisão humana). Motivação: gap
  aberto 2 de `docs/gaps.md` (ML supervisionado sem job de treino nem
  rótulos) e a fila da triagem (O10) já em operação; o PR-D-04 § alvo
  havia adiado explicitamente o `compute_cri` supervisionado para um PR
  futuro após o gap — este é esse PR. Relatório de volume e descritivas
  do dataset a documentar aqui antes da execução oficial.
- 2026-08-21: **relatório de volume do gate P1 (exploratório declarado,
  § 4)** — medição somente-leitura (AQL) sobre a coleção
  `signal_reviews` do cluster local, antes de qualquer execução das
  predições P2–P7. Resultado: 816.405 documentos na coleção, **todos
  em `pending_review`** — `confirmed` 0, `rejected` 0, `published` 0;
  nenhum documento com `reviewed_by` registrado (zero revisões por
  operador, relatório de precisão vazio). Fila pendente por
  `signal_type`: `collusion_network` 809.220, `anomalous_price` 7.153,
  `concentration` 32 (primeiro registro em 2026-08-19, janela de dois
  dias de produção de sinais). Entidades elegíveis ao dataset (§ 3):
  **N = 0** — positivos 0, negativos 0, pois toda entidade tem revisões
  pendentes e nenhuma revisão concluída; revisões (`confirmed` +
  `rejected`) por `signal_type`: 0 em todos os três tipos, abaixo da
  meta de 30. **Veredito: gate P1 refutado** — déficit integral de 300
  entidades rotuladas (100 por classe) e de 30 revisões por
  `signal_type`. Conforme § 4, a bateria **não executa**: este
  relatório de volume é o resultado publicável da rodada. Sem taxa
  histórica de revisão editorial, não há base para estimar data de
  reavaliação; o gate será reavaliado quando a triagem acumular
  revisões concluídas (acompanhar pelo relatório de precisão por
  operador, `/v1/triage/metrics`).
