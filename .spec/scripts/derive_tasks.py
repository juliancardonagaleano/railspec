#!/usr/bin/env python3
"""Deterministic derivation of `tasks.md` from a `plan.md` in the recognized
shape (unit 0116, CA-07a/CA-07b/CA-08/CA-09).

Subcommands
-----------
    derive_tasks.py generate --plan <plan.md> --spec <spec.md> --out <tasks.md>
        If `plan.md`'s "## Grupos de tareas paralelizables" table has the
        recognized shape (`Grupo`, `Alcance`, `Archivos` with concrete paths,
        `Depende de`, and the additive `Cubre CA-NN` column whose union covers
        100% of the `CA-NN` declared in `spec.md`), writes `tasks.md` with
        **zero model calls** (CA-07a) and a generation metadata line marking
        its origin as deterministic (CA-07b). Exit 0 on success.
        If the shape is not recognized, exits 3 (not an error: `sdd-tareas`
        falls back to the drafted path, CA-08) and writes nothing.
        Exits 1 on a real error (missing file, malformed table).

    derive_tasks.py check-plan --plan <plan.md> --spec <spec.md>
        Idéntica validación que `generate` pero sin escribir archivo: exit 0
        si la tabla está en la forma reconocida, exit 3 si no, exit 1 en error
        real. Útil como gate determinista en `sdd-gate` para que el flujo
        falle rápido si el redactor emitió un plan que no se puede derivar
        (0124-derivar-tasks-siempre-que-plan-de-de-la-forma-esperada eleva
        esto a precondición del gate de plan).

    derive_tasks.py verify --tasks <tasks.md>
        Recomputes `tasks_sha256` of a deterministically-derived `tasks.md`
        (the file content with the `> origen:` line itself excluded) and
        compares it against the hash declared in that line. A mismatch means
        a human edited the file after it was generated: the line is rewritten
        to `origen: determinista+forzado-humano · tasks_sha256: <new hash>`
        so the forcing is recorded, never lost silently (CA-09). Exit 0 in
        every non-error outcome (match, forced-rewrite, or origin that is not
        `determinista*` — nothing to verify); exit 1 on a real error (missing
        file, malformed `origen:` line).

Reuses `_common.py:sha256_text()` for the hash (plan.md § Decisiones de
diseño) — no local hasher is defined here, the same rule a retired ritual
script once broke and this unit does not repeat.

stdlib only: `argparse`, `re`, `sys`, `pathlib`.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import sections, sha256_text  # noqa: E402

CA_TOKEN = re.compile(r"CA-\d+[a-z]?")
ORIGEN_LINE = re.compile(r"^> origen: (?P<origen>[^\n·]+?)\s*·\s*(?P<detalle>.*)$",
                          re.MULTILINE)
TASKS_SHA_TOKEN = re.compile(r"tasks_sha256:\s*([0-9a-f]{64})")
PLAN_TITLE = re.compile(r"^# Plan técnico\s+—\s+(.+)$", re.MULTILINE)


# --- Reading `plan.md` --------------------------------------------------------------

def _table_rows(section_body: str) -> tuple[list[str], list[list[str]]] | None:
    """Header + data rows of the first markdown pipe table in `section_body`.

    Returns `None` if there is no pipe table at all.
    """
    lines = [ln for ln in section_body.splitlines()]
    table_lines = [ln for ln in lines if ln.strip().startswith("|")]
    if len(table_lines) < 2:
        return None

    def split_row(line: str) -> list[str]:
        inner = line.strip()
        if inner.startswith("|"):
            inner = inner[1:]
        if inner.endswith("|"):
            inner = inner[:-1]
        return [cell.strip() for cell in inner.split("|")]

    header = split_row(table_lines[0])
    # table_lines[1] is expected to be the separator (---|---|...); skip it if so.
    data_start = 1
    if re.match(r"^[\s:|-]+$", table_lines[1]):
        data_start = 2
    rows = [split_row(ln) for ln in table_lines[data_start:]]
    return header, rows


def _normalize_header(cell: str) -> str:
    return re.sub(r"\s+", " ", cell.strip().lower())


def parse_groups(plan_text: str) -> list[dict[str, str]] | None:
    """Groups of "## Grupos de tareas paralelizables" if the table has the
    recognized shape (header includes `cubre ca-nn`); `None` otherwise."""
    section = sections(plan_text).get("## Grupos de tareas paralelizables")
    if section is None:
        return None
    table = _table_rows(section)
    if table is None:
        return None
    header, rows = table
    normalized = [_normalize_header(h) for h in header]
    required = ["grupo", "alcance", "archivos", "depende de", "cubre ca-nn"]
    if not all(name in normalized for name in required):
        return None
    idx = {name: normalized.index(name) for name in normalized if name in required}
    groups: list[dict[str, str]] = []
    seen_grupo: set[str] = set()
    for row in rows:
        if len(row) != len(header):
            continue
        record = {name: row[idx[name]] for name in required}
        if not record["grupo"] or not record["archivos"]:
            continue
        # A placeholder ("...") means "Archivos" is not made of concrete paths.
        if "..." in record["archivos"]:
            return None
        if not record["cubre ca-nn"]:
            return None
        # A duplicate "Grupo" name would silently collapse in `group_to_task`
        # (dict keyed by that same text) when `_render_tasks_body` maps
        # "Depende de" back to task ids — ambiguous input, not a shape this
        # generator can derive safely. Camino redactado (CA-08).
        grupo_key = record["grupo"].strip()
        if grupo_key in seen_grupo:
            return None
        seen_grupo.add(grupo_key)
        groups.append(record)
    if not groups:
        return None
    return groups


def spec_ca_ids(spec_text: str) -> list[str]:
    """`CA-NN` ids declared as checklist items in `spec.md` (`- [ ] CA-07a — ...`)."""
    found = re.findall(r"^-\s*\[[ x]\]\s*(CA-\d+[a-z]?)\b", spec_text, re.MULTILINE)
    return sorted(set(found))


def coverage_is_complete(groups: list[dict[str, str]], spec_cas: list[str]) -> bool:
    """100% coverage rule (CA-07a): every `spec_cas` id is covered by the union
    of `Cubre CA-NN` across groups. A covering token without a letter suffix
    (`CA-07`) covers every lettered variant of the same number (`CA-07a`,
    `CA-07b`), same shorthand this unit's own `tasks.md` already uses."""
    covered_exact: set[str] = set()
    covered_roots: set[str] = set()
    for group in groups:
        for token in CA_TOKEN.findall(group["cubre ca-nn"]):
            covered_exact.add(token)
            if re.fullmatch(r"CA-\d+", token):
                covered_roots.add(token)
    for ca in spec_cas:
        root = re.match(r"CA-\d+", ca).group()
        if ca not in covered_exact and root not in covered_roots:
            return False
    return bool(spec_cas)


