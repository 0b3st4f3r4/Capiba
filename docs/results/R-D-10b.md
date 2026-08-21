# R-D-10b — P2b confirmada: bateria notice_clone verde nas 5 seeds (7/7)

- **Resultado**: bateria D-10b (refinamento pré-registrado
  `docs/preregistrations/PR-D-10b.md`, que recalibra P2 do
  `PR-D-10.md` como P2b após a refutação no exploratório)
- **Executada em**: 2026-08-21
- **Regime**: sintético exato offline — corpus de edições com os casos
  plantados N0–N6 (`experiments/detect/D-10b.json`), cadeia completa
  in-process (segmentação `gazette_segments` → sinal
  `detection/notice_clone.py`) com o encoder pinado real
  (`paraphrase-multilingual-MiniLM-L12-v2`, CPU). P6b/P8 (amostra real
  anotada do piloto Recife) e P9 (invariante pós-integração) seguem fora
  desta bateria, pendentes por desenho
- **Saída bruta**: `results/detect/D-10b/` — sinais (`seed_<n>.jsonl`),
  **medidas brutas por seed** (`measures_seed_<n>.json`: scores e ranks
  de todos os pares N0/N1/N2, revocação N2, taxa de FP, contagem N6,
  divergências de repetição) e `summary.json`. A lacuna declarada no
  PR-D-10b § 2 (só a seed 13 tinha medidas brutas persistidas) está
  corrigida: as 5 seeds têm medidas brutas versionadas
- **Veredito**: **success** — P1, P2b, P3, P4, P5, P6a e P7 verdes nas 5
  seeds (13, 29, 41, 59, 71)

## 1. Números

| Predição | Esperado | Medido | Veredito |
|---|---|---|---|
| P1 — âncora N0 (cópia exata) | score 1,0000 (±1e-9), rank 1 | exato nas 5 seeds (2 pares/seed) | success |
| P2b — clones verbais N1 | todo par sinaliza (score > 0,85) e rank ≤ 4 | scores N1 entre 0,8599 e 0,9985; rank máximo 4 (seed 71); 8/8 pares sinalizados por seed | success |
| P3 — clones parafraseados N2 | revocação ≥ 0,75 no limiar 0,85 | 0,875 / 0,875 / 0,875 / 0,75 / 1,0 (seeds 13/29/41/59/71) | success |
| P4 — disciplina de FP (N3/N5) | taxa ≤ 0,10 | 0,0488–0,0649 nas 5 seeds | success |
| P5 — veto de reedição (N4) | zero sinais | zero sinais nas 5 seeds | success |
| P6a — âncora de segmentação (N6) | exatamente 12 unidades | 12 nas 5 seeds | success |
| P7 — determinismo | repetição bit a bit | 0 divergências nas 5 seeds | success |

Medidas brutas de N1 por seed (mínimo–máximo de score; rank máximo):

- seed 13: 0,8893–0,9689; rank máx 3
- seed 29: 0,9248–0,9962; rank máx 3
- seed 41: 0,8599–0,9985; rank máx 3
- seed 59: 0,8748–0,9934; rank máx 2
- seed 71: 0,9021–0,9970; rank máx 4

## 2. Leitura

- **P2b confirmada exatamente na borda declarada.** A banda rank ≤ 4 foi
  ancorada no máximo observado na verificação de desenvolvimento (PR-D-10b
  § 4) e a execução oficial reproduziu o regime bit a bit (P7 verde): a
  seed 71 atinge rank 4, sem folga além da declarada — coerente com a
  natureza de reprodução da bateria (calibração auto-referente, limitação
  central declarada no PR-D-10b § 2). O pior score N1 (0,8599, seed 41)
  fica acima do limiar de emissão 0,85, confirmando que a recalibração
  correta era de **expectativa de rank**, não de semântica: o sinal
  recupera todos os clones verbais plantados.
- **Demais predições inalteradas e verdes**, como declarado no
  refinamento: P1 e P6a exatas, P5 exata, P3 no piso da banda na seed 59
  (0,75), P4 com folga ~1,5–2× sob o teto. Nenhum sinal existente foi
  afetado (guarda do PR-D-10 § 6: `notice_clone.py` intocado — apenas a
  avaliação do runner passou a ler `rank_max`).
- **O que P2b não prova** (PR-D-10b § 2): rank ≤ 4 não é garantia de
  precisão editorial (P8) nem mede generalização; clones reais mais
  perturbados podem cair abaixo de 0,85 e não sinalizar (perda de
  revocação declarada). O gatilho de escalonamento segue de pé: se P8
  mostrar a fila dominada por não-clones, a direção (b) — mascaramento de
  entidades — vira PR-D-10c com a justificativa medida.
- **O componente score ≥ 0,95 da P2 original segue removido**: o score é
  reportado como diagnóstico nas medidas brutas, fora dos critérios.

## 3. Guarda permanente

- O teste de regime `tests/test_detect_battery_notice_clone.py`
  (`@pytest.mark.slow`, `CAPIBA_SLOW=1`) agora lê
  `experiments/detect/D-10b.json` e pinna o veredito **success** das sete
  predições; `tests/test_detect_battery_notice_clone_fast.py` cobre as
  duas formas de P2 (legada `min_score` + rank 1; P2b `rank_max`) com
  encoder stub, incluindo refutação por rank acima da banda e a
  persistência das medidas brutas por seed.
- `D-10.json` permanece intocado como registro histórico da forma
  refutada (doutrina: resultados negativos são resultados); o runner
  mantém a avaliação legada quando `min_score` está declarado.

## 4. Encaminhamento

- O sucesso do D-10b **habilita** (PR-D-10 § 7 / PR-D-10b § 6) a emissão
  best-effort no `task_detect` — passo 5 do PR-D-10 § 8: sinais
  `notice_clone` sobre os textos bronze do `querido_diario`, registrados
  como `pending_review`, padrão best-effort dos demais sinais (nunca
  derrubam a task). Implementado nesta mesma frente, após o veredito.
- Seguem pendentes, por desenho: **P6b/P8** (amostra real do piloto
  Recife + anotação editorial — bandas a fixar por exploratório
  documentado) e **P9** (invariante estrutural pós-integração, verificado
  por query sobre o gold após uma run do `detect`).
