"""Guardia de las reglas de conducta: lo que deciden los hooks de los cuatro arneses."""

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


# --- Codex y GitHub Copilot CLI ---------------------------------------------------------------
#
# Las formas de entrada y salida salen de CLIs reales (codex-cli 0.160.0, Copilot CLI 1.0.91): ver
# railspec/docs/proxy-local.md § Reglas de conducta aplicadas con hooks.


def _parche(*ops: tuple[str, str]) -> str:
    cuerpo = "".join(
        f"*** {accion}: {ruta}\n" + ("+x\n" if accion == "Add File" else "@@\n-a\n+b\n")
        for accion, ruta in ops
    )
    return f"*** Begin Patch\n{cuerpo}*** End Patch\n"


def _codex(tool: str, cwd: Path, modo: str = "default", **tool_input) -> dict | None:
    entrada = {"tool_name": tool, "tool_input": tool_input, "cwd": str(cwd), "permission_mode": modo}
    return guardia.hook_codex(entrada)


def _copilot(tool: str, cwd: Path, args) -> dict | None:
    return guardia.hook_copilot({"toolName": tool, "toolArgs": args, "cwd": str(cwd)})


def _decision_plana(respuesta: dict | None) -> str:
    return "allow" if respuesta is None else respuesta["permissionDecision"]


