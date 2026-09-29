"""Unit 0002 — CA-30 parte (b): busca las frases del contrato viejo en
`installer/cli.py` y en el docstring de módulo de `installer/installer.py`,
y falla si sobreviven."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INSTALLER_PY = REPO_ROOT / "installer" / "installer.py"
CLI_PY = REPO_ROOT / "installer" / "cli.py"

LEGACY_PHRASES = (
    "sobrescribí conflictos",
    "sobrescriba conflictos",
    "aborta el run por conflictos",
)


def _grep_literal(path: Path, literal: str) -> bool:
    if not path.is_file():
        return False
    result = subprocess.run(
        ["grep", "-lIF", "-e", literal, str(path)],
        capture_output=True,
        text=True,
    )
    return result.returncode == 0


def test_u0002_ca30b_no_legacy_force_phrases() -> None:
    found: list[str] = []
    for phrase in LEGACY_PHRASES:
        if _grep_literal(INSTALLER_PY, phrase) or _grep_literal(CLI_PY, phrase):
            found.append(phrase)
    assert not found, (
        f"CA-30 — sobreviven frases del contrato viejo en installer/cli.py o installer.py: {found}"
    )


def test_u0002_ca30b_install_record_is_no_longer_called_bookkeeping_only() -> None:
    text = INSTALLER_PY.read_text(encoding="utf-8")
    forbidden = (
        "never read for this classification",
        "never read for this",
        "bookkeeping only",
    )
    found = [phrase for phrase in forbidden if phrase in text]
    assert not found, f"CA-30a — sobreviven frases que niegan lectura del registro: {found}"


def test_u0002_ca30b_install_docstring_describes_new_contract() -> None:
    text = INSTALLER_PY.read_text(encoding="utf-8")
    assert "drift" in text
    assert "desactualizada" in text
    assert "huérfana" in text or "huérfanas" in text