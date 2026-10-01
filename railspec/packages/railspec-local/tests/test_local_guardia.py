"""Guardia de las reglas de conducta: lo que deciden los hooks de Claude Code y OpenCode."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar, orden_redactar
from railspec.contracts.comun import Arnes, Fase
from railspec.local import adaptadores, almacen, cli, guardia
from railspec.local.almacen import Almacen

SPEC = ".railspec/unidades/0001-sumar/spec.md"


def _redactar_spec(estado, secuencia, orden_id):
    return orden_redactar(estado, secuencia, orden_id).model_copy(update={"ruta_artefacto": SPEC})


def _unidad(tmp_path: Path, orden=None):
    """Clon principal y worktree de una unidad en curso, con ``orden`` vigente si se da."""

    servidor = ServidorDoble([orden] if orden else [])
    proxy = crear_proxy(tmp_path, servidor)
    inicio = asyncio.run(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    if orden:
        asyncio.run(proxy.avanzar())
    return proxy.raiz, Path(inicio["worktree"])


def _claude(tool: str, cwd: Path, **tool_input) -> dict | None:
    return guardia.hook_claude_code({"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd)})


def _decision(respuesta: dict | None) -> str:
    return "allow" if respuesta is None else respuesta["hookSpecificOutput"]["permissionDecision"]


@pytest.fixture(autouse=True)
def _guardia_encendida(monkeypatch):
    monkeypatch.delenv(guardia.ENV_GUARDIA, raising=False)
    monkeypatch.delenv("RAILSPEC_WORKTREES", raising=False)


def test_constantes_alineadas_con_el_proxy():
    assert guardia.ARCHIVO_ESTADO == almacen.ARCHIVO_ESTADO
    assert adaptadores.TOOLS_HUMANAS == guardia.TOOLS_HUMANAS


def test_sin_unidades_no_opina(tmp_path):
    from local_fabricas import repo_git

    raiz = repo_git(tmp_path / "repo")
    assert _claude("Edit", raiz, file_path=str(raiz / "src" / "calc.py")) is None
    assert _claude("Write", tmp_path, file_path=str(tmp_path / "suelto.txt")) is None


def test_clon_principal_bloqueado_con_unidad_en_curso(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    respuesta = _claude("Edit", raiz, file_path=str(raiz / "src" / "calc.py"))
    assert _decision(respuesta) == "deny"
    motivo = respuesta["hookSpecificOutput"]["permissionDecisionReason"]
    assert str(worktree) in motivo and guardia.ENV_GUARDIA in motivo
    # Una ruta relativa se resuelve contra el cwd del arnés.
    assert _decision(_claude("Write", raiz, file_path="nuevo.py")) == "deny"
    # Fuera del repositorio decide el arnés.
    assert _claude("Write", raiz, file_path=str(tmp_path / "fuera.txt")) is None


def test_implementar_respeta_el_alcance(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    assert _claude("Edit", worktree, file_path=str(worktree / "src" / "calc.py")) is None
    assert _claude("Write", worktree, file_path=str(worktree / "src" / "nuevo" / "mod.py")) is None
    assert _decision(_claude("Edit", worktree, file_path=str(worktree / "README.md"))) == "deny"
    assert (
        _decision(_claude("Write", worktree, file_path=str(worktree / "src" / "generado" / "x.py"))) == "deny"
    )
    # El estado del proxy nunca, y los artefactos solo con su orden.
    assert _decision(_claude("Write", worktree, file_path=str(worktree / almacen.ARCHIVO_ESTADO))) == "deny"
    respuesta = _claude("Edit", worktree, file_path=str(worktree / SPEC))
    assert _decision(respuesta) == "deny"
    assert "implementar el grupo G1" in respuesta["hookSpecificOutput"]["permissionDecisionReason"]


def test_artefacto_solo_con_la_orden_que_lo_pide(tmp_path):
    raiz, worktree = _unidad(tmp_path, _redactar_spec)
    assert _claude("Write", worktree, file_path=str(worktree / SPEC)) is None
    plan = worktree / ".railspec/unidades/0001-sumar/plan.md"
    assert _decision(_claude("Write", worktree, file_path=str(plan))) == "deny"
    assert _decision(_claude("Edit", worktree, file_path=str(worktree / "src" / "calc.py"))) == "deny"


def test_sin_orden_vigente_no_se_escribe(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    respuesta = _claude("Edit", worktree, file_path=str(worktree / "src" / "calc.py"))
    assert "unit_advance" in respuesta["hookSpecificOutput"]["permissionDecisionReason"]


def test_unidad_cerrada_libera_el_clon_principal(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    alm = Almacen(worktree)
    estado = alm.leer()
    espejo = estado.espejo_remoto.model_copy(update={"fase": Fase.done})
    alm.escribir(estado.model_copy(update={"espejo_remoto": espejo, "orden_en_curso": None}))
    assert _claude("Edit", raiz, file_path=str(raiz / "src" / "calc.py")) is None
    assert _decision(_claude("Edit", worktree, file_path=str(worktree / "src" / "calc.py"))) == "deny"


def test_salida_de_emergencia(tmp_path, monkeypatch):
    raiz, _ = _unidad(tmp_path, orden_implementar)
    monkeypatch.setenv(guardia.ENV_GUARDIA, "0")
    assert _claude("Edit", raiz, file_path=str(raiz / "src" / "calc.py")) is None


def test_tools_humanas_siempre_preguntan(tmp_path):
    for tool in guardia.TOOLS_HUMANAS:
        assert _decision(_claude(f"mcp__railspec__{tool}", tmp_path)) == "ask"
        assert (
            guardia.hook_opencode({"tool": f"railspec_{tool}", "directory": str(tmp_path)})["decision"]
            == "ask"
        )
    assert _claude("mcp__railspec__unit_advance", tmp_path) is None


def test_opencode_revisa_edit_write_y_patch(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)

    def oc(tool, **args):
        return guardia.hook_opencode({"evento": "tool", "tool": tool, "args": args, "directory": str(raiz)})

    assert oc("edit", filePath=str(worktree / "src" / "calc.py"))["decision"] == "allow"
    assert oc("write", filePath="src/calc.py")["decision"] == "deny"  # relativa al proyecto: clon principal
    parche = (
        "*** Begin Patch\n"
        f"*** Update File: {worktree / 'src' / 'calc.py'}\n"
        "@@\n-a\n+b\n"
        f"*** Add File: {worktree / 'README.md'}\n"
        "+x\n*** End Patch\n"
    )
    assert oc("apply_patch", patchText=parche)["decision"] == "deny"
    assert oc("read", filePath=str(raiz / "README.md"))["decision"] == "allow"


def test_opencode_conoce_la_carpeta_de_worktrees(tmp_path, monkeypatch):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    assert guardia.hook_opencode({"evento": "config", "directory": str(raiz)}) == {
        "worktrees": str(worktree.parent)
    }
    monkeypatch.setenv("RAILSPEC_WORKTREES", str(tmp_path / "otra"))
    assert guardia.hook_opencode({"evento": "config", "directory": str(raiz)}) == {
        "worktrees": str(tmp_path / "otra")
    }
    assert guardia.hook_opencode({"evento": "config", "directory": str(tmp_path)}) == {"worktrees": None}


def test_cli_hook_falla_cerrada(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("no es json"))
    assert cli.main(["hook", "claude-code"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["hookSpecificOutput"]["permissionDecision"] == "deny"
    monkeypatch.setattr("sys.stdin", io.StringIO("no es json"))
    assert cli.main(["hook", "opencode"]) == 0
    assert json.loads(capsys.readouterr().out)["decision"] == "deny"


def test_cli_hook_sin_opinion_no_imprime(tmp_path, monkeypatch, capsys):
    entrada = {"tool_name": "Read", "tool_input": {}, "cwd": str(tmp_path)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entrada)))
    assert cli.main(["hook", "claude-code"]) == 0
    assert capsys.readouterr().out == ""


# --- instalación de los hooks -------------------------------------------------------------


def test_claude_code_instala_y_quita_el_hook_sin_tocar_los_ajenos(tmp_path):
    ajeno = {"matcher": "Bash", "hooks": [{"type": "command", "command": "mi-guardia"}]}
    viejo = {
        "matcher": "Edit",
        "hooks": [{"type": "command", "command": "railspec hook claude-code --viejo"}],
    }
    (tmp_path / ".claude").mkdir()
    (tmp_path / ".claude" / "settings.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [ajeno, viejo]}}), encoding="utf-8"
    )

    assert ".claude/settings.json" in adaptadores.instalar(tmp_path, Arnes.claude_code)
    hooks = json.loads((tmp_path / ".claude" / "settings.json").read_text())["hooks"]["PreToolUse"]
    # La versión anterior del hook de Railspec se reemplaza; la ajena se conserva.
    assert hooks == [ajeno, adaptadores.HOOK_CLAUDE_CODE]
    assert "Edit" in hooks[1]["matcher"] and "mcp__railspec__unit_approve" in hooks[1]["matcher"]
    assert adaptadores.instalar(tmp_path, Arnes.claude_code) == []
    assert adaptadores.verificar(tmp_path, Arnes.claude_code) == []

    adaptadores.desinstalar(tmp_path, Arnes.claude_code)
    restante = json.loads((tmp_path / ".claude" / "settings.json").read_text())
    assert restante == {"hooks": {"PreToolUse": [ajeno]}}


def test_claude_code_hook_derivado_se_detecta(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.claude_code)
    ruta = tmp_path / ".claude" / "settings.json"
    datos = json.loads(ruta.read_text())
    datos["hooks"]["PreToolUse"][0]["matcher"] = "Edit"
    ruta.write_text(json.dumps(datos))
    assert ".claude/settings.json" in adaptadores.verificar(tmp_path, Arnes.claude_code)


def test_opencode_instala_y_quita_el_plugin(tmp_path):
    cambios = adaptadores.instalar(tmp_path, Arnes.opencode)
    plugin = tmp_path / ".opencode" / "plugins" / "railspec.js"
    assert ".opencode/plugins/railspec.js" in cambios
    texto = plugin.read_text()
    assert '"railspec", "hook", "opencode"' in texto and "tool.execute.before" in texto
    assert "external_directory" in texto
    adaptadores.desinstalar(tmp_path, Arnes.opencode)
    assert not (tmp_path / ".opencode").exists()
