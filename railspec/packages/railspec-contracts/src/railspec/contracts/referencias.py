"""Referencias tipadas: la única forma en que el chat y los insumos señalan código.

Una referencia apunta a algo verificable (símbolo en un commit, rango de
líneas, nodo del grafo, unidad, criterio, gobernanza, decisión) y nunca
contiene su texto: quien la consume la resuelve en local contra su clon.
"""

from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AwareDatetime, Field, model_validator

from ._base import Contrato
from .comun import Commit, CriterioId, RutaRelativa, Slug, UnidadId
from .snapshot import SimboloId, TipoSimbolo


class RefSimbolo(Contrato):
    tipo: Literal["simbolo"] = "simbolo"
    repositorio: Slug
    commit: Commit
    simbolo: SimboloId
    nombre: str = Field(min_length=1, max_length=512)
    tipo_simbolo: TipoSimbolo
    ruta: RutaRelativa


class RefArchivo(Contrato):
    tipo: Literal["archivo"] = "archivo"
    repositorio: Slug
    commit: Commit
    ruta: RutaRelativa
    linea_inicio: int | None = Field(default=None, ge=1)
    linea_fin: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _rango(self) -> RefArchivo:
        if (self.linea_inicio is None) != (self.linea_fin is None):
            raise ValueError("linea_inicio y linea_fin van juntas")
        if self.linea_inicio is not None and self.linea_fin < self.linea_inicio:  # type: ignore[operator]
            raise ValueError("linea_fin anterior a linea_inicio")
        return self


class FrescuraGrafo(Contrato):
    """Desde 1.9: qué tan al día está el grafo canónico de un repositorio consultado.

    Lo calcula el servidor al responder ``graph.query`` y lo repite el contexto de una orden
    (``ContextoArmado.grafo_frescura``). El canónico lo avanza solo ``graph.index`` desde CI: un
    repositorio puede no haberse indexado nunca, o llevar tiempo sin índice nuevo.
    """

    indexado: bool = Field(
        description=(
            "False si el repositorio nunca recibió un índice canónico: sus consultas no devuelven "
            "nada de él (vacío no significa que el símbolo no exista)."
        )
    )
    commit: Commit | None = Field(default=None, description="Commit del canónico; None si nunca se indexó.")
    indexado_en: AwareDatetime | None = Field(
        default=None,
        description=(
            "Instante en que se aplicó el último índice canónico. None si nunca se indexó o si el "
            "índice es anterior a que el servidor guardara este instante."
        ),
    )
    desactualizado: bool = Field(
        default=False,
        description=(
            "True si ``indexado_en`` es más antiguo que el plazo del servidor "
            "(``RAILSPEC_GRAFO_FRESCURA_HORAS``; 0 = no avisa). Es un aviso de tiempo, no una "
            "comparación con el repositorio: ver ``avisos`` y el proxy local."
        ),
    )


class RefNodoGrafo(Contrato):
    """Nodo de la capa remota (cluster, proceso) que no es un símbolo."""

    tipo: Literal["nodo-grafo"] = "nodo-grafo"
    repositorio: Slug
    commit: Commit
    clase: Literal["cluster", "proceso"]
    id: str = Field(min_length=1, max_length=200)
    nombre: str = Field(min_length=1, max_length=300)


class RefUnidad(Contrato):
    tipo: Literal["unidad"] = "unidad"
    workspace: Slug
    unidad: UnidadId


class RefCriterio(Contrato):
    tipo: Literal["criterio"] = "criterio"
    workspace: Slug
    unidad: UnidadId
    criterio: CriterioId


class RefGobernanza(Contrato):
    tipo: Literal["gobernanza"] = "gobernanza"
    id: str = Field(min_length=1, max_length=200)
    proveedor: str = Field(default="pce", min_length=1, max_length=80)
    hash_version: str | None = Field(default=None, max_length=128)


class RefDecision(Contrato):
    tipo: Literal["decision"] = "decision"
    workspace: Slug
    id: str = Field(min_length=1, max_length=200)


Referencia = Annotated[
    Union[
        RefSimbolo,
        RefArchivo,
        RefNodoGrafo,
        RefUnidad,
        RefCriterio,
        RefGobernanza,
        RefDecision,
    ],
    Field(discriminator="tipo"),
]
