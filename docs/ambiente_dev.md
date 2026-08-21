# Ambiente de desenvolvimento — ferramentas do agente

> **Propósito:** documentar as ferramentas do ambiente do desenvolvedor
> (RTK, DeepSeek Harness) que não são componentes do produto.
> **Quando consultar:** ao configurar ou depurar o ambiente local de
> desenvolvimento com agentes.
> **Relacionados:** `AGENTS.md` (ponteiro), `docs/operacao.md` (cluster
> local).
> **Sincronizado com:** `.kimi/` — 2026-08-21.

Ferramentas do ambiente do desenvolvedor (não são componentes do produto).
Extraído do AGENTS.md em 2026-08-21.

## RTK — redução de tokens (Kimi Code CLI)

Redução de tokens via hook **block-and-suggest** mantido no projeto:
`.kimi/hooks/rtk-rewrite.py` (PreToolUse sobre `Bash`) — consulta `rtk rewrite`
e bloqueia o comando original com a forma RTK como sugestão; o agente reemite
reescrito. Fail-open: sem `rtk` ou sem o arquivo, nada é bloqueado. O Kimi só
registra hooks no `~/.kimi/config.toml` global; em cada máquina, uma entrada com
caminho relativo (o cwd do hook é o projeto da sessão — vale para qualquer
checkout que tenha o arquivo):

```toml
[[hooks]]
event = "PreToolUse"
matcher = "Bash"
command = "/usr/bin/python3 .kimi/hooks/rtk-rewrite.py"
timeout = 10
```

Comandos reescritos (redução de 60-90%): `ls`/`tree` → `rtk ls`;
`cat`/`head`/`tail` → `rtk read`; `grep`/`rg` → `rtk grep`; `git
status`/`log`/`diff`/`add`/`commit`/`push` → `rtk git ...`; `pytest` → `rtk
pytest`; `ruff check` → `rtk ruff check`; `docker ps`/`logs` → `rtk docker
...`. Com as ferramentas built-in do Kimi o hook não se aplica; para saída
compacta use explicitamente `rtk read <file>` (`-l aggressive` = só
assinaturas), `rtk smart`, `rtk grep`, `rtk find`, `rtk diff`. Analytics: `rtk
gain` (`--graph`/`--history`/`--daily`) e `rtk discover`. Config em
`~/.config/rtk/config.toml` (`[hooks] exclude_commands`, `[tee] enabled, mode =
"failures"`). Notas: RTK só intercepta bash; estimativas de token são `bytes /
4`; percentuais são redução na saída bash, não na conta total.

## DeepSeek Harness (dsh) — ferramenta experimental

Harness agêntico open source da DeepSeek (sobre o framework Cordis), ferramenta
de desenvolvimento **opcional e experimental** — não é componente do produto e
não substitui o Kimi Code CLI como driver. `scripts/setup.sh` verifica sua
presença; instalação pinada: `npm install -g @deepseek-ai/dsh@0.1.0-rc.7`
(developer preview com breaking changes — bump sempre deliberado, nunca
`latest`).

Spike (2026-08-20, scratch isolado): diferenciais confirmados — profiles
versionáveis (`package.json` + `cordis.patch.yml` reproduzem a config
byte-idêntica, auditável via `dsh --profile <nome> --dump-config`) e session
log append-only como fonte da verdade (requisição ao modelo 100% reconstruível
do JSONL). Hooks, compaction e sandbox/approval são paridade com o Kimi CLI.
Veredito: não adotar como driver principal; usos pontuais possíveis (runner
headless em CI, ambiente agêntico auditável).

**Atenção — telemetria**: o pacote `session-telemetry-otel` vem montado em modo
`DISABLED` (opt-in via `DSH_TELEMETRY_MODE`); habilitado, exporta logs de
sessão sem redação para endpoint da DeepSeek. Não habilitar — incompatível com
a governança do projeto.
