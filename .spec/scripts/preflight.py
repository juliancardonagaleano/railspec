#!/usr/bin/env python3
"""Preflight of a supervised mandate (unit 0114 — local, temporary layer).

Four read-only checks, run **before** § 1.1 of `sdd-supervisado` takes the lock, so the
stops that used to happen after writing state happen before writing anything:

    lock       is `## Instancia en curso` free? (read-only probe, `instance_lock.py
               inspect`). It is an **early, non-authoritative** check: § 1.1 remains
               the only authority that takes the lock.
    mcp        does the governance MCP resolve a known entity with the credentials the
               session will use, within the budget of `--mcp-timeout`?
    git        is there anything uncommitted under the trees this invocation is going
               to touch (`--unit`) or under the mandate file itself?
    validador  does `validate_mandate.py`, run **without appending anything**, report
               any code?

Usage
-----
    preflight.py --mandate <ruta> --unit <dir> [--unit <dir> …]
                 [--mode launch|resume] [--mcp-config <json>]
                 [--mcp-timeout <segundos>] [--entity <id-pce>]
                 [--self-heal --session <id> [--self-heal-mcp-retries <n>]]

`--unit` is required (at least one) and is **the** source of the scope of the git row:
the preflight never infers it. The invoking skill unites two sources — the unit given
as an explicit argument of the invocation (the only source at launch and in an
isolated unit) and `unidades-en-curso` of the entry in force of `## Punto de retoma`
when there is one. The row prints which units it received.

MCP configuration, in this order of precedence: `--mcp-config` > the environment
variable `IARK_PREFLIGHT_MCP_CONFIG` > `.mcp.json` of the repository root. The row
prints the resolved path **and its origin** (`flag` / `env` / `default`). Only origin
`default` checks what the interactive session really uses: with `flag` or `env` the
row is **not representative of the session**, and reading it as if it were is exactly
the late stop this script exists to remove.

Only `stdio` MCP servers are spoken (three JSON-RPC messages: `initialize`,
`notifications/initialized`, `tools/call resolve_entity`). A `http`/`sse` entry is a
`preflight-error` with an explicit reason, not a BLOCK.

Budget: `--mcp-timeout` (default 20 s) bounds the **whole** MCP subprocess, startup
included, measured with `time.monotonic()`; the server process is ended when it
expires, leaving no orphan.

Self-heal (opt-in, `--self-heal` + mandatory `--session`)
-----------------------------------------------------------
Absent, every row runs exactly the read-only path above — CA-07: byte-identical
output and exit code to the version before this flag existed. With `--self-heal`,
three remediations may fire, each requiring its own explicit evidence:

    lock  reaps an orphaned lock **of this same session** — `sesion` of the
          mandate's `## Instancia en curso` (read directly from the file, never
          from `instance_lock.py inspect`'s stdout, which does not change) equals
          `--session`, **and** its registered `pid` (captured at lock-take time,
          `instance_lock.py`) is no longer alive (`pid_alive()`, same
          `ProcessLookupError`/`PermissionError` pattern as
          `supervised_conductor.py:263-271`). A lock with no `pid` bullet is never
          reaped. The reap writes the mandate directly — never through
          `instance_lock.py`'s own write subcommands — and appends one line to
          `<directorio del mandato>/bitacora.md`.
    git   auto-commits (never stashes) the dirty paths already scoped by
          `dirty_paths`/`row_git` onto a new branch `self-heal/<sesión
          saneada>-<timestamp UTC>`, then checks out the original ref again.
    mcp   retries `probe_mcp` with a bounded exponential backoff
          (`--self-heal-mcp-retries`, default 3); each attempt opens its own
          `--mcp-timeout` budget.

Every remediation that succeeds is reported as `DEGRADED`, never as a bare `PASS`;
total exhaustion of the MCP retries stays `BLOCK`, exactly like today. A remediation
that fails partway is `BLOCK` with an explicit `self-heal-fallo: <paso>` detail —
never `PASS` nor a silent `DEGRADED`. No new numeric exit code is introduced:
`DEGRADED` still exits `0`.

Zero writes
-----------
Without `--self-heal` it creates, modifies and deletes nothing — not in the
repository, not outside it, not a log, not bytecode, not a cache. It runs with
`sys.dont_write_bytecode`, imports nothing from `usage/`, exports
`PYTHONDONTWRITEBYTECODE` into every subprocess it starts, and never appends to
`validaciones_mandato`. Of the lock script it uses only the read-only subcommand
`inspect`, even under `--self-heal` — the lock reap above writes the mandate file
itself, it never invokes instance_lock.py's own write subcommands. With
`--self-heal` active, the contract stops being zero-writes **only** when a
remediation actually acts: a reaped lock, a self-heal commit, or a bitácora line
appended.

What a real MCP server writes on its own (a package cache, for instance) belongs to
that server, not to this script.

Exit codes
----------
    0  every row PASS
    1  one or more rows BLOCK **by rule** (the table lists them all: it never stops at
       the first BLOCK)
    2  `preflight-error` — internal failure of the preflight itself (unreadable
       mandate, unsupported configuration, an answer outside the closed mapping). The
       table then prints each already-evaluated row with the literal mark
       `(no autoritativo: preflight-error)`, each unreached row as `no evaluada`, and a
       last line `preflight-error: <motivo>`. No partial PASS counts as valid.

stdlib only (plus `git`), no network beyond the single MCP call that is its reason to
exist, no resident process.
"""

