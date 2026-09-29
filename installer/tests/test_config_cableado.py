"""Unit 0002 — CA-35, CA-45..CA-47, CA-51, CA-52: cableado con marcador
del contrato de configuración del destino."""

from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
KIT_ROOT = REPO_ROOT
CLI = REPO_ROOT / "installer" / "cli.py"

sys.path.insert(0, str(REPO_ROOT / "installer"))

import config_cableado  # noqa: E402

DISCRIMINATOR = "_sdd_kit"


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _run_cli(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        cwd=cwd,
    )


def _copy_kit_to_target(target: Path) -> None:
    """Copia el kit entero al destino (incluyendo el manifest) para que
    el instalador tenga fuentes reales que escribir."""
    shutil.copytree(
        KIT_ROOT,
        target,
        symlinks=True,
        ignore=shutil.ignore_patterns(".git", "node_modules", ".angular", "__pycache__"),
    )


# ----------------------- mezclar_claude_settings ---------------------------------


def test_u0002_ca45_settings_merge_preserves_ajeno_and_kit_entries(tmp_path: Path) -> None:
    target = tmp_path / "settings-merge"
    _git_init(target)
    settings_path = target / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    settings_path.write_text(
        json.dumps(
            {
                "permissions": {"allow": ["Read"]},
                "env": {"FOO": "bar"},
                "hooks": {
                    "PreToolUse": [
                        {
                            "matcher": "Read|Edit",
                            "hooks": [{"type": "command", "command": "echo ajeno"}],
                        }
                    ]
                },
            }
        ),
        encoding="utf-8",
    )
    kit_settings_path = KIT_ROOT / ".claude" / "settings.json"
    if not kit_settings_path.exists():
        kit_settings_path.parent.mkdir(parents=True, exist_ok=True)
        kit_settings_path.write_text(json.dumps({"hooks": {"PreToolUse": []}}), encoding="utf-8")
    try:
        config_cableado.mezclar_claude_settings(target, KIT_ROOT)
    finally:
        if not (KIT_ROOT / ".claude" / "settings.json").exists():
            kit_settings_path.unlink()

    data = json.loads(settings_path.read_text(encoding="utf-8"))
    assert data["permissions"] == {"allow": ["Read"]}
    assert data["env"] == {"FOO": "bar"}
    pre_tool = data["hooks"]["PreToolUse"]
    ajenas = [h for h in pre_tool if not h.get(DISCRIMINATOR)]
    assert len(ajenas) == 1
    assert ajenas[0]["hooks"][0]["command"] == "echo ajeno"


def test_u0002_ca46_settings_byte_identical_across_two_installs(tmp_path: Path) -> None:
    target = tmp_path / "settings-idempotent"
    _git_init(target)
    kit_path = KIT_ROOT / ".claude" / "settings.json"
    if not kit_path.exists():
        kit_path.parent.mkdir(parents=True, exist_ok=True)
        kit_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "echo kit"}]}]}}),
            encoding="utf-8",
        )
    try:
        config_cableado.mezclar_claude_settings(target, KIT_ROOT)
        first = (target / ".claude" / "settings.json").read_bytes()
        config_cableado.mezclar_claude_settings(target, KIT_ROOT)
        second = (target / ".claude" / "settings.json").read_bytes()
        assert first == second
    finally:
        if not (KIT_ROOT / ".claude" / "settings.json").exists():
            kit_path.unlink()


def test_u0002_ca47_pre_existing_pre_tool_use_hook_is_not_overwritten(tmp_path: Path) -> None:
    target = tmp_path / "settings-preserve"
    _git_init(target)
    settings_path = target / ".claude" / "settings.json"
    settings_path.parent.mkdir(parents=True, exist_ok=True)
    foreign = {
        "hooks": {
            "PreToolUse": [
                {
                    "matcher": "Write|Edit",
                    "hooks": [{"type": "command", "command": "echo preserve-me"}],
                }
            ]
        }
    }
    settings_path.write_text(json.dumps(foreign), encoding="utf-8")
    kit_path = KIT_ROOT / ".claude" / "settings.json"
    had = kit_path.exists()
    if not had:
        kit_path.parent.mkdir(parents=True, exist_ok=True)
        kit_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "echo kit"}]}]}}),
            encoding="utf-8",
        )
    try:
        config_cableado.mezclar_claude_settings(target, KIT_ROOT)
    finally:
        if not had:
            kit_path.unlink()
    data = json.loads(settings_path.read_text(encoding="utf-8"))
    ajenas = [h for h in data["hooks"]["PreToolUse"] if not h.get(DISCRIMINATOR)]
    assert any("preserve-me" in h["hooks"][0]["command"] for h in ajenas)


# ----------------------- mezclar_mcp_json ----------------------------------------


