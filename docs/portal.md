# Portal editorial (capiba-dashboard)

> **Propósito:** documentar o portal servido pela própria API
> (`src/capiba/api/portal.py`): landing page com links e estatísticas, o
> fluxo SSO OIDC contra o Keycloak e a página de triagem editorial.
> **Quando consultar:** ao alterar rotas do portal, o fluxo de
> autenticação/sessão, os templates ou a página de triagem.
> **Relacionados:** `docs/api.md` (rotas REST `/v1/*`),
> `docs/jornalismo_dados.md` (fluxo editorial completo do jornalista).
> **Sincronizado com:** `src/capiba/api/` — 2026-08-21.

## Visão geral

O portal é a UI humana da plataforma: uma aplicação Jinja2
(`src/capiba/api/templates/`, estáticos em `api/static/`, montados em
`/static`) embutida na mesma FastAPI que serve o REST. Ele atende dois
públicos: o operador (landing page com links para as UIs do cluster e
estatísticas do lake) e o revisor editorial (página de triagem dos sinais).
O jornalista comunitário **não usa o portal** — ele entra pelas rotas
públicas (`/v1/public/*`, `/v1/subscriptions`), no fim do fluxo editorial:
só recebe o que um revisor publicou. O fluxo completo (detecção → triagem
→ publicação → alerta ao jornalista) está em `docs/jornalismo_dados.md`.

## Rotas

| Rota | Método | Papel |
|---|---|---|
| `/` | GET | Landing page: links para as UIs do cluster (`SERVICES` — API Docs, Grafana, Airflow, Lakekeeper, MinIO Console, Marquez, Trino, Keycloak, Headlamp) e estatísticas cross-service do lake. Com SSO habilitado e sem sessão, redireciona `302` para `/auth/login` |
| `/auth/login` | GET | Inicia o fluxo OIDC (redirect ao Keycloak); com SSO desabilitado, no-op de volta a `/` |
| `/auth/callback` | GET | Callback OIDC: valida o token, grava o `userinfo` na sessão, redireciona a `/` |
| `/auth/logout` | GET | Limpa a sessão e redireciona a `/` |
| `/triage` | GET | Página de triagem editorial (ver abaixo); mesma guarda de SSO da landing |
| `/triage/review` | POST | Aplica a transição editorial vinda do formulário da página (ver abaixo) |

## Autenticação SSO (Keycloak OIDC)

Fluxo authorization code via authlib (`OAuth` Starlette), com registro do
client `keycloak` apenas quando `SSO_ENABLED` e `KEYCLOAK_ISSUER` estão
configurados (`register_keycloak`):

- **Metadata backchannel**: o OIDC discovery é buscado pelo issuer
  **interno HTTP** (`KEYCLOAK_ISSUER`, plain HTTP/8088); o endpoint de
  autorização é reescrito para o ingress **público HTTPS**
  (`KEYCLOAK_PUBLIC_ISSUER`, `https://keycloak.capiba.local:8443`) antes do
  redirect do browser (`_public_authorization_endpoint`). Token/JWKS seguem
  por HTTPS/8443 com a CA `capiba-tls` montada (`SSL_CERT_FILE`).
- **Callback**: como o authorization endpoint é o público, o `iss` do
  id_token é o issuer público — o `/auth/callback` aceita explicitamente
  `KEYCLOAK_PUBLIC_ISSUER` na validação (`claims_options`), apesar de a
  metadata ter sido lida pelo issuer interno.
- **Sessão**: cookie assinado via `SessionMiddleware`
  (`PORTAL_SESSION_SECRET`, `https_only=False` porque o cluster também
  serve HTTP puro na 8088/localhost). `ProxyHeadersMiddleware` confia nos
  headers `X-Forwarded-*` do Traefik para gerar as URLs do fluxo OIDC com
  `https://<host>:8443`. O `userinfo` do token fica em
  `request.session["user"]`.
- **Fallback sem SSO**: com `SSO_ENABLED=false` (default local) o portal
  abre sem login — `/` e `/triage` renderizam direto, `/auth/login` é um
  redirect de volta a `/`. Nesse modo o revisor da triagem vem só do campo
  do formulário.

## Landing page (`/`)

Além dos links, renderiza os cartões de `collect_stats()` — estatísticas
agregadas (nunca scans) agrupadas por preocupação, cada coletor
best-effort via `_safe` (falha degrada o cartão para "indisponível", nunca
quebra a página):

- **detection**: sinais de fraude (ArangoDB), `political_connections`
  publicadas e contratos com CRI ≥ 0.5 (marts gold via Trino);
- **ingestion**: contratos silver, volume de contratos dos últimos 30 dias
  e ids duplicados do último dia (mart de qualidade);
- **platform**: % de CPU/memória ociosa dos requests (mart de custo,
  último dia) e energia do namespace nas últimas 24h (Kepler via
  Prometheus).

## Página de triagem (`/triage`)

Fila editorial sobre a coleção `signal_reviews` do ArangoDB, com filtro,
ordenação (maior score primeiro) e paginação **server-side** —
`list_reviews`/`count_reviews` (`db/triage.py`), page size fixo de 100, o
template recebe o total filtrado real para a navegação.

**Query params:**

| Parâmetro | Tipo | Descrição |
|---|---|---|
| `status` | string | Estado da fila (default `pending_review`; vocabulário `TriageStatus`) |
| `signal_type` | string, opcional | Filtro por tipo de sinal (vocabulário `SignalType`) |
| `min_score` | float, opcional | Score mínimo |
| `page` | integer | Página (default 1, normalizada para ≥ 1) |

A página também renderiza o relatório de precisão por operador
(`precision_report`, mesmo da rota `GET /v1/triage/metrics`) e um link de
evidência por sinal — `GET /v1/signals/{key}/evidence`, onde `key` é a
chave de triagem `{entity_type}:{entity_id}:{signal_type}` — que lista os
pacotes reproduzíveis gravados pelo `task_detect` (download do conteúdo
pelo `GET /v1/evidence/{sha256}`; detalhe em `docs/api.md`, "Evidências").
Degrada graciosamente: com o ArangoDB fora, renderiza com aviso de
"indisponível" em vez de falhar.

**Ações (`POST /triage/review`, form):** campos `key`, `status`
(transição alvo: `confirmed`, `rejected` ou `published`), `reviewer`,
`reason` e `filter` (estado da fila para o redirect de volta). O revisor é
obrigatório em toda transição: vem do campo do formulário (sincronizado da
barra de revisor da página) ou, vazio, do `preferred_username` da sessão
SSO. O `reason` é obrigatório no `rejected`, e `published` é **terminal**.
Erros de validação (transição inválida, revisor/motivo ausente, chave
desconhecida) voltam como redirect `303` para `/triage?error=...` com
banner — o formulário nunca responde página 4xx. Sucesso: `303` para
`/triage?status=<filter>`. A máquina de estados e o gancho de alertas por
assinatura no `published` são os mesmos do REST
(`POST /v1/triage/signals/{key}/review`, `docs/api.md`).
