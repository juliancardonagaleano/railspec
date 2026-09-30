"""``CheckpointStorage`` de Microsoft Agent Framework sobre Mongo.

MAF solo trae checkpoints oficiales para Cosmos DB; este es el protocolo de
seis métodos sobre la misma base del estado. Cada unidad tiene su propio
workflow (``nombre_workflow``), así que el nombre lleva el espacio de
nombres y se descompone en campos para que el filtro por workspace valga
también aquí.

El contenido se codifica con el codificador de MAF (pickle restringido a
tipos permitidos, embebido en JSON) y se guarda como texto: los dicts del
checkpoint pueden tener claves que Mongo no admite.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from agent_framework import InMemoryCheckpointStorage, WorkflowCheckpoint
from agent_framework._workflows._checkpoint_encoding import (  # API pública no reexportada
    decode_checkpoint_value,
    encode_checkpoint_value,
)
from agent_framework.exceptions import WorkflowCheckpointException
from pymongo import DESCENDING
from pymongo.database import Database

from railspec.contracts.comun import AlcanceUnidad

PREFIJO = "railspec"


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
        self._col.create_index([("workflow_name", 1), ("timestamp", DESCENDING)])
        self._permitidos = frozenset(tipos_permitidos or [])

    def _filtro(self, workflow_name: str) -> dict[str, Any]:
        a = alcance_de_workflow(workflow_name)
        return {"org": a.org, "workspace": a.workspace, "unidad": a.unidad, "workflow_name": workflow_name}

    def _decodificar(self, doc: dict[str, Any]) -> WorkflowCheckpoint:
        datos = decode_checkpoint_value(json.loads(doc["datos"]), allowed_types=self._permitidos)
        return WorkflowCheckpoint.from_dict(datos)

    async def save(self, checkpoint: WorkflowCheckpoint) -> str:
        codificado = encode_checkpoint_value(checkpoint.to_dict())
        # Falla al guardar, no al restaurar, si algún tipo no está permitido.
        decode_checkpoint_value(codificado, allowed_types=self._permitidos)
        self._col.replace_one(
            {"_id": checkpoint.checkpoint_id},
            {
                "_id": checkpoint.checkpoint_id,
                **self._filtro(checkpoint.workflow_name),
                "timestamp": datetime.fromisoformat(checkpoint.timestamp),
                "previo": checkpoint.previous_checkpoint_id,
                "datos": json.dumps(codificado),
            },
            upsert=True,
        )
        return checkpoint.checkpoint_id

    async def load(self, checkpoint_id: str) -> WorkflowCheckpoint:
        doc = self._col.find_one({"_id": checkpoint_id})
        if doc is None:
            raise WorkflowCheckpointException(f"No checkpoint found with ID {checkpoint_id}")
        return self._decodificar(doc)

    async def list_checkpoints(self, *, workflow_name: str) -> list[WorkflowCheckpoint]:
        return [self._decodificar(d) for d in self._col.find(self._filtro(workflow_name)).sort("timestamp", 1)]

    async def delete(self, checkpoint_id: str) -> bool:
        return self._col.delete_one({"_id": checkpoint_id}).deleted_count == 1

    async def get_latest(self, *, workflow_name: str) -> WorkflowCheckpoint | None:
        doc = self._col.find_one(self._filtro(workflow_name), sort=[("timestamp", DESCENDING)])
        return self._decodificar(doc) if doc else None

    async def list_checkpoint_ids(self, *, workflow_name: str) -> list[str]:
        return [d["_id"] for d in self._col.find(self._filtro(workflow_name), {"_id": 1}).sort("timestamp", 1)]


__all__ = ["CheckpointsMongo", "InMemoryCheckpointStorage", "alcance_de_workflow", "nombre_workflow"]
