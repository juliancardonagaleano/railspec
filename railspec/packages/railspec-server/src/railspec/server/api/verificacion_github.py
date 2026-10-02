"""Verificación de tokens de usuario de GitHub contra la GitHub App de Railspec.

Dos problemas con un ``GET /user`` a secas:

- **Suplantación (M3).** Cualquier OAuth app de terceros a la que la persona dio acceso (aunque sin
  scopes) obtiene un token con el que ``GET /user`` responde 200: ese token valdría como Bearer en
  /v1, MCP y /consola/api. Con las credenciales de la GitHub App se llama a
  ``POST /applications/{client_id}/token`` (HTTP Basic ``client_id:client_secret``, cuerpo
  ``{"access_token": ...}``): GitHub responde 200 solo si el token lo emitió esa App y el cuerpo trae
  la app y la persona. Todo lo demás (otra app, 404, sin persona) se rechaza, y si no se puede
  comprobar (red, límite de tasa, 5xx, credenciales de la App rechazadas) también: falla cerrado.
- **Denegación de servicio (M5).** Cada token distinto cuesta una llamada de hasta unos segundos a
  GitHub. Aquí las cachés tienen tope (LRU con TTL), los rechazos definitivos se recuerdan un rato
  corto y las llamadas simultáneas a GitHub están acotadas; quien no consigue turno a tiempo se
  rechaza sin esperar.

Sin credenciales de la App (``app=None``) solo queda ``GET /user``, que no prueba a qué app pertenece
el token: es el modo desarrollo explícito (``RAILSPEC_PERMITIR_DESARROLLO=1``), nunca producción.

Es síncrono a propósito (``httpx.Client``): las rutas async lo ejecutan en un hilo
(``api.superficies._actor``).
"""

from __future__ import annotations

import hashlib
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, Generic, TypeVar

log = logging.getLogger("railspec.identidad")

API = "https://api.github.com"
CABECERAS = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}

V = TypeVar("V")


class TokenRechazado(Exception):
    """El token no sirve: otra app, revocado, caducado o sin persona."""


class GithubNoDisponible(Exception):
    """No se pudo comprobar el token (red, límite de tasa, 5xx, credenciales de la App, saturación)."""


class CacheAcotada(Generic[V]):
    """Caché con TTL por entrada y tope de tamaño: al llenarse purga lo caducado y expulsa lo menos usado."""

    def __init__(self, ttl_s: float, max_entradas: int, reloj: Callable[[], float] = time.monotonic) -> None:
        if max_entradas < 1:
            raise ValueError("max_entradas debe ser al menos 1")
        self._ttl = ttl_s
        self._max = max_entradas
        self._reloj = reloj
        self._datos: OrderedDict[str, tuple[float, V]] = OrderedDict()
        self._bloqueo = threading.Lock()

    def obtener(self, clave: str) -> V | None:
        with self._bloqueo:
            entrada = self._datos.get(clave)
            if entrada is None:
                return None
            if entrada[0] <= self._reloj():
                del self._datos[clave]
                return None
            self._datos.move_to_end(clave)
            return entrada[1]

    def poner(self, clave: str, valor: V, ttl_s: float | None = None) -> None:
        with self._bloqueo:
            ahora = self._reloj()
            self._datos[clave] = (ahora + (self._ttl if ttl_s is None else ttl_s), valor)
            self._datos.move_to_end(clave)
            if len(self._datos) > self._max:
                for vieja in [k for k, (vence, _) in self._datos.items() if vence <= ahora]:
                    del self._datos[vieja]
                while len(self._datos) > self._max:
                    self._datos.popitem(last=False)

    def __len__(self) -> int:
        with self._bloqueo:
            return len(self._datos)


