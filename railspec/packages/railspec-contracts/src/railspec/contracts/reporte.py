"""Reporte de una orden de trabajo: entrada de ``unit.report``."""

from __future__ import annotations

import hashlib
from enum import StrEnum

from pydantic import UUID4, AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import AlcanceUnidad, Commit, Proveedor, Sha256, TareaId, ids_unicos
from .mandato import MAX_DECISIONES_POR_REPORTE, DecisionPropuesta
from .orden import Artefacto
from .snapshot import Snapshot

SALIDA_MAX_CARACTERES = 16_000


class ResultadoOrden(StrEnum):
    completado = "completado"
    fallido = "fallido"
    bloqueado = "bloqueado"


class ResultadoValidacion(Contrato):
    comando: str = Field(min_length=1, max_length=2000)
    codigo_salida: int
    duracion_ms: int = Field(ge=0)
    salida: str = Field(
        max_length=SALIDA_MAX_CARACTERES,
        description="Cola de stdout+stderr, recortada por el proxy al tope.",
    )
    recortada: bool = False


class ArtefactoRedactado(Contrato):
    tipo: Artefacto
    contenido: str = Field(min_length=1, max_length=200_000)
    sha256: Sha256

    @model_validator(mode="after")
    def _hash(self) -> ArtefactoRedactado:
        real = hashlib.sha256(self.contenido.encode("utf-8")).hexdigest()
        if real != self.sha256:
            raise ValueError("sha256 no coincide con el contenido del artefacto")
        return self


class UsoModeloArnes(Contrato):
    """Telemetría opcional del modelo que usó el arnés para ejecutar la orden."""

    proveedor: Proveedor | None = None
    modelo: str = Field(min_length=1, max_length=120)
    tokens_entrada: int | None = Field(default=None, ge=0)
    tokens_salida: int | None = Field(default=None, ge=0)


class ReporteOrden(Mensaje):
    orden_id: UUID4
    secuencia: int = Field(ge=1)
    unidad: AlcanceUnidad
    base_commit: Commit = Field(description="Debe coincidir con el de la orden vigente.")
    resultado: ResultadoOrden
    reportado_en: AwareDatetime
    snapshot: Snapshot | None = None
    validacion: ResultadoValidacion | None = None
    artefacto: ArtefactoRedactado | None = None
    tareas_completadas: list[TareaId] = Field(default_factory=list)
    motivo: str | None = Field(
        default=None, max_length=4000, description="Obligatorio si fallido o bloqueado."
    )
    uso_modelo: UsoModeloArnes | None = None
    decisiones: list[DecisionPropuesta] = Field(
        default_factory=list,
        max_length=MAX_DECISIONES_POR_REPORTE,
        description=(
            "Desde 1.11: decisiones que el arnés tomó apoyándose en las delegaciones del mandato de la "
            "unidad. El servidor las registra para revisión humana; una que cita una delegación "
            "`reservada` o inexistente se rechaza (hay que reportar `bloqueado`)."
        ),
    )

    @model_validator(mode="after")
    def _coherente(self) -> ReporteOrden:
        ids_unicos(list(self.tareas_completadas), "tareas_completadas")
        if self.resultado != ResultadoOrden.completado and not self.motivo:
            raise ValueError(f"un reporte {self.resultado.value} necesita motivo")
        if self.snapshot is not None:
            if self.snapshot.unidad != self.unidad:
                raise ValueError("el snapshot pertenece a otra unidad")
            if self.snapshot.base_commit != self.base_commit:
                raise ValueError("el snapshot tiene otro base_commit que el reporte")
        return self
