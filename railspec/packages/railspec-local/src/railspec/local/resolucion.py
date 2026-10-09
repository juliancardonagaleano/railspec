"""Averiguar con el servidor a qué vínculo pertenece un clon: ``GET /v1/repositorios/resolver``.

``railspec instalar`` sin parámetros lo usa para no pedir organización, workspace ni slug a quien clona un
repositorio ya vinculado (``railspec.server.api.resolucion``). La petición lleva el token de la persona: el
servidor solo contesta con vínculos de workspaces donde ella tiene rol.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlsplit

import httpx2

from .errores import ConfigInvalida, SinConexion
from .renovacion import _LOCALES, url_base

RUTA_RESOLVER = "/v1/repositorios/resolver"
_INICIAR = "Inicia sesión con `railspec login`."


def resolver_repositorio(
    url_servidor: str,
    token: str | None,
    url_repositorio: str,
    cliente: httpx2.Client | None = None,
    *,
    timeout_s: float = 60.0,
) -> list[dict[str, Any]]:
    """Los vínculos de ``url_repositorio`` (``https://github.com/<owner>/<repo>``) que la persona ve."""

    base = url_base(url_servidor)
    destino = urlsplit(base)
    if destino.scheme != "https" and destino.hostname not in _LOCALES:
        raise ConfigInvalida(f"{url_servidor} no es https: no se envía el token por un canal sin cifrar.")
    if not token:
        raise ConfigInvalida(f"No hay sesión para {base}. {_INICIAR}")
    propio = cliente is None
    cliente = cliente or httpx2.Client(timeout=httpx2.Timeout(timeout_s))
    try:
        r = cliente.get(
            base + RUTA_RESOLVER,
            params={"url": url_repositorio},
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
        )
    except httpx2.TransportError as exc:
        raise SinConexion(f"No se pudo contactar con {base} ({type(exc).__name__}); reintenta.") from exc
    finally:
        if propio:
            cliente.close()
    try:
        cuerpo = r.json()
    except ValueError:
        cuerpo = None
    if not isinstance(cuerpo, dict):
        cuerpo = {}
    if r.status_code == 200 and isinstance(cuerpo.get("coincidencias"), list):
        return [c for c in cuerpo["coincidencias"] if isinstance(c, dict)]
    detalle = str(cuerpo.get("detalle") or "")[:200]
    if r.status_code == 401:
        raise ConfigInvalida(f"{base} no acepta tu sesión{': ' + detalle if detalle else ''}. {_INICIAR}")
    if r.status_code in (404, 405):
        raise ConfigInvalida(
            f"{base} no sabe resolver repositorios (¿servidor sin actualizar?). "
            "Pasa --org, --workspace y --repositorio."
        )
    raise ConfigInvalida(f"{base} respondió {r.status_code}{': ' + detalle if detalle else ''}.")
