#!/usr/bin/env python3
"""Bitacora format validator — verifies the compact 1-line-per-event format.

Unit 0151: `bitacora.md` entries must be exactly one line, matching the pattern
``## <ISO-8601> · <fase|gate>:<veredicto> · <resumen>``.

Usage:
    validate_bitacora_format.py <bitacora.md>
    validate_bitacora_format.py --check <bitacora.md>  (same, explicit)
    cat bitacora.md | validate_bitacora_format.py --stdin

Exit codes:
    0 — all entries match the compact format, or file absent/empty
    1 — at least one line does not match any recognised format (compact, legacy, or doc header)
    2 — usage error (missing argument)

Legacy entries (multi-line with bullets) produce a warning on stderr but exit 0.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

PATTERN_COMPACT = re.compile(
    r"^##\s+\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2})?Z?\s*·\s*"
    r"(fase|gate):\S+\s*·\s*.+$"
)
PATTERN_DOC_HEADER = re.compile(r"^#\s+Bitácora\b")
PATTERN_COMMENT = re.compile(r"^>\s")
PATTERN_CONVENCION = re.compile(r"^\*\*Convención de merge\b")
PATTERN_BLANK = re.compile(r"^\s*$")
PATTERN_SEPARATOR = re.compile(r"^---\s*$")
PATTERN_LEGACY_BULLET = re.compile(
    r"^\*\*(Qué hice|Dónde quedó|Siguiente paso|Bloqueos|Qué criticó cada lente|Qué se corrigió|Qué se descartó|Qué quedó abierto):\*\*"
)


def is_compact(line: str) -> bool:
    return bool(PATTERN_COMPACT.match(line))


def is_doc_header(line: str) -> bool:
    return bool(PATTERN_DOC_HEADER.match(line) or PATTERN_COMMENT.match(line) or PATTERN_CONVENCION.match(line))


def is_blank_or_sep(line: str) -> bool:
    return bool(PATTERN_BLANK.match(line) or PATTERN_SEPARATOR.match(line))


def is_legacy(line: str) -> bool:
    return bool(PATTERN_LEGACY_BULLET.match(line))


def validate(path: Path | None) -> int:
    if path is None:
        lines = sys.stdin.read().splitlines(keepends=False)
    elif not path.exists():
        return 0
    else:
        lines = path.read_text(encoding="utf-8").splitlines(keepends=False)

    if not lines or all(not line.strip() for line in lines):
        return 0

    legacy_found = False
    bad_lines: list[tuple[int, str]] = []

    for idx, raw in enumerate(lines, start=1):
        line = raw.rstrip("\n")
        if is_doc_header(line) or is_blank_or_sep(line):
            continue
        if is_compact(line):
            continue
        if is_legacy(line):
            legacy_found = True
            continue
        bad_lines.append((idx, line[:120]))

    if legacy_found:
        print("validate_bitacora_format: AVISO — formato legacy detectado (multi-linea con bullets); no bloquea", file=sys.stderr)

    if bad_lines:
        for lineno, snippet in bad_lines:
            print(f"validate_bitacora_format: linea {lineno}: formato no reconocido — {snippet}", file=sys.stderr)
        print("validate_bitacora_format: ERROR — el archivo contiene lineas que no cumplen el formato compacto de 1 linea", file=sys.stderr)
        return 1

    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate bitacora.md compact format")
    parser.add_argument("path", nargs="?", help="Path to bitacora.md")
    parser.add_argument("--stdin", action="store_true", help="Read from stdin instead of a file")
    args = parser.parse_args()

    if args.stdin:
        path = None
    elif args.path:
        path = Path(args.path)
    else:
        print("validate_bitacora_format: falta el path del archivo o --stdin", file=sys.stderr)
        sys.exit(2)

    sys.exit(validate(path))


if __name__ == "__main__":
    main()
