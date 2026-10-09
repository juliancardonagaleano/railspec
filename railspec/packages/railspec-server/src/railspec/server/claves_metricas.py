"""Claves de ``GET /metrics`` administradas desde la consola: una por origen, revocables.

Quien administra la plataforma crea una clave con un nombre (el origen que la usará: «Grafana», «verificación
manual»...). La clave solo se muestra al crearla; se guarda su SHA-256, el prefijo para reconocerla en la
lista, quién la creó y cuándo se usó por última vez. Revocarla la invalida en todas las réplicas al instante
(cada ``/metrics`` la busca en la base). ``RAILSPEC_METRICAS_TOKEN`` sigue valiendo a la vez, como respaldo.

Un SHA-256 sin sal basta: la clave son 256 bits aleatorios, no una contraseña que se pueda adivinar.

Datos de la plataforma, no de una organización (como ``sesiones_revocadas``): el filtro no lleva org ni
workspace. Ver ``test_aislamiento_almacenes``.
"""

from __future__ import annotations

import hashlib
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict
from pymongo import DESCENDING
from pymongo.database import Database

COLECCION = "metricas_claves"
PREFIJO = "rsm1."
#: Caracteres del principio de la clave que se guardan en claro para reconocerla en la lista.
LARGO_PREFIJO = len(PREFIJO) + 6
#: Claves activas a la vez. Una por origen; más de esto suele ser claves olvidadas.
MAX_ACTIVAS = 20
#: ``ultimo_uso`` se escribe como mucho una vez por este intervalo: un scraper consulta cada pocos segundos.
REFRESCO_USO = timedelta(minutes=1)


class ErrorClaveMetricas(Exception):
    def __init__(self, estado: int, detalle: str) -> None:
        super().__init__(detalle)
        self.estado = estado
        self.detalle = detalle


class ClaveMetricas(BaseModel):
    """Lo que se lista de una clave. Nunca lleva la clave ni su hash."""

    model_config = ConfigDict(extra="ignore")

    id: str
    nombre: str
    prefijo: str
    creada_por: str
    creada_en: datetime
    ultimo_uso: datetime | None = None
    revocada_en: datetime | None = None
    revocada_por: str | None = None

    @property
    def activa(self) -> bool:
        return self.revocada_en is None


def _hash(secreto: str) -> str:
    return hashlib.sha256(secreto.encode()).hexdigest()


def _utc(valor: datetime) -> datetime:
    return valor.astimezone(UTC)


class ClavesMetricasMongo:
    def __init__(self, db: Database) -> None:
        self._col = db[COLECCION]

    def listar(self) -> list[ClaveMetricas]:
        return [ClaveMetricas(**d) for d in self._col.find({}).sort([("creada_en", DESCENDING)])]

    def crear(self, nombre: str, creada_por: str, ahora: datetime) -> tuple[ClaveMetricas, str]:
        """La clave nueva y su secreto, que no vuelve a salir de aquí."""

        nombre = nombre.strip()
        if not nombre:
            raise ErrorClaveMetricas(422, "la clave necesita un nombre: el origen que la usará")
        activas = list(self._col.find({"revocada_en": None}, {"nombre": 1}))
        if any(d["nombre"].casefold() == nombre.casefold() for d in activas):
            raise ErrorClaveMetricas(
                409, f"ya hay una clave activa llamada «{nombre}»: revócala o usa otro nombre"
            )
        if len(activas) >= MAX_ACTIVAS:
            raise ErrorClaveMetricas(409, f"ya hay {MAX_ACTIVAS} claves activas: revoca las que no uses")
        secreto = PREFIJO + secrets.token_urlsafe(32)
        clave = ClaveMetricas(
            id=str(uuid.uuid4()),
            nombre=nombre,
            prefijo=secreto[:LARGO_PREFIJO],
            creada_por=creada_por,
            creada_en=_utc(ahora),
        )
        self._col.insert_one({"_id": _hash(secreto), **clave.model_dump()})
        return clave, secreto

    def revocar(self, id_: str, revocada_por: str, ahora: datetime) -> ClaveMetricas | None:
        """``None`` si no existe. Revocar una ya revocada no cambia nada."""

        doc = self._col.find_one({"id": id_})
        if doc is None:
            return None
        if doc.get("revocada_en") is None:
            cambios = {"revocada_en": _utc(ahora), "revocada_por": revocada_por}
            self._col.update_one({"_id": doc["_id"], "revocada_en": None}, {"$set": cambios})
            doc = self._col.find_one({"_id": doc["_id"]}) or {**doc, **cambios}
        return ClaveMetricas(**doc)

    def validar(self, secreto: str, ahora: datetime) -> ClaveMetricas | None:
        """La clave activa con ese secreto, o ``None``. Anota el uso (como mucho una vez por minuto)."""

        if not secreto.startswith(PREFIJO):
            return None
        doc = self._col.find_one({"_id": _hash(secreto), "revocada_en": None})
        if doc is None:
            return None
        uso = doc.get("ultimo_uso")
        ahora = _utc(ahora)
        if uso is None or ahora - _utc(uso) >= REFRESCO_USO:
            self._col.update_one({"_id": doc["_id"]}, {"$set": {"ultimo_uso": ahora}})
            doc["ultimo_uso"] = ahora
        return ClaveMetricas(**doc)

    def hay_activas(self) -> bool:
        return self._col.find_one({"revocada_en": None}, {"_id": 1}) is not None
