"""Contexto compartido por las rutas de la consola: sesión, permisos y utilidades.

Autorización (R3): el rol efectivo en un workspace es el mayor entre las
asignaciones de la persona y las de sus equipos de GitHub, a nivel
organización o de ese workspace. Quien figura en ``RAILSPEC_CONSOLA_ADMINS``
administra la plataforma y actúa como ``org-admin`` en toda organización.
La consola oculta lo que el rol no permite, pero quien decide es esto.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException, Request
from railspec.contracts.comun import Actor, AlcanceWorkspace
from railspec.contracts.repositorio import Auditoria, EventoAuditoria, RegistroAuditoria, Rol

from ..api.identidad import ActorConEquipos, TokenInvalido, token_de_cabecera
from ..api.roles import JERARQUIA, alcanza, mayor, rol_efectivo  # noqa: F401 (la consola los importa de aquí)
from .almacen import WORKSPACE_ORG, AlmacenConsola
from .config import ConfigConsola
from .github import ClienteGithub
from .sesion import COOKIE, Firmador, Sesion

CABECERA_CSRF = "x-railspec-consola"


@dataclass
class ContextoConsola:
    """Lo que necesitan las rutas de ``/consola/api``."""

    config: ConfigConsola
    firmador: Firmador
    datos: AlmacenConsola
    #: ``AlmacenMongo`` del motor (estado, eventos, telemetría).
    almacen: Any
    registro: Any
    #: Identidad del servidor (GitHub/OIDC/desarrollo) ya envuelta con ``IdentidadConConsola``.
    identidad: Any
    github: ClienteGithub
    #: ``login → github_id`` para tokens de desarrollo (resuelve sujetos sin llamar a GitHub).
    logins_desarrollo: dict[str, int] = field(default_factory=dict)
    tokens_desarrollo: dict[str, tuple[str, int]] = field(default_factory=dict)
    grafo: Any | None = None
    acceso_grafo: Any | None = None
    #: ``FuenteCodigo`` del chat (clones canónicos de solo lectura); ``None`` sin ``RAILSPEC_CHAT_CLONES``.
    #: La consola solo le pide la punta de la rama por defecto para sugerir el ``commit_integrado``.
    fuente_codigo: Any | None = None
    #: ``Catalogo`` del servidor (lectura por API de cada proveedor); ``None`` sin proveedores de modelo.
    catalogo: Any | None = None
    #: Sin Mongo (solo desarrollo): toda persona sin asignaciones es ``desarrollador``.
    abierto: bool = False
    reloj: Callable[[], datetime] = field(default=lambda: datetime.now(UTC))
    sse_intervalo_s: float = 1.0
    sse_duracion_max_s: float = 300.0
    sse_ping_s: float = 15.0

    # --- sesión ----------------------------------------------------------------------

    async def sesion(self, request: Request, *, solo_cookie: bool = False) -> Sesion:
        """La sesión de la petición: cookie (con CSRF fuera de GET) o Bearer de GitHub/desarrollo.

        Un ``Authorization: Bearer rsc1…`` (token ``api``) nunca vale aquí: es la credencial de
        ``/v1`` y del chat, no de la consola. ``solo_cookie`` ignora todo Bearer: lo usa quien
        emite tokens, que solo puede hacerlo a partir de una sesión real del navegador.
        """

        token = None if solo_cookie else token_de_cabecera(request.headers.get("authorization"))
        try:
            if token is not None:
                if token.startswith("rsc1."):
                    raise HTTPException(
                        401,
                        "los tokens rsc1 no valen en /consola/api: usa la cookie de sesión "
                        "o un token de GitHub",
                    )
                actor = await asyncio.to_thread(self.identidad.actor_desde_token, token, "consola")
                if actor.github_id is None or actor.login is None:
                    raise HTTPException(403, "la consola solo admite personas")
                equipos = actor.equipos if isinstance(actor, ActorConEquipos) else frozenset()
                return Sesion(actor.login, actor.github_id, self.reloj(), equipos)
            cookie = request.cookies.get(self.config.nombre_cookie(COOKIE))
            if cookie is None:
                raise HTTPException(401, "sin sesión")
            sesion = self.firmador.sesion(cookie, "sesion")
        except TokenInvalido as exc:
            raise HTTPException(401, str(exc)) from exc
        if request.method not in ("GET", "HEAD", "OPTIONS") and request.headers.get(CABECERA_CSRF) != "1":
            raise HTTPException(403, f"falta la cabecera {CABECERA_CSRF}: 1")
        return sesion

    def permisos(self, sesion: Sesion) -> Permisos:
        return Permisos(self, sesion)

    # --- auditoría ---------------------------------------------------------------------

    def auditar(
        self,
        actor: Actor,
        org: str,
        workspace: str | None,
        evento: EventoAuditoria,
        *,
        repositorio: str | None = None,
        **detalle: str | int | bool,
    ) -> None:
        self.datos.registrar_auditoria(
            RegistroAuditoria(
                id=uuid.uuid4(),
                alcance=AlcanceWorkspace(org=org, workspace=workspace or WORKSPACE_ORG),
                evento=evento,
                actor=actor,
                en=self.reloj(),
                repositorio=repositorio,
                detalle={k: v for k, v in detalle.items() if v is not None},
            )
        )

    def auditoria_entidad(self, actor: Actor, previa: Auditoria | None) -> Auditoria:
        ahora = self.reloj()
        if previa is None:
            return Auditoria(creado_por=actor, creado_en=ahora, actualizado_por=actor, actualizado_en=ahora)
        return Auditoria(
            creado_por=previa.creado_por,
            creado_en=previa.creado_en,
            actualizado_por=actor,
            actualizado_en=max(ahora, previa.creado_en),
        )


class Permisos:
    def __init__(self, ctx: ContextoConsola, sesion: Sesion) -> None:
        self.ctx = ctx
        self.sesion = sesion
        self.plataforma = sesion.github_id in ctx.config.administradores
        self._cache: dict[str, list] = {}

    def _asignaciones(self, org: str) -> list:
        if org not in self._cache:
            self._cache[org] = self.ctx.datos.asignaciones(org, self.sesion.github_id, self.sesion.equipos)
        return self._cache[org]

    def rol(self, org: str, workspace: str | None) -> Rol | None:
        if self.plataforma:
            return Rol.org_admin
        return rol_efectivo(self._asignaciones(org), workspace, abierto=self.ctx.abierto)

    def exigir(self, org: str, workspace: str | None, minimo: Rol) -> Rol:
        rol = self.rol(org, workspace)
        if not alcanza(rol, minimo):
            donde = f"{org}/{workspace}" if workspace else org
            raise HTTPException(403, f"hace falta rol {minimo.value} en {donde}")
        return rol  # type: ignore[return-value]

    def exigir_plataforma(self) -> None:
        if not self.plataforma:
            raise HTTPException(403, "solo quien administra la plataforma (RAILSPEC_CONSOLA_ADMINS)")


class AutorizadorConsola:
    """``Autorizador`` del registro de tools para llamadas desde la consola (resuelve equipos)."""

    def __init__(self, permisos: Permisos) -> None:
        self.permisos = permisos

    def rol(self, actor: Actor, org: str, workspace: str | None) -> Rol | None:
        if actor.github_id != self.permisos.sesion.github_id:
            return None
        return self.permisos.rol(org, workspace)
