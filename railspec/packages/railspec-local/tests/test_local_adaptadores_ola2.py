"""Adaptadores de la segunda ola (Codex y Copilot) y piezas compartidas entre arneses."""

from __future__ import annotations

import json
import subprocess
import tomllib

import pytest
from railspec.contracts.comun import Arnes
from railspec.local import adaptadores, cli
from railspec.local.adaptadores import codex
from railspec.local.errores import ErrorRailspec

CONFIG_AJENA = 'model = "gpt-5"\n\n[mcp_servers.otro]\ncommand = "otro"\n'


def frontmatter(texto: str) -> list[str]:
    assert texto.startswith("---\n")
    return texto[4:].split("\n---\n", 1)[0].splitlines()


def test_instalar_codex_preserva_lo_ajeno_y_es_idempotente(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text(CONFIG_AJENA, encoding="utf-8")

    cambios = adaptadores.instalar(tmp_path, Arnes.codex)

    assert sorted(cambios) == sorted(
        [
            ".agents/skills/railspec/SKILL.md",
            ".agents/skills/railspec-bucle/SKILL.md",
            ".codex/config.toml",
            "AGENTS.md",
        ]
    )
    texto = (tmp_path / ".codex" / "config.toml").read_text()
    assert texto.startswith(CONFIG_AJENA)
    config = tomllib.loads(texto)
    assert config["model"] == "gpt-5" and config["mcp_servers"]["otro"] == {"command": "otro"}
    railspec = config["mcp_servers"]["railspec"]
    assert (railspec["command"], railspec["args"]) == ("railspec", ["mcp"])
    assert railspec["default_tools_approval_mode"] == "prompt"
    modos = {t: v["approval_mode"] for t, v in railspec["tools"].items()}
    assert {t for t, m in modos.items() if m == "approve"} == set(adaptadores.TOOLS_AUTOMATICAS)
    assert {t for t, m in modos.items() if m == "prompt"} == set(adaptadores.TOOLS_HUMANAS)
    assert adaptadores.INICIO_BLOQUE in (tmp_path / "AGENTS.md").read_text()

    assert adaptadores.instalar(tmp_path, Arnes.codex) == []
    assert adaptadores.verificar(tmp_path, Arnes.codex) == []
    assert adaptadores.instalados(tmp_path) == [Arnes.codex]

    adaptadores.desinstalar(tmp_path, Arnes.codex)
    assert (tmp_path / ".codex" / "config.toml").read_text() == CONFIG_AJENA
    assert not (tmp_path / ".agents").exists() and not (tmp_path / "AGENTS.md").exists()


def test_codex_sin_config_previa_deja_el_repositorio_como_estaba(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.codex)
    adaptadores.desinstalar(tmp_path, Arnes.codex)
    assert list(tmp_path.iterdir()) == []


def test_codex_no_pisa_un_servidor_railspec_definido_a_mano(tmp_path):
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "config.toml").write_text('[mcp_servers.railspec]\ncommand = "x"\n')
    with pytest.raises(ErrorRailspec, match="fuera del bloque"):
        adaptadores.instalar(tmp_path, Arnes.codex)
    (tmp_path / ".codex" / "config.toml").write_text("roto = [\n")
    with pytest.raises(ErrorRailspec, match="TOML válido"):
        adaptadores.instalar(tmp_path, Arnes.codex)


def test_codex_detecta_deriva_y_la_corrige(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.codex)
    ruta = tmp_path / ".codex" / "config.toml"
    ruta.write_text(ruta.read_text().replace('"approve"', '"prompt"', 1))
    assert adaptadores.verificar(tmp_path, Arnes.codex) == [".codex/config.toml"]
    assert adaptadores.instalar(tmp_path, Arnes.codex) == [".codex/config.toml"]
    assert ruta.read_text().count(codex.INICIO_TOML) == 1
    assert adaptadores.verificar(tmp_path, Arnes.codex) == []


def test_skill_de_arranque_de_codex_sale_del_comando_canonico(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.codex)
    skill = (tmp_path / ".agents" / "skills" / "railspec" / "SKILL.md").read_text()
    cabecera = frontmatter(skill)
    assert cabecera[0] == "name: railspec" and cabecera[1].startswith("description: ")
    assert not any(linea.startswith("argument-hint") for linea in cabecera)
    assert "$ARGUMENTS" not in skill and "`$railspec`" in skill
    assert "llama `unit_start` con `titulo`" in skill  # mismo cuerpo que plantillas/comando.md


