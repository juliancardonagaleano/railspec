"""Exploración y estadísticas: unidades, línea de tiempo, trazabilidad, eventos en vivo,
grafo por repositorio, resumen del workspace y auditoría.

Todo es de lectura con rol ``lector`` en el workspace. El tablero, la
telemetría y las consultas al grafo van por el registro de tools
(``POST /consola/api/tools/...``); aquí solo vive lo que no es una tool.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import ValidationError
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace
from railspec.contracts.eventos import Direccion
from railspec.contracts.repositorio import Rol
from railspec.contracts.tools import TelemetryQueryEntrada

from .contexto import ContextoConsola
from .vistas import resumen_orden, resumen_workspace, trazabilidad

router = APIRouter()

_LADOS = (Direccion.remoto_a_local, Direccion.local_a_remoto)


def _ctx(request: Request) -> ContextoConsola:
    return request.app.state.consola


async def _lector(request: Request, org: str, ws: str) -> ContextoConsola:
    ctx = _ctx(request)
    ctx.permisos(await ctx.sesion(request)).exigir(org, ws, Rol.lector)
    return ctx


def _alcance_unidad(org: str, ws: str, unidad: str) -> AlcanceUnidad:
    try:
        return AlcanceUnidad(org=org, workspace=ws, unidad=unidad)
    except ValidationError as exc:
        raise HTTPException(422, f"unidad inválida: {unidad}") from exc


def _estado(ctx: ContextoConsola, alcance: AlcanceUnidad):
    estado = ctx.almacen.obtener_estado(alcance)
    if estado is None:
        raise HTTPException(404, f"no existe la unidad {alcance.unidad}")
    return estado


def _snapshots(ctx: ContextoConsola, alcance: AlcanceUnidad, reportes: dict[str, dict[str, Any]]):
    snaps = {}
    for r in reportes.values():
        sid = r.get("snapshot_id")
        if sid:
            s = ctx.datos.snapshot(alcance, str(sid))
            if s is not None:
                snaps[str(sid)] = s
    return snaps


# --- unidades ------------------------------------------------------------------------------------


@router.get("/orgs/{org}/workspaces/{ws}/unidades/{unidad}")
async def unidad(org: str, ws: str, unidad: str, request: Request) -> dict[str, Any]:
    ctx = await _lector(request, org, ws)
    alcance = _alcance_unidad(org, ws, unidad)
    estado = _estado(ctx, alcance)
    vigente = None
    if estado.orden_vigente is not None:
        orden = next(
            (o for o in ctx.datos.ordenes(alcance) if str(o["id"]) == str(estado.orden_vigente)), None
        )
        vigente = resumen_orden(orden) if orden else None
    return {"estado": estado.model_dump(mode="json"), "orden_vigente": vigente}


@router.get("/orgs/{org}/workspaces/{ws}/unidades/{unidad}/linea-de-tiempo")
async def linea_de_tiempo(org: str, ws: str, unidad: str, request: Request) -> dict[str, Any]:
    ctx = await _lector(request, org, ws)
    alcance = _alcance_unidad(org, ws, unidad)
    _estado(ctx, alcance)
    eventos = [e for d in _LADOS for e in ctx.almacen.eventos_desde(alcance, d, 0)]
    eventos.sort(key=lambda e: (e.emitido_en, e.direccion.value, e.secuencia))
    ordenes = ctx.datos.ordenes(alcance)
    reportes = ctx.datos.reportes(alcance)
    snaps = _snapshots(ctx, alcance, reportes)
    resumenes = []
    for o in ordenes:
        r = reportes.get(str(o["id"]))
        sid = (r or {}).get("snapshot_id")
        resumenes.append(resumen_orden(o, r, snaps.get(str(sid)) if sid else None))
    return {"eventos": [e.model_dump(mode="json") for e in eventos], "ordenes": resumenes}


@router.get("/orgs/{org}/workspaces/{ws}/unidades/{unidad}/trazabilidad")
async def trazabilidad_unidad(org: str, ws: str, unidad: str, request: Request) -> dict[str, Any]:
    ctx = await _lector(request, org, ws)
    alcance = _alcance_unidad(org, ws, unidad)
    estado = _estado(ctx, alcance)
    reportes = ctx.datos.reportes(alcance)
    return trazabilidad(estado, ctx.datos.ordenes(alcance), reportes, _snapshots(ctx, alcance, reportes))


def _cursor_inicial(request: Request, desde_remoto: int, desde_local: int) -> tuple[int, int]:
    ultimo = request.headers.get("last-event-id", "")
    remoto, _, local = ultimo.partition(":")
    if remoto.isdigit() and local.isdigit():
        return int(remoto), int(local)
    return desde_remoto, desde_local


@router.get("/orgs/{org}/workspaces/{ws}/unidades/{unidad}/eventos")
async def eventos_en_vivo(
    org: str, ws: str, unidad: str, request: Request, desde_remoto: int = 0, desde_local: int = 0
) -> StreamingResponse:
    """Flujo SSE de eventos de sincronización de la unidad (R8).

    ``id`` de cada mensaje = ``<secuencia remoto→local>:<secuencia local→remoto>``,
    así que ``EventSource`` retoma con ``Last-Event-ID`` sin repetir eventos.
    """

    ctx = await _lector(request, org, ws)
    alcance = _alcance_unidad(org, ws, unidad)
    _estado(ctx, alcance)
    cursores = dict(zip(_LADOS, _cursor_inicial(request, desde_remoto, desde_local), strict=True))

    async def flujo():
        inicio = ultimo_envio = time.monotonic()
        version = None
        yield "retry: 3000\n\n"
        while True:
            enviado = False
            for direccion in _LADOS:
                nuevos = await asyncio.to_thread(
                    ctx.almacen.eventos_desde, alcance, direccion, cursores[direccion], 100
                )
                for e in nuevos:
                    cursores[direccion] = e.secuencia
                    marca = f"{cursores[_LADOS[0]]}:{cursores[_LADOS[1]]}"
                    yield f"event: sync\nid: {marca}\ndata: {e.model_dump_json()}\n\n"
                    enviado = True
            estado = await asyncio.to_thread(ctx.almacen.obtener_estado, alcance)
            if estado is not None and estado.version != version:
                if version is not None:
                    yield f"event: estado\ndata: {json.dumps({'version': estado.version})}\n\n"
                    enviado = True
                version = estado.version
            ahora = time.monotonic()
            if enviado:
                ultimo_envio = ahora
            elif ahora - ultimo_envio >= ctx.sse_ping_s:
                yield ": ping\n\n"
                ultimo_envio = ahora
            if ahora - inicio >= ctx.sse_duracion_max_s or await request.is_disconnected():
                return
            await asyncio.sleep(ctx.sse_intervalo_s)

    cabeceras = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(flujo(), media_type="text/event-stream", headers=cabeceras)


# --- grafo ------------------------------------------------------------------------------------------


@router.get("/orgs/{org}/workspaces/{ws}/grafo/repositorios")
async def repositorios_grafo(org: str, ws: str, request: Request) -> list[dict[str, Any]]:
    ctx = await _lector(request, org, ws)
    return await asyncio.to_thread(_repos_grafo, ctx, org, ws)


def _repos_grafo(ctx: ContextoConsola, org: str, ws: str) -> list[dict[str, Any]]:
    salida = []
    for v in ctx.datos.vinculos(org, ws):
        commit = None
        if ctx.acceso_grafo is not None:
            try:
                commit = ctx.acceso_grafo.espacio(AlcanceRepositorio(**v.alcance.model_dump())).meta().commit
            except Exception:  # grafo caído o sin indexar: la consola lo muestra sin commit
                commit = None
        salida.append(
            {
                "repositorio": v.alcance.repositorio,
                "nivel_codigo": v.nivel_codigo.value,
                "rol": v.rol.value,
                "commit": commit,
            }
        )
    return salida


# --- estadísticas y auditoría ---------------------------------------------------------------------


@router.get("/orgs/{org}/workspaces/{ws}/resumen")
async def resumen(org: str, ws: str, request: Request) -> dict[str, Any]:
    ctx = await _lector(request, org, ws)
    ahora = ctx.reloj().astimezone(UTC)
    inicio_mes = datetime(ahora.year, ahora.month, 1, tzinfo=UTC)
    telemetria = await asyncio.to_thread(
        ctx.almacen.consultar_telemetria,
        TelemetryQueryEntrada(org=org, workspace=ws, desde=inicio_mes, hasta=ahora + timedelta(seconds=1)),
    )
    presupuesto = ctx.almacen.presupuesto(AlcanceWorkspace(org=org, workspace=ws))
    return resumen_workspace(
        ctx.datos.unidades(org, ws),
        sum(f.costo_usd for f in telemetria.filas),
        presupuesto.mensual_usd if presupuesto else None,
        inicio_mes.isoformat(),
        await asyncio.to_thread(_repos_grafo, ctx, org, ws),
    )


@router.get("/orgs/{org}/workspaces/{ws}/auditoria")
async def auditoria(
    org: str,
    ws: str,
    request: Request,
    evento: str | None = None,
    repositorio: str | None = None,
    unidad: str | None = None,
    desde: str | None = None,
    hasta: str | None = None,
    cursor: str | None = None,
    limite: int = 50,
) -> dict[str, Any]:
    ctx = await _lector(request, org, ws)
    try:
        registros, siguiente = ctx.datos.auditoria(
            org,
            ws,
            evento=evento,
            repositorio=repositorio,
            unidad=unidad,
            desde=_fecha(desde),
            hasta=_fecha(hasta),
            cursor=cursor,
            limite=max(1, min(limite, 200)),
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    return {"registros": [r.model_dump(mode="json") for r in registros], "cursor_siguiente": siguiente}


def _fecha(valor: str | None) -> str | None:
    """ISO de entrada → ISO UTC con ``Z``, el formato con que se guarda ``en``."""

    if not valor:
        return None
    try:
        fecha = datetime.fromisoformat(valor.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"fecha inválida: {valor}") from exc
    if fecha.tzinfo is None:
        fecha = fecha.replace(tzinfo=UTC)
    return fecha.astimezone(UTC).isoformat().replace("+00:00", "Z")
