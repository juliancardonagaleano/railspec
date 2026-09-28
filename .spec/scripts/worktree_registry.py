#!/usr/bin/env python3
"""Worktree registry and stop board for supervised parallelism (unit 0118, G4).

`supervised_parallel.py` is the only writer of this module's files; this module
just knows the two artifacts' schema and how to read/write them safely — it
does not decide which units are eligible or when to launch anything (T17/T19
own that).

Two files per mandate, both under `.spec/.supervised-runtime/<mandato-slug>/`
(gitignored, unit 0118 T21 — local ephemeral run state, never a versioned
mandate artifact):

    worktrees.json   `{"actualizado": <ISO-8601>, "worktrees": {<unidad-slug>:
                       {"ruta", "rama", "fase", "pid", "sesion",
                        "estado": "activo"|"fusionando"|"huerfano"|"retirado",
                        "seed-commit", "parada-condicion", "parada-causa",
                        "actualizado"}}, "parada-arbol-principal": {"codigo",
                       "causa", "detectado-en"}}` — schema fixed by `plan.md`
                       § Decisiones de diseño. Written atomically (tmp file in
                       the same directory, then `Path.replace`, i.e.
                       rename(2) — the write is either whole or absent, never
                       half-written).

                       `"fusionando"` (worktree orchestrator DAG/merge unit):
                       the child process finished (`final_fase == "done"`)
                       but the worktree is still alive — its merge back to
                       the main tree, and re-gate confirmation if the hybrid
                       rule requires it, are still pending. The entry only
                       becomes `"retirado"`/`fase: "done"` once the merge (and
                       re-gate, if needed) landed — this module never decides
                       that transition itself (`supervised_parallel.py` does),
                       consistent with this module's own no-eligibility-logic
                       contract below.

                       `seed-commit` (per-unit field): the main tree's `git
                       rev-parse HEAD` at the instant the worktree was
                       created — fixed for the worktree's whole life, never
                       re-seeded while it stays alive.

                       `parada-condicion` / `parada-causa` (per-unit fields):
                       a stop the orchestrator itself originates against that
                       one unit (e.g. a merge conflict) — same shape as a
                       mandate's own `## Paradas` entry fields, but never
                       written to `## Paradas` directly by this module or its
                       caller (that stays a `claude -p`/human session's act).

                       `parada-arbol-principal` (file-level field, not inside
                       any unit's entry): a stop not attributable to a single
                       unit — e.g. an orphaned `MERGE_HEAD` found on the main
                       tree with no legitimate lock holding it. Read (never
                       auto-cleared) before launching new worktrees; only a
                       human/`claude -p` session clears it, as part of writing
                       a resumption entry.

    paradas.md       Regenerated **whole** on every observed change (never
                       appended), grouped by the 14 typified stop conditions
                       of `.spec/PARADAS-SUPERVISADO.md` (number + literal
                       name, in file order), one `| Unidad | Condición |
                       Causa | Timestamp |` table per group with at least
                       one row.

Reconciliation against `git worktree list` (plan.md § Decisiones de diseño):
an entry whose `ruta` no longer has a real worktree is marked `huerfano`
(reported, never deleted on its own — a human retires or adopts it); a real
worktree under this mandate's own worktrees root with no matching entry is
added as `huerfano` and excluded from new launches. A worktree the developer
made for something unrelated is not scoped in and is left alone.

stdlib only, no network.
"""

from __future__ import annotations

import json
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from _common import (  # noqa: E402
    entries,
    guarded_subprocess,  # noqa: E402 (CA-21/CA-22: same choke-point as
    sections,
)

                                          # supervised_parallel.py — gate 0118/G4 L3 media)

STATES = ("activo", "fusionando", "huerfano", "retirado")