# --- `generate` ------------------------------------------------------------

def _plan_title(plan_text: str) -> str:
    m = PLAN_TITLE.search(plan_text)
    return m.group(1).strip() if m else "unidad"


def _render_tasks_body(title: str, groups: list[dict[str, str]]) -> str:
    """Full `tasks.md` content, `> origen:` line excluded (it is inserted by
    the caller once the hash of this exact text is known)."""
    group_to_task = {g["grupo"].strip(): f"T{i}" for i, g in enumerate(groups, start=1)}

    def map_depends(raw: str) -> str:
        raw = raw.strip()
        if not raw or raw in {"—", "-"}:
            return "—"
        mapped = []
        for token in re.split(r",\s*", raw):
            token = token.strip()
            mapped.append(group_to_task.get(token, token))
        return ", ".join(mapped)

    lines = [
        f"# Tareas — {title}",
        "",
        "> Fase 3-4. Checklist derivado de `plan.md` (generado deterministamente por "
        "`derive_tasks.py generate`, cero llamadas a modelo — CA-07a). **Fuente de "
        "verdad resumible**: el estado de estas casillas indica qué falta. Marca "
        "`[x]` al completar cada tarea.",
        ">",
        "> Cada tarea declara los criterios de aceptación de `spec.md` que cubre "
        "(`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en "
        "ninguna tarea.",
        "",
        "## Pendientes",
        "",
    ]
    for i, group in enumerate(groups, start=1):
        task_id = f"T{i}"
        lines.append(
            f"- [ ] {task_id} — {group['alcance']} · archivos: `{group['archivos']}` "
            f"· depende de: {map_depends(group['depende de'])} · cubre: "
            f"{group['cubre ca-nn']}"
        )
    lines += [
        "",
        "## Validación final",
        "",
        "- [ ] Ejecutar comando de validación (ver `plan.md`)",
        "- [ ] Verificar criterios de aceptación de `spec.md` (uno por uno, por id)",
        "- [ ] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto "
        "registrado",
        "- [ ] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`",
        "",
        "## Notas de implementación",
        "",
        "Detalles que surjan al implementar (decisiones puntuales, desvíos del "
        "plan).",
        "",
    ]
    return "\n".join(lines)


