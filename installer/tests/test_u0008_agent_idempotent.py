"""Unit 0008 — CA-09: idempotencia del cableado sobre la sección `agent`.

Simétrico a U-0007 CA-10. Dos llamadas consecutivas a
`mezclar_opencode_jsonc` sobre el mismo `tmp_path` deben producir la
sección `agent` byte-idéntica, y la pasada `mcp` debe quedar intacta
tras las dos llamadas (no regresión U-0007).
"""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KIT_ROOT = REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "installer"))

import config_cableado  # noqa: E402


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def test_u0008_ca09_agent_section_idempotent_two_passes(tmp_path: Path) -> None:
    target = tmp_path / "agent-idempotent"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text('{"mcp": {}, "agent": {}}', encoding="utf-8")

    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    first = json.loads(jsonc.read_text(encoding="utf-8"))

    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    second = json.loads(jsonc.read_text(encoding="utf-8"))

    assert first["agent"] == second["agent"], (
        f"agent no idempotente: primera pasada tiene {sorted(first['agent'])}; "
        f"segunda tiene {sorted(second['agent'])}"
    )


def test_u0008_ca09_agent_keys_stable_in_insertion_order(tmp_path: Path) -> None:
    target = tmp_path / "agent-order"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        json.dumps(
            {
                "agent": {
                    "agente-del-operador": {"model": "azure/claude-sonnet-5-5"},
                    "sdd-implementador": {"_sdd_kit": True, "model": "old"},
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    keys = list(data["agent"].keys())
    assert keys[0] == "agente-del-operador", (
        f"el entry ajeno del operador debe preservarse en su posición original; "
        f"keys={keys}"
    )
    assert "sdd-implementador" in keys


def test_u0008_ca09_mcp_section_intact_after_two_agent_passes(tmp_path: Path) -> None:
    """No regresión U-0007: la pasada `mcp` sobrevive a la pasada `agent`."""
    target = tmp_path / "agent-and-mcp-idempotent"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text('{"mcp": {}, "agent": {}}', encoding="utf-8")

    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)

    data = json.loads(jsonc.read_text(encoding="utf-8"))
    assert "pce-mcp" in data["mcp"]
    assert data["mcp"]["pce-mcp"]["_sdd_kit"] is True
    assert data["mcp"]["pce-mcp"]["env"]["MCP_PCE_CACHE_TTL_SECS"] == "86400"
    assert data["mcp"]["pce-mcp"]["env"]["SSL_CERT_FILE"].endswith(
        "pce-valvula-0040.pem"
    )


def test_u0008_ca09_byte_identical_disk_after_two_passes(tmp_path: Path) -> None:
    """Disco byte-idéntico entre dos `--install` consecutivos."""
    target = tmp_path / "agent-byte-identical"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text('{"mcp": {}, "agent": {}}', encoding="utf-8")

    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    first_text = jsonc.read_text(encoding="utf-8")

    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    second_text = jsonc.read_text(encoding="utf-8")

    assert first_text == second_text
