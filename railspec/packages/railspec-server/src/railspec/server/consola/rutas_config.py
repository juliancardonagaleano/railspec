"""Configuración: catálogo de modelos, perfiles, presupuestos y proveedores de contexto.

Cada entidad existe a nivel organización (``workspace`` nulo, la edita un
``org-admin``) o de un workspace (la edita su ``workspace-admin``); el motor
usa la del workspace si existe y si no la de la organización. Leer la
configuración basta con ser ``lector`` del workspace (o de algún workspace,
para la de la organización).

Los proveedores de contexto reciben la consulta del gate y una credencial, así
que su ``url`` y su ``credencial_ref`` los fija solo un ``org-admin`` (o la
plataforma), contra la allowlist de hosts de la plataforma y el namespace de
secretos de la organización; el ``workspace-admin`` edita el resto. Ver
``railspec/docs/proveedores.md``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field
from railspec.contracts.comun import Fase, Perfil, Presupuesto, Riesgo
from railspec.contracts.repositorio import (
    EventoAuditoria,
    ModeloCatalogo,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    RequisitoRol,
    Rol,
    RolContexto,
    TopeGate,
)

from ..contexto.destinos import DestinoNoPermitido, PoliticaDestinos
from ..contexto.secretos import ReferenciaInvalida, validar_referencia
from .api import Entrada
from .contexto import ContextoConsola, alcanza

router = APIRouter()


def _ctx(request: Request) -> ContextoConsola:
    return request.app.state.consola


def _json(modelo: Any) -> dict[str, Any]:
    return modelo.model_dump(mode="json")


async def _lectura_permisos(request: Request, org: str, workspace: str | None):
    ctx = _ctx(request)
    permisos = ctx.permisos(await ctx.sesion(request))
    if workspace is not None:
        permisos.exigir(org, workspace, Rol.lector)
    elif permisos.rol(org, None) is None and not ctx.datos.asignaciones(
        org, permisos.sesion.github_id, permisos.sesion.equipos
    ):
        raise HTTPException(403, f"sin rol en {org}")
    return ctx, permisos


async def _lectura(request: Request, org: str, workspace: str | None) -> ContextoConsola:
    return (await _lectura_permisos(request, org, workspace))[0]


async def _escritura_permisos(request: Request, org: str, workspace: str | None):
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    permisos = ctx.permisos(sesion)
    if workspace is None:
        permisos.exigir(org, None, Rol.org_admin)
    else:
        permisos.exigir(org, workspace, Rol.workspace_admin)
    return ctx, sesion.actor(), permisos


async def _escritura(request: Request, org: str, workspace: str | None):
    ctx, actor, _ = await _escritura_permisos(request, org, workspace)
    return ctx, actor


# --- catálogo ------------------------------------------------------------------------------------


@router.get("/orgs/{org}/catalogo")
async def catalogo(org: str, request: Request) -> list[dict[str, Any]]:
    ctx = await _lectura(request, org, None)
    return [_json(m) for m in ctx.datos.catalogo(org)]


@router.post("/orgs/{org}/catalogo/sincronizar")
async def sincronizar_catalogo(org: str, request: Request) -> dict[str, Any]:
    await _escritura(request, org, None)
    # La lectura del catálogo por API de cada proveedor vive en ``proveedores``
    # (otro hilo); hasta que exista, la consola solo muestra lo que haya en Mongo.
    raise HTTPException(501, "la sincronización del catálogo aún no está disponible en este servidor")


def validar_perfil(roles: dict[str, RequisitoRol], catalogo: list[ModeloCatalogo]) -> list[str]:
    """Errores bloqueantes contra el catálogo; lanza 422 si hay. Devuelve avisos."""

    errores: list[str] = []
    avisos: list[str] = []
    por_proveedor: dict[str, dict[str, ModeloCatalogo]] = {}
    for m in catalogo:
        nombres = por_proveedor.setdefault(m.proveedor.value, {})
        nombres[m.modelo] = m
        if m.despliegue:
            nombres[m.despliegue] = m
    for rol, req in sorted(roles.items()):
        for proveedor, modelo in sorted(req.modelo.items()):
            conocidos = por_proveedor.get(proveedor.value)
            if not conocidos:
                avisos.append(f"{rol}: sin catálogo de {proveedor.value}; {modelo} no se pudo validar")
                continue
            m = conocidos.get(modelo)
            if m is None:
                errores.append(f"{rol}: {modelo} no está en el catálogo de {proveedor.value}")
                continue
            c = m.capacidades
            if req.effort is not None and c.efforts and req.effort not in c.efforts:
                errores.append(f"{rol}: {modelo} no admite effort {req.effort.value}")
            if req.structured_outputs and not c.structured_outputs:
                errores.append(f"{rol}: {modelo} no admite salidas estructuradas")
            if req.contexto_min_tokens and req.contexto_min_tokens > c.contexto_max_tokens:
                errores.append(f"{rol}: {modelo} tiene contexto de {c.contexto_max_tokens} tokens")
    if errores:
        raise HTTPException(422, "; ".join(errores))
    return avisos


# --- perfiles ------------------------------------------------------------------------------------


class PerfilEntrada(Entrada):
    roles: dict[str, RequisitoRol]
    gate: dict[Riesgo, TopeGate]
    exploradores: dict[Riesgo, int]
    version: int | None = None


@router.get("/orgs/{org}/perfiles")
async def perfiles(org: str, request: Request, workspace: str | None = None) -> list[dict[str, Any]]:
    ctx = await _lectura(request, org, workspace)
    return [_json(p) for p in ctx.datos.perfiles(org, workspace)]


@router.put("/orgs/{org}/perfiles/{nombre}")
async def guardar_perfil(
    org: str, nombre: Perfil, entrada: PerfilEntrada, request: Request, workspace: str | None = None
) -> dict[str, Any]:
    ctx, actor = await _escritura(request, org, workspace)
    avisos = validar_perfil(entrada.roles, ctx.datos.catalogo(org))
    previo = ctx.datos.perfil(org, workspace, nombre.value)
    p = PerfilConfig(
        org=org,
        workspace=workspace,
        nombre=nombre,
        roles=entrada.roles,
        gate=entrada.gate,
        exploradores=entrada.exploradores,
        version=(entrada.version or 0) + 1,
        auditoria=ctx.auditoria_entidad(actor, previo.auditoria if previo else None),
    )
    ctx.datos.guardar_perfil(p, entrada.version)
    ctx.auditar(
        actor, org, workspace, EventoAuditoria.cambio_configuracion, entidad="perfil", nombre=nombre.value
    )
    return {"perfil": _json(p), "avisos": avisos}


# --- presupuestos --------------------------------------------------------------------------------


class PresupuestoEntrada(Entrada):
    por_unidad: Presupuesto
    por_fase: dict[Fase, Presupuesto] = Field(default_factory=dict)
    mensual_usd: float | None = None
    version: int | None = None


@router.get("/orgs/{org}/presupuestos")
async def presupuestos(org: str, request: Request, workspace: str | None = None) -> list[dict[str, Any]]:
    ctx = await _lectura(request, org, workspace)
    return [_json(p) for p in ctx.datos.presupuestos(org, workspace)]


@router.put("/orgs/{org}/presupuestos")
async def guardar_presupuesto(
    org: str, entrada: PresupuestoEntrada, request: Request, workspace: str | None = None
) -> dict[str, Any]:
    ctx, actor = await _escritura(request, org, workspace)
    previo = ctx.datos.presupuesto(org, workspace)
    p = PresupuestoConfig(
        org=org,
        workspace=workspace,
        **entrada.model_dump(exclude={"version"}),
        version=(entrada.version or 0) + 1,
        auditoria=ctx.auditoria_entidad(actor, previo.auditoria if previo else None),
    )
    ctx.datos.guardar_presupuesto(p, entrada.version)
    ctx.auditar(actor, org, workspace, EventoAuditoria.cambio_configuracion, entidad="presupuesto")
    return _json(p)


# --- proveedores de contexto ------------------------------------------------------------------------


class ProveedorContextoEntrada(Entrada):
    url: str = Field(max_length=512)
    #: ``secret://<org>--<nombre>/<clave>``. ``None`` borra la referencia si quien edita es org-admin; para
    #: un workspace-admin (que no la ve) significa "la que ya hay".
    credencial_ref: str | None = None
    politica_fallo: str
    fases: list[Fase] = Field(default_factory=list)
    presupuesto_tokens: int | None = None
    version: int | None = None


def _vista_proveedor(p: ProveedorContexto, ve_credencial: bool) -> dict[str, Any]:
    """La referencia solo la ven quienes pueden fijarla (org-admin y plataforma); los demás, si hay una."""

    d = _json(p)
    d["credencial_configurada"] = p.credencial_ref is not None
    if not ve_credencial:
        d.pop("credencial_ref", None)
    return d


def _es_org_admin(permisos: Any, org: str) -> bool:
    return alcanza(permisos.rol(org, None), Rol.org_admin)


def _validar_destino(org: str, url: str, credencial_ref: str | None) -> None:
    try:
        PoliticaDestinos.desde_entorno().validar_url(url)
        if credencial_ref is not None:
            validar_referencia(credencial_ref, org)
    except (DestinoNoPermitido, ReferenciaInvalida) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.get("/orgs/{org}/proveedores-contexto")
async def proveedores_contexto(
    org: str, request: Request, workspace: str | None = None
) -> list[dict[str, Any]]:
    ctx, permisos = await _lectura_permisos(request, org, workspace)
    ve_credencial = _es_org_admin(permisos, org)
    return [_vista_proveedor(p, ve_credencial) for p in ctx.datos.proveedores_contexto(org, workspace)]


@router.put("/orgs/{org}/proveedores-contexto/{rol}/{nombre}")
async def guardar_proveedor_contexto(
    org: str,
    rol: RolContexto,
    nombre: str,
    entrada: ProveedorContextoEntrada,
    request: Request,
    workspace: str | None = None,
) -> dict[str, Any]:
    ctx, actor, permisos = await _escritura_permisos(request, org, workspace)
    previo = ctx.datos.proveedor_contexto(org, workspace, rol.value, nombre)
    es_org_admin = _es_org_admin(permisos, org)
    if es_org_admin:
        url, credencial_ref = entrada.url, entrada.credencial_ref
        _validar_destino(org, url, credencial_ref)
    else:
        # El workspace-admin edita política, fases y presupuesto, no a dónde va la consulta ni con qué clave.
        if previo is None:
            raise HTTPException(403, "crear un proveedor de contexto (url y credencial) exige org-admin")
        if entrada.url != previo.url or entrada.credencial_ref not in (None, previo.credencial_ref):
            raise HTTPException(403, "cambiar la url o la credencial_ref de un proveedor exige org-admin")
        url, credencial_ref = previo.url, previo.credencial_ref
    p = ProveedorContexto(
        org=org,
        workspace=workspace,
        rol=rol,
        nombre=nombre,
        url=url,
        credencial_ref=credencial_ref,
        **entrada.model_dump(exclude={"version", "url", "credencial_ref"}),
        version=(entrada.version or 0) + 1,
        auditoria=ctx.auditoria_entidad(actor, previo.auditoria if previo else None),
    )
    ctx.datos.guardar_proveedor_contexto(p, entrada.version)
    ctx.auditar(
        actor,
        org,
        workspace,
        EventoAuditoria.cambio_configuracion,
        entidad="proveedor-contexto",
        accion="crear" if previo is None else "editar",
        rol=rol.value,
        nombre=nombre,
        url=p.url,
        credencial_ref=p.credencial_ref,
        politica_fallo=p.politica_fallo,
        url_previa=previo.url if previo is not None and previo.url != p.url else None,
        credencial_ref_previa=(
            previo.credencial_ref
            if previo is not None and previo.credencial_ref != p.credencial_ref
            else None
        ),
    )
    return _vista_proveedor(p, es_org_admin)


@router.delete("/orgs/{org}/proveedores-contexto/{rol}/{nombre}")
async def borrar_proveedor_contexto(
    org: str, rol: RolContexto, nombre: str, request: Request, workspace: str | None = None
) -> Response:
    ctx, actor = await _escritura(request, org, workspace)
    previo = ctx.datos.proveedor_contexto(org, workspace, rol.value, nombre)
    if not ctx.datos.borrar_proveedor_contexto(org, workspace, rol.value, nombre):
        raise HTTPException(404, "no existe ese proveedor de contexto")
    ctx.auditar(
        actor,
        org,
        workspace,
        EventoAuditoria.cambio_configuracion,
        entidad="proveedor-contexto",
        accion="borrar",
        rol=rol.value,
        nombre=nombre,
        url=previo.url if previo else None,
        credencial_ref=previo.credencial_ref if previo else None,
    )
    return Response(status_code=204)
