"""Paquete de unidad (railspec.unidad/v1), desde 1.4: importar y exportar unidades.

``unit.import`` crea una unidad nueva a partir de un paquete (una unidad del
kit SDD o una exportada por otro Railspec) y ``unit.export`` devuelve el
paquete de una unidad existente. El paquete lleva artefactos redactados
(spec, plan, tasks), nunca código.

Semántica de la importación, que aplica el servidor:

- La unidad nace con id nuevo y modo ``interactivo`` salvo que el paquete
  traiga otro; ``supervisado`` y ``desatendido`` no se importan porque
  exigen un mandato.
- Los artefactos del paquete quedan aprobados por importación a nombre del
  humano del token, con un registro de auditoría ``importacion``. Solo un
  humano importa: un agente recibe ``fuera-de-alcance``.
- El motor emite la orden de ``fase_retomar`` y su gate corre normal. Una
  unidad cerrada (``fase_retomar = done``) entra con el gate de código
  escalado con causa ``importado`` y rehabilitado por ese humano: Railspec
  no finge un gate que no corrió.
- ``historial_gates`` y ``depende_de_original`` son informativos; no se
  re-evalúan ni crean dependencias.
- Idempotente por (workspace, repositorio primario, ``origen.tipo``,
  ``origen.id_original``): una segunda importación devuelve la unidad
  existente con ``ya_existia = true``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import MODOS_CON_MANDATO, Fase, Modo, Perfil, Riesgo, Slug
from .orden import Artefacto
from .reporte import ArtefactoRedactado

FORMATO_PAQUETE = "railspec.unidad/v1"


class OrigenPaquete(Contrato):
    tipo: Literal["sdd-kit", "railspec"]
    id_original: str = Field(min_length=1, max_length=200)
    repositorio: Slug | None = None


class ArtefactosPaquete(Contrato):
    spec: ArtefactoRedactado | None = None
    plan: ArtefactoRedactado | None = None
    tasks: ArtefactoRedactado | None = None

    @model_validator(mode="after")
    def _reglas(self) -> ArtefactosPaquete:
        for clave in Artefacto:
            artefacto = getattr(self, clave.value)
            if artefacto is not None and artefacto.tipo != clave:
                raise ValueError(f"el artefacto en {clave.value} es de tipo {artefacto.tipo.value}")
        if self.plan is not None and self.spec is None:
            raise ValueError("un plan sin spec no se importa")
        if self.tasks is not None and self.plan is None:
            raise ValueError("unas tasks sin plan no se importan")
        return self

    def presentes(self) -> int:
        return sum(getattr(self, a.value) is not None for a in Artefacto)


class GateImportado(Contrato):
    """Resultado de gate del origen; informativo, no se re-evalúa."""

    gate: str = Field(min_length=1, max_length=40)
    resultado: str = Field(min_length=1, max_length=40)
    iteraciones: int | None = Field(default=None, ge=0)
    cerrado_en: AwareDatetime | None = None


#: Fases válidas para retomar según cuántos artefactos trae el paquete (prefijo spec, plan, tasks).
FASES_RETOMAR: dict[int, frozenset[Fase]] = {
    0: frozenset({Fase.research, Fase.spec}),
    1: frozenset({Fase.plan}),
    2: frozenset({Fase.tasks}),
    3: frozenset({Fase.aprobacion, Fase.implement, Fase.done}),
}


class PaqueteUnidad(Mensaje):
    formato: Literal["railspec.unidad/v1"] = FORMATO_PAQUETE
    origen: OrigenPaquete
    titulo: str = Field(min_length=1, max_length=200)
    pedido: str = Field(min_length=1, max_length=20_000)
    artefactos: ArtefactosPaquete = Field(default_factory=ArtefactosPaquete)
    fase_retomar: Fase = Field(
        description="La primera fase cuyo artefacto falta; done si la unidad estaba cerrada."
    )
    modo: Modo | None = None
    riesgo: Riesgo | None = None
    perfil: Perfil | None = None
    governance_refs: list[str] = Field(default_factory=list)
    comando_validacion: str | None = Field(default=None, max_length=2000)
    depende_de_original: list[str] = Field(default_factory=list, description="Ids de origen; informativo.")
    historial_gates: list[GateImportado] = Field(default_factory=list)

    @model_validator(mode="after")
    def _reglas(self) -> PaqueteUnidad:
        presentes = self.artefactos.presentes()
        if self.fase_retomar not in FASES_RETOMAR[presentes]:
            validas = ", ".join(sorted(f.value for f in FASES_RETOMAR[presentes]))
            raise ValueError(f"con {presentes} artefactos fase_retomar debe ser una de: {validas}")
        if self.modo in MODOS_CON_MANDATO:
            raise ValueError(f"modo {self.modo.value} exige un mandato y no se importa")
        return self
