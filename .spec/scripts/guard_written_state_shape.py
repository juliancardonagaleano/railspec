#!/usr/bin/env python3
"""`PostToolUse` guard: a mandate file was just written in a shape the validator
rejects (unit 0114, CA-17/CA-18; local, temporary layer).

What it does
------------
Reads the `PostToolUse` payload Claude Code delivers on stdin (`session_id`,
`cwd`, `hook_event_name`, `tool_name`, `tool_input`, `tool_response`), looks at
`tool_input.file_path` and -- only when every condition below holds -- runs
`validate_mandate.py` over the just-written target. If the validator exits non
zero, it emits `{"decision": "block", "reason": ...}` on **stdout** with the
validator's literal codes and their location, and appends one JSONL line to
`.spec/.usage/guard-rejections-post-write.jsonl`.

"The shape" is, by single definition, whatever `validate_mandate.py` accepts or
rejects (CA-17, `pri-gob-fuente-verdad-unica`): this file owns **no** taxonomy of
its own. It reads `modo:` through `read_unit_state()` of that same validator and
gets the codes by running it, never by re-deriving them.

Acts only if all four hold
--------------------------
(a) the basename is one of `WATCHED_BASENAMES`;
(b) the path, relative to `REPO_ROOT`, falls under `.spec/units/<NNNN-slug>/` or
    `.spec/planes/<id>/`, and **not** under `.spec/_plantillas/**`,
    `.spec/_fixtures/**` or `.spec/scripts/usage/tests/fixtures/**` -- templates
    are not valid mandates by design and those two fixture trees hold ~20 states
    and mandates that are invalid **on purpose**;
(c) for `_estado.yaml`, `read_unit_state()` says `modo: supervisado` or
    `modo: desatendido` (`plan-maestro.md`/`mandato.md` carry
    no such condition: they **are** the supervised mandate);
(d) the path is not exempt under D-16 -- see `_exempt_roots()`.

Outside those conditions it terminates with no decision and no write.

Exit codes
----------
`EXIT_OK` (0), always. The decision travels as JSON on stdout, never as an exit
code: the shell wrapper of `.claude/settings.json` ends in `|| true` (convention
inherited from unit 0113), which would absorb any non-zero exit. Smoke-tested
under `claude -p` before this file was written (unit 0114, T12, D-8).

Fail-open (CA-22c): any exception of this hook's own is written to
`.spec/.usage/guard-failures.log` and the tool is **not** blocked -- a block only
ever happens because a rule was infringed, never because the guard broke. What
fails *before* this module runs at all -- no `python3` on `PATH`, a missing
`validate_mandate.py` next to it -- is the shell wrapper's job, as in unit 0113:
`sh -c '... 2>>guard-failures.log || true'` sends whatever the interpreter itself
prints to the same log and guarantees exit 0 at the process level.

It does **not** restore the file (S-5): reporting the rejection in the same call
is the point; undoing a write would be a hidden write of its own.

No network (CA-22a): stdlib only, plus the repo-local `validate_mandate`.
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import subprocess
import sys
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

#: `<repo>` by self-location: this file sits at `<repo>/.spec/scripts/`, so two
#: parents up is the repo root. Same base as `usage/paths.py::default_repo_root`
#: (`parents[3]` from `usage/paths.py`), which is what makes `REJECTIONS_LOG`
#: below and K6's `source.file` in `indicators.json` the very same path. It is
#: deliberately **not** `cwd` of the payload nor `CLAUDE_PROJECT_DIR` -- those
#: only decide *which* script the harness runs.
REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / ".spec" / "scripts"))

from validate_mandate import read_unit_state  # noqa: E402

USAGE_DIR = REPO_ROOT / ".spec" / ".usage"
REJECTIONS_LOG = USAGE_DIR / "guard-rejections-post-write.jsonl"
FAILURES_LOG = USAGE_DIR / "guard-failures.log"
VALIDATOR = REPO_ROOT / ".spec" / "scripts" / "validate_mandate.py"

#: Only exit code this hook ever uses (CA-10 iii, D-8).
EXIT_OK = 0

#: Condition (a).
WATCHED_BASENAMES = ("_estado.yaml", "plan-maestro.md", "mandato.md")

#: Condition (b): the two trees that hold real units and real mandates.
UNIT_TREE = re.compile(r"^\.spec/units/[0-9]{4}[a-z]?-[^/]+/")
PLAN_TREE = re.compile(r"^\.spec/planes/[^/]+/")

#: Condition (b), the other half: templates and the fixture trees that carry
#: invalid states and mandates on purpose.
EXCLUDED_TREES = (
    ".spec/_plantillas/",
    ".spec/_fixtures/",
    ".spec/scripts/usage/tests/fixtures/",
)

#: Condition (d), D-16: reserved prefix of the pilot's test unit -- the same one
#: the runner classifies as such in `supervised-test.sh`.
PILOT_UNIT_TREE = ".spec/units/9109-"

#: Condition (d), D-16: the runner exports this as `$TEST_UNIT` so a test unit
#: named otherwise stays exempt too.
EXEMPT_TREE_ENV = "IARK_GUARD_EXEMPT_TREE"

#: Cap on that variable: an exemption never widens past one unit directory.
EXEMPT_TREE_ROOT = ".spec/units/"

_CODES_LINE = re.compile(r"^códigos: (.*)$", re.MULTILINE)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _log_failure(message: str) -> None:
    """CA-22c: every failure of this hook's own leaves a motive here and blocks
    nothing. Never raises: a guard that cannot log still must not block."""
    with contextlib.suppress(OSError):
        _append(FAILURES_LOG, f"{_now()} guard_written_state_shape: {message}")


def _exempt_roots() -> list[str]:
    """Repo-relative prefixes exempt under D-16.

    `$IARK_GUARD_EXEMPT_TREE` is honoured **only** when it is non-empty and its
    normalized value falls under `.spec/units/`; any other value -- empty, `.`,
    a path outside that tree -- is ignored and leaves an `exempt-tree-ignored`
    line in `guard-failures.log`. Without that cap an
    `export IARK_GUARD_EXEMPT_TREE=` would make the prefix match every path and
    silently disable the guard over the whole repo; with it, a badly exported
    exemption is visible in the failures log and never widens past one unit.

    Kept byte-identical (on purpose) in `guard_bash_spec_writes.py`: the two
    hooks are separate processes with no shared module of their own, and
    `_common.py` belongs to the ritual scripts, not to the guards.
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
    """Whether a repo-relative path falls under one of the D-16 exempt trees.

    The effective exemption is **silent** -- no line in either rejections log --
    so K6 never counts the pilot."""
    return any(rel_path.startswith(root) for root in exempt_roots)


