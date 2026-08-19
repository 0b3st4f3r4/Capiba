# PR-D-01 — Calibração dos sinais estatísticos sobre contratos sintéticos

- **Pré-registro**: bateria D-01
- **Criado em**: 2026-08-17
- **Última atualização**: 2026-08-18
- **Status**: registrado, não executado

> **Nota (2026-08-18)**: os nomes de sinais citados neste histórico
> (`benford_deviation`, `supplier_concentration`, `duration_outlier_share`)
> foram renomeados para o vocabulário canônico da API — ver a emenda
> datada em `docs/preregistrations/PR-D-01b.md`. O texto abaixo permanece
> como registrado.
- **Alvo**: `detect_fraud_signals` (`src/capiba/pipeline/tasks.py:305`) e os
  operadores de `src/capiba/detection/statistical.py`
- **Configuração**: `experiments/detect/D-01.json` (declarativa, seeds
  inclusas)

## 1. Pergunta

Os três sinais estatísticos da task `detect` se comportam como a teoria
dos seus operadores prediz, sobre contratos sintéticos com verdade de
terreno plantada?

## 2. Regime medido e limitações (obrigatório)

- Regime **sintético**: contratos gerados por distribuições declaradas na
  config. A bateria mede a *fidelidade dos operadores à sua própria
  teoria*; não prova poder de detecção sobre dados reais de licitação.
- Escopo: apenas os operadores estatísticos. Grafos, NLP e os modelos ML
  (`graphs.py`, `nlp_operators.py`, `ml_models.py`) ficam fora.
- A calibração do qui-quadrado assume amostras i.i.d.; contratos reais do
  mesmo fornecedor podem violar isso (limitação transportável).
- Com n = 20 por célula, a contagem esperada do dígito 9 sob Benford é
  0,92 (< 5) — a aproximação assintótica do qui-quadrado é fraca nesse
  regime, e o p-valor pode não ser exatamente uniforme sob o controle. A
  banda de P2 é a predição nominal; se a taxa medida de falsos positivos
  sair da banda por esse motivo, a refutação é informativa (o operador
  opera no regime n ≥ 10, então o comportamento em amostra pequena é
  exatamente o que interessa medir — a forma corrigida iria para
  PR-D-01b, possivelmente com banda recalibrada ou simulação de Monte
  Carlo do p-valor).

## 3. Desenho

Gerador determinístico por seed (10 seeds declaradas na config). Por seed:

- **40 fornecedores controle**: 20 contratos cada, valores
  `log_uniform(1e3, 1e7)` — quatro ordens de grandeza, dígitos iniciais
  conformes a Benford por construção.
- **40 fornecedores plantados**: 20 contratos cada, 60% dos valores com
  dígito inicial 9 (`9.x · 10^k`), 40% log-uniform — padrão de manipulação
  clássico (valores "logo abaixo do teto").
- **Comprador `EQ4`**: 12 contratos divididos igualmente entre 4
  fornecedores (shares 0,25 cada) — âncora exata de HHI.
- **Comprador `C721`**: contratos com shares 0,7 / 0,2 / 0,1 entre 3
  fornecedores — segunda âncora exata.
- **Durações**: controlo `uniform(300, 430)` dias; fornecedor plantado
  `DUR-OUT` com 20 contratos, 6 deles com duração de 1 dia.
- Demais campos (ids, datas) preenchidos de forma válida e neutra.

Cada célula da grade é (seed × fornecedor) para Benford e duração;
compradores são 2 por seed. Totais por execução: 800 células Benford
(400 controle, 400 plantadas), 20 células HHI, 200 células de duração.

## 4. Predições (numéricas, falsificáveis)

- **P1 — conservação da contagem.** O número de sinais emitidos por tipo
  é exatamente o número de entidades elegíveis: 80 sinais
  `benford_deviation` (todos os fornecedores têm ≥ 10 valores), 2 sinais
  `supplier_concentration` por seed, e sinais `duration_outlier_share`
  somente para fornecedores com ao menos um outlier. *Refutada* se a
  contagem divergir em qualquer seed.
- **P2 — calibração de Benford (ânfora exata).** Sob o controle, o score
  (`1 − p`) é uniforme em [0, 1] por construção do teste qui-quadrado:
  a taxa de falso-positivo no limiar score ≥ 0,95 é **5% exatos**.
  Predição: total de falsos positivos nas 400 células controle em
  **[6, 34]** (banda binomial 99,9% em torno de 20). *Refutada* fora da
  banda — indicando amostragem não-i.i.d. ou operador mal implementado.
- **P3 — poder de Benford.** Nas 400 células plantadas (60% dígito 9),
  score ≥ 0,95 em **≥ 380 células** (χ² esperado ≈ 150 por célula contra
  o crítico 15,5). *Refutada* abaixo disso.
- **P4 — HHI exato.** `EQ4` → score **0,2500**; `C721` → score **0,5400**,
  ao dígito (o operador arredonda a 4 casas), nas 10 seeds. *Refutada*
  com qualquer desvio.
- **P5 — duração.** `DUR-OUT` → `duration_outlier_share` = **0,3000**
  exato (6/20) nas 10 seeds, e **zero** sinais de duração entre os
  fornecedores controle (durações 300–430 ficam dentro das cercas do IQR
  pooled). *Refutada* se a share divergir ou se algum controle for
  sinalizado.

## 5. Controles e invariantes

- O controle Benford (P2) e o controle de duração (P5) são os baselines
  internos; não há baseline externo — a teoria dos operadores é a
  referência.
- Invariante de execução: `signals_emitidos = elegíveis_benford +
  elegíveis_hhi + elegíveis_duração`, por seed, sempre (espelha a
  conservação exata do Tanajura adaptada a esta task).
- Reprodutibilidade: a mesma seed reproduz o mesmo conjunto de sinais ao
  dígito (scores arredondados a 4 casas).

## 6. Critério de encerramento

Bateria **bem-sucedida** com P1–P5 dentro das bandas. Qualquer refutação
é publicada em `docs/results/R-D-01.md` com a causa investigada, e a
forma corrigida vira `PR-D-01b.md` antes de nova execução.

## 7. Execução

O runner (`scripts/detect_battery.py`, a implementar com TDD após este
registro) lê `experiments/detect/D-01.json`, gera os contratos por seed,
invoca `detect_fraud_signals` in-process (sem lake/Airflow) e grava a
saída bruta em `results/detect/D-01/<seed>.jsonl`.

## Revisões

- 2026-08-17: criação.
- 2026-08-18: nota de renomeação dos sinais (ver cabeçalho), sem
  reescrita do histórico.
