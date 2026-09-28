"""Canonicalization rules for the SDD safe-write loop and partition table of
the closed set of validator codes (unit 0132, G2; CA-09/CA-13).

This module is the single home of:

- `EXPECTED_CODE_COUNT` -- the literal cardinality of the closed set of
  validator codes (CA-09). Any drift between this constant and what
  `validate_mandate.py --codigos` prints at runtime fails the
  `test_codigos_set_matches_validator` test (also CA-09).
- `FIXABLE_BY_FORMAT` -- the empty set of codes the loop can recover from by
  reformatting alone. Empty at the close of unit 0132 by human decision
  (CA-13, plan.md decision #6, `pol-dev-un-artefacto-por-archivo`). Adding a
  code here requires a new SDD unit with explicit human approval: no implicit
  promotion from semantic to format-fixable.
- `CODE_CODE_POINT` -- the set of codes classified as "format" (i.e. they
  *would* belong in `FIXABLE_BY_FORMAT`). Empty at start because of
  `pri-ia-cero-inferencia-implicita`: every one of the 30 codes is treated as
  semantic until a future unit declares otherwise. The loop never decides this
  on its own; a human does.
- `normalize_canonical(text)` -- the rule set (block style, indent 2,
  `null`/`true`/`false` literals, no flow style, stable key order, comment
  preservation). Implemented (CA-04); each rule is covered by a test in
  `tests/test_sdd_safe_write.py::NormalizeCanonicalRuleTests`.
- `list_validator_codes()` -- runtime reader of the closed set via subprocess
  on `validate_mandate.py --codigos`, parsed through
  `_common.codes_from_output` so the sort/dedup behavior matches every other
  consumer of the validator's output (preflight, `record_mandate_validation`).

Naming: English-only per `pol-dev-nomenclatura-english-only`. No PyYAML
imports: the loop preserves comments and key order that `yaml.safe_dump`
destroys (`pol-ia-no-embeber-conocimiento`, plan.md decision #1).
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import _common

EXPECTED_CODE_COUNT: int = 30

# Empty by human decision (CA-13, plan.md decision #6): the loop runs in
# "always-normalize, no style retry" mode until runtime evidence of a code
# that is genuinely recoverable by formatting alone. Promoting a code here
# requires a new SDD unit with explicit human approval.
FIXABLE_BY_FORMAT: frozenset[str] = frozenset()

# Empty at start per `pri-ia-cero-inferencia-implicita`: every one of the
# 30 codes is semantic until a future unit reassigns one to "format" with
# explicit human declaration. The loop never infers partition membership.
CODE_CODE_POINT: frozenset[str] = frozenset()

_VALIDATOR_SCRIPT: Path = Path(__file__).resolve().parent / "validate_mandate.py"

#: Bare YAML 1.1-style tokens rewritten to the canonical lowercase/tilde form.
#: Empty string is deliberately absent: `_common.field` treats a missing value
#: as `""`, so mapping empty -> `null` would change semantics.
_LITERAL_MAP: dict[str, str] = {
    "True": "true",
    "TRUE": "true",
    "False": "false",
    "FALSE": "false",
    "~": "null",
    "Null": "null",
    "NULL": "null",
}

_LEADING_WS = re.compile(r"^([ \t]*)(.*)$", re.DOTALL)
_KV = re.compile(r"^([^\s:#][^:]*?):([ \t]+)(.*)$")
_LIST_ITEM = re.compile(r"^-(?:[ \t]+)(.*)$")
_BLOCK_SCALAR = re.compile(r"^[|>](?:[+-][1-9]?|[1-9][+-]?)?$")


def _split_comment(line: str) -> tuple[str, str, str]:
    """`(code, ws, comment)` split on a quote-aware `#`.

    `code` has no trailing whitespace; `ws` is the whitespace that preceded
    `#`; `comment` starts with `#` (or is empty). A `#` inside `'`/`"` is not
    a comment. Idempotency relies on rebuilding as `code + ws + comment`.
    """
    in_single = in_double = False
    for i, ch in enumerate(line):
        if ch == "'" and not in_double:
            in_single = not in_single
        elif ch == '"' and not in_single:
            in_double = not in_double
        elif ch == "#" and not in_single and not in_double and (
            i == 0 or line[i - 1] in " \t"
        ):
            code = line[:i]
            code_stripped = code.rstrip(" \t")
            return code_stripped, code[len(code_stripped) :], line[i:]
    stripped = line.rstrip(" \t")
    return stripped, line[len(stripped) :], ""


def _attach_comment(core: str, ws: str, comment: str) -> str:
    if not comment:
        return core
    if not ws:
        ws = " "
    return core + ws + comment


def _split_flow_items(inner: str) -> list[str] | None:
    """Split flow-collection body on top-level commas; `None` on parse failure."""
    items: list[str] = []
    buf: list[str] = []
    depth = 0
    in_single = in_double = False
    i = 0
    while i < len(inner):
        ch = inner[i]
        if in_single:
            buf.append(ch)
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            buf.append(ch)
            if ch == "\\" and i + 1 < len(inner):
                buf.append(inner[i + 1])
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
            buf.append(ch)
        elif ch == '"':
            in_double = True
            buf.append(ch)
        elif ch in "{[":
            depth += 1
            buf.append(ch)
        elif ch in "}]":
            depth -= 1
            if depth < 0:
                return None
            buf.append(ch)
        elif ch == "," and depth == 0:
            items.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1
    if in_single or in_double or depth != 0:
        return None
    items.append("".join(buf))
    cleaned = [it.strip() for it in items]
    if any(not it for it in cleaned):
        return None
    return cleaned


def _split_flow_key_value(item: str) -> tuple[str, str] | None:
    """`(key, value)` of one flow-mapping entry; `None` if no top-level `:`."""
    in_single = in_double = False
    depth = 0
    i = 0
    while i < len(item):
        ch = item[i]
        if in_single:
            if ch == "'":
                in_single = False
            i += 1
            continue
        if in_double:
            if ch == "\\" and i + 1 < len(item):
                i += 2
                continue
            if ch == '"':
                in_double = False
            i += 1
            continue
        if ch == "'":
            in_single = True
        elif ch == '"':
            in_double = True
        elif ch in "{[":
            depth += 1
        elif ch in "}]":
            depth -= 1
        elif ch == ":" and depth == 0:
            rest = item[i + 1 :]
            if rest == "" or rest[0] in " \t":
                key = item[:i].strip()
                value = rest.strip()
                if key:
                    return key, value
                return None
        i += 1
    return None


def _normalize_scalar(value: str) -> str:
    """Rewrite a bare literal token; leave quoted and multi-word values alone."""
    stripped = value.strip()
    if len(stripped) >= 2 and stripped[0] in "\"'" and stripped[-1] == stripped[0]:
        return value
    if stripped in _LITERAL_MAP:
        leading = value[: len(value) - len(value.lstrip(" \t"))]
        trailing = value[len(value.rstrip(" \t")) :]
        return leading + _LITERAL_MAP[stripped] + trailing
    return value


def _expand_leading_tabs(prefix: str) -> str:
    return prefix.replace("\t", "  ")


def _emit_flow_value(
    key_prefix: str,
    value: str,
    indent: str,
    comment: str,
    ws: str,
) -> list[str] | None:
    """Expand one `key_prefix + value` (or bare value) into block lines.

    Returns `None` when the value is not an expandable non-empty flow
    collection (caller falls through to scalar handling).
    """
    value_stripped = value.strip()
    is_map = value_stripped.startswith("{") and value_stripped.endswith("}")
    is_seq = value_stripped.startswith("[") and value_stripped.endswith("]")
    if not ((is_map or is_seq) and value_stripped not in ("{}", "[]")):
        return None
    inner = value_stripped[1:-1].strip()
    items = _split_flow_items(inner) if inner else []
    if items is None:
        return None

    head = f"{indent}{key_prefix}"
    if is_map:
        entries: list[tuple[str, str]] = []
        for it in items:
            kv = _split_flow_key_value(it)
            if kv is None:
                return None
            entries.append(kv)
        if not entries:
            return None
        if not key_prefix:
            # bare flow map (list entry): first pair rides the `- ` line
            return None  # handled by list-item branch
        out = [_attach_comment(head, ws, comment) if comment else head]
        child = indent + "  "
        for k, v in entries:
            nested = _emit_flow_value(f"{k}: ", v, child, "", "")
            if nested is not None:
                out.extend(nested)
            else:
                out.append(f"{child}{k}: {_normalize_scalar(v)}")
        return out

    if not key_prefix:
        return None
    out = [_attach_comment(head, ws, comment) if comment else head]
    child = indent + "  "
    for it in items:
        if it.startswith("{") and it.endswith("}") and it != "{}":
            nested_inner = it[1:-1].strip()
            nested_items = _split_flow_items(nested_inner) if nested_inner else []
            if nested_items is None:
                return None
            entries = []
            for nit in nested_items:
                kv = _split_flow_key_value(nit)
                if kv is None:
                    return None
                entries.append(kv)
            if not entries:
                return None
            k0, v0 = entries[0]
            nested0 = _emit_flow_value(f"{k0}:", v0, child + "  ", "", "")
            if nested0 is not None:
                out.append(f"{child}- {k0}:")
                out.extend(nested0[1:])
            else:
                out.append(f"{child}- {k0}: {_normalize_scalar(v0)}")
            for k, v in entries[1:]:
                nested = _emit_flow_value(f"{k}: ", v, child + "  ", "", "")
                if nested is not None:
                    out.extend(nested)
                else:
                    out.append(f"{child}  {k}: {_normalize_scalar(v)}")
        else:
            out.append(f"{child}- {_normalize_scalar(it)}")
    return out


def _normalize_line(line: str) -> tuple[list[str], int | None]:
    """Normalize one content line. Returns `(output_lines, block_parent_indent)`.

    `block_parent_indent` is the indent of a key/item that opened a `|`/`>`
    scalar (caller must pass following lines through untouched until indent
    drops back to that level); `None` when no block scalar was opened.
    """
    core, ws, comment = _split_comment(line)
    m = _LEADING_WS.match(core)
    if not m:
        return [line], None
    raw_indent, rest = m.group(1), m.group(2)
    indent = _expand_leading_tabs(raw_indent)
    if not rest:
        return [line], None

    def finish(new_rest: str) -> list[str]:
        return [_attach_comment(indent + new_rest, ws, comment)]

    def rebuild_if_changed(new_rest: str) -> list[str]:
        if raw_indent == indent and new_rest == rest:
            if comment:
                return [line]
            return [line]
        rebuilt = indent + new_rest
        if comment:
            return [_attach_comment(rebuilt, ws, comment)]
        return [rebuilt]

    # --- block scalar opener: `key: |` / `- >` ---
    kv_probe = _KV.match(rest)
    if kv_probe and _BLOCK_SCALAR.match(kv_probe.group(3).strip()):
        return rebuild_if_changed(rest), _common._indent_width(indent)
    li_probe = _LIST_ITEM.match(rest)
    if li_probe and _BLOCK_SCALAR.match(li_probe.group(1).strip()):
        return rebuild_if_changed(rest), _common._indent_width(indent)

    # --- list item ---
    li = _LIST_ITEM.match(rest)
    if li:
        after_dash = li.group(1)
        sp_m = re.match(r"^([ \t]+)", after_dash)
        item_sp = sp_m.group(1) if sp_m else " "
        value = after_dash.strip()

        if value.startswith("{") and value.endswith("}") and value != "{}":
            inner = value[1:-1].strip()
            items = _split_flow_items(inner) if inner else []
            if items is None:
                return finish(rest), None
            entries: list[tuple[str, str]] = []
            for it in items:
                kv = _split_flow_key_value(it)
                if kv is None:
                    return finish(rest), None
                entries.append(kv)
            if not entries:
                return finish(rest), None
            out = []
            k0, v0 = entries[0]
            nested0 = _emit_flow_value(f"{k0}:", v0, indent + "  ", "", "")
            if nested0 is not None:
                first = f"{indent}-{item_sp}{k0}:"
                if comment:
                    out.append(_attach_comment(first, ws, comment))
                else:
                    out.append(first)
                out.extend(nested0[1:])
            else:
                first = f"{indent}-{item_sp}{k0}: {_normalize_scalar(v0)}"
                if comment:
                    out.append(_attach_comment(first, ws, comment))
                else:
                    out.append(first)
            child = indent + "  "
            for k, v in entries[1:]:
                nested = _emit_flow_value(f"{k}: ", v, child, "", "")
                if nested is not None:
                    out.extend(nested)
                else:
                    out.append(f"{child}{k}: {_normalize_scalar(v)}")
            return out, None

        # `- key: {…}` / `- key: […]` — first key rides the dash line.
        kv_li = _KV.match(value)
        if kv_li:
            key_li, v_li = kv_li.group(1), kv_li.group(3).strip()
            flow_li = (
                (v_li.startswith("{") and v_li.endswith("}") and v_li != "{}")
                or (v_li.startswith("[") and v_li.endswith("]") and v_li != "[]")
            )
            if flow_li:
                body = _emit_flow_value(f"{key_li}:", v_li, indent + "  ", "", "")
                if body is not None:
                    head = f"{indent}-{item_sp}{key_li}:"
                    out = [_attach_comment(head, ws, comment)] if comment else [head]
                    out.extend(body[1:])
                    return out, None

        if value.startswith("[") and value.endswith("]") and value != "[]":
            inner = value[1:-1].strip()
            items = _split_flow_items(inner) if inner else []
            if items is None:
                return finish(rest), None
            out = []
            for idx, it in enumerate(items):
                sp = item_sp if idx == 0 else " "
                if it.startswith("{") and it.endswith("}") and it != "{}":
                    nested_inner = it[1:-1].strip()
                    nested_items = _split_flow_items(nested_inner) if nested_inner else []
                    if nested_items is None:
                        return finish(rest), None
                    nest_entries = []
                    for nit in nested_items:
                        kv = _split_flow_key_value(nit)
                        if kv is None:
                            return finish(rest), None
                        nest_entries.append(kv)
                    if not nest_entries:
                        return finish(rest), None
                    child = indent + "  "
                    k0, v0 = nest_entries[0]
                    nested0 = _emit_flow_value(f"{k0}:", v0, child + "  ", "", "")
                    if nested0 is not None:
                        first = f"{indent}-{sp}{k0}:"
                        if idx == 0 and comment:
                            out.append(_attach_comment(first, ws, comment))
                        else:
                            out.append(first)
                        out.extend(nested0[1:])
                    else:
                        first = f"{indent}-{sp}{k0}: {_normalize_scalar(v0)}"
                        if idx == 0 and comment:
                            out.append(_attach_comment(first, ws, comment))
                        else:
                            out.append(first)
                    for k, v in nest_entries[1:]:
                        nested = _emit_flow_value(f"{k}: ", v, child, "", "")
                        if nested is not None:
                            out.extend(nested)
                        else:
                            out.append(f"{child}{k}: {_normalize_scalar(v)}")
                else:
                    piece = f"{indent}-{sp}{_normalize_scalar(it)}"
                    if idx == 0 and comment:
                        out.append(_attach_comment(piece, ws, comment))
                    else:
                        out.append(piece)
            return out, None

        new_value = _normalize_scalar(value)
        if new_value != value or raw_indent != indent:
            rebuilt = f"{indent}-{item_sp}{new_value}"
            if comment:
                return [_attach_comment(rebuilt, ws, comment)], None
            return [rebuilt], None
        return rebuild_if_changed(rest), None

    # --- key: value ---
    kv = _KV.match(rest)
    if kv:
        key, sp, value = kv.group(1), kv.group(2), kv.group(3)
        value_stripped = value.strip()

        if (
            value_stripped.startswith("{")
            and value_stripped.endswith("}")
            and value_stripped != "{}"
        ):
            expanded = _emit_flow_value(f"{key}:", value_stripped, indent, comment, ws)
            if expanded is None:
                inner = value_stripped[1:-1].strip()
                items = _split_flow_items(inner) if inner else []
                if items is None:
                    return finish(rest), None
                entries = []
                for it in items:
                    pair = _split_flow_key_value(it)
                    if pair is None:
                        return finish(rest), None
                    entries.append(pair)
                if not entries:
                    return finish(rest), None
                key_line = f"{indent}{key}:"
                out = [_attach_comment(key_line, ws, comment) if comment else key_line]
                child = indent + "  "
                for k, v in entries:
                    out.append(f"{child}{k}: {_normalize_scalar(v)}")
                return out, None
            return expanded, None

        if (
            value_stripped.startswith("[")
            and value_stripped.endswith("]")
            and value_stripped != "[]"
        ):
            expanded = _emit_flow_value(f"{key}:", value_stripped, indent, comment, ws)
            if expanded is not None:
                return expanded, None
            inner = value_stripped[1:-1].strip()
            items = _split_flow_items(inner) if inner else []
            if items is None:
                return finish(rest), None
            key_line = f"{indent}{key}:"
            out = [_attach_comment(key_line, ws, comment) if comment else key_line]
            child = indent + "  "
            for it in items:
                if it.startswith("{") and it.endswith("}") and it != "{}":
                    nested_inner = it[1:-1].strip()
                    nested_items = _split_flow_items(nested_inner) if nested_inner else []
                    if nested_items is None:
                        return finish(rest), None
                    entries = []
                    for nit in nested_items:
                        kv = _split_flow_key_value(nit)
                        if kv is None:
                            return finish(rest), None
                        entries.append(kv)
                    if not entries:
                        return finish(rest), None
                    k0, v0 = entries[0]
                    out.append(f"{child}- {k0}: {_normalize_scalar(v0)}")
                    for k, v in entries[1:]:
                        out.append(f"{child}  {k}: {_normalize_scalar(v)}")
                else:
                    out.append(f"{child}- {_normalize_scalar(it)}")
            return out, None

        new_value = _normalize_scalar(value)
        if new_value != value or raw_indent != indent:
            rebuilt = f"{indent}{key}:{sp}{new_value}"
            if comment:
                return [_attach_comment(rebuilt, ws, comment)], None
            return [rebuilt], None
        return rebuild_if_changed(rest), None

    # bare / unusual content: only fix leading tabs
    if raw_indent != indent:
        rebuilt = indent + rest
        if comment:
            return [_attach_comment(rebuilt, ws, comment)], None
        return [rebuilt], None
    return [line], None


def normalize_canonical(text: str) -> str:
    """Rewrite text to canonical form (block style, indent 2, literal
    `null`/`true`/`false`, stable key order, comments preserved).

    Rules (CA-04):
    - **block style**: non-empty flow `{...}` / `[...]` expands to block;
      empty `{}` / `[]` stay (semantic null vs empty-collection).
    - **indent 2**: leading tabs become two spaces each; newly emitted nesting
      uses +2; existing space-indented content is not re-indented.
    - **literals**: bare `True`/`TRUE`/`False`/`FALSE`/`~`/`Null`/`NULL`
      become `true`/`false`/`null`; quoted and empty values untouched.
    - **no flow style**: same expansion as block style above.
    - **stable key order**: lines are rewritten in place; no sorting.

    Does not use `yaml.safe_dump` (destroys comments and reorders keys via
    `sort_keys=True`); composition is regex over lines, reusing
    `_common._indent_width`. Idempotent: `normalize(normalize(x)) == x`.
    """
    if not text:
        return text
    lines = text.split("\n")
    out: list[str] = []
    block_parent: int | None = None
    for line in lines:
        if block_parent is not None:
            if not line.strip() or _common._indent_width(line) > block_parent:
                out.append(line)
                continue
            block_parent = None
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            out.append(line)
            continue
        produced, opens = _normalize_line(line)
        out.extend(produced)
        if opens is not None:
            block_parent = opens
    return "\n".join(out)


def list_validator_codes() -> list[str]:
    """Read the closed set of validator codes at runtime.

    Runs `python3 .spec/scripts/validate_mandate.py --codigos` via subprocess
    and parses its stdout (one code per line) through `_common.codes_from_output`
    -- the same splitter the preflight uses on the validator row, so sort and
    dedup behavior match every other consumer.

    Returns:
        Sorted, de-duplicated list of codes. Its length must equal
        `EXPECTED_CODE_COUNT` (verified by
        `test_sdd_safe_write.py::test_codigos_set_matches_validator`).

    Raises:
        RuntimeError: the subprocess exits non-zero; the message includes
        the exit code and the captured stderr (or stdout if stderr is empty).
    """
    proc = subprocess.run(
        [sys.executable, str(_VALIDATOR_SCRIPT), "--codigos"],
        capture_output=True,
        text=True,
        check=False,
    )
    if proc.returncode != 0:
        captured = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(
            f"validate_mandate.py --codigos exited {proc.returncode}: {captured}"
        )
    wrapped = "códigos: " + " ".join(proc.stdout.split())
    return _common.codes_from_output(wrapped)
