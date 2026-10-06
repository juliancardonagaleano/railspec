"""Administración: organizaciones, workspaces, roles y vínculos de repositorio.

Toda escritura lleva bloqueo optimista (``version``), auditoría en la propia
entidad (R4) y un registro en ``auditoria`` con el actor (R2).
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import uuid
from collections.abc import Callable
from functools import partial
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import Field, field_validator
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

from ..api.grafo import repositorio_de_url
from .almacen import WORKSPACE_ORG
from .api import Entrada
from .contexto import ContextoConsola, alcanza
from .github import PATRON_LOGIN, ErrorGithub

log = logging.getLogger("railspec.consola")

router = APIRouter()

SLUG = r"^[a-z0-9][a-z0-9-]{0,62}$"
#: ``org`` audita la organización; los otros dos son rutas de organización en la SPA.
RESERVADOS = frozenset({WORKSPACE_ORG, "administracion", "configuracion"})


def _ctx(request: Request) -> ContextoConsola:
    return request.app.state.consola


def _json(modelo: Any) -> dict[str, Any]:
    return modelo.model_dump(mode="json")


# --- política: diff auditado y relajaciones -------------------------------------------------------------

#: Cuanto mayor, menos restrictivo.
_ORDEN_NIVEL = {NivelCodigo.restringido: 0, NivelCodigo.interno: 1, NivelCodigo.abierto: 2}


def _txt(valor: Any) -> str:
    if valor is None or valor == "":
        return "-"
    if isinstance(valor, bool):
        return "true" if valor else "false"
    if isinstance(valor, list | tuple):
        return ",".join(_txt(v) for v in valor) or "[]"
    return str(getattr(valor, "value", valor))


def _detalle_cambios(cambios: dict[str, tuple[Any, Any]]) -> dict[str, str]:
    """``{campo: (antes, después)}`` → claves ``cambio_<campo>`` = ``antes -> después`` para la auditoría."""

    return {f"cambio_{campo}": f"{_txt(a)} -> {_txt(d)}"[:300] for campo, (a, d) in cambios.items()}


def _campos_vinculo(
    nivel: NivelCodigo, politica: PoliticaChat, v: VinculoRepositorio | None
) -> dict[str, Any]:
    """Campos auditables del vínculo (los de política primero). Sin ``v``, solo los de política."""

    campos: dict[str, Any] = {
        "nivel_codigo": nivel,
        "chat_permitido": politica.permitido,
        "chat_hosting": politica.hosting,
        "chat_modelos_permitidos": politica.modelos_permitidos,
        "chat_fragmentos_en_respuesta": politica.fragmentos_en_respuesta,
        "chat_huella_tokens_n": politica.huella_tokens_n,
        "chat_presupuesto_fuga_conversacion": politica.presupuesto_fuga_conversacion,
        "chat_presupuesto_fuga_usuario_dia": politica.presupuesto_fuga_usuario_dia,
    }
    if v is not None:
        campos |= {
            "url": v.url,
            "rol": v.rol,
            "rama_por_defecto": v.rama_por_defecto,
            "retencion_snapshots_dias": v.retencion_snapshots_dias,
            "exclusiones": v.exclusiones,
        }
    return campos


def _relajaciones_vinculo(antes: dict[str, Any], despues: dict[str, Any]) -> list[str]:
    """Campos de política que quedan menos restrictivos. Ante la duda (otra lista de modelos) relaja."""

    r: list[str] = []
    if _ORDEN_NIVEL[despues["nivel_codigo"]] > _ORDEN_NIVEL[antes["nivel_codigo"]]:
        r.append("nivel_codigo")
    if not antes["chat_permitido"] and despues["chat_permitido"]:
        r.append("chat_permitido")
    if not antes["chat_fragmentos_en_respuesta"] and despues["chat_fragmentos_en_respuesta"]:
        r.append("chat_fragmentos_en_respuesta")
    for campo in (
        "chat_huella_tokens_n",
        "chat_presupuesto_fuga_conversacion",
        "chat_presupuesto_fuga_usuario_dia",
    ):
        if despues[campo] > antes[campo]:
            r.append(campo)
    modelos_antes, modelos_despues = antes["chat_modelos_permitidos"], despues["chat_modelos_permitidos"]
    if modelos_antes and (not modelos_despues or not set(modelos_despues) <= set(modelos_antes)):
        r.append("chat_modelos_permitidos")  # vacío = todos los del catálogo
    return r


def _exigir_para_relajar(permisos: Any, org: str, relajan: list[str], motivo: str) -> None:
    """Relajar una política exige ``org-admin`` (o la plataforma) y un motivo; endurecer no."""

    if not relajan:
        return
    if not alcanza(permisos.rol(org, None), Rol.org_admin):
        raise HTTPException(403, f"relajar la política ({', '.join(relajan)}) exige org-admin")
    if not motivo:
        raise HTTPException(422, f"relajar la política ({', '.join(relajan)}) exige un motivo")


# --- organizaciones ------------------------------------------------------------------------


class OrganizacionNueva(Entrada):
    id: str = Field(pattern=SLUG)
    nombre: str
    #: Owner de GitHub de la organización: los vínculos de repositorio solo pueden ser suyos.
    github_org: str | None = Field(default=None, pattern=f"^{PATRON_LOGIN}$")
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
    permisos = ctx.permisos(sesion)
    permisos.exigir(org, None, Rol.org_admin)
    previa = ctx.datos.organizacion(org)
    if previa is None:
        raise HTTPException(404, f"no existe la organización {org}")
    if entrada.github_org != previa.github_org:
        # ``github_org`` delimita qué repositorios (y qué clones compartidos) puede vincular la organización:
        # si un org-admin lo fijara a su gusto, podría apuntar a los de otro tenant.
        if entrada.github_org is not None and not re.fullmatch(PATRON_LOGIN, entrada.github_org):
            raise HTTPException(422, "github_org no es un nombre de organización de GitHub")
        if not permisos.plataforma:
            raise HTTPException(403, "github_org solo lo cambia quien administra la plataforma")
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
    #: Opcional: queda en la auditoría. La zona es informativa (ya no restringe proveedores).
    motivo: str | None = Field(default=None, max_length=2000)


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
        zona_datos_azure=w.zona_datos_azure,
    )
    return _json(w)


@router.put("/orgs/{org}/workspaces/{ws}")
async def editar_workspace(org: str, ws: str, entrada: WorkspaceEdicion, request: Request) -> dict[str, Any]:
    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    permisos = ctx.permisos(sesion)
    permisos.exigir(org, ws, Rol.workspace_admin)
    previa = ctx.datos.workspace(org, ws)
    if previa is None:
        raise HTTPException(404, f"no existe el workspace {org}/{ws}")
    # La zona de datos del workspace es un dato informativo: ya no restringe proveedores ni modelos,
    # así que cambiarla no exige org-admin ni motivo (el cambio sí queda en la auditoría).
    zona_antes, zona_despues = previa.zona_datos_azure or None, entrada.zona_datos_azure or None
    motivo = (entrada.motivo or "").strip()
    actor = sesion.actor()
    w = Workspace(
        alcance=previa.alcance,
        **entrada.model_dump(exclude={"version", "motivo"}),
        version=entrada.version + 1,
        auditoria=ctx.auditoria_entidad(actor, previa.auditoria),
    )
    ctx.datos.guardar_workspace(w, entrada.version)
    cambios = {"zona_datos_azure": (zona_antes, zona_despues)} if zona_antes != zona_despues else {}
    ctx.auditar(
        actor,
        org,
        ws,
        EventoAuditoria.cambio_configuracion,
        entidad="workspace",
        accion="editar",
        motivo=motivo or None,
        **_detalle_cambios(cambios),
    )
    return _json(w)


# --- roles (R3) ------------------------------------------------------------------------------------


class SujetoPorLogin(Entrada):
    tipo: Literal["usuario"] = "usuario"
    login: str = Field(pattern=f"^{PATRON_LOGIN}$")


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

    @field_validator("url")
    @classmethod
    def _url_de_github(cls, url: str) -> str:
        if repositorio_de_url(url) is None:
            raise ValueError(
                "la URL debe ser https://github.com/<owner>/<repo>, sin credenciales, puerto, "
                "consulta, fragmento ni segmentos de más"
            )
        return url


def _owners_de_plataforma() -> frozenset[str]:
    """``RAILSPEC_VINCULOS_OWNERS``: owners que vincula una organización sin ``github_org``."""

    crudo = os.environ.get("RAILSPEC_VINCULOS_OWNERS", "")
    return frozenset(p.lower() for p in re.split(r"[,\s]+", crudo) if p)


def _exigir_owner_de_la_org(ctx: ContextoConsola, org: str, url: str) -> None:
    """El owner de la URL tiene que ser el ``github_org`` de la organización (falla cerrado si no hay)."""

    owner = (repositorio_de_url(url) or "").split("/")[0].lower()
    previa = ctx.datos.organizacion(org)
    if previa is not None and previa.github_org:
        if owner != previa.github_org.lower():
            raise HTTPException(
                422, f"el repositorio debe ser de la organización de GitHub '{previa.github_org}' ({org})"
            )
    elif owner not in _owners_de_plataforma():
        raise HTTPException(
            422,
            f"{org} no tiene github_org: quien administra la plataforma lo define en la organización "
            "o autoriza el owner en RAILSPEC_VINCULOS_OWNERS",
        )


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
    permisos = ctx.permisos(sesion)
    permisos.exigir(org, ws, Rol.workspace_admin)
    _exigir_owner_de_la_org(ctx, org, entrada.url)
    alcance = AlcanceRepositorio(org=org, workspace=ws, repositorio=repo)
    previo = ctx.datos.vinculo(alcance)
    if (previo is None) != (entrada.version is None):
        raise HTTPException(409, "el vínculo ya existe" if previo else "el vínculo no existe")
    motivo = (entrada.motivo or "").strip()
    cambia_nivel = previo is not None and previo.nivel_codigo != entrada.nivel_codigo
    if cambia_nivel and not motivo:
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
    # Diff de política contra lo vigente; un vínculo nuevo se compara con la política por defecto de
    # ``restringido`` (si no, desvincular y crear en ``abierto`` esquivaría el cambio de nivel).
    if previo is not None:
        antes = _campos_vinculo(previo.nivel_codigo, previo.chat_contexto_codigo, previo)
    else:
        por_defecto = politica_chat_por_defecto(NivelCodigo.restringido)
        antes = _campos_vinculo(NivelCodigo.restringido, por_defecto, None)
    despues = _campos_vinculo(v.nivel_codigo, v.chat_contexto_codigo, v if previo is not None else None)
    cambios = {k: (antes[k], despues[k]) for k in antes if antes[k] != despues[k]}
    relajan = _relajaciones_vinculo(antes, despues)
    _exigir_para_relajar(permisos, org, relajan, motivo)
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
            motivo=motivo,
            relaja=",".join(relajan) or None,
            **_detalle_cambios({k: c for k, c in cambios.items() if k != "nivel_codigo"}),
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
            motivo=motivo or None,
            relaja=",".join(relajan) or None,
            **_detalle_cambios(cambios),
        )
    return _json(v)


#: Intentos por paso al desvincular y espera entre ellos (se multiplica por el número de intento).
#: Cada paso es idempotente, así que repetirlo no deja nada a medias.
INTENTOS_DESVINCULO = 3
ESPERA_DESVINCULO_S = 0.5


async def _paso_desvinculo(nombre: str, paso: Callable[[], Any]) -> tuple[Any, str | None]:
    """Ejecuta ``paso`` en un hilo con reintento acotado: ``(resultado, None)`` o ``(None, tipo del error)``.

    De la causa solo sale el tipo de la excepción (a la auditoría y al cuerpo del error): su texto puede
    traer direcciones o credenciales de la base y va únicamente al log.
    """

    error = ""
    for intento in range(1, INTENTOS_DESVINCULO + 1):
        try:
            return await asyncio.to_thread(paso), None
        except Exception as exc:  # FalkorDB y Mongo fallan con tipos propios
            error = type(exc).__name__
            log.warning(
                "desvincular: %s falló (intento %d/%d): %s", nombre, intento, INTENTOS_DESVINCULO, exc
            )
            if intento < INTENTOS_DESVINCULO:
                await asyncio.sleep(ESPERA_DESVINCULO_S * intento)
    return None, error


@router.delete("/orgs/{org}/workspaces/{ws}/repositorios/{repo}")
async def desvincular(org: str, ws: str, repo: str, request: Request, motivo: str = "") -> Response:
    """Desvincula un repositorio y borra lo que el servidor guardó de él.

    Orden: audita la intención, borra el grafo, borra los snapshots y, solo si ambos terminaron, el
    vínculo. Si algo falla tras los reintentos el vínculo se queda (el repositorio sigue visible y la
    llamada se puede repetir: cada paso es idempotente), se audita el cierre como ``incompleto`` y
    responde 502 con lo que falta.
    """

    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.workspace_admin)
    if not motivo.strip():
        raise HTTPException(422, "desvincular exige un motivo")
    alcance = AlcanceRepositorio(org=org, workspace=ws, repositorio=repo)
    previo = ctx.datos.vinculo(alcance)
    if previo is None:
        raise HTTPException(404, f"{repo} no está vinculado a {org}/{ws}")
    actor = sesion.actor()
    base = {"motivo": motivo.strip(), "nivel": previo.nivel_codigo.value}
    evento = EventoAuditoria.desvinculo_repositorio
    ctx.auditar(actor, org, ws, evento, repositorio=repo, fase="inicio", **base)

    # Los pasos de datos no dependen entre sí: uno que falla no deja sin borrar el otro (el código de
    # los snapshots de ``interno`` y ``abierto`` no debe esperar a que vuelva el grafo).
    errores: dict[str, str] = {}
    grafo_borrado = False
    if ctx.grafo is not None:
        _, error = await _paso_desvinculo("grafo", partial(ctx.grafo.borrar_repositorio, alcance))
        grafo_borrado = error is None
        if error:
            errores["grafo"] = error
    snapshots, error = await _paso_desvinculo(
        "snapshots", partial(ctx.almacen.borrar_snapshots_repositorio, alcance)
    )
    if error:
        errores["snapshots"] = error
    if not errores:
        _, error = await _paso_desvinculo("vinculo", partial(ctx.datos.borrar_vinculo, alcance))
        if error:
            errores["vinculo"] = error

    ctx.auditar(
        actor,
        org,
        ws,
        evento,
        repositorio=repo,
        fase="fin",
        estado="incompleto" if errores else "completo",
        grafo_borrado=grafo_borrado,
        snapshots_borrados=snapshots,
        pendientes=",".join(errores) or None,
        **{f"error_{paso}": tipo for paso, tipo in errores.items()},
        **base,
    )
    if errores:
        raise HTTPException(
            502,
            f"desvinculación incompleta: falló {', '.join(errores)}. {repo} sigue vinculado; "
            "repite la operación para terminar",
        )
    return Response(status_code=204)
