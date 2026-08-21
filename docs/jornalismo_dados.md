# Jornalismo de Dados no Capiba

> **Propósito:** descrever o processo editorial ponta a ponta — de obter a
> publicar — e como ele se apoia nos componentes da plataforma.
> **Quando consultar:** ao conectar um sinal de risco à apuração humana e
> à publicação de interesse público.
> **Relacionados:** `docs/api.md` (triagem e evidência),
> `docs/oportunidades.md` (backlog editorial), `docs/governanca.md`.
> **Sincronizado com:** `src/capiba/db/triage.py` + `src/capiba/api/` —
> 2026-08-21.

O Capiba existe para produzir **jornalismo de dados a serviço da
comunidade**: transformar bases abertas em investigações de interesse
público, com método reproduzível, verificação rigorosa e publicação que o
cidadão consegue entender. Este documento descreve como o processo
editorial do jornalismo de dados, de obter a publicar, se apoia nos
componentes que já existem na plataforma.

A plataforma não substitui a reportagem: ela organiza a parte
computacional do método (coleta, cruzamento, detecção de padrões e
anomalias) para que a apuração humana, fontes, campo, contexto,
narrativa, aconteça sobre evidências sólidas. O componente humano e o
interesse público são o centro: um sinal de risco só vira história quando
responde por que o público deveria se importar, que problema sistêmico
revela e quem é afetado por ele.

## 1. Obter os dados

A coleta é declarativa: cada fonte pública é uma spec YAML em
`dags/pipelines/*.yaml`, com fontes, janela temporal, fórmula, validações
e destinos, executada pelo runner (`src/capiba/pipeline/runner.py`) sem
código Python adicional. Hoje cobrimos os diários de contratações (PNCP,
pipeline `daily_pncp`, e Portal da Transparência, pipeline
`monthly_transparency`, mais as atualizações incrementais do PNCP em
`daily_pncp_updates`), o dump CNPJ da Receita Federal (pipeline
`monthly_federal_revenue`), as listas de sanções CEIS/CNEP/CEAF do
Portal da Transparência (pipeline `weekly_sanctions`), os diários
oficiais de Recife via Querido Diário/OKBR (pipeline
`daily_querido_diario`) e a prestação de contas eleitorais do TSE
(pipeline `monthly_tse`).

A coleta obedece a três princípios editoriais. O primeiro é preferir
dados granulares a agregados: a análise sob todos os ângulos exige o
registro, não a estatística, e quando só há estatística publicada os
dados por trás dela podem ser solicitados via LAI. O segundo é respeitar
termos de uso e legislação: as fontes seguem os termos dos portais (uso
não-comercial de dados públicos, com atribuição), conforme a régua
regulatória de `docs/governanca.md`. O terceiro é preservar o original:
todo payload bruto fica em `<fonte>/dt=YYYY-MM-DD/` no bucket bronze
antes de qualquer transformação, uma cópia de auditoria que permite
rastrear qualquer erro até a origem e sustenta contestação independente.

## 2. Compreender os dados

Antes de analisar, entenda a fonte como se entende uma fonte humana.
Pergunte quem produziu os dados e com qual credibilidade: a spec YAML
registra a fonte e sua natureza, e o schema unificado `Contract`
(pydantic) declara o que cada campo representa. Pergunte como foram
coletados: a documentação de cada fonte é analisada em
`docs/apis_fontes.md`, e o profiling estatístico
(`src/capiba/quality/profiling.py`) revela distribuições, nulos e faixas
antes de qualquer conclusão. Pergunte o que está faltando: as lacunas de
uma base são preenchidas por cruzamento com outras (contratos × CNPJ ×
órgãos), e o roadmap de fontes futuras — dados privados via LGPD/DP —
está em `docs/arquitetura.md`.

## 3. Verificar os dados

Dados não verificados deixam a história por um fio. A plataforma
operacionaliza a verificação em camadas que se apoiam umas nas outras.
As regras de validação (`src/capiba/quality/validators.py`, ruleset
`contract_rules`) aplicam schema e regras de negócio com severidade a
todo pipeline que declare `validate:` na spec. O profiling e o monitor de
drift (`src/capiba/quality/monitor.py`) guardam baselines por coluna e
detectam mudanças de distribuição entre janelas, fazendo dados
desatualizados ou inconsistentes aparecerem antes de contaminar a
análise. O mart `data_quality_daily` consolida o quadro diário para
revisão humana: a verificação automática aponta, a decisão editorial é
de gente. E o próprio cruzamento verifica: confrontar bases
independentes entre si é uma forma de checagem, porque a discrepância
entre o que o dado diz e o que outra base, ou a realidade de campo,
mostra é frequentemente a própria história.

## 4. Documentar e proteger os dados

