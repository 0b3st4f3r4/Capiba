# Checklist de gaps

Gerado na revisão de 2026-08-18 e revalidado em 2026-08-21, ordenado do
mais crítico ao menos crítico, com os detalhes (caminhos, linhas,
evidências) dentro de cada item.

## Crítico: detecção não chega a produção

1. (feito) **Normalizar Receita Federal para silver/grafo.** Quatro peças
   entregues: o parser streaming do dump CNPJ em `src/capiba/ingestion/cnpj.py`
   (modelos Company/Establishment/Partner, chunked), registrado no
   `DUMP_PARSER_REGISTRY` (`src/capiba/pipeline/registry.py`); as tabelas
   silver `companies`/`establishments`/`partners`
   (`src/capiba/pipeline/lake.py`), declaradas como sources no dbt
   (`dbt/models/staging/sources.yml`); a carga de empresas e sócios no
   ArangoDB (`bulk_upsert_cnpj`, vocabulário FtM: vértices
   `companies`/`persons`, arestas `ownership`/`directorship` — as coleções
   legadas `partners`/`partner_of`/`owns` foram renomeadas, destino
   `arangodb_graph` no spec
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
   503 com ArangoDB indisponível). Guarda operacional (2026-08-21): a
   derivação de pares é pulada acima de `DETECTION_COLLUSION_MAX_PAIRS`
   (default 1M) — 9,6M pares projetados no grafo real OOMKillaram o pod;
   o snapshot de elegibilidade segue gravado como evidência para o PR-D-03c. `anomalous_geography` está **ativado**
   (O6): a fatia 1 entregou a infra (referência vendored de municípios em
   `src/capiba/ingestion/geography.py` + CSV MIT em
   `src/capiba/ingestion/reference/`, silvers `municipalities` —
   loader idempotente `lake.load_municipalities` — e `rfb_municipalities`,
   e enriquecimento best-effort dos vértices `buyers`/`suppliers` na
   persistência) e a fatia 2 entregou o sinal
   (`src/capiba/detection/geography.py`, contrato PR-D-09 §3: haversine
   R = 6371,0 km entre sedes municipais, gate estrito > 100 km, score
   proporcional saturando em 1.000 km), validado no regime sintético pela
   bateria D-09 (`docs/results/R-D-09.md`, 5/5) e emitido best-effort no
   `task_detect`; o operador AQL legado (vértices `bids` inexistentes) foi
   removido. **P6 satisfeita no volume real** (2026-08-21, R-D-09 § 4:
   66.485 sinais na partição dt=2026-08-21, 0 violações de gate/score/
   distância/coordenadas). Descritivo para a calibração: o gate de 100 km
   sinaliza ~1/3 dos contratos reais — calibração do limiar e exposição
   nos marts exigem PR-D-09b.
5. (aberto) **Conectar os operadores NLP.** `semantic_gap` e `detect_clone`
   (`src/capiba/detection/nlp_operators.py`), junto com `db/vectors.py` e
   `db/search.py`, seguem sem consumidor; `SignalType.SEMANTIC_GAP` não tem
   produtor. Rascunho de pré-registro do sinal `notice_clone` sobre o
   corpus do Querido Diário em `docs/preregistrations/PR-D-10.md`
   (2026-08-21 — aguarda revisão humana; `semantic_gap` fica
   explicitamente fora por falta de fonte).
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
   `dbt_run` após o destino bronze), com seleção de modelos
   (`select: [pod_usage_hourly, platform_cost_daily]`) — o run completo
   horário OOMKillava o Trino no mart `contract_red_flags`; o projeto
   inteiro segue com a `gold_detection` diária.
5. (aberto) **Janela de leitura pyiceberg × delete files Trino.** O pyarrow
   pinado (25.0.1) não decodifica os positional delete files escritos pelo
   Trino ("DecodeArrow of DictAccumulator") — detectado em 2026-08-21, quando
   o `detect` rodou silenciosamente sobre zero contratos. Mitigado para
   `contracts` (leitura via Trino em `lake.read_silver_contracts`), mas as
   silvers de dump (`companies`/`establishments`/`partners`) levam DELETE de
   partição a cada retry de normalize e seguem lidas via scan pyiceberg
   streaming (carga do grafo): entre um delete e o `optimize` semanal da
   `lake_maintenance`, a leitura quebra. Correções possíveis: rodar
   `optimize` ao fim do normalize, ou ler via Trino com paginação.
