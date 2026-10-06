"""Estado de una unidad: remoto (fuente de verdad) y local (espejo del proxy).

Reglas fijas de propiedad (punto 5 del análisis): en el protocolo gana el
remoto; en el código gana el local. ``EstadoUnidad`` lo escribe solo el
servidor y lleva ``version`` para bloqueo optimista. ``EstadoLocal`` es lo que
el proxy guarda en el worktree: un espejo de solo lectura del remoto más la
cola de eventos pendientes de subir.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import UUID4, AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    MODOS_CON_MANDATO,
    Actor,
    AlcanceUnidad,
    Arnes,
    Canal,
    CausaEscalado,
    Commit,
    Effort,
    EstadoFase,
    Fase,
    GateFase,
    GobernanzaConsultada,
    Modo,
    NivelCodigo,
    Perfil,
    Presupuesto,
    Proveedor,
    Riesgo,
    RolRepositorio,
    Slug,
    TipoActor,
    UnidadId,
    Veredicto,
    ids_unicos,
    mas_restrictivo,
)
from .eventos import Direccion, EventoSync
from .hallazgos import Hallazgo, bloqueantes
from .orden import OrdenDeTrabajo


def _exigir_humano(actor: Actor, que: str) -> None:
    if actor.tipo != TipoActor.humano:
        raise ValueError(f"{que} exige un actor humano")


# --- Gates ---------------------------------------------------------------------


class Rehabilitacion(Contrato):
    actor: Actor
    en: AwareDatetime
    motivo: str = Field(min_length=1, max_length=4000)

    @model_validator(mode="after")
    def _humano(self) -> Rehabilitacion:
        _exigir_humano(self.actor, "rehabilitar un gate")
        return self


class ResultadoGate(Contrato):
    veredicto: Veredicto
    causa: CausaEscalado | None = None
    iteraciones: int = Field(ge=0)
    hallazgos: list[Hallazgo] = Field(default_factory=list, description="Los que quedaron sin resolver.")
    gobernanza_consultada: GobernanzaConsultada
    criticos: list[str] = Field(default_factory=list, description="Lentes del panel.")
    refutador: bool = False
    rehabilitado: Rehabilitacion | None = None
    cerrado_en: AwareDatetime

    @model_validator(mode="after")
    def _reglas(self) -> ResultadoGate:
        if self.veredicto == Veredicto.escalado:
            if self.causa is None:
                raise ValueError("un gate escalado necesita causa")
            if self.causa == CausaEscalado.sin_convergencia and self.iteraciones < 1:
                raise ValueError("sin-convergencia exige al menos una iteración de refinamiento")
        else:
            if self.causa is not None:
                raise ValueError("solo un gate escalado lleva causa")
            if self.rehabilitado is not None:
                raise ValueError("solo un gate escalado puede rehabilitarse")
            # Nunca aprueba por agotamiento.
            if bloqueantes(self.hallazgos):
                raise ValueError("hallazgos alta/media sin resolver: el gate debe escalar")
            # Nunca critica de memoria.
            if self.gobernanza_consultada == GobernanzaConsultada.no:
                raise ValueError("sin gobernanza consultada el gate escala con causa sin-gobernanza")
        if (
            self.causa == CausaEscalado.sin_gobernanza
            and self.gobernanza_consultada == GobernanzaConsultada.si
        ):
            raise ValueError("causa sin-gobernanza con gobernanza_consultada=si es contradictorio")
        return self

    @property
    def superado(self) -> bool:
        return self.veredicto != Veredicto.escalado or self.rehabilitado is not None


# --- Checkpoints humanos (R7) ------------------------------------------------------


class TipoCheckpoint(StrEnum):
    aprobar_spec = "aprobar-spec"
    aprobar_plan = "aprobar-plan"
    paquete_aprobacion = "paquete-aprobacion"
    parada = "parada"
    gate_escalado = "gate-escalado"


class Decision(StrEnum):
    aprobado = "aprobado"
    cambios_solicitados = "cambios-solicitados"
    rechazado = "rechazado"


class Checkpoint(Contrato):
    """Decisión humana pendiente.

    Se resuelve por cualquier canal (elicitation en el arnés, ``unit.approve``
    o la consola): gana la primera resolución que llegue y las siguientes se
    rechazan con ``checkpoint-ya-resuelto``.
    """

    id: UUID4
    tipo: TipoCheckpoint
    fase: Fase
    pregunta: str = Field(min_length=1, max_length=2000)
    artefacto_sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    abierto_en: AwareDatetime


class ResolucionCheckpoint(Contrato):
    checkpoint: UUID4
    decision: Decision
    actor: Actor
    en: AwareDatetime
    comentario: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _reglas(self) -> ResolucionCheckpoint:
        _exigir_humano(self.actor, "resolver un checkpoint")
        if self.decision != Decision.aprobado and not self.comentario:
            raise ValueError(f"una decisión {self.decision.value} necesita comentario")
        return self


class Integracion(Contrato):
    """Estado posterior al cierre (R7): no condiciona el cierre de la unidad."""

    actor: Actor
    en: AwareDatetime
    especificacion_viva: str = Field(
        min_length=1, max_length=512, description="Dónde quedó consolidada la spec (ruta o id)."
    )
    pr_url: str | None = Field(default=None, pattern=r"^https://", max_length=512)

    @model_validator(mode="after")
    def _humano(self) -> Integracion:
        _exigir_humano(self.actor, "integrar una unidad")
        return self


# --- Estado remoto -------------------------------------------------------------------


class RepositorioUnidad(Contrato):
    repositorio: Slug
    rol: RolRepositorio
    rama: str | None = Field(default=None, max_length=255)
    base_commit: Commit
    nivel_codigo: NivelCodigo | None = Field(
        default=None,
        description=(
            "Desde 1.7: nivel de código del vínculo, fijado al crear la unidad y que no sigue a los "
            "cambios posteriores del vínculo. None = unidad anterior a 1.7: el nivel se lee del "
            "vínculo en cada uso, como hasta 1.6."
        ),
    )


class EjecucionModelo(Contrato):
    """Auditoría de qué modelo produjo cada contenido (antes modelo_ejecucion)."""

    fase: Fase | GateFase
    rol: str = Field(min_length=1, max_length=80)
    nodo: str = Field(min_length=1, max_length=120)
    proveedor: Proveedor | None = Field(default=None, description="None si lo ejecutó el arnés.")
    modelo: str = Field(min_length=1, max_length=120)
    effort: Effort | None = None
    en: AwareDatetime


class ConversionModo(Contrato):
    de: Modo
    a: Modo
    actor: Actor
    en: AwareDatetime
    motivo: str = Field(min_length=1, max_length=2000)
    tras: Fase | None = Field(
        default=None,
        description=(
            "Desde 1.2: fase tras la que se convirtió (research o el checkpoint de spec). "
            "None solo en la primera conversión, cuando el humano fija el modo en unit.start."
        ),
    )

    @model_validator(mode="after")
    def _reglas(self) -> ConversionModo:
        _exigir_humano(self.actor, "convertir el modo de una unidad")
        if self.de == self.a:
            raise ValueError("una conversión de modo cambia el modo")
        return self


class Consumo(Contrato):
    tokens: int = Field(default=0, ge=0)
    segundos: int = Field(default=0, ge=0)
    costo_usd: float = Field(default=0.0, ge=0)
    llamadas: int = Field(
        default=0, ge=0, description="Desde 1.7: llamadas al modelo que salieron al proveedor."
    )


class EstadoUnidad(Mensaje):
    unidad: AlcanceUnidad
    version: int = Field(ge=1, description="Bloqueo optimista; +1 en cada escritura.")
    titulo: str = Field(min_length=1, max_length=200)
    pedido: str | None = Field(
        default=None,
        max_length=20_000,
        description="Desde 1.2: el pedido original de unit.start, tal cual lo escribió el humano.",
    )
    dueno: Actor
    carril: str | None = Field(default=None, max_length=40)
    arnes: Arnes | None = Field(default=None, description="None si la unidad nació en la consola.")
    repositorios: list[RepositorioUnidad] = Field(min_length=1)
    nivel_efectivo: NivelCodigo | None = Field(
        default=None,
        description=(
            "Desde 1.7: el más restrictivo de los niveles congelados de los repositorios de la unidad; "
            "rige su política de datos (modelos, gate multi-repo, lectores). None solo si ningún "
            "repositorio trae nivel (unidad anterior a 1.7)."
        ),
    )
    fase: Fase
    estado: EstadoFase
    modo: Modo
    riesgo: Riesgo
    perfil: Perfil
    modo_conversion: list[ConversionModo] = Field(default_factory=list)
    governance_refs: list[str] = Field(default_factory=list)
    comando_validacion: str | None = Field(default=None, max_length=2000)
    insumos: list[UUID4] = Field(default_factory=list)
    gates: dict[GateFase, ResultadoGate] = Field(default_factory=dict)
    modelo_ejecucion: list[EjecucionModelo] = Field(default_factory=list)
    orden_vigente: UUID4 | None = None
    secuencia_ordenes: int = Field(default=0, ge=0)
    checkpoint_pendiente: Checkpoint | None = None
    resoluciones: list[ResolucionCheckpoint] = Field(default_factory=list)
    integracion: Integracion | None = None
    depende_de: list[UnidadId] = Field(default_factory=list)
    presupuesto: Presupuesto = Field(default_factory=Presupuesto)
    consumo: Consumo = Field(default_factory=Consumo)
    creado_en: AwareDatetime
    actualizado_en: AwareDatetime
    actualizado_por: Actor

    @model_validator(mode="after")
    def _reglas(self) -> EstadoUnidad:
        ids_unicos([r.repositorio for r in self.repositorios], "repositorios")
        ids_unicos([str(r.checkpoint) for r in self.resoluciones], "resoluciones de checkpoint")
        _exigir_humano(self.dueno, "ser dueño de una unidad")
        if not any(r.rol == RolRepositorio.primario for r in self.repositorios):
            raise ValueError("una unidad necesita al menos un repositorio primario")
        niveles = [r.nivel_codigo for r in self.repositorios]
        if any(n is None for n in niveles):
            if any(n is not None for n in niveles) or self.nivel_efectivo is not None:
                raise ValueError("el nivel se congela en todos los repositorios de la unidad o en ninguno")
        elif self.nivel_efectivo != mas_restrictivo(n for n in niveles if n is not None):
            raise ValueError("nivel_efectivo debe ser el más restrictivo de los repositorios")
        if self.actualizado_en < self.creado_en:
            raise ValueError("actualizado_en anterior a creado_en")
        if self.unidad.unidad in self.depende_de:
            raise ValueError("una unidad no puede depender de sí misma")
        if self.orden_vigente is not None and self.checkpoint_pendiente is not None:
            raise ValueError("no puede haber a la vez orden vigente y checkpoint pendiente")
        if self.orden_vigente is not None and self.secuencia_ordenes < 1:
            raise ValueError("orden_vigente sin secuencia_ordenes")
        if self.checkpoint_pendiente is not None and any(
            r.checkpoint == self.checkpoint_pendiente.id for r in self.resoluciones
        ):
            raise ValueError("el checkpoint pendiente ya tiene resolución")
        if self.modo_conversion:
            if self.modo_conversion[0].de != Modo.interactivo:
                raise ValueError("toda unidad nace interactivo: la primera conversión parte de ahí")
            if self.modo_conversion[-1].a != self.modo:
                raise ValueError("modo no coincide con la última conversión")
            for previa, siguiente in zip(self.modo_conversion, self.modo_conversion[1:], strict=False):
                if previa.a != siguiente.de:
                    raise ValueError("conversiones de modo encadenadas incoherentes")
            if any(c.tras is None for c in self.modo_conversion[1:]):
                raise ValueError("solo la primera conversión (en unit.start) puede no tener fase")
        elif self.modo != Modo.interactivo:
            raise ValueError("una unidad que no es interactivo necesita su modo_conversion")
        if self.modo in MODOS_CON_MANDATO and self.unidad.plan is None:
            raise ValueError(f"modo {self.modo.value} exige un mandato (unidad.plan)")
        if (self.fase == Fase.done) != (self.estado == EstadoFase.completado):
            raise ValueError("fase done y estado completado van juntos")
        if self.fase == Fase.done:
            codigo = self.gates.get(GateFase.codigo)
            if codigo is None or not codigo.superado:
                raise ValueError("fase done exige gate de codigo superado o rehabilitado")
            if self.orden_vigente is not None or self.checkpoint_pendiente is not None:
                raise ValueError("una unidad cerrada no tiene orden ni checkpoint pendiente")
        elif self.integracion is not None:
            raise ValueError("solo una unidad cerrada puede integrarse")
        return self


# --- Estado local --------------------------------------------------------------------


class BloqueoInstancia(Contrato):
    """Una sesión por unidad y máquina (sucesor de instance_lock.py)."""

    host: str = Field(min_length=1, max_length=255)
    pid: int = Field(ge=1)
    desde: AwareDatetime


class EstadoLocal(Mensaje):
    """Archivo ``.railspec/estado-local.json`` del worktree de la unidad."""

    unidad: AlcanceUnidad
    repositorio: Slug
    worktree: str = Field(min_length=1, max_length=4096, description="Ruta absoluta local.")
    rama: str = Field(min_length=1, max_length=255)
    base_commit: Commit
    espejo_remoto: EstadoUnidad | None = Field(
        default=None, description="Copia de solo lectura; el remoto gana siempre."
    )
    orden_en_curso: OrdenDeTrabajo | None = None
    ultima_secuencia_recibida: int = Field(
        default=0, ge=0, description="Último evento remoto→local aplicado."
    )
    ultima_secuencia_confirmada: int = Field(
        default=0, ge=0, description="Último evento local→remoto que el servidor confirmó."
    )
    cola_pendiente: list[EventoSync] = Field(
        default_factory=list, description="Eventos local→remoto sin confirmar, en orden."
    )
    bloqueo: BloqueoInstancia | None = None

    @model_validator(mode="after")
    def _reglas(self) -> EstadoLocal:
        if self.espejo_remoto is not None and self.espejo_remoto.unidad != self.unidad:
            raise ValueError("el espejo remoto es de otra unidad")
        if self.orden_en_curso is not None:
            if self.orden_en_curso.unidad != self.unidad:
                raise ValueError("la orden en curso es de otra unidad")
            if self.orden_en_curso.base_commit != self.base_commit:
                raise ValueError("la orden en curso tiene otro base_commit que el worktree")
        ids_unicos([str(e.id) for e in self.cola_pendiente], "eventos en cola")
        previa = self.ultima_secuencia_confirmada
        for evento in self.cola_pendiente:
            if evento.direccion != Direccion.local_a_remoto:
                raise ValueError("la cola local solo contiene eventos local→remoto")
            if evento.unidad != self.unidad:
                raise ValueError("evento en cola de otra unidad")
            if evento.secuencia <= previa:
                raise ValueError("secuencias de la cola no crecientes o ya confirmadas")
            previa = evento.secuencia
        return self


__all__ = [
    "BloqueoInstancia",
    "Canal",
    "Checkpoint",
    "ConversionModo",
    "Consumo",
    "Decision",
    "EjecucionModelo",
    "EstadoLocal",
    "EstadoUnidad",
    "Integracion",
    "Rehabilitacion",
    "RepositorioUnidad",
    "ResolucionCheckpoint",
    "ResultadoGate",
    "TipoCheckpoint",
]
