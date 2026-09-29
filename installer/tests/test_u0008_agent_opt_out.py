"""Unit 0008 — CA-07/CA-08: opt-out del cableado con marcador sobre `agent`.

Cuatro casos cubiertos (simétricos a U-0007 CA-07/CA-08/CA-09/CA-11):

- (a) destino con `agent.<rol>` **sin** `_sdd_kit: true` → entry del
  destino preservado intacto, el kit NO agrega su versión (opt-out,
  CA-07 parte 1).
- (b) destino con `agent.<rol>` **con** `_sdd_kit: true` y `model`
  arbitrario distinto al del kit → entry reemplazado atómicamente,
  `model` final idéntico al del kit (CA-07 parte 2, simétrico
  U-0007 CA-09).
- (c) destino con `agent.<rol-ajeno>` sin `_sdd_kit` (otro rol declarado
  por el operador, no por el kit) → entry preservado sin cambios tras
  la fusión (CA-08, simétrico U-0007 CA-11).
- (d) destino sin `agent.<rol-kit>` declarado → el entry del kit se
  agrega con `_sdd_kit: true` y `model` del kit (smoke del merge para
  instalaciones limpias).
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


def _kit_agent_entry(rol: str) -> dict:
    """Lee el entry declarado por el kit en `opencode.jsonc`."""
    data = json.loads(KIT_ROOT.joinpath("opencode.jsonc").read_text(encoding="utf-8"))
    return data["agent"][rol]


# ----------------------- CA-07 parte 1: opt-out en agent -----------------------


def test_u0008_ca07_mezclar_opencode_jsonc_preserves_unmarked_agent_entry(
    tmp_path: Path,
) -> None:
    target = tmp_path / "agent-opt-out"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        json.dumps(
            {
                "agent": {
                    "sdd-implementador": {
                        "_sdd_kit": False,
                        "model": "azure/claude-opus-4-8",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    entry = data["agent"]["sdd-implementador"]
    assert entry.get("_sdd_kit") is not True
    assert entry["model"] == "azure/claude-opus-4-8"


# ----------------------- CA-07 parte 2: reemplazo atómico ----------------------


def test_u0008_ca07_atomic_replacement_with_marker_overwrites_model(
    tmp_path: Path,
) -> None:
    target = tmp_path / "agent-atomic-replace"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        json.dumps(
            {
                "agent": {
                    "sdd-implementador": {
                        "_sdd_kit": True,
                        "model": "azure/claude-opus-4-8",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    entry = data["agent"]["sdd-implementador"]
    kit_entry = _kit_agent_entry("sdd-implementador")
    assert entry["_sdd_kit"] is True
    assert entry["model"] == kit_entry["model"]


# ----------------------- CA-08: entries ajenos sin _sdd_kit sobreviven ---------


def test_u0008_ca08_unmarked_others_survive_in_agent_section(tmp_path: Path) -> None:
    target = tmp_path / "agent-others"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        json.dumps(
            {
                "agent": {
                    "agente-del-operador": {
                        "model": "azure/claude-sonnet-5-5",
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    assert "agente-del-operador" in data["agent"]
    other = data["agent"]["agente-del-operador"]
    assert other["model"] == "azure/claude-sonnet-5-5"
    assert other.get("_sdd_kit") is not True


# ----------------------- (d) smoke: destino sin entry del kit, se agrega ------


def test_u0008_kit_entry_added_when_dest_lacks_it(tmp_path: Path) -> None:
    target = tmp_path / "agent-fresh-install"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text('{"agent": {}}', encoding="utf-8")
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    kit_entry = _kit_agent_entry("sdd-implementador")
    assert "sdd-implementador" in data["agent"]
    entry = data["agent"]["sdd-implementador"]
    assert entry["_sdd_kit"] is True
    assert entry["model"] == kit_entry["model"]


# ----------------------- mcp no se afecta por la pasada de agent --------------


def test_u0008_mcp_section_unchanged_after_agent_pass(tmp_path: Path) -> None:
    """Smoke test: la pasada sobre `agent` no toca `mcp` (no regresión U-0007)."""
    target = tmp_path / "agent-and-mcp"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text('{"mcp": {}, "agent": {}}', encoding="utf-8")
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    assert "pce-mcp" in data["mcp"]
    assert data["mcp"]["pce-mcp"]["_sdd_kit"] is True
    assert data["mcp"]["pce-mcp"]["env"]["MCP_PCE_CACHE_TTL_SECS"] == "86400"