6. (feito) **Estabilidade do `detect` em volume real (2026-08-21).** A
   primeira run real da `gold_detection` exigiu uma cadeia de correções,
   todas commitadas no dia: pool `heavy_lake` (1 slot) serializando as
   tasks pesadas do lake + `AIRFLOW__CORE__PARALLELISM=8` (32 workers
   idle do LocalExecutor custavam ~5 GiB de baseline); leitura seletiva
   da silver `establishments` por CNPJ (`lake.read_establishments_for_cnpjs`,
   Trino `IN` batches) no `task_detect` **e** na persistência do grafo
   (`ingestion/persistence._default_supplier_geo` — o scan completo da
   RFB levava o worker a >7,7 GB e OOMKillava o pod nos backfills de
   maio); `lake.read_silver_contracts` via Trino (delete files
   ilegíveis); guarda de pares da colusão (`DETECTION_COLLUSION_MAX_PAIRS`);
   prefilter vetorizado exato no screening fuzzy (570M pares → 82 s);
   `lake.write_fraud_signals` **replace-por-partição** com
   `table.refresh()` entre o DELETE via Trino e o append (sem o refresh o
   Lakekeeper rejeita o commit — os 78.014 sinais da run das 04:29 se
   perderam); o run completo do `dbt_run` excluindo os marts horários
   (`_HOURLY_OWNED_MARTS` — commits concorrentes na mesma tabela Iceberg
   com o pipeline horário eram rejeitados); e evidência tolerante a
   falha por manifesto (`evidence/packages.py` — um
   `SignatureDoesNotMatch` abortava o loop inteiro de manifestos; a causa
   raiz é determinística, não transitória: metadados S3 não-ASCII vindos
   de `entity_id` sujo da fonte, ex.: siafi_code `´01` — ver item 7; na
   run seguinte 78.013/78.014 manifestos gravados, exatamente 1 erro).
   A run agendada `scheduled__2026-08-21T08:00` completou verde: 78.014
   sinais gravados (66.485 `anomalous_geography`, 866
   `sanctioned_supplier`, 5.428 `concentration`, 2.706
   `anomalous_duration`, 2.529 `anomalous_price`; colusão pulada pela
   guarda de pares, snapshot gravado como evidência). **Pendência de
   dados**: os backfills `daily_pncp` de 2026-05-06 a 2026-05-09 foram
   marcados failed na limpeza de TIs zumbis — esses dias podem ter ficado
   incompletos no silver; re-rodar o backfill da janela se o recorte
   temporal importar.
7. (aberto) **`entity_id` sujo quebra triagem e evidência (ERR 1221 /
   SignatureDoesNotMatch).** A fonte entrega ids inválidos (ex.:
   siafi_code `´01`, observado em 2026-08-21), e dois consumidores quebram
   neles: a chave de triagem `{entity_type}:{entity_id}:{signal_type}`
   (`db/triage.py`) vira `_key` ArangoDB sem sanitização — caractere
   ilegal levanta "illegal document key" e aborta o registro **inteiro**
   (na run de 2026-08-21 nenhum dos 78.014 sinais entrou na fila
   editorial; o erro foi engolido pelo catch do bloco de colusão, com
   mensagem enganosa "Collusion detection unavailable"); e os metadados
   S3 do pacote de evidências (`entity_cnpj`) com caracteres não-ASCII
   falham a assinatura do PUT (após o fix de tolerância por manifesto,
   resta 1 manifesto perdido por run — o do id sujo). A correção
   (sanitizar/hashear a `_key` preservando a chave legível em campo
   próprio, normalizar metadados para ASCII, + tolerância por sinal no
   registro) mexe na identidade estável usada por API e evidências —
   decidir a semântica antes de implementar; avaliar também validar o
   `siafi_code` na normalização do contrato.

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
   `sanctions`, modelo `Sanction`); o sinal "fornecedor sancionado" já foi
   pré-registrado e validado (PR-D-06/R-D-06, 5/5; screening fuzzy em
   PR-D-06b/R-D-06b — ver item editorial 5). No PNCP,
   `contratos/atualizacao` está feito: fonte `pncp_contract_updates`
   (`src/capiba/pipeline/registry.py`) e pipeline diário bronze-only
   `daily_pncp_updates` (ver item editorial 4). Seguem abertos `licitacoes` e
   `despesas/documentos`
   (Transparência) e `atas` e `contratacoes/publicacao` (PNCP), com
   pré-registro PR-D-* para os que virarem sinal.
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
   `test_db.py`; e `config.py` ganhou teste dedicado.

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
   (+ agregados por fornecedor/órgão). **Etapa real concluída em
   2026-08-21** (R-D-04 § 4): P6–P8 satisfeitas sobre 205.349 contratos
   (P6 exigiu rebuild do mart — staleness mid-backfill; nenhum contrato
   com CRI ≥ 0,5 no volume real, insumo para PR-D-04b). O `compute_cri`
   supervisionado segue sem uso (gap de ML supervisionado); proposta
   única real aguarda a fonte `contratacoes/propostas` do PNCP.