from __future__ import annotations

import sys

sys.dont_write_bytecode = True               # zero writes: not even bytecode

import argparse  # noqa: E402
import contextlib  # noqa: E402
import json  # noqa: E402
import os  # noqa: E402
import queue  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402
import threading  # noqa: E402
import time  # noqa: E402
from datetime import datetime, timezone  # noqa: E402
from pathlib import Path  # noqa: E402

SCRIPTS_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPTS_DIR.parents[1]           # <repo>/.spec/scripts -> <repo>

sys.path.insert(0, str(SCRIPTS_DIR))

from _common import bullets, codes_from_output, dirty_paths, sections  # noqa: E402

EXIT_PASS = 0
EXIT_BLOCK = 1
EXIT_PREFLIGHT_ERROR = 2

#: Entity of S-2/D-4, declared in one place and overridable with `--entity`. Its
#: identity is the C3 id (`pol-gob-indice-agenticos-c3`); PASS means the call came
#: back without `isError` and carried a payload with that id — nothing else about the
#: answer is read.
KNOWN_ENTITY_ID = "pri-gob-fuente-verdad-unica"

DEFAULT_MCP_TIMEOUT = 20.0

LOCK_SCRIPT = SCRIPTS_DIR / "instance_lock.py"
VALIDATOR = SCRIPTS_DIR / "validate_mandate.py"

#: `## Instancia en curso` — same anchor `instance_lock.py` uses. The reap of
#: CA-01 reads it straight from the mandate file (via `_common.sections`/
#: `bullets`), never from `instance_lock.py inspect`'s stdout, which does not
#: change (plan.md § Decisiones).
LOCK_SECTION_HEADING = "## Instancia en curso"

#: Same literal `instance_lock.py` writes when it empties the section
#: (`EMPTY_BODY`) — copied, not imported: the reap writes the mandate directly
#: and never invokes that script's own write subcommands (module docstring,
#: § Zero writes).
LOCK_EMPTY_BODY = "Sin instancia."

#: Backoff of the MCP retry under `--self-heal` (CA-05/CA-06) — same shape as
#: `retry_infrastructure`'s (`orchestrator/.../step_result.py:61-62`), rewritten
#: sync/stdlib here: that module is a different subtree, `asyncio`-based, and is
#: not imported (allowlist of `test_scripts_naming_and_strict_mode.py:60-61`).
SELF_HEAL_RETRY_BASE_SECONDS = 2.0
SELF_HEAL_RETRY_MAX_DELAY_SECONDS = 8.0

#: Closed mapping of D-5: **observed entry → literal cause**. Its four distinct values
#: are the four BLOCK causes of CA-02; anything the client observes outside these keys
#: is a `preflight-error`, never a fifth cause.
MCP_CAUSES: dict[str, str] = {
    "server-exit-unauthorized": "401",
    "server-exit": "ausente",
    "tool-error-unauthorized": "401",
    "tool-error-budget": "presupuesto agotado",
    "no-response": "timeout",
}

ROWS = ("lock", "mcp", "git", "validador")

RULES = {
    "lock": ("BLOCK si `## Instancia en curso` del mandato no está vacía; chequeo "
             "adelantado, no autoritativo (§ 1.1 de sdd-supervisado decide)"),
    "mcp": ("BLOCK si el MCP de gobernanza no resuelve la entidad conocida dentro "
            "del tiempo máximo; causas: ausente, 401, presupuesto agotado, timeout"),
    "git": ("BLOCK si y solo si hay cambios sin commitear bajo las unidades "
            "recibidas en --unit o bajo el archivo de mandato; lo demás se lista "
            "como informativo y no altera el veredicto"),
    "validador": ("BLOCK si validate_mandate.py reporta algún código sobre este "
                  "mandato; el preflight no anexa nada a validaciones_mandato"),
}

