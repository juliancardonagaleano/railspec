#!/usr/bin/env python3
"""Deterministic file→test-subset mapper — motor genérico del kit para
`sdd-gate` (capa determinista de `fase: codigo`).

Separates "obtener archivos tocados" (a thin wrapper over `git status
--porcelain=v1 --untracked-files=all`) from "mapear archivos → subset" (a
pure function, no git, testable with injected path lists).

A diferencia del selector de origen (donde este archivo se generó), los
subárboles de test **no están cableados en el código**: se resuelven desde
`subarboles_test` de `.spec/protocolo-datos.yaml` del destino (`--protocolo-
datos` > variable de entorno `SDD_PROTOCOLO_DATOS_PATH` > archivo de reposo
en la raíz del destino — mismo contrato de precedencia que
`validate_protocol_drift.py`, ambos vía `_common.resolve_protocolo_datos_path`).
Cada entrada declara `ruta` (prefijo del subárbol), `runner` (`pytest` |
`jest`) y `comando_completo` (fallback). El *runner* decide qué convención de
mapeo aplica — es mecanismo genérico de cualquier protocolo SDD, no dato de
un destino:

    runner: pytest
        `<ruta>tests/**/test_*.py` tocado → se agrega tal cual al subset.
        Cualquier otro `.py` bajo `<ruta>` (fuera de `tests/`) → se busca por
        stem bajo `<ruta>tests/**/test_<stem>.py`; exactamente un match →
        se agrega; cero o más de uno → se descarta todo el subset del
        subárbol. Cualquier archivo no-`.py` bajo `<ruta>` también descarta
        el subset completo.

    runner: jest
        `<ruta>src/**` o un archivo cuyo nombre matchee `*.spec.ts`/
        `*.test.ts` → se agrega al subset, pasado a Jest vía
        `--findRelatedTests` (dependencia nativa, no convención de nombres).
        Cualquier otro archivo bajo `<ruta>` descarta el subset completo.

Si un subárbol no tiene archivos tocados, emite `no-aplica` — no hay comando
para él, no es un fallback. Si todos los subárboles son `no-aplica`, el
resultado global es `SIN-SUBSET: no-aplica`.

Subcommands
-----------
    test_subset.py plan --root <repo-root> [--files <archivo> ...]
                         [--protocolo-datos <ruta>]
        Sin `--files`: lee los archivos tocados vía `git status`. Con
        `--files`: usa esa lista en vez (nunca llama a git). Por cada
        subárbol imprime una línea `SUBSET`, `FALLBACK` o `NO-APLICA`, con su
        comando (si aplica) y motivo. Exit 0 siempre — herramienta
        informativa de planeación, nunca un gate de pass/fail por sí misma.

No external dependencies: stdlib only (`argparse`, `re`, `shlex`, `subprocess`,
`sys`, `pathlib`) más `_common` (sibling, mismo directorio).
"""

from __future__ import annotations

import argparse
import re
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _common import (  # noqa: E402 — reutiliza el parseo de `git status` y de `protocolo-datos.yaml` ya existentes (pol-dev-buscar-antes-de-crear)
    ProtocoloDatosError,
    _porcelain_paths,
    block_list_of_dicts,
    load_protocolo_datos,
    resolve_protocolo_datos_path,
)

_TEST_SUFFIX_TS = re.compile(r"\.(spec|test)\.ts$")


@dataclass(frozen=True)
class Subarbol:
    ruta: str              # p. ej. "backend/" — siempre con "/" final
    runner: str            # "pytest" | "jest"
    comando_completo: str  # comando de fallback (corrida completa del subárbol)


@dataclass
class SubtreeResult:
    subtree: str
    status: str  # "subset" | "fallback" | "no-aplica"
    command: str | None
    reason: str


# ======================================================================================
# Configuración de destino — subárboles resueltos, no cableados.
# ======================================================================================

