"""Snapshot: lo que el proxy local sube del worktree de una unidad.

Es el único vehículo por el que información del código sale del clon, así
que aquí se imponen las reglas de la política de código propietario:
en ``restringido`` no viaja texto de código (ni diff ni fragmentos), los
embeddings llegan ya calculados en local y un snapshot con secretos
detectados no es válido.
"""

from __future__ import annotations

import base64
import binascii
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import UUID4, AwareDatetime, Field, StringConstraints, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    CLASE_CODIGO_INTERNO,
    AlcanceUnidad,
    Commit,
    HashArbol,
    NivelCodigo,
    RutaRelativa,
    Sha256,
    Slug,
    ids_unicos,
)

SimboloId = Annotated[
    str,
    StringConstraints(pattern=r"^[0-9a-f]{64}$"),
    Field(
        description=(
            "Identificador estable del símbolo: SHA-256 de "
            "'<repositorio>\\0<ruta>\\0<tipo>\\0<nombre calificado>'."
        )
    ),
]

#: Tope del diff en bytes; por encima el proxy cae a ``solo-hashes``.
DIFF_MAX_BYTES = 2_000_000
EMBEDDING_DIMENSIONES = 768
EMBEDDING_MODELO = "nomic-embed-code"


class EstadoArchivo(StrEnum):
    agregado = "agregado"
    modificado = "modificado"
    borrado = "borrado"
    renombrado = "renombrado"


class CambioArchivo(Contrato):
    ruta: RutaRelativa
    estado: EstadoArchivo
    ruta_anterior: RutaRelativa | None = None
    sha256_antes: Sha256 | None = None
    sha256_despues: Sha256 | None = None

    @model_validator(mode="after")
    def _coherente(self) -> CambioArchivo:
        e = self.estado
        if (e == EstadoArchivo.renombrado) != (self.ruta_anterior is not None):
            raise ValueError("ruta_anterior solo y siempre en archivos renombrados")
        if e == EstadoArchivo.agregado and self.sha256_antes is not None:
            raise ValueError("un archivo agregado no tiene sha256_antes")
        if e == EstadoArchivo.borrado and self.sha256_despues is not None:
            raise ValueError("un archivo borrado no tiene sha256_despues")
        if e != EstadoArchivo.borrado and self.sha256_despues is None:
            raise ValueError(f"un archivo {e.value} necesita sha256_despues")
        if e in (EstadoArchivo.modificado, EstadoArchivo.borrado) and self.sha256_antes is None:
            raise ValueError(f"un archivo {e.value} necesita sha256_antes")
        return self


class TipoSimbolo(StrEnum):
    modulo = "modulo"
    clase = "clase"
    interfaz = "interfaz"
    funcion = "funcion"
    metodo = "metodo"
    variable = "variable"
    otro = "otro"


class Simbolo(Contrato):
    id: SimboloId
    nombre: str = Field(min_length=1, max_length=512, description="Nombre calificado.")
    tipo: TipoSimbolo
    ruta: RutaRelativa
    linea_inicio: int = Field(ge=1)
    linea_fin: int = Field(ge=1)
    sha256: Sha256 = Field(description="Hash del texto del fragmento; el texto no viaja aquí.")

    @model_validator(mode="after")
    def _rango(self) -> Simbolo:
        if self.linea_fin < self.linea_inicio:
            raise ValueError("linea_fin anterior a linea_inicio")
        return self


class Relacion(StrEnum):
    llama = "llama"
    importa = "importa"
    hereda = "hereda"
    implementa = "implementa"
    define = "define"
    prueba = "prueba"


class Arista(Contrato):
    origen: SimboloId
    destino: SimboloId
    relacion: Relacion


