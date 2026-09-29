"""Unit 0007 — CA-07/CA-08/CA-09/CA-11: opt-out del cableado con marcador.

Cuatro casos cubiertos:

- (a) `mezclar_mcp_json` no agrega `pce-mcp` del kit cuando el destino ya
  tiene `pce-mcp` sin `_sdd_kit: true` (CA-07).
- (b) simétrico para `mezclar_opencode_jsonc` (CA-08).
- (c) cuando el destino trae `pce-mcp` **con** `_sdd_kit: true` y un `env`
  arbitrario, el reemplazo atómico deja el `env` del kit tal cual (CA-09).
- (d) otros `mcpServers`/`mcp` declarados por el destino sin `_sdd_kit`
  sobreviven al `--install` sin cambios (CA-11).
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


# ----------------------- CA-07: opt-out en mezclar_mcp_json -----------------------


def test_u0007_ca07_mezclar_mcp_json_preserves_unmarked_entry(tmp_path: Path) -> None:
    target = tmp_path / "mcp-opt-out"
    _git_init(target)
    mcp = target / ".mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "pce-mcp": {
                        "type": "stdio",
                        "command": "sh",
                        "args": ["scripts/wrapper-del-operador.sh"],
                        "env": {"FOO": "bar"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    data = json.loads(mcp.read_text(encoding="utf-8"))
    pce = data["mcpServers"]["pce-mcp"]
    assert pce.get("_sdd_kit") is not True
    assert pce["args"] == ["scripts/wrapper-del-operador.sh"]
    assert pce["env"] == {"FOO": "bar"}
    assert pce["type"] == "stdio"


# ----------------------- CA-08: opt-out en mezclar_opencode_jsonc -----------------


def test_u0007_ca08_mezclar_opencode_jsonc_preserves_unmarked_entry(tmp_path: Path) -> None:
    target = tmp_path / "opencode-opt-out"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        '{\n  "mcp": {\n    "pce-mcp": {\n      "type": "local",\n      "command": ["sh", "scripts/wrapper-del-operador.sh"]\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    pce = data["mcp"]["pce-mcp"]
    assert pce.get("_sdd_kit") is not True
    assert pce["command"] == ["sh", "scripts/wrapper-del-operador.sh"]
    assert "env" not in pce or pce["env"] != json.loads(KIT_ROOT.joinpath(".mcp.json").read_text())["mcpServers"]["pce-mcp"]["env"]


# ----------------------- CA-09: reemplazo atómico con marcador --------------------


def test_u0007_ca09_atomic_replacement_with_marker_overwrites_env(tmp_path: Path) -> None:
    target = tmp_path / "mcp-atomic-replace"
    _git_init(target)
    mcp = target / ".mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "pce-mcp": {
                        "_sdd_kit": True,
                        "type": "stdio",
                        "command": "sh",
                        "args": ["scripts/mcp-pce-old.sh"],
                        "env": {"OLD_VAR": "old-value"},
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    data = json.loads(mcp.read_text(encoding="utf-8"))
    pce = data["mcpServers"]["pce-mcp"]
    assert pce["_sdd_kit"] is True
    assert pce["args"] == ["scripts/mcp-pce.sh"]
    kit_env = json.loads(KIT_ROOT.joinpath(".mcp.json").read_text())["mcpServers"]["pce-mcp"]["env"]
    assert pce["env"] == kit_env
    assert "OLD_VAR" not in pce["env"]


def test_u0007_ca09_atomic_replacement_opencode_with_marker_overwrites_env(tmp_path: Path) -> None:
    target = tmp_path / "opencode-atomic-replace"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        '{\n  "mcp": {\n    "pce-mcp": {\n      "_sdd_kit": true,\n      "type": "local",\n      "command": ["sh", "scripts/mcp-pce-old.sh"],\n      "env": {"OLD_VAR": "old-value"}\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    pce = data["mcp"]["pce-mcp"]
    assert pce["_sdd_kit"] is True
    assert pce["command"] == ["sh", "scripts/mcp-pce.sh"]
    kit_env = json.loads(KIT_ROOT.joinpath("opencode.jsonc").read_text())["mcp"]["pce-mcp"]["env"]
    assert pce["env"] == kit_env
    assert "OLD_VAR" not in pce["env"]


# ----------------------- CA-11: otros servers sin marcador sobreviven -------------


def test_u0007_ca11_unmarked_others_survive_mcp_json(tmp_path: Path) -> None:
    target = tmp_path / "mcp-others"
    _git_init(target)
    mcp = target / ".mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "gitnexus": {
                        "type": "stdio",
                        "command": "npx",
                        "args": ["-y", "gitnexus@latest", "mcp"],
                    },
                    "pce-mcp": {
                        "type": "stdio",
                        "command": "sh",
                        "args": ["scripts/wrapper-del-operador.sh"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    data = json.loads(mcp.read_text(encoding="utf-8"))
    servers = data["mcpServers"]
    assert "gitnexus" in servers
    assert servers["gitnexus"]["command"] == "npx"
    assert servers["gitnexus"].get("_sdd_kit") is not True
    assert servers["pce-mcp"].get("_sdd_kit") is not True
    assert servers["pce-mcp"]["args"] == ["scripts/wrapper-del-operador.sh"]


def test_u0007_ca11_unmarked_others_survive_opencode_jsonc(tmp_path: Path) -> None:
    target = tmp_path / "opencode-others"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        '{\n  "mcp": {\n    "another-tool": { "type": "local", "command": ["sh"] },\n    "pce-mcp": { "type": "local", "command": ["sh", "scripts/wrapper-del-operador.sh"] }\n  }\n}\n',
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    mcp = data["mcp"]
    assert "another-tool" in mcp
    assert mcp["another-tool"].get("_sdd_kit") is not True
    assert mcp["pce-mcp"].get("_sdd_kit") is not True
    assert mcp["pce-mcp"]["command"] == ["sh", "scripts/wrapper-del-operador.sh"]