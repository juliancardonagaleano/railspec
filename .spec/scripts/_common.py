"""Shared utilities for the `.spec/` scripts (local, temporary layer, unit 0109a).

Pattern origin: `.spec/units/0068-plan-maestro-de-oleadas/scripts/validar.py`
(`:21` `FALLOS` accumulator, `:44-45` `fallo()`, `:48-52` `unidades()` with the
`^(core-)?\\d{4}-?` regex, `:55-57` `campo()`). That unit is **closed**: it is not
touched nor imported; these ~10 lines are rewritten here because `0109b` will reuse
them from its own orchestration.

No external dependencies: stdlib only (`re`, `hashlib`, `pathlib`, `subprocess`, `os`).

Unit 0114 adds three functions (`dirty_paths`, `resume_point`, `codes_from_output`)
and changes no existing signature (D-13).

Adds `resolve_protocolo_datos_path`/`load_protocolo_datos`: the shared
flag > env > archivo-de-reposo resolution for `.spec/protocolo-datos.yaml`,
reused by `validate_protocol_drift.py` and `test_subset.py` so both read the
same destination-declared data instead of each carrying its own copy of the
precedence logic.
"""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from pathlib import Path

# --- Accumulators -------------------------------------------------------------------
# FAILURES are errors (they count toward exit != 0); WARNINGS are advisories (they are
# printed but do not affect the exit code or the reported set of codes — CA-21, S-16).
FAILURES: list[tuple[str, str, str]] = []   # (code, location, detail)
WARNINGS: list[tuple[str, str, str]] = []


def reset() -> None:
    """Clears the accumulators (the auxiliary modes and the tests reuse them)."""
    FAILURES.clear()
    WARNINGS.clear()


def add_failure(code: str, location: str, detail: str = "") -> None:
    FAILURES.append((code, location, detail))


def add_warning(code: str, location: str, detail: str = "") -> None:
    WARNINGS.append((code, location, detail))


def codes_from(accumulator: list[tuple[str, str, str]]) -> list[str]:
    """Reported set of codes, sorted and de-duplicated (union rule, CA-21)."""
    return sorted({c for c, _, _ in accumulator})


# --- Reading `_estado.yaml` (regex, no PyYAML: plan.md § Decisiones) -----------------

_UNIT_ID = re.compile(r"^(core-)?\d{4}[a-z]?-?")


def units(root: Path) -> list[Path]:
    """Unit directories under `root` (same criterion as 0068/validar.py:48-52)."""
    if not root.is_dir():
        return []
    return sorted(
        d for d in root.iterdir() if d.is_dir() and _UNIT_ID.match(d.name)
    )


def field(text: str, name: str) -> str:
    """Scalar value of a top-level flat YAML field. "" if absent."""
    m = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return ""
    return m.group(1).split("#")[0].strip().strip('"').strip("'")


def field_list(text: str, name: str) -> list[str] | None:
    """Items of a YAML field that comes as a list.

    Returns `None` when the field is not a list (or is absent). Supports both
    forms: inline (`campo: ["a", "b"]`) and block (`campo:` + `  - a`). This is
    what lets `unidad-mandato-doble` be detected without PyYAML (CA-04).
    """
    m = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return None
    rest = m.group(1).split("#")[0].strip()
    if rest.startswith("[") and rest.endswith("]"):
        inside = rest[1:-1].strip()
        if not inside:
            return []
        return [x.strip().strip('"').strip("'") for x in inside.split(",")]
    if rest:
        return None
    # Block form: bullets indented right below.
    lines = text[m.end():].splitlines()
    items: list[str] = []
    for line in lines[1:] if lines and lines[0] == "" else lines:
        if not line.strip():
            continue
        match = re.match(r"^[ \t]+-[ \t]*(.*)$", line)
        if not match:
            break
        items.append(match.group(1).split("#")[0].strip().strip('"').strip("'"))
    return items if items else None


