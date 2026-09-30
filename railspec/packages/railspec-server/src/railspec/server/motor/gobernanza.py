"""Proveedor de gobernanza del gate.

El gate nunca critica de memoria: si la gobernanza no respondió (``no``) o
respondió a medias (``parcial``), escala con ``sin-gobernanza`` antes de gastar
un solo token de modelo. Sin ``RAILSPEC_PCE_URL`` el servidor usa
``GobernanzaNoConfigurada`` y todo gate escala: es deliberado.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from railspec.contracts.comun import AlcanceUnidad, GateFase, GobernanzaConsultada
from railspec.contracts.orden import ItemGobernanza


@dataclass(frozen=True)
class ResultadoGobernanza:
    consultada: GobernanzaConsultada
    items: list[ItemGobernanza] = field(default_factory=list)
    detalle: str = ""


@runtime_checkable
class ProveedorGobernanza(Protocol):
    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza: ...


class GobernanzaNoConfigurada:
    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        return ResultadoGobernanza(GobernanzaConsultada.no, detalle="proveedor de gobernanza no configurado")


class GobernanzaFija:
    """Respuesta fija: pruebas y workspaces que declaran gobernanza vacía a propósito."""

    def __init__(self, items: list[ItemGobernanza] | None = None, consultada=GobernanzaConsultada.si) -> None:
        self._r = ResultadoGobernanza(consultada, list(items or []))

    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        return self._r


class GobernanzaPceMcp:
    """PCE por MCP (streamable HTTP), con las mismas consultas que el gate del kit.

    Llama ``search_catalog`` una vez por tipo (``adr``, ``policy``,
    ``principle``) con el objeto de la unidad como consulta. Si alguna falla,
    la consulta es ``parcial``; si fallan todas, ``no``. La forma exacta de la
    respuesta de PCE no está verificada contra el servicio real: el parseo es
    tolerante y todo lo que no reconoce lo descarta.
    """

    TIPOS = ("adr", "policy", "principle")

    def __init__(self, url: str, api_key: str | None, timeout_s: float = 30.0) -> None:
        self._url = url
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._timeout = timeout_s

    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        try:
            respuestas = await asyncio.wait_for(self._consultas(objeto), self._timeout)
        except Exception as exc:  # red, auth, presupuesto de PCE
            return ResultadoGobernanza(GobernanzaConsultada.no, detalle=f"PCE no respondió: {exc}")
        items: dict[str, ItemGobernanza] = {}
        fallidas = 0
        for tipo, resultado in respuestas:
            if resultado is None:
                fallidas += 1
                continue
            for item in _items(resultado, tipo):
                items.setdefault(item.id, item)
        if fallidas == len(self.TIPOS):
            consultada = GobernanzaConsultada.no
        elif fallidas:
            consultada = GobernanzaConsultada.parcial
        else:
            consultada = GobernanzaConsultada.si
        return ResultadoGobernanza(consultada, sorted(items.values(), key=lambda i: i.id))

    async def _consultas(self, objeto: str) -> list[tuple[str, Any]]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        salida: list[tuple[str, Any]] = []
        async with streamablehttp_client(self._url, headers=self._headers) as (lectura, escritura, _):
            async with ClientSession(lectura, escritura) as sesion:
                await sesion.initialize()
                for tipo in self.TIPOS:
                    r = await sesion.call_tool("search_catalog", {"query": objeto[:500], "type": tipo})
                    salida.append((tipo, None if r.isError else r))
        return salida


def _items(resultado: Any, tipo: str) -> list[ItemGobernanza]:
    encontrados: list[ItemGobernanza] = []
    for bloque in getattr(resultado, "content", []) or []:
        texto = getattr(bloque, "text", None)
        if not texto:
            continue
        try:
            datos = json.loads(texto)
        except ValueError:
            continue
        filas = (datos.get("results") or datos.get("items") or []) if isinstance(datos, dict) else datos
        for fila in filas if isinstance(filas, list) else []:
            if not isinstance(fila, dict) or not fila.get("id"):
                continue
            encontrados.append(
                ItemGobernanza(
                    id=str(fila["id"])[:200],
                    tipo=str(fila.get("type") or tipo)[:40],
                    titulo=str(fila.get("title") or fila.get("name") or fila["id"])[:300],
                    resumen=str(fila.get("summary") or fila.get("description") or "")[:4000],
                    hash_version=(str(fila["sourceDigest"])[:128] if fila.get("sourceDigest") else None),
                )
            )
    return encontrados
