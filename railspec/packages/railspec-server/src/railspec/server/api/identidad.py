"""Identidad del actor a partir del token (R2): nunca viaja en la entrada de la tool.

- ``IdentidadGithub``: token de usuario de GitHub (OAuth de la GitHub App).
  Resuelve ``github_id`` y login con ``GET /user`` y los equipos con
  ``GET /user/teams`` (el mismo token y el mismo permiso que el inicio de
  sesión de la consola); cachea por hash del token, sin guardarlo.
- ``IdentidadDesarrollo``: tokens fijos por variable de entorno, solo para
  desarrollo sin GitHub App.

- ``VerificadorOidcActions``: token OIDC de GitHub Actions (actor de
  servicio, canal ``ci``). Verifica firma contra el JWKS del emisor, ``iss``,
  ``aud`` y, si se configura, que ``repository`` esté en la lista permitida.
  El alcance fino (repositorio del vínculo y su rama por defecto) lo
  comprueba la tool que lo usa (``graph.index``).
- ``IdentidadCompuesta``: un JWT de GitHub Actions va al verificador OIDC;
  cualquier otro token, a la identidad humana.

Roles por equipo (R3): una identidad que conoce los equipos de GitHub de la
persona devuelve un ``ActorConEquipos``; el autorizador de roles los suma a
las asignaciones de la persona. Sin equipos (token de desarrollo, OIDC) el
actor es un ``Actor`` común y solo cuentan las asignaciones personales.
Leer los equipos falla cerrado: sin permiso o sin respuesta de GitHub, ninguno.
"""

from __future__ import annotations

import hashlib
import logging
import time
from datetime import datetime
from typing import Any

from pydantic import Field
from railspec.contracts.comun import Actor, AlcanceWorkspace, Canal, OidcGithubActions, TipoActor

EMISOR_ACTIONS = "https://token.actions.githubusercontent.com"
URL_EQUIPOS = "https://api.github.com/user/teams?per_page=100"

log = logging.getLogger("railspec.api")


class TokenInvalido(Exception):
    pass


class ActorConEquipos(Actor):
    """``Actor`` autenticado más los ``equipo_id`` de GitHub que su identidad resolvió.

    Solo lo construyen las identidades del servidor desde un token verificado
    (R2: el actor nunca viaja en la entrada de una tool). ``equipos`` no se
    serializa: ni el estado ni la auditoría lo guardan, y como no es parte del
    contrato ``Actor`` tampoco aparece en sus esquemas.
    """

    equipos: frozenset[int] = Field(default=frozenset(), exclude=True)


def con_equipos(actor: Actor, equipos: frozenset[int]) -> Actor:
    """El mismo actor con sus equipos; sin equipos (o sin ser persona) queda como ``Actor`` común."""

    if not equipos or actor.tipo != TipoActor.humano:
        return actor
    return ActorConEquipos(**actor.model_dump(), equipos=equipos)


def equipos_de_usuario(cliente: Any, cabeceras: dict[str, str]) -> frozenset[int]:
    """``equipo_id`` de los equipos del usuario del token (``GET /user/teams``, con paginación).

    Necesita el permiso de la App (Members, lectura) o el alcance ``read:org``;
    sin él, o ante una respuesta que no es la esperada, devuelve los que
    haya leído hasta ahí (ninguno si falla la primera página).
    """

    equipos: set[int] = set()
    url: str | None = URL_EQUIPOS
    for _ in range(10):
        if url is None:
            break
        r = cliente.get(url, headers=cabeceras)
        cuerpo = r.json() if r.status_code == 200 else None
        if not isinstance(cuerpo, list):
            break
        equipos.update(int(e["id"]) for e in cuerpo)
        url = r.links.get("next", {}).get("url") if hasattr(r, "links") else None
    return frozenset(equipos)


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
        self._cache: dict[str, tuple[float, str, int, frozenset[int]]] = {}

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        clave = hashlib.sha256(token.encode()).hexdigest()
        ahora = time.monotonic()
        cacheado = self._cache.get(clave)
        if cacheado is None or cacheado[0] < ahora:
            import httpx

            cliente = self._cliente or httpx.Client(timeout=10)
            cabeceras = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            r = cliente.get(self.API, headers=cabeceras)
            if r.status_code != 200:
                raise TokenInvalido(f"GitHub rechazó el token ({r.status_code})")
            datos = r.json()
            equipos = self._equipos(cliente, cabeceras)
            cacheado = (ahora + self._ttl, datos["login"], int(datos["id"]), equipos)
            self._cache[clave] = cacheado
        _, login, github_id, equipos = cacheado
        actor = Actor(tipo=TipoActor.humano, canal=Canal(canal), github_id=github_id, login=login)
        return con_equipos(actor, equipos)

    @staticmethod
    def _equipos(cliente: Any, cabeceras: dict[str, str]) -> frozenset[int]:
        # El token ya autenticó a la persona: que no se puedan leer sus equipos (permiso, red) no
        # invalida la sesión, solo deja los roles por equipo sin aplicar hasta el próximo refresco.
        try:
            return equipos_de_usuario(cliente, cabeceras)
        except Exception as exc:
            log.warning(
                "no se pudieron leer los equipos de GitHub (%s); sin roles por equipo", type(exc).__name__
            )
            return frozenset()

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return []

    def expira(self, token: str) -> datetime | None:
        return None


