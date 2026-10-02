"""``railspec instalar --alcance usuario``: los adaptadores en la configuración del usuario.

Todas las pruebas usan un HOME temporal (``hogar``): nunca se toca el real.
"""

from __future__ import annotations

import json
import stat
from pathlib import Path

import pytest
from railspec.contracts.comun import Arnes
from railspec.local import cli, config
from railspec.local.adaptadores import INICIO_BLOQUE, usuario
from railspec.local.adaptadores import piezas as piezas_mod
from railspec.local.errores import ErrorRailspec


@pytest.fixture
def hogar(tmp_path, monkeypatch):
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.delenv(config.ENV_WORKTREES, raising=False)
    monkeypatch.setattr(cli.shutil, "which", lambda comando: f"/usr/local/bin/{comando}")
    assert Path.home() == home  # nunca el real
    return home


def ejecutar(capsys, *args: str) -> tuple[int, dict | None, str]:
    codigo = cli.main(list(args))
    captura = capsys.readouterr()
    return codigo, json.loads(captura.out) if captura.out.strip() else None, captura.err


def leer(ruta: Path):
    return json.loads(ruta.read_text(encoding="utf-8"))


# --- Claude Code -----------------------------------------------------------------------------------


def test_claude_code_instala_las_piezas_en_el_home(hogar, capsys):
    codigo, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert codigo == 0 and salida["alcance"] == "usuario"
    assert sorted(salida["cambios"]["claude-code"]) == sorted(
        str(hogar / r)
        for r in (
            ".claude/commands/railspec.md",
            ".claude/skills/railspec-bucle/SKILL.md",
            ".claude.json",
            ".claude/settings.json",
            ".claude/CLAUDE.md",
        )
    )
    # La misma entrada que escribe `claude mcp add --scope user railspec -- railspec mcp`.
    assert leer(hogar / ".claude.json") == {
        "mcpServers": {"railspec": {"type": "stdio", "command": "railspec", "args": ["mcp"], "env": {}}}
    }
    ajustes = leer(hogar / ".claude" / "settings.json")
    assert "mcp__railspec__unit_advance" in ajustes["permissions"]["allow"]
    assert ajustes["permissions"]["ask"] == [
        "mcp__railspec__unit_approve",
        "mcp__railspec__unit_set_mode",
        "mcp__railspec__unit_integrate",
    ]
    (hook,) = ajustes["hooks"]["PreToolUse"]
    assert hook["hooks"][0]["command"] == "railspec hook claude-code"
    reglas = (hogar / ".claude" / "CLAUDE.md").read_text(encoding="utf-8")
    assert reglas.count(INICIO_BLOQUE) == 1
    assert "los que tienen `.railspec/config.json`" in reglas
    assert "Este repositorio trabaja con Railspec" not in reglas  # vale para cualquier repositorio
    # Nada que sea de un repositorio: ni `settings.local.json` ni `.mcp.json`.
    assert not (hogar / ".claude" / "settings.local.json").exists() and not (hogar / ".mcp.json").exists()


def test_es_idempotente_y_verificar_informa_deriva(hogar, capsys):
    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    codigo, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")
    assert codigo == 0 and salida["cambios"] == {"claude-code": []}

    codigo, salida, _ = ejecutar(
        capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code", "--verificar"
    )
    assert codigo == 0 and salida["deriva"] == {"claude-code": []}

    (hogar / ".claude" / "commands" / "railspec.md").write_text("a mano\n", encoding="utf-8")
    codigo, salida, _ = ejecutar(
        capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code", "--verificar"
    )
    assert codigo == 1 and salida["deriva"] == {"claude-code": [str(hogar / ".claude/commands/railspec.md")]}


