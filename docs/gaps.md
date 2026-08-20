# Checklist de gaps

Gerado na revisão de 2026-08-18, ordenado do mais crítico ao menos crítico,
com os detalhes (caminhos, linhas, evidências) dentro de cada item.

## Crítico: detecção não chega a produção

1. (feito) **Normalizar Receita Federal para silver/grafo.** Quatro peças
   entregues: o parser streaming do dump CNPJ em `src/capiba/ingestion/cnpj.py`
   (modelos Company/Establishment/Partner, chunked), registrado no
   `DUMP_PARSER_REGISTRY` (`src/capiba/pipeline/registry.py`); as tabelas
   silver `companies`/`establishments`/`partners`
   (`src/capiba/pipeline/lake.py`), declaradas como sources no dbt
   (`dbt/models/staging/sources.yml`); a carga de empresas e sócios no
   ArangoDB (`bulk_upsert_cnpj`, vértices `companies`/`partners`, aresta
   `partner_of`, destino `arangodb_graph` no spec
   `dags/pipelines/monthly_federal_revenue.yaml`); e a etapa de normalize
   streaming na fórmula `file_dump` (`src/capiba/pipeline/runner.py`), com a
   task granular `normalize_<fonte>` no factory.
2. (feito) **Expandir o post step `detect`** (`src/capiba/pipeline/tasks.py`).
   `single_bid` e IsolationForest (composto `anomalous_price`) adicionados; a
   computação bruta foi extraída para `src/capiba/detection/signals.py`.
3. (feito) **Reconciliar o vocabulário de sinais pipeline↔API.** O gold grava
   os nomes canônicos `single_bid`/`concentration`/`anomalous_price`/
   `anomalous_duration` (emenda datada em `docs/preregistrations/PR-D-01b.md`).
4. (feito) **Conectar os operadores de grafo ao pipeline.** `detect_collusion`
   e `trace_ownership` foram reescritos na semântica adaptada e validados
   empiricamente (bateria D-02, `docs/results/R-D-02.md`, 6/6), e agora estão
   conectados: o `task_detect` emite o sinal `collusion_network` por par de
   fornecedores (`collusion_signals` em `detection/signals.py`, score binário
   1.0, limiar `DETECTION_COLLUSION_MIN_WINS=3` — placeholder cuja calibração
   em volume real (bateria D-03, `docs/results/R-D-03.md`) foi **inconclusiva**:
   a semântica de pares explode no regime real, e o refinamento por co-ocorrência
   entre compradores (bateria D-03b, `docs/results/R-D-03b.md`) também foi
   **inconclusivo** — redução ~28×, mas nenhum ponto da grade {3,4,5} × {2,3}
   coube no orçamento de triagem (próximo refinamento: PR-D-03c);
   best-effort, pois ArangoDB fora do ar não derruba a task) e a API expõe
   `GET /v1/graph/ownership/{cnpj}?max_depth=3` (router `api/routers/graph.py`,
   503 com ArangoDB indisponível). `anomalous_geography` segue fora, sem fonte
   de lat/long.
5. (aberto) **Conectar os operadores NLP.** `semantic_gap` e `detect_clone`
   (`src/capiba/detection/nlp_operators.py`), junto com `db/vectors.py` e
   `db/search.py`, seguem sem consumidor; `SignalType.SEMANTIC_GAP` não tem
   produtor.
6. (feito) **Ativar `notification/`.** O `NotificationDispatcher` foi ligado ao
   pipeline via `src/capiba/notification/alerts.py`: o `task_detect` alerta
   sinais ≥ `NOTIFICATION_ALERT_SCORE` e o `task_validate_pipeline` alerta
   relatórios inválidos ou erro de normalização acima de 5%; recipients vazio
   significa no-op. O `NotificationScheduler` passou a enviar métricas reais
   (ver item próprio na seção Alto).
7. (feito) **Integrar `evidence/`.** Router `/v1/evidence`
   (`src/capiba/api/routers/evidence.py`): upload multipart com metadados
   obrigatórios, listagem por contrato e download por SHA-256, sobre o
   `EvidenceStorage` (instanciação lazy via `get_storage()`).

## Alto: operação em produção

1. (feito) **Configurar `TRANSPARENCY_API_KEY`.** A chave está no `.env` desde
   o início; o `scripts/helm-upgrade.sh` a injeta no chart via `--set
   global.transparencyApiKey`. Era só documentação desatualizada.
2. (aberto) **ML supervisionado com ciclo de vida.** `train_rf` e `compute_cri`
   seguem sem job de treino nem persistência de modelo; a API compõe o risco
   sem usar o CRI.
3. (feito) **`NotificationScheduler` oco.** Os relatórios diário, semanal e
   mensal agregam as métricas reais do `QualityMonitor`
   (`record_batch`/`get_metrics`/`list_datasets` em `quality/monitor.py`,
   alimentado pelo `task_validate_pipeline`); sem dados, o relatório diz
   explicitamente que não há dados no período. O scheduler é iniciado no
   lifespan da API (`api/main.py`) somente com `NOTIFICATION_RECIPIENTS`
   configurado.
