"""Guardia de Bash: los tres patrones del kit viejo (sed -i, redirección que trunca, código inline)."""

from __future__ import annotations

import asyncio
import io
import json
from pathlib import Path

import pytest
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar
from railspec.local import cli, guardia, ordenes_shell
from railspec.local.ordenes_shell import Hallazgo


def _unidad(tmp_path: Path):
    """Clon principal y worktree de una unidad en curso (alcance ``src/**``, sin ``src/generado/**``)."""

    proxy = crear_proxy(tmp_path, ServidorDoble([orden_implementar]))
    inicio = asyncio.run(proxy.iniciar("Corregir suma", "La suma resta en vez de sumar."))
    asyncio.run(proxy.avanzar())
    return proxy.raiz, Path(inicio["worktree"])


def _bash(orden: str, cwd: Path) -> dict | None:
    return guardia.hook_claude_code({"tool_name": "Bash", "tool_input": {"command": orden}, "cwd": str(cwd)})


def _decision(respuesta: dict | None) -> str:
    return "allow" if respuesta is None else respuesta["hookSpecificOutput"]["permissionDecision"]


def _motivo(respuesta: dict) -> str:
    return respuesta["hookSpecificOutput"]["permissionDecisionReason"]


@pytest.fixture(autouse=True)
def _guardia_encendida(monkeypatch):
    monkeypatch.delenv(guardia.ENV_GUARDIA, raising=False)
    monkeypatch.delenv("RAILSPEC_WORKTREES", raising=False)


def _reglas(orden: str, carpeta: Path) -> list[tuple[int, str]]:
    return [(h.regla, h.texto) for h in ordenes_shell.hallazgos(orden, carpeta)]


# --- el analizador: qué rutas toca cada patrón ---------------------------------------------------


def test_regla_1_sed_en_el_sitio(tmp_path):
    assert _reglas("sed -i 's/a/b/' src/x.py", tmp_path) == [(1, "src/x.py")]
    # El guion es el primer argumento que no es opción, salvo que -e o -f ya lo den.
    assert _reglas("sed -i -e 's/a/b/' -e 's/c/d/' x.py y.py", tmp_path) == [(1, "x.py"), (1, "y.py")]
    assert _reglas("sed -ne 's/a/b/p' -i.bak x.py", tmp_path) == [(1, "x.py")]
    assert _reglas("sed --in-place=.bak 's/a/b/' x.py", tmp_path) == [(1, "x.py")]
    assert _reglas("sed -i '' 's/a/b/' x.py", tmp_path) == [(1, "x.py")]
    assert _reglas("/usr/bin/sed -i s/a/b/ x.py", tmp_path) == [(1, "x.py")]
    # Sin -i el archivo no cambia; el guion y las redirecciones no son archivos.
    assert _reglas("sed 's/a/b/' x.py", tmp_path) == []
    # (`2>/dev/null` es la regla 2, que la guardia deja pasar por caer fuera del repositorio.)
    # Sin /dev/null como archivo (Windows) la redirección ni cuenta.
    esperado = [(2, "/dev/null"), (1, "x.py")] if Path("/dev/null").exists() else [(1, "x.py")]
    assert _reglas("sed -i s/a/b/ x.py 2>/dev/null", tmp_path) == esperado


def test_regla_2_solo_trunca_lo_que_existe(tmp_path):
    (tmp_path / "estado.json").write_text("{}", encoding="utf-8")
    assert _reglas("echo x > estado.json", tmp_path) == [(2, "estado.json")]
    assert _reglas("echo x>estado.json", tmp_path) == [(2, "estado.json")]
    assert _reglas("echo x 2> estado.json", tmp_path) == [(2, "estado.json")]
    assert _reglas("echo x &> estado.json", tmp_path) == [(2, "estado.json")]
    assert _reglas('echo "it\'s" > estado.json', tmp_path) == [(2, "estado.json")]
    # Anexar, un archivo que no existe y un descriptor no truncan nada.
    assert _reglas("echo x >> estado.json", tmp_path) == []
    assert _reglas("echo x > nuevo.json", tmp_path) == []
    assert _reglas("ls 2>&1", tmp_path) == []
    assert _reglas("echo x > estado.json.no-existe", tmp_path) == []


