# Checklist de gaps — Capiba

Gerado na revisão de 2026-08-18. Ordenado do mais crítico ao menos crítico.
Detalhes (caminhos, linhas, evidências) em cada item.

## Crítico — detecção não chega a produção

- [x] **Normalizar Receita Federal para silver/grafo** — 4 peças entregues:
  - [x] Parser streaming do dump CNPJ em `src/capiba/ingestion/cnpj.py` (modelos Company/Establishment/Partner, chunked), registrado no `DUMP_PARSER_REGISTRY` (`src/capiba/pipeline/registry.py`)
  - [x] Tabelas silver `companies`/`establishments`/`partners` (`src/capiba/pipeline/lake.py`) declaradas como sources no dbt (`dbt/models/staging/sources.yml`)
  - [x] Carga de empresas/sócios no ArangoDB (`bulk_upsert_cnpj`, vértices `companies`/`partners`, aresta `partner_of`; destino `arangodb_graph` no spec `dags/pipelines/monthly_federal_revenue.yaml`)
  - [x] Etapa de normalize streaming na fórmula `file_dump` (`src/capiba/pipeline/runner.py`) + task granular `normalize_<fonte>` no factory
- [x] **Expandir o post step `detect`** (`src/capiba/pipeline/tasks.py`) — `single_bid` e IsolationForest (composto `anomalous_price`) adicionados; computação bruta extraída para `src/capiba/detection/signals.py`
- [x] **Reconciliar vocabulário de sinais pipeline↔API** — gold grava os nomes canônicos `single_bid`/`concentration`/`anomalous_price`/`anomalous_duration` (emenda datada em `docs/preregistrations/PR-D-01b.md`)
- [ ] **Conectar operadores de grafo ao pipeline** — `detect_collusion`, `trace_ownership`, `anomalous_geography` (`src/capiba/detection/graphs.py`) sem chamador; `SignalType.COLLUSION_NETWORK` sem produtor (depende da Receita Federal)
- [ ] **Conectar operadores NLP** — `semantic_gap`, `detect_clone` (`src/capiba/detection/nlp_operators.py`) + `db/vectors.py`/`db/search.py` sem consumidor; `SignalType.SEMANTIC_GAP` sem produtor
- [x] **Ativar `notification/`** — `NotificationDispatcher` ligado ao pipeline via `src/capiba/notification/alerts.py`: `task_detect` alerta sinais ≥ `NOTIFICATION_ALERT_SCORE` e `task_validate_pipeline` alerta relatórios inválidos/erro de normalização > 5%; recipients vazio = no-op. O `NotificationScheduler` passou a enviar métricas reais (ver item próprio abaixo)
- [x] **Integrar `evidence/`** — router `/v1/evidence` (`src/capiba/api/routers/evidence.py`): upload multipart com metadados obrigatórios, listagem por contrato e download por SHA-256, sobre o `EvidenceStorage` (instanciação lazy via `get_storage()`)

## Alto — operação em produção

- [ ] **Configurar `TRANSPARENCY_API_KEY`** (`docs/apis_fontes.md`) — pipeline diário depende dela
- [ ] **ML supervisionado com ciclo de vida** — `train_rf`/`compute_cri` sem job de treino nem persistência de modelo; API compõe risco sem usar CRI
- [x] **`NotificationScheduler` oco** — relatórios diário/semanal/mensal agregam as métricas reais do `QualityMonitor` (`record_batch`/`get_metrics`/`list_datasets` em `quality/monitor.py`, alimentado pelo `task_validate_pipeline`); sem dados, o relatório diz explicitamente que não há dados no período; scheduler iniciado no lifespan da API (`api/main.py`) somente com `NOTIFICATION_RECIPIENTS` configurado
- [x] **Marts de pod usage sem refresh** — `post_steps: [dbt_run]` adicionado a `dags/pipelines/hourly_pod_usage.yaml` (task granular `dbt_run` após o destino bronze)

## Médio — qualidade e observabilidade

- [x] **Conectar profiling/monitoramento ao pipeline** — `task_validate_pipeline` (`pipeline/tasks.py`) alimenta o `QualityMonitor` best-effort: profile do lote (`profile_dataset` → baseline + check de thresholds) e métricas do lote (total, duplicates, normalization_errors, falhas de quality_rules por severidade) via `record_batch`; degrada graciosamente sem Redis
- [x] **Completar lineage OpenLineage** — `GOLD_MARTS` (`dags/pipeline_factory.py`) com `pod_usage_hourly`/`platform_cost_daily`; serving materializado no PostgreSQL DWH com outlets `capiba://dwh/serving_supplier_stats`/`capiba://dwh/serving_municipality_daily`; `SOURCE_INLETS` com `pod_usage` (`capiba://source/pod_usage`, metrics-server)
- [ ] **Novos endpoints de fontes já analisados** — Transparência: `ceis`, `cnep` (sanções), `licitacoes`, `despesas/documentos`; PNCP: `atas`, `contratacoes/publicacao`, `contratos/atualizacao` (pré-registro PR-D-* para os que virarem sinal)
- [x] **Testes dbt irregulares** — `unique`+`not_null` em `contracts.id` (silver) e `unique` em `contracts_by_agency.siafi_code`; testes de coluna (`not_null` em dt/ingested_at/payload_json) em `raw_pod_usage` e `raw_federal_revenue`; unicidade composta de `pod_usage_hourly` (hour, pod) e `platform_cost_daily` (dt, pod) via data tests singulares em `dbt/tests/` (dbt_utils não é dependência do projeto — sem `dbt/packages.yml` — então o teste nativo foi usado em vez de `unique_combination_of_columns`)

