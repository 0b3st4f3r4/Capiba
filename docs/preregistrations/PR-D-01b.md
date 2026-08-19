# PR-D-01b — Benford corrigido (refinamento de D-01)

- **Pré-registro**: bateria D-01b (refinamento de D-01, refutada — ver
  `docs/results/R-D-01.md`)
- **Criado em**: 2026-08-17
- **Última atualização**: 2026-08-18
- **Status**: executado, confirmado — ver `docs/results/R-D-01b.md`
- **Configuração**: `experiments/detect/D-01b.json` — mesma grade, seeds e
  expectativas de D-01; a única mudança é no operador sob teste

## 1. Mudança refinada

`benford_score` passa a alimentar `scipy.stats.chisquare` com **contagens
absolutas**: `f_obs` = contagens dos dígitos iniciais, `f_exp` =
frequências de Benford × n. Todo o resto da bateria é idêntico a D-01.

## 2. Predições

Idênticas a PR-D-01 (P1–P5, mesmas bandas), com a ressalva declarada da
aproximação do qui-quadrado em n = 20: se P2 sair da banda [6, 34] por
contagens esperadas < 5, a forma corrigida (p-valor por Monte Carlo, ou
n maior por célula) vai para PR-D-01c.

Âncora fina adicional: na célula controle típica medida em R-D-01
(obs = [5, 2, 4, 1, 2, 4, 1, 0, 1] em n = 20), o χ² correto é
≈ 8,64 (df = 8, p ≈ 0,37) — 20× o valor 0,43 medido com proporções.

## 3. Critério de encerramento

P1–P5 dentro das bandas → o refinamento fecha o ciclo de D-01. Qualquer
refutação é publicada em `R-D-01b.md` com a forma corrigida seguinte.

## Revisões

- 2026-08-17: criação (refinamento pré-registrado após a refutação dupla
  de D-01; mudança restrita ao operador Benford).
- 2026-08-18: emenda — ver seção "Emenda 2026-08-18" abaixo.

## Emenda 2026-08-18 — vocabulário canônico e novos sinais no pipeline

Emenda datada (não reescreve o histórico acima): o post step `detect`
passa a emitir os sinais no vocabulário canônico da API
(`capiba.detection.signals.SignalType`), com a computação bruta dos sinais
extraída para o módulo compartilhado `src/capiba/detection/signals.py`.

### Renomeação de sinais

| Nome antigo (gold)        | Nome canônico        | Mudança numérica |
| ------------------------- | -------------------- | ---------------- |
| `benford_deviation`       | `anomalous_price`    | ver composto abaixo |
| `supplier_concentration`  | `concentration`      | nenhuma (só o nome) |
| `duration_outlier_share`  | `anomalous_duration` | nenhuma (só o nome) |

### Composto `anomalous_price`

`anomalous_price` por fornecedor é o **máximo dos componentes elegíveis**:
`benford_deviation` (≥ 10 valores positivos) e `isolation_forest_rate`
(≥ 15 contratos, IsolationForest sobre `(log1p(amount), duration_days)`).
Ambos os componentes são preservados em `details`
(`{"benford_deviation": ..., "isolation_forest_rate": ..., "contracts": n}`,
`null` quando o componente é inelegível). O pipeline emite scores brutos;
os limiares de emissão da API (p < 0,05; taxa ≥ 0,2) permanecem só na API.

### Novo sinal `single_bid`

Taxa de contratos em modalidade não competitiva (dispensa/inexigibilidade,
mesma regra da API) por fornecedor. Emitido **somente quando a taxa é > 0
e o fornecedor tem ≥ 3 contratos** — evita encher o gold de zeros.
`details`: `{"contracts": n, "non_competitive": k}`.

### Determinismo do IsolationForest

`train_if` (`src/capiba/detection/ml_models.py`) já fixa
`random_state=42`; verificado nesta emenda, nenhuma mudança necessária.
A taxa de anomalias é determinística para uma dada entrada.

### Impacto nas predições

- **P2–P5 avaliam os mesmos números de antes.** P2/P3 passam a ler o
  componente `benford_deviation` de `details` (idêntico ao antigo score do
  sinal homônimo); P4 (HHI) e P5 (share de duração) são intocados.
- **P1 muda apenas nas contagens**, derivadas das regras de elegibilidade
  (não de números mágicos): por seed, `anomalous_price` = 81 (80
  fornecedores elegíveis a Benford + o fornecedor de duração, que com 20
  contratos e amounts nulos torna-se elegível ao IsolationForest),
  `concentration` = 2, `anomalous_duration` = 1 e `single_bid` = 0 (toda a
  população sintética é modality "pregao" → taxa 0, nunca emitido). Total:
  **84 linhas/seed** (eram 83).