def test_conserva_lo_ajeno_en_los_archivos_del_usuario(hogar, capsys):
    (hogar / ".claude.json").write_text(
        json.dumps(
            {
                "userID": "abc",
                "projects": {"/x": {"allowedTools": []}},
                "mcpServers": {"otro": {"type": "stdio", "command": "x", "args": [], "env": {}}},
            }
        ),
        encoding="utf-8",
    )
    (hogar / ".claude").mkdir()
    (hogar / ".claude" / "settings.json").write_text(
        json.dumps(
            {
                "theme": "dark",
                "permissions": {"allow": ["Bash(ls:*)"]},
                "hooks": {
                    "PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mio"}]}]
                },
            }
        ),
        encoding="utf-8",
    )
    (hogar / ".claude" / "CLAUDE.md").write_text("# Mis notas\n\nSiempre en español.\n", encoding="utf-8")

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    claude = leer(hogar / ".claude.json")
    assert claude["userID"] == "abc" and claude["projects"] == {"/x": {"allowedTools": []}}
    assert set(claude["mcpServers"]) == {"otro", "railspec"}
    ajustes = leer(hogar / ".claude" / "settings.json")
    assert ajustes["theme"] == "dark" and ajustes["permissions"]["allow"][0] == "Bash(ls:*)"
    assert [e["hooks"][0]["command"] for e in ajustes["hooks"]["PreToolUse"]] == [
        "mio",
        "railspec hook claude-code",
    ]
    assert (
        (hogar / ".claude" / "CLAUDE.md")
        .read_text(encoding="utf-8")
        .startswith("# Mis notas\n\nSiempre en español.\n")
    )

    # Desinstalar deja exactamente lo que había.
    codigo, salida, _ = ejecutar(capsys, "desinstalar", "--alcance", "usuario")
    assert codigo == 0 and "claude-code" in salida["cambios"]
    assert leer(hogar / ".claude.json") == {
        "userID": "abc",
        "projects": {"/x": {"allowedTools": []}},
        "mcpServers": {"otro": {"type": "stdio", "command": "x", "args": [], "env": {}}},
    }
    ajustes = leer(hogar / ".claude" / "settings.json")
    assert ajustes == {
        "theme": "dark",
        "permissions": {"allow": ["Bash(ls:*)"]},
        "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "mio"}]}]},
    }
    assert (hogar / ".claude" / "CLAUDE.md").read_text(
        encoding="utf-8"
    ) == "# Mis notas\n\nSiempre en español.\n"
    assert not (hogar / ".claude" / "commands").exists() and not (hogar / ".claude" / "skills").exists()


def test_desinstalar_sin_nada_instalado_no_hace_nada(hogar, capsys):
    codigo, salida, _ = ejecutar(capsys, "desinstalar", "--alcance", "usuario")

    assert codigo == 0 and salida == {"alcance": "usuario", "cambios": {}}
    assert list(hogar.iterdir()) == []


def test_desinstalar_un_solo_arnes(hogar, capsys):
    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code", "--arnes", "opencode")

    codigo, salida, _ = ejecutar(capsys, "desinstalar", "--alcance", "usuario", "--arnes", "opencode")

    assert codigo == 0 and list(salida["cambios"]) == ["opencode"]
    assert usuario.instalados() == [Arnes.claude_code]


def test_carpeta_de_worktrees_se_abre_si_esta_fijada(hogar, capsys, monkeypatch):
    monkeypatch.setenv(config.ENV_WORKTREES, "/srv/worktrees")

    codigo, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert codigo == 0 and "avisos" not in salida
    assert leer(hogar / ".claude" / "settings.json")["permissions"]["additionalDirectories"] == [
        "/srv/worktrees"
    ]
    # Y se quita al desinstalar.
    ejecutar(capsys, "desinstalar", "--alcance", "usuario", "--arnes", "claude-code")
    assert not (hogar / ".claude").exists()


def test_sin_carpeta_de_worktrees_avisa_de_que_claude_code_preguntara(hogar, capsys):
    _, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert any("pedirá permiso" in a and config.ENV_WORKTREES in a for a in salida["avisos"])
    assert "additionalDirectories" not in leer(hogar / ".claude" / "settings.json")["permissions"]


def test_avisa_si_railspec_no_esta_en_el_path(hogar, capsys, monkeypatch):
    monkeypatch.setattr(cli.shutil, "which", lambda comando: None)

    _, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    assert any("no está en el PATH" in a for a in salida["avisos"])


# --- OpenCode --------------------------------------------------------------------------------------


