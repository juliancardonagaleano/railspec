"""``GET /metrics`` en formato de texto de Prometheus.

Solo existe si ``RAILSPEC_METRICAS_TOKEN`` está definido y exige ``Authorization: Bearer <token>``: el
servidor puede estar en internet (Render) y las métricas no son para cualquiera. Contadores en memoria de
cada réplica (se reinician con ella); Prometheus suma entre réplicas.

- ``railspec_info{version}``: versión del servidor.
- ``railspec_estado_esquema{origen="codigo"|"almacenado"}``: versión del esquema del estado que entiende
  este código y la que hay guardada (ver ``estado/esquema.py``).
- ``railspec_sonda_ok{sonda}``: 1 si la base (o el grafo) responde, como ``/healthz``.
- ``railspec_http_peticiones_total{grupo,metodo,estado}``: peticiones por superficie y clase de estado.
- ``railspec_proceso_inicio_segundos``: instante de arranque, en segundos desde la época.
"""

from __future__ import annotations

import hmac
import threading
import time
from collections import Counter
from collections.abc import Callable
from typing import Any

from fastapi import Request
from fastapi.responses import PlainTextResponse

from .api.identidad import token_de_cabecera
from .api.superficies import TOPE_SONDA_S, _sondear
from .estado import VersionEsquema

TIPO_CONTENIDO = "text/plain; version=0.0.4; charset=utf-8"

#: Prefijo de ruta -> grupo. El más específico primero; lo demás es ``otras`` (cardinalidad acotada).
_GRUPOS = (
    ("/consola/api", "consola_api"),
    ("/consola", "consola"),
    ("/mcp", "mcp"),
    ("/v1", "v1"),
    ("/healthz", "sondas"),
    ("/livez", "sondas"),
    ("/metrics", "sondas"),
)
_METODOS = frozenset({"GET", "HEAD", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"})


def _grupo(ruta: str) -> str:
    for prefijo, grupo in _GRUPOS:
        if ruta == prefijo or ruta.startswith(prefijo + "/"):
            return grupo
    return "otras"


class Metricas:
    """Contadores de peticiones de esta réplica."""

    def __init__(self) -> None:
        self.inicio = time.time()
        self._peticiones: Counter[tuple[str, str, str]] = Counter()
        self._cerrojo = threading.Lock()

    def contar(self, ruta: str, metodo: str, estado: int) -> None:
        clave = (_grupo(ruta), metodo if metodo in _METODOS else "otro", f"{estado // 100}xx")
        with self._cerrojo:
            self._peticiones[clave] += 1

    def peticiones(self) -> dict[tuple[str, str, str], int]:
        with self._cerrojo:
            return dict(self._peticiones)


class MedirPeticiones:
    """Middleware ASGI puro (``BaseHTTPMiddleware`` rompería las respuestas en streaming de ``/mcp``)."""

    def __init__(self, app: Any, metricas: Metricas) -> None:
        self.app = app
        self.metricas = metricas

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        estado = 500

        async def enviar(mensaje: Any) -> None:
            nonlocal estado
            if mensaje["type"] == "http.response.start":
                estado = mensaje["status"]
            await send(mensaje)

        try:
            await self.app(scope, receive, enviar)
        finally:
            self.metricas.contar(scope.get("path", ""), scope.get("method", ""), estado)


def _etiqueta(valor: str) -> str:
    return valor.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _linea(nombre: str, valor: float, **etiquetas: str) -> str:
    if not etiquetas:
        return f"{nombre} {valor}"
    pares = ",".join(f'{k}="{_etiqueta(v)}"' for k, v in etiquetas.items())
    return f"{nombre}{{{pares}}} {valor}"


async def renderizar(
    metricas: Metricas,
    *,
    version_app: str,
    esquema: VersionEsquema,
    sondas: dict[str, Callable[[], Any]],
) -> str:
    sondeo = await _sondear(sondas, TOPE_SONDA_S)
    salida = [
        "# HELP railspec_info Versión del servidor.",
        "# TYPE railspec_info gauge",
        _linea("railspec_info", 1, version=version_app),
        "# HELP railspec_estado_esquema Versión del esquema del estado: la del código y la guardada.",
        "# TYPE railspec_estado_esquema gauge",
        _linea("railspec_estado_esquema", esquema.codigo, origen="codigo"),
        _linea("railspec_estado_esquema", esquema.almacenada, origen="almacenado"),
        "# HELP railspec_sonda_ok 1 si la dependencia responde dentro del tope.",
        "# TYPE railspec_sonda_ok gauge",
        *(_linea("railspec_sonda_ok", 1 if v == "ok" else 0, sonda=n) for n, v in sorted(sondeo.items())),
        "# HELP railspec_http_peticiones_total Peticiones atendidas, por superficie y clase de estado.",
        "# TYPE railspec_http_peticiones_total counter",
        *(
            _linea("railspec_http_peticiones_total", n, grupo=g, metodo=m, estado=e)
            for (g, m, e), n in sorted(metricas.peticiones().items())
        ),
        "# HELP railspec_proceso_inicio_segundos Arranque del proceso, en segundos desde la época.",
        "# TYPE railspec_proceso_inicio_segundos gauge",
        _linea("railspec_proceso_inicio_segundos", round(metricas.inicio, 3)),
    ]
    return "\n".join(salida) + "\n"


def montar_metricas(
    app: Any,
    *,
    token: str,
    version_app: str,
    esquema: VersionEsquema,
    sondas: dict[str, Callable[[], Any]],
    metricas: Metricas | None = None,
) -> Metricas:
    """Añade ``GET /metrics`` (con token) y el contador de peticiones a la app."""

    metricas = metricas or Metricas()
    esperado = token.encode()

    @app.get("/metrics", include_in_schema=False)
    async def _metricas(request: Request) -> PlainTextResponse:
        presentado = token_de_cabecera(request.headers.get("authorization"))
        if presentado is None or not hmac.compare_digest(presentado.encode(), esperado):
            return PlainTextResponse(
                "token de métricas inválido\n", status_code=401, headers={"WWW-Authenticate": "Bearer"}
            )
        cuerpo = await renderizar(metricas, version_app=version_app, esquema=esquema, sondas=sondas)
        return PlainTextResponse(cuerpo, media_type=TIPO_CONTENIDO)

    app.add_middleware(MedirPeticiones, metricas=metricas)
    return metricas
