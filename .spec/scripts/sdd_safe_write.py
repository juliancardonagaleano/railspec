#!/usr/bin/env python3
"""`PostToolUse` safe-write loop for SDD artifacts (unit 0132, G3; CA-01..CA-06, CA-10).

What it does
------------
Reads the `PostToolUse` payload Claude Code delivers on stdin (`session_id`,
`tool_name`, `tool_input`, ...), looks at `tool_input.file_path`, and -- only
when every condition below holds -- rewrites the just-written target in
**canonical form** (block style, indent 2, literal `null`/`true`/`false`, no
flow style, stable key order, comments preserved) before running
`validate_mandate.py` on it. If the validator exits non-zero, the loop emits
`{"decision": "block", ...}` on **stdout** (or retries when the offending code
is in `FIXABLE_BY_FORMAT`). Every run appends one line to
`.spec/.usage/sdd-safe-write.log`.

"The canonical form" is the one rule set exposed by
`sdd_safe_write_canonical.normalize_canonical`; "the closed set of codes" is
the one validator exposes via `--codigos`. This module owns neither: it is the
thin coordinator between them (CA-01, `pol-dev-un-artefacto-por-archivo`).

Acts only if all five hold
--------------------------
(a) the payload is a JSON object with a non-empty `tool_input.file_path`
    (CA-08: same matcher as the guard, no surprises);
(b) the path, resolved absolutely and made relative to `REPO_ROOT`, sits
    inside this repo (a path outside is not ours and exits 0 silently);
(c) the suffix is `.yaml` -- the loop is **no-op on `.md`** (no-op for
    `mandato.md`; the guard still catches it);
(d) the basename, the tree and the absence of exemption match the same
    conditions `guard_written_state_shape.py` enforces on its matcher
    `Edit|Write|MultiEdit` (the two hooks run in the same matcher; the loop
    **precedes** the guard, per decision D4);
(e) for `_estado.yaml` (the only `.yaml` basename that survives), `modo` is
    `supervisado` or `desatendido` -- the canonical "I am being
    written by the loop" marker, same as the guard.

Outside those conditions it terminates with no decision and no write.

Decision protocol
-----------------
- Validator exit 0 -> `{}` on stdout (no `decision`); append `<ts> ok <rel>`
  to the log.
- Validator exit != 0 with codes intersecting `FIXABLE_BY_FORMAT` -> retry,
  capped by `SDD_SAFE_WRITE_MAX_RETRIES` (default 3, decision D2).
- Validator exit != 0 with codes **outside** `FIXABLE_BY_FORMAT` (semantic)
  -> `{"decision": "block", "reason": ...}` on stdout; append
  `<ts> block <rel> códigos:<codes>` to the log.
- Decision travels as JSON on stdout, **never** as an exit code: the shell
  wrapper of `.claude/settings.json` ends in `|| true` (convention inherited
  from unit 0113), which would absorb any non-zero exit. Smoke-tested under
  `claude -p` before this file was written (unit 0113, T12, D-8).

With `FIXABLE_BY_FORMAT = frozenset()` (CA-13) the retry branch is dormant:
the loop runs in **"always-normalize, no style retry"** mode (CA-02) -- one
normalization, one validator run, one block on the first non-zero exit.
Promoting a code into `FIXABLE_BY_FORMAT` is a human decision that lives in
a future SDD unit; the loop never decides this on its own
(`pri-ia-cero-inferencia-implicita`).

Atomic mutation (CA-10)
-----------------------
The rewrite uses `tempfile.NamedTemporaryFile(delete=False, prefix=f".{name}.")`
+ `handle.flush()` + `os.fsync()` + `os.replace(tmp, target)`. If the process
crashes (or `os.replace` is mocked to `KeyboardInterrupt`, see G4 test) between
the write to the tempfile and the replace, the original `target` is left
untouched; the tempfile is unlinked on the failure branch. Pattern from
`usage/ledger.py:108-137` and `usage/baseline.py:418-432` (CA-28).

Fail-open (CA-22c)
------------------
Any exception of this hook's own is written to `.spec/.usage/sdd-safe-write.log`
(traceback in one line, replaced `\\\\n` with ` | `) and the tool is **not**
blocked -- a block only ever happens because a rule was infringed, never
because the loop broke. What fails *before* this module runs at all -- no
`python3` on `PATH`, a missing `validate_mandate.py` next to it -- is the
shell wrapper's job, as in unit 0113: the `2>>` redirection in
`.claude/settings.json` captures whatever the interpreter itself prints and
the `|| true` at the end guarantees exit 0 at the process level.

It does **not** restore the file: when the loop blocks, the just-written
canonical form (or the original, if canonicalization was a no-op) is left on
disk. Restoring would be a hidden write of its own.

No network (CA-22a): stdlib only, plus the repo-local `validate_mandate`,
`_common` and `sdd_safe_write_canonical` (the latter authored in G2, holding
`normalize_canonical`, `FIXABLE_BY_FORMAT`, `EXPECTED_CODE_COUNT`).

No PyYAML: `test_scripts_naming_and_strict_mode.py:60-62, 285-326` allows
only stdlib + `{"validate_mandate", "_common"}`, and `yaml.safe_dump` would
discard the comments and reorder the keys of `_estado.yaml`
(`pol-ia-no-embeber-conocimiento`, plan.md decision #1).
"""