def test_regla_3_codigo_inline_a_un_interprete(tmp_path):
    assert _reglas("python3 -c \"open('src/a.py','w').write('x')\"", tmp_path) == [(3, "src/a.py")]
    assert _reglas('python3.12 -c \'open("src/a.py", "w")\'', tmp_path) == [(3, "src/a.py")]
    assert _reglas("bash -lc 'echo x > src/a.py'", tmp_path) == [(3, "src/a.py")]
    assert _reglas("node -e \"require('fs').writeFileSync('src/a.js','x')\"", tmp_path) == [(3, "src/a.js")]
    assert _reglas("perl -pi -e 's/a/b/' src/a.py", tmp_path) == [(3, "src/a.py")]
    assert _reglas("python3 - <<'EOF'\nopen('src/a.py','w')\nEOF", tmp_path) == [(3, "src/a.py")]
    assert _reglas("sh <<< 'echo x > src/a.py'", tmp_path) == [(3, "src/a.py")]
    (tmp_path / "calc.py").write_text("", encoding="utf-8")
    assert _reglas("python3 -c \"open('calc.py','w')\"", tmp_path) == [(3, "calc.py")]


def test_regla_3_lo_que_no_es_una_ruta_no_cuenta(tmp_path):
    for orden in (
        "python3 -c 'print(1/2, and/or, os.path.join)'",
        "python3 -c 'import sys; sys.stdout.write(\"hola\")'",
        "node -e 'console.log(1)'",
        "python3 script.py src/a.py",  # un script versionado no es código inline
        "awk '{print > \"src/a.py\"}' f",  # fuera de la lista cerrada: documentado, no un hueco
        "ruby -e 'File.write(\"src/a.rb\", 1)'",
    ):
        assert _reglas(orden, tmp_path) == [], orden


def test_el_cuerpo_de_un_heredoc_no_es_una_orden(tmp_path):
    (tmp_path / "f.txt").write_text("", encoding="utf-8")
    orden = "cat <<EOF > f.txt\nsed -i s/a/b/ src/a.py\nEOF\nls"
    # Solo cuenta la redirección del cat; el sed del cuerpo es texto.
    assert _reglas(orden, tmp_path) == [(2, "f.txt")]


def test_cd_literal_cambia_contra_que_se_resuelve(tmp_path):
    (tmp_path / "sub").mkdir()
    hallazgos = ordenes_shell.hallazgos("cd sub && sed -i s/a/b/ x.py; sed -i s/a/b/ y.py", tmp_path)
    assert hallazgos == [
        Hallazgo(1, "x.py", tmp_path / "sub"),
        Hallazgo(1, "y.py", tmp_path / "sub"),
    ]
    # No se sabe adónde va: se sigue con la carpeta conocida.
    assert ordenes_shell.hallazgos("cd $DIR && sed -i s/a/b/ x.py", tmp_path) == [
        Hallazgo(1, "x.py", tmp_path)
    ]


def test_cada_comando_compuesto_se_juzga_por_separado(tmp_path):
    for orden in (
        "git status && sed -i s/a/b/ x.py",
        "(sed -i s/a/b/ x.py)",
        "echo $(sed -i s/a/b/ x.py)",
        "echo `sed -i s/a/b/ x.py`",
        "ls | sed -i s/a/b/ x.py",
        "FOO=1 sudo -n env sed -i s/a/b/ x.py",
        "echo hola\nsed -i s/a/b/ x.py",
        "echo hola; \\\nsed -i s/a/b/ x.py",
        "if true; then sed -i s/a/b/ x.py; fi",
    ):
        assert _reglas(orden, tmp_path) == [(1, "x.py")], orden


