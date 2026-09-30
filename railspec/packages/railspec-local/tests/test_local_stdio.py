"""Proxy real por stdio contra un servidor MCP Streamable HTTP real.

Las demás pruebas usan un servidor doble en memoria; esta levanta ``railspec mcp``
como proceso, igual que lo lanza el arnés, y un servidor HTTP mínimo que habla
el contrato, para cubrir lo que solo aparece con transportes reales (la sesión
MCP hacia el servidor sobrevive entre tools atendidas en tareas distintas).
"""

from __future__ import annotations

import asyncio
import json
import socket
import subprocess
import sys
import textwrap
import time

import pytest
from local_fabricas import repo_git
from mcp import Client
from mcp.client.stdio import StdioServerParameters, stdio_client
from railspec.contracts._base import VERSION_CONTRATO
from railspec.local import config

SERVIDOR = textwrap.dedent(
    """
    import sys
    from typing import Any

    from mcp.server.mcpserver import MCPServer

    servidor = MCPServer("railspec-doble")
    llamadas = 0


    def unit_list(
        alcance: dict[str, Any],
        version_contrato: str | None = None,
        repositorio: str | None = None,
        fase: list[str] | None = None,
        estado: list[str] | None = None,
        integradas: bool | None = None,
        cursor: str | None = None,
        limite: int = 50,
    ) -> dict[str, Any]:
        global llamadas
        llamadas += 1
        return {{"version_contrato": "{version}", "unidades": [], "cursor_siguiente": str(llamadas)}}


    servidor.add_tool(unit_list, name="unit_list", structured_output=False)
    servidor.run("streamable-http", host="127.0.0.1", port=int(sys.argv[1]))
    """
)


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar_puerto(puerto: int, proceso: subprocess.Popen, limite_s: float = 20.0) -> None:
    fin = time.monotonic() + limite_s
    while time.monotonic() < fin:
        if proceso.poll() is not None:
            pytest.fail(f"el servidor doble terminó: {proceso.stderr.read().decode()}")
        try:
            with socket.create_connection(("127.0.0.1", puerto), timeout=0.2):
                return
        except OSError:
            time.sleep(0.1)
    pytest.fail("el servidor doble no abrió el puerto")


@pytest.fixture
def servidor_http(tmp_path):
    script = tmp_path / "servidor_doble.py"
    script.write_text(SERVIDOR.format(version=VERSION_CONTRATO), encoding="utf-8")
    puerto = _puerto_libre()
    proceso = subprocess.Popen(
        [sys.executable, str(script), str(puerto)], stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
    )
    try:
        _esperar_puerto(puerto, proceso)
        yield f"http://127.0.0.1:{puerto}/mcp"
    finally:
        proceso.terminate()
        proceso.wait(timeout=10)


def test_proxy_por_stdio_atiende_varias_tools_con_una_sesion_hacia_el_servidor(tmp_path, servidor_http):
    raiz = repo_git(tmp_path / "repo")
    config.escribir_config_repositorio(
        raiz, config.ConfigRepositorio(org="acme", workspace="w", repositorio="r")
    )
    parametros = StdioServerParameters(
        command=sys.executable,
        args=["-m", "railspec.local.cli", "--repo", str(raiz), "mcp"],
        env={config.ENV_URL: servidor_http, config.ENV_TOKEN: "t"},
        cwd=str(raiz),
    )

    async def flujo() -> list[dict]:
        async with Client(stdio_client(parametros)) as cliente:
            salidas = []
            for _ in range(3):
                resultado = await cliente.call_tool("unit_list", {})
                assert not resultado.is_error, resultado.content
                salidas.append(json.loads(resultado.content[0].text))
            return salidas

    salidas = asyncio.run(asyncio.wait_for(flujo(), timeout=60))
    # Tres llamadas atendidas por el mismo proceso del proxy, cada una en su tarea.
    assert [s["cursor_siguiente"] for s in salidas] == ["1", "2", "3"]
    assert all(s["unidades"] == [] for s in salidas)
