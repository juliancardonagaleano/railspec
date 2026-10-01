"""Exploración y estadísticas: unidades, línea de tiempo, trazabilidad, eventos en vivo,
grafo por repositorio, resumen del workspace y auditoría.

Todo es de lectura con rol ``lector`` en el workspace. El tablero, la
telemetría y las consultas al grafo van por el registro de tools
(``POST /consola/api/tools/...``); aquí solo vive lo que no es una tool.

El estado de la unidad sale con la lista blanca de ``vistas.vista_estado``
(sin evidencia ni propuesta de los hallazgos, sin comando de validación).
"""

from __future__ import annotations

import asyncio
import functools
import itertools
import json
import time
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import ValidationError
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace
from railspec.contracts.eventos import Direccion
from railspec.contracts.repositorio import Rol
from railspec.contracts.tools import TelemetryQueryEntrada
from starlette.background import BackgroundTask

from .contexto import ContextoConsola
from .sesion import Sesion
from .vistas import resumen_orden, resumen_workspace, trazabilidad, vista_estado

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
    return {"estado": vista_estado(estado), "orden_vigente": vigente}


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


#: Una secuencia de eventos cabe de sobra en 18 dígitos (y en un entero de 64 bits de Mongo).
_MAX_SECUENCIA = 10**18 - 1
#: Hilos del SSE, aparte del executor por defecto (login, validación de GitHub): un tope de
#: conexiones alcanzado no debe dejar sin hilos al resto de la API.
_HILOS_SSE = 8
#: Holgura sobre la duración máxima antes de dar por perdido un cupo que nadie liberó.
_HOLGURA_CUPO_S = 30.0


def _secuencia(texto: str) -> int | None:
    # ``str.isdigit`` acepta dígitos como "²" que ``int`` no convierte (500): solo ASCII decimal.
    if 0 < len(texto) <= 18 and texto.isascii() and texto.isdecimal():
        return int(texto)
    return None


def _cursor_inicial(request: Request, desde_remoto: int, desde_local: int) -> tuple[int, int]:
    """Cursor de ``Last-Event-ID`` (``<remoto>:<local>``); si no es válido se ignora y vale la consulta."""

    remoto, sep, local = request.headers.get("last-event-id", "").partition(":")
    if sep and (r := _secuencia(remoto)) is not None and (lo := _secuencia(local)) is not None:
        return r, lo
    return desde_remoto, desde_local


class CuposSse:
    """Conexiones SSE abiertas, por persona y en total, en este proceso.

    No espera: si no hay cupo, ``tomar`` devuelve None y la ruta responde 429. Cada cupo
    caduca solo (``vence_s``) para que uno que nadie liberó no cierre el servicio.
    Se usa solo desde el bucle de eventos, sin ``await`` entre comprobar y tomar.
    """

    def __init__(self) -> None:
        self._activos: dict[int, tuple[int, float]] = {}
        self._fichas = itertools.count(1)

    def tomar(self, persona: int, por_persona: int, total: int, vence_s: float) -> int | None:
        ahora = time.monotonic()
        for ficha in [f for f, (_, vence) in self._activos.items() if vence <= ahora]:
            del self._activos[ficha]
        if len(self._activos) >= total:
            return None
        if sum(1 for p, _ in self._activos.values() if p == persona) >= por_persona:
            return None
        ficha = next(self._fichas)
        self._activos[ficha] = (persona, ahora + vence_s)
        return ficha

    def soltar(self, ficha: int) -> None:
        self._activos.pop(ficha, None)  # idempotente: lo llaman el flujo y la tarea de cierre

    def abiertos(self) -> int:
        return len(self._activos)


def _cupos(request: Request) -> CuposSse:
    estado = request.app.state
    if getattr(estado, "cupos_sse", None) is None:
        estado.cupos_sse = CuposSse()
    return estado.cupos_sse


@functools.cache
def _ejecutor_sse() -> ThreadPoolExecutor:
    return ThreadPoolExecutor(max_workers=_HILOS_SSE, thread_name_prefix="railspec-sse")


def _sigue_lector(ctx: ContextoConsola, sesion: Sesion, org: str, ws: str) -> bool:
    try:
        ctx.permisos(sesion).exigir(org, ws, Rol.lector)
    except HTTPException:
        return False
    return True


