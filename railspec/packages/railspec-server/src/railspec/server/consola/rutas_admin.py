"""Administración: organizaciones, workspaces, roles y vínculos de repositorio.

Toda escritura lleva bloqueo optimista (``version``), auditoría en la propia
entidad (R4) y un registro en ``auditoria`` con el actor (R2).
"""

from __future__ import annotations

import asyncio
import uuid
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field
from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace, NivelCodigo, Perfil, RolRepositorio
from railspec.contracts.repositorio import (
    AsignacionRol,
    EventoAuditoria,
    Organizacion,
    PoliticaChat,
    Rol,
    SujetoEquipo,
    SujetoUsuario,
    VinculoRepositorio,
    Workspace,
    politica_chat_por_defecto,
)

from .almacen import WORKSPACE_ORG
from .api import Entrada
from .contexto import ContextoConsola
from .github import ErrorGithub

router = APIRouter()

SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"
#: ``org`` audita la organización; los otros dos son rutas de organización en la SPA.
RESERVADOS = frozenset({WORKSPACE_ORG, "administracion", "configuracion"})


def _ctx(request: Request) -> ContextoConsola:
    return request.app.state.consola


def _json(modelo: Any) -> dict[str, Any]:
    return modelo.model_dump(mode="json")


# --- organizaciones ------------------------------------------------------------------------


class OrganizacionNueva(Entrada):
    id: str = Field(pattern=SLUG)
    nombre: str
    github_org: str | None = None
    region_datos: str


class OrganizacionEdicion(Entrada):
    nombre: str
    github_org: str | None = None
    region_datos: str
    version: int


@router.get("/orgs")
async def listar_orgs(request: Request) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    permisos = ctx.permisos(sesion)
    ids = (
        None
        if permisos.plataforma
        else {a.org for a in ctx.datos.asignaciones_de_sujeto(sesion.github_id, sesion.equipos)}
    )
    return [_json(o) for o in ctx.datos.organizaciones(ids)]