def _block_item_kv(text: str) -> tuple[str, str] | None:
    """Parses one `key: value` fragment of a block-list item line.

    Shared by the item-start line (after the leading `- `) and its
    continuation lines. Strips a trailing `# ...` comment and surrounding
    quotes from the value, same convention as `field()`. Returns `None`
    when `text` has no `key:` shape (e.g. a comment-only continuation line).
    """
    if ":" not in text:
        return None
    key, _, value = text.partition(":")
    key = key.strip()
    if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
        return None
    return key, value.split("#")[0].strip().strip('"').strip("'")


def block_list_of_dicts(text: str, name: str) -> list[dict[str, str]]:
    """Items of a top-level block-style YAML field whose value is a list of
    flat dicts (`name:` followed by `  - key: value` items, each with
    optional indented continuation lines `    key2: value2`).

    Same no-PyYAML technique as `gate_declared_subagents`
    (`.spec/scripts/usage/unit_state.py:276-295`): regex line-by-line, no
    recursive parser. Returns `[]` when `name` is absent, is an inline
    scalar (including `name: []`), or has no items. Values in single or
    double quotes come back unquoted; a trailing `# ...` comment on any
    line is dropped.
    """
    m = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return []
    rest = m.group(1).split("#")[0].strip()
    if rest:
        # Inline scalar, or an inline empty list (`name: []`): no items.
        return []
    items: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    dash_indent: int | None = None
    for line in text[m.end():].splitlines():
        if not line.strip():
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        stripped = line.strip()
        dash_match = re.match(r"^-[ \t]*(.*)$", stripped)
        if dash_match is not None and (dash_indent is None or indent == dash_indent):
            dash_indent = indent
            if current is not None:
                items.append(current)
            current = {}
            kv = _block_item_kv(dash_match.group(1))
            if kv:
                current[kv[0]] = kv[1]
            continue
        if dash_indent is None or indent <= dash_indent or current is None:
            break
        kv = _block_item_kv(stripped)
        if kv:
            current[kv[0]] = kv[1]
    if current is not None:
        items.append(current)
    return items


# --- Generic top-level block walker (no PyYAML) ---------------------------------------
# Shared by `validate_gate_budget.py` (locating `gates:` and its phase blocks) and
# `validate_model_catalog.py` (locating `gates:`/`modelo_ejecucion:` before walking them
# for `modelo` entries). Each script used to reimplement its own copy of this same
# top-level-key walk; one shared primitive replaces both copies.

_TOP_LEVEL_KEY = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")