def cmd_generate(args: argparse.Namespace) -> int:
    plan_path, spec_path, out_path = Path(args.plan), Path(args.spec), Path(args.out)
    for p in (plan_path, spec_path):
        if not p.is_file():
            print(f"ERROR — no existe {p}", file=sys.stderr)
            return 1

    plan_text = plan_path.read_text(encoding="utf-8")
    spec_text = spec_path.read_text(encoding="utf-8")

    groups = parse_groups(plan_text)
    if groups is None:
        print(
            "FORMA NO RECONOCIDA — `plan.md` no tiene la tabla de Grupos con "
            "columna `Cubre CA-NN` y rutas concretas en `Archivos`. Camino "
            "redactado (CA-08).", file=sys.stderr,
        )
        return 3

    spec_cas = spec_ca_ids(spec_text)
    if not coverage_is_complete(groups, spec_cas):
        print(
            "FORMA NO RECONOCIDA — la unión de `Cubre CA-NN` no cubre el 100% de "
            f"los {len(spec_cas)} CA-NN de spec.md. Camino redactado (CA-08).",
            file=sys.stderr,
        )
        return 3

    title = _plan_title(plan_text)
    body = _render_tasks_body(title, groups)
    # `tasks_sha256` covers "the rest of the file" (plan.md § Decisiones de
    # diseño): everything from `## Pendientes` onward, deliberately excluding
    # the header/intro above the origen line, which never changes once the
    # file exists — this is also what makes `verify` able to recompute it
    # without reconstructing exact blank-line spacing around the origen line.
    split_at = body.index("## Pendientes")
    header, rest = body[:split_at], body[split_at:]
    digest = sha256_text(rest)
    final_text = f"{header}> origen: determinista · tasks_sha256: {digest}\n\n{rest}"
    out_path.write_text(final_text, encoding="utf-8")
    print(
        f"PASS — determinista: {len(groups)} grupo(s) → {len(groups)} tarea(s), "
        f"0 llamadas a modelo, {len(spec_cas)} CA-NN cubiertos. Escrito en {out_path}."
    )
    return 0


# --- `check-plan` -----------------------------------------------------------

def cmd_check_plan(args: argparse.Namespace) -> int:
    """Valida sin escribir. Exit 0 = forma OK; exit 3 = no derivable;
    exit 1 = error real (archivo inexistente, etc.).

    Usado por `sdd-gate` para fallar rápido si el redactor emitió un plan
    que no satisface la forma. A partir de 0124-derivar-tasks, el gate de
    plan requiere exit 0 aquí — el camino redactado (CA-08) queda como
    legacy documentado pero no se acepta en el flujo normal.
    """
    plan_path, spec_path = Path(args.plan), Path(args.spec)
    for p in (plan_path, spec_path):
        if not p.is_file():
            print(f"ERROR — no existe {p}", file=sys.stderr)
            return 1
    plan_text = plan_path.read_text(encoding="utf-8")
    spec_text = spec_path.read_text(encoding="utf-8")
    groups = parse_groups(plan_text)
    if groups is None:
        print(
            f"FORMA NO RECONOCIDA — {plan_path} no tiene la tabla de Grupos con "
            "columna `Cubre CA-NN` y rutas concretas en `Archivos`.",
            file=sys.stderr,
        )
        return 3
    spec_cas = spec_ca_ids(spec_text)
    if not coverage_is_complete(groups, spec_cas):
        print(
            "FORMA NO RECONOCIDA — la unión de `Cubre CA-NN` no cubre el 100% de "
            f"los {len(spec_cas)} CA-NN de spec.md.",
            file=sys.stderr,
        )
        return 3
    print(f"PASS — {plan_path} en forma derivable ({len(groups)} grupo(s), "
          f"{len(spec_cas)} CA-NN cubiertos).")
    return 0


