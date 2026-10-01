"""Punto de entrada del binario congelado: lo mismo que el script ``railspec``.

PyInstaller necesita un archivo de arranque; el entry point de consola del
pyproject (``railspec.local.cli:main``) no le sirve directamente.
"""

import sys

from railspec.local.cli import main

if __name__ == "__main__":
    sys.exit(main())
