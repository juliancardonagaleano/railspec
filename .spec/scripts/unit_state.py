"""Minimal reader of nested `_estado.yaml` blocks (D-9, CA-07), without
PyYAML.

Reuses `_common.field` (flat, top-level lookups) and `_common.iso_date`
(ISO-8601 recognition) and only adds what they do not have: descent into
indented blocks (`modelo_ejecucion.<phase>`, `gates.<phase>`) and into list
entries whether they are still single-line flow mappings
(`- {lente: "...", subagente: ...}`) or already block style after unit 0132
CA-04 (`- lente: "..."` + continuation lines).

English module name (`unit_state`, not `estado`) per D-15/CA-23: the Spanish
SDD vocabulary lives in the literals this module searches for
(`modelo_ejecucion`, `gates`, `subagente`), not in the module's own name.

Known limitation, accepted rather than building a general YAML parser: a
flow mapping's value is assumed to have no nested `{...}` of its own -- true
of every shape `_estado.yaml` uses today (`criticos`/`refutador` entries,
`modelo_ejecucion.<phase>`). Block-style list entries are scanned line-wise
for the keys this module needs (`subagente`), not fully parsed.
"""

from __future__ import annotations

import re

import _common

#: `modelo_ejecucion:` uses the Spanish verb form of a phase
#: (`especificar`/`planificar`/`tareas`/`implementar`) while `gates:` and the
#: top-level `fase:` field use the canonical short form
#: (`spec`/`plan`/`tasks`/`implement`, `codigo` for the implementation gate).
#: These aliases let every lookup here accept the canonical name and still
#: find the real-world files that spell it the other way.
MODELO_EJECUCION_ALIASES = {
    "spec": "especificar",
    "plan": "planificar",
    "tasks": "tareas",
    "implement": "implementar",
}

GATES_ALIASES = {
    "implement": "codigo",
}

#: Canonical phase vocabulary this module reports against (matches the
#: top-level `fase:` field of `_estado.yaml`).
CANONICAL_PHASES = ("spec", "plan", "tasks", "implement")

_KEY_LINE = re.compile(r"^([ \t]*)([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.*)$")
_FLOW_ENTRY = re.compile(r"([A-Za-z_][A-Za-z0-9_]*):\s*((?:\"[^\"]*\")|(?:'[^']*')|[^,}]+)")


def unit_id(text: str) -> str:
    """Top-level `id:` field (flat -- reuses `_common.field` directly)."""
    return _common.field(text, "id")


def read_estado(path) -> str:  # path: pathlib.Path, kept untyped to avoid a hard import here
    return path.read_text(encoding="utf-8")


# --- Indentation-based block descent, with a single-line flow-mapping escape ---------


def _indent_of(line: str) -> int:
    return len(line) - len(line.lstrip(" \t"))


def _unquote(value: str) -> str:
    v = value.strip()
    if len(v) >= 2 and v[0] == v[-1] and v[0] in "\"'":
        return v[1:-1]
    return v


def _parse_flow_mapping(text: str) -> dict[str, str]:
    """Parses one single-line `{k: v, k2: "v2"}` flow mapping."""
    inner = text.strip()
    if inner.startswith("{") and inner.endswith("}"):
        inner = inner[1:-1]
    result: dict[str, str] = {}
    for m in _FLOW_ENTRY.finditer(inner):
        result[m.group(1)] = _unquote(m.group(2).strip().rstrip(","))
    return result


def _block_lines(lines: list[str], start: int, parent_indent: int) -> list[str]:
    """Lines from `lines[start:]` more indented than `parent_indent` -- the
    body of the block that key opened, up to (not including) the first line
    back at or below that indent."""
    body: list[str] = []
    for line in lines[start:]:
        if not line.strip():
            body.append(line)
            continue
        if _indent_of(line) <= parent_indent:
            break
        body.append(line)
    return body


def _find_top_level_block(text: str, key: str) -> tuple[str, str]:
    """`(inline_value, block_body)` for a `key:` at column 0."""
    m = re.search(rf"^{re.escape(key)}:[ \t]*(.*)$", text, re.MULTILINE)
    if not m:
        return "", ""
    inline = m.group(1).strip()
    lines = text[m.end():].splitlines()
    body = _block_lines(lines, 0, 0)
    return inline, "\n".join(body)


