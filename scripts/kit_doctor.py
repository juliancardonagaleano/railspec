#!/usr/bin/env python3
"""Shim — delega al módulo ``installer.doctor`` (U-0006).

La lógica vive en ``installer/doctor.py``; este archivo sólo reexporta
``main`` y propaga ``sys.argv[1:]``. Conserva el shebang y la firma de
entrada para que ``python3 scripts/kit_doctor.py [--action <s>]`` siga
funcionando con los mismos exit codes que la versión histórica — CA-13.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.doctor import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))