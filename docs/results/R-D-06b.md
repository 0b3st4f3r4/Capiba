# R-D-06b — Screening fuzzy de sanções validado (7/7)

- **Resultado**: bateria D-06b (pré-registro
  `docs/preregistrations/PR-D-06b.md`)
- **Executada em**: 2026-08-20
- **Regimes**: (i) sintético exato offline — casos plantados F1–F9 + 20
  controles (`experiments/detect/D-06b.json`), cômputo in-process via
  `src/capiba/detection/screening_fuzzy.py`; (ii) benchmark real
  OpenSanctions Pairs (snapshot 2025-12-09), regime **nome-only** no
  limiar 0,95, amostra reservoir estratificada 1.000 positivos + 1.000
  negativos com seed **53** (distinta das de D-07/D-07b), cacheada em
  `results/detect/D-06b/pairs_sample.jsonl` (não commitada)
- **Saída bruta**: `results/detect/D-06b/` (5 seeds + `summary.json`)
- **Veredito**: **success** — P1–P5 exatas nas 5 seeds, P6/P7 satisfeitas
  na amostra nova

## 1. Números

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| P1 — conjunto exato de sinais | exatamente F1, F2, F5; demais não | exato nas 5 seeds | success |
| P2 — veto documental | F3 (mascarado divergente) e F7 (completo divergente) sem sinal | exato nas 5 seeds | success |
| P3 — robustez a ruído de nome | F2 sinaliza | exato nas 5 seeds | success |
| P4 — limiar nome-only | F5 (1,0) sinaliza, F6 (0,875) não | exato nas 5 seeds | success |
| P5 — determinismo | zero divergências na repetição | 0 divergências | success |
| P6 — precisão nome-only OS Pairs | ≥ 0,85 | **0,925** (296 tp, 24 fp) | success |
| P7 — revocação nome-only OS Pairs | banda [0,15 – 0,45] | **0,296** | success |

Confusão OS Pairs (amostra 2.000, seed 53): tp 296, fp 24, fn 704,
tn 976. A precisão 0,925 confirma em amostra nova o exploratório da seed
19 (0,912); os 24 falsos positivos são homônimos sem documento — modo de
falha declarado do regime nome-only, filtrado pela triagem humana (O10).

## 2. Leitura

- A semântica de PR-D-06b § 3 está fielmente implementada: veto
  documental (contradição impede o sinal, ausência não), regime
  doc-assistido (0,6×nome + 0,4×doc, limiar 0,85), regime nome-only
  (limiar 0,95), vigência na assinatura e prioridade do match factual
  (F9: quem tem match exato não gera sinal fuzzy na mesma sanção).
- A fonte **CEAF** entrou no pipeline semanal (`weekly_sanctions`):
  crawler genérico (`fetch_sanctions("ceaf")`), normalizador
  `Sanction.from_ceaf` (CPF mascarado → `masked_document`, vigência
  declarada = publicação, aberta) e evolução de schema da silver
  `sanctions` (coluna `masked_document`, aditiva e retrocompatível).
- O sinal `sanctioned_name_match` é hipótese computada (score =
  similaridade), separado do factual `sanctioned_supplier` — a precisão
  dos dois regimes é medida separadamente na triagem (O10).
- **P8 (invariante estrutural, dados reais) fica pendente por desenho**:
  verificação por query sobre o gold após uma run do `detect` com a
  feature e uma coleta real do CEAF — todo sinal com score ≥ limiar do
  seu regime e sem contradição documental. O resultado será anexado a
  este relatório.
- Habilitado para PR próprio: CEAF no grafo (pessoa expulsa como sócia de
  fornecedor, via `same_as` pessoa↔sancionado) — combina O3 + O5.

## 3. Guarda permanente

- A bateria virou teste de regime
  (`tests/test_detect_battery_screening_fuzzy.py`, `@pytest.mark.slow`,
  `CAPIBA_SLOW=1`) com stream OS Pairs mockado (3 pares fixture: 1
  positivo nome idêntico → tp, 1 homônimo negativo → fp documentado, 1
  disjunto → tn), exigindo P1–P5 success e a detecção de refutação.
- `tests/test_screening_fuzzy.py` (13 testes, rápido) guarda a semântica
  do matcher: veto, regimes, limiares, vigência, prioridade factual e
  determinismo.
- `tests/test_sanctions.py` guarda o normalizador CEAF (payload real
  anonimizado como fixture) e o roundtrip silver com `masked_document`;
  a bateria D-06 segue verde (o screening exato não mudou).

## Revisões

- 2026-08-20: publicação — 7/7 predições confirmadas; P8 pendente da run
  real com a feature.
