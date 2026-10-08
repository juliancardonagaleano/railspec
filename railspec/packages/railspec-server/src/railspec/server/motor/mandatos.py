"""Mandato de los modos supervisado y desatendido (contrato 1.11).

Un mandato es la aprobación única, acotada y con fecha de caducidad bajo la que corren varias unidades
sin checkpoints de spec, plan ni paquete (``railspec/docs/mandato.md``). Este módulo lo impone con el
estado, no con una conversación:

- ``retencion`` dice si el mandato de una unidad ampara trabajo ahora. Comprueba la caducidad y el
  presupuesto total y, al verlos pasados, deja el mandato ``parado`` (la parada se escribe aquí, una vez).
  El motor la consulta en cada paso: sin amparo no entrega entradas, no corre gates ni emite órdenes.
- ``politica_escalado`` decide qué detiene un gate rojo: en supervisado, el mandato entero; en desatendido,
  solo la unidad (se difiere) salvo que la causa afecte a todas.
- ``tratar_parada`` tipifica la parada de una orden fallida o bloqueada y reintenta sola las que el mandato
  delega; ``rutas_fuera`` aplica el alcance; ``validar_decisiones`` y ``registrar_decisiones`` guardan las
  decisiones delegadas para su revisión humana.
- Los manejadores de las tools ``mandate.*`` (``propose``, ``approve``, ``revoke``, ``review``, ``get``,
  ``list``). Aprobar y revisar son siempre un acto humano desde la consola.

Una unidad cuyo modo no es supervisado ni desatendido no pasa por nada de esto.
"""

from __future__ import annotations

import fnmatch
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import timedelta
from types import SimpleNamespace
from typing import Any

from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import (
    MODOS_CON_MANDATO,
    Actor,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Canal,
    CausaEscalado,
    Modo,
    TipoActor,
)
from railspec.contracts.estado import Consumo, EstadoUnidad
from railspec.contracts.mandato import (
    DELEGACION_REINTENTO,
    AprobacionMandato,
    CausaParada,
    DecisionDelegada,
    DecisionPropuesta,
    EstadoMandato,
    Mandato,
    MandatoEnOrden,
    ParadaMandato,
    RevisionDecision,
    RevocacionMandato,
    TipoDelegacion,
)
from railspec.contracts.reporte import ReporteOrden, ResultadoOrden
from railspec.contracts.repositorio import EventoAuditoria, RegistroAuditoria
from railspec.contracts.tools import (
    MAX_DECISIONES_EN_MANDATO,
    CodigoError,
    DecisionDeUnidad,
    EstadoSalida,
    MandateApproveEntrada,
    MandateGetEntrada,
    MandateGetSalida,
    MandateListEntrada,
    MandateListSalida,
    MandateProposeEntrada,
    MandateReviewEntrada,
    MandateRevokeEntrada,
    MandateSalida,
    ResumenMandato,
    UnidadDeMandato,
    UnitStartEntrada,
)

from ..proveedores.base import Uso
from . import presupuesto
from .errores import ErrorNegocio
from .nucleo import Nucleo
from .ordenes import glob_artefactos
from .perfiles import ACTOR_SERVIDOR

log = logging.getLogger("railspec.motor")

#: Prefijos del motivo de ``Nucleo.presupuesto_agotado`` que afectan a todo el mandato, no solo a la unidad.
PREFIJO_PRESUPUESTO_MANDATO = "por_mandato"
_PREFIJOS_COMUNES = (f"({PREFIJO_PRESUPUESTO_MANDATO}", "(mensual_usd")


def es_comun(causa: CausaEscalado, motivo: str) -> bool:
    """¿La causa del escalado afecta a todas las unidades del mandato y no solo a la que escaló?

    Sin gobernanza y el error del proveedor no dependen de la unidad; el presupuesto, solo si es el del
    mandato o el del mes (el de la unidad y el de la fase son de la unidad).
    """

    if causa in (CausaEscalado.sin_gobernanza, CausaEscalado.error_proveedor):
        return True
    return causa == CausaEscalado.presupuesto_agotado and any(p in motivo for p in _PREFIJOS_COMUNES)