4. (em andamento) **Red flags de aditivos (O2).** Pré-registrado e
   validado no regime sintético (PR-D-05/R-D-05, 5/5): fonte PNCP
   `contratos/atualizacao` registrada (`pncp_contract_updates`, pipeline
   diário bronze-only `daily_pncp_updates`), semântica de referência em
   `detection/amendments.py` (aditivo de valor via `valorAcumulado` >
   `valorInicial`, aditivo de prazo via extensão de vigência — última
   observação soberana) e mart gold `contract_amendments` (+ agregados
   por fornecedor/órgão). **Etapa real executada em 2026-08-21**
   (R-D-05 § 4): P6 satisfeita; P7 refutada por arredondamento do
   descritor `value_ratio` (corrigido — precisão plena no módulo e no
   mart, cast double antes da divisão DECIMAL do Trino); **P8 refutada**
   (campo `valorAcumulado` computável em 23,54% < 50%) → forma corrigida
   com plano B (endpoint de termos por contrato, verificado ao vivo) em
   `docs/preregistrations/PR-D-05b.md`, aguardando revisão humana.
5. (em andamento) **Screening de sanções e PEPs (O3).** Match exato por
   CNPJ/CPF contra CEIS/CNEP validado (PR-D-06/R-D-06, 5/5): sinal
   `sanctioned_supplier` (`detection/screening.py`, score binário, vigência
   inclusiva na `signature_date`) integrado ao `task_detect` (best-effort
   sobre o silver `sanctions`). Match fuzzy por nome + documento mascarado
   validado (PR-D-06b/R-D-06b, 7/7): sinal separado `sanctioned_name_match`
   (`detection/screening_fuzzy.py` — veto documental, regime doc-assistido
   0,6/0,4 a 0,85, nome-only a 0,95; precisão 0,925 no OS Pairs nome-only),
   com a fonte **CEAF** (CPF mascarado na origem → coluna `masked_document`
   na silver `sanctions`) no pipeline `weekly_sanctions`. **P8 do PR-D-06b
   satisfeita por vacuidade no volume real** (2026-08-21, R-D-06b § 4: 0
   sinais `sanctioned_name_match` na partição dt=2026-08-21 — zero
   corroborado contra referência naive numa amostra real, não é artefato
   de implementação; os 866 sinais factuais `sanctioned_supplier` foram
   emitidos normalmente). PEPs/
   OpenSanctions (`yente` self-hosted) e CEAF no grafo (sancionado como
   sócio de fornecedor, via O5) ficam para PRs próprios.
6. (feito) **Esquema FollowTheMoney no grafo (O4).** Vocabulário FtM:
   vértices `companies` (Company) e `persons` (Person), arestas
   `ownership` ({persons,companies}→companies) e `directorship`
   (persons→companies) conforme a qualificação RFB
   (`cnpj.edge_kind_for_qualificacao`); sócio PJ vira Company com aresta
   ownership real (alimenta o `trace_ownership`); silver `partners`
   ampliado com CPF parcial e representante legal (Directorship);
   traversal "sócios de fornecedores de um órgão" em
   `GET /v1/graph/partners/{siafi}` e export FtM JSON básico em
   `GET /v1/graph/ftm/{cnpj}` (`db/ftm.py`). Merge
   suppliers↔companies fica para o O5 (entity resolution).
