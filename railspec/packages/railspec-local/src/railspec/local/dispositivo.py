"""Device flow de GitHub contra la GitHub App de Railspec (``railspec login``).

Es un cliente público: solo usa el client id de la App, nunca su secret. El flujo:

1. ``POST https://github.com/login/device/code`` devuelve un código de usuario y un código de dispositivo.
2. La persona abre ``https://github.com/login/device`` e introduce el código de usuario.
3. Mientras tanto se consulta ``POST https://github.com/login/oauth/access_token`` cada ``interval``
   segundos hasta que GitHub entrega el token de usuario (``ghu_…``), o dice que venció o se denegó.

El token que sale es el que el servidor acepta en ``/mcp``: el de usuario de esa App, que él verifica con
``POST /applications/{client_id}/token``. La App tiene que tener activado «Enable Device Flow». Si además
emite tokens que vencen, GitHub entrega un refresh token con el que ``renovacion.py`` mantiene la sesión.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import httpx2

from .errores import LoginFallido, SinConexion

URL_GITHUB = "https://github.com"
URL_API = "https://api.github.com"
GRANT_DISPOSITIVO = "urn:ietf:params:oauth:grant-type:device_code"
INTERVALO_POR_DEFECTO_S = 5
#: Sobrecarga que GitHub pide sumar al intervalo cuando contesta ``slow_down`` sin decir uno nuevo.
SLOW_DOWN_S = 5

_CABECERAS = {"Accept": "application/json"}
_CABECERAS_API = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


@dataclass(frozen=True)
class CodigoDispositivo:
    device_code: str = field(repr=False)
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int


@dataclass(frozen=True)
class TokenUsuario:
    access_token: str = field(repr=False)
    #: Segundos de vida; ``None`` si la App emite tokens que no vencen.
    expires_in: int | None
    #: Solo con «Expire user authorization tokens»: el que permite renovar la sesión (``renovacion.py``).
    refresh_token: str | None = field(default=None, repr=False)
    refresh_expires_in: int | None = None


@dataclass(frozen=True)
class Persona:
    login: str
    github_id: int


def _segundos(valor: Any) -> int | None:
    return valor if isinstance(valor, int) and not isinstance(valor, bool) and valor > 0 else None


def _recortar(texto: Any, tope: int = 200) -> str:
    return str(texto).replace("\n", " ")[:tope]


class FlujoDispositivo:
    """``cliente``: un ``httpx2.Client`` (las pruebas inyectan un ``MockTransport``).

    ``dormir`` y ``reloj`` son inyectables para que las pruebas no esperen los 5 s del intervalo.
    ``client_id`` solo hace falta para pedir el código y esperar el token, no para ``comprobar``.
    """

    def __init__(
        self,
        client_id: str | None,
        cliente: httpx2.Client | None = None,
        *,
        dormir: Callable[[float], None] = time.sleep,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client_id = client_id
        self._cliente = cliente or httpx2.Client(timeout=httpx2.Timeout(15.0))
        self._dormir = dormir
        self._reloj = reloj

    def cerrar(self) -> None:
        self._cliente.close()

    def __enter__(self) -> FlujoDispositivo:
        return self

    def __exit__(self, *exc: object) -> None:
        self.cerrar()

    # --- pasos del flujo ------------------------------------------------------------------

    def solicitar_codigo(self) -> CodigoDispositivo:
        cuerpo = self._enviar("POST", f"{URL_GITHUB}/login/device/code", data={"client_id": self._id()})
        if "error" in cuerpo:
            raise LoginFallido(self._mensaje_error(cuerpo))
        try:
            return CodigoDispositivo(
                device_code=str(cuerpo["device_code"]),
                user_code=str(cuerpo["user_code"]),
                verification_uri=str(cuerpo["verification_uri"]),
                expires_in=int(cuerpo["expires_in"]),
                interval=int(cuerpo.get("interval") or INTERVALO_POR_DEFECTO_S),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise LoginFallido("GitHub respondió al pedir el código sin los campos del device flow.") from exc

    def esperar_token(self, codigo: CodigoDispositivo) -> TokenUsuario:
        """Consulta cada ``interval`` s hasta recibir el token; respeta ``slow_down`` y el vencimiento."""

        intervalo = codigo.interval
        limite = self._reloj() + codigo.expires_in
        while True:
            if self._reloj() >= limite:
                raise LoginFallido(self._mensaje_error({"error": "expired_token"}))
            self._dormir(intervalo)
            cuerpo = self._enviar(
                "POST",
                f"{URL_GITHUB}/login/oauth/access_token",
                data={
                    "client_id": self._id(),
                    "device_code": codigo.device_code,
                    "grant_type": GRANT_DISPOSITIVO,
                },
            )
            error = cuerpo.get("error")
            if error is None:
                return self._token(cuerpo)
            if error == "authorization_pending":
                continue
            if error == "slow_down":
                nuevo = cuerpo.get("interval")
                intervalo = nuevo if isinstance(nuevo, int) and nuevo > intervalo else intervalo + SLOW_DOWN_S
                continue
            raise LoginFallido(self._mensaje_error(cuerpo))

    def comprobar(self, access_token: str) -> Persona | None:
        """Dueño del token según ``GET /user``; ``None`` si GitHub lo rechaza (revocado o vencido).

        Lanza ``SinConexion`` si GitHub no responde: sin red no se puede afirmar que vale ni que no.
        """

        try:
            r = self._cliente.get(
                f"{URL_API}/user", headers={**_CABECERAS_API, "Authorization": f"Bearer {access_token}"}
            )
        except httpx2.TransportError as exc:
            raise SinConexion(f"No se pudo contactar con GitHub ({type(exc).__name__}).") from exc
        if r.status_code == 401:
            return None
        if r.status_code != 200:
            raise LoginFallido(f"GitHub respondió {r.status_code} al comprobar el token.")
        try:
            datos = r.json()
        except ValueError:
            datos = None
        if (
            isinstance(datos, dict)
            and isinstance(datos.get("login"), str)
            and isinstance(datos.get("id"), int)
        ):
            return Persona(login=datos["login"], github_id=datos["id"])
        raise LoginFallido("GitHub no devolvió el usuario al comprobar el token.")

    def persona(self, access_token: str) -> Persona | None:
        """Como ``comprobar`` pero sin lanzar: tras ``login`` no saber quién es no impide guardar el token."""

        try:
            return self.comprobar(access_token)
        except (SinConexion, LoginFallido):
            return None

    # --- interno --------------------------------------------------------------------------

    def _id(self) -> str:
        if not self._client_id:
            raise LoginFallido("Falta el client id de la GitHub App de Railspec.")
        return self._client_id

    def _enviar(self, metodo: str, url: str, *, data: dict[str, str]) -> dict[str, Any]:
        try:
            r = self._cliente.request(metodo, url, data=data, headers=_CABECERAS)
        except httpx2.TransportError as exc:
            raise SinConexion(
                f"No se pudo contactar con GitHub ({type(exc).__name__}): revisa tu red o tu proxy."
            ) from exc
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = None
        if not isinstance(cuerpo, dict):
            raise LoginFallido(f"GitHub respondió {r.status_code} sin un JSON del device flow.")
        return cuerpo

    @staticmethod
    def _token(cuerpo: dict[str, Any]) -> TokenUsuario:
        token = cuerpo.get("access_token")
        if not isinstance(token, str) or not token:
            raise LoginFallido("GitHub no entregó un access_token.")
        refresh = cuerpo.get("refresh_token")
        return TokenUsuario(
            access_token=token,
            expires_in=_segundos(cuerpo.get("expires_in")),
            refresh_token=refresh if isinstance(refresh, str) and refresh else None,
            refresh_expires_in=_segundos(cuerpo.get("refresh_token_expires_in")),
        )

    def _mensaje_error(self, cuerpo: dict[str, Any]) -> str:
        error = str(cuerpo.get("error"))
        if error == "expired_token":
            return "El código venció sin que lo autorizaras. Vuelve a ejecutar `railspec login`."
        if error == "access_denied":
            return "Cancelaste la autorización en GitHub: no se inició sesión."
        if error == "device_flow_disabled":
            return (
                "La GitHub App de Railspec no tiene activado el device flow. Quien la administra debe marcar "
                "«Enable Device Flow» en Settings > Developer settings > GitHub Apps > (la App)."
            )
        if error in ("incorrect_client_credentials", "Not Found"):
            return (
                f"GitHub no reconoce el client id {self._client_id!r}. Usa el de la GitHub App de Railspec "
                "(Settings > Developer settings > GitHub Apps > Client ID; empieza por «Iv»)."
            )
        detalle = cuerpo.get("error_description")
        return f"GitHub rechazó el device flow ({_recortar(error)})" + (
            f": {_recortar(detalle)}" if detalle else "."
        )