@dataclass(frozen=True)
class Retencion:
    """Por qué el mandato de una unidad no ampara trabajo ahora."""

    mandato: str
    causa: CausaParada | None
    detalle: str


@dataclass(frozen=True)
class PoliticaEscalado:
    """Qué hace el mandato con un gate que escaló."""

    diferido: bool = False
    causa: CausaParada | None = None


@dataclass(frozen=True)
class AccionParada:
    """Qué hacer con una orden que el arnés no completó."""

    reintentar: bool = False
    causa: CausaParada | None = None


def gobernada(estado: EstadoUnidad) -> bool:
    """¿Corre esta unidad bajo un mandato? (modo supervisado o desatendido; ``plan`` lo exige el contrato)."""

    return estado.modo in MODOS_CON_MANDATO and estado.unidad.plan is not None


class Mandatos:
    def __init__(self, nucleo: Nucleo) -> None:
        self.n = nucleo
        #: Lo fija el ``Motor``: entrega las entradas pendientes de una unidad (``Motor._reanudar``).
        self.reanudar: Callable[[AlcanceUnidad], Awaitable[None]] | None = None

    # --- lectura ---------------------------------------------------------------------------------

    @staticmethod
    def _ws(org: str, workspace: str) -> AlcanceWorkspace:
        return AlcanceWorkspace(org=org, workspace=workspace)

    def obtener(self, alcance: AlcanceWorkspace, id_: str) -> Mandato | None:
        return self.n.almacen.obtener_mandato(alcance, id_)

    def de_unidad(self, estado: EstadoUnidad) -> Mandato | None:
        if estado.unidad.plan is None:
            return None
        return self.obtener(self._ws(estado.unidad.org, estado.unidad.workspace), estado.unidad.plan)

    def unidades(self, alcance: AlcanceWorkspace, plan: str) -> list[EstadoUnidad]:
        return self.n.almacen.estados_de_plan(alcance, plan)

    @staticmethod
    def consumo(unidades: Iterable[EstadoUnidad]) -> Consumo:
        total = Consumo()
        for u in unidades:
            c = u.consumo
            total = Consumo(
                tokens=total.tokens + c.tokens,
                segundos=total.segundos + c.segundos,
                costo_usd=round(total.costo_usd + c.costo_usd, 6),
                llamadas=total.llamadas + c.llamadas,
            )
        return total

    def exceso_presupuesto(self, m: Mandato, unidades: Iterable[EstadoUnidad] | None = None) -> str | None:
        """El tope del presupuesto total que el consumo de las unidades del mandato alcanza, o ``None``."""

        tope = m.contenido.limites.presupuesto
        if all(v is None for v in tope.model_dump().values()):
            return None
        if unidades is None:
            unidades = self.unidades(m.alcance, m.id)
        return presupuesto.exceso(self.consumo(unidades), tope)

    def presupuesto_agotado(self, estado: EstadoUnidad, gastado: Uso) -> str | None:
        """El tope del mandato visto desde una llamada de ``estado`` (con lo que ya gastó su panel).

        Suma el consumo guardado de las demás unidades, el de ésta tal como la lee el gate y ``gastado``.
        """

        m = self.de_unidad(estado) if gobernada(estado) else None
        if m is None:
            return None
        resto = [u for u in self.unidades(m.alcance, m.id) if u.unidad.unidad != estado.unidad.unidad]
        consumo = presupuesto.con_uso(self.consumo([*resto, estado]), gastado)
        motivo = presupuesto.exceso(consumo, m.contenido.limites.presupuesto)
        return f"{PREFIJO_PRESUPUESTO_MANDATO}: {motivo}" if motivo else None

    # --- vigencia y paradas del mandato ------------------------------------------------------------

    def _auditar(
        self, mandato: Mandato, actor: Actor, accion: str, unidad: str | None = None, **detalle: Any
    ) -> None:
        self.n.almacen.registrar_auditoria(
            RegistroAuditoria(
                id=self.n.nuevo_id(),
                alcance=mandato.alcance,
                evento=EventoAuditoria.cambio_mandato,
                actor=actor,
                en=self.n.reloj(),
                unidad=unidad,
                detalle={
                    "accion": accion,
                    "mandato": mandato.id,
                    "version": mandato.version,
                    **{k: v for k, v in detalle.items() if v is not None},
                },
            )
        )

    def detener(self, m: Mandato, causa: CausaParada, unidad: str | None, detalle: str) -> Mandato:
        """Deja ``parado`` un mandato ``aprobado`` y lo audita. Idempotente: ya parado, no cambia."""

        for _ in range(4):
            if m.estado != EstadoMandato.aprobado:
                return m
            ahora = self.n.reloj()
            nuevo = m.model_copy(
                update={
                    "estado": EstadoMandato.parado,
                    "parada": ParadaMandato(causa=causa, unidad=unidad, detalle=detalle[:1000], en=ahora),
                    "actualizado_en": ahora,
                    "actualizado_por": ACTOR_SERVIDOR,
                }
            )
            try:
                guardado = self.n.almacen.guardar_mandato(nuevo, m.version)
            except ConflictoVersion:
                m = self.obtener(m.alcance, m.id) or m
                continue
            self._auditar(
                guardado, ACTOR_SERVIDOR, "parado", unidad, causa=causa.value, detalle=detalle[:300]
            )
            return guardado
        return m

    def _al_dia(self, m: Mandato) -> Mandato:
        """Aplica al mandato lo que el reloj y el consumo ya decidieron: caducidad y presupuesto total."""

        if m.estado != EstadoMandato.aprobado:
            return m
        if not m.vigente(self.n.reloj()):
            hasta = m.vigente_hasta
            return self.detener(
                m, CausaParada.mandato_caducado, None, f"la aprobación caducó el {hasta:%Y-%m-%d %H:%M} UTC"
            )
        exceso = self.exceso_presupuesto(m)
        if exceso:
            return self.detener(
                m, CausaParada.presupuesto_mandato, None, f"presupuesto total alcanzado: {exceso}"
            )
        return m

    def vigente(self, alcance: AlcanceWorkspace, id_: str) -> tuple[Mandato | None, str | None]:
        """El mandato al día y, si no ampara trabajo, por qué; ``(None, motivo)`` si no existe."""

        m = self.obtener(alcance, id_)
        if m is None:
            return None, f"el mandato {id_} no existe en este workspace"
        m = self._al_dia(m)
        return m, m.motivo_no_vigente(self.n.reloj())

    def retencion(self, estado: EstadoUnidad) -> Retencion | None:
        """Si el mandato de la unidad no la ampara ahora, por qué; ``None`` si puede avanzar."""

        if not gobernada(estado):
            return None
        plan = estado.unidad.plan
        m, motivo = self.vigente(self._ws(estado.unidad.org, estado.unidad.workspace), plan)
        if m is None:
            return Retencion(plan, None, motivo)
        if motivo is None and m.contenido.modo != estado.modo:
            motivo = f"la unidad es {estado.modo.value} y el mandato ampara {m.contenido.modo.value}"
        if motivo is None:
            return None
        causa = m.parada.causa if m.parada else None
        if m.estado == EstadoMandato.revocado:
            causa = CausaParada.mandato_revocado
        elif m.estado == EstadoMandato.aprobado and causa is None:
            causa = CausaParada.mandato_caducado
        return Retencion(m.id, causa, motivo)

    # --- arranque y conversión ----------------------------------------------------------------------

    def validar_arranque(self, e: UnitStartEntrada) -> None:
        """``unit.start`` en supervisado o desatendido: mandato vigente y unidad dentro de sus límites."""

        m, motivo = self.vigente(e.alcance, e.plan)
        if motivo:
            raise ErrorNegocio(CodigoError.mandato_no_vigente, motivo)
        assert m is not None
        self._dentro_de_limites(m, e.modo, [r.repositorio for r in e.repositorios])
        n = len(self.unidades(e.alcance, m.id))
        if n >= m.contenido.limites.max_unidades:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                f"el mandato {m.id} ampara a lo sumo {m.contenido.limites.max_unidades} unidades "
                f"y ya tiene {n}",
            )

    def validar_conversion(self, estado: EstadoUnidad, modo: Modo) -> None:
        """Convertir a supervisado o desatendido: mandato vigente, mismo modo y mismos repositorios."""

        m, motivo = self.vigente(self._ws(estado.unidad.org, estado.unidad.workspace), estado.unidad.plan)
        if motivo:
            raise ErrorNegocio(CodigoError.mandato_no_vigente, motivo, estado.version)
        assert m is not None
        self._dentro_de_limites(m, modo, [r.repositorio for r in estado.repositorios], estado.version)

    @staticmethod
    def _dentro_de_limites(
        m: Mandato, modo: Modo, repositorios: list[str], version: int | None = None
    ) -> None:
        if modo != m.contenido.modo:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                f"el mandato {m.id} ampara unidades {m.contenido.modo.value}, no {modo.value}",
                version,
            )
        fuera = [r for r in repositorios if r not in m.contenido.limites.repositorios]
        if fuera:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                f"el mandato {m.id} no ampara los repositorios: {', '.join(fuera)}",
                version,
            )

    # --- lo que viaja en las órdenes ----------------------------------------------------------------

    def en_orden(self, estado: EstadoUnidad) -> MandatoEnOrden | None:
        """El mandato de la unidad tal como lo ve el arnés; ``None`` si no corre bajo uno aprobado."""

        m = self.de_unidad(estado) if gobernada(estado) else None
        if m is None or m.vigente_hasta is None:
            return None
        limites = m.contenido.limites
        return MandatoEnOrden(
            mandato=m.id,
            modo=m.contenido.modo,
            caduca_en=m.vigente_hasta,
            delegaciones=m.contenido.delegaciones,
            rutas_permitidas=limites.rutas_permitidas,
            reintentos_parada=limites.reintentos_parada,
        )

    # --- alcance -----------------------------------------------------------------------------------

    def rutas_fuera(self, estado: EstadoUnidad, rutas: Iterable[str]) -> list[str]:
        """Las rutas que el mandato no permite (siempre se admiten los artefactos de la propia unidad)."""

        if not gobernada(estado):
            return []
        m = self.de_unidad(estado)
        permitidas = m.contenido.limites.rutas_permitidas if m else []
        if not permitidas:
            return []
        patrones = [*permitidas, glob_artefactos(estado)]
        return sorted({r for r in rutas if not any(fnmatch.fnmatch(r, p) for p in patrones)})

    # --- gates rojos y paradas de orden ---------------------------------------------------------------

    def politica_escalado(self, estado: EstadoUnidad, causa: CausaEscalado, motivo: str) -> PoliticaEscalado:
        """Qué detiene un gate que escaló. Se llama una vez, al escalar, y escribe la parada del mandato."""

        if not gobernada(estado):
            return PoliticaEscalado()
        m = self.de_unidad(estado)
        if m is None:  # sin mandato la retención detiene la unidad igual
            return PoliticaEscalado()
        comun = es_comun(causa, motivo)
        if estado.modo == Modo.desatendido and not comun:
            return PoliticaEscalado(diferido=True, causa=CausaParada.unidad_amparada_fallida)
        if f"({PREFIJO_PRESUPUESTO_MANDATO}" in motivo:
            parada = CausaParada.presupuesto_mandato
        elif estado.modo == Modo.desatendido:
            parada = CausaParada.plan_incompleto
        else:
            parada = CausaParada.gate_escalado
        self.detener(m, parada, estado.unidad.unidad, motivo)
        return PoliticaEscalado(causa=parada)

    def tratar_parada(
        self, estado: EstadoUnidad, reporte: ReporteOrden, reintentos_hechos: int
    ) -> AccionParada:
        """Una orden que el arnés no completó: se reintenta si el mandato lo delega; si no, una parada."""

        m = self.de_unidad(estado) if gobernada(estado) else None
        if m is None:
            return AccionParada()
        if reporte.resultado == ResultadoOrden.fallido:
            if reintentos_hechos < m.contenido.limites.reintentos_parada:
                return AccionParada(reintentar=True)
            return AccionParada(causa=CausaParada.reintentos_agotados)
        return AccionParada(causa=CausaParada.decision_reservada)

    def reintentos_parada(self, estado: EstadoUnidad) -> int:
        m = self.de_unidad(estado) if gobernada(estado) else None
        return m.contenido.limites.reintentos_parada if m else 0

    # --- decisiones delegadas ---------------------------------------------------------------------------

    def validar_decisiones(self, estado: EstadoUnidad, decisiones: list[DecisionPropuesta]) -> None:
        """Rechaza las decisiones que citan una delegación inexistente o reservada (reportar bloqueado)."""

        if not decisiones:
            return
        m = self.de_unidad(estado) if gobernada(estado) else None
        if m is None:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                "la unidad no corre bajo un mandato: no hay decisiones delegadas",
                estado.version,
            )
        delegaciones = {d.id: d for d in m.contenido.delegaciones}
        for d in decisiones:
            delegacion = delegaciones.get(d.delegacion)
            if delegacion is None:
                raise ErrorNegocio(
                    CodigoError.fuera_de_alcance,
                    f"la delegación {d.delegacion} no existe en el mandato {m.id}: "
                    "reporta la orden como bloqueada",
                    estado.version,
                )
            if delegacion.tipo == TipoDelegacion.reservada:
                raise ErrorNegocio(
                    CodigoError.fuera_de_alcance,
                    f"la delegación {d.delegacion} está reservada: no se decide por criterio; "
                    "reporta la orden "
                    "como bloqueada y decide una persona",
                    estado.version,
                )

    def decisiones_nuevas(
        self, estado: EstadoUnidad, orden: Any, decisiones: Iterable[Any], actor: Actor
    ) -> list[DecisionDelegada]:
        """Las decisiones propuestas ya numeradas (``DD-n``) a continuación de las que la unidad lleva."""

        ahora = self.n.reloj()
        return [
            DecisionDelegada(
                id=f"DD-{len(estado.decisiones) + i + 1}",
                delegacion=d.delegacion,
                que=d.que,
                alternativas=d.alternativas,
                revertir=d.revertir,
                fase=orden.fase,
                orden=orden.id,
                tomada_por=actor,
                en=ahora,
            )
            for i, d in enumerate(decisiones)
        ]

    def auditar_decisiones(self, alcance: AlcanceUnidad, decisiones: Iterable[DecisionDelegada]) -> None:
        for d in decisiones:
            self.n.almacen.registrar_auditoria(
                RegistroAuditoria(
                    id=self.n.nuevo_id(),
                    alcance=self._ws(alcance.org, alcance.workspace),
                    evento=EventoAuditoria.decision_delegada,
                    actor=d.tomada_por,
                    en=d.en,
                    unidad=alcance.unidad,
                    detalle={
                        "decision": d.id,
                        "delegacion": d.delegacion,
                        "fase": d.fase.value,
                        "que": d.que[:300],
                    },
                )
            )

    def registrar_reintento(
        self, alcance: AlcanceUnidad, orden: Any, reporte: ReporteOrden, n: int, maximo: int
    ) -> None:
        """Deja constancia del reintento que el servidor aplicó sin preguntar (delegación ``reintento``)."""

        creadas: list[DecisionDelegada] = []

        def anadir(estado: EstadoUnidad) -> dict[str, Any]:
            creadas.clear()
            (decision,) = self.decisiones_nuevas(
                estado,
                orden,
                [
                    SimpleNamespace(
                        delegacion=DELEGACION_REINTENTO,
                        que=f"Reintento automático {n} de {maximo}: la orden {orden.tipo} #{orden.secuencia} "
                        f"reportó '{reporte.resultado.value}': {(reporte.motivo or '')[:1500]}",
                        alternativas=["Parar y esperar a una persona"],
                        revertir="Rechazar la unidad o bajar reintentos_parada del mandato.",
                    )
                ],
                ACTOR_SERVIDOR,
            )
            creadas.append(decision)
            return {"decisiones": [*estado.decisiones, decision]}

        self.n.escribir(alcance, anadir, ACTOR_SERVIDOR)
        self.auditar_decisiones(alcance, creadas)

    # --- reanudar -------------------------------------------------------------------------------------------

    async def reanudar_unidades(self, m: Mandato) -> None:
        """Entrega las entradas que quedaron retenidas mientras el mandato no amparaba trabajo."""

        if self.reanudar is None:
            return
        for u in self.unidades(m.alcance, m.id):
            if not gobernada(u) or not self.n.almacen.entradas_pendientes(u.unidad):
                continue
            try:
                await self.reanudar(u.unidad)
            except Exception:  # aprobar un mandato no falla porque una unidad lo haga
                log.exception("no se pudo reanudar %s tras aprobar el mandato %s", u.unidad.unidad, m.id)

    # --- tools mandate.* -------------------------------------------------------------------------------

    def _salida(self, m: Mandato) -> MandateSalida:
        return MandateSalida(mandato=m, huella=m.contenido.huella())

    @staticmethod
    def _humano_en_consola(actor: Actor, que: str) -> None:
        if actor.tipo != TipoActor.humano:
            raise ErrorNegocio(CodigoError.fuera_de_alcance, f"{que} exige una persona")
        if actor.canal != Canal.consola:
            raise ErrorNegocio(CodigoError.fuera_de_alcance, f"{que} solo se hace desde la consola")

    def _traer(self, alcance: AlcanceWorkspace, id_: str, version: int | None = None) -> Mandato:
        m = self.obtener(alcance, id_)
        if m is None:
            raise ErrorNegocio(CodigoError.no_encontrado, f"el mandato {id_} no existe")
        if version is not None and m.version != version:
            raise ErrorNegocio(
                CodigoError.conflicto_version,
                f"viste la versión {version} del mandato; la vigente es {m.version}",
                m.version,
            )
        return m

    def _guardar(self, m: Mandato, esperada: int | None) -> Mandato:
        try:
            return self.n.almacen.guardar_mandato(m, esperada)
        except ConflictoVersion as exc:
            raise ErrorNegocio(
                CodigoError.conflicto_version, "otra escritura cambió el mandato; vuelve a leerlo", exc.actual
            ) from None

    async def propose(self, e: MandateProposeEntrada, actor: Actor) -> MandateSalida:
        if actor.tipo == TipoActor.servicio:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance, "redactar un mandato exige una persona o un agente"
            )
        for repo in e.contenido.limites.repositorios:
            if self.n.almacen.vinculo(AlcanceRepositorio(**e.alcance.model_dump(), repositorio=repo)) is None:
                raise ErrorNegocio(
                    CodigoError.fuera_de_alcance, f"el repositorio {repo} no está vinculado al workspace"
                )
        previo = self.obtener(e.alcance, e.id)
        ahora = self.n.reloj()
        if e.version_vista is None:
            if previo is not None:
                raise ErrorNegocio(
                    CodigoError.conflicto_version,
                    f"ya existe el mandato {e.id}; edítalo con version_vista={previo.version}",
                    previo.version,
                )
            m = Mandato(
                alcance=e.alcance,
                id=e.id,
                version=1,
                contenido=e.contenido,
                estado=EstadoMandato.propuesto,
                creado_en=ahora,
                creado_por=actor,
                actualizado_en=ahora,
                actualizado_por=actor,
            )
            guardado = self._guardar(m, None)
            self._auditar(guardado, actor, "propuesto", huella=guardado.contenido.huella())
            return self._salida(guardado)
        m = self._traer(e.alcance, e.id, e.version_vista)
        if m.estado == EstadoMandato.revocado:
            raise ErrorNegocio(
                CodigoError.conversion_no_permitida,
                "un mandato revocado no se edita; redacta otro",
                m.version,
            )
        if e.contenido.huella() == m.contenido.huella():
            return self._salida(m)  # sin cambios: no pierde su aprobación
        if e.contenido.modo != m.contenido.modo and self.unidades(m.alcance, m.id):
            raise ErrorNegocio(
                CodigoError.conversion_no_permitida,
                "el modo de un mandato con unidades no cambia; redacta otro mandato",
                m.version,
            )
        nuevo = m.model_copy(
            update={
                "contenido": e.contenido,
                "estado": EstadoMandato.propuesto,
                "parada": None,
                "actualizado_en": ahora,
                "actualizado_por": actor,
            }
        )
        guardado = self._guardar(nuevo, m.version)
        self._auditar(
            guardado, actor, "propuesto", huella=guardado.contenido.huella(), editado_desde=m.estado.value
        )
        return self._salida(guardado)

    async def approve(self, e: MandateApproveEntrada, actor: Actor) -> MandateSalida:
        self._humano_en_consola(actor, "aprobar un mandato")
        m = self._traer(e.alcance, e.id, e.version_vista)
        if m.estado == EstadoMandato.revocado:
            raise ErrorNegocio(
                CodigoError.mandato_no_vigente, "un mandato revocado no se aprueba; redacta otro", m.version
            )
        if e.huella != m.contenido.huella():
            raise ErrorNegocio(
                CodigoError.conflicto_version,
                "el contenido del mandato cambió desde que lo viste; vuelve a leerlo",
                m.version,
            )
        exceso = self.exceso_presupuesto(m)
        if exceso:
            raise ErrorNegocio(
                CodigoError.presupuesto_agotado,
                f"el presupuesto total del mandato ya está alcanzado ({exceso}); edita el tope y apruébalo",
                m.version,
            )
        ahora = self.n.reloj()
        aprobacion = AprobacionMandato(
            actor=actor,
            en=ahora,
            caduca_en=ahora + timedelta(hours=m.contenido.limites.vigencia_horas),
            huella=e.huella,
            comentario=e.comentario,
        )
        nuevo = m.model_copy(
            update={
                "estado": EstadoMandato.aprobado,
                "parada": None,
                "aprobaciones": [*m.aprobaciones, aprobacion],
                "actualizado_en": ahora,
                "actualizado_por": actor,
            }
        )
        guardado = self._guardar(nuevo, m.version)
        self._auditar(
            guardado,
            actor,
            "aprobado",
            huella=e.huella,
            caduca_en=aprobacion.caduca_en.isoformat(),
            renovacion=True if m.aprobaciones else None,
        )
        await self.reanudar_unidades(guardado)
        return self._salida(guardado)

    async def revoke(self, e: MandateRevokeEntrada, actor: Actor) -> MandateSalida:
        if actor.tipo != TipoActor.humano:
            raise ErrorNegocio(CodigoError.fuera_de_alcance, "revocar un mandato exige una persona")
        m = self._traer(e.alcance, e.id, e.version_vista)
        if m.estado == EstadoMandato.revocado:
            return self._salida(m)
        ahora = self.n.reloj()
        nuevo = m.model_copy(
            update={
                "estado": EstadoMandato.revocado,
                "parada": None,
                "revocacion": RevocacionMandato(actor=actor, en=ahora, motivo=e.motivo),
                "actualizado_en": ahora,
                "actualizado_por": actor,
            }
        )
        guardado = self._guardar(nuevo, m.version)
        self._auditar(guardado, actor, "revocado", motivo=e.motivo[:300])
        return self._salida(guardado)

    async def review(self, e: MandateReviewEntrada, actor: Actor) -> EstadoSalida:
        self._humano_en_consola(actor, "revisar una decisión")
        revision = RevisionDecision(
            resultado=e.resultado, actor=actor, en=self.n.reloj(), comentario=e.comentario
        )

        def revisar(estado: EstadoUnidad) -> dict[str, Any]:
            if estado.version != e.version_vista:
                raise ErrorNegocio(
                    CodigoError.conflicto_version,
                    f"viste la versión {e.version_vista}; la vigente es {estado.version}",
                    estado.version,
                )
            decision = next((d for d in estado.decisiones if d.id == e.decision), None)
            if decision is None:
                raise ErrorNegocio(
                    CodigoError.no_encontrado, f"la decisión {e.decision} no existe", estado.version
                )
            if decision.revision is not None:
                raise ErrorNegocio(
                    CodigoError.checkpoint_ya_resuelto,
                    f"la decisión {e.decision} ya se revisó",
                    estado.version,
                )
            return {
                "decisiones": [
                    d.model_copy(update={"revision": revision}) if d.id == e.decision else d
                    for d in estado.decisiones
                ]
            }

        if self.n.almacen.obtener_estado(e.unidad) is None:
            raise ErrorNegocio(CodigoError.no_encontrado, f"unidad {e.unidad.unidad} no existe")
        nuevo = self.n.escribir(e.unidad, revisar, actor)
        self.n.almacen.registrar_auditoria(
            RegistroAuditoria(
                id=self.n.nuevo_id(),
                alcance=self._ws(e.unidad.org, e.unidad.workspace),
                evento=EventoAuditoria.revision_decision,
                actor=actor,
                en=revision.en,
                unidad=e.unidad.unidad,
                detalle={
                    "decision": e.decision,
                    "resultado": e.resultado.value,
                    **({"comentario": e.comentario[:300]} if e.comentario else {}),
                },
            )
        )
        return EstadoSalida(estado=nuevo)

    def _unidad_de_mandato(self, u: EstadoUnidad) -> UnidadDeMandato:
        cp = u.checkpoint_pendiente
        causa = cp.causa_parada if cp else None
        return UnidadDeMandato(
            unidad=u.unidad.unidad,
            titulo=u.titulo,
            fase=u.fase,
            estado=u.estado,
            modo=u.modo,
            consumo=u.consumo,
            diferida=causa == CausaParada.unidad_amparada_fallida,
            causa_parada=causa,
            decisiones_pendientes=sum(1 for d in u.decisiones if d.revision is None),
            actualizado_en=u.actualizado_en,
        )

    async def get(self, e: MandateGetEntrada, actor: Actor) -> MandateGetSalida:
        m = self._traer(e.alcance, e.id)
        unidades = self.unidades(m.alcance, m.id)
        ahora = self.n.reloj()
        motivo = m.motivo_no_vigente(ahora)
        if motivo is None:
            exceso = self.exceso_presupuesto(m, unidades)
            if exceso:
                motivo = f"el presupuesto total del mandato está alcanzado ({exceso})"
        decisiones = sorted(
            (DecisionDeUnidad(unidad=u.unidad.unidad, decision=d) for u in unidades for d in u.decisiones),
            key=lambda x: x.decision.en,
            reverse=True,
        )[:MAX_DECISIONES_EN_MANDATO]
        return MandateGetSalida(
            mandato=m,
            huella=m.contenido.huella(),
            vigente=motivo is None,
            motivo_no_vigente=motivo,
            unidades=[self._unidad_de_mandato(u) for u in unidades],
            consumo=self.consumo(unidades),
            decisiones=decisiones,
        )

    async def list(self, e: MandateListEntrada, actor: Actor) -> MandateListSalida:
        resumenes = []
        for m in self.n.almacen.listar_mandatos(e.alcance, e.estado, e.limite):
            unidades = self.unidades(m.alcance, m.id)
            resumenes.append(
                ResumenMandato(
                    id=m.id,
                    titulo=m.contenido.titulo,
                    modo=m.contenido.modo,
                    estado=m.estado,
                    vigente=m.vigente(self.n.reloj()),
                    vigente_hasta=m.vigente_hasta,
                    causa_parada=m.parada.causa if m.parada else None,
                    unidades=len(unidades),
                    max_unidades=m.contenido.limites.max_unidades,
                    decisiones_pendientes=sum(
                        1 for u in unidades for d in u.decisiones if d.revision is None
                    ),
                    actualizado_en=m.actualizado_en,
                )
            )
        return MandateListSalida(mandatos=resumenes)
