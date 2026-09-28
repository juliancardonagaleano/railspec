#!/usr/bin/env python3
"""Generate `.spec/cambios-menores/README.md`, the index of minor-change entries.

The folder holds one file per entry of the **menor** branch of the triaje
(`.spec/README.md ## Triaje`), each following the 5-line template (id, qué,
por qué, evidencia, gate). This script is the only writer of that folder's
`README.md`; running it twice produces byte-identical output, and `--check`
reports drift without writing.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRIES_DIR = REPO_ROOT / ".spec" / "cambios-menores"

_FILENAME_RE = re.compile(r"^(\d{4}-\d{2}-\d{2})-(.+)\.md$")
_QUE_RE = re.compile(r"^-\s*qué\s*:\s*(.*)$")
_MAX_QUE_LEN = 100

_TEMPLATE = """# Índice de cambios menores

> Generado por `.spec/scripts/generate_minor_changes_index.py` — no editar a mano.

Carpeta que registra la rama **menor** del triaje de `.spec/README.md ##
Triaje`: un archivo por entrada, con la plantilla de 5 líneas (id, qué, por
qué, evidencia, gate).

Para registrar una entrada nueva:

1. Copiar la plantilla de abajo en un archivo nuevo
   `.spec/cambios-menores/<YYYY-MM-DD>-<slug>.md`.
2. Validarla: `python3 .spec/scripts/classify_change.py validate-entry
   .spec/cambios-menores/<archivo>.md`.
3. Regenerar este índice: `python3 .spec/scripts/generate_minor_changes_index.py`.

## Plantilla

```
## <fecha> — <id>
- qué: <descripción concisa del cambio>
- por qué: <motivación>
- evidencia: <commit o ruta del diff>
- gate: <veredicto del mini-gate, e.g. "diff 27 líneas netas, 1 archivo nuevo, sin contrato tocado">
```

## Entradas
"""


def _entry_files(entries_dir: Path) -> list[Path]:
    return [p for p in entries_dir.glob("*.md") if p.name != "README.md"]


def _extract_que(path: Path) -> str:
    for line in path.read_text(encoding="utf-8").splitlines():
        match = _QUE_RE.match(line)
        if match:
            text = match.group(1).strip()
            if len(text) > _MAX_QUE_LEN:
                text = text[: _MAX_QUE_LEN - 1].rstrip() + "…"
            return text
    return ""


def _escape_cell(text: str) -> str:
    return text.replace("|", "\\|")


def build_readme(entries_dir: Path) -> str:
    rows = []
    for path in _entry_files(entries_dir):
        match = _FILENAME_RE.match(path.name)
        date = match.group(1) if match else ""
        slug = match.group(2) if match else path.stem
        rows.append((date, slug, path.name, _extract_que(path)))

    rows.sort(key=lambda row: row[1])
    rows.sort(key=lambda row: row[0], reverse=True)

    lines = [_TEMPLATE.rstrip("\n"), "", "| fecha | entrada | qué |", "|---|---|---|"]
    for date, slug, filename, que in rows:
        lines.append(f"| {date} | [{slug}]({filename}) | {_escape_cell(que)} |")
    lines.append("")
    return "\n".join(lines)


def _cmd_check(entries_dir: Path, readme_path: Path) -> int:
    expected = build_readme(entries_dir)
    actual = readme_path.read_text(encoding="utf-8") if readme_path.is_file() else None
    if actual == expected:
        print(f"OK: {readme_path} sin drift")
        return 0
    print(
        f"DRIFT: {readme_path} no coincide con las entradas de {entries_dir}",
        file=sys.stderr,
    )
    return 1


def _cmd_generate(entries_dir: Path, readme_path: Path) -> int:
    content = build_readme(entries_dir)
    readme_path.write_text(content, encoding="utf-8")
    print(f"OK: {readme_path} generado ({len(_entry_files(entries_dir))} entradas)")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Regenerate .spec/cambios-menores/README.md from its entries."
    )
    parser.add_argument("--check", action="store_true", help="Only check for drift; exit 0/1.")
    parser.add_argument("--entries-dir", type=Path, default=ENTRIES_DIR, help="Override entries dir (testing only).")
    parser.add_argument("--readme", type=Path, default=None, help="Override README path (testing only).")
    args = parser.parse_args(argv)

    entries_dir: Path = args.entries_dir
    readme_path: Path = args.readme or entries_dir / "README.md"

    if args.check:
        return _cmd_check(entries_dir, readme_path)
    return _cmd_generate(entries_dir, readme_path)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
