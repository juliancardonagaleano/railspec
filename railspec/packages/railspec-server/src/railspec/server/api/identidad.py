"""Identidad del actor a partir del token (R2): nunca viaja en la entrada de la tool.

- ``IdentidadGithub``: token de usuario de la GitHub App de Railspec. Comprueba
  con las credenciales de la App que el token lo emitió ella (no cualquier
  OAuth app de terceros), resuelve ``github_id`` y login y, ya verificado el
  token, los equipos con ``GET /user/teams`` (el mismo token y el mismo permiso
  que el inicio de sesión de la consola); cachés acotadas, por hash del token y
  sin guardarlo (``verificacion_github``). Sin App configurada rechaza todo token
  de GitHub salvo en modo desarrollo explícito.
- ``IdentidadDesarrollo``: tokens fijos por variable de entorno, solo para
  desarrollo sin GitHub App.

- ``VerificadorOidcActions``: token OIDC de GitHub Actions (actor de
  servicio, canal ``ci``). Verifica firma contra el JWKS del emisor, ``iss``,
  ``aud`` y que ``repository`` esté en la lista permitida, que es obligatoria:
  cualquier repositorio de GitHub puede pedir un token con la audiencia que
  quiera, así que la audiencia sola no autentica a nadie. El alcance fino
  (repositorio del vínculo y su rama por defecto) lo comprueba la tool que lo
  usa (``graph.index``); el actor de servicio no tiene rol en nada más.
- ``IdentidadCompuesta``: un JWT de GitHub Actions va al verificador OIDC;
  cualquier otro token, a la identidad humana.

Roles por equipo (R3): una identidad que conoce los equipos de GitHub de la
persona devuelve un ``ActorConEquipos``; el autorizador de roles los suma a
las asignaciones de la persona. Sin equipos (token de desarrollo, OIDC) el
actor es un ``Actor`` común y solo cuentan las asignaciones personales.
Leer los equipos falla cerrado: sin permiso o sin respuesta de GitHub, ninguno.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

from pydantic import Field
from railspec.contracts.comun import Actor, AlcanceWorkspace, Canal, OidcGithubActions, TipoActor

from .verificacion_github import (
    GithubNoDisponible,
    TokenRechazado,
    VerificadorTokenGithub,
    equipos_de_usuario,
)

__all__ = ["equipos_de_usuario"]

log = logging.getLogger("railspec.identidad")

EMISOR_ACTIONS = "https://token.actions.githubusercontent.com"


class TokenInvalido(Exception):
    pass


class IdentidadNoDisponible(TokenInvalido):
    """No se pudo comprobar el token (GitHub caído, límite de tasa, saturación). Se rechaza igual
    (es un ``TokenInvalido``: falla cerrado), pero las superficies responden 503 en vez de 401."""


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
    """Token de usuario de GitHub -> ``Actor`` humano, solo si es de la GitHub App de Railspec.

    ``app``: credenciales de la App (``client_id`` y ``client_secret``, p. ej. ``ConfigGithubApp``).
    Sin ``app`` todos los tokens de GitHub se rechazan, salvo con ``permitir_sin_app`` (modo desarrollo
    explícito: ``GET /user`` sin comprobar de qué app es el token). ``cliente``: ``httpx.Client`` o
    un doble. El resto de argumentos acotan las cachés y la concurrencia (ver ``verificacion_github``).
    """

    nombre = "github"

    def __init__(
        self,
        cliente: Any | None = None,
        ttl_s: int = 300,
        *,
        app: Any | None = None,
        permitir_sin_app: bool = False,
        **limites: Any,
    ) -> None:
        self._verificador: VerificadorTokenGithub | None = None
        if app is not None:
            credenciales = (app.client_id, app.client_secret)
            self._verificador = VerificadorTokenGithub(cliente, app=credenciales, ttl_s=ttl_s, **limites)
        elif permitir_sin_app:
            log.warning(
                "tokens de GitHub sin GitHub App configurada (RAILSPEC_PERMITIR_DESARROLLO=1): no se "
                "comprueba de qué app es el token. Solo para desarrollo."
            )
            self._verificador = VerificadorTokenGithub(cliente, app=None, ttl_s=ttl_s, **limites)
        else:
            log.warning(
                "sin GitHub App (RAILSPEC_GITHUB_APP_CLIENT_ID/_SECRET): se rechazan los tokens de GitHub; "
                "solo valen los tokens rsc1 de la consola, los de desarrollo y el OIDC de CI"
            )

    def tamanos_de_cache(self) -> tuple[int, int]:
        """(tokens aceptados, tokens rechazados) en caché; ambas acotadas."""

        return self._verificador.tamanos() if self._verificador else (0, 0)

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        if self._verificador is None:
            raise TokenInvalido(
                "la GitHub App de Railspec no está configurada: no se admiten tokens de GitHub"
            )
        try:
            login, github_id = self._verificador.verificar(token)
        except TokenRechazado as exc:
            raise TokenInvalido(str(exc)) from exc
        except GithubNoDisponible as exc:
            raise IdentidadNoDisponible(str(exc)) from exc
        actor = Actor(tipo=TipoActor.humano, canal=Canal(canal), github_id=github_id, login=login)
        # Solo con el token ya verificado: un token basura nunca llega a pedir equipos.
        return con_equipos(actor, self._verificador.equipos(token))

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
        self.repositorios = frozenset(r.strip().lower() for r in repositorios if r.strip())
        if not self.repositorios:
            # Fallar cerrado: sin lista, cualquier workflow del mundo con ``id-token: write`` valdría.
            raise ValueError(
                "OIDC de CI activo sin RAILSPEC_OIDC_REPOSITORIOS: lista los owner/repo que pueden "
                "presentar un token o deja RAILSPEC_OIDC_AUDIENCIA vacía para desactivarlo"
            )
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
        if repositorio.lower() not in self.repositorios:
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
