"""Cabeceras de seguridad y límite de peticiones de lo que sirve la consola.

Un middleware ASGI puro (no ``BaseHTTPMiddleware``: no interfiere con el SSE ni
con las respuestas en streaming) montado en la app del servidor. Solo actúa
sobre ``/consola`` y ``/consola/*``:

- Cabeceras en toda respuesta, también 308, 401, 404 y los estáticos:
  ``Content-Security-Policy`` (SPA: scripts solo del propio origen, sin
  evaluación dinámica ni scripts en línea; API: nada), ``frame-ancestors 'none'``
  y ``X-Frame-Options: DENY`` contra el clickjacking, ``nosniff``,
  ``Referrer-Policy: no-referrer`` y, solo con URL pública https, HSTS.
  No pisan lo que la ruta ya fijó (p. ej. ``Cache-Control`` de los estáticos).
- ``Cache-Control: no-store`` en ``/consola/api/auth/*`` (sesión y token).
- Límite de peticiones por IP en ``/consola/api/auth/*``, en memoria y de
  ventana deslizante. La IP es ``scope["client"]``, la que resuelve el servidor
  (uvicorn la toma de ``X-Forwarded-For`` solo de los proxies de
  ``FORWARDED_ALLOW_IPS``); este módulo nunca lee esa cabecera por su cuenta.
  El límite es por réplica: con N réplicas el tope efectivo es N veces mayor.

El CSP de la SPA sale de revisar el build de Vite: ``index.html`` solo trae un
``<script type="module" src>`` y ``<link>`` del propio origen, sin estilos ni
scripts en línea; ``style-src`` admite ``'unsafe-inline'`` porque React y las
librerías de gráficos (ECharts, Sigma, React Flow) fijan estilos en elementos.
"""

from __future__ import annotations

import math
import time
from collections import deque
from collections.abc import Callable
from typing import Any

from fastapi.responses import JSONResponse

CSP_SPA = "; ".join(
    [
        "default-src 'self'",
        "script-src 'self'",
        "style-src 'self' 'unsafe-inline'",
        "img-src 'self' data:",
        "font-src 'self' data:",
        "connect-src 'self'",
        "object-src 'none'",
        "base-uri 'none'",
        "form-action 'self'",
        "frame-ancestors 'none'",
    ]
)
#: La API solo devuelve JSON y SSE: no carga nada.
CSP_API = "default-src 'none'; frame-ancestors 'none'"
HSTS = "max-age=31536000"


class LimitadorVentana:
    """Máximo ``maximo`` peticiones por clave en ``ventana_s`` segundos (ventana deslizante)."""

    def __init__(
        self,
        maximo: int,
        ventana_s: float = 60.0,
        *,
        max_claves: int = 5000,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self.maximo = maximo
        self.ventana_s = ventana_s
        self.max_claves = max_claves
        self.reloj = reloj
        self._golpes: dict[str, deque[float]] = {}

    def golpear(self, clave: str) -> int | None:
        """Cuenta una petición. None si pasa; si no, los segundos a esperar (``Retry-After``)."""

        ahora = self.reloj()
        corte = ahora - self.ventana_s
        golpes = self._golpes.get(clave)
        if golpes is None:
            if len(self._golpes) >= self.max_claves:
                self._hacer_sitio(corte)
            golpes = self._golpes[clave] = deque()
        while golpes and golpes[0] <= corte:
            golpes.popleft()
        if len(golpes) >= self.maximo:
            return max(1, math.ceil(golpes[0] + self.ventana_s - ahora))
        golpes.append(ahora)
        return None

    def _hacer_sitio(self, corte: float) -> None:
        """Tabla llena: se descartan las IPs ya vencidas y, si todas están activas, la más antigua."""

        vencidas = [k for k, g in self._golpes.items() if not g or g[-1] <= corte]
        for clave in vencidas:
            del self._golpes[clave]
        while len(self._golpes) >= self.max_claves:
            del self._golpes[next(iter(self._golpes))]


class SeguridadConsola:
    def __init__(
        self,
        app: Any,
        *,
        prefijo_spa: str,
        prefijo_api: str,
        hsts: bool,
        limitador: LimitadorVentana | None,
    ) -> None:
        self.app = app
        self.prefijo_spa = prefijo_spa
        self.prefijo_api = prefijo_api
        self.hsts = hsts
        self.limitador = limitador

    def _cabeceras(self, es_api: bool, es_auth: bool) -> dict[str, str]:
        cabeceras = {
            "Content-Security-Policy": CSP_API if es_api else CSP_SPA,
            "X-Frame-Options": "DENY",
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "no-referrer",
        }
        if self.hsts:
            cabeceras["Strict-Transport-Security"] = HSTS
        if es_auth:
            cabeceras["Cache-Control"] = "no-store"
        return cabeceras

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        ruta = scope.get("path", "") if scope["type"] == "http" else ""
        if not (ruta == self.prefijo_spa or ruta.startswith(self.prefijo_spa + "/")):
            await self.app(scope, receive, send)
            return
        es_api = ruta == self.prefijo_api or ruta.startswith(self.prefijo_api + "/")
        es_auth = ruta.startswith(self.prefijo_api + "/auth/")
        cabeceras = self._cabeceras(es_api, es_auth)

        if es_auth and self.limitador is not None:
            cliente = scope.get("client")
            espera = self.limitador.golpear(cliente[0] if cliente else "desconocido")
            if espera is not None:
                respuesta = JSONResponse(
                    {"detalle": f"demasiadas peticiones de inicio de sesión; reintenta en {espera} s"},
                    status_code=429,
                    headers={**cabeceras, "Retry-After": str(espera)},
                )
                await respuesta(scope, receive, send)
                return

        async def enviar(mensaje: Any) -> None:
            if mensaje["type"] == "http.response.start":
                previas = list(mensaje.get("headers", []))
                fijadas = {nombre.lower() for nombre, _ in previas}
                for nombre, valor in cabeceras.items():
                    if nombre.lower().encode() not in fijadas:
                        previas.append((nombre.lower().encode(), valor.encode()))
                mensaje = {**mensaje, "headers": previas}
            await send(mensaje)

        await self.app(scope, receive, enviar)