_UNAUTHORIZED = re.compile(r"401|unauthorized|forbidden|credencial", re.IGNORECASE)
_BUDGET = re.compile(r"budget|presupuesto", re.IGNORECASE)


class PreflightError(Exception):
    """Internal failure of the preflight: exit 2, never a BLOCK by rule."""


class Row:
    """One row of the table: verdict, one-line detail and its extra lines."""

    def __init__(self, verdict: str, detail: str = "",
                 extra: list[str] | None = None) -> None:
        self.verdict = verdict
        self.detail = detail
        self.extra = extra or []

    @property
    def blocked(self) -> bool:
        return self.verdict == "BLOCK"


# --- Subprocesses ---------------------------------------------------------------------

def child_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Environment of every child: like ours, but forbidden to write bytecode."""
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    if extra:
        env.update(extra)
    return env


def run_python(script: Path, args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(script), *args],
                          capture_output=True, text=True, check=False,
                          env=child_env())


# --- Row: lock ------------------------------------------------------------------------

def pid_alive(pid: int) -> bool:
    """Whether the process `pid` names is still alive.

    Same two specific exceptions `_kill_group` inspects on `os.kill`
    (`supervised_conductor.py:263-271`): `ProcessLookupError` — no such process,
    dead — and `PermissionError` — it exists, owned by someone else, alive.
    Signal `0` sends nothing; it only probes existence. Never a bare `except`.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _rewrite_lock_section(text: str, body_lines: list[str]) -> str:
    """Rewrites the body of `## Instancia en curso`, leaving every other line
    intact. Same algorithm as `instance_lock.py`'s `replace_section`, duplicated
    on purpose (not imported) so this reap keeps using only the read-only
    `inspect` subcommand of that script (module docstring, § Zero writes)."""
    lines = text.splitlines()
    start = None
    for index, line in enumerate(lines):
        if line.strip() == LOCK_SECTION_HEADING:
            start = index
            break
    if start is None:
        return text
    end = len(lines)
    for index in range(start + 1, len(lines)):
        if lines[index].startswith("## "):
            end = index
            break
    rebuilt = lines[:start + 1] + [""] + body_lines + [""] + lines[end:]
    return "\n".join(rebuilt) + ("\n" if text.endswith("\n") else "")


def _append_reap_bitacora(mandate_path: Path, entry: str) -> None:
    """Appends `entry` to `mandate_path.parent / "bitacora.md"` (CA-02). "The
    unit/plan affected" is, by construction, whichever owns the mandate whose
    lock is being reaped — never inferred from anything else."""
    bitacora = mandate_path.parent / "bitacora.md"
    existing = bitacora.read_text(encoding="utf-8") if bitacora.is_file() else ""
    if existing and not existing.endswith("\n"):
        existing += "\n"
    if existing and not existing.endswith("\n\n"):
        existing += "\n"
    bitacora.write_text(existing + entry + "\n", encoding="utf-8")


def _commit_reap_changes(repo_root: Path, mandate: Path) -> str:
    """Commits the reap's own two writes (mandate + bitácora) on the **current**
    branch — never folded into a CA-03 seed-branch commit.

    Without this, the reap's writes would show up as ordinary dirty paths under
    the mandate's own scope; `row_git`'s self-heal would then commit them onto a
    *different* branch and check out back to the original one, which discards
    them from the working tree — silently undoing the very freeing CA-01 just
    did. Not in plan.md § Archivos a modificar; documented as an implementation
    addition in `tasks.md` § Notas de implementación (the interaction was not
    anticipated there). Returns `""` on success, or a `self-heal-fallo: …`
    detail (CA-10) — the writes themselves are **not** rolled back on a commit
    failure: the lock is genuinely free on disk, just not yet committed.
    """
    bitacora = mandate.parent / "bitacora.md"
    paths = [as_relative(repo_root, mandate), as_relative(repo_root, bitacora)]
    added = run_git(["add", "--", *paths], repo_root)
    if added.returncode != 0:
        return f"self-heal-fallo: reap-commit-add ({added.stderr.strip()})"
    committed = run_git(["commit", "-m", "self-heal: reap de lock huérfano",
                         "--", *paths], repo_root)
    if committed.returncode != 0:
        return f"self-heal-fallo: reap-commit ({committed.stderr.strip()})"
    return ""


