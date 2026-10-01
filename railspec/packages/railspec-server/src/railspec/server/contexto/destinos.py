"""Destinos permitidos de los proveedores de contexto que configura una organización.

Un ``ProveedorContexto`` guardado en Mongo lo edita la organización, así que su
``url`` recibe la consulta del gate (hasta 500 caracteres del objeto) y la
credencial en ``X-API-Key``. Sin control, sería una salida hacia cualquier
host (y SSRF ciego contra servicios internos). Controles, en este orden:

1. **Allowlist de hosts** de la plataforma, ``RAILSPEC_PROVEEDORES_HOSTS``
   (nombres separados por coma o espacio; ``*.dominio`` admite subdominios, no el
   apex). Se suma el host de ``RAILSPEC_PCE_URL``, el proveedor conocido. Sin
   ninguno, ninguna organización puede configurar proveedores (falla cerrado).
2. **Solo ``https``**, sin credenciales en la URL ni fragmento, con host DNS o IP
   pública literal.
3. **IP pública**: se comprueba la IP *resuelta* al conectar (no solo el nombre) y
   se conecta a esa IP ya comprobada, así que un DNS que cambia entre la
   validación y la conexión no sirve. Quedan fuera loopback, privadas,
   link-local (incluido el metadata de la nube), CGNAT, multicast y las IPv6 que
   envuelven una IPv4 no pública (mapeada, 6to4, Teredo, NAT64).
4. **Sin redirecciones**: el cliente no las sigue (el SDK de MCP solo sigue las
   del mismo origen).

La fuente por defecto del entorno (``RAILSPEC_PCE_URL``) la fija la plataforma y
no pasa por este control.
"""

from __future__ import annotations

import asyncio
import ipaddress
import os
import re
import socket
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

_ETIQUETA = r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?"
_HOST = re.compile(rf"{_ETIQUETA}(?:\.{_ETIQUETA})*")
_ENTRADA = re.compile(rf"(?:\*\.)?{_ETIQUETA}(?:\.{_ETIQUETA})*")
_IMPRIMIBLE = re.compile(r"[\x21-\x7e]+")

Resolutor = Callable[[str, int], Awaitable[list[str]]]


class DestinoNoPermitido(ValueError):
    pass


