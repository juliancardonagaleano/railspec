#!/usr/bin/env python3
"""Deterministic file→pytest/jest-subset mapper for `sdd-gate` (unit 0121, G4,
CA-11/CA-12).

Separates "obtener archivos tocados" (a thin wrapper over `git status
--porcelain=v1 --untracked-files=all`) from "mapear archivos → subset" (a pure
function, no git, testable with injected path lists — plan.md § Decisiones de
diseño). Covers exactly two subárboles: `orchestrator/` (pytest) and
`studio/app/` (Jest). Any other subárbol touched is out of this script's scope
— `comando_validacion` covers it unmodified.

Mapping rules, in order — any doubt discards the subset for that subárbol and
falls back to its full default command (never "run nothing", plan.md §
Riesgos):

    orchestrator/
        `orchestrator/tests/**/test_*.py` → added to the subset as-is.
        `orchestrator/src/iark_orchestrator/**/*.py` → looked up by stem
        (`tests/**/test_<stem>.py`); exactly one match → added; zero or more
        than one match → the whole `orchestrator/` subset is discarded.
        Any other touched file under `orchestrator/` (e.g. `pyproject.toml`,
        `conftest.py`) also discards the whole subset.

    studio/app/
        `studio/app/src/**` or a filename matching `*.spec.ts`/`*.test.ts` →
        added to the subset, all passed together to Jest's own
        `--findRelatedTests` (native dependency graph, not a naming
        convention of ours). Any other touched file under `studio/app/`
        (e.g. `jest.config.ts`, `package.json`) discards the whole subset.

If a subárbol has zero touched files, it emits `no-aplica` — no command for
it, not a fallback (CA-12: "no toca ninguno" is not a failure). If both
subárboles are `no-aplica`, the overall result is `SIN-SUBSET: no-aplica`.

Subcommands
-----------
    test_subset.py plan --root <repo-root> [--files <archivo> ...]
        Without `--files`: reads touched files via `git status`. With
        `--files`: uses that list instead (never calls git) — this is what
        `test_test_subset.py` exercises, one pure call per case. For each of
        the two subárboles prints one line: `SUBSET`, `FALLBACK`, or
        `NO-APLICA`, each with its command (if any) and reason. If both are
        `no-aplica`, prints a final `SIN-SUBSET: no-aplica` line. Exit 0
        always — this is an informational planning tool, never a pass/fail
        gate on its own (the fallback commands it prints are what actually
        runs and pass/fails).

No external dependencies: stdlib only (`argparse`, `re`, `shlex`, `subprocess`,
`sys`, `pathlib`).
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (
    _porcelain_paths,  # noqa: E402 — reutiliza el parseo de `git status` ya existente (pol-dev-buscar-antes-de-crear), en vez de reimplementarlo
)

ORCHESTRATOR_PREFIX = "orchestrator/"
ORCHESTRATOR_TESTS_PREFIX = "orchestrator/tests/"
ORCHESTRATOR_SRC_PREFIX = "orchestrator/src/iark_orchestrator/"
STUDIO_APP_PREFIX = "studio/app/"
STUDIO_APP_SRC_PREFIX = "studio/app/src/"

_TEST_SUFFIX = re.compile(r"\.(spec|test)\.ts$")


@dataclass
class SubtreeResult:
    subtree: str
    status: str  # "subset" | "fallback" | "no-aplica"
    command: str | None
    reason: str


# ======================================================================================
# "Obtener archivos tocados" — thin git wrapper, no mapping logic here.
# ======================================================================================

def get_touched_files(root: Path) -> list[str]:
    return _porcelain_paths(root)


# ======================================================================================
# "Mapear archivos → subset" — pure, no git, testable with injected lists.
# ======================================================================================

def _is_orch_test_file(f: str) -> bool:
    return f.startswith(ORCHESTRATOR_TESTS_PREFIX) and Path(f).name.startswith(
        "test_") and f.endswith(".py")


def _is_orch_src_file(f: str) -> bool:
    return f.startswith(ORCHESTRATOR_SRC_PREFIX) and f.endswith(".py")


def _find_test_by_stem(repo_root: Path, stem: str) -> list[Path]:
    tests_dir = repo_root / "orchestrator" / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob(f"**/test_{stem}.py"))


def map_orchestrator(files: list[str], repo_root: Path) -> tuple[list[str] | None, str]:
    """`(subset, reason)`. `subset is None` means "no subset for this
    subárbol" — `reason` distinguishes `no-aplica` (nothing touched) from a
    discard (something touched but not cleanly mappable)."""
    orch_files = [f for f in files if f.startswith(ORCHESTRATOR_PREFIX)]
    if not orch_files:
        return None, "no-aplica"

    subset: set[str] = set()
    for f in orch_files:
        if _is_orch_test_file(f):
            subset.add(f)
            continue
        if _is_orch_src_file(f):
            stem = Path(f).stem
            matches = _find_test_by_stem(repo_root, stem)
            if len(matches) == 1:
                subset.add(matches[0].relative_to(repo_root).as_posix())
                continue
            return None, (
                f"stem `{stem}` de {f}: {len(matches)} test(s) por convención "
                f"(se requiere exactamente 1)")
        return None, f"{f} no cae en ninguna convención mapeable"
    return sorted(subset), "ok"


def _is_studio_app_mappable(f: str) -> bool:
    rel = f[len(STUDIO_APP_PREFIX):]
    if rel.startswith("src/"):
        return True
    return bool(_TEST_SUFFIX.search(Path(f).name))


def map_studio_app(files: list[str]) -> tuple[list[str] | None, str]:
    """`(subset, reason)` — same `None`-with-reason convention as
    `map_orchestrator`. Pure: no filesystem access, Jest itself resolves the
    dependency graph when `--findRelatedTests` runs."""
    app_files = [f for f in files if f.startswith(STUDIO_APP_PREFIX)]
    if not app_files:
        return None, "no-aplica"

    subset: set[str] = set()
    for f in app_files:
        if _is_studio_app_mappable(f):
            subset.add(f)
            continue
        return None, f"{f} no es código/test de `src/` mapeable por Jest"
    return sorted(subset), "ok"


# ======================================================================================
# Comandos
# ======================================================================================

def orchestrator_result(files: list[str], repo_root: Path) -> SubtreeResult:
    subset, reason = map_orchestrator(files, repo_root)
    if reason == "no-aplica":
        return SubtreeResult("orchestrator", "no-aplica", None, reason)
    if subset is None:
        return SubtreeResult(
            "orchestrator", "fallback", "cd orchestrator && uv run pytest", reason)
    rel = [f[len(ORCHESTRATOR_PREFIX):] for f in subset]
    # `-m ""` anula el `-m "not integration"` que `addopts` inyecta por
    # defecto (unidad 0121, T3): sin esto, un archivo tocado marcado
    # `integration` produce un subset que deselecciona el 100% de sus propios
    # tests ("no tests ran"), violando el invariante de que el subset es
    # siempre superconjunto o igual del test del archivo tocado (hallazgo L2
    # del gate de código de esta unidad). `-m` es "último valor gana" en
    # pytest, igual que `-n` con xdist (ver `orchestrator/README.md`).
    command = (
        "cd orchestrator && uv run pytest -m \"\" "
        + " ".join(shlex.quote(r) for r in rel))
    return SubtreeResult("orchestrator", "subset", command, reason)


def studio_app_result(files: list[str]) -> SubtreeResult:
    subset, reason = map_studio_app(files)
    if reason == "no-aplica":
        return SubtreeResult("studio/app", "no-aplica", None, reason)
    if subset is None:
        return SubtreeResult(
            "studio/app", "fallback", "cd studio/app && npm test", reason)
    command = "npx --prefix studio/app jest --findRelatedTests " + \
        " ".join(shlex.quote(f) for f in subset)
    return SubtreeResult("studio/app", "subset", command, reason)


def plan(files: list[str], repo_root: Path) -> list[SubtreeResult]:
    return [orchestrator_result(files, repo_root), studio_app_result(files)]


# ======================================================================================
# CLI
# ======================================================================================

def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    files = args.files if args.files else get_touched_files(root)
    results = plan(files, root)
    for r in results:
        label = {"subset": "SUBSET", "fallback": "FALLBACK", "no-aplica": "NO-APLICA"}[r.status]
        if r.command:
            print(f"{label} — {r.subtree}: {r.command}  ({r.reason})")
        else:
            print(f"{label} — {r.subtree}: {r.reason}")
    if all(r.status == "no-aplica" for r in results):
        print("SIN-SUBSET: no-aplica — comando_validacion completo aplica sin recorte "
              "para estos dos subárboles.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="test_subset.py",
        description="Mapeo determinista archivo tocado → subset de test "
                     "(pytest/Jest) para la capa determinista de sdd-gate "
                     "en `fase: codigo` (unidad 0121, CA-11/CA-12).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    plan_parser = sub.add_parser(
        "plan", help="subset (o fallback) por subárbol para los archivos tocados")
    plan_parser.add_argument("--root", required=True, metavar="DIR",
                              help="raíz del repo (para `git status` y para resolver "
                                   "el stem de orchestrator/)")
    plan_parser.add_argument("--files", nargs="*", metavar="ARCHIVO",
                              help="lista de archivos tocados; si se omite, se obtiene "
                                   "de `git status` sobre --root")
    plan_parser.set_defaults(func=cmd_plan)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