def top_level_block(text: str, name: str) -> str:
    """Indented body under a top-level `name:` key.

    "" when `name` is absent, or is an inline scalar or inline flow value
    (`name: value`, `name: {}`, `name: []`) — only a bare `name:` followed by
    indented lines counts as a block. The body keeps each line's own
    indentation; a blank line inside the block is kept, a line back at column
    0 (the next top-level key) ends it.
    """
    m = re.search(rf"^{re.escape(name)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return ""
    rest = m.group(1).split("#")[0].strip()
    if rest:
        return ""
    remainder = text[m.end():]
    if remainder.startswith("\n"):
        remainder = remainder[1:]
    body: list[str] = []
    for line in remainder.splitlines():
        if not line.strip():
            body.append(line)
            continue
        if len(line) - len(line.lstrip(" \t")) <= 0:
            break
        body.append(line)
    return "\n".join(body)


def child_blocks(block: str) -> list[tuple[str, str]]:
    """`(key, body)` pairs of the direct children of `block`.

    "Direct" means indented at `block`'s shallowest non-blank line — a child
    at a deeper indent (including a `- ...` list item) is part of the body of
    the sibling key above it, never a child of its own. `[]` when `block` has
    no non-blank line.
    """
    lines = block.splitlines()
    indents = [len(line) - len(line.lstrip(" \t")) for line in lines if line.strip()]
    if not indents:
        return []
    base = min(indents)
    result: list[tuple[str, str]] = []
    current_key: str | None = None
    buffer: list[str] = []
    for line in lines:
        if not line.strip():
            if current_key is not None:
                buffer.append(line)
            continue
        indent = len(line) - len(line.lstrip(" \t"))
        if indent == base:
            m = _TOP_LEVEL_KEY.match(line.strip())
            if m is not None:
                if current_key is not None:
                    result.append((current_key, "\n".join(buffer)))
                current_key = m.group(1)
                buffer = []
                continue
        if current_key is not None:
            buffer.append(line)
    if current_key is not None:
        result.append((current_key, "\n".join(buffer)))
    return result


# --- Dates ----------------------------------------------------------------------------

_ISO = re.compile(
    r"^\d{4}-\d{2}-\d{2}([T ]\d{2}:\d{2}(:\d{2})?(Z|[+-]\d{2}:?\d{2})?)?$"
)


def iso_date(value: str) -> str:
    """Returns the date if it is ISO-8601 (day, or day+time); "" if it is not.

    ISO dates sort lexicographically, so the returned string compares directly
    with `<` / `>` without converting to datetime.
    """
    v = (value or "").strip().strip("`")
    return v if _ISO.match(v) else ""


def date_key(value: str, *, end_of_day: bool = False) -> str:
    """Comparison key that normalizes granularity before comparing two ISO dates.

    A day-only date (`2026-09-18`) denotes the **whole** day, so comparing it raw
    against a timestamp of that same day (`2026-09-18T09:00Z`) is wrong in one
    direction or the other. The key pads it to the start of the day, or to its end
    when `end_of_day` — pick the side that makes the interval contain the day.
    Returns "" when the value is not ISO-8601.

    Chosen trade-off of `end_of_day` (code gate it.2, documented rather than changed):
    padding a day-only date to `23:59:59Z` makes a renewal that starts **later that
    same day** count as the one in force for an event dated only by its day — an
    unblock dated `2026-09-14` is attributed to a mandate entry starting at
    `2026-09-14T09:00Z` even if it actually happened at 08:00. Without the padding the
    opposite error occurs, and it is the worse one: the check would blame the previous
    author for an unblock the renewal's author signed. With a day-only date the order
    within the day is simply unknown; this side of the trade-off keeps `## Mandato`
    renewals from producing false `desbloqueo-no-autor` findings. Whoever needs the
    exact ordering writes the time, and then no padding applies.
    """
    v = iso_date(value)
    if not v:
        return ""
    if len(v) == 10:
        return v + ("T23:59:59Z" if end_of_day else "T00:00:00Z")
    return v


# --- Markdown: sections and entries ---------------------------------------------------

def sections(text: str) -> dict[str, str]:
    """`## Anchor` → body (up to the next `## `). Keeps the inner `### ` headers."""
    result: dict[str, str] = {}
    current: str | None = None
    buffer: list[str] = []
    for line in text.splitlines():
        if line.startswith("## "):
            if current is not None:
                result[current] = "\n".join(buffer)
            current = line.strip()
            buffer = []
        elif current is not None:
            buffer.append(line)
    if current is not None:
        result[current] = "\n".join(buffer)
    return result


def entries(body: str) -> list[tuple[str, dict[str, str], str]]:
    """`### <key>` entries of a registry section (S-22, plan.md § Decisiones).

    Returns `(key, {field: value}, raw_body)`; the fields come from the
    `- field: value` bullets hanging off the entry.
    """
    result: list[tuple[str, dict[str, str], str]] = []
    key: str | None = None
    buffer: list[str] = []
    for line in (body or "").splitlines():
        if line.startswith("### "):
            if key is not None:
                result.append((key, _bullets("\n".join(buffer)), "\n".join(buffer)))
            key = line[4:].strip()
            buffer = []
        elif key is not None:
            buffer.append(line)
    if key is not None:
        result.append((key, _bullets("\n".join(buffer)), "\n".join(buffer)))
    return result


_BULLET = re.compile(r"^([ \t]*)-[ \t]+(.*)$")
_KEY_VALUE = re.compile(r"^([^:\n]+):[ \t]*(.*)$")


def _indent_width(prefix: str) -> int:
    return len(prefix.expandtabs(4))


def _bullets(body: str) -> dict[str, str]:
    """`- field: value` bullets of an entry, level zero only.

    Three rules, each of them a defect this parser used to have:

    * The indentation of the **first** bullet anchors level zero. A deeper bullet
      belongs to the value of the bullet above it and never becomes a field of its
      own — otherwise a `  - revision: …` hanging off `- notas: …` silently
      replaced the entry's real `revision`.
    * The **first** occurrence of a key wins. A later bullet with the same name does
      not overwrite it.
    * A field whose inline value is empty but that hangs a **non-empty sub-list**
      counts as declared: its value is the sub-list joined with `, `. So
      `- alternativas:` + two sub-bullets is a filled field, not a missing one.
    """
    result: dict[str, str] = {}
    base: str | None = None
    pending: str | None = None          # level-zero key still waiting for its sub-list
    sublist: list[str] = []

    def flush() -> None:
        nonlocal pending, sublist
        if pending and sublist and not result.get(pending):
            result[pending] = ", ".join(sublist)
        pending, sublist = None, []

    for line in (body or "").splitlines():
        m = _BULLET.match(line)
        if m is None:
            if line.strip():            # prose closes any open sub-list
                flush()
            continue
        indent, rest = m.group(1), m.group(2)
        if base is None:
            base = indent
        if _indent_width(indent) > _indent_width(base):
            if pending:
                sublist.append(rest.split("#")[0].strip())
            continue
        flush()
        kv = _KEY_VALUE.match(rest)
        if kv is None:
            continue
        key = kv.group(1).strip().lower()
        if key in result:
            continue
        value = kv.group(2).strip()
        result[key] = value
        if not value:
            pending = key
    flush()
    return result


def bullets(body: str) -> dict[str, str]:
    """Loose `- field: value` bullets of a section without entries (e.g. Paralelismo)."""
    return _bullets(body or "")


# --- Normalization and hash -----------------------------------------------------------

def normalize(text: str) -> str:
    """Text normalized for the approval hash (S-24): no edge whitespace and no
    blank lines."""
    return "\n".join(
        line.strip() for line in (text or "").splitlines() if line.strip()
    )


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


# --- Unit 0114: git working tree, resume point, validator codes ----------------------

def _under_tree(path: str, tree: str) -> bool:
    """`path` is under `tree` exactly as the pilot runner's first two `case` branches.

    Parity is declared against `supervised-test.sh:496-504`
    (`case "$path" in "$TEST_UNIT"|"$TEST_UNIT"/*)`) and the same two branches of
    `ruta_permitida()` at `:852-861`. The runner's **other** branches
    (`.spec/units/9109-*`, `"$FX"/*`, `"$EVIDENCIA"/*`, `"$PLAN_MAESTRO"`) are widenings
    of its own, and this helper does **not** reproduce them: a prefix sibling such as
    `9109-prueba-supervisado-bis` is outside `9109-prueba-supervisado` here and inside for
    the runner's third branch.
    """
    tree = tree.rstrip("/")
    return path == tree or path.startswith(tree + "/")


def _porcelain_paths(repo_root: Path) -> list[str]:
    """Paths reported by `git status --porcelain -z`, repo-root relative.

    `-z` (NUL separated, no quoting) is what makes a path with spaces or a rename
    readable without un-quoting by hand: a rename/copy record carries **two** NUL
    fields, `XY <new>` then `<orig>`; the destination is the one that counts, same
    choice as `path_from_status()` in `supervised-test.sh:866-871`.
    """
    completed = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "-z"],
        capture_output=True, text=True, check=True,
    )
    fields = completed.stdout.split("\0")
    paths: list[str] = []
    index = 0
    while index < len(fields):
        record = fields[index]
        index += 1
        if not record:
            continue
        # Fixed layout: two status characters, one space, then the path.
        status, path = record[:2], record[3:]
        if not path:
            continue
        if "R" in status or "C" in status:
            index += 1               # skip the original path of a rename/copy
        paths.append(path)
    return paths


