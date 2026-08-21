# Oportunidades de evolução: jornalismo de dados comunitário

> **Propósito:** backlog de médio prazo orientado pela missão editorial,
> com ponto de partida no código e critério de aceitação por item.
> **Quando consultar:** para escolher evoluções de capacidade editorial e
> analítica após os gaps de curto prazo.
> **Relacionados:** `docs/gaps.md` (curto prazo), `docs/jornalismo_dados.md`
> (método editorial).
> **Sincronizado com:** pesquisa de 2026-08-18, revalidada em 2026-08-21.

Pesquisa de 2026-08-18. Este documento é o backlog de médio prazo do
Capiba orientado pela missão de produzir **jornalismo de dados a serviço
da comunidade** (método em `docs/jornalismo_dados.md`). Cada oportunidade
é dimensionada para **uma sessão de trabalho** (ou um ciclo curto de
sessões), com ponto de partida no código e critério de aceitação. Os gaps
técnicos de curto prazo seguem em `docs/gaps.md`; aqui moram as evoluções
de capacidade editorial e analítica.

## Síntese da pesquisa

A literatura e o ecossistema mostram um conjunto de práticas já
consolidadas, e é sobre elas que este backlog se apoia.

**Red flags de corrupção em compras públicas têm literatura própria.** O
Corruption Risk Index (CRI) de Fazekas & Kocsis é o padrão de referência:
proposta única (*single bid*), prazo curto de submissão, procedimento
restritivo, ausência de chamada pública, alta taxa de aditivos e
divergência entre valor adjudicado e valor final do contrato
([Fazekas & Kocsis, 2020](https://www.sciencedirect.com/science/article/abs/pii/S0176268021001166),
[guia RICG](https://ricg.org/wp-content/uploads/2023/01/Guide-to-identify-corruptions-risks-in-public-procurement-using-data-science.pdf),
[revisão de red flags](https://link.springer.com/article/10.1007/s11205-024-03331-w)).
O Capiba já emite o sinal `single_bid` e tem um `compute_cri` em
`src/capiba/detection/ml_models.py` sem uso fora dos testes: a maior
parte da matéria-prima do CRI já entra pelo PNCP.

**Screening contra listas de sanções e PEPs é commodity open source.** O
[OpenSanctions](https://github.com/orgs/opensanctions/repositories)
agrega mais de 400 listas no esquema
[FollowTheMoney](https://followthemoney.tech/) (FtM), com o motor de
matching `yente` self-hostable (MIT) e o benchmark de entity matching
[OpenSanctions Pairs](https://arxiv.org/pdf/2603.11051). No Brasil, as
listas equivalentes são CEIS, CNEP e CEAF do Portal da Transparência, e
as três já entram semanalmente pelo pipeline `weekly_sanctions`.

**Plataformas investigativas convergiram para o modelo "follow the
money".** O [Aleph (OCCRP)](https://knowledge.iadb.org/open-knowledge/code-development/open-source-solution/occrp-aleph)
organiza entidades (pessoas, empresas, vínculos) sobre FtM e prova que
entity resolution somada a grafos é o núcleo do cruzamento investigativo.
Atenção: o Aleph migrou para o Aleph Pro e deixou de ser open source; a
lição que fica é o modelo de dados, não a dependência.

**O Brasil tem infraestrutura cívica pronta para reuso.** O
[Querido Diário (OKBR)](https://docs.queridodiario.ok.org.br/) raspa e
abre diários oficiais municipais (reconhecido como
[Digital Public Good](https://www.digitalpublicgoods.net/r/querido-diario));
o [Brasil.IO](http://turicas.info/slides/brasil.io/qconsp2019/) e a Base
dos Dados mantêm datasets libertos; a
[Operação Serenata de Amor](https://ok.org.br/projetos/serenata-de-amor/)
provou o modelo de auditoria cidadã automatizada com valor público
mensurado
([análise acadêmica](https://www.redalyc.org/journal/1954/195470012004/html/)).

**Reprodutibilidade é requisito editorial, não opcional.** O
[Data Journalism Handbook](https://s3.eu-central-1.amazonaws.com/datajournalismcom/handbooks/The-Data-Journalism-Handbook-1.pdf)
e a literatura de verificação convergem: quem repete o procedimento sobre
os mesmos dados deve chegar à mesma conclusão, e o diário de dados com a
cópia original intocada é a base da defesa da história.

## Horizonte 1: detecção com lastro acadêmico

O primeiro horizonte dá lastro publicável ao que a plataforma já mede:
trocar intuição por índices que a literatura validou, para que cada
número se sustente em revisão por pares e em juízo.

### O1. Índice de Risco de Corrupção (CRI) por contratação

Implementar o CRI de Fazekas & Kocsis como mart gold: compor red flags
binárias por contratação (proposta única, prazo de submissão curto,
modalidade restritiva/dispensa, razão valor final/valor adjudicado,
aditivos) em um score 0–1, agregado por órgão e por fornecedor.

**Por quê**: dá lastro publicável ao score, pois cada red flag tem
validação empírica na literatura e a composição é defensável em revisão
jurídica ("não torturar os dados"). **Onde**: `compute_cri`
(`src/capiba/detection/ml_models.py`) existe sem uso fora dos testes;
novo mart gold de red flags (entregue como
`dbt/models/marts/contract_red_flags.sql`, + agregados
`red_flags_by_agency.sql`/`red_flags_by_supplier.sql`); post step
`detect`. **Critério de aceitação**: pré-registro `PR-D-*` com predição
falsificável (ex.: órgãos no top decil do CRI concentram achados de
auditoria); mart publicado; resultado em `docs/results/R-D-*.md`.
**Sessões**: 2–3 (pré-registro, implementação, bateria).

### O2. Red flags de aditivos e reequilíbrio

Coletar aditivos e alterações contratuais (PNCP `contratos/atualizacao`,
já listado em `docs/gaps.md`) e derivar as red flags "alta taxa de
aditivos" e "contrato modificado após adjudicação".

**Onde**: fonte nova na spec `dags/pipelines/daily_pncp_updates.yaml`
(entregue como `pncp_contract_updates`, bronze-only); colunas no silver;
red flags no mart `contract_amendments` (O1). **Critério de
aceitação**: percentual de contratos com aditivo por órgão fornecedor
disponível no gold, com teste dbt.

### O3. Screening de fornecedores contra sanções e PEPs

**Entregue** (PR-D-06/PR-D-06b, ver item editorial 5 do `docs/gaps.md`).
Cruzar fornecedores e sócios, já normalizados no silver da Receita
(`companies`/`partners`) e carregados no grafo pelo
`monthly_federal_revenue`, com as listas de sanções e, opcionalmente, com
o OpenSanctions self-hosted (`yente`).

**Por quê**: "fornecedor sancionado contratando com o poder público" é a
história mais direta e de maior interesse público do domínio. **Onde**:
as fontes `ceis`/`cnep`/`ceaf` (analisadas em `docs/apis_fontes.md`) já
entram pelo pipeline `weekly_sanctions` e deságuam na tabela silver
`sanctions`; os operadores de screening vivem em `detection/screening.py`
(match exato) e `detection/screening_fuzzy.py` (fuzzy com veto
documental), com os sinais `SignalType.SANCTIONED_SUPPLIER` e
`SANCTIONED_NAME_MATCH` emitidos pelo `task_detect`. **Critério
de aceitação** (cumprido): sinal emitido quando CNPJ ou sócio casa com
lista vigente; falso positivo mitigado por match em CNPJ (exato) antes de
match em nome (fuzzy com limiar calibrado e pré-registrado). Restam para
PRs próprios: PEPs/OpenSanctions (`yente`) e CEAF no grafo (sancionado
como sócio de fornecedor, via O5).

## Horizonte 2: modelo "follow the money"

O segundo horizonte dá ao grafo o vocabulário e a memória do jornalismo
investigativo: quem é quem, e de quem é o quê.

### O4. Esquema de entidades FollowTheMoney no grafo

Adotar o vocabulário FtM (Person, Company, Ownership, Directorship,
Contract) como convenção de vértices e arestas do ArangoDB.

**Por quê**: interoperabilidade com o ecossistema investigativo
(OpenSanctions, export para FtM JSON) e vocabulário pronto para vínculos
societários, dando forma canônica ao que o `trace_ownership` já percorre.
**Onde**: `src/capiba/db/` (coleções), carga no destino
`arangodb_graph`. **Critério de aceitação**: empresas e sócios da
Receita carregados como FtM; consulta "sócios de fornecedores de um
órgão" respondida por traversal.

### O5. Entity resolution de fornecedores e sócios

Deduplicar entidades entre bases (CNPJ × nome × sócio) com matching
calibrado, usando o benchmark OpenSanctions Pairs como referência de
avaliação.

**Por quê**: sem resolução de entidade, todo cruzamento downstream (O3,
O4, grafos de colusão) infla falsos negativos. **Onde**: módulo novo
`src/capiba/detection/entities.py`; experimento pré-registrado com
precisão e revocação medidas. **Critério de aceitação**: métricas
publicadas em `R-D-*`; merges só entram no grafo acima do limiar
pré-registrado.

### O6. Operadores de grafo em produção

**Entregue.** O `detect_collusion` já está em produção: o `task_detect`
emite o sinal `collusion_network` por par de fornecedores (limiar
`DETECTION_COLLUSION_MIN_WINS`, best-effort), e o `trace_ownership`
responde na API em `GET /v1/graph/ownership/{cnpj}`. A trinca foi
completada: o `anomalous_geography` ganhou fonte de
latitude/longitude (silver `municipalities` + referência vendored em
`src/capiba/ingestion/reference/municipios.csv`, enriquecimento via
`ingestion/geography.py`) e está em produção (`detection/geography.py`,
validado pela bateria D-09, emitido best-effort no `task_detect`), e as
evidências dos sinais de grafo são gravadas via `evidence/` (pacote
`graph_batch` do `collusion_network` em `evidence/packages.py`).

**Critério de aceitação** (cumprido, com adaptação): em vez de um post
step `detect_graph` separado, o `task_detect` único cobre todos os
sinais; sinais com as evidências gravadas via `evidence/`.

## Horizonte 3: fontes para o território

O terceiro horizonte desce a plataforma até o município, onde a
transparência é mais fraca e a pauta vive mais perto das pessoas.

### O7. Diários oficiais municipais via Querido Diário

**Entregue** (pendente só a primeira run real no cluster, conforme
`docs/gaps.md`). Ingerir diários oficiais pela API/toolkit do Querido
Diário (OKBR, MIT) como fonte declarativa nova.

**Por quê**: leva a plataforma ao nível municipal, onde o jornalismo
comunitário vive e onde a transparência é mais fraca. **Onde**: source
`querido_diario` no registry (fórmula `documents_collect`) e spec
`dags/pipelines/daily_querido_diario.yaml` do município-piloto Recife
(IBGE 2611606); o NLP (`semantic_gap`,
`detect_clone`) ganha matéria-prima textual. **Critério de aceitação**
(cumprido): pipeline diário de um município-piloto persistindo no bronze
com validação declarada (`gazette_rules`).

### O8. Dados eleitorais TSE: doações × contratos

**Entregue** (P8/volume real pendente, conforme item editorial 9 do
`docs/gaps.md`). Fonte TSE (já no roadmap) com o cruzamento clássico:
doadores de campanha que viram fornecedores do ente apoiado.

**Por quê**: é o "indicador de conexões políticas" do CRI estendido e uma
das pautas mais publicáveis do jornalismo de dados brasileiro. **Onde**:
spec `monthly_tse.yaml` (dump periódico); cruzamento no gold via dbt;
sinal `political_connection`. **Critério de aceitação** (cumprido no
regime sintético, PR-D-08/R-D-08): pré-registro com
critério de refutação (doação lícita não é indício por si; o sinal exige
coincidência temporal e concentração); mart `political_connections`
publicado.

## Horizonte 4: publicação e verificação comunitária

O último horizonte fecha o ciclo editorial: verificar antes de publicar,
publicar com as provas na mesa e devolver os dados à comunidade que os
financiou.

### O9. Pacote de evidências reproduzível por sinal

**Entregue.** Cada sinal publicado carrega um pacote: query/agregação que
o gerou, janela temporal, versão do código (SHA do artefato), cópia das
linhas fonte e hash. É o "diário de dados" automatizado.

**Por quê**: é o que permite à segunda pessoa reproduzir a análise antes
da publicação, e a defesa jurídica depois dela. **Onde**: a vertical
`evidence/` guarda e serve arquivos pelo router `/v1/evidence`; o
`task_detect` grava os pacotes best-effort no `EvidenceStorage`
(`evidence/packages.py`) e o endpoint `GET /v1/signals/{key}/evidence`
(`api/routers/signals.py`) lista os pacotes do sinal. **Critério de
aceitação** (cumprido): dado um
sinal, um terceiro executa o pacote e obtém o mesmo resultado (teste
BDD).

### O10. Triagem editorial de sinais

Estado editorial nos sinais (`pending_review` → `confirmed` /
`rejected` / `published`), com motivo obrigatório no descarte.

**Por quê**: formaliza a etapa "confirmar" do método e gera o dataset de
feedback que falta para o ML supervisionado (`train_rf`, gaps.md), pois
cada revisão humana vira rótulo. **Onde**: coleção no ArangoDB e rotas da
API e portal. **Critério de aceitação**: nenhum sinal sai como
"confirmado" sem revisor; relatório de precisão por operador derivado dos
rótulos.

### O11. Saída pública para a comunidade

Downloads abertos (CSV/Parquet) dos marts gold e documento de
metodologia gerado automaticamente a partir do dbt docs e das specs YAML.

**Por quê**: "disponibilizar os dados" é parte do checklist de
publicação; a metodologia aberta é o que diferencia transparência de
caixa-preta, e convida a comunidade a auditar a plataforma. **Onde**:
camada de serving e rotas públicas read-only na API; consolidação dos
docs de metodologia. **Critério de aceitação**: mart gold baixável sem
autenticação, com metodologia anexa e classificação LGPD conferida (sem
dado pessoal desnecessário).

### O12. Alertas para jornalistas comunitários

A `notification/` já está ativa, despachando alertas por e-mail a partir
do `task_detect` e da validação do pipeline; este item a estende com
assinatura de pautas: um veículo comunitário assina "sinais novos do
município X" e recebe o alerta com link para o pacote de evidências (O9).

**Depende de**: O9/O10 para não alertar ruído. **Critério de
aceitação**: despacho end-to-end com teste de integração; preferências de
assinatura por município/órgão.

## Ordem sugerida

A ordem abaixo é a registrada na pesquisa de 2026-08-18; hoje os itens
O1–O12 estão entregues ou em andamento (status detalhado em
`docs/gaps.md`), e o que resta são os desdobramentos anotados nos itens
(`semantic_gap` sem fonte, CEAF no grafo) e o modo leve de onboarding.

1. **O9 + O10** (verificação e triagem): transformam sinais em produto
   editorial e criam o laço de rótulos humanos.
2. **O1 + O2** (CRI): maior ganho de credibilidade analítica com a
   matéria-prima que já entra hoje.
3. **O4 + O3**: o eixo "follow the money", agora que a Receita já despeja
   empresas e sócios no grafo.
4. **O7** (municípios): a expansão territorial para o jornalismo
   comunitário.
5. **O5, O6, O8, O11, O12**: conforme as frentes anteriores amadurecem.

Toda detecção nova entra por pré-registro (`docs/preregistrations/`),
inclusive as com lastro na literatura: a doutrina é o que mantém a
plataforma do lado certo da linha entre evidência e tortura de dados.
