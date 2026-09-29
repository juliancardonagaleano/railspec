"""Tests de `check_governance_surface.py` (unit 0003, fix 7).

Cubre CA-01, CA-02, CA-03, CA-06, CA-07, CA-15, CA-16a, CA-21 del spec
de 0003. Los tests usan `tmp_path` (sin I/O fuera del argumento) excepto
`test_integration_*` que apuntan al filesystem real del kit.

Convenciones:
- `u0003_ca<NN>_*` para que el gate de tasks los pueda coleccionar.
- Cada test arma un `tmp_path` con un spec sintético y un filesystem
  sintético; no comparten estado entre tests.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SCRIPT = REPO_ROOT / ".spec" / "scripts" / "check_governance_surface.py"


def _write_spec(tmp_path: Path, paths: list[str]) -> Path:
    spec = tmp_path / "spec.md"
    inner = ", ".join(f'"{p}"' for p in paths)
    spec.write_text(
        f"# Test spec\n\n## Configuration contract\n\n"
        f"```\nconfiguration_contract: [{inner}]\n```\n",
        encoding="utf-8",
    )
    return spec


def _run(unit: Path, spec: Path, root: Path) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT),
         "--unit", str(unit),
         "--spec", str(spec),
         "--filesystem-root", str(root)],
        capture_output=True,
        text=True,
    )


# --- u0003_ca01: contract present + every declared path exists → ok ---

def test_u0003_ca01_contract_consistent(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("# AGENTS\n")
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    spec = _write_spec(tmp_path, [".mcp.json", "AGENTS.md"])
    r = _run(tmp_path, spec, tmp_path)
    assert r.returncode == 0, r.stderr
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "ok"
    assert payload["declared"] == [".mcp.json", "AGENTS.md"]
    assert payload["found"] == [".mcp.json"]


# --- u0003_ca02: declared path missing → configuration_contract-inconsistente ---

def test_u0003_ca02_declared_missing(tmp_path: Path) -> None:
    spec = _write_spec(tmp_path, [".mcp.json", "AGENTS.md"])
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    r = _run(tmp_path, spec, tmp_path)
    assert r.returncode == 2, r.stderr
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "configuration_contract-inconsistente"
    assert any("AGENTS.md" in c for c in payload["causes"])


# --- u0003_ca03: file with MCP but not in contract → superficie-no-declarada ---

def test_u0003_ca03_undeclared_governance(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "opencode.jsonc").write_text(json.dumps({"mcp": {}}))
    spec = _write_spec(tmp_path, [".mcp.json"])
    r = _run(tmp_path, spec, tmp_path)
    assert r.returncode == 1, r.stderr
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "superficie-no-declarada"
    assert any("opencode.jsonc" in c for c in payload["causes"])


# --- u0003_ca06: filesystem limpio sin contrato → ok con nota ---

def test_u0003_ca06_no_contract_clean_filesystem(tmp_path: Path) -> None:
    spec = tmp_path / "spec.md"
    spec.write_text("# Test spec\n", encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n")
    r = _run(tmp_path, spec, tmp_path)
    assert r.returncode == 0
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "ok"
    assert payload["declared"] == []
    assert any("configuration_contract" in n for n in payload["notes"])


# --- u0003_ca07: opencode.jsonc declaring pce-mcp sin contrato → superficie-no-declarada ---

def test_u0003_ca07_opencode_undeclared(tmp_path: Path) -> None:
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "opencode.jsonc").write_text(
        json.dumps({"mcp": {"pce-mcp": {"type": "local"}}})
    )
    spec = _write_spec(tmp_path, [".mcp.json"])
    r = _run(tmp_path, spec, tmp_path)
    assert r.returncode == 1
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "superficie-no-declarada"
    assert "opencode.jsonc" in payload["found"]
    assert ".mcp.json" not in [c.split("'")[1] for c in payload["causes"]]


# --- u0003_ca15: --help + funciones puras testeables con tmp_path ---

def test_u0003_ca15_help(tmp_path: Path) -> None:
    r = subprocess.run(
        [sys.executable, str(SCRIPT), "--help"],
        capture_output=True, text=True,
    )
    assert r.returncode == 0
    assert "--filesystem-root" in r.stdout
    assert "configuration_contract" in r.stdout or "configuration contract" in r.stdout.lower()


def test_u0003_ca15_pure_function_compute_fs_hash(tmp_path: Path) -> None:
    from check_governance_surface import compute_fs_hash
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "b.txt").write_text("world")
    h1 = compute_fs_hash(tmp_path)
    assert len(h1) == 64
    h2 = compute_fs_hash(tmp_path)
    assert h1 == h2
    (tmp_path / "c.txt").write_text("!")
    h3 = compute_fs_hash(tmp_path)
    assert h3 != h1


def test_u0003_ca15_pure_function_find_declared_governance(tmp_path: Path) -> None:
    from check_governance_surface import find_declared_governance
    (tmp_path / ".mcp.json").write_text(json.dumps({"mcpServers": {}}))
    (tmp_path / "opencode.jsonc").write_text(json.dumps({"mcp": {}}))
    (tmp_path / "AGENTS.md").write_text("# AGENTS\nmenciona pce-mcp en prosa\n")
    (tmp_path / "README.md").write_text("menciona pce-mcp en prosa pero no es config\n")
    found = find_declared_governance(tmp_path)
    assert ".mcp.json" in found
    assert "opencode.jsonc" in found
    assert "AGENTS.md" not in found
    assert "README.md" not in found


# --- u0003_ca16a: smoke test contra la unit 0001 cerrada ---

def test_u0003_ca16a_smoke_unit_0001() -> None:
    """El script contra la unit 0001 (cerrada antes del scope delta de 0002)
    detecta superficie no declarada — porque su spec no tiene
    `configuration_contract:` y el filesystem sí tiene `.mcp.json` y
    `opencode.jsonc`. Esto es **esperado**: el script nuevo es más estricto
    que la triada literal (que perdió `opencode.jsonc` en el incidente del
    2026-09-29). El veredicto correcto es `superficie-no-declarada`, no
    `ok` — la unit 0001 tendría que actualizar su spec para declarar la
    configuration_contract, lo cual queda fuera del alcance de 0003 (es
    trabajo de una unit posterior que se re-correrá cuando 0003 llegue a
    done)."""
    unit_0001_spec = REPO_ROOT / ".spec" / "units" / "0001-versionado-y-actualizacion-del-kit" / "spec.md"
    assert unit_0001_spec.is_file(), "spec de 0001 no existe"
    r = _run(REPO_ROOT / ".spec" / "units" / "0001-versionado-y-actualizacion-del-kit",
             unit_0001_spec, REPO_ROOT)
    payload = json.loads(r.stdout)
    if ".mcp.json" in payload["found"] or "opencode.jsonc" in payload["found"]:
        assert payload["verdict"] == "superficie-no-declarada", (
            f"Si el filesystem tiene .mcp.json u opencode.jsonc, el script "
            f"debe detectarlo como superficie no declarada; got {payload}"
        )
    else:
        assert payload["verdict"] == "ok"


# --- u0003_ca21: smoke test del script contra el filesystem real del kit ---

@pytest.mark.skipif(
    not all((REPO_ROOT / p).exists() for p in [".mcp.json", "opencode.jsonc", "AGENTS.md"]),
    reason="kit no tiene los 3 archivos de governance cargados; saltar",
)
def test_u0003_ca21_kit_self_consistent() -> None:
    """El script contra el filesystem real del kit (post-delta de 0002):
    AGENTS.md + .mcp.json + opencode.jsonc deben aparecer como encontrados
    y estar en el configuration_contract del spec de 0003."""
    spec_0003 = REPO_ROOT / ".spec" / "units" / "0003-fiabilidad-de-gates-de-gobernanza" / "spec.md"
    assert spec_0003.is_file()
    r = _run(REPO_ROOT / ".spec" / "units" / "0003-fiabilidad-de-gates-de-gobernanza",
             spec_0003, REPO_ROOT)
    payload = json.loads(r.stdout)
    assert payload["verdict"] == "ok", f"kit debe ser self-consistent; got {payload}"
    assert ".mcp.json" in payload["found"]
    assert "opencode.jsonc" in payload["found"]
    assert ".mcp.json" in payload["declared"]
    assert "opencode.jsonc" in payload["declared"]
    assert "AGENTS.md" in payload["declared"]
    assert payload["fs_hash"]


# --- u0003_ca04 / ca05 (hash check + atajo) cubierto por el script en sí ---

def test_u0003_ca04_ca05_fs_hash_changes(tmp_path: Path) -> None:
    """Si el filesystem cambia, el fs_hash del script cambia; un gate que
    comparase dos instantáneas los detectaría distintos."""
    from check_governance_surface import compute_fs_hash
    (tmp_path / "a.txt").write_text("v1")
    h1 = compute_fs_hash(tmp_path)
    (tmp_path / "a.txt").write_text("v2")
    h2 = compute_fs_hash(tmp_path)
    assert h1 != h2
