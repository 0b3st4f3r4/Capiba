# PR-D-01b — Benford corrigido (refinamento de D-01)

- **Pré-registro**: bateria D-01b (refinamento de D-01, refutada — ver
  `docs/results/R-D-01.md`)
- **Criado em**: 2026-08-17
- **Última atualização**: 2026-08-17
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
