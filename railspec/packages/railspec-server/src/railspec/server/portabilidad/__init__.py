"""``unit.import`` y ``unit.export`` (contrato 1.4): unidades portables ``railspec.unidad/v1``.

Importar crea una unidad nueva desde un paquete (una unidad del kit SDD que el
proxy convirtió, u otra exportada por Railspec). Los artefactos del paquete
quedan aprobados por importación a nombre del humano que importa y el DAG
retoma en ``fase_retomar`` (``dag.Importacion``); el primero que no encaje en la
plantilla vuelve a refinar. Es idempotente por (workspace, repositorio
primario, origen): el almacén reserva el origen para la unidad antes de crearla.
Cada importación queda en la auditoría con su origen y cuántos artefactos
entraron aprobados por importación.

Exportar devuelve el paquete de una unidad: su estado y los artefactos que el
DAG guarda en su checkpoint, aprobados como prefijo según la fase.
"""

from __future__ import annotations

import hashlib
from typing import Any

from railspec.contracts.comun import (
    MODOS_CON_MANDATO,
    Actor,
    AlcanceUnidad,
    EstadoFase,
    Fase,
    Modo,
    Perfil,
    Presupuesto,
    RolRepositorio,
)
from railspec.contracts.estado import Consumo, ConversionModo, EstadoUnidad, Rehabilitacion, RepositorioUnidad
from railspec.contracts.orden import Artefacto
from railspec.contracts.portabilidad import ArtefactosPaquete, GateImportado, OrigenPaquete, PaqueteUnidad
from railspec.contracts.reporte import ArtefactoRedactado
from railspec.contracts.repositorio import EventoAuditoria, RegistroAuditoria
from railspec.contracts.tools import (
    UnitExportEntrada,
    UnitExportSalida,
    UnitImportEntrada,
    UnitImportSalida,
    UnitStartEntrada,
)

from ..estado.checkpoints import nombre_workflow
from ..motor import dag
from ..motor.motor import Motor, negociar, slug, triaje

#: Artefactos aprobados que implica cada fase (los mismos tramos que ``FASES_RETOMAR``).
_APROBADOS_EN = {
    Fase.research: 0,
    Fase.spec: 0,
    Fase.plan: 1,
    Fase.tasks: 2,
    Fase.aprobacion: 3,
    Fase.implement: 3,
    Fase.done: 3,
}
_ORDEN = (Artefacto.spec, Artefacto.plan, Artefacto.tasks)
_FASE_DE = {Artefacto.spec: Fase.spec, Artefacto.plan: Fase.plan, Artefacto.tasks: Fase.tasks}