def _ip(texto: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(texto.split("%", 1)[0])
    except ValueError:
        return None


def ip_publica(valor: str | ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True solo para una IP global y no multicast; las IPv6 que envuelven una IPv4 valen lo que ella."""

    ip = _ip(valor) if isinstance(valor, str) else valor
    if ip is None:
        return False
    if isinstance(ip, ipaddress.IPv6Address):
        envueltas = [ip.ipv4_mapped, ip.sixtofour]
        if ip.teredo:
            envueltas += list(ip.teredo)
        if ip in ipaddress.ip_network("64:ff9b::/96"):  # NAT64: la IPv4 son los 32 bits bajos
            envueltas.append(ipaddress.IPv4Address(int(ip) & 0xFFFFFFFF))
        if any(v4 is not None and not ip_publica(v4) for v4 in envueltas):
            return False
    return ip.is_global and not ip.is_multicast


async def _getaddrinfo(host: str, puerto: int) -> list[str]:
    infos = await asyncio.get_running_loop().getaddrinfo(host, puerto, type=socket.SOCK_STREAM)
    return [str(info[4][0]) for info in infos]


@dataclass(frozen=True)
class PoliticaDestinos:
    hosts: frozenset[str] = frozenset()
    #: Resolución de nombres (las pruebas inyectan una); por defecto, ``getaddrinfo`` del sistema.
    resolver: Resolutor | None = field(default=None, compare=False, repr=False)

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> PoliticaDestinos:
        env = os.environ if entorno is None else entorno
        hosts: set[str] = set()
        for crudo in re.split(r"[,\s]+", env.get("RAILSPEC_PROVEEDORES_HOSTS", "")):
            host = crudo.lower()
            if not host:
                continue
            if not _ENTRADA.fullmatch(host) or (host.startswith("*.") and "." not in host[2:]):
                raise ValueError(
                    f"RAILSPEC_PROVEEDORES_HOSTS: {crudo!r} no es un host (use nombre.dominio o *.dominio)"
                )
            hosts.add(host)
        pce = (env.get("RAILSPEC_PCE_URL") or "").strip()
        if pce and (host_pce := urlsplit(pce).hostname):
            hosts.add(host_pce.rstrip(".").lower())
        return cls(hosts=frozenset(hosts))

    # --- validación de la URL ----------------------------------------------------------------

    def host_permitido(self, host: str) -> bool:
        return host in self.hosts or any(h.startswith("*.") and host.endswith(h[1:]) for h in self.hosts)

    def validar_url(self, url: str) -> tuple[str, int]:
        """``(host, puerto)`` si la URL es un destino permitido; ``DestinoNoPermitido`` si no."""

        if not isinstance(url, str) or _IMPRIMIBLE.fullmatch(url) is None or "\\" in url:
            raise DestinoNoPermitido("la URL tiene caracteres no permitidos")
        try:
            partes = urlsplit(url)
            puerto = 443 if partes.port is None else partes.port
        except ValueError as exc:
            raise DestinoNoPermitido("la URL no es válida") from exc
        if not 0 < puerto < 65536:
            raise DestinoNoPermitido("el puerto de la URL no es válido")
        if partes.scheme != "https":
            raise DestinoNoPermitido("la URL debe ser https")
        if "@" in partes.netloc or "#" in url:
            raise DestinoNoPermitido("la URL no puede llevar credenciales ni fragmento")
        host = (partes.hostname or "").rstrip(".").lower()
        if not host:
            raise DestinoNoPermitido("la URL no tiene host")
        ip = _ip(host)
        if ip is not None:
            if not ip_publica(ip):
                raise DestinoNoPermitido("la URL apunta a una IP no pública")
        elif not _HOST.fullmatch(host):
            raise DestinoNoPermitido("el host de la URL no es un nombre DNS válido")
        if not self.host_permitido(host):
            raise DestinoNoPermitido(
                f"el host {host} no está permitido por la plataforma (RAILSPEC_PROVEEDORES_HOSTS)"
            )
        return host, puerto

    # --- conexión ------------------------------------------------------------------------------

    async def resolver_publica(self, host: str, puerto: int) -> list[str]:
        """IPs de ``host``; todas tienen que ser públicas (un nombre con una sola interna se rechaza)."""

        if _ip(host) is not None:
            ips = [host]
        else:
            ips = await (self.resolver or _getaddrinfo)(host, puerto)
        if not ips:
            raise DestinoNoPermitido(f"{host} no resuelve a ninguna IP")
        if not all(ip_publica(ip) for ip in ips):
            raise DestinoNoPermitido(f"{host} resuelve a una IP no pública")
        return ips

    def cliente_http(
        self,
        headers: Mapping[str, str] | None = None,
        *,
        timeout_s: float = 30.0,
        transporte: Any | None = None,
    ) -> Any:
        """``httpx2.AsyncClient`` (el de MCP 2.x) que solo conecta a IPs públicas y no sigue redirecciones."""

        import httpx2

        return httpx2.AsyncClient(
            headers=dict(headers or {}),
            timeout=httpx2.Timeout(timeout_s),
            follow_redirects=False,
            transport=transporte or transporte_verificado(self),
            trust_env=False,
        )


def backend_verificado(politica: PoliticaDestinos, base: Any | None = None) -> Any:
    """Backend de red de httpcore2 que resuelve, comprueba y conecta a la IP comprobada."""

    import httpcore2

    class BackendVerificado(httpcore2.AsyncNetworkBackend):
        def __init__(self) -> None:
            self._base = base or httpcore2.AnyIOBackend()

        async def connect_tcp(
            self,
            host: str,
            port: int,
            timeout: float | None = None,
            local_address: str | None = None,
            socket_options: Any = None,
        ) -> Any:
            try:
                ips = await politica.resolver_publica(host, port)
            except DestinoNoPermitido as exc:
                raise httpcore2.ConnectError(str(exc)) from exc
            ultimo: Exception | None = None
            for ip in ips:  # la IP ya comprobada, no el nombre: no hay segunda resolución que falsear
                try:
                    return await self._base.connect_tcp(
                        ip, port, timeout=timeout, local_address=local_address, socket_options=socket_options
                    )
                except httpcore2.ConnectError as exc:
                    ultimo = exc
            raise ultimo or httpcore2.ConnectError("sin IP a la que conectar")

        async def connect_unix_socket(
            self, path: str, timeout: float | None = None, socket_options: Any = None
        ):
            raise httpcore2.ConnectError("los proveedores de contexto no usan sockets unix")

        async def sleep(self, seconds: float) -> None:
            await self._base.sleep(seconds)

    return BackendVerificado()


def transporte_verificado(politica: PoliticaDestinos) -> Any:
    """Transporte de httpx2 sin proxy cuyo backend de red verifica la IP de cada conexión."""

    import httpcore2
    import httpx2

    class TransporteVerificado(httpx2.AsyncHTTPTransport):
        def __init__(self) -> None:
            super().__init__()
            # Mismo pool que arma httpx2, con nuestro backend de red (API pública de httpcore2).
            self._pool = httpcore2.AsyncConnectionPool(
                ssl_context=httpx2.create_ssl_context(),
                max_connections=20,
                max_keepalive_connections=5,
                keepalive_expiry=5.0,
                network_backend=backend_verificado(politica),
            )

    return TransporteVerificado()