def dirty_paths(repo_root: Path, scoped_trees: list[str]) -> tuple[list[str], list[str]]:
    """Uncommitted paths split into (in scope, informational).

    `scoped_trees` are repo-root-relative paths — a directory (the unit tree) or a
    single file (the mandate). Membership uses `_under_tree`, whose parity with the
    pilot runner is declared there. Both lists come back sorted; a path that is under
    no declared tree is informational and never changes a verdict (CA-03).
    """
    trees = [t.strip().rstrip("/") for t in scoped_trees if t and t.strip()]
    scoped: list[str] = []
    informational: list[str] = []
    for path in _porcelain_paths(repo_root):
        if any(_under_tree(path, tree) for tree in trees):
            scoped.append(path)
        else:
            informational.append(path)
    return sorted(scoped), sorted(informational)


def resume_point(text: str) -> dict[str, str] | None:
    """Fields of the entry in force of `## Punto de retoma`. `None` if there is none.

    Single source of the parse for code written from 0114 on: `report_git_sync.py` and
    the scope of CA-03 read the resume point through here, never re-parsing it.

    **Tie-break `>=`**, inherited from `check_resume` (`validate_mandate.py:755-759`),
    which is the reader that actually decides which entry is in force — it is the one
    that emits `retoma-incompleta` about it. Between two entries with the same date the
    **last one in the file** wins, which is what an append-only list means: what was
    appended later is what is in force. (`print_summary` at `:918-922` uses `>` and
    would pick the first, but it only prints a date; it chooses no fields.) Neither is
    refactored — D-13: `validate_mandate.py` is not touched.

    The returned dict is a copy of the entry's bullets plus `fecha`, set to the
    resolved ISO date of the entry (the date lives in the `### <clave>` header when the
    entry has no `fecha` bullet).
    """
    body = sections(text or "").get("## Punto de retoma")
    if body is None:
        return None
    latest_date, latest = "", None
    for key, fields, _ in entries(body):
        f = iso_date(key) or iso_date(fields.get("fecha", ""))
        if f and f >= latest_date:
            latest_date, latest = f, fields
    if latest is None:
        return None
    resolved = dict(latest)
    resolved["fecha"] = latest_date
    return resolved


