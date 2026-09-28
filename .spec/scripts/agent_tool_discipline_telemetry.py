#!/usr/bin/env python3
"""Telemetría de disciplina de herramientas runtime (CA-22, CA-23, unit 0154 G1).

Hook que lee el payload JSON del harness por stdin y, si el opt-in
`SDD_TOOL_DISCIPLINE_LOG=1` está activo y el comando abre con uno de los
patrones triviales de inspección (`cat`, `ls`, `find`, `tree`, `git status`,
`git diff`), escribe una línea append-only atómica en
`<cwd>/.spec/.usage/tool-discipline.log`. Sin la variable, exit 0 sin
escribir. Errores de I/O a `sys.stderr`; nunca raise (CA-12).

Formato CA-11:
    <ISO-8601 con Z>\tsession=<id>\ttool=Bash\tpattern=<regex_hit>\tcommand=<sha256[:8]>
"""

from __future__ import annotations

import datetime
import hashlib
import json
import os
import re
import sys
from pathlib import Path

sys.dont_write_bytecode = True

EXIT_OK = 0
OPT_IN_ENV = "SDD_TOOL_DISCIPLINE_LOG"
MATCH = re.compile(r"^(?P<pattern>cat|ls|find|tree|git\s+status|git\s+diff)\b")
LOG_REL = Path(".spec") / ".usage" / "tool-discipline.log"
ISO_FMT = "%Y-%m-%dT%H:%M:%SZ"


def _now_utc() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime(ISO_FMT)


def _hash8(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8")).hexdigest()[:8]


def _format_line(timestamp: str, session: str, tool: str,
                 pattern: str, command: str) -> str:
    sha8 = _hash8(command)
    return (f"{timestamp}\tsession={session}\ttool={tool}"
            f"\tpattern={pattern}\tcommand={sha8}\n")


def _append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_APPEND | os.O_CREAT | os.O_WRONLY, 0o644)
    try:
        os.write(fd, line.encode("utf-8"))
    finally:
        os.close(fd)


def process(payload: dict, *, log_root: Path, now: str | None = None) -> int:
    """Procesa un payload; escribe una línea si aplica. Retorna siempre 0."""
    if os.environ.get(OPT_IN_ENV) != "1":
        return EXIT_OK
    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") or ""
    match = MATCH.match(command)
    if not match:
        return EXIT_OK
    pattern = match.group("pattern")
    line = _format_line(
        timestamp=now or _now_utc(),
        session=payload.get("session_id") or "",
        tool=payload.get("tool_name") or "Bash",
        pattern=pattern,
        command=command,
    )
    try:
        _append_line(log_root / LOG_REL, line)
    except OSError as exc:
        print(f"ERROR — no se pudo escribir el log: {exc}", file=sys.stderr)
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    del argv
    raw = sys.stdin.read() or "{}"
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"ERROR — payload no es JSON: {exc}", file=sys.stderr)
        return EXIT_OK
    cwd = payload.get("cwd") or "."
    return process(payload, log_root=Path(cwd))


if __name__ == "__main__":
    sys.exit(main())
