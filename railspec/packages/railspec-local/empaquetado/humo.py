"""Humo del binario congelado ``railspec``: comprueba que funciona fuera del repositorio.

Uso::

    python railspec/packages/railspec-local/empaquetado/humo.py RUTA/AL/railspec

Solo usa la biblioteca estándar (más PyInstaller, si está, para inspeccionar
el archivo): así corre con cualquier Python 3.11+, no hace falta el entorno de
construcción. Lanza el binario con un entorno mínimo cuyo PATH tiene delante el
directorio del binario (como lo vería el arnés) y comprueba:

1. ``railspec --version`` y ``railspec instalar --help``.
2. ``instalar`` con los dos arneses en un repositorio git temporal escribe sus
   archivos (``.mcp.json``, ``opencode.jsonc``, comandos, skills…) y
   ``desinstalar --config`` los quita todos.
3. ``railspec mcp`` responde por stdio a ``initialize`` y ``tools/list``, y
   una tool que necesita el servidor falla limpia (``RAILSPEC_URL`` apunta a
   un puerto cerrado) en vez de romperse por un módulo o un metadato que falte.
4. ``railspec hook claude-code`` (si el binario tiene ese subcomando) acepta
   un evento por stdin y sale con 0 sin escribir nada.
5. Con PyInstaller instalado: el archivo incluye las plantillas, los metadatos
   de railspec-local con su entry point ``railspec.indexadores`` y el módulo
   del indexador, que nadie importa por nombre.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

TIMEOUT_S = 60


class FalloHumo(AssertionError):
    pass


def _comprobar(condicion: bool, mensaje: str) -> None:
    if not condicion:
        raise FalloHumo(mensaje)


def _entorno(binario: Path, casa: Path, **extra: str) -> dict[str, str]:
    """Entorno mínimo: sin el virtualenv de construcción ni PYTHONPATH."""

    sistema = ["/usr/local/bin", "/usr/bin", "/bin", "/opt/homebrew/bin"]
    env = {
        "PATH": os.pathsep.join([str(binario.parent), *sistema]),
        "HOME": str(casa),
        "LANG": "C.UTF-8",
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "humo",
        "GIT_AUTHOR_EMAIL": "humo@railspec.invalid",
        "GIT_COMMITTER_NAME": "humo",
        "GIT_COMMITTER_EMAIL": "humo@railspec.invalid",
    }
    if "TMPDIR" in os.environ:
        env["TMPDIR"] = os.environ["TMPDIR"]
    env.update(extra)
    return env


def _correr(
    args: list[str], env: dict[str, str], cwd: Path, entrada: str | None = None
) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        args, env=env, cwd=cwd, input=entrada, capture_output=True, text=True, timeout=TIMEOUT_S, check=False
    )
    if proc.returncode != 0:
        raise FalloHumo(f"{' '.join(args)} salió con {proc.returncode}:\n{proc.stdout}\n{proc.stderr}")
    return proc


def version(binario: Path, env: dict[str, str], cwd: Path) -> None:
    inicio = time.monotonic()
    salida = _correr([str(binario), "--version"], env, cwd).stdout.strip()
    _comprobar(salida.startswith("railspec-local "), f"--version inesperado: {salida!r}")
    print(f"ok  --version: {salida} ({time.monotonic() - inicio:.2f} s)")
    _correr([str(binario), "instalar", "--help"], env, cwd)
    print("ok  instalar --help")


def instalar_y_desinstalar(binario: Path, env: dict[str, str], tmp: Path) -> None:
    repo = tmp / "repo"
    repo.mkdir()
    _correr(["git", "init", "-q"], env, repo)
    _correr(["git", "commit", "-q", "--allow-empty", "-m", "inicio"], env, repo)
    proc = _correr(
        [str(binario), "instalar", "--org", "o", "--workspace", "w", "--repositorio", "r"]
        + ["--arnes", "claude-code", "--arnes", "opencode"],
        env,
        repo,
    )
    cambios = json.loads(proc.stdout)["cambios"]
    escritos = [ruta for rutas in cambios.values() for ruta in rutas]
    for esperado in (".mcp.json", ".claude/commands/railspec.md", "opencode.jsonc", "AGENTS.md", "CLAUDE.md"):
        _comprobar(esperado in escritos, f"instalar no escribió {esperado}: {cambios}")
    for ruta in escritos:
        _comprobar((repo / ruta).is_file(), f"instalar dice que escribió {ruta} pero no existe")
    mcp = json.loads((repo / ".mcp.json").read_text(encoding="utf-8"))
    _comprobar(
        mcp["mcpServers"]["railspec"]["command"] == "railspec", f".mcp.json no registra el proxy: {mcp}"
    )
    comando = (repo / ".claude/commands/railspec.md").read_text(encoding="utf-8")
    _comprobar(len(comando) > 100, "la plantilla del comando /railspec llegó vacía")
    print(f"ok  instalar: {len(escritos)} archivos ({', '.join(sorted(cambios))})")

    _correr([str(binario), "desinstalar", "--config"], env, repo)
    restos = [ruta for ruta in escritos if (repo / ruta).exists()]
    _comprobar(not restos, f"desinstalar dejó: {restos}")
    estado = _correr(["git", "status", "--porcelain", "--untracked-files=all", "--ignored"], env, repo).stdout
    _comprobar(not estado.strip(), f"desinstalar dejó el árbol sucio:\n{estado}")
    print("ok  desinstalar --config: árbol limpio")


def _mensaje(proc: subprocess.Popen, mensaje: dict, fin: float) -> dict | None:
    proc.stdin.write(json.dumps(mensaje) + "\n")
    proc.stdin.flush()
    if "id" not in mensaje:
        return None
    while time.monotonic() < fin:
        linea = proc.stdout.readline()
        if not linea:
            raise FalloHumo(f"railspec mcp cerró la salida; stderr:\n{proc.stderr.read()}")
        respuesta = json.loads(linea)
        if respuesta.get("id") == mensaje["id"]:
            return respuesta
    raise FalloHumo(f"sin respuesta a {mensaje['method']}")


def servidor_mcp(binario: Path, env: dict[str, str], tmp: Path) -> None:
    repo = tmp / "repo-mcp"
    repo.mkdir()
    _correr(["git", "init", "-q"], env, repo)
    _correr(["git", "commit", "-q", "--allow-empty", "-m", "inicio"], env, repo)
    _correr([str(binario), "instalar", "--org", "o", "--workspace", "w", "--repositorio", "r"], env, repo)
    # Puerto 9 (discard): nadie escucha, la conexión se rechaza al instante.
    env_mcp = {**env, "RAILSPEC_URL": "http://127.0.0.1:9/mcp", "RAILSPEC_TOKEN": "humo"}
    proc = subprocess.Popen(
        [str(binario), "mcp"],
        env=env_mcp,
        cwd=repo,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    fin = time.monotonic() + TIMEOUT_S
    try:
        inicio = time.monotonic()
        iniciar = _mensaje(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2025-06-18",
                    "capabilities": {},
                    "clientInfo": {"name": "humo", "version": "0"},
                },
            },
            fin,
        )
        _comprobar("result" in iniciar, f"initialize falló: {iniciar}")
        info = iniciar["result"]["serverInfo"]
        print(f"ok  mcp initialize: {info['name']} {info['version']} ({time.monotonic() - inicio:.2f} s)")
        _mensaje(proc, {"jsonrpc": "2.0", "method": "notifications/initialized"}, fin)
        lista = _mensaje(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}, fin)
        tools = sorted(t["name"] for t in lista["result"]["tools"])
        _comprobar("unit_start" in tools and "unit_list" in tools, f"tools/list incompleto: {tools}")
        sin_descripcion = [t["name"] for t in lista["result"]["tools"] if not t.get("description")]
        _comprobar(not sin_descripcion, f"tools sin descripción (¿docstrings eliminados?): {sin_descripcion}")
        print(f"ok  mcp tools/list: {len(tools)} tools")
        # unit_list crea el proxy: carga el indexador por entry point y abre el
        # transporte hacia el servidor, que no existe.
        llamada = _mensaje(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 3,
                "method": "tools/call",
                "params": {"name": "unit_list", "arguments": {}},
            },
            fin,
        )
        resultado = llamada.get("result", {})
        texto = " ".join(c.get("text", "") for c in resultado.get("content", []))
        _comprobar(resultado.get("isError") is True, f"unit_list debía fallar sin servidor: {llamada}")
        for roto in ("ModuleNotFoundError", "No module named", "PackageNotFoundError", "no implementa"):
            _comprobar(roto not in texto, f"unit_list falló por el empaquetado: {texto}")
        print(f"ok  mcp tools/call sin servidor: error limpio ({texto[:90]!r})")
    finally:
        proc.stdin.close()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def hook(binario: Path, env: dict[str, str], tmp: Path) -> None:
    ayuda = _correr([str(binario), "--help"], env, tmp).stdout
    if not re.search(r"\bhook\b", ayuda):
        print("--  hook: este binario no tiene el subcomando; se omite")
        return
    evento = json.dumps({"tool_name": "Read", "tool_input": {}, "cwd": "."})
    proc = _correr([str(binario), "hook", "claude-code"], env, tmp, entrada=evento)
    _comprobar(not proc.stdout and not proc.stderr, f"hook escribió algo: {proc.stdout!r} {proc.stderr!r}")
    print("ok  hook claude-code: 0 y sin salida")


def archivo(binario: Path) -> None:
    visor = shutil.which("pyi-archive_viewer") or shutil.which(
        "pyi-archive_viewer", path=str(Path(sys.executable).parent)
    )
    if visor is None:
        print("--  contenido del archivo: PyInstaller no está instalado; se omite")
        return
    listado = subprocess.run(
        [visor, "--recursive", "--brief", str(binario)], capture_output=True, text=True, check=True
    ).stdout
    nombres = {linea.strip() for linea in listado.splitlines()}
    for patron in (
        r"railspec/local/adaptadores/plantillas/comando\.md",
        r"railspec_local-[^/]+\.dist-info/entry_points\.txt",
        r"railspec_contracts-[^/]+\.dist-info/METADATA",
        r"railspec\.local\.indexador_cbm",
    ):
        _comprobar(any(re.fullmatch(patron, n) for n in nombres), f"falta en el archivo: {patron}")
    print("ok  archivo: plantillas, metadatos con entry point e indexador_cbm")


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print(__doc__, file=sys.stderr)
        return 2
    binario = Path(args[0]).resolve()
    if not os.access(binario, os.X_OK):
        print(f"no es ejecutable: {binario}", file=sys.stderr)
        return 2
    with tempfile.TemporaryDirectory(prefix="railspec-humo-") as nombre:
        tmp = Path(nombre)
        casa = tmp / "casa"
        casa.mkdir()
        env = _entorno(binario, casa)
        try:
            version(binario, env, tmp)
            instalar_y_desinstalar(binario, env, tmp)
            servidor_mcp(binario, env, tmp)
            hook(binario, env, tmp)
            archivo(binario)
        except FalloHumo as exc:
            print(f"FALLO {exc}", file=sys.stderr)
            return 1
    print("humo: todo bien")
    return 0


if __name__ == "__main__":
    sys.exit(main())