def test_los_interpretes_de_la_lista_cerrada_se_reconocen():
    assert {ordenes_shell._nombre(i) for i in ordenes_shell.INTERPRETES} <= set(
        ordenes_shell._OPCIONES_INLINE
    )


# --- la guardia: se juzga como una escritura de Edit ----------------------------------------------


def test_sed_en_el_clon_principal_con_unidad_en_curso_se_rechaza(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    respuesta = _bash("sed -i 's/a - b/a + b/' src/calc.py", raiz)
    assert _decision(respuesta) == "deny"
    motivo = _motivo(respuesta)
    assert "regla 1" in motivo and "src/calc.py" in motivo
    assert str(worktree) in motivo and guardia.ENV_GUARDIA in motivo
    # El mismo comando dentro del alcance pasa: la guardia no prohíbe `sed`, prohíbe salirse del alcance.
    assert _bash("sed -i 's/a - b/a + b/' src/calc.py", worktree) is None
    assert _bash(f"sed -i 's/a - b/a + b/' {worktree.as_posix()}/src/calc.py", raiz) is None


def test_fuera_del_alcance_de_la_orden_se_rechaza(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    assert _decision(_bash("sed -i s/demo/otro/ README.md", worktree)) == "deny"
    assert _decision(_bash("sed -i s/a/b/ src/generado/x.py", worktree)) == "deny"
    respuesta = _bash("sed -i s/a/b/ .railspec/estado-local.json", worktree)
    assert _decision(respuesta) == "deny" and "estado de Railspec" in _motivo(respuesta)
    assert _bash("sed -i s/a - b/a + b/ src/calc.py README.md", worktree) is not None
    assert _bash("sed -i s/a/b/ src/calc.py", worktree) is None


def test_redireccion_que_trunca_un_archivo_existente(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    respuesta = _bash("echo roto > README.md", worktree)
    assert _decision(respuesta) == "deny" and "regla 2" in _motivo(respuesta)
    assert _decision(_bash("echo roto > .railspec/estado-local.json", worktree)) == "deny"
    assert _decision(_bash("echo roto > src/calc.py", raiz)) == "deny"
    # Dentro del alcance, anexando, creando un archivo o hacia fuera del repositorio, no.
    assert _bash("echo ok > src/calc.py", worktree) is None
    assert _bash("echo ok >> README.md", worktree) is None
    assert _bash("echo ok > notas-nuevas.txt", worktree) is None
    assert _bash("echo ok > /dev/null", worktree) is None
    assert _bash(f"echo ok > {tmp_path.as_posix()}/fuera.txt", raiz) is None


def test_codigo_inline_que_nombra_un_archivo_fuera_del_alcance(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    respuesta = _bash("python3 -c \"open('README.md','w').write('x')\"", worktree)
    assert _decision(respuesta) == "deny" and "regla 3" in _motivo(respuesta)
    assert _decision(_bash("python3 - <<'EOF'\nopen('src/generado/x.py','w')\nEOF", worktree)) == "deny"
    assert _decision(_bash("bash -c 'sed -i s/a/b/ src/calc.py'", raiz)) == "deny"
    assert _bash("python3 -c \"open('src/calc.py','w').write('x')\"", worktree) is None
    assert _bash("python3 -c 'print(sum(range(10)) / 2)'", raiz) is None


def test_cd_a_otra_carpeta_se_sigue(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    assert _decision(_bash(f"cd {raiz.as_posix()} && sed -i s/a/b/ src/calc.py", worktree)) == "deny"
    assert _bash(f"cd {worktree.as_posix()}/src && sed -i s/a/b/ calc.py", raiz) is None
    assert _decision(_bash(f"cd {worktree.as_posix()} && sed -i s/a/b/ README.md", raiz)) == "deny"


def test_sin_unidades_o_con_la_unidad_cerrada_no_opina(tmp_path):
    from local_fabricas import repo_git

    raiz = repo_git(tmp_path / "repo")
    assert _bash("sed -i s/a/b/ src/calc.py", raiz) is None
    assert _bash("python3 -c \"open('src/calc.py','w')\"", tmp_path) is None


def test_salida_de_emergencia(tmp_path, monkeypatch):
    raiz, _ = _unidad(tmp_path)
    monkeypatch.setenv(guardia.ENV_GUARDIA, "0")
    assert _bash("sed -i s/a/b/ src/calc.py", raiz) is None


def test_las_ordenes_que_no_casan_las_decide_el_arnes(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    for orden in (
        "ls -la",
        "git status",
        "cat README.md",
        "tee README.md < /dev/null",  # fuera de los tres patrones: documentado
        "cp src/calc.py README.md",
        "python3 -m pytest",
        "",
        "   ",
    ):
        assert _bash(orden, raiz) is None, orden
        assert _bash(orden, worktree) is None, orden
    assert guardia.hook_claude_code({"tool_name": "Bash", "tool_input": {}, "cwd": str(raiz)}) is None
    assert (
        guardia.hook_claude_code({"tool_name": "Bash", "tool_input": {"command": 3}, "cwd": str(raiz)})
        is None
    )


def test_falla_abierta_con_aviso(tmp_path, monkeypatch, capsys):
    raiz, _ = _unidad(tmp_path)

    def roto(orden, carpeta):
        raise RuntimeError("no se pudo")

    monkeypatch.setattr(ordenes_shell, "hallazgos", roto)
    assert _bash("sed -i s/a/b/ src/calc.py", raiz) is None
    assert "no pudo leer la orden (no se pudo)" in capsys.readouterr().err


# --- los cuatro arneses ----------------------------------------------------------------------------


def test_los_cuatro_arneses_revisan_su_shell(tmp_path):
    raiz, worktree = _unidad(tmp_path)
    orden = "sed -i s/a/b/ src/calc.py"

    respuesta = guardia.hook_codex(
        {
            "tool_name": "Bash",
            "tool_input": {"command": orden},
            "cwd": str(raiz),
            "permission_mode": "default",
        }
    )
    assert respuesta["hookSpecificOutput"]["permissionDecision"] == "deny"
    assert (
        guardia.hook_codex({"tool_name": "Bash", "tool_input": {"command": orden}, "cwd": str(worktree)})
        is None
    )

    respuesta = guardia.hook_copilot({"toolName": "bash", "toolArgs": {"command": orden}, "cwd": str(raiz)})
    assert respuesta["permissionDecision"] == "deny" and "regla 1" in respuesta["permissionDecisionReason"]
    texto = json.dumps({"command": orden})  # versiones anteriores de Copilot mandan `toolArgs` como texto
    assert (
        guardia.hook_copilot({"toolName": "bash", "toolArgs": texto, "cwd": str(raiz)})["permissionDecision"]
        == "deny"
    )
    assert (
        guardia.hook_copilot({"toolName": "bash", "toolArgs": {"command": orden}, "cwd": str(worktree)})
        is None
    )

    respuesta = guardia.hook_opencode(
        {"evento": "tool", "tool": "bash", "args": {"command": orden}, "directory": str(raiz)}
    )
    assert respuesta["decision"] == "deny" and "regla 1" in respuesta["motivo"]
    permitido = guardia.hook_opencode(
        {"evento": "tool", "tool": "bash", "args": {"command": orden}, "directory": str(worktree)}
    )
    assert permitido["decision"] == "allow"


def test_cli_hook_de_claude_code_con_bash(tmp_path, monkeypatch, capsys):
    raiz, _ = _unidad(tmp_path)
    entrada = {"tool_name": "Bash", "tool_input": {"command": "sed -i s/a/b/ src/calc.py"}, "cwd": str(raiz)}
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entrada)))
    assert cli.main(["hook", "claude-code"]) == 0
    salida = json.loads(capsys.readouterr().out)
    assert salida["hookSpecificOutput"]["permissionDecision"] == "deny"
    entrada["tool_input"]["command"] = "ls"
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(entrada)))
    assert cli.main(["hook", "claude-code"]) == 0
    assert capsys.readouterr().out == ""
