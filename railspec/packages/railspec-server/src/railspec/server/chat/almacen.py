"""Persistencia del chat en Mongo: conversaciones, mensajes, huellas, consumo de fuga e insumos.

Todo documento lleva el espacio de nombres del workspace y toda consulta lo
exige (misma regla que ``estado/mongo.py``), también las lecturas y los
reemplazos por ``_id``: un id que existe en otro workspace no se lee ni se
pisa (un ``replace_one`` con upsert sobre él lanza ``DuplicateKeyError``).
Conversaciones, mensajes y huellas caducan con el TTL de la conversación
(``_expira``); los fragmentos de código leídos nunca se guardan, solo sus
huellas. Los insumos no caducan: los consumen unidades que pueden arrancar
días después.

Excepciones, a propósito:

* ``conversacion(id_, autor_id)`` es la puerta de entrada por id: las rutas
  ``/v1/chat/conversaciones/{id}`` no llevan org ni workspace, así que el
  workspace se conoce al leer el documento. Se acota por la persona
  (``_autor``) y quien llama exige el rol sobre ``conv.alcance`` antes de
  hacer nada más; todo lo demás (mensajes, huellas, commits) se acota con el
  alcance de esa conversación.
* El consumo de fuga por persona y día (``chat_fuga_usuario``) es de la
  organización: su ``_id`` es ``org/github_id/día`` y el tope diario se
  comparte entre todos los workspaces de la persona.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from pymongo import ASCENDING
from pymongo.database import Database
from railspec.contracts.chat import Conversacion, MensajeChat
from railspec.contracts.comun import AlcanceWorkspace
from railspec.contracts.insumo import Insumo

from .normalizacion import HuellasContexto


def _doc(modelo: Any) -> dict[str, Any]:
    return json.loads(modelo.model_dump_json())


def _ws(alcance: AlcanceWorkspace) -> dict[str, str]:
    return {"alcance.org": alcance.org, "alcance.workspace": alcance.workspace}


class AlmacenChat:
    def __init__(self, db: Database, *, crear_indices: bool = True) -> None:
        self.db = db
        if crear_indices:
            self.crear_indices()

    def crear_indices(self) -> None:
        db = self.db
        for coleccion in ("chat_conversaciones", "chat_mensajes", "chat_huellas", "chat_fuga_usuario"):
            db[coleccion].create_index("_expira", expireAfterSeconds=0)
        db.chat_conversaciones.create_index(
            [("alcance.org", ASCENDING), ("alcance.workspace", ASCENDING), ("_autor", ASCENDING)]
        )
        db.chat_mensajes.create_index([("_conversacion", ASCENDING), ("_orden", ASCENDING)])
        db.chat_huellas.create_index("_conversacion")
        db.insumos.create_index([("alcance.org", ASCENDING), ("alcance.workspace", ASCENDING)])

    # --- conversaciones ---------------------------------------------------------------------

    def guardar_conversacion(
        self, c: Conversacion, *, n_tokens: int, commits: dict[str, str], autor_id: int
    ) -> None:
        self.db.chat_conversaciones.replace_one(
            {"_id": str(c.id), **_ws(c.alcance)},
            {
                "_id": str(c.id),
                "_expira": c.expira_en.astimezone(UTC),
                "_n_tokens": n_tokens,
                "_commits": commits,
                "_autor": autor_id,
                **_doc(c),
            },
            upsert=True,
        )

    def conversacion(self, id_: UUID, autor_id: int) -> tuple[Conversacion, dict[str, Any]] | None:
        """La conversación de ``autor_id`` y sus metadatos internos (``n_tokens``, ``commits``, ``autor``).

        Es la única lectura por id sin org ni workspace (ver el docstring del módulo): la de otra
        persona no sale de Mongo, y quien llama exige el rol sobre ``conv.alcance`` antes de seguir.
        """

        doc = self.db.chat_conversaciones.find_one({"_id": str(id_), "_autor": autor_id})
        if doc is None:
            return None
        meta = {
            "n_tokens": doc["_n_tokens"],
            "commits": dict(doc.get("_commits", {})),
            "autor": doc["_autor"],
        }
        return Conversacion.model_validate({k: v for k, v in doc.items() if not k.startswith("_")}), meta

    def conversaciones_de(
        self, alcance: AlcanceWorkspace, autor_id: int, ahora: datetime, limite: int
    ) -> list[Conversacion]:
        """Las conversaciones vigentes de ``autor_id`` en el workspace, recientes primero.

        El filtro de workspace y de autor va en la consulta, no después: ninguna conversación de
        otro workspace o de otra persona sale de Mongo. Se ordena por ``creada_en`` ya validado
        (el documento guarda la fecha como texto, que no ordena bien con husos distintos).
        """

        cursor = self.db.chat_conversaciones.find(
            {**_ws(alcance), "_autor": autor_id, "_expira": {"$gt": ahora.astimezone(UTC)}}
        )
        encontradas = [
            Conversacion.model_validate({k: v for k, v in d.items() if not k.startswith("_")}) for d in cursor
        ]
        encontradas.sort(key=lambda c: c.creada_en, reverse=True)
        return encontradas[:limite]

    def actualizar_commits(self, alcance: AlcanceWorkspace, id_: UUID, commits: dict[str, str]) -> None:
        """Fija el commit visto de cada repositorio la primera vez que aparece (nunca lo cambia)."""

        for repo, commit in commits.items():
            self.db.chat_conversaciones.update_one(
                {"_id": str(id_), **_ws(alcance), f"_commits.{repo}": {"$exists": False}},
                {"$set": {f"_commits.{repo}": commit}},
            )

    # --- mensajes -----------------------------------------------------------------------------

    def agregar_mensaje(self, m: MensajeChat, expira: datetime) -> None:
        orden = self.db.chat_mensajes.count_documents(
            {"_conversacion": str(m.conversacion), **_ws(m.alcance)}
        )
        self.db.chat_mensajes.insert_one(
            {
                "_id": str(m.id),
                "_conversacion": str(m.conversacion),
                "_orden": orden,
                "_expira": expira.astimezone(UTC),
                **_doc(m),
            }
        )

    def reemplazar_mensaje(self, m: MensajeChat) -> None:
        self.db.chat_mensajes.update_one(
            {"_id": str(m.id), "_conversacion": str(m.conversacion), **_ws(m.alcance)}, {"$set": _doc(m)}
        )

    def mensajes(self, alcance: AlcanceWorkspace, conversacion: UUID) -> list[MensajeChat]:
        cursor = self.db.chat_mensajes.find({"_conversacion": str(conversacion), **_ws(alcance)}).sort(
            "_orden", ASCENDING
        )
        return [
            MensajeChat.model_validate({k: v for k, v in d.items() if not k.startswith("_")}) for d in cursor
        ]

    # --- huellas --------------------------------------------------------------------------------

    def agregar_huellas(
        self, alcance: AlcanceWorkspace, conversacion: UUID, h: HuellasContexto, expira: datetime
    ) -> None:
        self.db.chat_huellas.insert_one(
            {
                "alcance": {"org": alcance.org, "workspace": alcance.workspace},
                "_conversacion": str(conversacion),
                "_expira": expira.astimezone(UTC),
                "tokens": sorted(h.tokens),
                "caracteres": sorted(h.caracteres),
                "identificadores": sorted(h.identificadores),
            }
        )

    def huellas(self, alcance: AlcanceWorkspace, conversacion: UUID) -> HuellasContexto:
        total = HuellasContexto()
        for d in self.db.chat_huellas.find({"_conversacion": str(conversacion), **_ws(alcance)}):
            total.unir([HuellasContexto(set(d["tokens"]), set(d["caracteres"]), set(d["identificadores"]))])
        return total

    # --- presupuesto de fuga por persona y día -----------------------------------------------------

    @staticmethod
    def _clave_fuga(org: str, github_id: int, ahora: datetime) -> str:
        return f"{org}/{github_id}/{ahora.astimezone(UTC).date().isoformat()}"

    def fuga_usuario(self, org: str, github_id: int, ahora: datetime) -> int:
        doc = self.db.chat_fuga_usuario.find_one({"_id": self._clave_fuga(org, github_id, ahora)})
        return int(doc["caracteres"]) if doc else 0

    def sumar_fuga_usuario(self, org: str, github_id: int, ahora: datetime, caracteres: int) -> int:
        clave = self._clave_fuga(org, github_id, ahora)
        dia = datetime.combine(ahora.astimezone(UTC).date(), datetime.min.time(), tzinfo=UTC)
        self.db.chat_fuga_usuario.update_one(
            {"_id": clave},
            {"$inc": {"caracteres": caracteres}, "$set": {"_expira": dia + timedelta(days=2)}},
            upsert=True,
        )
        return self.fuga_usuario(org, github_id, ahora)

    # --- insumos ----------------------------------------------------------------------------------

    def guardar_insumo(self, insumo: Insumo) -> None:
        self.db.insumos.insert_one({"_id": str(insumo.id), **_doc(insumo)})

    def obtener_insumo(self, alcance: AlcanceWorkspace, id_: UUID) -> Insumo | None:
        doc = self.db.insumos.find_one({"_id": str(id_), **_ws(alcance)})
        return Insumo.model_validate({k: v for k, v in doc.items() if not k.startswith("_")}) if doc else None