from __future__ import annotations

import contextlib
import datetime
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

sys.dont_write_bytecode = True

#: `<repo>` by self-location: this file sits at `<repo>/.spec/scripts/`, so two
#: parents up is the repo root. Same base as `guard_written_state_shape.py:75`
#: and `usage/paths.py::default_repo_root` (`parents[3]` from `usage/paths.py`),
#: which is what makes `.spec/.usage/sdd-safe-write.log` below and K6's
#: `source.file` in `indicators.json` the very same path. It is deliberately
#: **not** `cwd` of the payload nor `CLAUDE_PROJECT_DIR` -- those only decide
#: *which* script the harness runs.
REPO_ROOT = Path(__file__).resolve().parents[2]

sys.path.insert(0, str(REPO_ROOT / ".spec" / "scripts"))

from sdd_safe_write_canonical import (  # noqa: E402
    FIXABLE_BY_FORMAT,
    normalize_canonical,
)
from validate_mandate import read_unit_state  # noqa: E402

USAGE_DIR = REPO_ROOT / ".spec" / ".usage"
LOG_FILE = USAGE_DIR / "sdd-safe-write.log"
VALIDATOR = REPO_ROOT / ".spec" / "scripts" / "validate_mandate.py"

#: Only exit code this hook ever uses (CA-10 iii, D-8).
EXIT_OK = 0

#: Loop retry budget (decision D2, plan.md decision #4): the human-readable
#: cap on retries when a validator code intersects `FIXABLE_BY_FORMAT`. Empty
#: today (CA-13) means the loop never reaches the retry branch.
MAX_RETRIES_ENV = "SDD_SAFE_WRITE_MAX_RETRIES"
MAX_RETRIES_DEFAULT = 3

#: Condition (a) -- only this basename in the `.yaml` family survives the
#: extension filter; the rest are caught by `guard_written_state_shape.py`.
WATCHED_YAML_BASENAME = "_estado.yaml"

#: Condition (b) of `guard_written_state_shape.py:90-102`. Byte-identical with
#: `guard_written_state_shape.py` by decision of plan.md § Decisiones de diseño
#: -- the two hooks are separate processes with no shared module of their own,
#: and `_common.py` belongs to the ritual scripts, not to the hooks.
WATCHED_BASENAMES = ("_estado.yaml", "mandato.md")
UNIT_TREE = re.compile(r"^\.spec/units/[0-9]{4}[a-z]?-[^/]+/")
PLAN_TREE = re.compile(r"^\.spec/planes/[^/]+/")
EXCLUDED_TREES = (
    ".spec/_plantillas/",
    ".spec/_fixtures/",
    ".spec/scripts/usage/tests/fixtures/",
)

#: Condition (d) of `guard_written_state_shape.py:104-113`, D-16: reserved
#: prefix of the pilot's test unit and the runner-exported variable. Same
#: cap (one unit directory) -- an exemption never widens past `.spec/units/`.
#: Byte-identical with `guard_written_state_shape.py` by decision of
#: plan.md § Decisiones de diseño.
PILOT_UNIT_TREE = ".spec/units/9109-"
EXEMPT_TREE_ENV = "SDD_GUARD_EXEMPT_TREE"
EXEMPT_TREE_ROOT = ".spec/units/"

