"""Las tools del protocolo sobre el DAG: start, advance, report, approve, integrate, status, list.

Reglas que se imponen aquí, en el borde:

- el arnés nunca decide fase ni gate: un reporte que no corresponde a la
  orden vigente, a su secuencia o a su ``base_commit`` se rechaza;
- en un checkpoint gana la primera resolución por cualquier canal: la segunda
  pierde el bloqueo optimista y recibe ``checkpoint-ya-resuelto``;
- toda respuesta aceptada se guarda como entrada pendiente antes de reanudar
  el DAG, así que una caída del proceso no la pierde.

El DAG avanza bajo un turno exclusivo por unidad (válido entre réplicas).
``en_linea=True`` lo corre dentro de la llamada (pruebas, desarrollo); en el
servidor corre en segundo plano y ``unit.advance`` responde ``en-espera``
mientras tanto.
"""

from __future__ import annotations

import asyncio
import logging
import re
import socket
import unicodedata
import uuid
from typing import Any

from railspec.contracts import VERSION_CONTRATO
from railspec.contracts.comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    EstadoFase,
    Fase,
    GateFase,
    Modo,
    Perfil,
    Presupuesto,
    Riesgo,
    RolRepositorio,
    TipoActor,
)
from railspec.contracts.estado import (
    Consumo,
    ConversionModo,
    EstadoUnidad,
    Integracion,
    RepositorioUnidad,
    ResolucionCheckpoint,
    TipoCheckpoint,
)
from railspec.contracts.eventos import CheckpointResuelto, Direccion, EventoSync, UnidadIntegrada
from railspec.contracts.reporte import ReporteOrden, ResultadoOrden
from railspec.contracts.repositorio import EventoAuditoria, RegistroAuditoria
from railspec.contracts.tools import (
    AvanceCerrada,
    AvanceCheckpoint,
    AvanceEspera,
    AvanceOrden,
    CodigoError,
    EstadoSalida,
    ResumenUnidad,
    SyncPullEntrada,
    SyncPullSalida,
    SyncPushEntrada,
    SyncPushSalida,
    TelemetryQueryEntrada,
    TelemetryQuerySalida,
    UnitAdvanceEntrada,
    UnitAdvanceSalida,
    UnitApproveEntrada,
    UnitIntegrateEntrada,
    UnitListEntrada,
    UnitListSalida,
    UnitReportSalida,
    UnitSetModeEntrada,
    UnitStartEntrada,
    UnitStartSalida,
    UnitStatusEntrada,
    UnitStatusSalida,
)

from ..estado.checkpoints import nombre_workflow
from ..estado.interfaces import EntradaPendiente, TipoEntrada
from . import dag
from .nucleo import Nucleo

log = logging.getLogger("railspec.motor")

VERSION_SERVIDOR = tuple(int(x) for x in VERSION_CONTRATO.split("."))
REINTENTAR_S = 5


class ErrorNegocio(Exception):
    """Se traduce a ``ErrorTool`` en MCP y HTTP."""

    def __init__(self, codigo: CodigoError, detalle: str, version_estado: int | None = None) -> None:
        super().__init__(f"{codigo.value}: {detalle}")
        self.codigo = codigo
        self.detalle = detalle
        self.version_estado = version_estado


# --- Triaje determinista ----------------------------------------------------------------

_SENALES_ALTO = (
    "seguridad",
    "autentic",
    "autoriz",
    "permiso",
    "pago",
    "factura",
    "migraci",
    "datos personales",
    "cifr",
    "credencial",
    "secreto",
    "borrado",
    "eliminar datos",
    "producción",
    "esquema de base",
)
_ORDEN_RIESGO = [Riesgo.bajo, Riesgo.medio, Riesgo.alto]


