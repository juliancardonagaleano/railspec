"""Evento de sincronización entre proxy local y servidor.

Registro bidireccional e idempotente por ``id``. Cada dirección es un flujo
con ``secuencia`` monótona por unidad, de modo que el receptor aplica en
orden y descarta duplicados. El mismo evento alimenta el flujo en vivo de la
consola (SSE, R8): la consola se suscribe al flujo remoto→local de las
unidades que puede ver.

Reglas de conflicto (fijas): en el protocolo gana el remoto; en el código
gana el local; un push del local reindexa el canónico y descarta la
superposición de la unidad hasta ese commit.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Literal, Union

from pydantic import UUID4, AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    Actor,
    AlcanceUnidad,
    Canal,
    Commit,
    EstadoFase,
    Fase,
    GateFase,
    HashArbol,
    Slug,
    Veredicto,
)


class Direccion(StrEnum):
    local_a_remoto = "local-a-remoto"
    remoto_a_local = "remoto-a-local"


REGLAS_CONFLICTO = {
    "protocolo": "remoto",
    "codigo": "local",
    "push": "reindexar-canonico-y-descartar-superposicion",
}


# --- Cargas local → remoto ----------------------------------------------------


class SnapshotSubido(Contrato):
    tipo: Literal["snapshot.subido"] = "snapshot.subido"
    snapshot_id: UUID4
    repositorio: Slug
    base_commit: Commit
    hash_arbol: HashArbol


class OrdenReportada(Contrato):
    tipo: Literal["orden.reportada"] = "orden.reportada"
    orden_id: UUID4
    secuencia_orden: int = Field(ge=1)


class CommitEmpujado(Contrato):
    tipo: Literal["commit.empujado"] = "commit.empujado"
    repositorio: Slug
    rama: str = Field(min_length=1, max_length=255)
    commit: Commit


# --- Cargas remoto → local ----------------------------------------------------


class OrdenEmitida(Contrato):
    tipo: Literal["orden.emitida"] = "orden.emitida"
    orden_id: UUID4
    secuencia_orden: int = Field(ge=1)


class VeredictoEmitido(Contrato):
    tipo: Literal["veredicto.emitido"] = "veredicto.emitido"
    gate: GateFase
    veredicto: Veredicto


class EstadoActualizado(Contrato):
    tipo: Literal["estado.actualizado"] = "estado.actualizado"
    version: int = Field(ge=1)
    fase: Fase
    estado: EstadoFase


class CheckpointSolicitado(Contrato):
    tipo: Literal["checkpoint.solicitado"] = "checkpoint.solicitado"
    checkpoint_id: UUID4


class CheckpointResuelto(Contrato):
    tipo: Literal["checkpoint.resuelto"] = "checkpoint.resuelto"
    checkpoint_id: UUID4
    canal: Canal


class UnidadIntegrada(Contrato):
    tipo: Literal["unidad.integrada"] = "unidad.integrada"
    especificacion_viva: str = Field(min_length=1, max_length=512)
    pr_url: str | None = Field(default=None, max_length=512)


CargaEvento = Annotated[
    Union[
        SnapshotSubido,
        OrdenReportada,
        CommitEmpujado,
        OrdenEmitida,
        VeredictoEmitido,
        EstadoActualizado,
        CheckpointSolicitado,
        CheckpointResuelto,
        UnidadIntegrada,
    ],
    Field(discriminator="tipo"),
]

DIRECCION_POR_TIPO: dict[str, Direccion] = {
    "snapshot.subido": Direccion.local_a_remoto,
    "orden.reportada": Direccion.local_a_remoto,
    "commit.empujado": Direccion.local_a_remoto,
    "orden.emitida": Direccion.remoto_a_local,
    "veredicto.emitido": Direccion.remoto_a_local,
    "estado.actualizado": Direccion.remoto_a_local,
    "checkpoint.solicitado": Direccion.remoto_a_local,
    "checkpoint.resuelto": Direccion.remoto_a_local,
    "unidad.integrada": Direccion.remoto_a_local,
}


class EventoSync(Mensaje):
    id: UUID4 = Field(description="Clave de idempotencia.")
    direccion: Direccion
    unidad: AlcanceUnidad
    secuencia: int = Field(ge=1, description="Monótona por (unidad, dirección).")
    emitido_en: AwareDatetime
    actor: Actor
    causado_por: UUID4 | None = Field(default=None, description="Evento que lo originó.")
    carga: CargaEvento

    @model_validator(mode="after")
    def _direccion(self) -> EventoSync:
        esperada = DIRECCION_POR_TIPO[self.carga.tipo]
        if self.direccion != esperada:
            raise ValueError(f"{self.carga.tipo} viaja {esperada.value}, no {self.direccion.value}")
        return self
