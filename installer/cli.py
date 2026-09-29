#!/usr/bin/env python3
"""Punto de entrada único de sdd-kit.

Uso:
    python3 installer/cli.py --version
    python3 installer/cli.py [--target <ruta>]
    python3 installer/cli.py [--target <ruta>] --install [--force]
    python3 installer/cli.py doctor [--action <sujeto>]
    python3 installer/cli.py materialize <agents|commands|skills> [--check] [--dry-run] [--repo-root <ruta>]
    python3 installer/cli.py install-hook pre-push [--uninstall]
    python3 installer/cli.py --help

Sin flags de escritura, la operación por defecto es **verificar**: compara
el destino —implícito (este repositorio) o explícito (``--target``)— contra
el manifiesto del kit y no escribe nada. ``--install`` activa la operación
de **instalar**: escribe la carga del kit en el destino, regenera sus
espejos y deja instalado el gancho de pre-push. ``--force`` sólo tiene
efecto junto con ``--install``: autoriza borrar huérfanas con drift del
destino (la única semántica que ``--force`` controla; no hay otras — CA-22,
CA-30b). ``--version`` imprime por stdout el valor del campo ``kit_version:``
del manifiesto sin espacios ni líneas extra (CA-02); si el manifiesto no
declara ``kit_version:`` o está vacío, sale con código 2.

Los sub-comandos ``doctor``, ``materialize`` e ``install-hook`` (U-0006)
son añadidos, no sustituyen los flags top-level — los consumidores que
llaman ``installer/cli.py --install``/``--version``/``--target`` siguen
operando exactamente como antes (DD-3 de U-0006). ``doctor`` delega a
``installer.doctor.main``; ``materialize`` dispatcha entre los
``main`` de ``installer.materializers.{agents,commands,skills}``;
``install-hook pre-push`` invoca por subproceso
``scripts/install_pre_push_hook.sh`` con la misma firma que ya usa
``installer.installer._install_pre_push_hook``.

Códigos de salida: 0 correcto (incluye ``--version`` y verificar sin
divergencias); 1 verificar con al menos una divergencia; 2 error
operativo (manifiesto sin ``kit_version:``, o destino inválido); 3 instalar
abortado por colisión de ruta nueva sin posibilidad de ``--force`` (CA-40).
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

_INSTALLER_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _INSTALLER_DIR.parent

# Cuando se invoca como ``python3 installer/cli.py``, Python inserta
# ``installer/`` en ``sys.path[0]``. Esto bloquea ``from installer.X
# import ...`` porque Python ve ``installer/`` como un directorio de
# módulos top-level (no como un paquete). Lo retiramos y añadimos la raíz
# del repo, de modo que ``installer.X`` resuelve como namespace package
# (PEP 420) — U-0006.
sys.path[:] = [p for p in sys.path if Path(p).resolve() != _INSTALLER_DIR]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from installer.doctor import main as doctor_main  # noqa: E402
from installer.installer import run_install  # noqa: E402
from installer.manifest import REPO_ROOT, ManifestError, get_kit_version  # noqa: E402
from installer.materializers.agents import main as agents_main  # noqa: E402
from installer.materializers.commands import main as commands_main  # noqa: E402
from installer.materializers.skills import main as skills_main  # noqa: E402
from installer.verifier import run_verify  # noqa: E402

# Nombres de operación estables: esta es la única fuente que la ayuda
# imprime y que la documentación de entrada debe citar en el mismo orden.
OPERATIONS = ("verificar", "instalar")

# Materializadores disponibles para ``materialize``. Orden estable: el de la
# especificación del spec — agents, commands, skills.
MATERIALIZER_CHOICES = ("agents", "commands", "skills")
MATERIALIZER_DISPATCH = {
    "agents": agents_main,
    "commands": commands_main,
    "skills": skills_main,
}

HOOK_INSTALLER_REL = "scripts/install_pre_push_hook.sh"


def build_parser() -> argparse.ArgumentParser:
    description = (
        "Instala y verifica el kit SDD sobre un destino implícito (este "
        "repositorio) o explícito (--target).\n\n"
        f"Operaciones: {', '.join(OPERATIONS)}\n\n"
        "Sub-comandos: doctor, materialize, install-hook"
    )
    parser = argparse.ArgumentParser(
        prog="sdd-kit",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        description=description,
    )
    parser.add_argument(
        "--target",
        type=Path,
        default=None,
        metavar="<ruta>",
        help="Raíz del árbol de trabajo git destino. Por defecto, este repositorio.",
    )
    parser.add_argument(
        "--install",
        action="store_true",
        help="Activa la operación 'instalar' (por defecto: 'verificar').",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Con --install: autoriza borrar huérfanas con drift del destino "
             "(única semántica que controla --force; CA-22).",
    )
    parser.add_argument(
        "--version",
        action="store_true",
        help="Imprime el valor de `kit_version:` del manifiesto y sale.",
    )
    subparsers = parser.add_subparsers(dest="command")

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Diagnóstico del entorno agéntico local (sub-comando de "
             "installer.doctor).",
        description=(
            "Diagnóstico del entorno agéntico local: servidores MCP, índice "
            "de grafo, etc. Delega a ``installer.doctor.main``."
        ),
    )
    doctor_parser.add_argument(
        "--action",
        metavar="<sujeto>",
        help="Sujeto a remediar (si se omite, sólo diagnostica).",
    )

    materialize_parser = subparsers.add_parser(
        "materialize",
        help="Regenera los espejos .claude/<target>/ desde .agents/ "
             "(sub-comando de installer.materializers.*).",
        description=(
            "Regenera el espejo ``.claude/<target>/`` desde la fuente "
            "``.agents/``. Conserva --check/--dry-run/--repo-root del "
            "materializador original."
        ),
    )
    materialize_parser.add_argument(
        "target",
        choices=MATERIALIZER_CHOICES,
        metavar="<" + "|".join(MATERIALIZER_CHOICES) + ">",
        help="Espejo a regenerar.",
    )
    materialize_parser.add_argument(
        "--check",
        action="store_true",
        help="Sólo verifica drift; no escribe. Exit 0/1.",
    )
    materialize_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Muestra qué cambiaría sin escribir.",
    )
    materialize_parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        metavar="<ruta>",
        help="Raíz del repo destino (por defecto: este repositorio).",
    )

    install_hook_parser = subparsers.add_parser(
        "install-hook",
        help="Instala o retira el gancho de pre-push del worktree "
             "(subproceso a scripts/install_pre_push_hook.sh).",
        description=(
            "Invoca por subproceso ``scripts/install_pre_push_hook.sh`` con "
            "la misma firma que ``installer.installer._install_pre_push_hook``."
        ),
    )
    install_hook_parser.add_argument(
        "hook",
        choices=("pre-push",),
        metavar="<pre-push>",
        help="Tipo de gancho (hoy: pre-push).",
    )
    install_hook_parser.add_argument(
        "--uninstall",
        action="store_true",
        help="Retira el gancho en lugar de instalarlo.",
    )

    return parser


def _run_install_hook(target: Path, uninstall: bool) -> int:
    """Igual firma que ``installer.installer._install_pre_push_hook`` pero
    parámetrizable para ``--uninstall``."""
    hook_installer = target / HOOK_INSTALLER_REL
    argv = ["bash", str(hook_installer)]
    if uninstall:
        argv.append("--uninstall")
    result = subprocess.run(
        argv,
        cwd=target,
        capture_output=True,
        text=True,
    )
    if result.stdout:
        print(result.stdout, end="")
    if result.stderr:
        print(result.stderr, file=sys.stderr, end="")
    return result.returncode


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "doctor":
        return doctor_main(
            [] if not args.action else ["--action", args.action]
        )

    if args.command == "materialize":
        sub_argv: list[str] = []
        if args.check:
            sub_argv.append("--check")
        if args.dry_run:
            sub_argv.append("--dry-run")
        if args.repo_root is not None:
            sub_argv.extend(["--repo-root", str(args.repo_root)])
        return MATERIALIZER_DISPATCH[args.target](sub_argv)

    if args.command == "install-hook":
        target = args.target.resolve() if args.target is not None else REPO_ROOT
        return _run_install_hook(target, uninstall=args.uninstall)

    if args.version:
        try:
            print(get_kit_version())
        except ManifestError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 2
        return 0

    target = args.target.resolve() if args.target is not None else REPO_ROOT

    if args.install:
        return run_install(target, force=args.force)
    return run_verify(target)


if __name__ == "__main__":
    sys.exit(main())