def watched_target(rel_path: str, basename: str) -> bool:
    """Conditions (a) and (b)."""
    if basename not in WATCHED_BASENAMES:
        return False
    if any(rel_path.startswith(tree) for tree in EXCLUDED_TREES):
        return False
    return bool(UNIT_TREE.match(rel_path) or PLAN_TREE.match(rel_path))


def validator_command(target: Path, basename: str) -> list[str]:
    """`--plan` for a master plan, `--unidad` for a unit's own two files. One
    single target per run, never the whole backlog (CA-22b budget).

    `--plan` gets the plan **id** (the directory name), not the file path: a
    directory argument makes the validator look for members under
    `<dir>/units/` (its fixture form), while the id resolves members under
    `.spec/units/`, which is where a real plan's members live. Condition (b)
    already guarantees the file sits at `.spec/planes/<id>/plan-maestro.md`.
    """
    if basename == "plan-maestro.md":
        return [sys.executable, str(VALIDATOR), "--plan", target.parent.name]
    return [sys.executable, str(VALIDATOR), "--unidad", str(target.parent)]


def block_reason(rel_path: str, stdout: str) -> str:
    """The validator's own output, verbatim: its `código  dónde  — detalle`
    lines plus its `códigos:` line (CA-17/CA-18 ask for the literal codes **with
    their location**). No second taxonomy, no rewording."""
    detail = [line for line in stdout.splitlines() if line.startswith("  ")]
    codes_line = _CODES_LINE.search(stdout)
    parts = [f"{rel_path}: `validate_mandate.py` rechaza la forma recién escrita."]
    parts.extend(detail)
    if codes_line:
        parts.append("códigos: " + codes_line.group(1))
    parts.append(
        "Corregir la forma en el mismo turno; el archivo no se restauró."
    )
    return "\n".join(parts)


def main() -> int:
    raw_stdin = sys.stdin.read()
    payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    if not isinstance(payload, dict):
        return EXIT_OK

    tool_input = payload.get("tool_input") or {}
    raw_path = tool_input.get("file_path") if isinstance(tool_input, dict) else None
    if not raw_path:
        return EXIT_OK

    target = Path(str(raw_path))
    if not target.is_absolute():
        target = REPO_ROOT / target
    try:
        rel_path = target.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return EXIT_OK  # outside the repo: not ours

    basename = target.name
    if not watched_target(rel_path, basename):
        return EXIT_OK
    if is_exempt(rel_path, _exempt_roots()):
        return EXIT_OK

    if (
        basename == "_estado.yaml"
        and read_unit_state(target.parent).get("modo") not in ("supervisado", "desatendido")
    ):
        return EXIT_OK

    result = subprocess.run(
        validator_command(target, basename),
        capture_output=True,
        text=True,
        check=False,  # a non-zero exit is the signal, not an error: inspected below
    )
    if result.returncode == 0:
        return EXIT_OK

    codes_line = _CODES_LINE.search(result.stdout)
    _append(
        REJECTIONS_LOG,
        json.dumps(
            {
                "ts": _now(),
                "session_id": payload.get("session_id", ""),
                "tool": payload.get("tool_name", ""),
                "path": rel_path,
                "rule": "validate_mandate " + validator_command(target, basename)[2],
                "codes": codes_line.group(1).split() if codes_line else [],
            },
            ensure_ascii=False,
        ),
    )
    print(
        json.dumps(
            {"decision": "block", "reason": block_reason(rel_path, result.stdout)},
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