def test_opencode_instala_en_la_carpeta_global(hogar, capsys):
    codigo, salida, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    base = hogar / ".config" / "opencode"
    assert codigo == 0
    assert sorted(salida["cambios"]["opencode"]) == sorted(
        str(base / r)
        for r in (
            "commands/railspec.md",
            "skills/railspec-bucle/SKILL.md",
            "opencode.json",
            "plugins/railspec.js",
            "AGENTS.md",
        )
    )
    configuracion = leer(base / "opencode.json")
    assert configuracion["mcp"]["railspec"] == {
        "type": "local",
        "command": ["railspec", "mcp"],
        "enabled": True,
    }
    assert configuracion["permission"]["railspec_unit_approve"] == "ask"
    assert "los que tienen `.railspec/config.json`" in (base / "AGENTS.md").read_text(encoding="utf-8")
    # Sin carpetas de versiones antiguas del adaptador (singular) ni nada de Claude Code.
    assert not (base / "command").exists() and not (hogar / ".claude").exists()


def test_opencode_respeta_xdg_config_home_absoluta(hogar, capsys, monkeypatch, tmp_path):
    xdg = tmp_path / "xdg"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    assert (xdg / "opencode" / "opencode.json").is_file() and not (hogar / ".config").exists()


def test_opencode_ignora_una_xdg_config_home_relativa(hogar, capsys, monkeypatch):
    monkeypatch.setenv("XDG_CONFIG_HOME", "relativa")

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    assert (hogar / ".config" / "opencode" / "opencode.json").is_file()


def test_opencode_usa_el_opencode_jsonc_que_ya_existe(hogar, capsys):
    base = hogar / ".config" / "opencode"
    base.mkdir(parents=True)
    (base / "opencode.jsonc").write_text(
        '{\n  // mis ajustes\n  "theme": "tokyonight",\n}\n', encoding="utf-8"
    )

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    assert not (base / "opencode.json").exists()
    datos = leer(base / "opencode.jsonc")
    assert datos["theme"] == "tokyonight" and "railspec" in datos["mcp"]


def test_opencode_desinstalar_deja_la_carpeta_global_como_estaba(hogar, capsys):
    base = hogar / ".config" / "opencode"
    base.mkdir(parents=True)
    (base / "opencode.json").write_text('{"theme": "dark"}', encoding="utf-8")
    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "opencode")

    ejecutar(capsys, "desinstalar", "--alcance", "usuario", "--arnes", "opencode")

    assert sorted(p.name for p in base.iterdir()) == ["opencode.json"]
    # Lo que ya había se queda; `$schema` lo puso `instalar` y, como en el repositorio, no se retira
    # mientras el archivo tenga otras claves.
    assert leer(base / "opencode.json") == {"$schema": "https://opencode.ai/config.json", "theme": "dark"}


# --- lo que no se admite ---------------------------------------------------------------------------


@pytest.mark.parametrize("arnes", ["codex", "copilot"])
def test_codex_y_copilot_responden_no_soportado(hogar, capsys, arnes):
    codigo, salida, error = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", arnes)

    assert codigo == 1 and salida is None
    assert error.startswith("railspec: ") and f"{arnes} no admite todavía el alcance de usuario" in error
    assert "claude-code, opencode" in error and f"railspec instalar --arnes {arnes}" in error
    assert list(hogar.iterdir()) == []


def test_un_arnes_no_soportado_cancela_todo_antes_de_escribir(hogar, capsys):
    codigo, _, error = ejecutar(
        capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code", "--arnes", "codex"
    )

    assert codigo == 1 and "codex no admite" in error
    assert list(hogar.iterdir()) == []


def test_desinstalar_tambien_rechaza_codex(hogar, capsys):
    codigo, _, error = ejecutar(capsys, "desinstalar", "--alcance", "usuario", "--arnes", "codex")

    assert codigo == 1 and "codex no admite" in error


def test_sin_arnes_pide_uno(hogar, capsys):
    codigo, _, error = ejecutar(capsys, "instalar", "--alcance", "usuario")

    assert codigo == 1 and "--arnes claude-code, --arnes opencode" in error
    assert list(hogar.iterdir()) == []


def test_lo_del_repositorio_no_se_mezcla_con_el_usuario(hogar, capsys):
    codigo, _, error = ejecutar(
        capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code", "--org", "acme"
    )
    assert codigo == 1 and "--org son del repositorio" in error

    codigo, _, error = ejecutar(capsys, "desinstalar", "--alcance", "usuario", "--config")
    assert codigo == 1 and "--config" in error
    assert list(hogar.iterdir()) == []


