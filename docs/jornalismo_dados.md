# Jornalismo de Dados — Capiba

O Capiba existe para produzir **jornalismo de dados a serviço da
comunidade**: transformar bases abertas em investigações de interesse
público, com método reproduzível, verificação rigorosa e publicação que o
cidadão consegue entender. Este documento descreve como o processo
editorial do jornalismo de dados — obter, compreender, verificar,
documentar, analisar, confirmar e publicar — se apoia nos componentes que
já existem na plataforma.

A plataforma não substitui a reportagem: ela organiza a parte
computacional do método (coleta, cruzamento, detecção de padrões e
anomalias) para que a apuração humana — fontes, campo, contexto,
narrativa — aconteça sobre evidências sólidas. O componente humano e o
interesse público são o centro: um sinal de risco só vira história quando
responde por que o público deveria se importar, que problema sistêmico
revela e quem é afetado por ele.

## 1. Obter os dados

A coleta é declarativa: cada fonte pública é uma spec YAML em
`dags/pipelines/*.yaml` (fontes, janela temporal, fórmula, validações,
destinos), executada pelo runner (`src/capiba/pipeline/runner.py`) sem
código Python adicional. Hoje cobrimos diários de contratações (PNCP,
Portal da Transparência, pipeline `daily_ingestion`) e o dump CNPJ da
Receita Federal (pipeline `monthly_federal_revenue`).

Princípios editoriais da coleta:

- **Preferir dados granulares a agregados** — a análise sob todos os
  ângulos exige o registro, não a estatística; quando só há estatística
  publicada, os dados por trás dela podem ser solicitados via LAI.
- **Respeitar termos de uso e legislação** — as fontes seguem os termos
  dos portais (uso não-comercial de dados públicos, com atribuição); ver
  `docs/governanca.md`, "Régua regulatória".
- **Preservar o original** — todo payload bruto fica em
  `<fonte>/dt=YYYY-MM-DD/` no bucket bronze antes de qualquer
  transformação: a cópia de auditoria permite rastrear qualquer erro até
  a origem e sustenta contestação independente.

## 2. Compreender os dados

Antes de analisar, entenda a fonte como se entende uma fonte humana:

- **Quem produziu os dados** e com qual credibilidade — a spec YAML
  registra a fonte e sua natureza; o schema unificado `Contract`
  (pydantic) declara o que cada campo representa.
- **Como foram coletados** — a documentação da fonte é analisada em
  `docs/apis_fontes.md`; o profiling estatístico
  (`src/capiba/quality/profiling.py`) revela distribuições, nulos e
  faixas antes de qualquer conclusão.
- **O que está faltando** — lacunas de uma base são preenchidas por
  cruzamento com outras (contratos × CNPJ × órgãos); o roadmap de fontes
  (TSE, dados privados via LGPD/DP) está em `docs/arquitetura.md`.

## 3. Verificar os dados

Dados não verificados deixam a história por um fio. A plataforma
operacionaliza a verificação em camadas:

- **Regras de validação** (`src/capiba/quality/validators.py`, ruleset
  `contract_rules`): schema e regras de negócio com severidade, aplicadas
  a todo pipeline que declare `validate:` na spec.
- **Profiling e monitor de drift** (`src/capiba/quality/monitor.py`):
  baselines por coluna para detectar mudanças de distribuição entre
  janelas — dados desatualizados ou inconsistentes aparecem antes de
  contaminar a análise.
- **Mart de qualidade** (`data_quality_daily`): consolidado diário
  revisado por um humano — a verificação automática aponta, a decisão
  editorial é de gente.
- **Cruzamento como verificação**: confrontar bases independentes entre
  si é, em si, uma forma de checagem — a discrepância entre o que o dado
  diz e o que outra base (ou a realidade de campo) mostra é,
  frequentemente, a própria história.

## 4. Documentar e proteger os dados

Reprodutibilidade é requisito editorial: editores, verificadores e
advogados podem questionar cada número.

- **Documentação viva**: o catálogo dbt (`make dbt-docs`) documenta
  modelos e colunas; o Marquez registra a linhagem ponta a ponta
  (fonte → bronze → silver → gold → grafo/serving) derivada da própria
  spec — a metodologia é um artefato versionado, não um relato de
  memória.