def _maybe_reap_lock(mandate: Path, session: str | None,
                     repo_root: Path | None) -> Row | None:
    """CA-01: reaps a lock of this own session whose registered pid is dead.

    Both conditions are required, each checked with evidence, never inferred
    (`pri-ia-cero-inferencia-implicita`): own `sesion` **and** a registered `pid`
    that `pid_alive()` reports dead. No `pid` bullet (a lock written by an older
    `instance_lock.py`) is never reaped — absence of evidence is not evidence of
    abandonment. Returns `None` when none of this applies (the caller keeps the
    ordinary BLOCK); a `Row` when it does (`DEGRADED` on success, `BLOCK` with
    `self-heal-fallo: …` if the write partway fails — CA-10).
    """
    if not session:
        return None
    text = mandate.read_text(encoding="utf-8")
    body = sections(text).get(LOCK_SECTION_HEADING)
    if body is None:
        return None
    fields = bullets(body)
    owner = fields.get("sesion", "").strip()
    if not owner or owner != session:
        return None
    pid_raw = fields.get("pid", "").strip()
    if not pid_raw or not pid_raw.isdigit():
        return None
    pid = int(pid_raw)
    if pid_alive(pid):
        return None

    try:
        mandate.write_text(_rewrite_lock_section(text, [LOCK_EMPTY_BODY]),
                           encoding="utf-8")
    except OSError as error:
        return Row("BLOCK", f"self-heal-fallo: reap-lock ({error})")

    entry_ts = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    entry = (f"## {entry_ts} · self-heal:reap · lock liberado "
            f"(sesion={owner}, pid={pid}, evidencia=pid-muerto)")
    try:
        _append_reap_bitacora(mandate, entry)
    except OSError as error:
        with contextlib.suppress(OSError):
            mandate.write_text(text, encoding="utf-8")   # best-effort undo
        return Row("BLOCK", f"self-heal-fallo: reap-bitacora ({error})")

    if repo_root is not None:
        failure = _commit_reap_changes(repo_root, mandate)
        if failure:
            return Row("BLOCK", failure)

    return Row("DEGRADED",
              f"self-heal:reap liberado (sesion={owner}, pid={pid}, "
              "evidencia=pid-muerto)")


def row_lock(mandate: Path, *, self_heal: bool = False,
            session: str | None = None, repo_root: Path | None = None) -> Row:
    completed = run_python(LOCK_SCRIPT, ["inspect", str(mandate)])
    detail = completed.stdout.strip()
    if completed.returncode == 0:
        return Row("PASS", detail or "libre")
    if completed.returncode == 3:
        if self_heal:
            reaped = _maybe_reap_lock(mandate, session, repo_root)
            if reaped is not None:
                return reaped
        return Row("BLOCK", detail)
    raise PreflightError(
        f"instance_lock.py inspect terminó con exit {completed.returncode}: "
        f"{completed.stderr.strip() or 'sin motivo'}")


# --- Row: MCP -------------------------------------------------------------------------

def resolve_mcp_config(flag: str | None) -> tuple[Path, str]:
    """`--mcp-config` > `IARK_PREFLIGHT_MCP_CONFIG` > `.mcp.json` of the repo root."""
    if flag:
        return Path(flag), "flag"
    from_env = os.environ.get("IARK_PREFLIGHT_MCP_CONFIG", "").strip()
    if from_env:
        return Path(from_env), "env"
    return REPO_ROOT / ".mcp.json", "default"


