"""Tests for `agent_tool_discipline_telemetry.py` (unit 0154, G1 -- CA-22, CA-23).

The script is opt-in: `SDD_TOOL_DISCIPLINE_LOG=1` must be set, otherwise it
exits 0 without writing. All tests set the env via `monkeypatch.setenv`.

The CA-23a/b/d cases drive `process()` directly with a fake payload and
`log_root=tmp_path`. CA-23c invokes the script as a subprocess to verify that
an upstream failure (empty/closed stdin) does not raise and still returns 0.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "agent_tool_discipline_telemetry.py"
sys.path.insert(0, str(SCRIPTS))

import agent_tool_discipline_telemetry as atd  # noqa: E402

LOG_REL = Path(".spec") / ".usage" / "tool-discipline.log"
FIXED_TS = "2026-09-23T13:44:00Z"


def _payload(command: str, *, cwd: str, session: str = "sess-abc") -> dict:
    return {
        "tool_name": "Bash",
        "tool_input": {"command": command},
        "session_id": session,
        "cwd": cwd,
    }


def _read_log(root: Path) -> str:
    return (root / LOG_REL).read_text(encoding="utf-8")


# --- CA-23a --------------------------------------------------------------
# `cat foo.py` -> exactly one line, `pattern=cat`, formato CA-11.

def test_cat_command_writes_one_line_with_pattern_cat(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    rc = atd.process(_payload("cat foo.py", cwd=str(tmp_path)),
                     log_root=tmp_path, now=FIXED_TS)
    assert rc == atd.EXIT_OK
    lines = _read_log(tmp_path).splitlines()
    assert len(lines) == 1
    line = lines[0]
    assert "\tpattern=cat\t" in line
    # Formato CA-11: ISO-8601 con Z, tabs, command=<sha256[:8]>.
    assert re.match(
        r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\t"
        r"session=sess-abc\ttool=Bash\tpattern=cat\tcommand=[a-f0-9]{8,}$",
        line,
    ), f"línea no cumple CA-11: {line!r}"


# --- CA-23b --------------------------------------------------------------
# `pytest` no matchea ninguno de los patrones -> no se escribe nada.

def test_pytest_command_writes_no_line(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    rc = atd.process(_payload("pytest", cwd=str(tmp_path)),
                     log_root=tmp_path, now=FIXED_TS)
    assert rc == atd.EXIT_OK
    assert not (tmp_path / LOG_REL).exists()


# --- CA-23c --------------------------------------------------------------
# El harness puede crashear antes de alimentar stdin al script: la tubería
# llega vacía. El hook debe retornar 0 sin raise (CA-12).

def test_subprocess_with_closed_stdin_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="", capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    # No se escribió nada (payload vacío no tiene cwd real ni match).
    assert not (tmp_path / LOG_REL).exists()


# Variante: payload malformado (upstream entrega basura) tampoco rompe.

def test_subprocess_with_malformed_payload_exits_zero(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    proc = subprocess.run(
        [sys.executable, str(SCRIPT)],
        input="esto no es json", capture_output=True, text=True, check=False,
    )
    assert proc.returncode == 0
    assert "ERROR" in proc.stderr  # diagnóstico en stderr (CA-22)
    assert not (tmp_path / LOG_REL).exists()


# --- CA-23d --------------------------------------------------------------
# Si el árbol `.spec/.usage/` desapareció entre dos invocaciones, la
# siguiente llamada debe recrearlo sin raise (idempotencia de escritura).

def test_recreates_log_dir_when_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    atd.process(_payload("cat a.py", cwd=str(tmp_path)),
                log_root=tmp_path, now=FIXED_TS)
    assert len(_read_log(tmp_path).splitlines()) == 1
    shutil.rmtree(tmp_path / ".spec")
    rc = atd.process(_payload("cat b.py", cwd=str(tmp_path)),
                     log_root=tmp_path, now="2026-09-23T13:45:00Z")
    assert rc == atd.EXIT_OK
    text = _read_log(tmp_path)
    assert len(text.splitlines()) == 1
    assert "pattern=cat" in text


# --- extra: opt-in -------------------------------------------------------
# Sin `SDD_TOOL_DISCIPLINE_LOG=1`, no se escribe nada aunque el comando
# matchee (P1 resuelto en `plan.md`).

def test_no_env_var_means_no_write(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.delenv("SDD_TOOL_DISCIPLINE_LOG", raising=False)
    rc = atd.process(_payload("cat foo.py", cwd=str(tmp_path)),
                     log_root=tmp_path, now=FIXED_TS)
    assert rc == atd.EXIT_OK
    assert not (tmp_path / LOG_REL).exists()


# --- extra: append acumulado --------------------------------------------
# Dos invocaciones que matchean en la misma corrida escriben dos líneas
# en orden, sin pisarse (atomicidad POSIX de `O_APPEND`).

def test_repeated_match_appends_two_lines(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    monkeypatch.setenv("SDD_TOOL_DISCIPLINE_LOG", "1")
    atd.process(_payload("cat a.py", cwd=str(tmp_path)),
                log_root=tmp_path, now="2026-09-23T13:44:00Z")
    atd.process(_payload("ls -la", cwd=str(tmp_path)),
                log_root=tmp_path, now="2026-09-23T13:44:01Z")
    lines = _read_log(tmp_path).splitlines()
    assert len(lines) == 2
    assert "pattern=cat" in lines[0]
    assert "pattern=ls" in lines[1]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v"]))