def _consultar(
    ctx: ContextoConsola,
    alcance: AlcanceUnidad,
    cursores: dict[Direccion, int],
    permiso: Callable[[], bool] | None,
):
    """Todo lo que pide un tick del SSE, en una sola ida al executor.

    None = ya no tiene permiso (``permiso`` solo se pasa en los ticks que revalidan).
    """

    if permiso is not None and not permiso():
        return None
    nuevos = [(d, ctx.almacen.eventos_desde(alcance, d, cursores[d], 100)) for d in _LADOS]
    estado = ctx.almacen.obtener_estado(alcance)
    return nuevos, (estado.version if estado is not None else None)


@router.get("/orgs/{org}/workspaces/{ws}/unidades/{unidad}/eventos")
async def eventos_en_vivo(
    org: str,
    ws: str,
    unidad: str,
    request: Request,
    desde_remoto: int = Query(0, ge=0, le=_MAX_SECUENCIA),
    desde_local: int = Query(0, ge=0, le=_MAX_SECUENCIA),
):
    """Flujo SSE de eventos de sincronización de la unidad (R8).

    ``id`` de cada mensaje = ``<secuencia remoto→local>:<secuencia local→remoto>``,
    así que ``EventSource`` retoma con ``Last-Event-ID`` sin repetir eventos.

    Cada flujo gasta un cupo (por persona y global, ``ConfigConsola.sse_max_*``; al exceder, 429
    con ``Retry-After``), consulta el almacén en un executor propio y una sola vez por tick, y
    vuelve a comprobar el rol ``lector`` cada ``sse_revalidar_s``: si lo pierde, se cierra.
    """

    ctx = _ctx(request)
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.lector)
    alcance = _alcance_unidad(org, ws, unidad)
    _estado(ctx, alcance)
    cursores = dict(zip(_LADOS, _cursor_inicial(request, desde_remoto, desde_local), strict=True))
    cupos = _cupos(request)
    config = ctx.config
    ficha = cupos.tomar(
        sesion.github_id,
        config.sse_max_por_usuario,
        config.sse_max_global,
        ctx.sse_duracion_max_s + _HOLGURA_CUPO_S,
    )
    if ficha is None:
        return JSONResponse(
            {"detalle": "demasiadas conexiones de eventos en vivo; cierra alguna y reintenta"},
            status_code=429,
            headers={"Retry-After": "5"},
        )

    async def flujo():
        bucle = asyncio.get_running_loop()
        try:
            inicio = ultimo_envio = ultima_revision = time.monotonic()
            version = None
            yield "retry: 3000\n\n"
            while True:
                ahora = time.monotonic()
                revisar = ahora - ultima_revision >= config.sse_revalidar_s
                if revisar:
                    ultima_revision = ahora
                permiso = functools.partial(_sigue_lector, ctx, sesion, org, ws) if revisar else None
                lectura = await bucle.run_in_executor(
                    _ejecutor_sse(), _consultar, ctx, alcance, dict(cursores), permiso
                )
                if lectura is None:
                    return  # perdió el rol: se corta; reconectar recibe 403
                nuevos, nueva_version = lectura
                enviado = False
                for direccion, eventos in nuevos:
                    for e in eventos:
                        cursores[direccion] = e.secuencia
                        marca = f"{cursores[_LADOS[0]]}:{cursores[_LADOS[1]]}"
                        yield f"event: sync\nid: {marca}\ndata: {e.model_dump_json()}\n\n"
                        enviado = True
                if nueva_version is not None and nueva_version != version:
                    if version is not None:
                        yield f"event: estado\ndata: {json.dumps({'version': nueva_version})}\n\n"
                        enviado = True
                    version = nueva_version
                ahora = time.monotonic()
                if enviado:
                    ultimo_envio = ahora
                elif ahora - ultimo_envio >= ctx.sse_ping_s:
                    yield ": ping\n\n"
                    ultimo_envio = ahora
                if ahora - inicio >= ctx.sse_duracion_max_s or await request.is_disconnected():
                    return
                await asyncio.sleep(ctx.sse_intervalo_s)
        finally:
            cupos.soltar(ficha)

    cabeceras = {"Cache-Control": "no-cache", "X-Accel-Buffering": "no"}
    return StreamingResponse(
        flujo(),
        media_type="text/event-stream",
        headers=cabeceras,
        # Red de seguridad: si el flujo nunca llegó a arrancar, el cupo igual se libera.
        background=BackgroundTask(cupos.soltar, ficha),
    )


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