def server_entry(config_path: Path) -> dict | None:
    """The stdio server entry to probe. `None` when the configuration declares none
    (which D-5 reads as the server being `ausente`, not as a broken preflight)."""
    if not config_path.is_file():
        return None
    try:
        data = json.loads(config_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise PreflightError(f"{config_path} no es JSON válido: {error}") from error
    servers = data.get("mcpServers") or {}
    if not isinstance(servers, dict) or not servers:
        return None
    if "pce-mcp" in servers:
        return servers["pce-mcp"]
    if len(servers) == 1:
        return next(iter(servers.values()))
    raise PreflightError(
        f"{config_path} declara varios servidores MCP y ninguno se llama `pce-mcp`: "
        f"{', '.join(sorted(servers))}")


def _pump(stream, sink: queue.Queue) -> None:
    for line in stream:
        sink.put(line)
    sink.put(None)                            # sentinel: end of stream


def _collect(stream, sink: list[str]) -> None:
    for line in stream:
        sink.append(line)


def _send(process: subprocess.Popen, payload: dict) -> None:
    try:
        process.stdin.write(json.dumps(payload) + "\n")
        process.stdin.flush()
    except (BrokenPipeError, ValueError):
        pass                                  # the server is gone; the reader says so


def _await_answer(frames: queue.Queue, request_id: int,
                  deadline: float) -> tuple[str, dict | None]:
    """Reads JSON-RPC frames until the one carrying `result`/`error` for `request_id`.

    Design precedent (not imported: different subtree, its own dependencies):
    `decode_jsonrpc` in
    `orchestrator/src/iark_orchestrator/infrastructure/mcp_transport.py:31`. Its lesson
    travels here: a server may emit `notifications/progress` or `notifications/message`
    **before** its answer, so a frame with `method` and no `id` is skipped, not decoded
    as the answer. Taking the first frame as the answer would report a healthy MCP as
    malformed JSON-RPC.

    Returns `("frame", obj)`, `("eof", None)` when the stream ends without the answer,
    or `("timeout", None)` when the budget expires.
    """
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return "timeout", None
        try:
            line = frames.get(timeout=remaining)
        except queue.Empty:
            return "timeout", None
        if line is None:
            return "eof", None
        line = line.strip()
        if not line:
            continue
        try:
            frame = json.loads(line)
        except json.JSONDecodeError:
            continue                          # noise on stdout is not an answer
        if not isinstance(frame, dict):
            continue
        if frame.get("id") != request_id:
            continue                          # notification or another id: keep reading
        return "frame", frame


def _tool_text(result: dict) -> str:
    parts = []
    for item in result.get("content") or []:
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _classify_tool_answer(frame: dict, entity: str) -> str:
    if "error" in frame:
        raise PreflightError(
            f"el MCP respondió un error JSON-RPC a tools/call: {frame['error']}")
    result = frame.get("result")
    if not isinstance(result, dict):
        raise PreflightError("la respuesta de tools/call no trae `result`")
    text = _tool_text(result)
    if result.get("isError"):
        if _UNAUTHORIZED.search(text):
            return "tool-error-unauthorized"
        if _BUDGET.search(text):
            return "tool-error-budget"
        raise PreflightError(
            f"el MCP respondió isError con un texto fuera del mapeo cerrado: "
            f"{text.strip()[:200] or 'sin texto'}")
    if entity not in text:
        raise PreflightError(
            f"el MCP resolvió sin error pero el payload no trae la entidad {entity}")
    return ""                                  # PASS


def probe_mcp(config_path: Path, timeout: float, entity: str) -> str:
    """Returns the observed-entry key of `MCP_CAUSES`, or `""` for PASS."""
    entry = server_entry(config_path)
    if entry is None:
        return "server-exit"
    kind = str(entry.get("type") or "stdio").lower()
    if kind != "stdio":
        raise PreflightError(
            f"{config_path} declara un servidor MCP `type: {kind}`; el preflight solo "
            "habla stdio (D-6)")
    command = entry.get("command")
    if not command:
        return "server-exit"
    env_extra = {k: os.path.expandvars(str(v))
                 for k, v in (entry.get("env") or {}).items()}
    argv = [str(command), *[str(a) for a in (entry.get("args") or [])]]

    deadline = time.monotonic() + timeout
    try:
        process = subprocess.Popen(
            argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, cwd=str(REPO_ROOT),
            env=child_env(env_extra))
    except OSError:
        return "server-exit"                   # missing command: the server is absent

    frames: queue.Queue = queue.Queue()
    errors: list[str] = []
    readers = [
        threading.Thread(target=_pump, args=(process.stdout, frames), daemon=True),
        threading.Thread(target=_collect, args=(process.stderr, errors), daemon=True),
    ]
    for reader in readers:
        reader.start()
    try:
        _send(process, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                        "params": {"protocolVersion": "2024-11-05",
                                   "capabilities": {},
                                   "clientInfo": {"name": "preflight",
                                                  "version": "0114"}}})
        kind_of_answer, frame = _await_answer(frames, 1, deadline)
        if kind_of_answer == "timeout":
            return "no-response"
        if kind_of_answer == "eof":
            stderr_text = "".join(errors)
            if _UNAUTHORIZED.search(stderr_text):
                return "server-exit-unauthorized"
            return "server-exit"
        if frame is not None and "error" in frame:
            raise PreflightError(
                f"el MCP respondió un error JSON-RPC a initialize: {frame['error']}")

        _send(process, {"jsonrpc": "2.0", "method": "notifications/initialized"})
        _send(process, {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
                        "params": {"name": "resolve_entity",
                                   "arguments": {"entity_id": entity}}})
        kind_of_answer, frame = _await_answer(frames, 2, deadline)
        if kind_of_answer == "timeout":
            return "no-response"
        if kind_of_answer == "eof" or frame is None:
            raise PreflightError(
                "el MCP cerró la salida sin responder a tools/call resolve_entity")
        return _classify_tool_answer(frame, entity)
    finally:
        _end_process(process, readers)


def _end_process(process: subprocess.Popen,
                 readers: list[threading.Thread]) -> None:
    """Ends the server, leaving no orphan: terminate, then kill if it insists.

    Order matters. The process dies **first**: that is what gives the reader threads
    their end of stream so they finish on their own. Closing the pipes from this
    thread while a reader is still blocked inside them hangs instead of tidying up —
    which is exactly how the `timeout` case used to never come back.
    """
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for reader in readers:
        reader.join(timeout=2)
    for stream in (process.stdin, process.stdout, process.stderr):
        if stream is not None:
            with contextlib.suppress(OSError, ValueError):
                stream.close()


