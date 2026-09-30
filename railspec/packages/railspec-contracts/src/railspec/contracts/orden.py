"""Orden de trabajo: el contrato entre el servidor y el arnés.

Cada ``unit.advance`` devuelve como mucho una orden vigente. El arnés la
ejecuta en el worktree local y la cierra con ``unit.report``; el servidor
rechaza cualquier reporte que no corresponda a la orden vigente (regla de un
solo orquestador: el arnés nunca decide fase ni gate).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import UUID4, AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    AlcanceUnidad,
    Commit,
    Criterio,
    CriterioId,
    Fase,
    Glob,
    GobernanzaConsultada,
    GrupoId,
    Presupuesto,
    RolRepositorio,
    RutaRelativa,
    Sha256,
    Slug,
    TareaId,
    ids_unicos,
)
from .hallazgos import Hallazgo
from .insumo import InsumoResuelto
from .snapshot import SimboloId, TipoSimbolo

# --- Contexto armado por el servidor -----------------------------------------


class ItemGobernanza(Contrato):
    """Entrada de gobernanza aplicable (ADR, policy, principio)."""

    id: str = Field(min_length=1, max_length=200)
    tipo: str = Field(min_length=1, max_length=40)
    titulo: str = Field(min_length=1, max_length=300)
    resumen: str = Field(max_length=4000)
    hash_version: str | None = Field(default=None, max_length=128)


class NodoContexto(Contrato):
    """Nodo de la rebanada del grafo. Nunca lleva texto de código."""

    repositorio: Slug
    rol: RolRepositorio
    simbolo: SimboloId
    nombre: str = Field(min_length=1, max_length=512)
    tipo: TipoSimbolo
    ruta: RutaRelativa
    motivo: str = Field(
        max_length=300, description="Por qué entra en la rebanada (impacto, llamada, prueba…)."
    )


class ContextoArmado(Contrato):
    gobernanza: list[ItemGobernanza] = Field(default_factory=list)
    gobernanza_consultada: GobernanzaConsultada
    grafo: list[NodoContexto] = Field(default_factory=list)
    hallazgos_previos: list[Hallazgo] = Field(
        default_factory=list, description="Hallazgos de la iteración anterior (gate_delta)."
    )
    insumos: list[InsumoResuelto] = Field(
        default_factory=list, description="Insumos del chat resueltos contra base_commit (R6)."
    )
    notas: str | None = Field(default=None, max_length=8000)


class AlcanceArchivos(Contrato):
    permitidos: list[Glob] = Field(default_factory=list)
    prohibidos: list[Glob] = Field(default_factory=list)


class ReporteRequerido(Contrato):
    snapshot: bool = True
    validacion: bool = False
    artefacto: bool = False


# --- Tipos de orden ------------------------------------------------------------


class Artefacto(StrEnum):
    spec = "spec"
    plan = "plan"
    tasks = "tasks"


class Complejidad(StrEnum):
    simple = "simple"
    complejo = "complejo"


class Tarea(Contrato):
    id: TareaId
    descripcion: str = Field(min_length=1, max_length=2000)
    criterios: list[CriterioId] = Field(default_factory=list)


class _OrdenBase(Mensaje):
    id: UUID4
    secuencia: int = Field(ge=1, description="Monótona por unidad; la vigente es la mayor.")
    unidad: AlcanceUnidad
    repositorio: Slug = Field(description="Repositorio primario donde se ejecuta la orden.")
    base_commit: Commit
    fase: Fase
    emitida_en: AwareDatetime
    expira_en: AwareDatetime | None = None
    instrucciones: str = Field(min_length=1, max_length=20_000)
    contexto: ContextoArmado
    alcance: AlcanceArchivos = Field(default_factory=AlcanceArchivos)
    criterios: list[Criterio] = Field(default_factory=list)
    comando_validacion: str | None = Field(default=None, max_length=2000)
    presupuesto: Presupuesto = Field(default_factory=Presupuesto)
    reporte_requerido: ReporteRequerido = Field(default_factory=ReporteRequerido)

    @model_validator(mode="after")
    def _base(self) -> _OrdenBase:
        ids_unicos([c.id for c in self.criterios], "criterios")
        if self.expira_en is not None and self.expira_en <= self.emitida_en:
            raise ValueError("expira_en debe ser posterior a emitida_en")
        if self.reporte_requerido.validacion and not self.comando_validacion:
            raise ValueError("una orden que exige validación necesita comando_validacion")
        return self


class OrdenRedactar(_OrdenBase):
    """Redactar un artefacto del protocolo (spec, plan o tasks)."""

    tipo: Literal["redactar"] = "redactar"
    artefacto: Artefacto
    ruta_artefacto: RutaRelativa
    reporte_requerido: ReporteRequerido = Field(
        default_factory=lambda: ReporteRequerido(snapshot=False, artefacto=True)
    )
    plantilla: str = Field(max_length=40_000)
    delegable: bool = Field(
        default=True,
        description="Si el arnés puede pedir al servidor que redacte en su lugar.",
    )

    @model_validator(mode="after")
    def _redactar(self) -> OrdenRedactar:
        if not self.reporte_requerido.artefacto:
            raise ValueError("una orden de redactar exige reportar el artefacto")
        return self


class OrdenRefinar(_OrdenBase):
    """Corregir un artefacto ya redactado según hallazgos del gate."""

    tipo: Literal["refinar"] = "refinar"
    artefacto: Artefacto
    ruta_artefacto: RutaRelativa
    reporte_requerido: ReporteRequerido = Field(
        default_factory=lambda: ReporteRequerido(snapshot=False, artefacto=True)
    )
    sha256_actual: Sha256
    hallazgos: list[Hallazgo] = Field(min_length=1)

    @model_validator(mode="after")
    def _refinar(self) -> OrdenRefinar:
        if not self.reporte_requerido.artefacto:
            raise ValueError("una orden de refinar exige reportar el artefacto")
        return self


class OrdenImplementar(_OrdenBase):
    """Implementar un grupo de tareas en el worktree de la unidad."""

    tipo: Literal["implementar"] = "implementar"
    grupo: GrupoId
    complejidad: Complejidad = Complejidad.simple
    tareas: list[Tarea] = Field(min_length=1)

    @model_validator(mode="after")
    def _implementar(self) -> OrdenImplementar:
        ids_unicos([t.id for t in self.tareas], "tareas")
        if not self.alcance.permitidos:
            raise ValueError("una orden de implementar necesita alcance.permitidos")
        if not self.reporte_requerido.snapshot:
            raise ValueError("una orden de implementar exige snapshot")
        conocidos = {c.id for c in self.criterios}
        huerfanos = sorted({c for t in self.tareas for c in t.criterios} - conocidos)
        if huerfanos:
            raise ValueError(f"tareas citan criterios que la orden no incluye: {', '.join(huerfanos)}")
        return self


class OrdenValidar(_OrdenBase):
    """Correr el comando de validación en local y reportar la salida."""

    tipo: Literal["validar"] = "validar"
    reporte_requerido: ReporteRequerido = Field(
        default_factory=lambda: ReporteRequerido(snapshot=False, validacion=True)
    )

    @model_validator(mode="after")
    def _validar(self) -> OrdenValidar:
        if not self.comando_validacion or not self.reporte_requerido.validacion:
            raise ValueError("una orden de validar exige comando_validacion y reportar validación")
        return self


OrdenDeTrabajo = Annotated[
    Union[OrdenRedactar, OrdenRefinar, OrdenImplementar, OrdenValidar],
    Field(discriminator="tipo"),
]