class VerificadorTokenGithub:
    """Resuelve ``token -> (login, github_id)`` con las cachés y los límites descritos arriba.

    ``cliente``: un ``httpx.Client`` (las pruebas inyectan un doble); sin él se crea uno compartido
    con timeout corto. ``app``: ``(client_id, client_secret)`` de la GitHub App, o ``None`` (desarrollo).
    """

    def __init__(
        self,
        cliente: Any | None = None,
        *,
        app: tuple[str, str] | None,
        ttl_s: float = 300,
        ttl_negativo_s: float = 30,
        max_entradas: int = 10_000,
        max_concurrentes: int = 8,
        espera_s: float = 2.0,
        timeout_s: float = 5.0,
        reloj: Callable[[], float] = time.monotonic,
    ) -> None:
        self._app = app
        self._cliente = cliente
        self._timeout_s = timeout_s
        self._max_concurrentes = max_concurrentes
        self._espera_s = espera_s
        self._ttl = ttl_s
        self._positivo: CacheAcotada[tuple[str, int]] = CacheAcotada(ttl_s, max_entradas, reloj)
        self._negativo: CacheAcotada[str] = CacheAcotada(ttl_negativo_s, max_entradas, reloj)
        self._equipos: CacheAcotada[frozenset[int]] = CacheAcotada(ttl_s, max_entradas, reloj)
        self._cupo = threading.BoundedSemaphore(max_concurrentes)
        self._bloqueo_cliente = threading.Lock()

    def tamanos(self) -> tuple[int, int]:
        return len(self._positivo), len(self._negativo)

    def equipos(self, token: str) -> frozenset[int]:
        """``equipo_id`` de GitHub del usuario del token (roles por equipo), con caché acotada.

        Solo se llama con un token ya verificado. Falla cerrado: sin turno de concurrencia, sin permiso
        o ante cualquier error no hay equipos, y la sesión sigue valiendo con las asignaciones personales.
        Solo se recuerda una respuesta definitiva (equipos leídos, o GitHub dice que este token no puede
        verlos): un fallo pasajero (red, límite de tasa, 5xx) no se recuerda, para reintentar en la
        próxima llamada en vez de dejar sin sus roles de equipo a quien trabaja desde el arnés durante
        todo el TTL. Lo que se devuelva en ese caso nunca es más que lo que GitHub confirmó.
        """

        clave = hashlib.sha256(token.encode()).hexdigest()
        conocido = self._equipos.obtener(clave)
        if conocido is not None:
            return conocido
        if not self._cupo.acquire(timeout=self._espera_s):
            return frozenset()
        try:
            equipos, definitivo = leer_equipos(
                self._http(), {**CABECERAS, "Authorization": f"Bearer {token}"}
            )
        except Exception as exc:
            log.warning(
                "no se pudieron leer los equipos de GitHub (%s); sin roles por equipo", type(exc).__name__
            )
            return frozenset()
        finally:
            self._cupo.release()
        if not definitivo:
            log.warning("GitHub no respondió bien al leer los equipos; se reintenta en la próxima llamada")
            return equipos
        self._equipos.poner(clave, equipos)
        return equipos

    def verificar(self, token: str) -> tuple[str, int]:
        clave = hashlib.sha256(token.encode()).hexdigest()
        conocido = self._positivo.obtener(clave)
        if conocido is not None:
            return conocido
        rechazo = self._negativo.obtener(clave)
        if rechazo is not None:
            raise TokenRechazado(rechazo)
        if not self._cupo.acquire(timeout=self._espera_s):
            raise GithubNoDisponible(
                "demasiadas comprobaciones de token en curso: reintenta en unos segundos"
            )
        try:
            try:
                login, github_id, vigencia_s = self._consultar(token)
            except TokenRechazado as exc:
                # Solo los rechazos definitivos: un fallo de red no es culpa del token.
                self._negativo.poner(clave, str(exc))
                raise
        finally:
            self._cupo.release()
        self._positivo.poner(clave, (login, github_id), ttl_s=min(self._ttl, vigencia_s or self._ttl))
        return login, github_id

    # --- GitHub -----------------------------------------------------------------------

    def _http(self) -> Any:
        with self._bloqueo_cliente:
            if self._cliente is None:
                import httpx

                self._cliente = httpx.Client(
                    timeout=self._timeout_s,
                    limits=httpx.Limits(max_connections=self._max_concurrentes * 2),
                )
            return self._cliente

    def _consultar(self, token: str) -> tuple[str, int, float | None]:
        try:
            if self._app is None:
                respuesta = self._http().get(
                    f"{API}/user", headers={**CABECERAS, "Authorization": f"Bearer {token}"}
                )
            else:
                client_id, client_secret = self._app
                respuesta = self._http().post(
                    f"{API}/applications/{client_id}/token",
                    auth=(client_id, client_secret),
                    json={"access_token": token},
                    headers=CABECERAS,
                )
        except Exception as exc:  # red, timeout, TLS…
            raise GithubNoDisponible(
                f"no se pudo comprobar el token con GitHub: {type(exc).__name__}"
            ) from exc
        estado = respuesta.status_code
        if self._app is None:
            if estado == 401:
                raise TokenRechazado("GitHub rechazó el token (401)")
        elif estado in (404, 422):
            raise TokenRechazado(f"GitHub no reconoce el token para la GitHub App de Railspec ({estado})")
        elif estado == 401:
            # Las credenciales de la App no valen: error de despliegue, no del token.
            log.error("GitHub rechazó las credenciales de la GitHub App de Railspec (RAILSPEC_GITHUB_APP_*)")
            raise GithubNoDisponible("GitHub rechazó las credenciales de la GitHub App de Railspec")
        if estado != 200:
            raise GithubNoDisponible(f"GitHub respondió {estado} al comprobar el token")
        try:
            datos = respuesta.json()
        except ValueError as exc:
            raise GithubNoDisponible("respuesta ilegible de GitHub al comprobar el token") from exc
        if not isinstance(datos, dict):
            raise GithubNoDisponible("respuesta inesperada de GitHub al comprobar el token")
        if self._app is None:
            persona, vigencia_s = datos, None
        else:
            app = datos.get("app")
            if not isinstance(app, dict) or str(app.get("client_id")) != self._app[0]:
                raise TokenRechazado("el token no pertenece a la GitHub App de Railspec")
            persona = datos.get("user")
            vigencia_s = _vigencia_s(datos.get("expires_at"))
        if (
            not isinstance(persona, dict)
            or not isinstance(persona.get("login"), str)
            or not isinstance(persona.get("id"), int)
        ):
            raise TokenRechazado("el token no corresponde a una persona (GitHub no devolvió el usuario)")
        return persona["login"], persona["id"], vigencia_s