def retry_probe_mcp(config_path: Path, timeout: float, entity: str, *,
                    attempts: int, sleep=time.sleep, probe=probe_mcp
                    ) -> tuple[str, int, list[float]]:
    """Retries `probe` (`probe_mcp` by default) with a bounded exponential
    backoff — sync/stdlib rewrite of `retry_infrastructure`
    (`orchestrator/.../step_result.py:130-188`; not imported, see module
    docstring). Each attempt opens its **own** `timeout` budget — CA-05/CA-06:
    a single shared deadline could in practice run fewer attempts than
    configured, contradicting the explicit retry limit. `sleep`/`probe` are
    injectable so tests stay deterministic (plan.md § Reutilización).

    Returns `(observado, intentos_hechos, delays)`: `observado` is the
    `MCP_CAUSES` key of the last attempt ("" on success), `intentos_hechos`
    counts from 1, and `delays` is the backoff actually used between attempts.
    """
    attempts = max(int(attempts), 1)
    delays: list[float] = []
    observed = ""
    for attempt in range(1, attempts + 1):
        observed = probe(config_path, timeout, entity)
        if not observed:
            return "", attempt, delays
        if attempt < attempts:
            delay = min(SELF_HEAL_RETRY_BASE_SECONDS * (2 ** (attempt - 1)),
                       SELF_HEAL_RETRY_MAX_DELAY_SECONDS)
            delays.append(delay)
            sleep(delay)
    return observed, attempts, delays


def row_mcp(config_path: Path, origin: str, timeout: float, entity: str, *,
           self_heal: bool = False, mcp_retries: int = 1) -> Row:
    where = f"(config: {config_path}, origen: {origin})"
    if not self_heal:
        observed = probe_mcp(config_path, timeout, entity)
        if not observed:
            return Row("PASS", f"resolvió {entity} {where}")
        return Row("BLOCK", f"{MCP_CAUSES[observed]} {where}")

    observed, attempts, delays = retry_probe_mcp(config_path, timeout, entity,
                                                 attempts=mcp_retries)
    delay_text = ", ".join(f"{d:.1f}s" for d in delays) if delays else "ninguno"
    extra = [f"intentos: {attempts}/{mcp_retries}", f"delays: {delay_text}"]
    if not observed:
        if attempts > 1:
            return Row("DEGRADED",
                       f"resolvió {entity} tras {attempts} intento(s) {where}",
                       extra)
        return Row("PASS", f"resolvió {entity} {where}", extra)
    return Row("BLOCK",
              f"{MCP_CAUSES[observed]} {where} (agotados {attempts} intento(s))",
              extra)


# --- Row: git -------------------------------------------------------------------------

def git_root_of(path: Path) -> Path:
    completed = subprocess.run(
        ["git", "-C", str(path.parent), "rev-parse", "--show-toplevel"],
        capture_output=True, text=True, check=False)
    if completed.returncode != 0:
        raise PreflightError(f"{path.parent} no está dentro de un repositorio git")
    return Path(completed.stdout.strip())


def as_relative(repo_root: Path, path: Path) -> str:
    try:
        return path.resolve().relative_to(repo_root.resolve()).as_posix()
    except ValueError as error:
        raise PreflightError(
            f"{path} queda fuera del repositorio {repo_root}") from error


def run_git(args: list[str], repo_root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root), *args],
                          capture_output=True, text=True, check=False)


def _sanitize_session(session: str) -> str:
    return re.sub(r"[^a-zA-Z0-9._-]", "-", session)