def test_el_alcance_de_usuario_no_toca_el_repositorio_ni_exige_uno(hogar, capsys, tmp_path, monkeypatch):
    fuera = tmp_path / "no-es-un-repo"
    fuera.mkdir()
    monkeypatch.chdir(fuera)

    codigo, _, _ = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert codigo == 0 and list(fuera.iterdir()) == []


def test_por_defecto_sigue_siendo_el_repositorio(hogar, capsys, tmp_path):
    from local_fabricas import repo_git

    raiz = repo_git(tmp_path / "repo")

    codigo, salida, _ = ejecutar(
        capsys,
        "--repo",
        str(raiz),
        "instalar",
        "--org",
        "acme",
        "--workspace",
        "certificados",
        "--repositorio",
        "api",
        "--arnes",
        "claude-code",
    )

    assert codigo == 0 and "alcance" not in salida
    assert (raiz / ".mcp.json").is_file() and (raiz / ".claude" / "commands" / "railspec.md").is_file()
    assert list(hogar.iterdir()) == []


# --- escritura segura ------------------------------------------------------------------------------


def test_conserva_el_modo_de_claude_json_y_no_deja_temporales(hogar, capsys):
    (hogar / ".claude.json").write_text('{"userID": "abc"}', encoding="utf-8")
    (hogar / ".claude.json").chmod(0o600)

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert stat.S_IMODE((hogar / ".claude.json").stat().st_mode) == 0o600
    assert [p.name for p in hogar.iterdir() if p.name.endswith(".tmp")] == []


def test_un_enlace_simbolico_de_dotfiles_sigue_siendo_un_enlace(hogar, capsys, tmp_path):
    dotfiles = tmp_path / "dotfiles"
    dotfiles.mkdir()
    real = dotfiles / "settings.json"
    real.write_text('{"theme": "dark"}', encoding="utf-8")
    (hogar / ".claude").mkdir()
    (hogar / ".claude" / "settings.json").symlink_to(real)

    ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert (hogar / ".claude" / "settings.json").is_symlink()
    assert leer(real)["theme"] == "dark" and "hooks" in leer(real)


def test_si_el_reemplazo_falla_el_archivo_original_queda_intacto(hogar, capsys, monkeypatch):
    original = '{"userID": "abc"}'
    (hogar / ".claude.json").write_text(original, encoding="utf-8")

    def falla(origen, destino):
        raise OSError("disco lleno")

    monkeypatch.setattr(piezas_mod.os, "replace", falla)
    with pytest.raises(OSError, match="disco lleno"):
        usuario.instalar(Arnes.claude_code, home=hogar)

    assert (hogar / ".claude.json").read_text(encoding="utf-8") == original
    assert [p.name for p in hogar.iterdir() if p.name.endswith(".tmp")] == []


def test_un_json_invalido_no_se_pisa(hogar, capsys):
    (hogar / ".claude.json").write_text("{ no es json", encoding="utf-8")

    codigo, _, error = ejecutar(capsys, "instalar", "--alcance", "usuario", "--arnes", "claude-code")

    assert codigo == 1 and "no es JSON válido; no se toca" in error
    assert (hogar / ".claude.json").read_text(encoding="utf-8") == "{ no es json"


# --- el servidor MCP en cualquier carpeta -------------------------------------------------------------------


def test_el_servidor_mcp_arranca_fuera_de_un_repositorio(hogar, tmp_path, monkeypatch):
    """Con alcance de usuario el arnés lo lanza en todas partes: el error llega con la tool."""

    fabricas = []
    monkeypatch.setattr("railspec.local.servidor_mcp.servir", fabricas.append)
    fuera = tmp_path / "no-es-un-repo"
    fuera.mkdir()
    monkeypatch.chdir(fuera)

    assert cli.main(["mcp"]) == 0

    (fabrica,) = fabricas
    with pytest.raises(ErrorRailspec, match="no está dentro de un repositorio git"):
        fabrica()


def test_en_un_repositorio_sin_railspec_el_error_dice_que_falta_instalar(hogar, tmp_path, monkeypatch):
    from local_fabricas import repo_git

    fabricas = []
    monkeypatch.setattr("railspec.local.servidor_mcp.servir", fabricas.append)
    monkeypatch.chdir(repo_git(tmp_path / "repo"))

    assert cli.main(["mcp"]) == 0

    with pytest.raises(ErrorRailspec, match=r"No existe \.railspec/config\.json"):
        fabricas[0]()
