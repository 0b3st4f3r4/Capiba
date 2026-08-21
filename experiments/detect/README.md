# experiments/detect — baterias de detecção

> **Propósito**: como rodar uma bateria de detecção (experimento
> pré-registrado) após a migração de 2026-08-21 para o pacote `batteries/`.
> **Quando consultar**: antes de executar ou criar uma bateria D-*.
> **Relacionados**: `docs/preregistrations/README.md` (doutrina de
> pré-registro e índice de baterias/vereditos), `docs/operadores.md`
> (sinais), `results/detect/` (saídas brutas).
> **Sincronizado com**: `scripts/detect_battery.py`,
> `experiments/detect/*.json`, `experiments/detect/batteries/` — 2026-08-21.

## Doutrina (resumo)

Nenhuma bateria roda sem **pré-registro**: predição numérica falsificável
com critérios de sucesso **e de refutação** em
`docs/preregistrations/PR-D-*.md` antes de qualquer execução. O resultado —
inclusive negativo — é publicado em `docs/results/R-D-*.md`. Índice e
vereditos: `docs/preregistrations/README.md`.

## Layout

- `batteries/` — pacote com 13 módulos de bateria (`battery.py`,
  `battery_flags.py`, `battery_amendments.py`, `battery_screening.py`,
  `battery_screening_fuzzy.py`, `battery_entities.py`, `battery_political.py`,
  `battery_geography.py`, `battery_notice_clone.py`, `battery_terms.py`,
  `battery_graphs.py`, `battery_collusion.py`, `battery_pep_screening.py`).
  Vive fora do pacote de produção: importável via pythonpath do pytest e via
  `sys.path.insert` no script dispatcher.
- `*.json` — configs declarativas das baterias (seeds inclusas).
- `scripts/detect_battery.py` — dispatcher; escolhe o runner pelo campo
  `runner` da config (`requires_infra: "arangodb"` cai em `battery_graphs`).

## Como rodar

```bash
python scripts/detect_battery.py experiments/detect/D-01.json [--out DIR] [--skip-real]
```

- Posicional: caminho da config JSON. Saída default em
  `results/detect/<id>/`, com veredito consolidado em `summary.json`;
  `--out` sobrepõe o diretório. Exit code 0 só em veredito `success`.
- `--skip-real`: pula o sweep sobre o grafo real (só runner `collusion`).
- Baterias lentas (regime/calibração) usam o marker pytest `slow`:
  habilitar com `CAPIBA_SLOW=1` (CI e `make test-cov`/`make test-slow`
  já habilitam).
- Baterias com infra externa (ArangoDB) exigem o cluster acessível
  (`make port-forward`).

## Configs existentes

Vereditos completos no índice de `docs/preregistrations/README.md`.

- `D-01.json` — calibração dos sinais estatísticos (benford, hhi, duracao)
  sobre contratos sintéticos.
- `D-01b.json` — refinamento de D-01: operador Benford corrigido.
- `D-02.json` — operadores de grafo sobre ArangoDB (semântica adaptada).
- `D-03.json` — calibração do limiar `min_wins` do `collusion_network`.
- `D-03b.json` — refinamento collusion: semântica de co-ocorrência.
- `D-03c.json` — refinamento collusion: escala da derivação de pares.
- `D-03d.json` — refinamento collusion: emissão ranqueada top-k com orçamento
  editorial.
- `D-04.json` — red flags de contrato e CRI determinístico (PR-D-04).
- `D-05.json` — red flags de aditivos sobre sequências plantadas (PR-D-05).
- `D-05b.json` — aditivos via termos registrados do contrato (plano B).
- `D-06.json` — screening de sanções por documento exato.
- `D-06b.json` — screening fuzzy: nome normalizado + documento mascarado.
- `D-07.json` — entity resolution de fornecedores e sócios.
- `D-07b.json` — recalibração da banda de revogação no OS Pairs.
- `D-08.json` — conexão política: doadores TSE × fornecedores do ente.
- `D-08b.json` — paridade de espelho TSE (silvers campaign_donations/
  candidacies).
- `D-09.json` — geografia anômala: sede PJ distante do município comprador.
- `D-10.json` — clonagem de editais (`notice_clone`) no Querido Diário.
- `D-10b.json` — refinamento de D-10: P2 recalibrada para o regime real.
- `D-11.json` — ML supervisionado sobre rótulos da triagem editorial.
- `D-12.json` — piloto de screening de PEPs (yente/OpenSanctions).
