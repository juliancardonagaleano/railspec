"""Hallazgos estructurados que producen los críticos del gate.

Sustituyen la prosa libre con severidad del kit: con esta forma la
refutación y la convergencia se deciden en código (punto 6 del análisis).
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, model_validator

from ._base import Contrato
from .comun import CriterioId, GateFase, RutaRelativa, Severidad

HallazgoId = Annotated[
    str,
    StringConstraints(pattern=r"^H-[0-9]{1,4}$"),
    Field(description="Identificador del hallazgo dentro del gate, p. ej. H-12."),
]


class Cita(Contrato):
    """Dónde está lo que el hallazgo señala.

    Para artefactos (spec, plan, tasks) basta ``seccion``; para código se
    exige ``ruta`` con rango de líneas.
    """

    ruta: RutaRelativa | None = None
    linea_inicio: int | None = Field(default=None, ge=1)
    linea_fin: int | None = Field(default=None, ge=1)
    seccion: str | None = Field(default=None, max_length=200)

    @model_validator(mode="after")
    def _coherente(self) -> Cita:
        if self.ruta is None and self.seccion is None:
            raise ValueError("una cita necesita ruta o seccion")
        if (self.linea_inicio is None) != (self.linea_fin is None):
            raise ValueError("linea_inicio y linea_fin van juntas")
        if self.linea_inicio is not None and self.linea_fin is not None:
            if self.linea_fin < self.linea_inicio:
                raise ValueError("linea_fin anterior a linea_inicio")
            if self.ruta is None:
                raise ValueError("un rango de líneas exige ruta")
        return self


class Hallazgo(Contrato):
    id: HallazgoId
    gate: GateFase
    lente: str = Field(min_length=1, max_length=80, description="Lente del crítico que lo produjo.")
    severidad: Severidad
    criterio: CriterioId | None = Field(default=None, description="CA-NN afectado, si aplica.")
    titulo: str = Field(min_length=1, max_length=200)
    cita: Cita
    evidencia: str = Field(min_length=1, max_length=4000)
    propuesta: str | None = Field(default=None, max_length=4000)
    refutado: bool = Field(
        default=False,
        description="True si el refutador lo descartó; un hallazgo refutado no bloquea.",
    )


def bloqueantes(hallazgos: list[Hallazgo]) -> list[Hallazgo]:
    """Hallazgos que impiden aprobar: alta o media y no refutados."""

    return [h for h in hallazgos if not h.refutado and h.severidad in (Severidad.alta, Severidad.media)]