4. (feito) **Marts de pod usage sem refresh.** `post_steps: [dbt_run]`
   adicionado a `dags/pipelines/hourly_pod_usage.yaml` (task granular
   `dbt_run` após o destino bronze).

## Médio: qualidade e observabilidade

1. (feito) **Conectar profiling e monitoramento ao pipeline.** O
   `task_validate_pipeline` (`pipeline/tasks.py`) alimenta o `QualityMonitor`
   em regime best-effort: profile do lote (`profile_dataset`, com baseline e
   check de thresholds) e métricas do lote (total, duplicates,
   normalization_errors, falhas de quality_rules por severidade) via
   `record_batch`. Degrada graciosamente sem Redis.
2. (feito) **Completar o lineage OpenLineage.** `GOLD_MARTS`
   (`dags/pipeline_factory.py`) com `pod_usage_hourly` e
   `platform_cost_daily`; serving materializado no PostgreSQL DWH com outlets
   `capiba://dwh/serving_supplier_stats` e
   `capiba://dwh/serving_municipality_daily`; `SOURCE_INLETS` com `pod_usage`
   (`capiba://source/pod_usage`, metrics-server).
3. (parcial) **Novos endpoints de fontes já analisados.** Na Transparência,
   `ceis` e `cnep` (sanções) estão feitos: pipeline semanal `weekly_sanctions`
   (fórmula `entities_collect`, bronze `raw_ceis`/`raw_cnep` e silver
   `sanctions`, modelo `Sanction`); o sinal "fornecedor sancionado" depende de
   pré-registro (PR-D-06, registrado e aprovado, não executado). Seguem abertos `licitacoes` e `despesas/documentos`
   (Transparência) e `atas`, `contratacoes/publicacao` e
   `contratos/atualizacao` (PNCP), com pré-registro PR-D-* para os que virarem
   sinal.
4. (feito) **Testes dbt irregulares.** `unique` e `not_null` em `contracts.id`
   (silver) e `unique` em `contracts_by_agency.siafi_code`; testes de coluna
   (`not_null` em dt/ingested_at/payload_json) em `raw_pod_usage` e
   `raw_federal_revenue`; unicidade composta de `pod_usage_hourly` (hour, pod)
   e `platform_cost_daily` (dt, pod) via data tests singulares em `dbt/tests/`.
   O dbt_utils não é dependência do projeto (não há `dbt/packages.yml`), então
   o teste nativo foi usado em vez de `unique_combination_of_columns`.

## Baixo: código e docs

1. (feito) **Docs desatualizados.** `docs/ingestao.md` descrevia o design
   rejeitado da task única, e o factory gera tasks granulares;
   `docs/apis_fontes.md` já registra a Receita integrada ao pipeline (bronze,
   silver e grafo) e o cache/fila em Redis; o `README.md` reflete que as DAGs
   são geradas pelo factory, que o Redis vem habilitado por padrão e que o
   issuer SSO é HTTPS na 8443; `docs/api.md` documenta as rotas do portal SSO
   (`/`, `/auth/login`, `/auth/callback`, `/auth/logout`) e tem a string do 503
   alinhada (`ArangoDB database unavailable`).
2. (feito) **Limpezas de código.** O hack `_Result` local em
   `pipeline/tasks.py` cedeu lugar ao `FormulaResult`; a duplicação de
   download/manifest/sha256 entre `task_crawl_federal_revenue` e
   `task_download_source` foi resolvida removendo o legado imperativo
   (`task_crawl_federal_revenue` e demais `task_*` sem chamador em
   `pipeline/tasks.py`), e o fluxo declarativo (`task_download_source`) é o
   único caminho; o SMS, declarado mas não suportado, saiu do enum
   `NotificationChannel` (`notification/dispatcher.py`); os testes de
   `db/vectors.py` e `db/search.py` migraram de `test_detection.py` para
   `test_db.py`; e `config.py` e `transformations/filter_by_min_value.py`
   ganharam teste dedicado.

## Editorial: jornalismo de dados comunitário

Itens dimensionados e com critério de aceitação em `docs/oportunidades.md`
(ordem sugerida: O9/O10 → O1/O2 → grafo/O3/O4 → O7 → demais).

1. (feito) **Triagem editorial de sinais (O10).** Coleção
   ArangoDB `signal_reviews` (`db/triage.py`, chave estável
   `{entity_type}:{entity_id}:{signal_type}`, estado `pending_review` →
   `confirmed`/`rejected`/`published`, revisor obrigatório e motivo no
   descarte, `published` terminal, histórico de revisões); o `task_detect`
   registra sinais novos como `pending_review` (best-effort); rotas `GET
   /v1/triage/signals`, `POST /v1/triage/signals/{key}/review` e `GET
   /v1/triage/metrics` (precisão por operador derivada dos rótulos); página
   `/triage` no portal (fila por estado, ações de confirmar/rejeitar/
   publicar, revisor da sessão SSO ou do campo, relatório de precisão).