def test_u0002_ca51_mcp_json_fusion_idempotent_and_preserves_others(tmp_path: Path) -> None:
    target = tmp_path / "mcp-fusion"
    _git_init(target)
    mcp = target / ".mcp.json"
    mcp.write_text(
        json.dumps(
            {
                "mcpServers": {
                    "other-server": {"type": "stdio", "command": "sh"},
                    "pce-mcp": {
                        "_sdd_kit": True,
                        "type": "stdio",
                        "command": "sh",
                        "args": ["scripts/mcp-pce-old.sh"],
                    },
                }
            }
        ),
        encoding="utf-8",
    )
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    first = json.loads(mcp.read_text(encoding="utf-8"))
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    second = json.loads(mcp.read_text(encoding="utf-8"))
    assert first == second

    assert "other-server" in first["mcpServers"]
    assert first["mcpServers"]["other-server"].get("_sdd_kit") is not True
    pce = first["mcpServers"]["pce-mcp"]
    assert pce.get("_sdd_kit") is True
    assert pce["args"] == ["scripts/mcp-pce.sh"]


def test_u0002_ca51_mcp_json_is_created_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "mcp-create"
    _git_init(target)
    config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    mcp = target / ".mcp.json"
    assert mcp.is_file()
    data = json.loads(mcp.read_text(encoding="utf-8"))
    assert "mcpServers" in data


def test_u0002_ca51_mcp_json_aborts_on_invalid_json(tmp_path: Path) -> None:
    target = tmp_path / "mcp-invalid"
    _git_init(target)
    mcp = target / ".mcp.json"
    mcp.write_text("{invalid json", encoding="utf-8")
    with pytest.raises(config_cableado.CableadoError):
        config_cableado.mezclar_mcp_json(target, KIT_ROOT)
    assert mcp.read_text(encoding="utf-8") == "{invalid json"


# ----------------------- mezclar_opencode_jsonc ----------------------------------


def test_u0002_ca52_opencode_jsonc_fusion_idempotent_and_preserves_others(tmp_path: Path) -> None:
    target = tmp_path / "opencode-fusion"
    _git_init(target)
    jsonc = target / "opencode.jsonc"
    jsonc.write_text(
        '{\n  // existing\n  "mcp": {\n    "other-server": { "type": "local", "command": ["sh"] },\n    "pce-mcp": {\n      "_sdd_kit": true,\n      "type": "local",\n      "command": ["sh", "scripts/mcp-pce-old.sh"]\n    }\n  }\n}\n',
        encoding="utf-8",
    )
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    first = jsonc.read_text(encoding="utf-8")
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    second = jsonc.read_text(encoding="utf-8")
    assert first == second
    data = json.loads(first)
    assert "other-server" in data["mcp"]
    assert data["mcp"]["pce-mcp"].get("_sdd_kit") is True
    assert data["mcp"]["pce-mcp"]["command"] == ["sh", "scripts/mcp-pce.sh"]


def test_u0002_ca52_opencode_jsonc_is_created_if_missing(tmp_path: Path) -> None:
    target = tmp_path / "opencode-create"
    _git_init(target)
    config_cableado.mezclar_opencode_jsonc(target, KIT_ROOT)
    jsonc = target / "opencode.jsonc"
    assert jsonc.is_file()
    data = json.loads(jsonc.read_text(encoding="utf-8"))
    assert "mcp" in data


# ----------------------- CA-35 guard interface -----------------------------------


def test_u0002_ca35_guard_denies_kit_path_and_allows_foreign(tmp_path: Path) -> None:
    target = tmp_path / "guard-test"
    _git_init(target)
    guard = REPO_ROOT / ".spec" / "scripts" / "guard_generated_paths.py"

    payload = json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": ".claude/skills/sdd-foo/SKILL.md"}}
    )
    result = subprocess.run(
        [sys.executable, str(guard)],
        cwd=target,
        input=payload,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "deny" in result.stdout

    payload2 = json.dumps(
        {"tool_name": "Edit", "tool_input": {"file_path": "destino/propio/notas.md"}}
    )
    result2 = subprocess.run(
        [sys.executable, str(guard)],
        cwd=target,
        input=payload2,
        capture_output=True,
        text=True,
    )
    assert result2.returncode == 0, result2.stderr
    assert "deny" not in result2.stdout


# ----------------------- CA-36 cableado real --------------------------------------


def test_u0002_ca36_settings_cablea_guards_post_install(tmp_path: Path) -> None:
    target = tmp_path / "cableado-real"
    _git_init(target)
    kit_path = KIT_ROOT / ".claude" / "settings.json"
    had = kit_path.exists()
    if not had:
        kit_path.parent.mkdir(parents=True, exist_ok=True)
        kit_path.write_text(
            json.dumps({"hooks": {"PreToolUse": [{"matcher": "", "hooks": [{"type": "command", "command": "echo kit"}]}]}}),
            encoding="utf-8",
        )
    try:
        result = _run_cli(["--target", str(target), "--install"], cwd=REPO_ROOT)
        assert result.returncode == 0, result.stdout + result.stderr
        settings_path = target / ".claude" / "settings.json"
        assert settings_path.is_file()
        data = json.loads(settings_path.read_text(encoding="utf-8"))
        assert isinstance(data.get("hooks", {}).get("PreToolUse"), list)
    finally:
        if not had:
            kit_path.unlink()