def _find_sub_block(body: str, key: str) -> tuple[str, str]:
    """`(inline_value, block_body)` for the first `key:` line found inside
    `body`, whatever its indent -- that indent becomes the reference for
    where its own block ends."""
    lines = body.splitlines()
    for i, line in enumerate(lines):
        m = _KEY_LINE.match(line)
        if not m or m.group(2) != key:
            continue
        indent = _indent_of(line)
        inline = m.group(3).strip()
        sub_lines = _block_lines(lines, i + 1, indent)
        return inline, "\n".join(sub_lines)
    return "", ""


def _walk_scalar(inline: str, body: str, remaining: tuple[str, ...]) -> str:
    if not remaining:
        return _unquote(inline)
    if inline.startswith("{"):
        flow = _parse_flow_mapping(inline)
        if len(remaining) == 1:
            return flow.get(remaining[0], "")
        return ""
    sub_inline, sub_body = _find_sub_block(body, remaining[0])
    if not sub_inline and not sub_body:
        return ""
    return _walk_scalar(sub_inline, sub_body, remaining[1:])


def _walk_block(inline: str, body: str, remaining: tuple[str, ...]) -> str:
    if not remaining:
        return body
    if inline.startswith("{"):
        return ""
    sub_inline, sub_body = _find_sub_block(body, remaining[0])
    if not sub_inline and not sub_body:
        return ""
    return _walk_block(sub_inline, sub_body, remaining[1:])


def find_nested(text: str, path: tuple[str, ...]) -> str:
    """Scalar value at a dotted path of nested `key:` blocks (block-style or
    single-line flow `{...}`). `""` when any segment is missing."""
    if not path:
        return ""
    inline, body = _find_top_level_block(text, path[0])
    if not inline and not body:
        return ""
    return _walk_scalar(inline, body, path[1:])


def find_nested_block(text: str, path: tuple[str, ...]) -> str:
    """Raw body text of a dotted path of nested `key:` blocks. `""` when
    missing, or when the path resolves to a scalar/flow leaf instead."""
    if not path:
        return ""
    inline, body = _find_top_level_block(text, path[0])
    if not inline and not body:
        return ""
    return _walk_block(inline, body, path[1:])


# --- `modelo_ejecucion.<phase>` -------------------------------------------------------


def phase_execution_mark(text: str, phase: str) -> str:
    """ISO mark at `modelo_ejecucion.<phase>.en`, trying the canonical name
    first and its verb-form alias second (`MODELO_EJECUCION_ALIASES`)."""
    for key in (phase, MODELO_EJECUCION_ALIASES.get(phase)):
        if not key:
            continue
        value = find_nested(text, ("modelo_ejecucion", key, "en"))
        if value:
            return _common.iso_date(value)
    return ""


def phase_execution_subagent(text: str, phase: str) -> str:
    """`subagente:` declared at `modelo_ejecucion.<phase>`."""
    for key in (phase, MODELO_EJECUCION_ALIASES.get(phase)):
        if not key:
            continue
        value = find_nested(text, ("modelo_ejecucion", key, "subagente"))
        if value:
            return value
    return ""


# --- `gates.<phase>` -------------------------------------------------------------------


def gate_block(text: str, phase: str) -> str:
    for key in (phase, GATES_ALIASES.get(phase)):
        if not key:
            continue
        body = find_nested_block(text, ("gates", key))
        if body:
            return body
    return ""


def gate_verdict(text: str, phase: str) -> str:
    """`veredicto` declared directly under `gates.<phase>` (`aprobado` |
    `refinado` | `escalado`). `""` when the phase has no gate block, or the
    block has no `veredicto:` line yet (G2, CA-04/CA-05)."""
    body = gate_block(text, phase)
    if not body:
        return ""
    inline, _ = _find_sub_block(body, "veredicto")
    return _unquote(inline)