7. (em andamento) **Entity resolution (O5).** Matcher validado
   (`src/capiba/detection/entities.py`, bateria D-07 —
   `docs/results/R-D-07.md`): dedupe conservador de sócios PF (nome
   normalizado 0,6 + documento mascarado 0,3 + faixa etária 0,1, limiar
   0,85 — nome sozinho nunca mergea) e link determinístico
   supplier↔company (CNPJ 14d → `cnpj_basico`); aresta `same_as`
   (persons↔persons) no grafo via `resolve_entities` — integrada à carga
   do grafo (`persist_cnpj_entities`, limiar `DETECTION_ENTITY_THRESHOLD`,
   best-effort, sem colapsar vértices). No benchmark real OpenSanctions
   Pairs: precisão 1,00, revocação 0,025 (banda refutada — causa
   estrutural: 4,8% dos positivos com documento bilateral; recalibração
   confirmada em D-07b — `docs/results/R-D-07b.md`: banda [0,00–0,10]
   satisfeita em 3 amostras novas, precisão 1,00).
   Pendente: invariante P8 no grafo real (após o reload pós-backfill) e
   screening fuzzy de sanções (PR-D-06b).
8. (em andamento) **Diários oficiais municipais via Querido Diário (O7).** Source
   `querido_diario` no registry (crawler
   `src/capiba/ingestion/crawler_querido_diario.py`), fórmula nova
   `documents_collect` (crawl windowed + `download_<fonte>_texts` com
   skip-existing no retry + validação declarada `gazette_rules`, bronze-only)
   e pipeline diário `daily_querido_diario` do município-piloto Recife
   (IBGE 2611606): metadados no bronze (`raw_querido_diario`) + texto
   extraído de cada diário como arquivo bronze. **Primeira run real
   executada em 2026-08-20** (sucesso; janela de 19/08 legitimamente
   vazia — o diário de 18/08 é o mais recente raspado pelo QD; run com
   janela de 18/08 disparada para exercitar o download de textos).
   Pendente: alimentar os sinais de NLP com o corpus — rascunho de
   pré-registro `docs/preregistrations/PR-D-10.md` (`notice_clone`)
   aguardando revisão humana.
9. (em andamento) **TSE: doações × contratos (O8).** Pré-registrado e
   validado no regime sintético (PR-D-08/R-D-08): pipeline mensal
   `monthly_tse` (snapshot da prestação de contas, silvers
   `campaign_donations`/`candidacies`, parser `ingestion/tse.py`), sinal
   `political_connection` (`detection/political.py`, match exato por
   documento de doador de campanha de prefeito eleito que vira fornecedor
   do município na janela do mandato; emitido best-effort no `task_detect`)
   e mart gold `political_connections` (CPF mascarado na origem, chave
   `signal_id` sha256, já na allowlist da exportação pública —
   `pipeline/public_export.py`). **P8/volume real bloqueado por causa
   externa**: o CDN do TSE (Akamai) recusa os downloads com 403 a partir
   do IP de saída deste host (geo/bot restriction, verificado com e sem
   User-Agent de browser em 2026-08-21; o crawler já prevê a página de
   erro do CDN); as runs da `monthly_tse` falham com "No files
   downloaded". Destravar exige rede com egresso brasileiro ou espelho
   alternativo (decisão de PR-D-08b). Calibração dos placeholders exige
   PR-D-08b.
10. (implementado) **Saída pública para a comunidade (O11).** Marts gold
    baixáveis (CSV/Parquet) sem auth: post step `export_public_marts` da
    `gold_detection` exporta a allowlist LGPD (fail-closed) para o bucket
    `capiba-public` versionado por data; API pública
    (`/v1/public/marts`, download presignado 302, `/v1/public/methodology`
    gerado do dbt + specs YAML). Pendente de deploy: política de leitura
    pública do bucket (charts/values).
11. (implementado) **Alertas para jornalistas comunitários (O12).** Assinatura
    de pautas por código IBGE do município: coleção ArangoDB `subscriptions`
    (token opaco, só hash persistido), rotas públicas
    `/v1/subscriptions` (inscrição/confirmação/cancelamento) e disparo por
    e-mail somente na transição de triagem para `published`, com link para o
    pacote de evidências (O9).