def codes_from_output(stdout: str) -> list[str]:
    """Codes of a `validate_mandate.py` run, sorted and de-duplicated.

    Single Python implementation of the parse that `comparar_esperado` does in Bash
    (`_common.sh:140-144`): `sed -n 's/^códigos: //p' | tr ' ' '\\n' | sed '/^$/d'
    | sort -u`. Splitting on the space character (not on arbitrary whitespace) and
    dropping the empties is what keeps double spaces from becoming a code. Without a
    `códigos:` line the answer is `[]`.

    Both the preflight (validator row) and `record_mandate_validation.py` (the
    `codigos:` field it appends) read the codes through here, so what the preflight
    printed and what `validaciones_mandato` records cannot diverge.
    """
    prefix = "códigos: "
    found: set[str] = set()
    for line in (stdout or "").splitlines():
        if not line.startswith(prefix):
            continue
        for token in line[len(prefix):].split(" "):
            if token:
                found.add(token)
    return sorted(found)


# --- Guarded subprocess launch (unit 0118, G4 — CA-21/CA-22) ------------------------
# Single choke-point for every `subprocess` call this repo's `.spec/scripts/` make on
# behalf of a supervised run: `supervised_parallel.py` (worktree lifecycle,
# `supervised_conductor.py` launch) and `worktree_registry.py` (`git worktree list`)
# both route through here, rather than each module keeping its own copy of the
# forbidden-pattern list (gate 0118/G4, hallazgo L3 media: uniform guard, one list).

