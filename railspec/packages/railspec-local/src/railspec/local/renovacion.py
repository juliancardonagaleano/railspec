"""Renovación de la sesión de ``railspec login`` con el refresh token.

Con «Expire user authorization tokens» activada en la GitHub App el token de usuario dura 8 horas y GitHub
entrega, junto a él, un refresh token (6 meses) con el que se pide otro. Esa petición lleva el client secret
de la App, que no puede estar en las máquinas de los desarrolladores: la hace ``railspec-server`` en
``POST {servidor}/v1/auth/renovar`` (``railspec.server.api.renovacion``) y aquí solo se llama a ese endpoint.

GitHub **rota** el refresh token en cada uso: el viejo deja de valer. Por eso la renovación corre dentro del
candado del archivo de credenciales (``AlmacenCredenciales.bloqueo``) y relee la sesión dentro de él: si otro
proceso (otro arnés abierto) ya la renovó, se usa esa y no se canjea nada.

Un fallo es *pasajero* (sin red, servidor caído o sin el endpoint: se reintenta más tarde, la sesión no se
toca) o *definitivo* (GitHub no acepta el refresh token: se descarta de la sesión guardada para no volver a
probarlo y el mensaje manda a ``railspec login``).
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import datetime, timedelta
from urllib.parse import urlsplit, urlunsplit

import httpx2

from .credenciales import AlmacenCredenciales, Credencial, FuenteToken, ahora_utc
from .errores import RenovacionFallida

RUTA_RENOVAR = "/v1/auth/renovar"
RUTA_CONFIG = "/v1/auth/config"
_CLIENT_ID = re.compile(r"[A-Za-z0-9_.-]{1,128}")
_LOCALES = frozenset({"localhost", "127.0.0.1", "::1"})
_VOLVER_A_INICIAR = "Ejecuta `railspec login` otra vez."


def url_base(url: str) -> str:
    """El servidor sin el ``/mcp`` de ``RAILSPEC_URL`` (que es el endpoint MCP)."""

    partes = urlsplit(url.strip())
    ruta = partes.path.rstrip("/")
    if ruta.endswith("/mcp"):
        ruta = ruta[: -len("/mcp")]
    return urlunsplit((partes.scheme, partes.netloc, ruta, "", ""))


def descubrir_client_id(
    url: str, cliente: httpx2.Client | None = None, *, timeout_s: float = 60.0
) -> str | None:
    """El client id público de la GitHub App que publica el servidor (``GET /v1/auth/config``).

    Evita que cada persona tenga que conseguirlo: ``railspec login`` solo necesita ``RAILSPEC_URL``. Devuelve
    ``None`` si el servidor no lo ofrece (versión anterior, sin App), no responde o contesta algo raro: el
    llamador cae entonces en ``--client-id``/``RAILSPEC_GITHUB_CLIENT_ID``. Nunca lanza. El plazo es largo
    porque el plan gratuito de Render duerme el servicio y la primera petición lo despierta.
    """

    base = url_base(url)
    destino = urlsplit(base)
    if destino.scheme != "https" and destino.hostname not in _LOCALES:
        return None  # un canal sin cifrar podría cambiar la App a la que se autoriza
    propio = cliente is None
    cliente = cliente or httpx2.Client(timeout=httpx2.Timeout(timeout_s))
    try:
        r = cliente.get(base + RUTA_CONFIG, headers={"Accept": "application/json"})
        cuerpo = r.json() if r.status_code == 200 else None
    except (httpx2.TransportError, ValueError):
        return None
    finally:
        if propio:
            cliente.close()
    valor = cuerpo.get("github_client_id") if isinstance(cuerpo, dict) else None
    return valor if isinstance(valor, str) and _CLIENT_ID.fullmatch(valor) else None


class RenovadorServidor:
    """``cliente``: un ``httpx2.Client`` (las pruebas inyectan un ``MockTransport``)."""

    def __init__(
        self, almacen: AlmacenCredenciales, cliente: httpx2.Client | None = None, *, timeout_s: float = 15.0
    ) -> None:
        self._almacen = almacen
        self._cliente = cliente
        self._timeout_s = timeout_s

    def renovar(self, url: str, ahora: datetime) -> Credencial:
        with self._almacen.bloqueo():
            actual = self._almacen.leer(url)
            if actual is None:
                raise RenovacionFallida(
                    f"La sesión se cerró mientras se renovaba (¿`railspec logout`?). {_VOLVER_A_INICIAR}",
                    definitiva=True,
                )
            if not actual.vencida(ahora):
                return actual  # otro proceso la renovó mientras esperábamos el candado
            if not actual.renovable(ahora):
                self._descartar(url, actual)
                raise RenovacionFallida(
                    f"su refresh token ya no sirve (vence a los 6 meses). {_VOLVER_A_INICIAR}",
                    definitiva=True,
                )
            nueva = self._canjear(url, actual, ahora)
            self._almacen.guardar(url, nueva)
            return nueva

    # --- interno --------------------------------------------------------------------------

    def _descartar(self, url: str, actual: Credencial) -> None:
        """Quita el refresh token inservible: así nadie lo vuelve a canjear en cada petición."""

        self._almacen.guardar(
            url, actual.model_copy(update={"refresh_token": None, "refresh_expira_en": None})
        )

    def _canjear(self, url: str, actual: Credencial, ahora: datetime) -> Credencial:
        base = url_base(url)
        destino = urlsplit(base)
        if destino.scheme != "https" and destino.hostname not in _LOCALES:
            raise RenovacionFallida(
                f"{url} no es https: no se envía el refresh token por un canal sin cifrar. "
                f"{_VOLVER_A_INICIAR}"
            )
        assert actual.refresh_token is not None
        cliente = self._cliente or httpx2.Client(timeout=httpx2.Timeout(self._timeout_s))
        try:
            r = cliente.post(
                base + RUTA_RENOVAR,
                json={"refresh_token": actual.refresh_token, "client_id": actual.client_id},
                headers={"Accept": "application/json"},
            )
        except httpx2.TransportError as exc:
            raise RenovacionFallida(
                f"no se pudo contactar con {base} para renovarla ({type(exc).__name__}); se reintenta sola."
            ) from exc
        finally:
            if self._cliente is None:
                cliente.close()
        try:
            cuerpo = r.json()
        except ValueError:
            cuerpo = None
        if not isinstance(cuerpo, dict):
            cuerpo = {}
        if r.status_code == 200:
            return self._credencial(actual, cuerpo, ahora)
        codigo, detalle = cuerpo.get("codigo"), str(cuerpo.get("detalle") or "")[:200]
        if r.status_code == 401 and codigo == "refresh-token-invalido":
            self._descartar(url, actual)
            raise RenovacionFallida(
                f"{detalle or 'GitHub no acepta su refresh token'}. {_VOLVER_A_INICIAR}", definitiva=True
            )
        if r.status_code == 400 and codigo == "client-id-incorrecto":
            self._descartar(url, actual)
            raise RenovacionFallida(f"{detalle}.", definitiva=True)
        if r.status_code in (404, 405) or codigo == "renovacion-no-disponible":
            raise RenovacionFallida(
                f"{base} no ofrece la renovación de sesión (¿servidor sin actualizar o sin la GitHub App?). "
                f"{_VOLVER_A_INICIAR}"
            )
        raise RenovacionFallida(
            f"{base} respondió {r.status_code}{': ' + detalle if detalle else ''}; se reintenta sola."
        )

    @staticmethod
    def _credencial(actual: Credencial, cuerpo: dict, ahora: datetime) -> Credencial:
        token = cuerpo.get("access_token")
        if not isinstance(token, str) or not token:
            raise RenovacionFallida("el servidor respondió sin un access_token; se reintenta sola.")
        refresh = cuerpo.get("refresh_token")
        vida, vida_refresh = cuerpo.get("expires_in"), cuerpo.get("refresh_token_expires_in")
        return actual.model_copy(
            update={
                "access_token": token,
                "obtenido_en": ahora,
                "expira_en": ahora + timedelta(seconds=vida) if isinstance(vida, int) and vida > 0 else None,
                "refresh_token": refresh if isinstance(refresh, str) and refresh else None,
                "refresh_expira_en": ahora + timedelta(seconds=vida_refresh)
                if isinstance(refresh, str) and refresh and isinstance(vida_refresh, int) and vida_refresh > 0
                else None,
            }
        )


def fuente_con_renovacion(
    url: str | None,
    token_entorno: str | None,
    almacen: AlmacenCredenciales,
    ahora: Callable[[], datetime] = ahora_utc,
) -> FuenteToken:
    """La ``FuenteToken`` de producción: con la sesión vencida y un refresh token vigente, la renueva."""

    return FuenteToken(url, token_entorno, almacen, ahora, renovador=RenovadorServidor(almacen))