## Baixo — código e docs

- [x] **Docs desatualizados:**
  - [x] `docs/ingestao.md` — descreve o design rejeitado (task única); factory gera tasks granulares
  - [x] `docs/apis_fontes.md` — Receita já está integrada ao pipeline (bronze + silver + grafo); cache/fila já existe (Redis)
  - [x] `README.md` — DAGs são geradas pelo factory; Redis habilitado por padrão; issuer SSO é HTTPS 8443
  - [x] `docs/api.md` — rotas do portal SSO documentadas (`/`, `/auth/login`, `/auth/callback`, `/auth/logout`); string do 503 alinhada (`ArangoDB database unavailable`)
- [ ] **Limpezas de código:**
  - [x] Hack `_Result` local em `pipeline/tasks.py` — reutilizar `FormulaResult`
  - [ ] Duplicação download/manifest/sha256 entre `task_crawl_federal_revenue` e `task_download_source`
  - [x] SMS declarado mas não suportado (`notification/dispatcher.py`) — removido do enum `NotificationChannel`
  - [x] Mover testes de `db/vectors.py`/`db/search.py` de `test_detection.py` para `test_db.py`
  - [x] Teste dedicado para `config.py` e `transformations/filter_by_min_value.py`

## Editorial — jornalismo de dados comunitário

Itens dimensionados e com critério de aceitação em `docs/oportunidades.md`
(ordem sugerida: O9/O10 → O1/O2 → grafo/O3/O4 → O7 → demais).

- [ ] **Triagem editorial de sinais (O10)** — estado `pending_review` → `confirmed`/`rejected`/`published` com motivo obrigatório no descarte; rótulos humanos alimentam o ML supervisionado
- [ ] **Pacote de evidências reproduzível por sinal (O9)** — query + janela temporal + SHA do artefato + linhas fonte com hash; endpoint `GET /signals/{id}/evidence`; integra `EvidenceStorage` ao post step `detect`
- [ ] **CRI de Fazekas & Kocsis (O1)** — mart gold com red flags: proposta única, prazo curto de submissão, modalidade restritiva, razão valor final/adjudicado, aditivos; `compute_cri` existe sem uso — requer pré-registro PR-D-*
- [ ] **Red flags de aditivos (O2)** — fonte PNCP `contratos/atualizacao`; percentual de contratos com aditivo por órgão/fornecedor no gold
- [ ] **Screening de sanções e PEPs (O3)** — CEIS/CNEP/CEAF (Transparência) + OpenSanctions (`yente` self-hosted); match exato por CNPJ antes de fuzzy por nome
- [ ] **Esquema FollowTheMoney no grafo (O4)** — Person/Company/Ownership/Directorship no ArangoDB; prepara `trace_ownership` e export FtM JSON
- [ ] **Entity resolution (O5)** — deduplicação de fornecedores/sócios entre bases; benchmark OpenSanctions Pairs; merges só acima de limiar pré-registrado
- [ ] **Diários oficiais municipais via Querido Diário (O7)** — source `querido_diario` no registry; município-piloto persistindo no bronze
- [ ] **TSE: doações × contratos (O8)** — sinal `political_connection` com pré-registro e critério de refutação
- [ ] **Saída pública para a comunidade (O11)** — marts gold baixáveis (CSV/Parquet) sem auth + metodologia gerada do dbt docs; classificação LGPD conferida
- [ ] **Alertas para jornalistas comunitários (O12)** — assinatura de pautas por município/órgão via `notification/` (depende de O9/O10)
- [ ] **Modo leve de onboarding** — execução local sem k3s (sqlite/duckdb) para jornalista solo ou redação comunitária

## Roadmap de longo prazo (declarado, fora da fase)

- [ ] Fontes TSE (dados eleitorais)
- [ ] Dados privados via LGPD/DP — exige análise de base legal prévia
- [ ] Protocolos federados — differential privacy, federated learning, ZKP
- [ ] Governança: glossário de negócio, classificação de sensibilidade por coluna, laço automático de requests/limits
- [ ] Baterias pré-registradas de validação empírica para grafos/NLP/ML — grafos com PR em rascunho (`docs/preregistrations/PR-D-02.md`, aguardando aprovação; bateria D-02 não executada); NLP e ML sem PR