def triaje(entrada: UnitStartEntrada) -> Riesgo:
    """El servidor decide el riesgo: nunca por debajo del sugerido, sube con señales de impacto."""

    riesgo = entrada.riesgo_sugerido or Riesgo.medio
    texto = f"{entrada.titulo}\n{entrada.pedido}".lower()
    if len(entrada.repositorios) > 1 or any(s in texto for s in _SENALES_ALTO):
        riesgo = Riesgo.alto
    return max(riesgo, entrada.riesgo_sugerido or Riesgo.bajo, key=_ORDEN_RIESGO.index)


def slug(titulo: str) -> str:
    plano = unicodedata.normalize("NFKD", titulo).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9]+", "-", plano).strip("-")[:50].strip("-")
    return s or "unidad"


def negociar(version_cliente: str) -> str:
    mayor, menor = (int(x) for x in version_cliente.split("."))
    if mayor != VERSION_SERVIDOR[0]:
        raise ErrorNegocio(
            CodigoError.version_contrato_no_soportada,
            f"el servidor habla {VERSION_SERVIDOR[0]}.x; el cliente pidió {version_cliente}",
        )
    return f"{mayor}.{min(menor, VERSION_SERVIDOR[1])}"


def _momento_de_conversion(estado: EstadoUnidad) -> Fase | None:
    """Fase tras la que se admite convertir el modo ahora, o ``None`` si no se admite."""

    if estado.fase == Fase.research:
        return Fase.research
    cp = estado.checkpoint_pendiente
    if cp is not None and cp.tipo == TipoCheckpoint.aprobar_spec:
        return Fase.spec
    # Tras el gate del spec y antes de que el plan tenga veredicto.
    if (
        GateFase.spec in estado.gates
        and GateFase.plan not in estado.gates
        and estado.fase in (Fase.spec, Fase.plan)
    ):
        return Fase.spec
    return None


# --- Motor --------------------------------------------------------------------------------