def load_subarboles(protocolo_datos_flag: str | None, repo_root: Path) -> list[Subarbol]:
    path, _origen = resolve_protocolo_datos_path(protocolo_datos_flag, repo_root)
    text = load_protocolo_datos(path)
    raw = block_list_of_dicts(text, "subarboles_test")
    subarboles: list[Subarbol] = []
    for i, entry in enumerate(raw):
        for key in ("ruta", "runner", "comando_completo"):
            if key not in entry:
                raise ProtocoloDatosError(
                    f"{path}: entrada {i} de `subarboles_test` sin `{key}`"
                )
        ruta = entry["ruta"] if entry["ruta"].endswith("/") else entry["ruta"] + "/"
        subarboles.append(Subarbol(ruta=ruta, runner=entry["runner"],
                                    comando_completo=entry["comando_completo"]))
    return subarboles


# ======================================================================================
# "Obtener archivos tocados" — thin git wrapper, no mapping logic here.
# ======================================================================================

def get_touched_files(root: Path) -> list[str]:
    return _porcelain_paths(root)


# ======================================================================================
# "Mapear archivos → subset" — pure, no git, testable with injected lists.
# ======================================================================================

def _find_test_by_stem(repo_root: Path, ruta: str, stem: str) -> list[Path]:
    tests_dir = repo_root / ruta / "tests"
    if not tests_dir.is_dir():
        return []
    return sorted(tests_dir.glob(f"**/test_{stem}.py"))


def map_pytest_subtree(files: list[str], repo_root: Path, ruta: str) -> tuple[list[str] | None, str]:
    """`(subset, reason)`. `subset is None` significa "sin subset para este
    subárbol" — `reason` distingue `no-aplica` (nada tocado) de un descarte
    (algo tocado pero no mapeable con limpieza)."""
    tests_prefix = ruta + "tests/"
    sub_files = [f for f in files if f.startswith(ruta)]
    if not sub_files:
        return None, "no-aplica"

    subset: set[str] = set()
    for f in sub_files:
        name = Path(f).name
        if f.startswith(tests_prefix) and name.startswith("test_") and f.endswith(".py"):
            subset.add(f)
            continue
        if f.endswith(".py"):
            stem = Path(f).stem
            matches = _find_test_by_stem(repo_root, ruta, stem)
            if len(matches) == 1:
                subset.add(matches[0].relative_to(repo_root).as_posix())
                continue
            return None, (
                f"stem `{stem}` de {f}: {len(matches)} test(s) por convención "
                f"(se requiere exactamente 1)")
        return None, f"{f} no cae en ninguna convención mapeable"
    return sorted(subset), "ok"


def _is_jest_mappable(f: str, ruta: str) -> bool:
    rel = f[len(ruta):]
    if rel.startswith("src/"):
        return True
    return bool(_TEST_SUFFIX_TS.search(Path(f).name))


def map_jest_subtree(files: list[str], ruta: str) -> tuple[list[str] | None, str]:
    """`(subset, reason)` — misma convención `None`-con-motivo que
    `map_pytest_subtree`. Pura: sin acceso a filesystem, Jest mismo resuelve
    el grafo de dependencias cuando corre `--findRelatedTests`."""
    sub_files = [f for f in files if f.startswith(ruta)]
    if not sub_files:
        return None, "no-aplica"

    subset: set[str] = set()
    for f in sub_files:
        if _is_jest_mappable(f, ruta):
            subset.add(f)
            continue
        return None, f"{f} no es código/test de `src/` mapeable por Jest"
    return sorted(subset), "ok"


