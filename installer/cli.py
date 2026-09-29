#!/usr/bin/env python3
"""Punto de entrada único de sdd-kit.

Uso:
    python3 installer/cli.py --version
    python3 installer/cli.py [--target <ruta>]
    python3 installer/cli.py [--target <ruta>] --install [--force]
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

Códigos de salida: 0 correcto (incluye ``--version`` y verificar sin
divergencias); 1 verificar con al menos una divergencia; 2 error
operativo (manifiesto sin ``kit_version:``, o destino inválido); 3 instalar
abortado por colisión de ruta nueva sin posibilidad de ``--force`` (CA-40).
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

_INSTALLER_DIR = Path(__file__).resolve().parent
if str(_INSTALLER_DIR) not in sys.path:
    sys.path.insert(0, str(_INSTALLER_DIR))

from installer import run_install  # noqa: E402
from manifest import REPO_ROOT, ManifestError, get_kit_version  # noqa: E402
from verifier import run_verify  # noqa: E402

# Nombres de operación estables: esta es la única fuente que la ayuda
# imprime y que la documentación de entrada debe citar en el mismo orden.
OPERATIONS = ("verificar", "instalar")


def build_parser() -> argparse.ArgumentParser:
    description = (
        "Instala y verifica el kit SDD sobre un destino implícito (este "
        "repositorio) o explícito (--target).\n\n"
        f"Operaciones: {', '.join(OPERATIONS)}"
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
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

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