# --- `verify` ----------------------------------------------------------------

def _rest_after_origen(text: str, match: re.Match[str]) -> str:
    """Content strictly after the `> origen:` line — the same slice `generate`
    hashed (`## Pendientes` onward). Leading newlines are stripped so the exact
    blank-line spacing around the origen line need not be reconstructed."""
    return text[match.end():].lstrip("\n")


def cmd_verify(args: argparse.Namespace) -> int:
    tasks_path = Path(args.tasks)
    if not tasks_path.is_file():
        print(f"ERROR — no existe {tasks_path}", file=sys.stderr)
        return 1

    text = tasks_path.read_text(encoding="utf-8")
    match = ORIGEN_LINE.search(text)
    if match is None:
        print(f"ERROR — {tasks_path} no tiene línea `> origen: ...`", file=sys.stderr)
        return 1

    origen = match.group("origen").strip()
    if not origen.startswith("determinista"):
        print(f"N/A — origen: {origen}, `verify` no aplica.")
        return 0

    detalle = match.group("detalle")
    sha_match = TASKS_SHA_TOKEN.search(detalle)
    if sha_match is None:
        print(
            f"ERROR — {tasks_path} declara origen `{origen}` sin `tasks_sha256:` "
            "verificable", file=sys.stderr,
        )
        return 1
    declared = sha_match.group(1)

    actual = sha256_text(_rest_after_origen(text, match))

    if actual == declared:
        print(f"PASS — tasks_sha256 coincide ({actual[:12]}…): sin edición humana "
              "detectada tras la derivación.")
        return 0

    new_line = f"> origen: determinista+forzado-humano · tasks_sha256: {actual}"
    updated = ORIGEN_LINE.sub(lambda _m: new_line, text, count=1)
    tasks_path.write_text(updated, encoding="utf-8")
    print(
        f"FORZADO — tasks_sha256 no coincide (declarado {declared[:12]}…, real "
        f"{actual[:12]}…): {tasks_path} fue editado a mano tras generarse. Línea "
        "de origen reescrita a `determinista+forzado-humano` (CA-09)."
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="derive_tasks.py",
        description="Derivación determinista de tasks.md desde plan.md (CA-07/CA-08/CA-09).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    generate_parser = sub.add_parser(
        "generate", help="deriva tasks.md de plan.md si la forma es reconocida")
    generate_parser.add_argument("--plan", required=True, metavar="PLAN.MD")
    generate_parser.add_argument("--spec", required=True, metavar="SPEC.MD")
    generate_parser.add_argument("--out", required=True, metavar="TASKS.MD")
    generate_parser.set_defaults(func=cmd_generate)

    check_plan_parser = sub.add_parser(
        "check-plan",
        help="valida que plan.md esté en forma derivable (sin escribir tasks.md)",
    )
    check_plan_parser.add_argument("--plan", required=True, metavar="PLAN.MD")
    check_plan_parser.add_argument("--spec", required=True, metavar="SPEC.MD")
    check_plan_parser.set_defaults(func=cmd_check_plan)

    verify_parser = sub.add_parser(
        "verify", help="recalcula tasks_sha256 y detecta forzado humano")
    verify_parser.add_argument("--tasks", required=True, metavar="TASKS.MD")
    verify_parser.set_defaults(func=cmd_verify)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
