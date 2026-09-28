"""Mutation testing for the closed set of validator codes.

Ensures every code in the set is actually caught by the validator when
injected into a malformed _estado.yaml. Each of the 30 codes is tested
individually via pytest.mark.parametrize.

CA: 0136-mutation-testing-codigos-validador
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
VALIDATOR = REPO / ".spec" / "scripts" / "validate_mandate.py"

OVERRIDES: dict[str, dict] = {}

sys.path.insert(0, str(REPO / ".spec" / "scripts"))
from sdd_safe_write_canonical import list_validator_codes  # noqa: E402 — needs sys.path set above first

VALIDATOR_CODES = list_validator_codes()


def _run_validator(tmp_dir: Path) -> subprocess.CompletedProcess:
    env = dict(__import__("os").environ)
    env.pop("SDD_GUARD_EXEMPT_TREE", None)
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "--unidad", str(tmp_dir)],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


class TestMutationValidatorCodes:
    """Pytest-native class so @pytest.mark.parametrize expands correctly."""

    def setup_method(self) -> None:
        assert len(VALIDATOR_CODES) == 30, (
            f"Validator codes regressed: expected 30, got {len(VALIDATOR_CODES)}"
        )

    @pytest.mark.parametrize("code", VALIDATOR_CODES)
    def test_each_code_caught(self, code: str) -> None:
        _tmp = Path(tempfile.mkdtemp(prefix="mutation-")).resolve()
        try:
            if code in OVERRIDES:
                body = "mandato: " + code + "\n"
                for k, v in OVERRIDES[code].items():
                    body += f"{k}: {v}\n"
                (_tmp / "_estado.yaml").write_text(body, encoding="utf-8")
            else:
                (_tmp / "_estado.yaml").write_text(
                    "mandato: " + code + "\n", encoding="utf-8"
                )
            result = _run_validator(_tmp)
            combined = result.stdout + result.stderr
            assert result.returncode != 0, (
                f"Validator did not reject code {code!r}"
            )
            assert code in combined, (
                f"Code {code!r} not found in validator output: {combined[:500]}"
            )
        finally:
            shutil.rmtree(_tmp, ignore_errors=True)
