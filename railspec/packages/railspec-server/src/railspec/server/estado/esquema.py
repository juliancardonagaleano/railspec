"""Versión del esquema del estado.

El estado (Mongo o Postgres) es de varias réplicas y sobrevive a los despliegues: una versión nueva del
servidor puede dejar documentos con una forma que la anterior no entiende. El documento ``estado`` de la
colección ``meta_esquema`` guarda la versión que dejó el último servidor que arrancó:

- Base vacía: se escribe ``VERSION_ESQUEMA``.
- Versión guardada menor: se aplican en orden las migraciones de ``MIGRACIONES`` (``n`` -> ``n + 1``).
  Cada una debe ser idempotente: si dos réplicas arrancan a la vez, ambas pueden ejecutarla.
- Versión guardada mayor: ``EsquemaIncompatible`` y el servidor no arranca, para que un retroceso de
  despliegue no escriba sobre datos que no entiende.

Subir ``VERSION_ESQUEMA`` es cambiar la forma de lo guardado de modo que el código anterior no lo lea:
exige registrar su migración en ``MIGRACIONES``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError

log = logging.getLogger("railspec.server")

VERSION_ESQUEMA = 1

COLECCION = "meta_esquema"
ID_DOCUMENTO = "estado"

#: ``n`` -> función que lleva el estado de la versión ``n`` a la ``n + 1`` (recibe la base).
MIGRACIONES: dict[int, Callable[[Any], None]] = {}


class EsquemaIncompatible(RuntimeError):
    """El estado lo dejó un servidor más nuevo, o falta una migración para llegar a esta versión."""


@dataclass(frozen=True)
class VersionEsquema:
    codigo: int
    almacenada: int


def _ahora() -> datetime:
    return datetime.now(UTC)


def asegurar_esquema(
    db: Any,
    *,
    version: int = VERSION_ESQUEMA,
    migraciones: Mapping[int, Callable[[Any], None]] | None = None,
) -> VersionEsquema:
    """Registra o migra la versión del esquema; ``EsquemaIncompatible`` si no se puede."""

    pasos = MIGRACIONES if migraciones is None else migraciones
    col = db[COLECCION]
    inicial = {"$setOnInsert": {"version": version, "creado": _ahora()}}
    try:
        doc = col.find_one_and_update(
            {"_id": ID_DOCUMENTO}, inicial, upsert=True, return_document=ReturnDocument.AFTER
        )
    except DuplicateKeyError:  # otra réplica la creó entre la lectura y la escritura
        doc = col.find_one({"_id": ID_DOCUMENTO})
    almacenada = int(doc["version"])
    if almacenada > version:
        raise EsquemaIncompatible(
            f"el estado está en el esquema {almacenada} y este servidor solo entiende hasta el {version}: "
            "despliega una versión igual o más nueva (un retroceso no es seguro sobre ese estado)"
        )
    while almacenada < version:
        paso = pasos.get(almacenada)
        if paso is None:
            raise EsquemaIncompatible(
                f"el estado está en el esquema {almacenada} y no hay migración al {almacenada + 1}"
            )
        log.info("migrando el estado del esquema %d al %d", almacenada, almacenada + 1)
        paso(db)
        col.update_one(
            {"_id": ID_DOCUMENTO, "version": almacenada},
            {"$set": {"version": almacenada + 1, "migrado": _ahora()}},
        )
        almacenada += 1
    return VersionEsquema(codigo=version, almacenada=almacenada)


__all__ = [
    "COLECCION",
    "MIGRACIONES",
    "VERSION_ESQUEMA",
    "EsquemaIncompatible",
    "VersionEsquema",
    "asegurar_esquema",
]