#: The 14 typified stop conditions (`.spec/PARADAS-SUPERVISADO.md`), number and
#: literal name, in the file's own order — `paradas.md` groups by exactly this
#: list, never a taxonomy invented here (`pol-ia-no-embeber-conocimiento`).
STOP_CONDITIONS: list[tuple[int, str]] = [
    (1, "Cambio destructivo en lectura amplia"),
    (2, "Decisión con alternativas válidas fuera de la delegación"),
    (3, "Divergencia entre plan y realidad"),
    (4, "Decisión de ### Reservadas"),
    (5, "Tarea reservada a presencia humana"),
    (6, "Fin del mandato"),
    (7, "Gate escalado"),
    (8, "MCP de gobernanza ausente o sin presupuesto"),
    (9, "Denegación del harness"),
    (10, "Único dueño del stack vivo"),
    (11, "Congelamiento por renombres"),
    (12, "No reconstruir la PCE con corridas en vuelo"),
    (13, "Cambio que crea o modifica un artefacto de gobernanza"),
    (14, "Fallo del validador de mandatos"),
]
UNTYPIFIED: tuple[int, str] = (0, "Sin tipificar")


def now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# ======================================================================================
# Paths
# ======================================================================================

def runtime_dir(scripts_dir: Path, mandate_slug: str) -> Path:
    return scripts_dir.parent / ".supervised-runtime" / mandate_slug


def registry_path(scripts_dir: Path, mandate_slug: str) -> Path:
    return runtime_dir(scripts_dir, mandate_slug) / "worktrees.json"


def board_path(scripts_dir: Path, mandate_slug: str) -> Path:
    return runtime_dir(scripts_dir, mandate_slug) / "paradas.md"


def worktrees_root(scripts_dir: Path, mandate_slug: str) -> Path:
    return runtime_dir(scripts_dir, mandate_slug) / "worktrees"


# ======================================================================================
# `worktrees.json` — atomic read/write
# ======================================================================================

def load(path: Path) -> dict:
    if not path.is_file():
        return {"actualizado": now_iso(), "worktrees": {}}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {"actualizado": now_iso(), "worktrees": {}}
    if not isinstance(data, dict):
        return {"actualizado": now_iso(), "worktrees": {}}
    data.setdefault("actualizado", now_iso())
    data.setdefault("worktrees", {})
    return data