- **Diário de dados**: os relatórios de run no gold
  (`reports/daily_ingestion/`) e a tabela `platform_metrics` registram o
  que cada execução fez, passo a passo.
- **Controle de acesso por sensibilidade**: SSO Keycloak em todas as UIs,
  usuários com escopo por bucket no MinIO — compartilhar dados apenas com
  quem precisa deles, com a classificação LGPD explícita por fonte
  (ver `docs/governanca.md`).

## 5. Analisar em busca de padrões e anomalias

"Entrevistar os dados" aqui significa operadores de detecção
(`src/capiba/detection/` — estatísticos, ML, grafos, NLP) executados
como post step `detect` dos pipelines, gravando sinais na tabela gold
`fraud_signals`. Os operadores estão catalogados em `docs/operadores.md`.

Dois compromissos contra "torturar os dados até confessarem":

- **Pré-registro de experimentos**: todo novo sinal nasce de uma predição
  numérica falsificável com critérios de sucesso **e de refutação**
  (`docs/preregistrations/PR-D-*.md`), antes de qualquer execução;
  resultados — inclusive negativos — são publicados em
  `docs/results/R-D-*.md`. A hipótese vem antes da análise, não depois.
- **Processos autorreferenciais**: fórmulas e código versionados, seeds
  declaradas nos experimentos, nada de copiar-e-colar — qualquer membro
  da equipe (ou de fora dela) reproduz o resultado.

## 6. Confirmar as descobertas

Nenhum sinal vira publicação sem confirmação:

- **Revisão humana**: o revisor de experimentos e o data steward da fonte
  (papéis em `docs/governanca.md`) validam sinais contra leis e
  regulamentações vigentes e contra a qualidade da fonte no período.
- **Reportagem adicional**: a análise indica *onde* apurar — a visita ao
  local, a fonte humana e o documento público confirmam ou derrubam o
  sinal. O direito de resposta às pessoas e entidades citadas precede
  qualquer publicação.
- **Perguntas de validação editorial**: o sinal expõe irregularidade
  real? Há problema de validade nos dados? Há novidade? Esclarece um
  problema sistêmico? É um outlier que merece história própria?

## 7. Publicar para a comunidade

A publicação é pensada para o público, não para o especialista:

- **Camadas de consumo**: portal capiba-dashboard (SSO) e API de sinais
  (`docs/api.md`) para acesso programático; dashboards Grafana sobre os
  marts gold para exploração visual; modelos de serving no PostgreSQL DWH
  para consulta de baixa latência.
- **Dados e metodologia abertos**: os marts gold e o catálogo dbt docs
  funcionam como o "documento de metodologia" público — qualquer pessoa
  pode ver de onde veio cada número e como foi calculado, e a cópia de
  auditoria no bronze permite verificação independente.
- **Narrativa antes do dado bruto**: o público raramente se interessa por
  tabelas — a história precisa de ângulo, personagens e contexto. A
  plataforma entrega as evidências e as visualizações; a narrativa é
  trabalho editorial.
- **Checklist de publicação**: ângulo definido, análise reproduzida por
  segunda pessoa, visualizações conferidas contra os dados, revisão
  jurídica, metodologia documentada e, quando possível, dados
  disponibilizados para download.

## Comunidades de dados

No limite, este método deixa de ser de uma redação só: **comunidades de
dados** — empresas, clientes e instituições públicas compartilhando dados
para formar inteligência soberana em território nacional (ver README.md,
"Objetivo"). O jornalismo de dados é a prática que dá sentido a esse
compartilhamento: cada instituição contribui sabendo o que entrou, de
onde veio, como foi transformado e quem consome — e a comunidade recebe
de volta investigações de interesse público, não vigilância.

## Referências

- `docs/governanca.md` — papéis, régua regulatória LGPD/LAI e federação.
- `docs/oportunidades.md` — backlog de evolução orientado a jornalismo comunitário.
- `docs/ingestao.md` — framework declarativo de ingestão.
- `docs/operadores.md` — catálogo de operadores de detecção.
- `docs/api.md` — especificação da API de sinais.
- `docs/preregistrations/README.md` — doutrina de pré-registro de experimentos.
