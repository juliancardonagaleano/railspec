#!/usr/bin/env python3
"""`PreToolUse` guard: a Bash command about to write under `.spec/` by a route
that is not the harness' edit tool nor a versioned script (unit 0114,
CA-19/CA-20/CA-21; local, temporary layer).

What it does
------------
Reads the `PreToolUse` payload Claude Code delivers on stdin (`session_id`,
`cwd`, `hook_event_name`, `tool_name`, `tool_input`), looks at
`tool_input.command` and applies the **three** patterns of S-6 -- and only those
three:

1. `sed -i` whose path falls under `.spec/`;
2. a truncating redirection (`>`, never `>>`) toward a path under `.spec/` that
   **already exists**;
3. code passed inline to an interpreter (heredoc, or `-c`) whose text names a
   path under `.spec/`. The interpreter list is **closed** and is read from
   `.spec/_fixtures/patrones-prohibidos.txt` (CA-21): widening it is
   editing that fixture, never this file. An interpreter outside the list (say
   `awk`) is not blocked -- documented outcome, not a hole.

Invoking a versioned script of the repo (`.spec/scripts/*.py`, `*.sh`) with
arguments under `.spec/**` matches none of the three.

On a block it emits the `deny` decision on **stdout** and appends one JSONL line
to `.spec/.usage/guard-rejections-pre-bash.jsonl`. That log feeds **no**
indicator: K6 reads only the post-write one (CA-23, S-8).

The rejection message names the rule infringed and the allowed route, and
neither demands nor forbids stopping the mandate (CA-19): acting on the block
and retrying through the allowed route is the guard working, not bypassing a
harness denial.

Exit codes
----------
`EXIT_OK` (0), always. The decision travels as JSON on stdout, never as an exit
code: the shell wrapper of `.claude/settings.json` ends in `|| true` (convention
inherited from unit 0113), which would absorb any non-zero exit. Smoke-tested
under `claude -p` before this file was written (unit 0114, T12, D-8).

Fail-open (CA-22c): any exception of this hook's own is written to
`.spec/.usage/guard-failures.log` and the tool is **not** blocked.

No network (CA-22a) and no file reads besides the pattern fixture and the
`os.path.exists` probe of rule 2: stdlib only.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import shlex
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

#: `<repo>` by self-location, same base as the post-write guard and as
#: `usage/paths.py::default_repo_root` -- not `cwd` of the payload, not
#: `CLAUDE_PROJECT_DIR`.
REPO_ROOT = Path(__file__).resolve().parents[2]

USAGE_DIR = REPO_ROOT / ".spec" / ".usage"
REJECTIONS_LOG = USAGE_DIR / "guard-rejections-pre-bash.jsonl"
FAILURES_LOG = USAGE_DIR / "guard-failures.log"
PATTERNS_FIXTURE = REPO_ROOT / ".spec" / "_fixtures" / "patrones-prohibidos.txt"

#: Only exit code this hook ever uses (CA-10 iii, D-8).
EXIT_OK = 0

#: D-16: reserved prefix of the pilot's test unit (`supervised-test.sh`).
PILOT_UNIT_TREE = ".spec/units/9109-"
EXEMPT_TREE_ENV = "IARK_GUARD_EXEMPT_TREE"
EXEMPT_TREE_ROOT = ".spec/units/"

#: Shell operators that separate one command from the next. Each segment is
#: judged on its own, so `cd x && sed -i … .spec/y` is caught.
_SEPARATORS = ("&&", "||", ";", "|", "&")

#: A path under `.spec/` anywhere in a text (token, `-c` body, heredoc body).
_SPEC_PATH = re.compile(r"[\w./@+-]*\.spec/[\w./@+-]*")

#: Heredoc opener: `<<EOF`, `<<-EOF`, `<<'EOF'`, `<< "EOF"`.
_HEREDOC = re.compile(r"<<-?\s*[\"']?[A-Za-z_][A-Za-z0-9_]*")

#: Redirection tokens. `>>` (append) is deliberately absent: CA-20 blocks only
#: the truncating form.
_REDIRECT_BARE = re.compile(r"^[0-9]?>$")
_REDIRECT_INLINE = re.compile(r"^[0-9]?>([^>].*)$")

RULES = {
    1: "`sed -i` sobre una ruta bajo `.spec/`",
    2: "redirección truncante `>` sobre un archivo existente bajo `.spec/`",
    3: "código inline (`-c` o heredoc) pasado a un intérprete y que nombra una ruta bajo `.spec/`",
}

ALLOWED_ROUTE = (
    "vía permitida: herramienta de edición del harness o script versionado de "
    ".spec/scripts/"
)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _log_failure(message: str) -> None:
    """CA-22c: every failure of this hook's own leaves a motive here and blocks
    nothing. Never raises."""
    with contextlib.suppress(OSError):
        _append(FAILURES_LOG, f"{_now()} guard_bash_spec_writes: {message}")


def load_interpreters() -> tuple[str, ...]:
    """The closed interpreter list of CA-21, read from the versioned fixture --
    the single source it shares with the CA-15 ritual patterns."""
    names: list[str] = []
    for raw in PATTERNS_FIXTURE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "|" not in line:
            continue
        kind, value = line.split("|", 1)
        if kind.strip() == "interprete" and value.strip():
            names.append(value.strip())
    return tuple(names)


def _exempt_roots() -> list[str]:
    """Repo-relative prefixes exempt under D-16.

    Same cap and same `exempt-tree-ignored` trace as
    `guard_written_state_shape.py::_exempt_roots` -- kept byte-identical on
    purpose: the two hooks are separate processes with no shared module of their
    own, and `_common.py` belongs to the ritual scripts, not to the guards.
    """
    roots = [PILOT_UNIT_TREE]
    raw = os.environ.get(EXEMPT_TREE_ENV)
    if raw is None:
        return roots
    value = raw.strip()
    normalized = value.rstrip("/")
    if normalized and (normalized + "/").startswith(EXEMPT_TREE_ROOT) and normalized != EXEMPT_TREE_ROOT.rstrip("/"):
        roots.append(normalized + "/")
        return roots
    _log_failure(f"exempt-tree-ignored: {value!r}")
    return roots


def is_exempt(rel_path: str, exempt_roots: list[str]) -> bool:
    return any(rel_path.startswith(root) for root in exempt_roots)


def spec_path(raw: str) -> str | None:
    """`raw` as a repo-relative `.spec/…` path, or `None` if it is not one.

    Accepts the three shapes a command can carry: `.spec/x`, `./.spec/x` and an
    absolute path whose tail is `/.spec/x`. Rejects `foo.spec/x`, where `.spec`
    is only the tail of another name."""
    text = raw.strip().strip("'\"")
    index = text.find(".spec/")
    if index == -1:
        return None
    if index == 0 or text[index - 1] == "/":
        return text[index:]
    return None


def spec_paths_in_text(text: str) -> list[str]:
    """Every repo-relative `.spec/…` path named anywhere in a free text."""
    found: list[str] = []
    for candidate in _SPEC_PATH.findall(text):
        rel = spec_path(candidate)
        if rel and rel not in found:
            found.append(rel)
    return found


def tokenize(command: str) -> list[str]:
    """`shlex.split`, degrading to a whitespace split when the command does not
    balance its quotes (`echo "it's fine" > .spec/x`).

    A `ValueError` here would otherwise fail open and let exactly that kind of
    command through, so the degradation keeps the three rules active on a
    best-effort tokenization and leaves a trace in the failures log."""
    try:
        return shlex.split(command)
    except ValueError as exc:
        _log_failure(f"tokenize-degraded: {exc}")
        return command.split()


def segments(tokens: list[str]) -> list[list[str]]:
    """The token list split on shell operators, so each command of a compound
    line is judged on its own."""
    result: list[list[str]] = [[]]
    for token in tokens:
        if token in _SEPARATORS:
            result.append([])
        else:
            result[-1].append(token)
    return [segment for segment in result if segment]


def _basename(token: str) -> str:
    return token.rsplit("/", 1)[-1]


def match_sed_in_place(command_segments: list[list[str]]) -> list[str]:
    """Rule 1 (CA-19)."""
    matched: list[str] = []
    for segment in command_segments:
        if _basename(segment[0]) != "sed":
            continue
        if not any(token == "-i" or token.startswith("-i") for token in segment[1:]):
            continue
        for token in segment[1:]:
            rel = spec_path(token)
            if rel and rel not in matched:
                matched.append(rel)
    return matched


def match_truncating_redirect(tokens: list[str], cwd: Path) -> list[str]:
    """Rule 2 (CA-20): `>` (never `>>`) toward a path under `.spec/` that already
    exists. Toward a file that does not exist yet there is nothing to clobber,
    so it is not blocked."""
    matched: list[str] = []
    targets: list[str] = []
    for index, token in enumerate(tokens):
        if _REDIRECT_BARE.match(token):
            if index + 1 < len(tokens):
                targets.append(tokens[index + 1])
            continue
        inline = _REDIRECT_INLINE.match(token)
        if inline:
            targets.append(inline.group(1))
    for target in targets:
        rel = spec_path(target)
        if rel is None:
            continue
        candidate = Path(target.strip().strip("'\""))
        absolute = candidate if candidate.is_absolute() else cwd / candidate
        if absolute.exists() and rel not in matched:
            matched.append(rel)
    return matched


def match_inline_interpreter(
    command: str, command_segments: list[list[str]], interpreters: tuple[str, ...]
) -> list[str]:
    """Rule 3 (CA-21, S-6): code passed inline to an interpreter of the closed
    list whose text names a path under `.spec/`."""
    matched: list[str] = []
    for segment in command_segments:
        if _basename(segment[0]) not in interpreters:
            continue
        if "-c" not in segment[1:]:
            continue
        index = segment.index("-c", 1)
        body = " ".join(segment[index + 1:])
        for rel in spec_paths_in_text(body):
            if rel not in matched:
                matched.append(rel)
    if _HEREDOC.search(command) and any(
        _basename(segment[0]) in interpreters for segment in command_segments
    ):
        for rel in spec_paths_in_text(command):
            if rel not in matched:
                matched.append(rel)
    return matched


def evaluate(command: str, cwd: Path, interpreters: tuple[str, ...]) -> list[tuple[int, str]]:
    """`(rule number, repo-relative path)` for every match of the three rules."""
    tokens = tokenize(command)
    if not tokens:
        return []
    command_segments = segments(tokens)
    if not command_segments:
        return []
    hits: list[tuple[int, str]] = []
    hits.extend((1, rel) for rel in match_sed_in_place(command_segments))
    hits.extend((2, rel) for rel in match_truncating_redirect(tokens, cwd))
    hits.extend((3, rel) for rel in match_inline_interpreter(command, command_segments, interpreters))
    return hits


def deny_reason(rule: int, path: str) -> str:
    """CA-19: names the rule and the allowed route, and says nothing about
    stopping the mandate -- acting on the block is the guard working."""
    return f"regla {rule}: {RULES[rule]} (`{path}`). {ALLOWED_ROUTE}."


def main() -> int:
    raw_stdin = sys.stdin.read()
    payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    if not isinstance(payload, dict):
        return EXIT_OK

    tool_input = payload.get("tool_input") or {}
    command = tool_input.get("command") if isinstance(tool_input, dict) else None
    if not command:
        return EXIT_OK

    cwd = Path(str(payload.get("cwd") or REPO_ROOT))
    hits = evaluate(str(command), cwd, load_interpreters())
    if not hits:
        return EXIT_OK

    # D-16: only when **every** matched path is exempt does the guard stay
    # silent. One path outside the exempt trees (`_plan-maestro.md`, another
    # unit) still blocks, inside the pilot too.
    exempt_roots = _exempt_roots()
    outside = [hit for hit in hits if not is_exempt(hit[1], exempt_roots)]
    if not outside:
        return EXIT_OK

    rule, path = outside[0]
    reason = deny_reason(rule, path)
    _append(
        REJECTIONS_LOG,
        json.dumps(
            {
                "ts": _now(),
                "session_id": payload.get("session_id", ""),
                "tool": payload.get("tool_name", "Bash"),
                "path": path,
                "rule": f"regla {rule}",
            },
            ensure_ascii=False,
        ),
    )
    print(
        json.dumps(
            {
                "hookSpecificOutput": {
                    "hookEventName": "PreToolUse",
                    "permissionDecision": "deny",
                    "permissionDecisionReason": reason,
                }
            },
            ensure_ascii=False,
        )
    )
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # CA-22c: fail-open, the only broad catch of this file
        _log_failure(traceback.format_exc().replace("\n", " | "))
        sys.exit(EXIT_OK)
