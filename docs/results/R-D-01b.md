# R-D-01b — Benford corrigido: ciclo fechado (5/5)

- **Resultado**: bateria D-01b (pré-registro `docs/preregistrations/PR-D-01b.md`,
  refinamento de D-01 — refutação em `docs/results/R-D-01.md`)
- **Executada em**: 2026-08-17
- **Saída bruta**: `results/detect/D-01b/` (10 seeds × 83 sinais + `summary.json`)
- **Veredito**: **success** — P1–P5 dentro das bandas

## 1. Números

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| P1 — conservação | 80 Benford + 2 HHI + duração só do plantado, por seed | exato nas 10 seeds | success |
| P2 — calibração | FP em [6, 34] de 400 células controle | **24** (6,0%; nominal 5%) | success |
| P3 — poder | ≥ 380 de 400 células plantadas | **400** | success |
| P4 — HHI exato | 0,2500 / 0,5400 | exato nas 10 seeds | success |
| P5 — duração | share 0,3000; zero sinais controle | exato nas 10 seeds | success |

## 2. Leitura

- O operador corrigido (contagens absolutas no qui-quadrado) comporta-se
  como a teoria prediz: taxa de falsos positivos medida de 6,0% contra o
  5% nominal do limiar — dentro da banda binomial pré-registrada, com o
  pequeno excesso consistente com a aproximação assintótica em n = 20
  (limitação declarada em PR-D-01 § 2; não requer PR-D-01c).
- Poder integral no regime plantado (60% dígito 9): 400/400.
- As âncoras exatas (HHI e share de duração) reproduziram ao dígito pela
  segunda bateria consecutiva.
- O sinal `benford_deviation` passa a ter sentido operacional: estava
  inerte desde a introdução (R-D-01 § 3) e agora separa controle de
  plantado com a taxa de erro nominal do teste.

## 3. Guarda permanente

As predições P1–P5 viraram teste de regressão
(`tests/test_detect_battery.py`, executado sobre a config D-01b): qualquer
mudança futura nos operadores estatísticos ou na task `detect` que viole
as bandas pré-registradas quebra a suíte.

## Revisões

- 2026-08-17: publicação (primeiro ciclo refutação → refinamento →
  confirmação do programa de detecção).