FORBIDDEN_SUBPROCESS_PATTERNS = [
    "docker-compose", "docker compose", "docker_compose",
    "knowledge-router", "reindex", "rebuild-graph", "rebuild_graph",
    "index_repository",
]


class GuardedCommandError(Exception):
    """A command matched a `FORBIDDEN_SUBPROCESS_PATTERNS` entry (CA-21/CA-22)."""

    def __init__(self, pattern: str, cmd: list) -> None:
        super().__init__(f"comando bloqueado (patrón «{pattern}»): {cmd}")
        self.pattern = pattern
        self.cmd = cmd


def forbidden_subprocess_pattern(cmd: list) -> str | None:
    joined = " ".join(str(part) for part in cmd).lower()
    for pattern in FORBIDDEN_SUBPROCESS_PATTERNS:
        if pattern in joined:
            return pattern
    return None


def guarded_subprocess(cmd: list, **kwargs) -> subprocess.CompletedProcess:
    pattern = forbidden_subprocess_pattern(cmd)
    if pattern:
        raise GuardedCommandError(pattern, cmd)
    return subprocess.run(cmd, **kwargs)


def guarded_popen(cmd: list, **kwargs) -> subprocess.Popen:
    pattern = forbidden_subprocess_pattern(cmd)
    if pattern:
        raise GuardedCommandError(pattern, cmd)
    return subprocess.Popen(cmd, **kwargs)


# --- Configuración de datos del destino (`.spec/protocolo-datos.yaml`) ---------------
# Contrato del motor del kit, válido para cualquier destino: los checks del
# validador de deriva de protocolo y el selector de subset de tests que
# necesitan datos propios de cada destino (modelos/perfiles permitidos,
# subárboles de test) los leen de este archivo en vez de traerlos cableados.
# Precedencia flag > variable de entorno > archivo de reposo en la raíz del
# destino — mismo patrón que ya usa `preflight.py` (`resolve_mcp_config`)
# para el MCP. Un destino sin este archivo no tiene configuración de datos:
# el motor falla explícito (`ProtocoloDatosError`), nunca cae a un default
# silencioso.

PROTOCOLO_DATOS_ENV_VAR = "SDD_PROTOCOLO_DATOS_PATH"


class ProtocoloDatosError(Exception):
    """`.spec/protocolo-datos.yaml` ausente o no legible en la ruta resuelta."""


def resolve_protocolo_datos_path(
    flag: str | None, repo_root: Path, env_var: str = PROTOCOLO_DATOS_ENV_VAR
) -> tuple[Path, str]:
    """`--protocolo-datos` (flag) > `env_var` > `.spec/protocolo-datos.yaml`
    relativo a `repo_root`. Devuelve `(ruta, origen)`, `origen` en
    `{"flag", "env", "default"}` — mismo contrato de retorno que
    `preflight.resolve_mcp_config`."""
    if flag:
        return Path(flag), "flag"
    from_env = os.environ.get(env_var, "").strip()
    if from_env:
        return Path(from_env), "env"
    return repo_root / ".spec" / "protocolo-datos.yaml", "default"


def load_protocolo_datos(path: Path) -> str:
    """Texto de `.spec/protocolo-datos.yaml` en `path`. Levanta
    `ProtocoloDatosError` si no existe o no se puede leer — nunca un default
    silencioso (el archivo mismo documenta esta garantía)."""
    if not path.is_file():
        raise ProtocoloDatosError(
            f"{path}: no existe — el motor del kit no tiene configuración de "
            "datos para este destino y no cae a un default silencioso"
        )
    try:
        return path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ProtocoloDatosError(f"{path}: no se pudo leer ({exc})") from exc