def _build_subset_command(sub: Subarbol, rel_files: list[str]) -> str:
    """Compone el comando de subset a partir de `comando_completo` (la corrida
    completa) más la lista de archivos, con la convención propia de cada
    *runner*:

      pytest: los archivos se agregan como argumentos posicionales, más
      `-m ""` al final para anular cualquier `-m "not integration"` (u otro
      marker exclusion) que el `addopts` del destino inyecte por defecto —
      sin este override, un archivo tocado marcado con ese marker produciría
      un subset que deselecciona el 100% de sus propios tests. `-m` es
      "último valor gana" en pytest.

      jest: se asume que `comando_completo` es un script npm que reenvía
      argumentos extra tras `--` (convención estándar de `package.json`);
      se le agrega ` -- --findRelatedTests <archivos>`.
    """
    quoted = " ".join(shlex.quote(r) for r in rel_files)
    if sub.runner == "pytest":
        return f'{sub.comando_completo} -m "" {quoted}'
    if sub.runner == "jest":
        return f"{sub.comando_completo} -- --findRelatedTests {quoted}"
    raise ValueError(f"runner desconocido: {sub.runner!r}")


def subtree_result(files: list[str], repo_root: Path, sub: Subarbol) -> SubtreeResult:
    label = sub.ruta.rstrip("/")
    if sub.runner == "pytest":
        subset, reason = map_pytest_subtree(files, repo_root, sub.ruta)
    elif sub.runner == "jest":
        subset, reason = map_jest_subtree(files, sub.ruta)
    else:
        return SubtreeResult(label, "fallback", sub.comando_completo,
                              f"runner desconocido `{sub.runner}` — se corre la completa")

    if reason == "no-aplica":
        return SubtreeResult(label, "no-aplica", None, reason)
    if subset is None:
        return SubtreeResult(label, "fallback", sub.comando_completo, reason)
    rel = [f[len(sub.ruta):] for f in subset]
    command = _build_subset_command(sub, rel)
    return SubtreeResult(label, "subset", command, reason)


def plan(files: list[str], repo_root: Path, subarboles: list[Subarbol]) -> list[SubtreeResult]:
    return [subtree_result(files, repo_root, sub) for sub in subarboles]


# ======================================================================================
# Comandos
# ======================================================================================

def cmd_plan(args: argparse.Namespace) -> int:
    root = Path(args.root)
    try:
        subarboles = load_subarboles(args.protocolo_datos, root)
    except ProtocoloDatosError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    files = args.files if args.files else get_touched_files(root)
    results = plan(files, root, subarboles)
    for r in results:
        label = {"subset": "SUBSET", "fallback": "FALLBACK", "no-aplica": "NO-APLICA"}[r.status]
        if r.command:
            print(f"{label} — {r.subtree}: {r.command}  ({r.reason})")
        else:
            print(f"{label} — {r.subtree}: {r.reason}")
    if all(r.status == "no-aplica" for r in results):
        print("SIN-SUBSET: no-aplica — comando_validacion completo aplica sin recorte "
              "para estos subárboles.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="test_subset.py",
        description="Mapeo determinista archivo tocado → subset de test "
                     "(pytest/Jest) para la capa determinista de sdd-gate "
                     "en `fase: codigo` — subárboles resueltos desde "
                     "`.spec/protocolo-datos.yaml` del destino.",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    plan_parser = sub.add_parser(
        "plan", help="subset (o fallback) por subárbol para los archivos tocados")
    plan_parser.add_argument("--root", required=True, metavar="DIR",
                              help="raíz del repo (para `git status`, para resolver "
                                   "stems de convención pytest, y como raíz por defecto "
                                   "de `.spec/protocolo-datos.yaml`)")
    plan_parser.add_argument("--files", nargs="*", metavar="ARCHIVO",
                              help="lista de archivos tocados; si se omite, se obtiene "
                                   "de `git status` sobre --root")
    plan_parser.add_argument("--protocolo-datos", metavar="RUTA", default=None,
                              help="ruta explícita a protocolo-datos.yaml (> env "
                                   "SDD_PROTOCOLO_DATOS_PATH > .spec/protocolo-datos.yaml "
                                   "de --root)")
    plan_parser.set_defaults(func=cmd_plan)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