#: Splitter of the validator's `códigos: ...` line, used to recover the codes
#: emitted by `validate_mandate.py` for the retry / block branch.
_CODES_LINE = re.compile(r"^códigos: (.*)$", re.MULTILINE)


def _now() -> str:
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _append(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line + "\n")


def _log_failure(message: str) -> None:
    """CA-22c: every failure of this hook's own leaves a motive here and
    blocks nothing. Never raises: a loop that cannot log still must not
    block the tool the user just ran."""
    with contextlib.suppress(OSError):
        _append(LOG_FILE, f"{_now()} sdd_safe_write: {message}")


def _exempt_roots() -> list[str]:
    """Repo-relative prefixes exempt under D-16.

    `$SDD_GUARD_EXEMPT_TREE` is honoured **only** when it is non-empty and its
    normalized value falls under `.spec/units/`; any other value -- empty, `.`,
    a path outside that tree -- is ignored and leaves an `exempt-tree-ignored`
    line in `.spec/.usage/sdd-safe-write.log`. Without that cap an
    `export SDD_GUARD_EXEMPT_TREE=` would make the prefix match every path and
    silently disable the loop over the whole repo; with it, a badly exported
    exemption is visible in the log and never widens past one unit.

    Byte-identical with `guard_written_state_shape.py::_exempt_roots` by
    decision of plan.md § Decisiones de diseño -- the two hooks are separate
    processes with no shared module of their own, and `_common.py` belongs to
    the ritual scripts, not to the hooks.
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
    so K6 never counts the pilot.

    Byte-identical with `guard_written_state_shape.py::is_exempt` by decision
    of plan.md § Decisiones de diseño.
    """
    return any(rel_path.startswith(root) for root in exempt_roots)


def watched_target(rel_path: str, basename: str) -> bool:
    """Conditions (a) and (b) of the guard, replicated byte-identical.

    Byte-identical with `guard_written_state_shape.py::watched_target` by
    decision of plan.md § Decisiones de diseño. The narrowing to
    `_estado.yaml` only happens in `main()` via the extension filter (the
    `.md` branch is a no-op here per spec § Alcance "No incluye"); in
    practice, with the extension filter applied upstream, only
    `_estado.yaml` ever reaches this function with a `True` answer.
    """
    if basename not in WATCHED_BASENAMES:
        return False
    if any(rel_path.startswith(tree) for tree in EXCLUDED_TREES):
        return False
    return bool(UNIT_TREE.match(rel_path) or PLAN_TREE.match(rel_path))


def validator_command(target: Path, basename: str) -> list[str]:
    """`--plan` for a master plan, `--unidad` for a unit's own two files. One
    single target per run, never the whole backlog (CA-22b budget).

    Byte-identical with `guard_written_state_shape.py::validator_command` by
    decision of plan.md § Decisiones de diseño. The `--plan` branch is
    unreachable in this loop (the `.md` extension is filtered upstream);
    it is kept for byte-identity with the guard and so that a future
    re-widening of the loop does not silently lose the plan form.
    """
    return [sys.executable, str(VALIDATOR), "--unidad", str(target.parent)]


def atomic_write_text(target: Path, text: str) -> None:
    """Atomic rewrite via `tempfile + os.replace` (CA-10, CA-28).

    The tempfile lives in the same directory as `target` so `os.replace` is
    atomic on the same filesystem (cross-device `os.replace` raises). On any
    exception raised after the tempfile is created -- including a mocked
    `KeyboardInterrupt` from G4's atomic-write test -- the tempfile is
    unlinked so the directory does not accumulate `.<filename>.*` ghosts.

    Pattern from `usage/ledger.py:108-137` (CA-28, `write_session`) and
    `usage/baseline.py:418-432` (`_atomic_write_json`), adapted to plain
    text: no `json.dump`, no `sort_keys` -- `text` is the already-canonical
    YAML the loop just produced.
    """
    with tempfile.NamedTemporaryFile(
        mode="w",
        encoding="utf-8",
        delete=False,
        prefix=f".{target.name}.",
        dir=target.parent,
    ) as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
        tmp_path = handle.name
    try:
        os.replace(tmp_path, target)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
        raise


