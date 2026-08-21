# Índice da documentação

> **Propósito:** índice único de toda a documentação do Capiba — o que cada
> documento cobre, para quem e quando consultar.
> **Quando consultar:** sempre que procurar documentação ou ao criar/alterar
> um doc (o índice é atualizado no mesmo commit).
> **Relacionados:** `README.md` (visão geral), `AGENTS.md` (convenções e
> regras duras).
> **Sincronizado com:** `docs/` — 2026-08-21.

## Documentos de `docs/`

| Arquivo | Propósito | Público | Quando consultar |
|---|---|---|---|
| `arquitetura.md` | Visão de alto nível: camadas, stack, deploy, SSO | todos | entender onde cada componente mora |
| `ingestao.md` | Arquitetura da ingestão: crawlers, normalizers, persistência | engenharia | criar/alterar fonte ou pipeline declarativo |
| `apis_fontes.md` | Referência das APIs públicas-fonte (endpoints, exigências, estado) | engenharia | adicionar fonte ou diagnosticar falha de ingestão |
| `operadores.md` | Catálogo dos operadores de detecção (scores, limiares, status) | engenharia/ciência | criar, calibrar ou conectar um sinal |
| `api.md` | Referência rota a rota da API REST `/v1/*` | engenharia/consumidores | consumir ou alterar endpoints |
| `portal.md` | Portal editorial: landing, fluxo SSO, triagem | engenharia/redação | alterar rotas, sessão ou templates do portal |
| `jornalismo_dados.md` | Processo editorial ponta a ponta apoiado na plataforma | redação | conectar sinais à apuração e publicação |
| `governanca.md` | Governança de dados na régua DAMA-DMBOK | engenharia/liderança | decisões de qualidade, catálogo, LGPD |
| `operacao.md` | Runbook de deploy e operação do cluster local | operadores | subir/parar/remover cluster, SSO, backups |
| `operacao_lake.md` | Idempotência, retry e memória do lake em detalhe | engenharia | alterar download, normalize ou destinos do lake |
| `ambiente_dev.md` | Ferramentas do ambiente do desenvolvedor (RTK, dsh) | desenvolvedores | configurar o ambiente local com agentes |
| `gaps.md` | Gaps técnicos de curto prazo, priorizados | todos | saber o que falta fazer agora |
| `oportunidades.md` | Backlog de médio prazo orientado pela missão editorial | todos | escolher evoluções de capacidade |

## Fora de `docs/`

- `charts/capiba/README.md` — referência e deploy do chart Helm.
- `dbt/README.md` — projeto dbt (marts gold e serving sobre Trino).
- `experiments/detect/README.md` — como rodar as baterias de detecção.
- `docs/preregistrations/README.md` — índice das baterias PR-D-* (doutrina
  de pré-registro).
- `docs/results/` — resultados R-D-* das baterias, inclusive os negativos.

## Roteamento por tarefa

- Vou mexer em ingestão de fonte X → `apis_fontes.md` + `ingestao.md`.
- Vou mexer em sinal de detecção → `operadores.md`.
- Vou mexer no lake/idempotência → `operacao_lake.md`.
- Vou deployar/operar → `operacao.md` + `charts/capiba/README.md`.
- Vou mexer em marts dbt → `dbt/README.md`.
- Vou rodar/propor experimento → `preregistrations/README.md` +
  `experiments/detect/README.md`.
- Vou mexer na API → `api.md`.
- Vou mexer no portal → `portal.md`.
- Processo editorial → `jornalismo_dados.md`.
- Governança → `governanca.md`.
- O que falta fazer → `gaps.md` (curto prazo) + `oportunidades.md` (médio).

## Regras de frescor

- Doc novo ou alterado entra neste índice **no mesmo commit**.
- Todo doc da raiz de `docs/` carrega o cabeçalho-padrão (blockquote com
  Propósito / Quando consultar / Relacionados / Sincronizado com).
- `docs/preregistrations/*.md` e `docs/results/*.md` são registros formais
  com formato próprio — fora do cabeçalho-padrão.