URL_EQUIPOS = f"{API}/user/teams?per_page=100"
PAGINAS_EQUIPOS = 10


def equipos_de_usuario(cliente: Any, cabeceras: dict[str, str]) -> frozenset[int]:
    """``equipo_id`` de los equipos del usuario del token (``GET /user/teams``, con paginación).

    Necesita el permiso de la App (Members, lectura) o el alcance ``read:org``; sin él, o ante una
    respuesta que no es la esperada, devuelve los que haya leído hasta ahí (ninguno si falla la primera
    página). Solo sigue enlaces ``next`` que apunten a la API de GitHub: las cabeceras llevan el token.
    """

    return leer_equipos(cliente, cabeceras)[0]


def _pasajero(r: Any) -> bool:
    """¿Un fallo que se arregla solo (límite de tasa, 5xx) y no una respuesta definitiva sobre el token?"""

    estado = r.status_code
    if estado == 429 or estado >= 500:
        return True
    cabeceras = getattr(r, "headers", {})
    # GitHub responde 403 también al límite de tasa primario y al secundario.
    return estado == 403 and ("retry-after" in cabeceras or cabeceras.get("x-ratelimit-remaining") == "0")


def leer_equipos(cliente: Any, cabeceras: dict[str, str]) -> tuple[frozenset[int], bool]:
    """Como ``equipos_de_usuario``, y si la respuesta es definitiva.

    ``False`` cuando GitHub falló de forma pasajera (429, 5xx, límite de tasa) antes de terminar: los equipos
    devueltos son los leídos hasta ahí (un subconjunto de los reales, nunca de más) y quien llama no debe
    recordarlos. Sin permiso (401, 403 sin señales de límite, 404), una respuesta que no es una lista o una
    paginación completa son definitivas.
    """

    equipos: set[int] = set()
    url: str | None = URL_EQUIPOS
    for _ in range(PAGINAS_EQUIPOS):
        if url is None or not url.startswith(f"{API}/"):
            break
        r = cliente.get(url, headers=cabeceras)
        if _pasajero(r):
            return frozenset(equipos), False
        cuerpo = r.json() if r.status_code == 200 else None
        if not isinstance(cuerpo, list):
            break
        equipos.update(int(e["id"]) for e in cuerpo)
        url = r.links.get("next", {}).get("url") if hasattr(r, "links") else None
    return frozenset(equipos), True


def _vigencia_s(expira: Any) -> float | None:
    """Segundos hasta ``expires_at`` (ISO 8601) o None si no caduca; un token caducado se rechaza."""

    if not isinstance(expira, str):
        return None
    try:
        restante = (datetime.fromisoformat(expira.replace("Z", "+00:00")) - datetime.now(UTC)).total_seconds()
    except (ValueError, TypeError):
        return None
    if restante <= 0:
        raise TokenRechazado("el token de GitHub caducó")
    return restante
