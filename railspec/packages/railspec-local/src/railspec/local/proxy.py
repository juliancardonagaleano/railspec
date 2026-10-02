"""Núcleo del proxy local, independiente del SDK de MCP.

Traduce las llamadas del arnés a tools del contrato y hace en local lo que el
protocolo exige en local: crear el worktree de la unidad, construir el
snapshot bajo la política de código, correr la validación, leer el artefacto
redactado y mantener el estado local con su cola de eventos.

El arnés nunca decide fase ni gate: el proxy solo reporta la orden en curso
y siempre devuelve el control a ``unit_advance``.
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

from railspec.contracts._base import VERSION_CONTRATO, VERSION_MAYOR
from railspec.contracts.comun import Actor, AlcanceWorkspace, Canal, Modo, Perfil, Riesgo
from railspec.contracts.estado import Checkpoint, Decision, EstadoLocal, EstadoUnidad
from railspec.contracts.eventos import CommitEmpujado, Direccion, EventoSync, OrdenReportada, SnapshotSubido
from railspec.contracts.orden import OrdenDeTrabajo, OrdenImplementar, OrdenRedactar, OrdenRefinar
from railspec.contracts.reporte import ArtefactoRedactado, ReporteOrden, ResultadoOrden, UsoModeloArnes
from railspec.contracts.snapshot import EMBEDDING_MODELO
from railspec.contracts.tools import (
    MAX_EVENTOS_SYNC,
    AvanceCerrada,
    AvanceCheckpoint,
    AvanceEspera,
    AvanceOrden,
    CodigoError,
    EstadoSalida,
    EventoSubida,
    GraphQueryEntrada,
    GraphQuerySalida,
    InsumoGetEntrada,
    InsumoGetSalida,
    RepositorioInicio,
    SyncPullEntrada,
    SyncPullSalida,
    SyncPushEntrada,
    SyncPushSalida,
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

from . import git, insumos, validacion
from .almacen import EXCLUIR_DE_GIT, Almacen
from .cliente import ClienteServidor
from .config import Config
from .errores import ErrorRailspec, ErrorServidor, FueraDeAlcance, SinConexion
from .indice import Indexador
from .politica import construir_snapshot
from .rebase import rebasar_unidad
from .rutas import coincide

Json = dict[str, Any]


def _json(modelo: Any) -> Any:
    return modelo.model_dump(mode="json", exclude_none=True)


class ProxyLocal:
    def __init__(
        self,
        config: Config,
        cliente: ClienteServidor,
        indexador: Indexador | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        nuevo_id: Callable[[], UUID] = uuid4,
    ) -> None:
        self.config = config
        self.cliente = cliente
        self.indexador = indexador
        self.reloj = reloj
        self.nuevo_id = nuevo_id

    # --- localización de unidades ------------------------------------------------

    @property
    def raiz(self) -> Path:
        return self.config.raiz_path

    def unidades_locales(self) -> dict[str, Path]:
        return git.worktrees(self.raiz)

    def _almacen(self, unidad: str | None) -> Almacen:
        locales = self.unidades_locales()
        if unidad is None:
            if len(locales) != 1:
                nombres = ", ".join(sorted(locales)) or "ninguna"
                raise ErrorRailspec(f"Indica la unidad; unidades locales: {nombres}.")
            unidad = next(iter(locales))
        if unidad not in locales:
            raise ErrorRailspec(f"No hay worktree local para la unidad {unidad}; arráncala con unit_start.")
        almacen = Almacen(locales[unidad])
        if not almacen.existe():
            raise ErrorRailspec(f"El worktree de {unidad} no tiene estado local ({almacen.ruta}).")
        return almacen

    def _alcance_ws(self) -> AlcanceWorkspace:
        return AlcanceWorkspace(org=self.config.repo.org, workspace=self.config.repo.workspace)

    # --- unit.start ------------------------------------------------------------------

    async def iniciar(
        self,
        titulo: str,
        pedido: str,
        insumos: list[UUID] | None = None,
        perfil: Perfil | None = None,
        riesgo_sugerido: Riesgo | None = None,
        plan: str | None = None,
        modo: Modo | None = None,
    ) -> Json:
        """``modo`` solo cuando el humano lo fija al pedir (contrato 1.2); si no, nace interactivo."""

        repositorio = self.repositorio_inicio()
        entrada = UnitStartEntrada(
            alcance=self._alcance_ws(),
            repositorios=[repositorio],
            titulo=titulo,
            pedido=pedido,
            plan=plan,
            perfil=perfil,
            riesgo_sugerido=riesgo_sugerido,
            modo=modo,
            arnes=self.config.repo.arnes,
            insumos=insumos or [],
            version_contrato_cliente=VERSION_CONTRATO,
        )
        salida = await self.cliente.llamar("unit.start", entrada, UnitStartSalida)
        return self.abrir_unidad_local(
            salida.estado, salida.version_contrato_negociada, repositorio.base_commit
        )

    def repositorio_inicio(self) -> RepositorioInicio:
        base = git.head(self.raiz)
        rama = git.rama_actual(self.raiz) or base
        return RepositorioInicio(repositorio=self.config.repo.repositorio, rama=rama, base_commit=base)

    def abrir_unidad_local(self, estado_remoto: EstadoUnidad, version_negociada: str, base: str) -> Json:
        """Worktree, rama y estado local de una unidad recién creada en el servidor."""

        if int(version_negociada.split(".")[0]) != VERSION_MAYOR:
            raise ErrorRailspec(
                f"El servidor negoció el contrato {version_negociada}; "
                f"este proxy habla {VERSION_CONTRATO}. Actualiza railspec-local."
            )
        unidad = estado_remoto.unidad
        worktree = Path(self.config.dir_worktrees) / unidad.unidad
        rama_unidad = git.rama_de_unidad(unidad.unidad)
        git.excluir_localmente(self.raiz, EXCLUIR_DE_GIT)
        git.crear_worktree(self.raiz, worktree, rama_unidad, base)
        almacen = Almacen(worktree)
        with almacen.cerrojo():
            estado = EstadoLocal(
                unidad=unidad,
                repositorio=self.config.repo.repositorio,
                worktree=str(worktree),
                rama=rama_unidad,
                base_commit=base,
                espejo_remoto=estado_remoto,
            )
            almacen.escribir(almacen.tomar_bloqueo(estado))
        avisos = []
        if git.hay_cambios(self.raiz):
            avisos.append(
                "El clon principal tiene cambios sin commit; la unidad parte de HEAD y no los incluye."
            )
        return {
            "unidad": unidad.unidad,
            "worktree": str(worktree),
            "rama": rama_unidad,
            "base_commit": base,
            "fase": estado_remoto.fase.value,
            "modo": estado_remoto.modo.value,
            "avisos": avisos,
            "siguiente": "unit_advance",
        }

    # --- unit.advance ------------------------------------------------------------------

    async def avanzar(self, unidad: str | None = None) -> Json:
        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.tomar_bloqueo(almacen.leer())
            almacen.escribir(estado)
            try:
                estado, rechazos = await self._sincronizar(almacen, estado)
                version_vista = estado.espejo_remoto.version if estado.espejo_remoto else 1
                salida = await self.cliente.llamar(
                    "unit.advance",
                    UnitAdvanceEntrada(unidad=estado.unidad, version_vista=version_vista),
                    UnitAdvanceSalida,
                )
                estado, recibidos = await self._traer_eventos(estado)
            except SinConexion as exc:
                # _sincronizar persiste cada paso: lo vigente está en disco.
                return self._respuesta_sin_conexion(almacen.leer(), exc)
            estado = await self._refrescar_espejo(estado, 0 if recibidos else salida.version_estado)
            respuesta: Json = {"unidad": estado.unidad.unidad, "worktree": estado.worktree}
            if rechazos:
                respuesta["reportes_rechazados"] = rechazos
            avance = salida.avance
            if isinstance(avance, AvanceOrden):
                orden = avance.orden
                if orden.base_commit != estado.base_commit:
                    estado, rebase, avisos = await self._rebasar(almacen, estado, orden)
                    respuesta["rebase"] = rebase
                    if avisos:
                        respuesta["avisos"] = avisos
                estado = estado.model_copy(update={"orden_en_curso": orden})
                respuesta |= {"tipo": "orden", "orden": _json(orden), "como_ejecutar": _como_ejecutar(orden)}
            else:
                estado = estado.model_copy(update={"orden_en_curso": None})
                if isinstance(avance, AvanceCheckpoint):
                    espejo = estado.espejo_remoto
                    if espejo is None or espejo.checkpoint_pendiente != avance.checkpoint:
                        estado = await self._refrescar_espejo(estado, version=0)
                    respuesta |= {"tipo": "checkpoint", "checkpoint": _json(avance.checkpoint)}
                elif isinstance(avance, AvanceEspera):
                    respuesta |= {
                        "tipo": "en-espera",
                        "motivo": avance.motivo,
                        "reintentar_en_s": avance.reintentar_en_s,
                    }
                elif isinstance(avance, AvanceCerrada):
                    respuesta |= {"tipo": "cerrada"}
            almacen.escribir(estado)
            return respuesta

    async def _rebasar(
        self, almacen: Almacen, estado: EstadoLocal, orden: OrdenDeTrabajo
    ) -> tuple[EstadoLocal, Json, list[str]]:
        """Lleva el worktree al ``base_commit`` de la orden (la base de la unidad avanzó).

        ``rebasar_unidad`` se niega con cambios sin commit y aborta ante un conflicto: en los dos
        casos el error llega al arnés y nada de aquí se escribe. Tras un rebase sin error el
        ``base_commit`` nuevo se guarda ya, junto con la orden: git se movió y lo que quede en disco
        no puede seguir diciendo la base anterior. El worktree se toca fuera del bucle de eventos
        porque ``rebasar_unidad`` puede esperar a un ``git fetch``."""

        resultado = await asyncio.to_thread(
            rebasar_unidad, Path(estado.worktree), estado.rama, estado.base_commit, orden.base_commit
        )
        estado = estado.model_copy(update={"base_commit": orden.base_commit, "orden_en_curso": orden})
        almacen.escribir(estado)
        avisos = []
        if resultado.forzar_empuje:
            avisos.append(
                f"El rebase reescribió la rama {estado.rama}, que ya estaba en el remoto: el próximo "
                f"push exige `git -C {estado.worktree} push --force-with-lease` (nunca `--force`)."
            )
        rebase: Json = {
            "base_anterior": resultado.base_anterior,
            "base_nueva": resultado.base_nueva,
            "reaplicados": resultado.reaplicados,
            "movido": resultado.movido,
        }
        return estado, rebase, avisos

    def _respuesta_sin_conexion(self, estado: EstadoLocal, exc: SinConexion) -> Json:
        respuesta: Json = {
            "unidad": estado.unidad.unidad,
            "worktree": estado.worktree,
            "tipo": "sin-conexion",
            "detalle": str(exc),
            "eventos_pendientes": len(estado.cola_pendiente),
        }
        if estado.orden_en_curso is not None:
            respuesta["orden"] = _json(estado.orden_en_curso)
            respuesta["como_ejecutar"] = (
                "Sin conexión con el servidor. Puedes seguir ejecutando la orden en curso; "
                "el reporte se encola y los gates esperan a la reconexión."
            )
        return respuesta

    async def _refrescar_espejo(self, estado: EstadoLocal, version: int) -> EstadoLocal:
        if estado.espejo_remoto is not None and estado.espejo_remoto.version == version:
            return estado
        salida = await self.cliente.llamar(
            "unit.status", UnitStatusEntrada(unidad=estado.unidad), UnitStatusSalida
        )
        return estado.model_copy(update={"espejo_remoto": salida.estado})

    # --- unit.report ---------------------------------------------------------------------

    async def reportar(
        self,
        unidad: str | None = None,
        resultado: ResultadoOrden = ResultadoOrden.completado,
        tareas_completadas: list[str] | None = None,
        motivo: str | None = None,
        uso_modelo: UsoModeloArnes | None = None,
    ) -> Json:
        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.tomar_bloqueo(almacen.leer())
            orden = estado.orden_en_curso
            if orden is None:
                raise ErrorRailspec("No hay orden en curso: llama unit_advance para recibir la siguiente.")
            if any(
                isinstance(e.carga, OrdenReportada) and e.carga.orden_id == orden.id
                for e in estado.cola_pendiente
            ):
                raise ErrorRailspec("Esta orden ya está reportada y en cola; llama unit_advance.")
            worktree = Path(estado.worktree)
            completado = resultado == ResultadoOrden.completado
            ahora = self.reloj()
            avisos: list[str] = []
            local: Json = {}

            snapshot = None
            if orden.reporte_requerido.snapshot or (completado and isinstance(orden, OrdenImplementar)):
                construido = construir_snapshot(
                    worktree=worktree,
                    unidad=estado.unidad,
                    repositorio=estado.repositorio,
                    base_commit=estado.base_commit,
                    nivel=self.config.repo.nivel_codigo,
                    indexador=self.indexador,
                    snapshot_id=self.nuevo_id(),
                    ahora=ahora,
                )
                snapshot = construido.snapshot
                avisos += construido.avisos
                if construido.excluidos:
                    local["excluidos_del_snapshot"] = construido.excluidos
                if completado and isinstance(orden, OrdenImplementar):
                    _verificar_alcance(orden, [a.ruta for a in snapshot.archivos] + construido.excluidos)

            resultado_validacion = None
            if orden.reporte_requerido.validacion and completado and orden.comando_validacion:
                ejecucion = validacion.ejecutar(
                    worktree,
                    orden.comando_validacion,
                    self.config.repo.nivel_codigo,
                    str(orden.id),
                    self.config.repo.validacion_timeout_s,
                )
                resultado_validacion = ejecucion.resultado
                local["validacion"] = {
                    "codigo_salida": ejecucion.resultado.codigo_salida,
                    "log": str(ejecucion.log),
                    "cola_salida": ejecucion.salida_local,
                }

            artefacto = None
            if orden.reporte_requerido.artefacto and completado:
                artefacto = _leer_artefacto(worktree, orden)

            reporte = ReporteOrden(
                orden_id=orden.id,
                secuencia=orden.secuencia,
                unidad=estado.unidad,
                base_commit=estado.base_commit,
                resultado=resultado,
                reportado_en=ahora,
                snapshot=snapshot,
                validacion=resultado_validacion,
                artefacto=artefacto,
                tareas_completadas=tareas_completadas or [],
                motivo=motivo,
                uso_modelo=uso_modelo,
            )
            almacen.guardar_pendiente(reporte)
            estado = self._encolar(estado, reporte)
            almacen.escribir(estado)

            respuesta: Json = {"unidad": estado.unidad.unidad, "orden": str(orden.id)}
            if avisos:
                respuesta["avisos"] = avisos
            respuesta |= local
            try:
                estado, rechazos = await self._sincronizar(almacen, estado)
            except SinConexion as exc:
                respuesta |= {
                    "encolado": True,
                    "detalle": f"{exc}. El reporte queda en cola y se envía al reconectar.",
                    "siguiente": "unit_advance",
                }
                return respuesta
            if rechazos:
                respuesta |= {"aceptado": False, "rechazos": rechazos, "siguiente": "unit_advance"}
            else:
                respuesta |= {"aceptado": True, "siguiente": "unit_advance"}
            return respuesta

    # --- cola de sincronización -----------------------------------------------------------

    def _actor(self, estado: EstadoLocal) -> Actor:
        if estado.espejo_remoto is None:
            raise ErrorRailspec("Estado local sin espejo remoto; llama unit_status con conexión.")
        return estado.espejo_remoto.dueno.model_copy(update={"canal": Canal.arnes})

    def _evento(
        self, estado: EstadoLocal, secuencia: int, carga: Any, causa: UUID | None = None
    ) -> EventoSync:
        return EventoSync(
            id=self.nuevo_id(),
            direccion=Direccion.local_a_remoto,
            unidad=estado.unidad,
            secuencia=secuencia,
            emitido_en=self.reloj(),
            actor=self._actor(estado),
            causado_por=causa,
            carga=carga,
        )

    @staticmethod
    def _siguiente(estado: EstadoLocal) -> int:
        cola = estado.cola_pendiente
        return (cola[-1].secuencia if cola else estado.ultima_secuencia_confirmada) + 1

    def _encolar(self, estado: EstadoLocal, reporte: ReporteOrden) -> EstadoLocal:
        """Desde el contrato 1.3 el proxy numera la dirección local→remoto: el servidor
        no emite ``snapshot.subido`` ni ``orden.reportada`` al recibir ``unit.report``."""

        cola = list(estado.cola_pendiente)
        siguiente = self._siguiente(estado)
        causa = None
        if reporte.snapshot is not None:
            s = reporte.snapshot
            evento_snapshot = self._evento(
                estado,
                siguiente,
                SnapshotSubido(
                    snapshot_id=s.id,
                    repositorio=s.repositorio,
                    base_commit=s.base_commit,
                    hash_arbol=s.hash_arbol,
                ),
            )
            cola.append(evento_snapshot)
            causa = evento_snapshot.id
            siguiente += 1
        carga = OrdenReportada(orden_id=reporte.orden_id, secuencia_orden=reporte.secuencia)
        cola.append(self._evento(estado, siguiente, carga, causa))
        return estado.model_copy(update={"cola_pendiente": cola})

    def _detectar_empuje(self, almacen: Almacen, estado: EstadoLocal) -> EstadoLocal:
        """Encola ``commit.empujado`` si la rama de la unidad llegó al remoto con un commit nuevo.

        La referencia remota la actualiza git en cada ``push``; el webhook de la GitHub App
        sigue cubriendo las ramas empujadas desde otra máquina."""

        commit = git.commit_empujado(Path(estado.worktree), estado.rama)
        if commit is None or commit == almacen.leer_ultimo_empuje():
            return estado
        carga = CommitEmpujado(repositorio=estado.repositorio, rama=estado.rama, commit=commit)
        estado = estado.model_copy(
            update={
                "cola_pendiente": [
                    *estado.cola_pendiente,
                    self._evento(estado, self._siguiente(estado), carga),
                ]
            }
        )
        almacen.escribir(estado)
        almacen.escribir_ultimo_empuje(commit)
        return estado

    async def _sincronizar(self, almacen: Almacen, estado: EstadoLocal) -> tuple[EstadoLocal, list[Json]]:
        """Vacía la cola local→remoto en dos pasos.

        1. Cada reporte pendiente viaja por ``unit.report``. Si el servidor lo rechaza, el
           remoto gana: el reporte y sus avisos salen de la cola y se devuelven al arnés.
        2. Los avisos que quedan (``snapshot.subido`` y ``orden.reportada`` de reportes
           aceptados, ``commit.empujado``) suben en orden por ``sync.push``.

        Cada paso se persiste; una caída de red deja en disco lo que falte."""

        estado = self._detectar_empuje(almacen, estado)
        rechazos: list[Json] = []
        for evento in list(estado.cola_pendiente):
            if not isinstance(evento.carga, OrdenReportada):
                continue
            orden_id = evento.carga.orden_id
            reporte = almacen.leer_pendiente(orden_id)
            if reporte is None:
                continue  # ya aceptado; falta subir su aviso
            reenvio = almacen.fue_enviado(orden_id)
            almacen.marcar_enviado(orden_id)
            try:
                await self.cliente.llamar("unit.report", reporte, UnitReportSalida)
            except ErrorServidor as exc:
                if not self._ya_aceptado(exc):
                    rechazos.append(_rechazo(orden_id, exc, reenvio))
                    estado = self._quitar_de_cola(estado, evento)
            estado = self._orden_resuelta(estado, orden_id)
            almacen.escribir(estado)
            almacen.borrar_pendiente(orden_id)
        return await self._subir_avisos(almacen, estado), rechazos

    @staticmethod
    def _ya_aceptado(exc: ErrorServidor) -> bool:
        """``secuencia-duplicada`` en un reenvío: el servidor ya tenía el reporte."""

        return exc.codigo == CodigoError.secuencia_duplicada

    @staticmethod
    def _orden_resuelta(estado: EstadoLocal, orden_id: UUID) -> EstadoLocal:
        if estado.orden_en_curso is not None and estado.orden_en_curso.id == orden_id:
            return estado.model_copy(update={"orden_en_curso": None})
        return estado

    @staticmethod
    def _quitar_de_cola(estado: EstadoLocal, evento: EventoSync) -> EstadoLocal:
        """Saca un reporte rechazado y su ``snapshot.subido`` y renumera lo que sigue.

        Renumerar es seguro: los avisos solo suben después de resolver todos los
        reportes, así que nada de la cola ha llegado aún al servidor."""

        fuera = {evento.id, evento.causado_por}
        quedan = [e for e in estado.cola_pendiente if e.id not in fuera]
        base = estado.ultima_secuencia_confirmada
        cola = [e.model_copy(update={"secuencia": base + i}) for i, e in enumerate(quedan, start=1)]
        return estado.model_copy(update={"cola_pendiente": cola})

    async def _subir_avisos(self, almacen: Almacen, estado: EstadoLocal) -> EstadoLocal:
        while estado.cola_pendiente:
            lote = estado.cola_pendiente[:MAX_EVENTOS_SYNC]
            salida = await self.cliente.llamar(
                "sync.push",
                SyncPushEntrada(unidad=estado.unidad, eventos=[_subida(e) for e in lote]),
                SyncPushSalida,
            )
            quedan = [e for e in estado.cola_pendiente if e.secuencia > salida.confirmada_hasta]
            if len(quedan) == len(estado.cola_pendiente):
                raise ErrorRailspec(
                    f"sync.push no confirmó ningún evento (confirmada_hasta={salida.confirmada_hasta})."
                )
            estado = estado.model_copy(
                update={
                    "cola_pendiente": quedan,
                    "ultima_secuencia_confirmada": max(
                        salida.confirmada_hasta, estado.ultima_secuencia_confirmada
                    ),
                }
            )
            almacen.escribir(estado)
        return estado

    async def _traer_eventos(self, estado: EstadoLocal) -> tuple[EstadoLocal, int]:
        """``sync.pull``: avanza ``ultima_secuencia_recibida``. El espejo se refresca aparte
        con ``unit.status``, que trae el estado completo; los eventos solo avisan del cambio."""

        recibidos = 0
        while True:
            salida = await self.cliente.llamar(
                "sync.pull",
                SyncPullEntrada(unidad=estado.unidad, desde=estado.ultima_secuencia_recibida),
                SyncPullSalida,
            )
            recibidos += len(salida.eventos)
            ultima = salida.eventos[-1].secuencia if salida.eventos else estado.ultima_secuencia_recibida
            estado = estado.model_copy(update={"ultima_secuencia_recibida": ultima})
            if not salida.hay_mas:
                return estado, recibidos

    async def sincronizar(self, unidad: str | None = None) -> Json:
        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.leer()
            estado, rechazos = await self._sincronizar(almacen, estado)
            estado, recibidos = await self._traer_eventos(estado)
            almacen.escribir(estado)
            return {
                "unidad": estado.unidad.unidad,
                "pendientes": len(estado.cola_pendiente),
                "eventos_recibidos": recibidos,
                "rechazos": rechazos,
            }

    # --- resto de tools -----------------------------------------------------------------------

    async def estado(self, unidad: str | None = None) -> Json:
        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.leer()
            try:
                salida = await self.cliente.llamar(
                    "unit.status", UnitStatusEntrada(unidad=estado.unidad), UnitStatusSalida
                )
            except SinConexion as exc:
                return {"sin_conexion": str(exc), "local": _resumen_local(estado)}
            estado = estado.model_copy(update={"espejo_remoto": salida.estado})
            almacen.escribir(estado)
            return {"estado": _json(salida.estado), "local": _resumen_local(estado)}

    def checkpoint_pendiente(self, unidad: str | None = None) -> Checkpoint:
        """Checkpoint pendiente según el espejo remoto (lo refresca ``unit_advance``)."""

        estado = self._almacen(unidad).leer()
        checkpoint = estado.espejo_remoto.checkpoint_pendiente if estado.espejo_remoto else None
        if checkpoint is None:
            raise ErrorRailspec("No hay checkpoint pendiente; llama unit_advance.")
        return checkpoint

    async def aprobar(
        self, unidad: str | None, checkpoint: UUID, decision: Decision, comentario: str | None = None
    ) -> Json:
        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.leer()
            salida = await self.cliente.llamar(
                "unit.approve",
                UnitApproveEntrada(
                    unidad=estado.unidad, checkpoint=checkpoint, decision=decision, comentario=comentario
                ),
                EstadoSalida,
            )
            almacen.escribir(estado.model_copy(update={"espejo_remoto": salida.estado}))
            return {
                "unidad": estado.unidad.unidad,
                "fase": salida.estado.fase.value,
                "siguiente": "unit_advance",
            }

    async def cambiar_modo(self, unidad: str | None, modo: Modo, motivo: str) -> Json:
        """``unit.set_mode`` (1.2): solo a petición explícita del humano; el servidor lo admite
        tras research o tras el checkpoint del spec y responde ``conversion-no-permitida`` si no."""

        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.leer()
            version = estado.espejo_remoto.version if estado.espejo_remoto else 1
            salida = await self.cliente.llamar(
                "unit.set_mode",
                UnitSetModeEntrada(unidad=estado.unidad, modo=modo, motivo=motivo, version_vista=version),
                EstadoSalida,
            )
            almacen.escribir(estado.model_copy(update={"espejo_remoto": salida.estado}))
            return {
                "unidad": estado.unidad.unidad,
                "modo": salida.estado.modo.value,
                "siguiente": "unit_advance",
            }

    async def integrar(
        self,
        unidad: str | None,
        especificacion_viva: str,
        pr_url: str | None = None,
        commit_integrado: str | None = None,
    ) -> Json:
        """``unit.integrate``. El commit resultante en la rama destino (1.4) lo da el arnés o, si
        no, es la punta de la rama por defecto del remoto: el servidor conserva la superposición
        de la unidad en el grafo hasta que el índice canónico alcance ese commit. Sin remoto o sin
        red no se manda y el servidor la descarta al integrar."""

        almacen = self._almacen(unidad)
        with almacen.cerrojo():
            estado = almacen.leer()
            if commit_integrado is None:
                commit_integrado = await asyncio.to_thread(git.punta_de_destino, Path(estado.worktree))
            salida = await self.cliente.llamar(
                "unit.integrate",
                UnitIntegrateEntrada(
                    unidad=estado.unidad,
                    especificacion_viva=especificacion_viva,
                    pr_url=pr_url,
                    commit_integrado=commit_integrado,
                ),
                EstadoSalida,
            )
            almacen.escribir(estado.model_copy(update={"espejo_remoto": salida.estado}))
            return {
                "unidad": estado.unidad.unidad,
                "integrada": salida.estado.integracion is not None,
                "commit_integrado": commit_integrado,
            }

    async def listar(self, **filtros: Any) -> Json:
        entrada = UnitListEntrada(alcance=self._alcance_ws(), **filtros)
        salida = await self.cliente.llamar("unit.list", entrada, UnitListSalida)
        locales = self.unidades_locales()
        filas = []
        for u in salida.unidades:
            fila = _json(u)
            if u.unidad in locales:
                fila["worktree"] = str(locales[u.unidad])
            filas.append(fila)
        return {"unidades": filas, "cursor_siguiente": salida.cursor_siguiente}

    async def consultar_grafo(
        self,
        consulta: Json,
        repositorios: list[str] | None = None,
        unidad: str | None = None,
        limite: int = 25,
    ) -> Json:
        preparada, avisos = self._preparar_consulta_grafo(consulta)
        entrada = GraphQueryEntrada(
            alcance=self._alcance_ws(),
            repositorios=repositorios or [],
            unidad=unidad,
            consulta=preparada,
            limite=limite,
        )
        salida = await self.cliente.llamar("graph.query", entrada, GraphQuerySalida)
        respuesta: Json = _json(salida)
        if avisos:
            respuesta["avisos"] = avisos
        return respuesta

    def _preparar_consulta_grafo(self, consulta: Json) -> tuple[Json, list[str]]:
        """En una búsqueda semántica el vector de la consulta se calcula en local (contrato 1.1):
        el texto de la consulta no necesita salir para que el servidor compare vectores.

        Si no hay vector la búsqueda sigue, pero el servidor solo puede resolverla por texto: se
        avisa al arnés en vez de dejarle creer que los resultados son por similitud."""

        if not (
            consulta.get("verbo") == "search" and consulta.get("semantica") and "vector_b64" not in consulta
        ):
            return consulta, []
        vector = (
            None if self.indexador is None else self.indexador.embedding_consulta(consulta.get("texto", ""))
        )
        if vector is not None:
            return {
                **consulta,
                "vector_b64": base64.b64encode(vector).decode(),
                "modelo_embedding": EMBEDDING_MODELO,
            }, []
        causa = (
            "no hay indexador local que calcule el vector"
            if self.indexador is None
            else "el indexador local no calcula embeddings de consulta"
        )
        return consulta, [
            f"La búsqueda semántica no lleva vector de consulta ({causa}): sin él el servidor busca por "
            "texto (salvo que tenga su propio codificador), así que los resultados pueden no ser por "
            "similitud. Busca por nombre (`resolve`) o con texto literal."
        ]

    async def traer_insumo(self, insumo_id: UUID, unidad: str | None = None) -> Json:
        """``railspec insumo pull``: escribe el insumo en el worktree de la unidad (o en la raíz)."""

        salida = await self.cliente.llamar(
            "insumo.get", InsumoGetEntrada(alcance=self._alcance_ws(), id=insumo_id), InsumoGetSalida
        )
        destino = Path(self._almacen(unidad).worktree) if unidad else self.raiz
        if unidad is None:
            git.excluir_localmente(self.raiz, EXCLUIR_DE_GIT)
        ruta = insumos.escribir(destino, salida.insumo)
        return {"insumo": str(insumo_id), "ruta": str(ruta), "objetivo": salida.insumo.objetivo}


# --- utilidades -------------------------------------------------------------------------------


def _subida(evento: EventoSync) -> EventoSubida:
    return EventoSubida(
        id=evento.id,
        secuencia=evento.secuencia,
        emitido_en=evento.emitido_en,
        causado_por=evento.causado_por,
        carga=evento.carga,
    )


def _rechazo(orden_id: UUID, exc: ErrorServidor, reenvio: bool) -> Json:
    rechazo: Json = {"orden": str(orden_id), "codigo": exc.codigo.value, "detalle": exc.error.detalle}
    if reenvio and exc.codigo == CodigoError.orden_no_vigente:
        # unit.report no es idempotente: si el primer envío llegó y se perdió la respuesta,
        # el reenvío ya no encuentra la orden vigente. El servidor manda en ambos casos.
        rechazo["incierto"] = (
            "Un envío anterior quedó sin respuesta; puede que el servidor ya tuviera este reporte. "
            "Llama unit_advance: la orden siguiente lo refleja."
        )
    return rechazo


def _verificar_alcance(orden: OrdenImplementar, rutas: list[str]) -> None:
    fuera = [
        r for r in rutas if not coincide(r, orden.alcance.permitidos) or coincide(r, orden.alcance.prohibidos)
    ]
    if fuera:
        raise FueraDeAlcance(sorted(fuera))


def _leer_artefacto(worktree: Path, orden: Any) -> ArtefactoRedactado:
    assert isinstance(orden, OrdenRedactar | OrdenRefinar)
    ruta = worktree / orden.ruta_artefacto
    if not ruta.is_file():
        raise ErrorRailspec(
            f"La orden pide el artefacto en {orden.ruta_artefacto} y no existe en el worktree."
        )
    contenido = ruta.read_text(encoding="utf-8")
    return ArtefactoRedactado(
        tipo=orden.artefacto,
        contenido=contenido,
        sha256=hashlib.sha256(contenido.encode("utf-8")).hexdigest(),
    )


def _como_ejecutar(orden: Any) -> str:
    base = "Trabaja solo dentro del worktree de la unidad. "
    if isinstance(orden, OrdenImplementar):
        return base + (
            "Implementa las tareas de la orden tocando solo archivos de alcance.permitidos; "
            "luego llama unit_report con las tareas completadas. El proxy construye el snapshot y "
            "corre la validación; no subas diffs ni fragmentos tú mismo."
        )
    if isinstance(orden, OrdenRedactar | OrdenRefinar):
        return base + (
            f"Escribe el artefacto en {orden.ruta_artefacto} siguiendo las instrucciones y luego "
            "llama unit_report; el proxy lo lee del disco."
        )
    return base + "Llama unit_report: el proxy corre el comando de validación en local y reporta la salida."


def _resumen_local(estado: EstadoLocal) -> Json:
    return {
        "worktree": estado.worktree,
        "rama": estado.rama,
        "base_commit": estado.base_commit,
        "orden_en_curso": str(estado.orden_en_curso.id) if estado.orden_en_curso else None,
        "eventos_pendientes": len(estado.cola_pendiente),
        "ultima_secuencia_confirmada": estado.ultima_secuencia_confirmada,
    }