def gate_causa(text: str, phase: str) -> str:
    """`causa` declared directly under `gates.<phase>` -- only meaningful
    when `gate_verdict(text, phase) == "escalado"` (the schema only writes
    `causa` in that case); `""` otherwise or when absent (G2, CA-05)."""
    body = gate_block(text, phase)
    if not body:
        return ""
    inline, _ = _find_sub_block(body, "causa")
    return _unquote(inline)


def _scalar_values(body: str) -> list[str]:
    """Every scalar value found directly in `body`: `key: value` lines and
    the values of any flow mapping in a `- {...}` list entry -- flat, not
    recursing into further nested blocks beyond one flow-mapping level
    (sufficient for `gates.<phase>`'s shape today). Block-style list entries
    contribute through the `key: value` match on each line."""
    values: list[str] = []
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("-") and "{" in stripped:
            flow = _parse_flow_mapping(stripped[stripped.index("{"):])
            values.extend(flow.values())
            continue
        m = _KEY_LINE.match(line)
        if m:
            values.append(_unquote(m.group(3).strip()))
        else:
            m_item = re.match(
                r"^[ \t]*-[ \t]+([A-Za-z_][A-Za-z0-9_]*):[ \t]*(.+)$",
                line,
            )
            if m_item:
                values.append(_unquote(m_item.group(2).strip()))
    return values


def gate_iso_marks(text: str, phase: str) -> list[str]:
    """Every ISO-8601 value found anywhere inside `gates.<phase>` (sorted,
    de-duplicated). Today's `_estado.yaml` schema carries none -- this is
    written generically anyway (D-9 asks for it forward-compatibly, and
    `pri-ia-cero-inferencia-implicita` forbids assuming a shape that is not
    actually declared closed)."""
    body = gate_block(text, phase)
    marks = {iso for value in _scalar_values(body) if (iso := _common.iso_date(value))}
    return sorted(marks)


def gate_declared_subagents(text: str, phase: str) -> set[str]:
    """`subagente:` names declared under `gates.<phase>.criticos[*]` and
    `gates.<phase>.refutador[*]` (flow-style `- {...}` or block-style
    `- key: value` / continuation lines after unit 0132 CA-04)."""
    body = gate_block(text, phase)
    names: set[str] = set()
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("-") and "{" in stripped:
            flow = _parse_flow_mapping(stripped[stripped.index("{"):])
            if flow.get("subagente"):
                names.add(flow["subagente"])
            continue
        m = re.match(
            r"^(?:[ \t]*-[ \t]+|[ \t]+)subagente:[ \t]+(.+)$",
            line,
        )
        if m:
            names.add(_unquote(m.group(1).strip().split(",")[0].strip()))
    return names


# --- Combined, phase-level API used by `attribution.py` (G2) --------------------------


def earliest_phase_mark(text: str, phase: str) -> str:
    """Earliest ISO mark for one phase between `modelo_ejecucion.<phase>.en`
    and any ISO value under `gates.<phase>` (D-9: "gana la anterior"). `""`
    when neither source has one."""
    candidates = []
    exec_mark = phase_execution_mark(text, phase)
    if exec_mark:
        candidates.append(exec_mark)
    candidates.extend(gate_iso_marks(text, phase))
    return min(candidates) if candidates else ""


def all_phase_marks(text: str) -> dict[str, str]:
    """Earliest mark per canonical phase found in this unit's `_estado.yaml`,
    omitting phases with no mark at all. `attribution.py` (T13) turns the
    sorted marks of a unit into the semi-open-interval rule of CA-07."""
    marks: dict[str, str] = {}
    for phase in CANONICAL_PHASES:
        mark = earliest_phase_mark(text, phase)
        if mark:
            marks[phase] = mark
    return marks


def declared_subagents(text: str, phase: str) -> set[str]:
    """Every subagent name declared for one phase, from both
    `modelo_ejecucion.<phase>.subagente` and `gates.<phase>.criticos[*]` /
    `gates.<phase>.refutador[*]` (D-9, CA-08 `declared` source)."""
    names: set[str] = set()
    exec_name = phase_execution_subagent(text, phase)
    if exec_name:
        names.add(exec_name)
    names |= gate_declared_subagents(text, phase)
    return names