class Embedding(Contrato):
    simbolo: SimboloId
    modelo: Literal["nomic-embed-code"] = EMBEDDING_MODELO
    dimensiones: Literal[768] = EMBEDDING_DIMENSIONES
    cuantizacion: Literal["int8"] = "int8"
    vector_b64: str = Field(
        pattern=r"^[A-Za-z0-9+/]+={0,2}$",
        description="Vector int8 codificado en base64 estándar; 768 bytes decodificados.",
    )

    @model_validator(mode="after")
    def _longitud(self) -> Embedding:
        try:
            crudo = base64.b64decode(self.vector_b64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise ValueError("vector_b64 no es base64 válido") from exc
        if len(crudo) != self.dimensiones:
            raise ValueError(f"vector de {len(crudo)} bytes, se esperaban {self.dimensiones}")
        return self


class MotorIndice(Contrato):
    nombre: Literal["codebase-memory-mcp"] = "codebase-memory-mcp"
    version: str = Field(pattern=r"^[0-9]+\.[0-9]+\.[0-9]+([-+].+)?$")


class DeltaIndice(Contrato):
    """Delta del índice local entre el commit base y el árbol de trabajo."""

    motor: MotorIndice
    simbolos_upsert: list[Simbolo] = Field(default_factory=list)
    simbolos_borrados: list[SimboloId] = Field(default_factory=list)
    aristas_agregadas: list[Arista] = Field(default_factory=list)
    aristas_borradas: list[Arista] = Field(default_factory=list)
    embeddings: list[Embedding] = Field(default_factory=list)

    @model_validator(mode="after")
    def _coherente(self) -> DeltaIndice:
        ids = [s.id for s in self.simbolos_upsert]
        ids_unicos(ids, "simbolos_upsert")
        cruzados = set(ids) & set(self.simbolos_borrados)
        if cruzados:
            raise ValueError("un símbolo no puede estar a la vez en upsert y borrados")
        sin_simbolo = {e.simbolo for e in self.embeddings} - set(ids)
        if sin_simbolo:
            raise ValueError("todo embedding debe referir un símbolo de simbolos_upsert")
        return self


class Fragmento(Contrato):
    """Texto de código; solo permitido en niveles ``interno`` y ``abierto``."""

    ruta: RutaRelativa
    simbolo: SimboloId | None = None
    sha256: Sha256
    texto: str = Field(max_length=200_000, json_schema_extra=CLASE_CODIGO_INTERNO)


class EscaneoSecretos(Contrato):
    herramienta: str = Field(min_length=1, max_length=80)
    version: str = Field(min_length=1, max_length=40)
    hallazgos: int = Field(
        ge=0,
        le=0,
        description="Siempre 0: el proxy no sube snapshots con secretos detectados.",
    )


class ModoDelta(StrEnum):
    completo = "completo"
    solo_hashes = "solo-hashes"


class Snapshot(Mensaje):
    id: UUID4
    unidad: AlcanceUnidad
    repositorio: Slug
    base_commit: Commit
    hash_arbol: HashArbol
    creado_en: AwareDatetime
    nivel_codigo: NivelCodigo
    modo_delta: ModoDelta
    archivos: list[CambioArchivo]
    diff: str | None = Field(
        default=None,
        max_length=DIFF_MAX_BYTES,
        json_schema_extra=CLASE_CODIGO_INTERNO,
        description="Diff unificado base..árbol; prohibido en nivel restringido.",
    )
    fragmentos: list[Fragmento] | None = Field(
        default=None, description="Texto acotado a símbolos tocados; prohibido en restringido."
    )
    delta_indice: DeltaIndice | None = None
    escaneo_secretos: EscaneoSecretos

    @model_validator(mode="after")
    def _politica(self) -> Snapshot:
        ids_unicos([a.ruta for a in self.archivos], "rutas de archivos")
        if self.nivel_codigo == NivelCodigo.restringido:
            if self.diff is not None or self.fragmentos is not None:
                raise ValueError("nivel restringido: el snapshot no puede llevar diff ni fragmentos")
        if self.modo_delta == ModoDelta.solo_hashes:
            if self.diff is not None or self.fragmentos is not None or self.delta_indice is not None:
                raise ValueError("modo solo-hashes: solo viajan los hashes de archivo")
        elif self.delta_indice is None:
            raise ValueError("modo completo exige delta_indice")
        if self.fragmentos:
            tocadas = {a.ruta for a in self.archivos}
            fuera = sorted({f.ruta for f in self.fragmentos} - tocadas)
            if fuera:
                raise ValueError(f"fragmentos fuera de los archivos tocados: {', '.join(fuera)}")
        return self
