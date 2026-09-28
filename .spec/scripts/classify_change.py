#!/usr/bin/env python3
"""Fast-path classifier for SDD triage (0123-fast-path-cambios-menores).

Decides the triaje branch for a proposed change: trivial, menor, or no_trivial.
Pure function over (new_files, lines_net, contract_change) — no LLM, no network.

Umbrales (de `.spec/README.md ## Triaje`):
  trivial    -> new_files == 0 and lines_net <= 5
  menor      -> new_files <= 1 and lines_net <= 50 and not contract_change
  no_trivial -> todo lo demás

El subcomando `validate-entry` (`validate_entry`/`parse_entry_file`, más
abajo) valida la *forma* de una entrada ya registrada en
`.spec/cambios-menores/` y su umbral de líneas netas — no re-evalúa
`contract_change`: ese campo es una declaración humana tomada en el triaje,
antes de escribirse la entrada, no algo que este script re-derive del
archivo. `classify()` sigue siendo la única función que aplica los tres
umbrales (`new_files`, `lines_net`, `contract_change`).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Literal

Branch = Literal["trivial", "menor", "no_trivial"]

_HEADER_RE = re.compile(r"^## (\d{4}-\d{2}-\d{2}) — (.+)$")
_BULLET_RE = re.compile(r"^-\s*(qué|por qué|evidencia|gate)\s*:\s*(.*)$")
#: El signo es opcional y puede venir como `-` (ASCII) o `−` (U+2212, el que
#: produce la escritura tipográfica de las entradas). Sin él, una eliminación
#: pura se leía como su valor absoluto y un retiro de código se rechazaba por
#: "exceder el presupuesto".
_LINES_NET_RE = re.compile(r"([+−-]?\d+)\s+líneas netas")

#: `int()` no acepta U+2212; se normaliza al menos ASCII antes de convertir.
_MINUS_SIGNS = str.maketrans({"−": "-", "+": ""})
# Un override humano del presupuesto se declara afirmando que el diff excede el
# presupuesto/límite/tope, no mencionando la palabra suelta. La negación ("no
# excede") queda fuera por el `(?<!no )`.
_HUMAN_BUDGET_OVERRIDE_RE = re.compile(
    r"(?<!no )excede\s+(el\s+)?(umbral|presupuesto|l[íi]mite|tope)", re.IGNORECASE
)
_LABEL_TO_KEY = {
    "qué": "que",
    "por qué": "por_que",
    "evidencia": "evidencia",
    "gate": "gate",
}


def classify(new_files: int, lines_net: int, contract_change: bool) -> Branch:
    if new_files == 0 and lines_net <= 5 and not contract_change:
        return "trivial"
    if new_files <= 1 and lines_net <= 50 and not contract_change:
        return "menor"
    return "no_trivial"


def validate_entry(entry: dict) -> None:
    """Mini-gate determinista (T4). Rechaza entradas inválidas.

    Valida forma (los campos obligatorios no vacíos) y, cuando el archivo lo
    declara, el umbral de `lines_net` (<= 50). No valida `contract_change`:
    una entrada con `contract_change = true` refleja una decisión humana ya
    tomada en el triaje (antes de escribirse la entrada) y pasa este chequeo
    sin objeción — ver el docstring del módulo.
    """
    required = ("id", "que", "por_que", "evidencia", "gate")
    for key in required:
        if not str(entry.get(key, "")).strip():
            raise ValueError(f"campo obligatorio vacío: {key!r}")
    if "lines_net" in entry and int(entry["lines_net"]) > 50:
        raise ValueError(
            f"lines_net={entry['lines_net']} excede el presupuesto de 50"
        )


def parse_entry_file(path: Path) -> dict:
    """Parsea un archivo de `.spec/cambios-menores/` a un dict de `validate_entry`.

    El archivo sigue la plantilla de 5 líneas: encabezado `## <fecha> — <id>`
    seguido de bullets `- qué:`, `- por qué:`, `- evidencia:`, `- gate:` (más
    bullets adicionales que este parser ignora). `lines_net` se extrae del
    texto de `gate`/`evidencia` cuando aparece un número —con signo opcional,
    para que un retiro puro cuente como negativo— seguido de "líneas
    netas"; si el propio `gate` ya declara que la entrada excede el umbral
    (mini-gate con override humano registrado en la bitácora), la extracción
    se omite para no revalidar automáticamente una excepción ya decidida.

    El dict resultante no incluye `contract_change`: este parser solo llena
    los campos que `validate_entry` compara contra forma y umbral de líneas;
    `contract_change` es la declaración humana del triaje, no un campo que
    la plantilla de 5 líneas registre para que este parser lo re-derive.
    """
    lines = path.read_text(encoding="utf-8").splitlines()
    header_match = _HEADER_RE.match(lines[0]) if lines else None
    entry_id = header_match.group(2) if header_match else ""

    fields = {"que": "", "por_que": "", "evidencia": "", "gate": ""}
    for line in lines[1:]:
        bullet_match = _BULLET_RE.match(line)
        if bullet_match:
            key = _LABEL_TO_KEY[bullet_match.group(1)]
            fields[key] = bullet_match.group(2).strip()

    entry: dict = {"id": entry_id, **fields}

    # El override humano se reconoce por la frase completa, no por la palabra
    # suelta: `"excede" in gate` daba por registrado un override ante cualquier
    # mención de la palabra, incluso negada ("no excede el límite habitual"),
    # y entonces `lines_net` no se extraía y el tope de líneas no se comparaba
    # con nada. Corregido el 2026-09-27.
    if not _HUMAN_BUDGET_OVERRIDE_RE.search(fields["gate"]):
        for field_name in ("gate", "evidencia"):
            net_match = _LINES_NET_RE.search(fields[field_name])
            if net_match:
                entry["lines_net"] = net_match.group(1).translate(_MINUS_SIGNS)
                break

    return entry


def _cmd_classify(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Fast-path classifier for SDD triage (0123)")
    p.add_argument("--new-files", type=int, default=0)
    p.add_argument("--lines-net", type=int, default=0)
    p.add_argument("--contract-change", action="store_true")
    args = p.parse_args(argv)
    print(classify(args.new_files, args.lines_net, args.contract_change))
    return 0


def _cmd_validate_entry(argv: list[str]) -> int:
    p = argparse.ArgumentParser(description="Validate a .spec/cambios-menores/ entry file")
    p.add_argument("archivo")
    args = p.parse_args(argv)
    entry_path = Path(args.archivo)
    entry = parse_entry_file(entry_path)
    try:
        validate_entry(entry)
    except ValueError as exc:
        print(f"INVALID: {entry_path}: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {entry_path}")
    return 0


def _main(argv: list[str]) -> int:
    if argv and argv[0] == "validate-entry":
        return _cmd_validate_entry(argv[1:])
    return _cmd_classify(argv)


if __name__ == "__main__":
    sys.exit(_main(sys.argv[1:]))