12. (aberto) **Modo leve de onboarding.** Execução local sem k3s (sqlite/duckdb)
    para o jornalista solo ou a redação comunitária.
13. (aberto) **A fábrica de rótulos não produz (2026-08-21).** O mecanismo do
    item 1 existe e está íntegro (página `/triage` acessível via SSO, API
    `/v1/triage` sem auth, transições válidas), mas a coleção
    `signal_reviews` tem 816.405 documentos, **100% `pending_review`**,
    zero `reviewed_by` em três dias de produção (AQL de leitura,
    2026-08-21: `first_seen` 19/08 = 313.161, 20/08 = 491.314, 21/08 =
    11.930 — os 78.014 sinais da run de 21/08 entraram só em parte pelo
    ERR 1221 do item Alto 7). Causas identificadas:
    (a) **dominação de um signal_type sem eixo de prioridade** —
    `collusion_network` é 99,1% da fila (809.220 entradas, uma por par de
    fornecedores, chave `supplier:<cnpj1>+<cnpj2>:collusion_network`),
    todas com score binário 1.0 e `details` contendo apenas
    `{min_wins, suppliers}` (a anotação de compradores distintos da D-03b
    não é persistida): os 809 mil pares são indistinguíveis entre si na
    fila, e a guarda `DETECTION_COLLUSION_MAX_PAIRS` (1M) protege a
    memória do pod, não a capacidade editorial — abaixo dela, cada par
    vira uma entrada (`register_signals`, `db/triage.py`, não tem teto nem
    amostragem; a deduplicação por chave só impede duplicata);
    (b) **fila sem priorização e com varredura completa** —
    `list_reviews` (`db/triage.py:114-134`) lê a coleção INTEIRA para a
    memória do pod da API (`_all_reviews`), filtra e ordena em Python só
    por `last_seen` desc (sem ordenação por score, sem paginação por
    offset, limite máximo 1.000, default 100); a página `/triage` filtra
    apenas por status — sem filtro por signal_type nem por score, o
    revisor vê os 100 pares de colusão re-observados mais recentemente,
    e cada carga de página materializa 816 mil documentos (o portal já
    OOMKillou o pod da API com full scans — `api/portal.py:159`);
    (c) **alerta por score inoperante** — `NOTIFICATION_RECIPIENTS` tem
    default vazio (`config.py:329`), vazio no `.env.example` e sem wiring
    no chart → `notify_fraud_signals` é no-op; e mesmo configurado, os
    809 mil sinais de colusão têm score 1.0 ≥ `NOTIFICATION_ALERT_SCORE`
    (0,7), o que geraria UM e-mail com 809 mil sinais embutidos no
    metadata — o design do alerta também não escala com a dominação.
    Encaminhamento proposto (tarefa de engenharia, BDD/TDD normal — não é
    alegação empírica, dispensa pré-registro): fila priorizada com
    filtro/ordenação/paginação no AQL (SORT score DESC, OFFSET/LIMIT,
    filtro por signal_type também na página), teto diário de revisão por
    operador, amostragem declarada e determinística dos pares de colusão
    no registro (ex.: hash da chave) ou agregação por fornecedor em vez de
    por par (mudança de semântica do sinal fica com o PR-D-03c), e cap no
    payload do alerta (top-K por run). O destrave da fábrica de rótulos é
    pré-condição do ML supervisionado (item Alto 2, PR-D-11).

## Roadmap de longo prazo (declarado, fora da fase)

1. Fontes TSE (dados eleitorais) — entregues (pipeline `monthly_tse`, silvers
   `campaign_donations`/`candidacies`, ver item editorial 9); restam anos
   eleitorais adicionais e ampliação do recorte.
2. Dados privados via LGPD/DP, que exigem análise de base legal prévia.
3. Protocolos federados: differential privacy, federated learning, ZKP.
4. Governança: glossário de negócio, classificação de sensibilidade por coluna,
   laço automático de requests/limits.
5. Baterias pré-registradas de validação empírica para grafos, NLP e ML:
   grafos feitos (PR-D-02 aprovado, bateria D-02 executada com sucesso,
   `docs/results/R-D-02.md`); NLP com rascunho de PR (`PR-D-10`,
   `notice_clone`, aguardando revisão humana); ML segue sem PR.