def test_codex_revisa_apply_patch_de_todas_las_rutas(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    dentro = _parche(("Update File", str(worktree / "src" / "calc.py")))
    assert _codex("apply_patch", worktree, command=dentro) is None
    # Una ruta relativa se resuelve contra el cwd de Codex.
    assert _codex("apply_patch", worktree, command=_parche(("Update File", "src/calc.py"))) is None
    # Basta una ruta fuera de la orden, o en el clon principal, para rechazar el parche entero.
    for ruta in (worktree / "README.md", raiz / "src" / "calc.py"):
        mezclado = _parche(("Update File", str(worktree / "src" / "calc.py")), ("Add File", str(ruta)))
        respuesta = _codex("apply_patch", worktree, command=mezclado)
        assert respuesta["hookSpecificOutput"]["permissionDecision"] == "deny"
        assert respuesta["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
        assert respuesta["hookSpecificOutput"]["permissionDecisionReason"].startswith("Railspec:")
    # Lo demás (Bash, tools MCP del bucle) lo decide Codex.
    assert _codex("Bash", raiz, command="echo hola > x.txt") is None
    assert _codex("mcp__railspec__unit_advance", raiz) is None


def test_codex_solo_rechaza_nunca_pregunta(tmp_path):
    """El hook de Codex trata `ask` como error y deja correr la tool: jamás se emite."""

    raiz, _ = _unidad(tmp_path, orden_implementar)
    for tool in guardia.TOOLS_HUMANAS:
        nombre = f"mcp__railspec__{tool}"
        # Con aprobaciones, pregunta `approval_mode = "prompt"` de .codex/config.toml: el hook no opina.
        assert _codex(nombre, raiz, "default") is None
        # Sin aprobaciones (approval_policy = never / --dangerously-bypass-approvals-and-sandbox) nadie
        # puede confirmar: se rechaza, con el motivo.
        respuesta = _codex(nombre, raiz, "bypassPermissions")
        decision = respuesta["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert tool in decision["permissionDecisionReason"]
        assert "aprobaciones" in decision["permissionDecisionReason"]
        assert guardia.ENV_GUARDIA in decision["permissionDecisionReason"]
    assert _codex("apply_patch", raiz, "default", command=_parche(("Add File", "n.py"))) is not None


def test_codex_salida_de_emergencia_abre_tambien_las_tools_humanas(tmp_path, monkeypatch):
    raiz, _ = _unidad(tmp_path, orden_implementar)
    monkeypatch.setenv(guardia.ENV_GUARDIA, "0")
    assert _codex("mcp__railspec__unit_approve", raiz, "bypassPermissions") is None
    assert _codex("apply_patch", raiz, command=_parche(("Add File", "n.py"))) is None


def test_copilot_revisa_create_edit_y_apply_patch(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    calc = str(worktree / "src" / "calc.py")
    assert _copilot("edit", worktree, {"path": calc, "old_str": "a", "new_str": "b"}) is None
    assert (
        _copilot("create", worktree, {"path": str(worktree / "src" / "nuevo.py"), "file_text": "x"}) is None
    )
    for tool, args in (
        ("create", {"path": str(raiz / "n.py"), "file_text": "x"}),
        ("edit", {"path": str(worktree / "README.md"), "old_str": "a", "new_str": "b"}),
        ("edit", {"path": str(worktree / almacen.ARCHIVO_ESTADO), "old_str": "a", "new_str": "b"}),
    ):
        respuesta = _copilot(tool, worktree, args)
        assert set(respuesta) == {"permissionDecision", "permissionDecisionReason"}
        assert respuesta["permissionDecision"] == "deny" and respuesta["permissionDecisionReason"]
    # `apply_patch` (modelos GPT-5 de Copilot) trae el parche como texto plano en `toolArgs`.
    assert _copilot("apply_patch", worktree, _parche(("Update File", calc))) is None
    assert (
        _decision_plana(_copilot("apply_patch", worktree, _parche(("Add File", str(raiz / "p.py")))))
        == "deny"
    )
    # Lo demás lo decide Copilot.
    assert _copilot("bash", raiz, {"command": "echo hola > x.txt"}) is None
    assert _copilot("view", raiz, {"path": str(raiz / "README.md")}) is None


def test_copilot_acepta_toolargs_como_texto_json(tmp_path):
    raiz, worktree = _unidad(tmp_path, orden_implementar)
    args = json.dumps({"path": str(raiz / "n.py"), "file_text": "x"})
    assert _decision_plana(_copilot("create", worktree, args)) == "deny"
    assert _copilot("create", worktree, json.dumps({"path": str(worktree / "src" / "n.py")})) is None
    # Lo que no se puede leer se rechaza, no se deja pasar.
    assert _decision_plana(_copilot("edit", worktree, "no es json")) == "deny"
    assert _decision_plana(_copilot("create", worktree, None)) == "deny"


def test_copilot_las_tools_humanas_preguntan_siempre(tmp_path):
    for tool in guardia.TOOLS_HUMANAS:
        respuesta = _copilot(f"railspec-{tool}", tmp_path, {})
        assert respuesta["permissionDecision"] == "ask" and tool in respuesta["permissionDecisionReason"]
    assert _copilot("railspec-unit_advance", tmp_path, {}) is None


def test_cli_hook_de_codex_y_copilot_fallan_cerrados_en_su_formato(monkeypatch, capsys):
    monkeypatch.setattr("sys.stdin", io.StringIO("no es json"))
    assert cli.main(["hook", "codex"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert salida["hookSpecificOutput"]["permissionDecisionReason"]  # Codex exige un motivo con `deny`
    monkeypatch.setattr("sys.stdin", io.StringIO("no es json"))
    assert cli.main(["hook", "copilot"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["permissionDecision"] == "deny" and "hookSpecificOutput" not in salida


def test_cli_hook_de_codex_y_copilot_sin_opinion_no_imprimen(tmp_path, monkeypatch, capsys):
    for arnes, entrada in (
        ("codex", {"tool_name": "Bash", "tool_input": {"command": "ls"}, "cwd": str(tmp_path)}),
        ("copilot", {"toolName": "view", "toolArgs": {"path": "x"}, "cwd": str(tmp_path)}),
    ):
        monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entrada)))
        assert cli.main(["hook", arnes]) == 0
        assert capsys.readouterr().out == ""


# --- instalación de los hooks de Codex y Copilot --------------------------------------------


def test_codex_instala_y_quita_el_hook_sin_tocar_los_ajenos(tmp_path):
    ajeno = {"matcher": "Bash", "hooks": [{"type": "command", "command": "mi-guardia"}]}
    viejo = {"hooks": [{"type": "command", "command": "railspec hook codex --viejo"}]}
    (tmp_path / ".codex").mkdir()
    (tmp_path / ".codex" / "hooks.json").write_text(
        json.dumps({"hooks": {"PreToolUse": [ajeno, viejo], "Stop": [{"hooks": []}]}}), encoding="utf-8"
    )

    assert ".codex/hooks.json" in adaptadores.instalar(tmp_path, Arnes.codex)
    datos = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert datos["hooks"]["PreToolUse"] == [ajeno, adaptadores.HOOK_CODEX]
    assert datos["hooks"]["Stop"] == [{"hooks": []}]
    assert adaptadores.instalar(tmp_path, Arnes.codex) == []
    assert adaptadores.verificar(tmp_path, Arnes.codex) == []

    adaptadores.desinstalar(tmp_path, Arnes.codex)
    restante = json.loads((tmp_path / ".codex" / "hooks.json").read_text())
    assert restante == {"hooks": {"PreToolUse": [ajeno], "Stop": [{"hooks": []}]}}


def test_codex_hook_derivado_se_detecta_y_el_archivo_propio_se_borra(tmp_path):
    adaptadores.instalar(tmp_path, Arnes.codex)
    ruta = tmp_path / ".codex" / "hooks.json"
    datos = json.loads(ruta.read_text())
    datos["hooks"]["PreToolUse"][0]["matcher"] = "apply_patch"
    ruta.write_text(json.dumps(datos))
    assert ".codex/hooks.json" in adaptadores.verificar(tmp_path, Arnes.codex)
    assert ".codex/hooks.json" in adaptadores.instalar(tmp_path, Arnes.codex)
    adaptadores.desinstalar(tmp_path, Arnes.codex)
    assert not ruta.exists()


def test_matchers_cubren_las_tools_de_cada_arnes():
    import re

    codex = adaptadores.HOOK_CODEX["matcher"]
    assert re.fullmatch(codex, "apply_patch")
    assert all(re.fullmatch(codex, f"mcp__railspec__{t}") for t in guardia.TOOLS_HUMANAS)
    assert not any(re.fullmatch(codex, t) for t in ("Bash", "mcp__railspec__unit_advance", "view_image"))
    assert adaptadores.HOOK_CODEX["hooks"][0]["command"] == "railspec hook codex"

    entrada = adaptadores.HOOK_COPILOT["hooks"]["preToolUse"][0]
    copilot = entrada["matcher"]
    assert all(re.fullmatch(copilot, t) for t in ("create", "edit", "apply_patch"))
    assert all(re.fullmatch(copilot, f"railspec-{t}") for t in guardia.TOOLS_HUMANAS)
    assert not any(re.fullmatch(copilot, t) for t in ("bash", "view", "grep", "railspec-unit_advance"))
    # Windows y el resto de plataformas llaman al mismo comando.
    assert entrada["bash"] == entrada["powershell"] == "railspec hook copilot"


def test_copilot_instala_un_archivo_propio_en_hooks_y_deja_los_ajenos(tmp_path):
    ajeno = tmp_path / ".github" / "hooks" / "formato.json"
    ajeno.parent.mkdir(parents=True)
    ajeno.write_text('{"version": 1, "hooks": {"postToolUse": []}}', encoding="utf-8")

    assert ".github/hooks/railspec.json" in adaptadores.instalar(tmp_path, Arnes.copilot)
    propio = json.loads((tmp_path / ".github" / "hooks" / "railspec.json").read_text())
    assert propio == adaptadores.HOOK_COPILOT and propio["version"] == 1
    assert adaptadores.instalar(tmp_path, Arnes.copilot) == []
    assert adaptadores.verificar(tmp_path, Arnes.copilot) == []

    (tmp_path / ".github" / "hooks" / "railspec.json").write_text("{}", encoding="utf-8")
    assert ".github/hooks/railspec.json" in adaptadores.verificar(tmp_path, Arnes.copilot)
    adaptadores.instalar(tmp_path, Arnes.copilot)

    adaptadores.desinstalar(tmp_path, Arnes.copilot)
    assert not (tmp_path / ".github" / "hooks" / "railspec.json").exists()
    assert ajeno.is_file()
