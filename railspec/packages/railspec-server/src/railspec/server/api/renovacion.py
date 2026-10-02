"""Renovación del token de usuario de la GitHub App: ``POST /v1/auth/renovar``.

``railspec login`` (device flow) guarda el token de usuario y, si la App tiene activada «Expire user
authorization tokens», su refresh token. Renovar exige el client secret de la App, que no puede viajar a las
máquinas de los desarrolladores: este endpoint es el único sitio fuera de GitHub donde vive. Hace de puente
(``refresh_token`` -> token nuevo) y nada más:

- No tiene identidad propia: la credencial es el refresh token, que solo GitHub sabe validar. Con uno que no
  emitió esta App GitHub responde ``bad_refresh_token``.
- No guarda ni registra tokens. La respuesta lleva ``Cache-Control: no-store``.
- Las llamadas simultáneas a GitHub están acotadas (como en ``verificacion_github``): quien no consigue turno
  a tiempo recibe 503 y el proxy reintenta solo.

Cuerpo: ``{"refresh_token": "ghr_…", "client_id": "Iv…"}`` (``client_id`` opcional: si viene y no es el de la
App del servidor, 400, que explica mejor que un ``bad_refresh_token`` por qué falla). Respuesta 200:
``{"access_token", "expires_in", "refresh_token", "refresh_token_expires_in"}`` (los tres
últimos, nulos si GitHub no los da).
Errores: ``{"codigo", "detalle"}`` con 400 ``client-id-incorrecto``, 401 ``refresh-token-invalido``, 404
``renovacion-no-disponible`` (el servidor no tiene la GitHub App configurada), 422 ``entrada-invalida`` y 503
``github-no-disponible``.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ..consola.config import ConfigGithubApp

log = logging.getLogger("railspec.renovacion")

TOKEN = "https://github.com/login/oauth/access_token"
TOPE_REFRESH_TOKEN = 512
#: Hilos propios (como en ``api.superficies``): una avalancha de renovaciones no agota el grupo por defecto,
#: que usan las sondas de /healthz.
_HILOS = ThreadPoolExecutor(max_workers=8, thread_name_prefix="railspec-renovacion")


class _Rechazo(Exception):
    def __init__(self, estado: int, codigo: str, detalle: str) -> None:
        super().__init__(detalle)
        self.estado, self.codigo, self.detalle = estado, codigo, detalle


def _entero_positivo(valor: Any) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else None


class RenovadorGithub:
    """``cliente``: un ``httpx.Client`` (las pruebas inyectan un transporte simulado)."""

    def __init__(
        self,
        app: ConfigGithubApp | None,
        cliente: Any | None = None,
        *,
        max_concurrentes: int = 4,
        espera_s: float = 2.0,
        timeout_s: float = 10.0,
    ) -> None:
        self._app = app
        self._cliente = cliente
        self._timeout_s = timeout_s
        self._espera_s = espera_s
        self._cupo = threading.BoundedSemaphore(max_concurrentes)
        self._bloqueo_cliente = threading.Lock()

    def _http(self) -> Any:
        with self._bloqueo_cliente:
            if self._cliente is None:
                import httpx

                self._cliente = httpx.Client(timeout=self._timeout_s)
            return self._cliente

    def renovar(self, refresh_token: str, client_id: str | None) -> dict[str, Any]:
        """Bloqueante (red): las rutas async lo ejecutan en un hilo."""

        app = self._app
        if app is None:
            raise _Rechazo(404, "renovacion-no-disponible", "el servidor no tiene configurada la GitHub App")
        if client_id is not None and client_id != app.client_id:
            raise _Rechazo(
                400,
                "client-id-incorrecto",
                "el client id de tu sesión no es el de la GitHub App de este servidor: "
                "vuelve a ejecutar `railspec login`",
            )
        if not self._cupo.acquire(timeout=self._espera_s):
            raise _Rechazo(503, "github-no-disponible", "demasiadas renovaciones en curso: reintenta")
        try:
            try:
                r = self._http().post(
                    TOKEN,
                    data={
                        "client_id": app.client_id,
                        "client_secret": app.client_secret,
                        "grant_type": "refresh_token",
                        "refresh_token": refresh_token,
                    },
                    headers={"Accept": "application/json"},
                )
            except Exception as exc:  # red, timeout, TLS…
                raise _Rechazo(
                    503, "github-no-disponible", f"no se pudo contactar con GitHub ({type(exc).__name__})"
                ) from exc
        finally:
            self._cupo.release()
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = None
        if not isinstance(cuerpo, dict):
            raise _Rechazo(503, "github-no-disponible", f"GitHub respondió {r.status_code} sin JSON")
        error = cuerpo.get("error")
        if error == "bad_refresh_token":
            raise _Rechazo(
                401,
                "refresh-token-invalido",
                "GitHub no acepta el refresh token (venció, ya se usó o se revocó la App)",
            )
        if error is not None:
            # incorrect_client_credentials y similares: el despliegue está mal, no el token de la persona.
            log.error("GitHub rechazó la renovación con la GitHub App de Railspec (%s)", str(error)[:60])
            raise _Rechazo(503, "github-no-disponible", "GitHub rechazó la renovación")
        token = cuerpo.get("access_token")
        if not isinstance(token, str) or not token:
            raise _Rechazo(503, "github-no-disponible", "GitHub no entregó un access_token")
        nuevo = cuerpo.get("refresh_token")
        return {
            "access_token": token,
            "expires_in": _entero_positivo(cuerpo.get("expires_in")),
            "refresh_token": nuevo if isinstance(nuevo, str) and nuevo else None,
            "refresh_token_expires_in": _entero_positivo(cuerpo.get("refresh_token_expires_in")),
        }


def router_renovacion(renovador: RenovadorGithub) -> APIRouter:
    router = APIRouter(prefix="/v1/auth")

    def error(estado: int, codigo: str, detalle: str) -> JSONResponse:
        return JSONResponse({"codigo": codigo, "detalle": detalle}, status_code=estado)

    @router.post("/renovar")
    async def renovar(request: Request) -> JSONResponse:
        try:
            cuerpo = await request.json()
        except ValueError:
            return error(422, "entrada-invalida", "cuerpo JSON inválido")
        refresh = cuerpo.get("refresh_token") if isinstance(cuerpo, dict) else None
        client_id = cuerpo.get("client_id") if isinstance(cuerpo, dict) else None
        if not isinstance(refresh, str) or not 0 < len(refresh) <= TOPE_REFRESH_TOKEN:
            return error(422, "entrada-invalida", "falta refresh_token (texto de hasta 512 caracteres)")
        if client_id is not None and (not isinstance(client_id, str) or len(client_id) > 128):
            return error(422, "entrada-invalida", "client_id debe ser un texto de hasta 128 caracteres")
        try:
            salida = await asyncio.get_running_loop().run_in_executor(
                _HILOS, renovador.renovar, refresh, client_id
            )
        except _Rechazo as exc:
            return error(exc.estado, exc.codigo, exc.detalle)
        return JSONResponse(salida, headers={"Cache-Control": "no-store"})

    return router