def _seed_branch_name(session: str) -> str:
    """`self-heal/<sesión saneada>-<timestamp UTC>` — convención propia (Q4 del
    spec quedó invalidada por evidencia: `worktree_registry.py`/0118 no define
    ninguna; `git worktree add` corre `--detach`, plan.md § Decisiones)."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"self-heal/{_sanitize_session(session)}-{stamp}"


def _original_ref(repo_root: Path) -> str:
    """The branch to return to after the self-heal commit, or the bare commit
    when `HEAD` is detached (the plan's decision: no special-case branch, just
    an explicit failure if returning to it later does not work)."""
    completed = run_git(["rev-parse", "--abbrev-ref", "HEAD"], repo_root)
    ref = completed.stdout.strip()
    if completed.returncode == 0 and ref and ref != "HEAD":
        return ref
    completed = run_git(["rev-parse", "HEAD"], repo_root)
    return completed.stdout.strip()


def _self_heal_git(repo_root: Path, scoped: list[str], session: str) -> Row:
    """CA-03/CA-04: commits (never stashes) `scoped` onto a new seed branch, then
    checks out `original` again. Each step inspects its own `returncode`
    explicitly (CA-10 (ii)); on a mid-sequence failure it attempts a best-effort
    checkout back and reports `BLOCK` with `self-heal-fallo: <paso>` — never a
    silent `PASS`/`DEGRADED`. A branch-name collision is a `preflight-error`
    (plan.md § Decisiones), not a failed remediation: nothing has been touched
    yet at that point.
    """
    branch = _seed_branch_name(session)
    original = _original_ref(repo_root)

    created = run_git(["checkout", "-b", branch], repo_root)
    if created.returncode != 0:
        raise PreflightError(
            f"self-heal: no se pudo crear la rama seed {branch} (posible "
            f"colisión de nombre): {created.stderr.strip() or 'sin motivo'}")

    added = run_git(["add", "--", *scoped], repo_root)
    if added.returncode != 0:
        run_git(["checkout", original], repo_root)
        return Row("BLOCK", f"self-heal-fallo: git-add ({added.stderr.strip()})")

    message = f"self-heal: auto-commit de árbol sucio hacia {branch}"
    committed = run_git(["commit", "-m", message, "--", *scoped], repo_root)
    if committed.returncode != 0:
        run_git(["checkout", original], repo_root)
        return Row("BLOCK",
                  f"self-heal-fallo: git-commit ({committed.stderr.strip()})")

    back = run_git(["checkout", original], repo_root)
    if back.returncode != 0:
        return Row("BLOCK", "self-heal-fallo: git-checkout-vuelta "
                            f"({back.stderr.strip()})")

    return Row("DEGRADED", f"self-heal:commit rama={branch} accion=commit",
              [f"rama seed: {branch}", "accion: commit"])


def row_git(repo_root: Path, mandate: Path, units: list[Path], *,
           self_heal: bool = False, session: str | None = None) -> Row:
    scope = [as_relative(repo_root, unit) for unit in units]
    scope.append(as_relative(repo_root, mandate))
    scoped, informational = dirty_paths(repo_root, scope)
    extra = [f"alcance: {', '.join(scope)}"]
    extra.extend(_path_lines("sucias", scoped))
    extra.extend(_path_lines("informativas", informational))
    if scoped:
        if self_heal and session:
            remediated = _self_heal_git(repo_root, scoped, session)
            remediated.extra = extra + remediated.extra
            return remediated
        return Row("BLOCK", f"{len(scoped)} ruta(s) sin commitear bajo el alcance",
                   extra)
    return Row("PASS", "nada sin commitear bajo el alcance", extra)


def _path_lines(label: str, paths: list[str]) -> list[str]:
    if not paths:
        return [f"{label}: ninguna"]
    return [f"{label}:"] + [f"  {path}" for path in paths]


# --- Row: validator -------------------------------------------------------------------

def is_plan_mandate(mandate: Path) -> bool:
    # Location, not file name: every real plan lives at `.spec/planes/<id>/plan.md`,
    # so keying off `plan-maestro.md` alone sent each one down the isolated-unit
    # branch below, where a directory with no `_estado.yaml` of its own always
    # reports `unidad-supervisado-sin-mandato` — a BLOCK on a plan the authoritative
    # validator passes. The old name stays accepted for mandates that use it.
    if mandate.name == "plan-maestro.md":
        return True
    parent = mandate.parent
    return parent.parent.name == "planes"


def validator_args(mandate: Path) -> list[str]:
    if is_plan_mandate(mandate):
        # The plan id, not its directory path: `validate_mandate.py --plan` treats an
        # existing directory argument as an isolated test fixture with its own
        # `units/`, which would silently point this row at a fixture tree instead of
        # the real `.spec/units/` for every real mandate (whose directory always
        # exists).
        return ["--plan", mandate.parent.name]
    return ["--unidad", str(mandate.parent)]


def row_validator(mandate: Path) -> Row:
    completed = run_python(VALIDATOR, validator_args(mandate))
    if completed.returncode not in (0, 1):
        raise PreflightError(
            f"validate_mandate.py terminó con exit {completed.returncode}: "
            f"{completed.stderr.strip() or 'sin motivo'}")
    codes = codes_from_output(completed.stdout)
    if codes:
        return Row("BLOCK", f"{len(codes)} código(s)", [f"codigos: {' '.join(codes)}"])
    return Row("PASS", "sin códigos", ["codigos: ninguno"])


# --- Table ----------------------------------------------------------------------------

def print_header(mandate: Path, mode: str, units: list[Path]) -> None:
    unit_list = ", ".join(str(u) for u in units)
    print(f"preflight — mandato: {mandate} · modo: {mode} · unidades: {unit_list}")


def print_table(results: dict[str, Row]) -> None:
    for name in ROWS:
        row = results[name]
        print(f"{name}: {row.verdict} {row.detail}".rstrip())
        print(f"  regla: {RULES[name]}")
        for line in row.extra:
            print(f"  {line}")


def print_error_table(results: dict[str, Row], reason: str) -> None:
    for name in ROWS:
        row = results.get(name)
        if row is None:
            print(f"{name}: no evaluada")
        else:
            print(f"{name}: {row.verdict} (no autoritativo: preflight-error)")
    print(f"preflight-error: {reason}")


# --- Orchestration --------------------------------------------------------------------

def run(args: argparse.Namespace) -> int:
    mandate = Path(args.mandate)
    units = [Path(u) for u in args.unit]
    results: dict[str, Row] = {}
    print_header(mandate, args.mode, units)
    try:
        if args.self_heal and not args.session:
            raise PreflightError(
                "--self-heal requiere --session (CA-01 exige sesión propia + "
                "PID muerto, nunca uno solo)")
        if not units:
            raise PreflightError(
                "hace falta al menos un --unit: el preflight no infiere el alcance "
                "de la fila git (CA-03)")
        if not mandate.is_file():
            raise PreflightError(f"no existe el archivo de mandato: {mandate}")
        config_path, origin = resolve_mcp_config(args.mcp_config)

        # Only computed under --self-heal (never for the CA-07 default path): a
        # successful reap commits its own writes on this same repo_root, so
        # row_git's dirty scope never sees them and folds them into a seed
        # branch that would revert them from the working tree on checkout-back.
        lock_repo_root = git_root_of(mandate) if args.self_heal else None
        results["lock"] = row_lock(mandate, self_heal=args.self_heal,
                                   session=args.session,
                                   repo_root=lock_repo_root)
        results["mcp"] = row_mcp(config_path, origin, args.mcp_timeout, args.entity,
                                 self_heal=args.self_heal,
                                 mcp_retries=args.self_heal_mcp_retries)
        results["git"] = row_git(git_root_of(mandate), mandate, units,
                                 self_heal=args.self_heal, session=args.session)
        results["validador"] = row_validator(mandate)
    except PreflightError as error:
        print_error_table(results, str(error))
        return EXIT_PREFLIGHT_ERROR

    print_table(results)
    blocked = [name for name in ROWS if results[name].blocked]
    if blocked:
        print(f"resultado: BLOCK ({', '.join(blocked)})")
        return EXIT_BLOCK
    degraded = [name for name in ROWS if results[name].verdict == "DEGRADED"]
    if degraded:
        print(f"resultado: DEGRADED ({', '.join(degraded)})")
        return EXIT_PASS
    print("resultado: PASS")
    return EXIT_PASS


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="preflight.py",
        description="Preflight de mandato supervisado (unidad 0114, capa local).")
    parser.add_argument("--mandate", required=True, metavar="RUTA")
    parser.add_argument("--unit", action="append", default=[], metavar="DIR",
                        help="árbol de unidad del alcance de CA-03 (repetible)")
    parser.add_argument("--mode", default="launch", choices=["launch", "resume"],
                        help="informativo: se imprime en la cabecera de la tabla")
    parser.add_argument("--mcp-config", default=None, metavar="JSON")
    parser.add_argument("--mcp-timeout", type=float, default=DEFAULT_MCP_TIMEOUT,
                        metavar="SEGUNDOS")
    parser.add_argument("--entity", default=KNOWN_ENTITY_ID, metavar="ID-PCE")
    parser.add_argument("--self-heal", action="store_true",
                        help="opt-in: intenta remediar lock huérfano, git dirty "
                             "y MCP caído antes de bloquear (ver docstring)")
    parser.add_argument("--session", default=None, metavar="ID",
                        help="sesión propia; obligatoria junto con --self-heal "
                             "(CA-01)")
    parser.add_argument("--self-heal-mcp-retries", type=int, default=3,
                        metavar="N",
                        help="límite explícito de reintentos del MCP bajo "
                             "--self-heal (CA-05), presupuesto propio por "
                             "intento vía --mcp-timeout")
    args = parser.parse_args(argv)
    try:
        return run(args)
    except Exception as error:                 # noqa: BLE001 — CA-10 (iv)
        print(f"preflight-error: {type(error).__name__}: {error}")
        return EXIT_PREFLIGHT_ERROR


if __name__ == "__main__":
    sys.exit(main())