class Motor:
    def __init__(
        self, nucleo: Nucleo, checkpoints: Any, *, en_linea: bool = False, ttl_turno_s: int = 900
    ) -> None:
        self.n = nucleo
        self.checkpoints = checkpoints
        self.en_linea = en_linea
        self.ttl_turno_s = ttl_turno_s
        self.dueno = f"{socket.gethostname()}:{uuid.uuid4().hex[:8]}"
        self._tareas: dict[str, asyncio.Task] = {}

    # --- utilidades -------------------------------------------------------------------

    def _estado(self, alcance: AlcanceUnidad) -> EstadoUnidad:
        estado = self.n.almacen.obtener_estado(alcance)
        if estado is None:
            raise ErrorNegocio(CodigoError.no_encontrado, f"unidad {alcance.unidad} no existe")
        return estado

    @staticmethod
    def _humano(actor: Actor, que: str) -> None:
        if actor.tipo != TipoActor.humano:
            raise ErrorNegocio(CodigoError.fuera_de_alcance, f"{que} exige una persona")

    def _auditar(self, alcance: AlcanceUnidad, evento: EventoAuditoria, actor: Actor, **detalle: Any) -> None:
        self.n.almacen.registrar_auditoria(
            RegistroAuditoria(
                id=self.n.nuevo_id(),
                alcance=AlcanceWorkspace(org=alcance.org, workspace=alcance.workspace),
                evento=evento,
                actor=actor,
                en=self.n.reloj(),
                unidad=alcance.unidad,
                detalle={k: v for k, v in detalle.items() if v is not None},
            )
        )

    async def _reanudar(self, alcance: AlcanceUnidad) -> None:
        if self.en_linea:
            await self.procesar(alcance)
            return
        clave = nombre_workflow(alcance)
        tarea = self._tareas.get(clave)
        if tarea is None or tarea.done():
            self._tareas[clave] = asyncio.create_task(self.procesar(alcance))

    async def esperar(self) -> None:
        """Espera a que termine el trabajo en segundo plano (pruebas y apagado)."""

        while pendientes := [t for t in self._tareas.values() if not t.done()]:
            await asyncio.gather(*pendientes, return_exceptions=True)

    # --- runner del DAG ------------------------------------------------------------------

    async def procesar(self, alcance: AlcanceUnidad, pedido: str | None = None) -> bool:
        """Entrega las entradas pendientes al DAG hasta que no quede ninguna."""

        if not self.n.almacen.tomar_turno(alcance, self.dueno, self.n.reloj(), self.ttl_turno_s):
            return False
        try:
            if pedido is not None:
                await dag.construir(self.n, alcance, self.checkpoints).run(dag.Arranque(pedido=pedido))
            while entradas := self.n.almacen.entradas_pendientes(alcance):
                cp = await self.checkpoints.get_latest(workflow_name=nombre_workflow(alcance))
                pendientes = set(cp.pending_request_info_events) if cp else set()
                respuestas: dict[str, Any] = {}
                for e in entradas:
                    if e.request_id not in pendientes:
                        log.warning(
                            "entrada %s sin petición pendiente en %s; se descarta",
                            e.request_id,
                            alcance.unidad,
                        )
                        self.n.almacen.consumir_entrada(alcance, e.request_id)
                        continue
                    modelo = ReporteOrden if e.tipo == TipoEntrada.reporte else ResolucionCheckpoint
                    respuestas[e.request_id] = modelo.model_validate(e.carga)
                if not respuestas:
                    continue
                wf = dag.construir(self.n, alcance, self.checkpoints)
                await wf.run(responses=respuestas, checkpoint_id=cp.checkpoint_id)
                for request_id in respuestas:
                    self.n.almacen.consumir_entrada(alcance, request_id)
            return True
        except Exception:
            log.exception("el DAG de %s falló; las entradas quedan pendientes", alcance.unidad)
            if self.en_linea:
                raise
            return False
        finally:
            self.n.almacen.soltar_turno(alcance, self.dueno)

    # --- unit.start ----------------------------------------------------------------------

    async def start(self, e: UnitStartEntrada, actor: Actor) -> UnitStartSalida:
        negociada = negociar(e.version_contrato_cliente)
        self._humano(actor, "arrancar una unidad")
        motivos = await self.n.validar_perfil(
            e.alcance, e.perfil or Perfil.estandar, e.repositorios[0].repositorio, triaje(e)
        )
        if motivos:
            raise ErrorNegocio(
                CodigoError.perfil_insatisfacible,
                f"perfil {(e.perfil or Perfil.estandar).value}: " + " | ".join(motivos),
            )
        numero = self.n.almacen.siguiente_numero_unidad(e.alcance)
        alcance = AlcanceUnidad(
            org=e.alcance.org,
            workspace=e.alcance.workspace,
            unidad=f"{numero:04d}-{slug(e.titulo)}",
            plan=e.plan,
        )
        presupuesto = self.n.almacen.presupuesto(e.alcance)
        ahora = self.n.reloj()
        conversiones = []
        if e.modo is not None and e.modo != Modo.interactivo:
            conversiones.append(
                ConversionModo(
                    de=Modo.interactivo,
                    a=e.modo,
                    actor=actor,
                    en=ahora,
                    motivo="modo fijado en la petición inicial",
                    tras=None,
                )
            )
        estado = EstadoUnidad(
            unidad=alcance,
            version=1,
            titulo=e.titulo,
            dueno=actor,
            arnes=e.arnes,
            repositorios=[
                RepositorioUnidad(
                    repositorio=r.repositorio,
                    rama=r.rama,
                    base_commit=r.base_commit,
                    rol=RolRepositorio.primario if i == 0 else RolRepositorio.transversal,
                )
                for i, r in enumerate(e.repositorios)
            ],
            fase=Fase.spec,
            estado=EstadoFase.en_progreso,
            modo=conversiones[-1].a if conversiones else Modo.interactivo,
            modo_conversion=conversiones,
            pedido=e.pedido,
            riesgo=triaje(e),
            perfil=e.perfil or Perfil.estandar,
            insumos=list(e.insumos),
            presupuesto=presupuesto.por_unidad if presupuesto else Presupuesto(),
            consumo=Consumo(),
            creado_en=ahora,
            actualizado_en=ahora,
            actualizado_por=actor,
        )
        self.n.almacen.guardar_estado(estado, None)
        # El primer tramo del DAG no llama modelos (solo emite la orden del spec): corre en línea.
        await self.procesar(alcance, pedido=e.pedido)
        return UnitStartSalida(estado=self._estado(alcance), version_contrato_negociada=negociada)

    # --- unit.set_mode ---------------------------------------------------------------------

    async def set_mode(self, e: UnitSetModeEntrada, actor: Actor) -> EstadoSalida:
        """Conversión de modo tras research o tras el checkpoint del spec (protocolo del kit).

        El DAG lee el modo al decidir cada checkpoint, así que la conversión
        rige desde el siguiente gate sin tocar el workflow.
        """

        self._humano(actor, "convertir el modo")

        def convertir(estado: EstadoUnidad) -> dict[str, Any]:
            if estado.version != e.version_vista:
                raise ErrorNegocio(
                    CodigoError.conflicto_version,
                    f"viste la versión {e.version_vista}; la vigente es {estado.version}",
                    estado.version,
                )
            tras = _momento_de_conversion(estado)
            if tras is None:
                raise ErrorNegocio(
                    CodigoError.conversion_no_permitida,
                    "el modo solo se convierte tras research o tras el checkpoint del spec",
                    estado.version,
                )
            if estado.modo == e.modo:
                raise ErrorNegocio(
                    CodigoError.conversion_no_permitida,
                    f"la unidad ya está en {e.modo.value}",
                    estado.version,
                )
            conversion = ConversionModo(
                de=estado.modo, a=e.modo, actor=actor, en=self.n.reloj(), motivo=e.motivo, tras=tras
            )
            return {"modo": e.modo, "modo_conversion": [*estado.modo_conversion, conversion]}

        self._estado(e.unidad)
        nuevo = self.n.escribir(e.unidad, convertir, actor)
        return EstadoSalida(estado=nuevo)

    # --- sync.pull y sync.push (contrato 1.3) --------------------------------------------------

    async def sync_pull(self, e: SyncPullEntrada, actor: Actor) -> SyncPullSalida:
        self._estado(e.unidad)
        eventos = self.n.almacen.eventos_desde(e.unidad, Direccion.remoto_a_local, e.desde, e.limite + 1)
        return SyncPullSalida(
            eventos=eventos[: e.limite],
            ultima_secuencia=self.n.almacen.ultima_secuencia(e.unidad, Direccion.remoto_a_local),
            hay_mas=len(eventos) > e.limite,
        )

    async def sync_push(self, e: SyncPushEntrada, actor: Actor) -> SyncPushSalida:
        """Cola local→remoto del proxy: idempotente por id, sin huecos de secuencia."""

        estado = self._estado(e.unidad)
        ultima = self.n.almacen.ultima_secuencia(e.unidad, Direccion.local_a_remoto)
        duplicados = 0
        for subida in e.eventos:
            if self.n.almacen.existe_evento(e.unidad, subida.id):
                duplicados += 1
                continue
            if subida.secuencia <= ultima:
                raise ErrorNegocio(
                    CodigoError.secuencia_duplicada,
                    f"la secuencia {subida.secuencia} ya está confirmada con otro evento (última {ultima})",
                    estado.version,
                )
            if subida.secuencia != ultima + 1:
                raise ErrorNegocio(
                    CodigoError.secuencia_con_hueco,
                    f"se esperaba la secuencia {ultima + 1} y llegó {subida.secuencia}",
                    estado.version,
                )
            evento = EventoSync(
                id=subida.id,
                direccion=Direccion.local_a_remoto,
                unidad=e.unidad,
                secuencia=subida.secuencia,
                emitido_en=subida.emitido_en,
                actor=actor,
                causado_por=subida.causado_por,
                carga=subida.carga,
            )
            self.n.almacen.registrar_evento(evento)
            self.n.notificar(evento)
            ultima = subida.secuencia
        return SyncPushSalida(confirmada_hasta=ultima, duplicados=duplicados)

    # --- unit.advance ----------------------------------------------------------------------

    async def advance(self, e: UnitAdvanceEntrada, actor: Actor) -> UnitAdvanceSalida:
        estado = self._estado(e.unidad)
        if self.n.almacen.entradas_pendientes(e.unidad):
            await self._reanudar(e.unidad)
            estado = self._estado(e.unidad)
        if estado.fase == Fase.done:
            avance: Any = AvanceCerrada()
        elif estado.orden_vigente is not None:
            orden = self.n.almacen.obtener_orden(e.unidad, str(estado.orden_vigente))
            avance = AvanceOrden(orden=orden)
        elif estado.checkpoint_pendiente is not None:
            avance = AvanceCheckpoint(checkpoint=estado.checkpoint_pendiente)
        elif estado.estado == EstadoFase.bloqueado:
            avance = AvanceEspera(motivo="unidad rechazada; no hay más trabajo", reintentar_en_s=3600)
        else:
            avance = AvanceEspera(
                motivo="el motor está evaluando (gate o transición)", reintentar_en_s=REINTENTAR_S
            )
        return UnitAdvanceSalida(version_estado=estado.version, avance=avance)

    # --- unit.report -------------------------------------------------------------------------

    async def report(self, r: ReporteOrden, actor: Actor) -> UnitReportSalida:
        estado = self._estado(r.unidad)
        if estado.orden_vigente is None or estado.orden_vigente != r.orden_id:
            raise ErrorNegocio(
                CodigoError.orden_no_vigente, "el reporte no corresponde a la orden vigente", estado.version
            )
        orden = self.n.almacen.obtener_orden(r.unidad, str(r.orden_id))
        if orden is None or orden.secuencia != r.secuencia:
            raise ErrorNegocio(
                CodigoError.orden_no_vigente, "secuencia distinta de la orden vigente", estado.version
            )
        if r.base_commit != orden.base_commit:
            raise ErrorNegocio(
                CodigoError.base_commit_distinto, "base_commit distinto del de la orden", estado.version
            )
        self._exigencias(orden, r, estado)

        self.n.almacen.guardar_reporte(r)
        # Los avisos local→remoto (orden.reportada, snapshot.subido) los numera y sube el proxy
        # con sync.push: el servidor no escribe en esa dirección para no pisar su secuencia.
        if r.snapshot is not None:
            s = r.snapshot
            vinculo = self.n.almacen.vinculo(
                AlcanceRepositorio(org=r.unidad.org, workspace=r.unidad.workspace, repositorio=s.repositorio)
            )
            if vinculo:
                self.n.almacen.guardar_snapshot(s, vinculo.retencion_snapshots_dias)
            else:
                self.n.almacen.guardar_snapshot(s)
            if self.n.grafo is not None and s.delta_indice is not None:
                self._grafo_snapshot(r, orden, vinculo)

        def cerrar_orden(e: EstadoUnidad) -> dict[str, Any] | None:
            if e.orden_vigente != r.orden_id:
                raise ErrorNegocio(CodigoError.orden_no_vigente, "otra escritura cerró la orden", e.version)
            return {"orden_vigente": None}

        nuevo = self.n.escribir(r.unidad, cerrar_orden, actor)
        self.n.almacen.registrar_entrada(
            EntradaPendiente(
                alcance=r.unidad,
                request_id=str(r.orden_id),
                tipo=TipoEntrada.reporte,
                carga=r.model_dump(mode="json"),
                recibida_en=self.n.reloj(),
            )
        )
        await self._reanudar(r.unidad)
        return UnitReportSalida(
            version_estado=self._estado(r.unidad).version if self.en_linea else nuevo.version
        )

    def _grafo_snapshot(self, r: ReporteOrden, orden: Any, vinculo: Any) -> None:
        """Superposición de la unidad y trazas ``CA-NN`` en el grafo central.

        Con railspec-graph pasa por ``ingerir`` (exclusiones del vínculo otra
        vez en el servidor y enlace de los criterios de las tareas completadas
        con los símbolos que cambió este snapshot). El grafo informa, no
        decide: un fallo suyo no tumba un reporte ya guardado.
        """

        s = r.snapshot
        criterios = sorted(
            {c for t in getattr(orden, "tareas", []) if t.id in r.tareas_completadas for c in t.criterios}
        )
        ingerir = getattr(self.n.grafo, "ingerir", None)
        try:
            if ingerir is not None and vinculo is not None:
                ingerir(s, vinculo, criterios)
            else:
                self.n.grafo.aplicar_delta(
                    AlcanceRepositorio(
                        org=r.unidad.org, workspace=r.unidad.workspace, repositorio=s.repositorio
                    ),
                    s.base_commit,
                    s.delta_indice,
                    r.unidad.unidad,
                )
        except Exception as exc:
            log.warning("grafo no actualizado para %s/%s: %s", r.unidad.unidad, s.repositorio, exc)

    def _exigencias(self, orden: Any, r: ReporteOrden, estado: EstadoUnidad) -> None:
        req = orden.reporte_requerido
        if r.resultado == ResultadoOrden.completado:
            if req.snapshot and r.snapshot is None:
                raise ErrorNegocio(CodigoError.snapshot_invalido, "la orden exige snapshot", estado.version)
            if req.artefacto and (r.artefacto is None or r.artefacto.tipo != orden.artefacto):
                raise ErrorNegocio(
                    CodigoError.snapshot_invalido,
                    f"la orden exige el artefacto {orden.artefacto.value}",
                    estado.version,
                )
            if req.validacion and r.validacion is None:
                raise ErrorNegocio(
                    CodigoError.snapshot_invalido, "la orden exige la salida de validación", estado.version
                )
        if r.snapshot is not None:
            if r.snapshot.repositorio not in {x.repositorio for x in estado.repositorios}:
                raise ErrorNegocio(
                    CodigoError.snapshot_invalido,
                    "snapshot de un repositorio ajeno a la unidad",
                    estado.version,
                )
            esperado = self.n.nivel(estado, r.snapshot.repositorio)
            if r.snapshot.nivel_codigo != esperado:
                raise ErrorNegocio(
                    CodigoError.snapshot_invalido,
                    f"el vínculo fija nivel {esperado.value}; "
                    f"el snapshot declara {r.snapshot.nivel_codigo.value}",
                    estado.version,
                )

    # --- unit.approve -----------------------------------------------------------------------------

    async def approve(self, e: UnitApproveEntrada, actor: Actor) -> EstadoSalida:
        self._humano(actor, "resolver un checkpoint")
        resolucion = ResolucionCheckpoint(
            checkpoint=e.checkpoint,
            decision=e.decision,
            actor=actor,
            en=self.n.reloj(),
            comentario=e.comentario,
        )

        def resolver(estado: EstadoUnidad) -> dict[str, Any]:
            cp = estado.checkpoint_pendiente
            if cp is None or cp.id != e.checkpoint:
                if any(x.checkpoint == e.checkpoint for x in estado.resoluciones):
                    raise ErrorNegocio(
                        CodigoError.checkpoint_ya_resuelto, "otro canal lo resolvió primero", estado.version
                    )
                raise ErrorNegocio(CodigoError.no_encontrado, "checkpoint desconocido", estado.version)
            return {
                "checkpoint_pendiente": None,
                "resoluciones": [*estado.resoluciones, resolucion],
                "estado": EstadoFase.en_progreso,
            }

        self._estado(e.unidad)
        nuevo = self.n.escribir(e.unidad, resolver, actor)
        self.n.emitir(e.unidad, CheckpointResuelto(checkpoint_id=e.checkpoint, canal=actor.canal), actor)
        self._auditar(
            e.unidad,
            EventoAuditoria.resolucion_checkpoint,
            actor,
            checkpoint=str(e.checkpoint),
            decision=e.decision.value,
            canal=actor.canal.value,
        )
        self.n.almacen.registrar_entrada(
            EntradaPendiente(
                alcance=e.unidad,
                request_id=str(e.checkpoint),
                tipo=TipoEntrada.resolucion,
                carga=resolucion.model_dump(mode="json"),
                recibida_en=self.n.reloj(),
            )
        )
        await self._reanudar(e.unidad)
        return EstadoSalida(estado=self._estado(e.unidad) if self.en_linea else nuevo)

    # --- unit.integrate ------------------------------------------------------------------------------

    async def integrate(self, e: UnitIntegrateEntrada, actor: Actor) -> EstadoSalida:
        self._humano(actor, "integrar una unidad")
        integracion = Integracion(
            actor=actor, en=self.n.reloj(), especificacion_viva=e.especificacion_viva, pr_url=e.pr_url
        )

        def integrar(estado: EstadoUnidad) -> dict[str, Any]:
            if estado.fase != Fase.done:
                raise ErrorNegocio(
                    CodigoError.unidad_no_cerrada, "solo se integra una unidad cerrada", estado.version
                )
            return {"integracion": integracion}

        self._estado(e.unidad)
        nuevo = self.n.escribir(e.unidad, integrar, actor)
        self._descartar_superposiciones(nuevo)
        self.n.emitir(
            e.unidad, UnidadIntegrada(especificacion_viva=e.especificacion_viva, pr_url=e.pr_url), actor
        )
        self._auditar(
            e.unidad,
            EventoAuditoria.integracion,
            actor,
            especificacion_viva=e.especificacion_viva,
            pr_url=e.pr_url,
        )
        return EstadoSalida(estado=nuevo)

    def _descartar_superposiciones(self, estado: EstadoUnidad) -> None:
        """Integrada la unidad, su código llega al canónico por el reindexado de CI; su
        superposición sobra. Las trazas ``CA-NN`` se quedan (grafo aparte)."""

        descartar = getattr(self.n.grafo, "descartar_superposicion", None)
        if descartar is None:
            return
        u = estado.unidad
        for r in estado.repositorios:
            try:
                descartar(
                    AlcanceRepositorio(org=u.org, workspace=u.workspace, repositorio=r.repositorio),
                    u.unidad,
                    r.base_commit,
                )
            except Exception as exc:  # el grafo informa, no decide
                log.warning("superposición de %s/%s no descartada: %s", u.unidad, r.repositorio, exc)

    # --- lecturas ---------------------------------------------------------------------------------------

    async def status(self, e: UnitStatusEntrada, actor: Actor) -> UnitStatusSalida:
        estado = self._estado(e.unidad)
        orden = (
            self.n.almacen.obtener_orden(e.unidad, str(estado.orden_vigente))
            if estado.orden_vigente
            else None
        )
        return UnitStatusSalida(estado=estado, orden_vigente=orden)

    async def list(self, e: UnitListEntrada, actor: Actor) -> UnitListSalida:
        estados, cursor = self.n.almacen.listar_estados(e)
        return UnitListSalida(
            unidades=[
                ResumenUnidad(
                    unidad=s.unidad.unidad,
                    titulo=s.titulo,
                    fase=s.fase,
                    estado=s.estado,
                    modo=s.modo,
                    riesgo=s.riesgo,
                    repositorio_primario=next(
                        r.repositorio for r in s.repositorios if r.rol == RolRepositorio.primario
                    ),
                    dueno_login=s.dueno.login,
                    integrada=s.integracion is not None,
                    actualizado_en=s.actualizado_en,
                )
                for s in estados
            ],
            cursor_siguiente=cursor,
        )

    async def telemetry(self, e: TelemetryQueryEntrada, actor: Actor) -> TelemetryQuerySalida:
        return self.n.almacen.consultar_telemetria(e)