Reprodutibilidade é requisito editorial: editores, verificadores e
advogados podem questionar cada número. A documentação é viva: o
catálogo dbt (`make dbt-docs`) documenta modelos e colunas, e o Marquez
registra a linhagem ponta a ponta (fonte → bronze → silver → gold →
grafo/serving) derivada da própria spec, de modo que a metodologia é um
artefato versionado, não um relato de memória. Os relatórios de run no
gold (`reports/<pipeline>/dt=YYYY-MM-DD/`, p.ex. `reports/daily_pncp/`)
e a tabela `platform_metrics` funcionam como diário de dados,
registrando o que cada execução fez, passo a passo. Cada sinal de
fraude ganha um pacote de evidências reproduzível
(`src/capiba/evidence/packages.py`), servido por
`GET /v1/signals/{key}/evidence`, e o `reproduce_signal` reexecuta a
detecção sobre o pacote para conferir o score, a resposta
computacional ao "questionar cada número". O acesso é controlado por
sensibilidade: SSO Keycloak em todas as UIs e usuários com escopo por
bucket no MinIO, compartilhando
dados apenas com quem precisa deles, com a classificação LGPD explícita
por fonte (ver `docs/governanca.md`).

## 5. Analisar em busca de padrões e anomalias

"Entrevistar os dados" aqui significa operadores de detecção
(`src/capiba/detection/`, estatísticos, ML, grafos e NLP) executados
como post step `detect` dos pipelines, gravando sinais na tabela gold
`fraud_signals`. Os operadores estão catalogados em `docs/operadores.md`.

Dois compromissos nos protegem de torturar os dados até confessarem. O
primeiro é o pré-registro de experimentos: todo novo sinal nasce de uma
predição numérica falsificável, com critérios de sucesso **e de
refutação** (`docs/preregistrations/PR-D-*.md`), antes de qualquer
execução, e os resultados, inclusive os negativos, são publicados em
`docs/results/R-D-*.md`. A hipótese vem antes da análise, não depois. O
segundo é o processo autorreferencial: fórmulas e código versionados,
seeds declaradas nos experimentos, nada de copiar e colar. Qualquer
membro da equipe, ou de fora dela, reproduz o resultado.

## 6. Confirmar as descobertas

Nenhum sinal vira publicação sem confirmação. A revisão humana vem
primeiro: o revisor de experimentos e o data steward da fonte (papéis em
`docs/governanca.md`) validam sinais contra leis e regulamentações
vigentes e contra a qualidade da fonte no período. Esse trabalho é
operacionalizado pela triagem de sinais: cada sinal novo entra como
`pending_review` na coleção `signal_reviews` (`src/capiba/db/triage.py`)
e transita para `confirmed`, `rejected` ou `published`, sempre com
revisor identificado, pela API `/v1/triage` ou pela página `/triage` do
portal; o relatório de precisão por operador vira rótulo para o ML.
Depois vem a reportagem adicional: a análise indica *onde* apurar, e a
visita ao local, a fonte humana e o documento público confirmam ou
derrubam o
sinal; o direito de resposta às pessoas e entidades citadas precede
qualquer publicação. Ao longo do caminho, as perguntas de validação
editorial orientam o julgamento: o sinal expõe irregularidade real? Há
problema de validade nos dados? Há novidade? Esclarece um problema
sistêmico? É um outlier que merece história própria?

## 7. Publicar para a comunidade

A publicação é pensada para o público, não para o especialista. O
consumo acontece em camadas: o portal capiba-dashboard (SSO) e a API de
sinais (`docs/api.md`) para acesso programático, os dashboards Grafana
sobre os marts gold para exploração visual, e os modelos de serving no
PostgreSQL DWH para consulta de baixa latência. Dados e metodologia são
abertos: os marts gold e o catálogo dbt docs funcionam como o documento
de metodologia público, qualquer pessoa pode ver de onde veio cada
número e como foi calculado, e a cópia de auditoria no bronze permite
verificação independente. A camada pública é concreta: os marts
liberados são exportados para o bucket `capiba-public`
(`src/capiba/pipeline/public_export.py`, allowlist fail-closed) e
servidos sem autenticação por `GET /v1/public/marts` e
`GET /v1/public/marts/{name}/{csv|parquet}`, com a metodologia em
`GET /v1/public/methodology`; quem quer acompanhar um município assina
alertas por e-mail (`/v1/subscriptions`), disparados quando um sinal do
município é publicado na triagem. E a narrativa vem antes do dado bruto: o
público raramente se interessa por tabelas, a história precisa de
ângulo, personagens e contexto. A plataforma entrega as evidências e as
visualizações; a narrativa é trabalho editorial.

Antes de ir ao ar, o checklist de publicação:

1. Ângulo definido.
2. Análise reproduzida por segunda pessoa.
3. Visualizações conferidas contra os dados.
4. Revisão jurídica.
5. Metodologia documentada e, quando possível, dados disponibilizados
   para download.

## Comunidades de dados

No limite, este método deixa de ser de uma redação só: **comunidades de
dados** são empresas, clientes e instituições públicas compartilhando
dados para formar inteligência soberana em território nacional (ver
README.md, "Objetivo"). O jornalismo de dados é a prática que dá sentido
a esse compartilhamento: cada instituição contribui sabendo o que
entrou, de onde veio, como foi transformado e quem consome, e a
comunidade recebe de volta investigações de interesse público, não
vigilância.

## Referências

A documentação vizinha completa o quadro: `docs/governanca.md` traz
papéis, régua regulatória LGPD/LAI e federação; `docs/oportunidades.md`
guarda o backlog de evolução orientado a jornalismo comunitário;
`docs/ingestao.md` detalha o framework declarativo de ingestão;
`docs/operadores.md` cataloga os operadores de detecção; `docs/api.md`
especifica a API de sinais; e `docs/preregistrations/README.md` explica
a doutrina de pré-registro de experimentos.
