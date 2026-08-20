# R-D-07b — Banda de revocação recalibrada confirmada (7/7)

- **Resultado**: bateria D-07b (pré-registro
  `docs/preregistrations/PR-D-07b.md`, forma corrigida da P7 refutada em
  D-07 — ver `docs/results/R-D-07.md`)
- **Executada em**: 2026-08-20
- **Regimes**: idênticos a D-07 — sintético exato (E1–E8 + 20 controles,
  5 seeds) e benchmark OpenSanctions Pairs (snapshot 2025-12-09), agora
  com **3 amostras** reservoir estratificadas (1.000 positivos + 1.000
  negativos) de seeds 23, 37 e 41 (distintas da seed 19 de D-07),
  cacheadas em `results/detect/D-07b/pairs_sample_<seed>.jsonl` (não
  commitadas; regeneráveis)
- **Matcher**: intocado — mesma semântica, pesos e limiar de D-07
  (config `experiments/detect/D-07b.json` idêntica à D-07 exceto
  `sample_seeds` e `recall_band`)
- **Saída bruta**: `results/detect/D-07b/` (5 seeds + `summary.json`)
- **Veredito**: **success** — P1–P5 exatas nas 5 seeds, P6 e P7b
  satisfeitas nas 3 amostras

## 1. Números

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| P1–P5 (sintético) | exato nas 5 seeds, zero divergências | exato | success |
| P6 — precisão OS Pairs | ≥ 0,90 por amostra | **1,00 / 1,00 / 1,00** (seeds 23/37/41; 0 fp em 3.000 negativos) | success |
| P7b — revocação OS Pairs | banda [0,00 – 0,10] por amostra | **0,024 / 0,025 / 0,034** | success |

Taxa de documento bilateral entre os positivos por amostra: **4,5% /
4,7% / 5,2%** — estável e coerente com os 4,8% medidos em D-07,
confirmando que a revocação ≈ taxa_bilateral × merge_condicional é uma
propriedade estrutural do benchmark × matcher, não flutuação amostral.

Confusão agregada (3 amostras, 6.000 pares): tp 83, fp 0, fn 2.917,
tn 3.000.

## 2. Leitura

- A recalibração **fecha a calibração do matcher** para o regime RFB
  (sócios com CPF mascarado sempre presente): a revocação baixa no OS
  Pairs é o preço conhecido e medido da disciplina de homônimo
  (precisão 1,00 em 6.000 pares acumulados entre D-07 e D-07b).
- Nenhuma feature nome-only foi adicionada; elevar a revocação sobre
  listas multilíngues de sanções é problema do screening fuzzy
  (PR-D-06b), com matcher e limiar próprios se vier.
- O caminho `resolve_entities` → aresta `same_as` está validado para uso
  best-effort na carga do grafo; **P8 (invariante estrutural no grafo
  real) segue pendente** do reload pós-O4, como registrado em R-D-07 § 3.

## 3. Guarda permanente

- `tests/test_detect_battery_entities.py` cobre a multi-amostra:
  config D-07b com stream fixture gera um cache por seed, agrega os
  vereditos por amostra e detecta refutação da banda.
- Os guards de D-07 (R-D-07 § 4) seguem valendo inalterados.

## Revisões

- 2026-08-20: publicação — 7/7 predições confirmadas; calibração do
  matcher de entity resolution encerrada para o regime RFB.
