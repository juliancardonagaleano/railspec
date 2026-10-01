"""Construye el binario autocontenido ``railspec`` con PyInstaller.

Uso (desde cualquier directorio, con el Python del entorno de construcción, que
ya debe tener instalado ``requirements.lock``)::

    python railspec/packages/railspec-local/empaquetado/construir.py [--salida DIR] [--trabajo DIR]

Pasos:

1. Construye las ruedas de railspec-contracts y railspec-local desde una copia
   temporal de sus fuentes (así no quedan ``build/`` ni ``*.egg-info`` en el
   repositorio) y las instala sin dependencias ni aislamiento: las de terceros
   vienen fijadas con hashes en ``requirements.lock``.
2. Corre PyInstaller con ``railspec.spec``.

Deja ``<salida>/railspec`` (por defecto ``empaquetado/dist/railspec``).
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

AQUI = Path(__file__).resolve().parent
PAQUETES = AQUI.parent.parent  # railspec/packages
PROPIOS = ("railspec-contracts", "railspec-local")
IGNORAR = shutil.ignore_patterns("build", "dist", "*.egg-info", "__pycache__", "tests", "empaquetado")


def _correr(*args: str | Path) -> None:
    print("+", " ".join(str(a) for a in args), flush=True)
    subprocess.run([str(a) for a in args], check=True)


def instalar_propios() -> None:
    with tempfile.TemporaryDirectory(prefix="railspec-ruedas-") as tmp:
        tmp_path = Path(tmp)
        ruedas = tmp_path / "ruedas"
        for nombre in PROPIOS:
            copia = tmp_path / nombre
            shutil.copytree(PAQUETES / nombre, copia, ignore=IGNORAR)
            _correr(
                sys.executable, "-m", "pip", "wheel", "--quiet", "--no-deps", "--no-build-isolation",
                "--wheel-dir", ruedas, copia,
            )  # fmt: skip
        _correr(
            sys.executable, "-m", "pip", "install", "--quiet", "--no-deps", "--no-index", "--force-reinstall",
            *sorted(ruedas.glob("*.whl")),
        )  # fmt: skip


def congelar(salida: Path, trabajo: Path) -> Path:
    _correr(
        sys.executable, "-m", "PyInstaller", "--noconfirm", "--clean",
        "--distpath", salida, "--workpath", trabajo, AQUI / "railspec.spec",
    )  # fmt: skip
    binario = salida / "railspec"
    if not binario.is_file():
        raise SystemExit(f"PyInstaller no dejó {binario}")
    return binario


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("--salida", type=Path, default=AQUI / "dist", help="Dónde dejar el binario.")
    p.add_argument(
        "--trabajo", type=Path, default=AQUI / "build", help="Directorio de trabajo de PyInstaller."
    )
    p.add_argument("--sin-instalar", action="store_true", help="No reinstala los paquetes propios.")
    args = p.parse_args(argv)
    if not args.sin_instalar:
        instalar_propios()
    binario = congelar(args.salida.resolve(), args.trabajo.resolve())
    print(f"binario: {binario} ({binario.stat().st_size / 1e6:.1f} MB)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