class VerificadorOidcActions:
    """Valida tokens OIDC de GitHub Actions y los convierte en actores de servicio.

    ``claves`` resuelve la clave de firma de un JWT (``PyJWKClient`` contra el
    JWKS del emisor por defecto; las pruebas inyectan una clave local)."""

    ALGORITMOS = ("RS256",)
    RECLAMOS = ("exp", "iat", "iss", "aud", "repository", "workflow_ref", "ref")

    def __init__(
        self,
        audiencia: str,
        emisor: str = EMISOR_ACTIONS,
        repositorios: frozenset[str] = frozenset(),
        claves: Any | None = None,
        margen_s: int = 60,
    ) -> None:
        self.audiencia = audiencia
        self.emisor = emisor.rstrip("/")
        self.repositorios = frozenset(r.lower() for r in repositorios)
        self._margen = margen_s
        if claves is None:
            import jwt

            claves = jwt.PyJWKClient(f"{self.emisor}/.well-known/jwks", cache_keys=True, lifespan=3600)
        self._claves = claves

    def es_suyo(self, token: str) -> bool:
        """Un JWT cuyo ``iss`` (sin verificar) es este emisor."""

        if token.count(".") != 2:
            return False
        import jwt

        try:
            reclamos = jwt.decode(token, options={"verify_signature": False})
        except jwt.PyJWTError:
            return False
        return str(reclamos.get("iss", "")).rstrip("/") == self.emisor

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        import jwt

        try:
            clave = self._claves.get_signing_key_from_jwt(token).key
            reclamos = jwt.decode(
                token,
                clave,
                algorithms=list(self.ALGORITMOS),
                audience=self.audiencia,
                issuer=self.emisor,
                leeway=self._margen,
                options={"require": list(self.RECLAMOS)},
            )
        except jwt.PyJWTError as exc:
            raise TokenInvalido(f"token OIDC inválido: {exc}") from exc
        except Exception as exc:  # JWKS inaccesible
            raise TokenInvalido(f"no se pudo verificar el token OIDC: {exc}") from exc
        repositorio = str(reclamos["repository"])
        if self.repositorios and repositorio.lower() not in self.repositorios:
            raise TokenInvalido(f"el repositorio {repositorio} no está autorizado para OIDC")
        run_id = reclamos.get("run_id")
        return Actor(
            tipo=TipoActor.servicio,
            canal=Canal.ci,
            oidc=OidcGithubActions(
                repositorio=repositorio,
                workflow=str(reclamos["workflow_ref"]),
                run_id=int(run_id) if str(run_id or "").isdigit() else None,
            ),
        )


class IdentidadCompuesta:
    """Identidad humana (GitHub o desarrollo) más la de servicio por OIDC de Actions."""

    def __init__(self, humana: Any, oidc: VerificadorOidcActions | None) -> None:
        self.humana = humana
        self.oidc = oidc
        self.nombre = humana.nombre

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        if self.oidc is not None and self.oidc.es_suyo(token):
            return self.oidc.actor_desde_token(token, canal)
        return self.humana.actor_desde_token(token, canal)

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return self.humana.workspaces_visibles(actor, org)

    def expira(self, token: str) -> datetime | None:
        return self.humana.expira(token)


def token_de_cabecera(valor: str | None) -> str | None:
    if not valor:
        return None
    esquema, _, token = valor.partition(" ")
    return token.strip() if esquema.lower() == "bearer" and token.strip() else None
