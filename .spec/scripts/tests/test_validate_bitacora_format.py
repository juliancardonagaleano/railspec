"""Tests for validate_bitacora_format.py — unit 0151."""

import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "validate_bitacora_format.py"


def run_validator(bitacora_path):
    return subprocess.run(
        [sys.executable, str(SCRIPT), str(bitacora_path)],
        capture_output=True,
        text=True,
    )


def run_stdin(text):
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--stdin"],
        input=text,
        capture_output=True,
        text=True,
    )


class TestCompactFormatValid:
    def test_single_entry(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("## 2026-09-23T10:00Z · fase:spec · spec redactado y validado\n")
        result = run_validator(path)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_multiple_entries(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text(
            "# Bitácora — test\n\n"
            "## 2026-09-23T10:00Z · fase:spec · spec redactado\n"
            "## 2026-09-23T10:30Z · gate:spec:aprobado · sin hallazgos abiertos\n"
            "## 2026-09-23T11:00Z · fase:plan · plan redactado\n"
        )
        result = run_validator(path)
        assert result.returncode == 0
        assert result.stderr == ""

    def test_gate_with_veredicto(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("## 2026-09-23T10:30Z · gate:spec:refinado · 2 iteraciones\n")
        result = run_validator(path)
        assert result.returncode == 0


class TestEmptyOrAbsent:
    def test_empty_file(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("")
        result = run_validator(path)
        assert result.returncode == 0

    def test_whitespace_only(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("\n  \n")
        result = run_validator(path)
        assert result.returncode == 0

    def test_absent_file(self, tmp_path):
        path = tmp_path / "bitacora.md"
        result = run_validator(path)
        assert result.returncode == 0


class TestLegacyWarning:
    def test_legacy_multi_line(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text(
            "## 2026-09-23T10:00Z · fase:spec · spec redactado\n"
            "**Qué hice:** redactar spec\n"
            "**Dónde quedó:** fase spec\n"
            "**Siguiente paso:** planificar\n"
        )
        result = run_validator(path)
        assert result.returncode == 0
        assert "AVISO" in result.stderr or "legacy" in result.stderr

    def test_legacy_gate_entry(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text(
            "## 2026-09-23T11:00Z · gate:spec:refinado · 2 iteraciones\n"
            "**Qué criticó cada lente:** L1 encontro 3 hallazgos\n"
            "**Qué se corrigió:** todos\n"
            "**Qué se descartó:** ninguno\n"
            "**Qué quedó abierto:** ninguno\n"
        )
        result = run_validator(path)
        assert result.returncode == 0
        assert "AVISO" in result.stderr or "legacy" in result.stderr


class TestUnparseable:
    def test_random_text(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("esto no es una entrada valida de bitacora\n")
        result = run_validator(path)
        assert result.returncode == 1
        assert "ERROR" in result.stderr

    def test_malformed_header(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text("## not a timestamp · fase:spec · resumen\n")
        result = run_validator(path)
        assert result.returncode == 1

    def test_mixed_valid_and_invalid(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text(
            "## 2026-09-23T10:00Z · fase:spec · entrada valida\n"
            "linea sin formato\n"
            "## 2026-09-23T11:00Z · fase:plan · otra valida\n"
        )
        result = run_validator(path)
        assert result.returncode == 1


class TestDocHeaders:
    def test_doc_header_ignored(self, tmp_path):
        path = tmp_path / "bitacora.md"
        path.write_text(
            "# Bitácora — test\n"
            "> comentario\n"
            "**Convención de merge** alguna\n"
            "## 2026-09-23T10:00Z · fase:spec · resumen\n"
        )
        result = run_validator(path)
        assert result.returncode == 0

    def test_stdin_mode(self):
        result = run_stdin("## 2026-09-23T10:00Z · fase:spec · resumen\n")
        assert result.returncode == 0