def test_instalar_copilot_comparte_mcp_json_y_aprueba_solo_el_bucle(tmp_path):
    cambios = adaptadores.instalar(tmp_path, Arnes.copilot)

    assert sorted(cambios) == sorted(
        [
            ".github/skills/railspec/SKILL.md",
            ".github/skills/railspec-bucle/SKILL.md",
            ".mcp.json",
            "AGENTS.md",
        ]
    )
    mcp = json.loads((tmp_path / ".mcp.json").read_text())
    assert mcp == {"mcpServers": {"railspec": {"type": "stdio", "command": "railspec", "args": ["mcp"]}}}
    for nombre in ("railspec", "railspec-bucle"):
        cabecera = frontmatter((tmp_path / ".github" / "skills" / nombre / "SKILL.md").read_text())
        assert cabecera[0] == f"name: {nombre}"
        permitidas = [linea.strip()[2:] for linea in cabecera if linea.startswith("  - ")]
        assert permitidas == [f"railspec({t})" for t in adaptadores.TOOLS_AUTOMATICAS]
        assert not any(t in linea for t in adaptadores.TOOLS_HUMANAS for linea in cabecera)
    arranque = frontmatter((tmp_path / ".github" / "skills" / "railspec" / "SKILL.md").read_text())
    assert any(linea.startswith("argument-hint:") for linea in arranque)
    assert adaptadores.instalar(tmp_path, Arnes.copilot) == []
    assert adaptadores.verificar(tmp_path, Arnes.copilot) == []


def test_piezas_compartidas_sobreviven_a_desinstalar_un_solo_arnes(tmp_path):
    for arnes in (Arnes.claude_code, Arnes.copilot, Arnes.opencode, Arnes.codex):
        adaptadores.instalar(tmp_path, arnes, tmp_path.parent / "wt")
    assert adaptadores.instalados(tmp_path) == list(adaptadores.ADAPTADORES)

    # .mcp.json lo comparten Claude Code y Copilot; AGENTS.md, OpenCode, Codex y Copilot.
    adaptadores.desinstalar(tmp_path, Arnes.copilot, tmp_path.parent / "wt")
    assert "railspec" in json.loads((tmp_path / ".mcp.json").read_text())["mcpServers"]
    assert adaptadores.INICIO_BLOQUE in (tmp_path / "AGENTS.md").read_text()
    assert not (tmp_path / ".github").exists()
    assert Arnes.copilot not in adaptadores.instalados(tmp_path)
    assert adaptadores.verificar(tmp_path, Arnes.claude_code, tmp_path.parent / "wt") == []

    adaptadores.desinstalar(tmp_path, Arnes.opencode, tmp_path.parent / "wt")
    assert adaptadores.INICIO_BLOQUE in (tmp_path / "AGENTS.md").read_text()
    adaptadores.desinstalar(tmp_path, Arnes.codex, tmp_path.parent / "wt")
    assert not (tmp_path / "AGENTS.md").exists()
    adaptadores.desinstalar(tmp_path, Arnes.claude_code, tmp_path.parent / "wt")
    assert not (tmp_path / ".mcp.json").exists()
    assert adaptadores.instalados(tmp_path) == []


def test_un_arnes_no_se_da_por_instalado_por_piezas_ajenas(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.claude_code)
    assert adaptadores.instalados(tmp_path) == [Arnes.claude_code]
    adaptadores.instalar(tmp_path, Arnes.opencode)
    assert adaptadores.instalados(tmp_path) == [Arnes.claude_code, Arnes.opencode]


def test_cli_desinstalar_todo_no_deja_piezas_compartidas(tmp_path, capsys):
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True)
    for arnes in ("claude-code", "copilot", "codex"):
        args = ["--repo", str(tmp_path), "instalar", "--org", "a", "--workspace", "w", "--repositorio", "r"]
        assert cli.main([*args, "--arnes", arnes]) == 0
        avisos = json.loads(capsys.readouterr().out)["avisos"]
    assert any("codex --add-dir" in a for a in avisos)

    assert cli.main(["--repo", str(tmp_path), "desinstalar", "--config"]) == 0
    restos = sorted(p.name for p in tmp_path.iterdir() if p.name != ".git")
    assert restos == []