2. (feito) **Pacote de evidências reproduzível por sinal (O9).** O `task_detect`
   armazena no `EvidenceStorage` (best-effort) um pacote de lote por run (linhas
   silver + `source_rows_sha256` + janela + versão do código, essencial porque
   `anomalous_duration` usa IQR pooled) e um manifesto por sinal com a chave do
   O10 e `batch_sha256` (`evidence/packages.py`; o `collusion_network` ganhou
   na bateria D-03 o pacote `graph_batch` — snapshot de elegibilidade
   `{buyer, supplier, wins}` + `min_wins` + `snapshot_sha256` — e seus
   manifestos passam a `reproducible: true` quando o snapshot é armazenado).
   Reprodução via
   `reproduce_signal` (reexecuta `detect_fraud_signals` sobre as linhas do
   pacote, ou re-deriva os pares do snapshot de grafo, e compara o score —
   critério de aceitação coberto por BDD). Endpoint
   `GET /v1/signals/{key}/evidence` lista os pacotes do sinal; o download segue
   pelo `GET /v1/evidence/{sha256}`.
3. (em andamento) **CRI de Fazekas & Kocsis (O1).** Pré-registrado e
   validado no regime sintético (PR-D-04/R-D-04, 5/5): semântica de
   referência em `detection/red_flags.py` (3 flags — modalidade não
   competitiva, janela curta de submissão, razão valor final/estimado —
   1/0/NULL, CRI = média das não nulas) e mart gold `contract_red_flags`
   (+ agregados por fornecedor/órgão) com data tests dbt dos invariantes
   reais P6–P8, pendentes da conclusão do backfill. O `compute_cri`
   supervisionado segue sem uso (gap de ML supervisionado); proposta
   única real aguarda a fonte `contratacoes/propostas` do PNCP.
4. (em andamento) **Red flags de aditivos (O2).** Pré-registrado e
   validado no regime sintético (PR-D-05/R-D-05, 5/5): fonte PNCP
   `contratos/atualizacao` registrada (`pncp_contract_updates`, pipeline
   diário bronze-only `daily_pncp_updates`), semântica de referência em
   `detection/amendments.py` (aditivo de valor via `valorAcumulado` >
   `valorInicial`, aditivo de prazo via extensão de vigência — última
   observação soberana) e mart gold `contract_amendments` (+ agregados
   por fornecedor/órgão) com data tests dbt dos invariantes reais P6–P8,
   pendentes da conclusão do backfill.
5. (em andamento) **Screening de sanções e PEPs (O3).** Match exato por
   CNPJ/CPF contra CEIS/CNEP validado (PR-D-06/R-D-06, 5/5): sinal
   `sanctioned_supplier` (`detection/screening.py`, score binário, vigência
   inclusiva na `signature_date`) integrado ao `task_detect` (best-effort
   sobre o silver `sanctions`). CEAF fora do escopo (CPF mascarado na fonte,
   confirmado ao vivo); PEPs/OpenSanctions (`yente` self-hosted) e fuzzy por
   nome ficam para PRs próprios.
6. (aberto) **Esquema FollowTheMoney no grafo (O4).**
   Person/Company/Ownership/Directorship no ArangoDB; prepara o
   `trace_ownership` e a exportação FtM JSON.
7. (aberto) **Entity resolution (O5).** Deduplicação de fornecedores e sócios
   entre bases; benchmark OpenSanctions Pairs; merges só acima de limiar
   pré-registrado.
8. (aberto) **Diários oficiais municipais via Querido Diário (O7).** Source
   `querido_diario` no registry; município-piloto persistindo no bronze.
9. (aberto) **TSE: doações × contratos (O8).** Sinal `political_connection` com
   pré-registro e critério de refutação.
10. (aberto) **Saída pública para a comunidade (O11).** Marts gold baixáveis
    (CSV/Parquet) sem auth, com metodologia gerada do dbt docs; classificação
    LGPD conferida.
11. (aberto) **Alertas para jornalistas comunitários (O12).** Assinatura de
    pautas por município ou órgão via `notification/` (depende de O9/O10).
12. (aberto) **Modo leve de onboarding.** Execução local sem k3s (sqlite/duckdb)
    para o jornalista solo ou a redação comunitária.

## Roadmap de longo prazo (declarado, fora da fase)

1. Fontes TSE (dados eleitorais).
2. Dados privados via LGPD/DP, que exigem análise de base legal prévia.
3. Protocolos federados: differential privacy, federated learning, ZKP.
4. Governança: glossário de negócio, classificação de sensibilidade por coluna,
   laço automático de requests/limits.
5. Baterias pré-registradas de validação empírica para grafos, NLP e ML:
   grafos feitos (PR-D-02 aprovado, bateria D-02 executada com sucesso,
   `docs/results/R-D-02.md`); NLP e ML seguem sem PR.