def save(path: Path, data: dict) -> None:
    """tmp-file-in-same-dir + `Path.replace` (rename): the write lands whole or
    not at all, never half-written (plan.md § Decisiones de diseño)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    out = dict(data)
    out["actualizado"] = now_iso()
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".worktrees-", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            json.dump(out, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise


def set_entry(data: dict, unidad_slug: str, **fields: str) -> None:
    entry = data.setdefault("worktrees", {}).setdefault(unidad_slug, {})
    entry.update(fields)
    entry["actualizado"] = now_iso()


# ======================================================================================
# Reconciliation against real `git worktree list`
# ======================================================================================

def git_worktree_list(repo_root: Path) -> list[str]:
    """Real worktree paths, resolved and absolute (`git worktree list --porcelain`).

    Routed through `_common.guarded_subprocess` (CA-21/CA-22), the same choke-point
    `supervised_parallel.py` uses — this command is fixed and harmless today, but the
    guard is uniform across every subprocess launch this unit's G4 code makes, not
    verifiable-by-inspection on a per-file basis (gate 0118/G4, hallazgo L3 media)."""
    completed = guarded_subprocess(
        ["git", "-C", str(repo_root), "worktree", "list", "--porcelain"],
        capture_output=True, text=True, check=True,
    )
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if line.startswith("worktree "):
            paths.append(str(Path(line[len("worktree "):]).resolve()))
    return paths


def reconcile(data: dict, real_paths: list[str], scoped_root: Path) -> list[str]:
    """Reconciles `data["worktrees"]` in place; returns the human-readable report
    lines (plan.md § Decisiones de diseño, both directions documented at module
    level)."""
    report: list[str] = []
    real_set = {str(Path(p).resolve()) for p in real_paths}
    scoped_root_resolved = str(Path(scoped_root).resolve())

    worktrees = data.setdefault("worktrees", {})
    for slug, entry in worktrees.items():
        ruta = entry.get("ruta", "")
        if not ruta:
            continue
        if entry.get("estado") != "activo":
            continue
        if str(Path(ruta).resolve()) not in real_set:
            entry["estado"] = "huerfano"
            entry["actualizado"] = now_iso()
            report.append(f"{slug}: entrada activa sin worktree real — marcado huerfano ({ruta})")

    known_paths = {
        str(Path(e.get("ruta", "")).resolve())
        for e in worktrees.values() if e.get("ruta")
    }
    for path in sorted(real_set):
        if not (path == scoped_root_resolved or path.startswith(scoped_root_resolved + "/")):
            continue
        if path in known_paths:
            continue
        slug = Path(path).name
        worktrees[slug] = {
            "ruta": path, "rama": "", "fase": "", "pid": "", "sesion": "",
            "estado": "huerfano", "actualizado": now_iso(),
        }
        report.append(f"{slug}: worktree real sin entrada — añadido como huerfano ({path})")
    return report


# ======================================================================================
# `paradas.md` — regenerated whole, grouped by the 14 typified conditions
# ======================================================================================

def classify_condition(disparador: str) -> tuple[int, str]:
    """Maps a `disparador` string (as `## Paradas` entries write it — the leading
    condition number, or its literal name) to one of the 14 typified conditions.
    Falls back to `UNTYPIFIED` rather than guessing — a `disparador` that names
    neither a number nor a known literal is not silently folded into a random
    bucket."""
    text = (disparador or "").strip()
    m = re.match(r"^\s*(?:condici[oó]n\s+)?(\d{1,2})\b", text, re.IGNORECASE)
    if m:
        number = int(m.group(1))
        for cond in STOP_CONDITIONS:
            if cond[0] == number:
                return cond
    lowered = text.lower()
    for cond in STOP_CONDITIONS:
        if cond[1].lower() in lowered:
            return cond
    return UNTYPIFIED


def latest_stop_for_unit(mandate_text: str, unidad_slug: str) -> dict[str, str] | None:
    """Latest `## Paradas` entry of `mandate_text` relevant to `unidad_slug` — for a
    plan mandate, filtered by the entry's `unidad` field (several member units
    share the same `## Paradas` section); for an isolated unit's own
    `mandato.md`, every entry already concerns only that one unit. Reuses
    `_common.sections`/`entries`, never a parser of its own."""
    body = sections(mandate_text or "").get("## Paradas", "")
    latest_key, latest = "", None
    for key, fields, _ in entries(body):
        unidad_field = fields.get("unidad", "")
        if unidad_field and unidad_slug not in unidad_field:
            continue
        if key >= latest_key:
            latest_key, latest = key, fields
    if latest is None:
        return None
    resolved = dict(latest)
    resolved["fecha"] = latest_key
    return resolved


def render_board(stops: list[dict[str, str]]) -> str:
    """`stops`: each `{"unidad", "disparador", "causa", "timestamp"}`. Regenerated
    whole (plan.md § Decisiones de diseño), one table per non-empty group, in the
    fixed order of `.spec/PARADAS-SUPERVISADO.md`."""
    groups: dict[tuple[int, str], list[dict[str, str]]] = {c: [] for c in STOP_CONDITIONS}
    groups[UNTYPIFIED] = []
    for stop in stops:
        cond = classify_condition(stop.get("disparador", ""))
        groups.setdefault(cond, []).append(stop)

    lines = [
        "# Tablero de paradas — paralelismo por worktrees",
        "",
        f"> Regenerado {now_iso()} por `worktree_registry.py` (unidad 0118, G4, "
        "CA-20). Agrupado por las 14 condiciones de `.spec/PARADAS-SUPERVISADO.md`; "
        "no reproduce su texto normativo (`pol-ia-no-embeber-conocimiento`).",
        "",
    ]
    any_row = False
    for cond in [*STOP_CONDITIONS, UNTYPIFIED]:
        rows = groups.get(cond, [])
        if not rows:
            continue
        any_row = True
        number, name = cond
        heading = f"## {number}. {name}" if number else f"## {name}"
        lines += [heading, "", "| Unidad | Condición | Causa | Timestamp |", "|---|---|---|---|"]
        for row in rows:
            lines.append(
                f"| {row.get('unidad', '')} | {row.get('disparador', '')} | "
                f"{row.get('causa', '')} | {row.get('timestamp', '')} |"
            )
        lines.append("")
    if not any_row:
        lines += ["Sin paradas activas.", ""]
    return "\n".join(lines)


def write_board(path: Path, stops: list[dict[str, str]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = render_board(stops)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".paradas-", suffix=".tmp")
    try:
        with open(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
        Path(tmp_name).replace(path)
    except Exception:
        Path(tmp_name).unlink(missing_ok=True)
        raise