class Portabilidad:
    def __init__(self, motor: Motor) -> None:
        self.motor = motor
        self.n = motor.n

    # --- unit.import ---------------------------------------------------------------------

    async def importar(self, e: UnitImportEntrada, actor: Actor) -> UnitImportSalida:
        negociada = negociar(e.version_contrato_cliente)
        self.motor._humano(actor, "importar una unidad")
        paquete = e.paquete
        almacen = self.n.almacen
        nueva = f"{almacen.siguiente_numero_unidad(e.alcance):04d}-{slug(paquete.titulo)}"
        reservada = almacen.reclamar_importacion(
            e.alcance, e.repositorios[0].repositorio, paquete.origen.tipo, paquete.origen.id_original, nueva
        )
        alcance = AlcanceUnidad(org=e.alcance.org, workspace=e.alcance.workspace, unidad=reservada)
        existente = almacen.obtener_estado(alcance)
        if existente is not None:
            return UnitImportSalida(estado=existente, ya_existia=True, version_contrato_negociada=negociada)
        # Si la reserva es de un intento anterior que no llegó a crear la unidad, se crea con ese id.
        almacen.guardar_estado(self._estado_inicial(e, alcance, actor), None)
        ahora = self.n.reloj()
        almacen.registrar_auditoria(
            RegistroAuditoria(
                id=self.n.nuevo_id(),
                alcance=e.alcance,
                evento=EventoAuditoria.importacion,
                actor=actor,
                en=ahora,
                repositorio=e.repositorios[0].repositorio,
                unidad=alcance.unidad,
                origen_importacion=paquete.origen,
                artefactos_importados=paquete.artefactos.presentes(),
            )
        )
        rehabilitacion = None
        if paquete.fase_retomar == Fase.done:
            rehabilitacion = Rehabilitacion(
                actor=actor,
                en=ahora,
                motivo=(
                    f"Unidad cerrada importada de {paquete.origen.tipo} {paquete.origen.id_original}: "
                    "su gate de código no corrió en Railspec."
                ),
            )
        arranque = dag.Importacion(
            pedido=paquete.pedido,
            artefactos={t.value: a.contenido for t in _ORDEN if (a := getattr(paquete.artefactos, t.value))},
            fase=paquete.fase_retomar,
            rehabilitacion=rehabilitacion,
        )
        await self.motor.procesar(alcance, arranque=arranque)
        return UnitImportSalida(
            estado=self.motor._estado(alcance), ya_existia=False, version_contrato_negociada=negociada
        )

    def _estado_inicial(self, e: UnitImportEntrada, alcance: AlcanceUnidad, actor: Actor) -> EstadoUnidad:
        paquete = e.paquete
        ahora = self.n.reloj()
        modo = paquete.modo or Modo.interactivo
        conversiones = []
        if modo != Modo.interactivo:
            conversiones.append(
                ConversionModo(
                    de=Modo.interactivo,
                    a=modo,
                    actor=actor,
                    en=ahora,
                    motivo="modo del paquete importado",
                    tras=None,
                )
            )
        # El triaje del servidor decide el riesgo igual que en unit.start; el del paquete es una sugerencia.
        riesgo = triaje(
            UnitStartEntrada(
                alcance=e.alcance,
                repositorios=e.repositorios,
                titulo=paquete.titulo,
                pedido=paquete.pedido,
                riesgo_sugerido=paquete.riesgo,
                version_contrato_cliente=e.version_contrato_cliente,
            )
        )
        presupuesto = self.n.almacen.presupuesto(e.alcance)
        return EstadoUnidad(
            unidad=alcance,
            version=1,
            titulo=paquete.titulo,
            pedido=paquete.pedido,
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
            # Una unidad cerrada pasa por implement solo hasta que el DAG la cierra (done exige completado).
            fase=Fase.implement if paquete.fase_retomar == Fase.done else paquete.fase_retomar,
            estado=EstadoFase.en_progreso,
            modo=modo,
            modo_conversion=conversiones,
            riesgo=riesgo,
            perfil=paquete.perfil or Perfil.estandar,
            governance_refs=list(paquete.governance_refs),
            comando_validacion=paquete.comando_validacion,
            presupuesto=presupuesto.por_unidad if presupuesto else Presupuesto(),
            consumo=Consumo(),
            creado_en=ahora,
            actualizado_en=ahora,
            actualizado_por=actor,
        )

    # --- unit.export ---------------------------------------------------------------------

    async def exportar(self, e: UnitExportEntrada, actor: Actor) -> UnitExportSalida:
        estado = self.motor._estado(e.unidad)
        textos = await self._artefactos(estado.unidad)
        fase = estado.fase
        aprobados: dict[str, ArtefactoRedactado] = {}
        for tipo in _ORDEN[: _APROBADOS_EN[fase]]:
            if tipo.value not in textos:
                fase = _FASE_DE[tipo]
                break
            texto = textos[tipo.value]
            aprobados[tipo.value] = ArtefactoRedactado(
                tipo=tipo, contenido=texto, sha256=hashlib.sha256(texto.encode("utf-8")).hexdigest()
            )
        primario = next(r for r in estado.repositorios if r.rol == RolRepositorio.primario)
        paquete = PaqueteUnidad(
            origen=OrigenPaquete(
                tipo="railspec", id_original=estado.unidad.unidad, repositorio=primario.repositorio
            ),
            titulo=estado.titulo,
            pedido=estado.pedido or estado.titulo,
            artefactos=ArtefactosPaquete(**aprobados),
            fase_retomar=fase,
            modo=None if estado.modo in MODOS_CON_MANDATO else estado.modo,
            riesgo=estado.riesgo,
            perfil=estado.perfil,
            governance_refs=list(estado.governance_refs),
            comando_validacion=estado.comando_validacion,
            depende_de_original=list(estado.depende_de),
            historial_gates=[
                GateImportado(
                    gate=g.value,
                    resultado=r.veredicto.value,
                    iteraciones=r.iteraciones,
                    cerrado_en=r.cerrado_en,
                )
                for g, r in estado.gates.items()
            ],
        )
        return UnitExportSalida(paquete=paquete)

    async def _artefactos(self, alcance: AlcanceUnidad) -> dict[str, str]:
        cp = await self.motor.checkpoints.get_latest(workflow_name=nombre_workflow(alcance))
        crudo = (cp.state or {}).get(dag.CLAVE_DATOS) if cp is not None else None
        return dict(dag.DatosUnidad.model_validate(crudo).artefactos) if crudo else {}


def manejadores(motor: Motor) -> dict[str, Any]:
    """Para ``Registro.del_motor(..., extra=...)``."""

    p = Portabilidad(motor)
    return {"unit.import": p.importar, "unit.export": p.exportar}