def _parse_max_retries() -> int:
    """Read `SDD_SAFE_WRITE_MAX_RETRIES` (decision D2, default 3).

    The variable has no `IARK_` prefix (human decision: it is internal to the
    loop, not a PCE / governance knob). On `ValueError` the default is used
    and a warning line lands in `.spec/.usage/sdd-safe-write.log` -- the
    loop never raises out of here.
    """
    raw = os.environ.get(MAX_RETRIES_ENV, str(MAX_RETRIES_DEFAULT))
    try:
        return int(raw)
    except ValueError:
        _log_failure(
            f"warn {MAX_RETRIES_ENV}={raw!r} not int; using {MAX_RETRIES_DEFAULT}"
        )
        return MAX_RETRIES_DEFAULT


def _codes_from_validator_stdout(stdout: str) -> list[str]:
    """Recover the codes emitted by `validate_mandate.py` from its `códigos:`
    line, matching the guard's parsing of the same field."""
    match = _CODES_LINE.search(stdout)
    return match.group(1).split() if match else []


def block_reason(rel_path: str, codes: list[str]) -> str:
    """Reason string the harness reads on stdout when the loop blocks.

    Distinct from the guard's `block_reason`: the loop did normalize first,
    so the reason names what is left to fix (semantic, not format).
    """
    codes_text = " ".join(codes) if codes else "(sin códigos reportados)"
    return (
        f"sdd-safe-write: validate_mandate.py rechaza {rel_path} con códigos "
        f"semánticos {codes_text}. El bucle normalizó el formato canónico; "
        f"el problema no es de forma, es de contenido. Corregir el contenido "
        f"en el mismo turno."
    )


def _run_loop(
    target: Path,
    rel_path: str,
    basename: str,
    payload: dict,
) -> None:
    """Normalize, write atomically, validate, decide. Always returns; never
    raises out of itself -- the broad `except` in `__main__` is the safety
    net for bugs in this module (CA-22c)."""
    max_retries = _parse_max_retries()
    attempt = 0

    while True:
        text = target.read_text(encoding="utf-8")
        normalized = normalize_canonical(text)
        if normalized != text:
            atomic_write_text(target, normalized)

        result = subprocess.run(
            validator_command(target, basename),
            capture_output=True,
            text=True,
            check=False,  # a non-zero exit is the signal, not an error
        )

        if result.returncode == 0:
            _append(LOG_FILE, f"{_now()} ok {rel_path}")
            print("{}")
            return

        codes = _codes_from_validator_stdout(result.stdout)
        fixable = set(codes) & FIXABLE_BY_FORMAT
        attempt += 1
        if fixable and attempt <= max_retries:
            continue

        # Either no code is fixable-by-format (FIXABLE_BY_FORMAT empty today),
        # or the retry budget is exhausted: block.
        codes_text = " ".join(codes)
        _append(LOG_FILE, f"{_now()} block {rel_path} códigos:{codes_text}")
        print(
            json.dumps(
                {
                    "decision": "block",
                    "reason": block_reason(rel_path, codes),
                },
                ensure_ascii=False,
            )
        )
        return


def main() -> int:
    raw_stdin = sys.stdin.read()
    try:
        payload = json.loads(raw_stdin) if raw_stdin.strip() else {}
    except json.JSONDecodeError:
        return EXIT_OK

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

    # Condition (c): extension filter -- the loop is no-op on `.md`. The
    # guard still catches `mandato.md` behind us;
    # the loop is the canonical-form pass for `_estado.yaml` only.
    suffix = target.suffix
    if suffix == ".md":
        return EXIT_OK
    if suffix != ".yaml":
        return EXIT_OK  # defensive: only .yaml ever reaches the validator

    basename = target.name
    if not watched_target(rel_path, basename):
        return EXIT_OK
    if is_exempt(rel_path, _exempt_roots()):
        return EXIT_OK

    # Condition (e): only supervisado/desatendido units' own
    # state files. For `.yaml` only `_estado.yaml` survives conditions
    # (a)..(d); this is the equivalent of the guard's condition (c).
    if (
        basename == WATCHED_YAML_BASENAME
        and read_unit_state(target.parent).get("modo") not in ("supervisado", "desatendido")
    ):
        return EXIT_OK

    _run_loop(target, rel_path, basename, payload)
    return EXIT_OK


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception:  # CA-22c: fail-open, the only broad catch of this file
        _log_failure(traceback.format_exc().replace("\n", " | "))
        sys.exit(EXIT_OK)
