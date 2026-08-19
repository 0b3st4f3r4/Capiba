#!/usr/bin/env python3
"""PreToolUse hook para o Kimi Code: block-and-suggest de comandos com
equivalente RTK.

Lê o payload do evento em stdin, extrai `tool_input.command` e pergunta ao
`rtk rewrite` (fonte única de reescrita) se há equivalente RTK. Se houver e
o comando ainda não estiver na forma reescrita, bloqueia (exit 2) com a
sugestão na stderr; o agente reemite o comando na forma `rtk ...`.

Fail-open por contrato: qualquer erro interno sai com exit 0 (permite),
inclusive a ausência do próprio `rtk` na máquina.

Instalação: registrar no `~/.kimi/config.toml` (global, vale para qualquer
projeto que tenha este arquivo; nos demais, o hook falha aberto e é no-op):

    [[hooks]]
    event = "PreToolUse"
    matcher = "Bash"
    command = "/usr/bin/python3 .kimi/hooks/rtk-rewrite.py"
    timeout = 10

O working directory do hook é o diretório do projeto da sessão, por isso o
caminho relativo funciona em qualquer checkout.
"""

import json
import shutil
import subprocess
import sys
from pathlib import Path

RTK = shutil.which("rtk") or str(Path.home() / ".local" / "bin" / "rtk")


def main() -> int:
    try:
        payload = json.load(sys.stdin)
    except Exception:
        return 0

    command = (payload.get("tool_input") or {}).get("command") or ""
    command = command.strip()
    if not command:
        return 0

    try:
        proc = subprocess.run(
            [RTK, "rewrite", command],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except Exception:
        return 0

    rewritten = proc.stdout.strip()
    if not rewritten or rewritten == command:
        return 0

    print(
        "RTK: este comando tem equivalente compacto. Reemita exatamente assim:\n"
        f"{rewritten}",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())
