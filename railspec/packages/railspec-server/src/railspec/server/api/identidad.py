"""Identidad del actor a partir del token (R2): nunca viaja en la entrada de la tool.

- ``IdentidadGithub``: token de usuario de GitHub (OAuth de la GitHub App).
  Resuelve ``github_id`` y login con ``GET /user`` y cachea por hash del token.
- ``IdentidadDesarrollo``: tokens fijos por variable de entorno, solo para
  desarrollo sin GitHub App.

La identidad OIDC de GitHub Actions (actor de servicio) queda pendiente: exige
verificar el JWT contra las claves de GitHub y la necesita ``graph.index``.
"""

from __future__ import annotations

import hashlib
import time
from datetime import datetime
from typing import Any

from railspec.contracts.comun import Actor, AlcanceWorkspace, Canal, TipoActor


class TokenInvalido(Exception):
    pass


class IdentidadDesarrollo:
    nombre = "desarrollo"

    def __init__(self, tokens: dict[str, tuple[str, int]]) -> None:
        self._tokens = tokens

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        if token not in self._tokens:
            raise TokenInvalido("token desconocido")
        login, github_id = self._tokens[token]
        return Actor(tipo=TipoActor.humano, canal=Canal(canal), github_id=github_id, login=login)

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return []

    def expira(self, token: str) -> datetime | None:
        return None


class IdentidadGithub:
    nombre = "github"
    API = "https://api.github.com/user"

    def __init__(self, cliente: Any | None = None, ttl_s: int = 300) -> None:
        self._cliente = cliente
        self._ttl = ttl_s
        self._cache: dict[str, tuple[float, str, int]] = {}

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        clave = hashlib.sha256(token.encode()).hexdigest()
        ahora = time.monotonic()
        cacheado = self._cache.get(clave)
        if cacheado is None or cacheado[0] < ahora:
            import httpx

            cliente = self._cliente or httpx.Client(timeout=10)
            r = cliente.get(
                self.API,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            )
            if r.status_code != 200:
                raise TokenInvalido(f"GitHub rechazó el token ({r.status_code})")
            datos = r.json()
            cacheado = (ahora + self._ttl, datos["login"], int(datos["id"]))
            self._cache[clave] = cacheado
        _, login, github_id = cacheado
        return Actor(tipo=TipoActor.humano, canal=Canal(canal), github_id=github_id, login=login)

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return []

    def expira(self, token: str) -> datetime | None:
        return None


def token_de_cabecera(valor: str | None) -> str | None:
    if not valor:
        return None
    esquema, _, token = valor.partition(" ")
    return token.strip() if esquema.lower() == "bearer" and token.strip() else None
