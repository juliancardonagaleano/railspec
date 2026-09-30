"""Servidor MCP local visto desde un arnés (cliente MCP en proceso)."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import mcp.types as types
from local_fabricas import ServidorDoble, crear_proxy, orden_implementar
from mcp import Client
from railspec.local.servidor_mcp import crear_servidor


def _datos(resultado) -> dict:
    assert not resultado.is_error, resultado.content
    return json.loads(resultado.content[0].text)


def test_tools_expuestas_y_bucle_por_mcp(tmp_path):
    servidor = ServidorDoble([orden_implementar])
    proxy = crear_proxy(tmp_path, servidor)

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            nombres = {t.name for t in (await cliente.list_tools()).tools}
            assert nombres == {
                "unit_start",
                "unit_advance",
                "unit_report",
                "unit_checkpoint",
                "unit_approve",
                "unit_set_mode",
                "unit_integrate",
                "unit_status",
                "unit_list",
                "graph_query",
                "insumo_pull",
                "railspec_sync",
            }
            inicio = _datos(
                await cliente.call_tool("unit_start", {"titulo": "Sumar", "pedido": "Arregla suma."})
            )
            worktree = Path(inicio["worktree"])
            avance = _datos(await cliente.call_tool("unit_advance", {}))
            assert avance["tipo"] == "orden"
            (worktree / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n")
            reporte = _datos(
                await cliente.call_tool("unit_report", {"tareas_completadas": ["T-01"], "modelo": "sonnet"})
            )
            assert reporte["aceptado"] is True
            return worktree

    asyncio.run(flujo())
    assert servidor.reportes[-1].uso_modelo.modelo == "sonnet"


def test_errores_del_proxy_llegan_como_resultado_de_error(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            return await cliente.call_tool("unit_report", {})

    resultado = asyncio.run(flujo())
    assert resultado.is_error
    assert "unidades locales: ninguna" in resultado.content[0].text


def test_checkpoint_se_resuelve_por_elicitation(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)
    preguntas: list[str] = []

    async def elicitar(contexto, params):
        preguntas.append(params.message)
        return types.ElicitResult(action="accept", content={"decision": "aprobado", "comentario": ""})

    async def flujo():
        async with Client(crear_servidor(lambda: proxy), elicitation_callback=elicitar) as cliente:
            await cliente.call_tool("unit_start", {"titulo": "Sumar", "pedido": "Arregla suma."})
            servidor.abrir_checkpoint()
            avance = _datos(await cliente.call_tool("unit_advance", {}))
            assert avance["tipo"] == "checkpoint" and "unit_checkpoint" in avance["como_resolver"]
            return _datos(await cliente.call_tool("unit_checkpoint", {}))

    respuesta = asyncio.run(flujo())
    assert respuesta["tipo"] == "checkpoint-resuelto" and respuesta["decision"] == "aprobado"
    assert "¿Apruebas el spec?" in preguntas[0]
    assert servidor.resoluciones[0].decision.value == "aprobado"


def test_checkpoint_sin_elicitation_no_se_resuelve_solo(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)

    async def flujo():
        async with Client(crear_servidor(lambda: proxy)) as cliente:
            await cliente.call_tool("unit_start", {"titulo": "Sumar", "pedido": "Arregla suma."})
            servidor.abrir_checkpoint()
            await cliente.call_tool("unit_advance", {})
            try:
                return await cliente.call_tool("unit_checkpoint", {})
            except Exception as exc:  # el cliente no declaró elicitation
                return exc

    resultado = asyncio.run(flujo())
    assert isinstance(resultado, Exception) or resultado.is_error
    assert servidor.resoluciones == [] and servidor.checkpoint is not None


def test_checkpoint_rechazado_por_el_humano_queda_pendiente(tmp_path):
    servidor = ServidorDoble()
    proxy = crear_proxy(tmp_path, servidor)

    async def declinar(contexto, params):
        return types.ElicitResult(action="decline")

    async def flujo():
        async with Client(crear_servidor(lambda: proxy), elicitation_callback=declinar) as cliente:
            await cliente.call_tool("unit_start", {"titulo": "Sumar", "pedido": "Arregla suma."})
            servidor.abrir_checkpoint()
            await cliente.call_tool("unit_advance", {})
            return _datos(await cliente.call_tool("unit_checkpoint", {}))

    assert asyncio.run(flujo())["tipo"] == "checkpoint"
    assert servidor.resoluciones == []


def test_tools_del_contrato_usan_su_alias_mcp():
    from railspec.contracts.tools import resolver_tool

    async def nombres():
        async with Client(crear_servidor(lambda: None)) as cliente:
            return {t.name for t in (await cliente.list_tools()).tools}

    propias = {"unit_checkpoint", "insumo_pull", "railspec_sync"}
    for nombre in asyncio.run(nombres()) - propias:
        tool = resolver_tool(nombre)
        assert tool is not None and tool.nombre_mcp == nombre


def test_transporte_llama_al_servidor_por_alias_mcp():
    from railspec.local.transporte_mcp import TransporteMcpHttp

    llamadas = []

    class ClienteFalso:
        async def call_tool(self, nombre, argumentos):
            llamadas.append(nombre)
            return types.CallToolResult(content=[], structured_content={"ok": True}, is_error=False)

    transporte = TransporteMcpHttp("http://railspec.invalid/mcp", "token")

    async def abrir():
        return ClienteFalso()

    transporte._abrir = abrir
    assert asyncio.run(transporte.llamar("unit.set_mode", {})) == {"ok": True}
    assert llamadas == ["unit_set_mode"]
