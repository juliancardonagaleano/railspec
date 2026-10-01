"""Cliente MCP de un proveedor de contexto con los verbos de PCE, con caché por consulta.

Solo usa ``search_catalog`` (el verbo ``search`` del contrato mínimo de
contexto). La caché es exacta por (url, tipo, consulta) con vigencia fija:
dos gates de la misma unidad con el mismo objeto no repiten la consulta, y la
respuesta, y con ella el prefijo del prompt, es idéntica entre ambos. Las
consultas fallidas no se cachean.

La forma exacta de la respuesta de PCE no está verificada contra el servicio
real: el parseo es tolerante y descarta lo que no reconoce.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections import OrderedDict
from collections.abc import Callable
from typing import Any, Protocol

from railspec.contracts.orden import ItemGobernanza


class ClienteContexto(Protocol):
    async def buscar(self, consultas: list[tuple[str | None, str]]) -> list[list[ItemGobernanza] | None]:
        """Una lista por consulta ``(tipo, texto)``; ``None`` donde esa consulta falló."""


class ClientePce:
    def __init__(
        self,
        url: str,
        api_key: str | None,
        *,
        timeout_s: float = 30.0,
        cache_s: float = 900.0,
        max_entradas: int = 2048,
        monotono: Callable[[], float] = time.monotonic,
    ) -> None:
        self.url = url
        self._headers = {"X-API-Key": api_key} if api_key else {}
        self._timeout = timeout_s
        self._cache_s = cache_s
        self._max = max_entradas
        self._monotono = monotono
        self._cache: OrderedDict[tuple[str | None, str], tuple[float, list[ItemGobernanza]]] = OrderedDict()

    async def buscar(self, consultas: list[tuple[str | None, str]]) -> list[list[ItemGobernanza] | None]:
        salida: list[list[ItemGobernanza] | None] = [self._en_cache(c) for c in consultas]
        faltan = [i for i, r in enumerate(salida) if r is None]
        if not faltan:
            return salida
        try:
            frescas = await asyncio.wait_for(self._llamar([consultas[i] for i in faltan]), self._timeout)
        except Exception:  # red, auth, presupuesto de PCE: esas consultas fallan
            return salida
        for i, items in zip(faltan, frescas, strict=True):
            salida[i] = items
            if items is not None and self._cache_s > 0:
                self._guardar(consultas[i], items)
        return salida

    def _en_cache(self, consulta: tuple[str | None, str]) -> list[ItemGobernanza] | None:
        entrada = self._cache.get(consulta)
        if entrada is None or self._monotono() - entrada[0] >= self._cache_s:
            return None
        self._cache.move_to_end(consulta)
        return list(entrada[1])

    def _guardar(self, consulta: tuple[str | None, str], items: list[ItemGobernanza]) -> None:
        self._cache[consulta] = (self._monotono(), list(items))
        self._cache.move_to_end(consulta)
        while len(self._cache) > self._max:
            self._cache.popitem(last=False)

    async def _llamar(self, consultas: list[tuple[str | None, str]]) -> list[list[ItemGobernanza] | None]:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        salida: list[list[ItemGobernanza] | None] = []
        async with streamablehttp_client(self.url, headers=self._headers) as (lectura, escritura, _):
            async with ClientSession(lectura, escritura) as sesion:
                await sesion.initialize()
                for tipo, texto in consultas:
                    argumentos: dict[str, Any] = {"query": texto[:500]}
                    if tipo:
                        argumentos["type"] = tipo
                    r = await sesion.call_tool("search_catalog", argumentos)
                    salida.append(None if r.isError else items_de(r, tipo or "contexto"))
        return salida


def items_de(resultado: Any, tipo: str) -> list[ItemGobernanza]:
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
