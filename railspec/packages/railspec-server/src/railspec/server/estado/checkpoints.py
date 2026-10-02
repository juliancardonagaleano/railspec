"""``CheckpointStorage`` de Microsoft Agent Framework sobre Mongo.

MAF solo trae checkpoints oficiales para Cosmos DB; este es el protocolo de
seis métodos sobre la misma base del estado. Cada unidad tiene su propio
workflow (``nombre_workflow``), así que el nombre lleva el espacio de
nombres y se descompone en campos para que el filtro por workspace valga
también aquí.

El contenido se codifica con el codificador de MAF (pickle restringido a
tipos permitidos, embebido en JSON) y se guarda como texto: los dicts del
checkpoint pueden tener claves que Mongo no admite.

Toda lectura y escritura lleva org, workspace y unidad. ``save``, ``get_latest`` y los
``list_*`` los sacan del nombre del workflow. ``load`` y ``delete`` el protocolo de MAF
solo los llama con el id del checkpoint, así que usan el alcance del último workflow que
tocó el contexto de ejecución (``_alcance_actual``): ese id siempre sale de un ``save`` o
``get_latest`` previo del mismo flujo, que sí traen el nombre. Sin alcance en el contexto
fallan cerrado, y un id de otra unidad no se encuentra.
"""

from __future__ import annotations

import json
from contextvars import ContextVar
from datetime import datetime
from typing import Any

from agent_framework import InMemoryCheckpointStorage, WorkflowCheckpoint
from agent_framework._workflows._checkpoint_encoding import (  # API pública no reexportada
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from agent_framework.exceptions import WorkflowCheckpointException
from pymongo import DESCENDING, ReturnDocument
from pymongo.database import Database
from railspec.contracts.comun import AlcanceUnidad

PREFIJO = "railspec"

#: Unidad del último workflow que usó el almacén en este contexto de ejecución (ver el docstring).
#: Un contexto con otra unidad no ve este valor, y uno más reciente lo reemplaza: en el peor caso
#: ``load`` no encuentra el checkpoint, nunca devuelve el de otra unidad.
_alcance_actual: ContextVar[AlcanceUnidad | None] = ContextVar("railspec_checkpoints_alcance", default=None)


def nombre_workflow(alcance: AlcanceUnidad) -> str:
    return f"{PREFIJO}:{alcance.org}:{alcance.workspace}:{alcance.unidad}"


def alcance_de_workflow(nombre: str) -> AlcanceUnidad:
    prefijo, org, workspace, unidad = nombre.split(":")
    if prefijo != PREFIJO:
        raise WorkflowCheckpointException(f"workflow ajeno a Railspec: {nombre}")
    return AlcanceUnidad(org=org, workspace=workspace, unidad=unidad)


class CheckpointsMongo:
    """Implementa ``agent_framework.CheckpointStorage``."""

    def __init__(self, db: Database, *, tipos_permitidos: list[str] | None = None) -> None:
        self._col = db.checkpoints
        self._contadores = db.contadores
        # Mongo guarda milisegundos: dos checkpoints del mismo superpaso empatan en
        # ``timestamp``. El orden lo da ``orden``, un contador atómico por workflow.
        self._col.create_index([("workflow_name", 1), ("orden", DESCENDING)])
        self._permitidos = frozenset(tipos_permitidos or [])

    def _filtro(self, workflow_name: str) -> dict[str, Any]:
        """Filtro de la unidad del workflow; deja esa unidad en el contexto para ``load`` y ``delete``."""

        a = alcance_de_workflow(workflow_name)
        _alcance_actual.set(a)
        return {"org": a.org, "workspace": a.workspace, "unidad": a.unidad, "workflow_name": workflow_name}

    def _filtro_id(self, checkpoint_id: str) -> dict[str, Any]:
        """Filtro por id acotado a la unidad del contexto; falla cerrado si no hay ninguna."""

        a = _alcance_actual.get()
        if a is None:
            raise WorkflowCheckpointException(
                f"checkpoint {checkpoint_id}: sin unidad en el contexto (antes va save, get_latest o list_*)"
            )
        return {"_id": checkpoint_id, "org": a.org, "workspace": a.workspace, "unidad": a.unidad}

    def _decodificar(self, doc: dict[str, Any]) -> WorkflowCheckpoint:
        datos = decode_checkpoint_value(json.loads(doc["datos"]), allowed_types=self._permitidos)
        return WorkflowCheckpoint.from_dict(datos)

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        codificado = encode_checkpoint_value(checkpoint.to_dict())
        # Falla al guardar, no al restaurar, si algún tipo no está permitido.
        decode_checkpoint_value(codificado, allowed_types=self._permitidos)
        filtro = self._filtro(checkpoint.workflow_name)
        previo = self._col.find_one({"_id": checkpoint.checkpoint_id, **filtro}, {"orden": 1})
        orden = (
            previo["orden"]
            if previo
            else self._contadores.find_one_and_update(
                {"_id": f"checkpoints:{checkpoint.workflow_name}"},
                {"$inc": {"n": 1}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )["n"]
        )
        # Con upsert, un id que ya existe en otra unidad no coincide con el filtro y el insert choca
        # con la clave: ``DuplicateKeyError``, nunca se pisa el checkpoint ajeno.
        self._col.replace_one(
            {"_id": checkpoint.checkpoint_id, **filtro},
            {
                "_id": checkpoint.checkpoint_id,
                **filtro,
                "timestamp": datetime.fromisoformat(checkpoint.timestamp),
                "orden": orden,
                "previo": checkpoint.previous_checkpoint_id,
                "datos": json.dumps(codificado),
            },
            upsert=True,
        )
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        doc = self._col.find_one(self._filtro_id(checkpoint_id))
        if doc is None:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        return self._decodificar(doc)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        return [self._decodificar(d) for d in self._col.find(self._filtro(workflow_name)).sort("orden", 1)]

    async def delete(self, checkpoint_id: str) -> bool:
        return self._col.delete_one(self._filtro_id(checkpoint_id)).deleted_count == 1

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        doc = self._col.find_one(self._filtro(workflow_name), sort=[("orden", DESCENDING)])
        return self._decodificar(doc) if doc else None

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        return [d["_id"] for d in self._col.find(self._filtro(workflow_name), {"_id": 1}).sort("orden", 1)]


__all__ = ["CheckpointsMongo", "InMemoryCheckpointStorage", "alcance_de_workflow", "nombre_workflow"]
