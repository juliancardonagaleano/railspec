"""Unit 0007 — CA-06: cobertura doble del bloque `env` en `.mcp.json` y
`opencode.jsonc` del kit. Las 3 vars (SSL_CERT_FILE, NODE_EXTRA_CA_CERTS,
MCP_PCE_CACHE_TTL_SECS) deben ser idénticas en ambos configs."""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KIT_ROOT = REPO_ROOT

sys.path.insert(0, str(REPO_ROOT / "installer"))
import config_cableado  # noqa: E402


def _load_env_block(config_path: Path) -> dict:
    if config_path.suffix == ".jsonc":
        data = config_cableado._read_jsonc(config_path)
    else:
        data = config_cableado._read_json(config_path)
    assert isinstance(data, dict)
    if config_path.suffix == ".jsonc":
        return data["mcp"]["pce-mcp"]["env"]
    return data["mcpServers"]["pce-mcp"]["env"]


def test_u0007_env_block_in_mcp_json_is_complete() -> None:
    env = _load_env_block(KIT_ROOT / ".mcp.json")
    assert sorted(env) == ["MCP_PCE_CACHE_TTL_SECS", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"]
    assert env["MCP_PCE_CACHE_TTL_SECS"] == "86400"
    assert "${HOME}" in env["SSL_CERT_FILE"]
    assert env["SSL_CERT_FILE"].endswith("pce-valvula-0040.pem")
    assert "${HOME}" in env["NODE_EXTRA_CA_CERTS"]
    assert env["NODE_EXTRA_CA_CERTS"].endswith("pce-valvula-0040.pem")


def test_u0007_env_block_in_opencode_jsonc_is_complete() -> None:
    env = _load_env_block(KIT_ROOT / "opencode.jsonc")
    assert sorted(env) == ["MCP_PCE_CACHE_TTL_SECS", "NODE_EXTRA_CA_CERTS", "SSL_CERT_FILE"]
    assert env["MCP_PCE_CACHE_TTL_SECS"] == "86400"
    assert "${HOME}" in env["SSL_CERT_FILE"]
    assert env["SSL_CERT_FILE"].endswith("pce-valvula-0040.pem")
    assert "${HOME}" in env["NODE_EXTRA_CA_CERTS"]
    assert env["NODE_EXTRA_CA_CERTS"].endswith("pce-valvula-0040.pem")


def test_u0007_env_blocks_match_across_mcp_json_and_opencode_jsonc() -> None:
    mcp_env = _load_env_block(KIT_ROOT / ".mcp.json")
    opencode_env = _load_env_block(KIT_ROOT / "opencode.jsonc")
    assert mcp_env == opencode_env
    assert sorted(mcp_env) == sorted(opencode_env)