@router.post("/orgs")
async def crear_org(entrada: OrganizacionNueva, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir_plataforma()
    actor = sesion.actor()
    org = Organizacion(**entrada.model_dump(), version=1, auditoria=ctx.auditoria_entidad(actor, None))
    ctx.datos.guardar_organizacion(org, None)
    ctx.auditar(
        actor, org.id, None, EventoAuditoria.cambio_configuracion, entidad="organizacion", accion="crear"
    )
    return _json(org)


@router.put("/orgs/{org}")
async def editar_org(org: str, entrada: OrganizacionEdicion, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, None, Rol.org_admin)
    previa = ctx.datos.organizacion(org)
    if previa is None:
        raise HTTPException(404, f"no existe la organización {org}")
    actor = sesion.actor()
    nueva = Organizacion(
        id=org,
        **entrada.model_dump(exclude={"version"}),
        version=entrada.version + 1,
        auditoria=ctx.auditoria_entidad(actor, previa.auditoria),
    )
    ctx.datos.guardar_organizacion(nueva, entrada.version)
    ctx.auditar(
        actor, org, None, EventoAuditoria.cambio_configuracion, entidad="organizacion", accion="editar"
    )
    return _json(nueva)


# --- workspaces --------------------------------------------------------------------------------


class WorkspaceNuevo(Entrada):
    workspace: str = Field(pattern=SLUG)
    nombre: str
    zona_datos_azure: str | None = None
    perfil_por_defecto: Perfil = Perfil.estandar


class WorkspaceEdicion(Entrada):
    nombre: str
    zona_datos_azure: str | None = None
    perfil_por_defecto: Perfil = Perfil.estandar
    version: int


@router.get("/orgs/{org}/workspaces")
async def listar_workspaces(org: str, request: Request) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    permisos = ctx.permisos(await ctx.sesion(request))
    return [_json(w) for w in ctx.datos.workspaces(org) if permisos.rol(org, w.alcance.workspace) is not None]


@router.post("/orgs/{org}/workspaces")
async def crear_workspace(org: str, entrada: WorkspaceNuevo, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, None, Rol.org_admin)
    if entrada.workspace in RESERVADOS:
        raise HTTPException(422, f"'{entrada.workspace}' es un nombre reservado de la consola")
    actor = sesion.actor()
    w = Workspace(
        alcance=AlcanceWorkspace(org=org, workspace=entrada.workspace),
        **entrada.model_dump(exclude={"workspace"}),
        version=1,
        auditoria=ctx.auditoria_entidad(actor, None),
    )
    ctx.datos.guardar_workspace(w, None)
    ctx.auditar(
        actor,
        org,
        w.alcance.workspace,
        EventoAuditoria.cambio_configuracion,
        entidad="workspace",
        accion="crear",
    )
    return _json(w)


@router.put("/orgs/{org}/workspaces/{ws}")
async def editar_workspace(org: str, ws: str, entrada: WorkspaceEdicion, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.workspace_admin)
    previa = ctx.datos.workspace(org, ws)
    if previa is None:
        raise HTTPException(404, f"no existe el workspace {org}/{ws}")
    actor = sesion.actor()
    w = Workspace(
        alcance=previa.alcance,
        **entrada.model_dump(exclude={"version"}),
        version=entrada.version + 1,
        auditoria=ctx.auditoria_entidad(actor, previa.auditoria),
    )
    ctx.datos.guardar_workspace(w, entrada.version)
    ctx.auditar(actor, org, ws, EventoAuditoria.cambio_configuracion, entidad="workspace", accion="editar")
    return _json(w)


# --- roles (R3) ------------------------------------------------------------------------------------


class SujetoPorLogin(Entrada):
    tipo: Literal["usuario"] = "usuario"
    login: str = Field(min_length=1, max_length=39)


class RolNuevo(Entrada):
    workspace: str | None = Field(default=None, pattern=SLUG)
    rol: Rol
    sujeto: SujetoUsuario | SujetoEquipo | SujetoPorLogin


def _exigir_para_asignacion(ctx: ContextoConsola, permisos: Any, org: str, a_workspace: str | None) -> None:
    if a_workspace is None:
        permisos.exigir(org, None, Rol.org_admin)
    else:
        permisos.exigir(org, a_workspace, Rol.workspace_admin)


@router.get("/orgs/{org}/roles")
async def listar_roles(org: str, request: Request, workspace: str | None = None) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    permisos = ctx.permisos(await ctx.sesion(request))
    if workspace is None:
        permisos.exigir(org, None, Rol.org_admin)
        return [_json(a) for a in ctx.datos.roles(org)]
    permisos.exigir(org, workspace, Rol.workspace_admin)
    return [_json(a) for a in ctx.datos.roles(org) if a.workspace in (None, workspace)]


@router.post("/orgs/{org}/roles")
async def asignar_rol(org: str, entrada: RolNuevo, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    _exigir_para_asignacion(ctx, ctx.permisos(sesion), org, entrada.workspace)
    sujeto = entrada.sujeto
    if isinstance(sujeto, SujetoPorLogin):
        github_id = ctx.logins_desarrollo.get(sujeto.login)
        if github_id is None:
            try:
                github_id = await asyncio.to_thread(ctx.github.id_de_login, sujeto.login)
            except ErrorGithub as exc:
                raise HTTPException(502, str(exc)) from exc
        if github_id is None:
            raise HTTPException(422, f"GitHub no conoce a {sujeto.login}")
        sujeto = SujetoUsuario(github_id=github_id)
    actor = sesion.actor()
    a = AsignacionRol(
        id=uuid.uuid4(),
        org=org,
        workspace=entrada.workspace,
        rol=entrada.rol,
        sujeto=sujeto,
        version=1,
        auditoria=ctx.auditoria_entidad(actor, None),
    )
    ctx.datos.guardar_rol(a)
    ctx.auditar(
        actor,
        org,
        entrada.workspace,
        EventoAuditoria.cambio_configuracion,
        entidad="rol",
        accion="asignar",
        rol=a.rol.value,
        sujeto=_sujeto(a),
    )
    return _json(a)


@router.delete("/orgs/{org}/roles/{id_}")
async def quitar_rol(org: str, id_: str, request: Request) -> Response:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    permisos = ctx.permisos(sesion)
    a = ctx.datos.rol(org, id_)
    if a is None:
        raise HTTPException(404, "no existe esa asignación")
    _exigir_para_asignacion(ctx, permisos, org, a.workspace)
    if a.rol == Rol.org_admin and not permisos.plataforma:
        restantes = [r for r in ctx.datos.roles(org) if r.rol == Rol.org_admin and str(r.id) != id_]
        if not restantes:
            raise HTTPException(409, "es la última asignación org-admin de la organización")
    ctx.datos.borrar_rol(org, id_)
    ctx.auditar(
        sesion.actor(),
        org,
        a.workspace,
        EventoAuditoria.cambio_configuracion,
        entidad="rol",
        accion="quitar",
        rol=a.rol.value,
        sujeto=_sujeto(a),
    )
    return Response(status_code=204)


def _sujeto(a: AsignacionRol) -> str:
    s = a.sujeto
    return f"usuario:{s.github_id}" if isinstance(s, SujetoUsuario) else f"equipo:{s.github_org}/{s.equipo}"


# --- vínculos de repositorio (R4 y política de código) ----------------------------------------------


class VinculoEntrada(Entrada):
    url: str
    rol: RolRepositorio
    rama_por_defecto: str = "main"
    nivel_codigo: NivelCodigo = NivelCodigo.restringido
    chat_contexto_codigo: PoliticaChat | None = None
    retencion_snapshots_dias: int = 30
    exclusiones: list[str] = Field(default_factory=list)
    version: int | None = None
    motivo: str | None = Field(default=None, max_length=2000)


@router.get("/orgs/{org}/workspaces/{ws}/repositorios")
async def listar_vinculos(org: str, ws: str, request: Request) -> list[dict[str, Any]]:
    ctx = _ctx(request)
    ctx.permisos(await ctx.sesion(request)).exigir(org, ws, Rol.lector)
    return [_json(v) for v in ctx.datos.vinculos(org, ws)]


@router.put("/orgs/{org}/workspaces/{ws}/repositorios/{repo}")
async def guardar_vinculo(
    org: str, ws: str, repo: str, entrada: VinculoEntrada, request: Request
) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.workspace_admin)
    alcance = AlcanceRepositorio(org=org, workspace=ws, repositorio=repo)
    previo = ctx.datos.vinculo(alcance)
    if (previo is None) != (entrada.version is None):
        raise HTTPException(409, "el vínculo ya existe" if previo else "el vínculo no existe")
    cambia_nivel = previo is not None and previo.nivel_codigo != entrada.nivel_codigo
    if cambia_nivel and not (entrada.motivo or "").strip():
        raise HTTPException(422, "cambiar el nivel de política exige un motivo")
    politica = entrada.chat_contexto_codigo
    if politica is None:
        politica = (
            previo.chat_contexto_codigo
            if previo is not None and not cambia_nivel
            else politica_chat_por_defecto(entrada.nivel_codigo)
        )
    actor = sesion.actor()
    v = VinculoRepositorio(
        alcance=alcance,
        **entrada.model_dump(exclude={"version", "motivo", "chat_contexto_codigo"}),
        chat_contexto_codigo=politica,
        version=(entrada.version or 0) + 1,
        auditoria=ctx.auditoria_entidad(actor, previo.auditoria if previo else None),
    )
    ctx.datos.guardar_vinculo(v, entrada.version)
    if cambia_nivel:
        ctx.auditar(
            actor,
            org,
            ws,
            EventoAuditoria.cambio_nivel,
            repositorio=repo,
            de=previo.nivel_codigo.value,
            a=v.nivel_codigo.value,
            motivo=entrada.motivo.strip(),  # type: ignore[union-attr]
        )
    else:
        ctx.auditar(
            actor,
            org,
            ws,
            EventoAuditoria.cambio_configuracion,
            repositorio=repo,
            entidad="vinculo",
            accion="crear" if previo is None else "editar",
            nivel=v.nivel_codigo.value,
        )
    return _json(v)


@router.delete("/orgs/{org}/workspaces/{ws}/repositorios/{repo}")
async def desvincular(org: str, ws: str, repo: str, request: Request, motivo: str = "") -> Response:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.workspace_admin)
    if not motivo.strip():
        raise HTTPException(422, "desvincular exige un motivo")
    alcance = AlcanceRepositorio(org=org, workspace=ws, repositorio=repo)
    previo = ctx.datos.vinculo(alcance)
    if previo is None:
        raise HTTPException(404, f"{repo} no está vinculado a {org}/{ws}")
    ctx.datos.borrar_vinculo(alcance)
    grafo_borrado = False
    if ctx.grafo is not None:
        await asyncio.to_thread(ctx.grafo.borrar_repositorio, alcance)
        grafo_borrado = True
    ctx.auditar(
        sesion.actor(),
        org,
        ws,
        EventoAuditoria.desvinculo_repositorio,
        repositorio=repo,
        motivo=motivo.strip(),
        nivel=previo.nivel_codigo.value,
        grafo_borrado=grafo_borrado,
    )
    return Response(status_code=204)
