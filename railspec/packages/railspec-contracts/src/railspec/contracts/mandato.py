"""Mandato (desde 1.11): la aprobación única bajo la que corren los modos supervisado y desatendido.

Una unidad ``interactivo`` pide a un humano que apruebe el spec y el plan; una ``semi-autonomo``, el
paquete antes de implementar. Una unidad ``supervisado`` o ``desatendido`` no abre esos checkpoints
porque un humano aprobó **antes**, una sola vez, un mandato que ampara varias unidades dentro de
límites. El mandato no es una pausa menos: es una autorización acotada y con fecha de caducidad.

Reglas que el contrato fija (cada una tiene su prueba negativa):

- Lo aprueba siempre un humano, de forma explícita y sobre el contenido exacto que vio
  (``AprobacionMandato.huella``). Cambiar el contenido lo devuelve a ``propuesto``: no hay edición
  silenciosa de lo aprobado.
- Caduca (``LimitesMandato.vigencia_horas``, 24 h por defecto, 7 días como máximo). Renovarlo es una
  aprobación nueva; la historia de aprobaciones solo crece.
- Los límites son de alcance (repositorios, rutas, número de unidades), de gasto (presupuesto total)
  y de autonomía (reintentos de parada). Los gates **no** se configuran: siguen escalando.
- Una parada es tipificada (``CausaParada``). Las que afectan a todo el mandato lo dejan ``parado``;
  las de una sola unidad quedan en el checkpoint de esa unidad. Cualquier gate rojo, exceso de
  presupuesto o salida del alcance detiene la unidad.
- Un humano decide las decisiones ``reservadas`` y las que ninguna delegación cubre. Las delegadas se
  registran una por una (``DecisionDelegada``) para que un humano las revise después.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from enum import StrEnum
from typing import Annotated

from pydantic import UUID4, AwareDatetime, Field, StringConstraints, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    MODOS_CON_MANDATO,
    Actor,
    AlcanceWorkspace,
    Fase,
    Glob,
    Modo,
    Presupuesto,
    Slug,
    TipoActor,
    UnidadId,
    ids_unicos,
)

DelegacionId = Annotated[
    str,
    StringConstraints(pattern=r"^D-[0-9]{1,3}$"),
    Field(description="Delegación del mandato, p. ej. D-2."),
]

DecisionId = Annotated[
    str,
    StringConstraints(pattern=r"^DD-[0-9]{1,4}$"),
    Field(description="Decisión delegada de una unidad, p. ej. DD-3; única dentro de la unidad."),
]

#: Delegación que el servidor aplica sin que el mandato la liste: reintentar una orden que falló, tantas
#: veces como diga ``LimitesMandato.reintentos_parada``.
DELEGACION_REINTENTO = "reintento"

#: Máximo de decisiones que un reporte puede registrar de una vez.
MAX_DECISIONES_POR_REPORTE = 20


def _exigir_humano(actor: Actor, que: str) -> None:
    if actor.tipo != TipoActor.humano:
        raise ValueError(f"{que} exige un actor humano")


class EstadoMandato(StrEnum):
    propuesto = "propuesto"  # redactado o editado; nadie lo aprobó (o cambió tras la última aprobación)
    aprobado = "aprobado"  # aprobado y dentro de su vigencia (la caducidad se evalúa contra el reloj)
    parado = "parado"  # una condición de parada lo detuvo; reanudar exige una aprobación nueva
    revocado = "revocado"  # cerrado por un humano; no se reabre


class TipoDelegacion(StrEnum):
    pre_decidida = "pre-decidida"  # el mandato ya decidió: se aplica tal cual
    con_criterio = "con-criterio"  # el mandato da un criterio; quien ejecuta decide dentro de él
    reservada = "reservada"  # nunca se decide por criterio: se detiene y la decide un humano


class CausaParada(StrEnum):
    """Condiciones de parada tipificadas (kit SDD: ``.spec/PARADAS-SUPERVISADO.md``).

    Las cinco primeras detienen el mandato entero; las cuatro últimas, solo la unidad afectada
    (``gate-escalado`` es de ambas: en supervisado congela el mandato y en desatendido difiere la unidad).
    """

    gate_escalado = "gate-escalado"  # un gate escaló y el modo exige congelar el mandato
    plan_incompleto = "plan-incompleto"  # una causa común (gobernanza, proveedor) afecta a todas las unidades
    presupuesto_mandato = "presupuesto-mandato"  # el presupuesto total del mandato se alcanzó
    mandato_caducado = "mandato-caducado"  # la vigencia terminó
    mandato_revocado = "mandato-revocado"  # un humano lo cerró
    unidad_amparada_fallida = "unidad-amparada-fallida"  # desatendido: el gate escaló y la unidad se difiere
    fuera_de_alcance = "fuera-de-alcance"  # tocó rutas que el mandato no permite
    decision_reservada = "decision-reservada"  # necesita una decisión que el mandato no delega
    reintentos_agotados = "reintentos-agotados"  # la orden falló más veces que las delegadas


#: Las que se guardan en ``Mandato.parada``.
CAUSAS_DE_MANDATO = frozenset(
    {
        CausaParada.gate_escalado,
        CausaParada.plan_incompleto,
        CausaParada.presupuesto_mandato,
        CausaParada.mandato_caducado,
        CausaParada.mandato_revocado,
    }
)


class ResultadoRevision(StrEnum):
    aceptada = "aceptada"
    revertida = "revertida"


# --- Contenido (lo que se aprueba) -----------------------------------------------------------


class Delegacion(Contrato):
    id: DelegacionId
    tipo: TipoDelegacion
    texto: str = Field(min_length=1, max_length=2000)


class LimitesMandato(Contrato):
    repositorios: list[Slug] = Field(
        min_length=1,
        max_length=20,
        description="Repositorios en los que las unidades amparadas pueden trabajar.",
    )
    max_unidades: int = Field(default=5, ge=1, le=50, description="Cuántas unidades puede amparar.")
    rutas_permitidas: list[Glob] = Field(
        default_factory=list,
        max_length=100,
        description=(
            "Si hay alguna, un snapshot que toque una ruta que no case con ninguna detiene la unidad "
            "(`fuera-de-alcance`). Vacía: sin restricción adicional a la del plan de cada unidad."
        ),
    )
    presupuesto: Presupuesto = Field(
        default_factory=Presupuesto,
        description=(
            "Tope **total** del mandato, sumando el consumo de todas sus unidades; se alcanza una vez y "
            "detiene el mandato entero (`presupuesto-mandato`). Es aparte de los topes por unidad, fase "
            "y mes."
        ),
    )
    reintentos_parada: int = Field(
        default=0,
        ge=0,
        le=3,
        description=(
            "Cuántas veces reintenta el servidor, sin preguntar, una orden que el arnés reportó `fallido`. "
            "0: ninguna, una parada la ve siempre un humano. `bloqueado` nunca se reintenta."
        ),
    )
    vigencia_horas: int = Field(
        default=24, ge=1, le=168, description="Cuánto vale cada aprobación; después hay que renovarla."
    )

    @model_validator(mode="after")
    def _reglas(self) -> LimitesMandato:
        ids_unicos(list(self.repositorios), "repositorios del mandato")
        return self


class MandatoContenido(Contrato):
    """Lo que un humano aprueba. Su huella liga la aprobación a este contenido exacto."""

    titulo: str = Field(min_length=1, max_length=200)
    objetivo: str = Field(
        min_length=1, max_length=4000, description="Qué se quiere lograr y cuándo se da por terminado."
    )
    modo: Modo = Field(description="El modo de todas las unidades que ampara: supervisado o desatendido.")
    limites: LimitesMandato
    delegaciones: list[Delegacion] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def _reglas(self) -> MandatoContenido:
        if self.modo not in MODOS_CON_MANDATO:
            raise ValueError("un mandato ampara unidades supervisado o desatendido")
        ids_unicos([d.id for d in self.delegaciones], "delegaciones")
        p = self.limites.presupuesto
        if self.modo == Modo.desatendido and not any(
            v is not None for v in (p.tokens_max, p.segundos_max, p.costo_usd_max, p.llamadas_max)
        ):
            raise ValueError("un mandato desatendido necesita al menos un tope de presupuesto total")
        return self

    def huella(self) -> str:
        """SHA-256 del contenido canónico (claves ordenadas, sin espacios): lo que firma la aprobación."""

        crudo = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":"), ensure_ascii=False
        )
        return hashlib.sha256(crudo.encode("utf-8")).hexdigest()


# --- Mandato -------------------------------------------------------------------------------------


class AprobacionMandato(Contrato):
    actor: Actor
    en: AwareDatetime
    caduca_en: AwareDatetime
    huella: str = Field(pattern=r"^[0-9a-f]{64}$", description="Huella del contenido que el humano vio.")
    comentario: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reglas(self) -> AprobacionMandato:
        _exigir_humano(self.actor, "aprobar un mandato")
        if self.caduca_en <= self.en:
            raise ValueError("la aprobación caduca después de darse")
        return self


class ParadaMandato(Contrato):
    causa: CausaParada
    unidad: UnidadId | None = Field(default=None, description="La unidad que originó la parada, si la hubo.")
    detalle: str = Field(min_length=1, max_length=1000)
    en: AwareDatetime

    @model_validator(mode="after")
    def _reglas(self) -> ParadaMandato:
        if self.causa not in CAUSAS_DE_MANDATO:
            raise ValueError(f"{self.causa.value} detiene una unidad, no el mandato")
        return self


class RevocacionMandato(Contrato):
    actor: Actor
    en: AwareDatetime
    motivo: str = Field(min_length=1, max_length=2000)

    @model_validator(mode="after")
    def _humano(self) -> RevocacionMandato:
        _exigir_humano(self.actor, "revocar un mandato")
        return self


class Mandato(Mensaje):
    """El mandato de un workspace. Su ``id`` es el ``plan`` de las unidades que ampara."""

    alcance: AlcanceWorkspace
    id: Slug
    version: int = Field(ge=1, description="Bloqueo optimista; +1 en cada escritura.")
    contenido: MandatoContenido
    estado: EstadoMandato
    aprobaciones: list[AprobacionMandato] = Field(
        default_factory=list, description="Historia que solo crece; la última es la vigente."
    )
    parada: ParadaMandato | None = None
    revocacion: RevocacionMandato | None = None
    creado_en: AwareDatetime
    creado_por: Actor
    actualizado_en: AwareDatetime
    actualizado_por: Actor

    @model_validator(mode="after")
    def _reglas(self) -> Mandato:
        if self.actualizado_en < self.creado_en:
            raise ValueError("actualizado_en anterior a creado_en")
        for previa, siguiente in zip(self.aprobaciones, self.aprobaciones[1:], strict=False):
            if siguiente.en < previa.en:
                raise ValueError("las aprobaciones van en orden cronológico")
        if self.estado == EstadoMandato.revocado:
            if self.revocacion is None:
                raise ValueError("un mandato revocado necesita su revocación")
            return self
        if self.revocacion is not None:
            raise ValueError("solo un mandato revocado lleva revocación")
        if self.estado == EstadoMandato.propuesto:
            if self.parada is not None:
                raise ValueError("un mandato propuesto no está parado")
            return self
        # aprobado y parado descansan en una aprobación del contenido vigente.
        if not self.aprobaciones:
            raise ValueError(f"un mandato {self.estado.value} necesita una aprobación")
        if self.aprobaciones[-1].huella != self.contenido.huella():
            raise ValueError("el contenido cambió después de la última aprobación: debe volver a propuesto")
        if self.estado == EstadoMandato.parado and self.parada is None:
            raise ValueError("un mandato parado necesita su parada")
        if self.estado == EstadoMandato.aprobado and self.parada is not None:
            raise ValueError("un mandato aprobado no lleva parada")
        return self

    @property
    def vigente_hasta(self) -> datetime | None:
        """Hasta cuándo vale la última aprobación (``None`` si nunca se aprobó)."""

        return self.aprobaciones[-1].caduca_en if self.aprobaciones else None

    def vigente(self, ahora: datetime) -> bool:
        """Solo un mandato ``aprobado`` y dentro de su vigencia ampara trabajo."""

        hasta = self.vigente_hasta
        return self.estado == EstadoMandato.aprobado and hasta is not None and ahora < hasta

    def motivo_no_vigente(self, ahora: datetime) -> str | None:
        """Por qué no ampara trabajo ahora, o ``None`` si lo ampara."""

        if self.vigente(ahora):
            return None
        if self.estado == EstadoMandato.revocado:
            return "el mandato fue revocado"
        if self.estado == EstadoMandato.propuesto:
            return "el mandato no está aprobado (o cambió después de aprobarse)"
        if self.estado == EstadoMandato.parado and self.parada is not None:
            return f"el mandato está parado ({self.parada.causa.value}): {self.parada.detalle}"
        return f"la aprobación del mandato caducó el {self.vigente_hasta:%Y-%m-%d %H:%M} UTC"


# --- Lo que viaja en las órdenes, los reportes y el estado de la unidad ------------------------------


class MandatoEnOrden(Contrato):
    """Lo del mandato que el arnés necesita para ejecutar una orden dentro de sus límites."""

    mandato: Slug
    modo: Modo
    caduca_en: AwareDatetime
    delegaciones: list[Delegacion] = Field(default_factory=list)
    rutas_permitidas: list[Glob] = Field(default_factory=list)
    reintentos_parada: int = Field(default=0, ge=0, le=3)


class DecisionPropuesta(Contrato):
    """Una decisión que el arnés tomó apoyándose en una delegación del mandato y reporta."""

    delegacion: DelegacionId
    que: str = Field(min_length=1, max_length=2000, description="Qué decidió.")
    alternativas: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10, description="Las que consideró y descartó."
    )
    revertir: str = Field(min_length=1, max_length=1000, description="Cómo se revierte.")


class RevisionDecision(Contrato):
    resultado: ResultadoRevision
    actor: Actor
    en: AwareDatetime
    comentario: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _reglas(self) -> RevisionDecision:
        _exigir_humano(self.actor, "revisar una decisión")
        if self.resultado == ResultadoRevision.revertida and not self.comentario:
            raise ValueError("revertir una decisión necesita comentario")
        return self


class DecisionDelegada(Contrato):
    """Registro de una decisión tomada bajo el mandato; un humano la revisa después."""

    id: DecisionId
    delegacion: str = Field(
        pattern=rf"^(D-[0-9]{{1,3}}|{DELEGACION_REINTENTO})$",
        description="La delegación del mandato en que se apoya, o `reintento` (la aplica el servidor).",
    )
    que: str = Field(min_length=1, max_length=2000)
    alternativas: list[Annotated[str, Field(min_length=1, max_length=500)]] = Field(
        default_factory=list, max_length=10
    )
    revertir: str = Field(min_length=1, max_length=1000)
    fase: Fase
    orden: UUID4 | None = Field(default=None, description="La orden durante la que se tomó.")
    tomada_por: Actor
    en: AwareDatetime
    revision: RevisionDecision | None = None

    @model_validator(mode="after")
    def _reglas(self) -> DecisionDelegada:
        if self.tomada_por.tipo == TipoActor.servicio:
            raise ValueError("una decisión la toma un agente o un humano")
        return self


__all__ = [
    "CAUSAS_DE_MANDATO",
    "DELEGACION_REINTENTO",
    "MAX_DECISIONES_POR_REPORTE",
    "AprobacionMandato",
    "CausaParada",
    "DecisionDelegada",
    "DecisionId",
    "DecisionPropuesta",
    "Delegacion",
    "DelegacionId",
    "EstadoMandato",
    "LimitesMandato",
    "Mandato",
    "MandatoContenido",
    "MandatoEnOrden",
    "ParadaMandato",
    "ResultadoRevision",
    "RevisionDecision",
    "RevocacionMandato",
    "TipoDelegacion",
]
