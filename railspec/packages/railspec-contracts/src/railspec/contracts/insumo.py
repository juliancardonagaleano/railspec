"""Insumo (railspec.insumo/v1): lo que el chat exporta para que una unidad lo consuma.

No es una transcripción: es objetivo, hallazgos con referencias tipadas,
preguntas y restricciones. Nunca lleva texto de código en ningún nivel (R6)
y guarda el veredicto del gate de salida que lo dejó salir.
"""

from __future__ import annotations

import hashlib
import json
from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, field_validator, model_validator

from ._base import Contrato, Mensaje
from .chat import Afirmacion, VeredictoGateSalida
from .comun import (
    Actor,
    AlcanceWorkspace,
    Commit,
    NivelCodigo,
    RolRepositorio,
    Sha256,
    Slug,
    verificar_texto_plano,
)
from .referencias import Referencia

FORMATO_INSUMO = "railspec.insumo/v1"


class RepositorioInsumo(Contrato):
    repositorio: Slug
    rol: RolRepositorio
    base_commit: Commit


class Insumo(Mensaje):
    formato: Literal["railspec.insumo/v1"] = FORMATO_INSUMO
    id: UUID4
    alcance: AlcanceWorkspace
    repositorios: list[RepositorioInsumo] = Field(min_length=1)
    autor: Actor
    creado_en: AwareDatetime
    nivel_efectivo: NivelCodigo
    conversacion: UUID4 | None = None
    objetivo: str = Field(min_length=1, max_length=600)
    hallazgos: list[Afirmacion] = Field(min_length=1, max_length=50)
    preguntas_abiertas: list[str] = Field(default_factory=list, max_length=20)
    restricciones: list[str] = Field(default_factory=list, max_length=20)
    transcripcion_resumida: str | None = Field(
        default=None, max_length=8000, description="Anexo para humanos; no lo consume el motor."
    )
    veredicto_gate: VeredictoGateSalida
    sha256: Sha256 = Field(description="Hash de contenido_canonico(); lo calcula el servidor.")

    @field_validator("objetivo", "transcripcion_resumida")
    @classmethod
    def _plano(cls, v: str | None) -> str | None:
        return None if v is None else verificar_texto_plano(v, "insumo")

    @field_validator("preguntas_abiertas", "restricciones")
    @classmethod
    def _lista_plana(cls, v: list[str]) -> list[str]:
        for t in v:
            if not t or len(t) > 500:
                raise ValueError("entrada vacía o de más de 500 caracteres")
            verificar_texto_plano(t, "insumo")
        return v

    def contenido_canonico(self) -> bytes:
        """Serialización estable de lo que el hash cubre (todo menos el propio hash)."""

        datos = self.model_dump(mode="json", exclude={"sha256"})
        return json.dumps(datos, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()

    @model_validator(mode="after")
    def _coherente(self) -> Insumo:
        if not self.veredicto_gate.permitido:
            raise ValueError("un insumo solo existe si el gate de salida lo permitió")
        if hashlib.sha256(self.contenido_canonico()).hexdigest() != self.sha256:
            raise ValueError("sha256 no coincide con el contenido del insumo")
        return self


class ReferenciaResuelta(Contrato):
    referencia: Referencia
    obsoleta: bool = Field(
        default=False,
        description="True si el objeto cambió entre el commit del insumo y el base de la orden.",
    )


class InsumoResuelto(Contrato):
    """Lo que entra en ``contexto.insumos`` de una orden de trabajo."""

    id: UUID4
    sha256: Sha256
    objetivo: str = Field(min_length=1, max_length=600)
    hallazgos: list[Afirmacion] = Field(default_factory=list)
    restricciones: list[str] = Field(default_factory=list)
    referencias: list[ReferenciaResuelta] = Field(default_factory=list)
