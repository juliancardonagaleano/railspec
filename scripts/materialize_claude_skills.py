#!/usr/bin/env python3
"""Shim — delega al módulo ``installer.materializers.skills`` (U-0006).

La lógica vive en ``installer/materializers/skills.py``; este archivo sólo
reexporta ``main`` y propaga ``sys.argv[1:]`` — CA-03, CA-04, CA-11.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from installer.materializers.skills import main  # noqa: E402

if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))