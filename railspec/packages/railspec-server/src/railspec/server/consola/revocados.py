"""Sesiones revocadas de la consola, compartidas entre réplicas.

Cada cookie de sesión y cada token ``api`` derivado de ella llevan el id de su
sesión (``sid``). Cerrar sesión guarda ese id aquí y desde ese momento ni la
cookie ni los tokens de esa sesión valen en ninguna réplica. El registro caduca
solo (índice TTL sobre ``_expira``): cuando la sesión revocada ya habría
expirado por su propia fecha, el id deja de hacer falta.

Misma base Mongo que el resto de la consola. Es la excepción a la regla de que toda
consulta lleva organización y workspace: una sesión es de una persona, no de un
workspace, y el registro guarda solo el id aleatorio de sesión (``sid``) y su
caducidad, sin ningún dato de organización. Por eso ``revocar`` y ``revocada`` van por
``sid`` solo.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pymongo import ASCENDING
from pymongo.database import Database

COLECCION = "sesiones_revocadas"
#: Holgura sobre la expiración de la sesión (relojes de réplicas distintas).
MARGEN = timedelta(minutes=2)


class RevocadosMongo:
    def __init__(self, db: Database) -> None:
        self._col = db[COLECCION]
        self._col.create_index([("_expira", ASCENDING)], expireAfterSeconds=0)

    def revocar(self, sid: str, expira: datetime) -> None:
        """Marca ``sid`` como revocada hasta ``expira`` (la caducidad de la sesión) más un margen."""

        caduca = (expira + MARGEN).astimezone(UTC)
        self._col.replace_one({"_id": sid}, {"_id": sid, "_expira": caduca}, upsert=True)

    def revocada(self, sid: str) -> bool:
        return self._col.find_one({"_id": sid}, {"_id": 1}) is not None
