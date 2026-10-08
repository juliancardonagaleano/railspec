"""Acceso a datos de la consola sobre la misma base Mongo que el motor.

Mismas reglas que ``estado/mongo.py``: toda consulta lleva su organización y,
si el dato cuelga de un workspace, también el workspace, aunque se busque por
``_id``. Las excepciones son datos de la organización entera o de la plataforma:
``organizaciones`` y ``organizacion`` (una organización es su propio espacio de
nombres), ``workspaces`` (los de una organización), ``roles``, ``rol``,
``asignaciones``, ``catalogo``, ``suscripciones`` y ``suscripciones_claves`` (por organización; el workspace
de una asignación
lo mira quien la leyó para decidir si puede tocarla) y ``asignaciones_de_sujeto``,
que busca por persona en todas las organizaciones para armar ``GET /yo``; solo
devuelve las asignaciones de esa persona. ``test_aislamiento_almacenes`` lista cada
método con su regla.

Las colecciones y la forma de sus claves son las de ``COLECCIONES`` en los
contratos y las que ya escribe ``AlmacenMongo.guardar_configuracion``
(perfiles y presupuestos por campos, vínculos con ``_id`` ``org/ws/repo``,
roles con ``_id`` uuid), así que la consola y el motor leen lo mismo.
Las escrituras usan bloqueo optimista por ``version`` (R4). Crear es atómico: perfiles,
presupuestos y proveedores de contexto llevan un ``_id`` determinista (el mismo que escribe
el motor, ver ``estado.mongo.clave_*``), así que dos creaciones simultáneas de la misma
clave no pueden coexistir: la segunda choca con ``DuplicateKeyError`` y sale como 409.
Los documentos anteriores a esa clave (``_id`` ObjectId) se siguen leyendo y editando por su
clave natural y conservan su ``_id``.
"""

from __future__ import annotations

import base64
import json
import uuid
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad
from railspec.contracts.repositorio import (
    AsignacionRol,
    ModeloCatalogo,
    Organizacion,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    RegistroAuditoria,
    SuscripcionModelo,
    VinculoRepositorio,
    Workspace,
)
from railspec.contracts.snapshot import Snapshot

from ..avisos.modelo import ConfigAvisos
from ..estado.mongo import clave_perfil, clave_presupuesto, clave_proveedor_contexto, filtro_sujetos

#: Workspace reservado para auditar cambios a nivel organización.
WORKSPACE_ORG = "org"

#: Proyección del snapshot: la consola solo usa rutas y símbolos, nunca el texto de código
#: (``diff`` y ``fragmentos`` de los niveles interno y abierto), así que ni se carga.
SIN_TEXTO_DE_CODIGO = {"diff": 0, "fragmentos": 0}


def _doc(modelo: Any) -> dict[str, Any]:
    return json.loads(modelo.model_dump_json())


def _limpio(doc: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def _clave_unidad(a: AlcanceUnidad) -> str:
    return f"{a.org}/{a.workspace}/{a.unidad}"


class AlmacenConsola:
    def __init__(self, db: Database) -> None:
        self.db = db
        db.workspaces.create_index([("alcance.org", ASCENDING)])
        db.roles.create_index([("org", ASCENDING), ("workspace", ASCENDING)])
        db.reportes.create_index([("_clave", ASCENDING)])
        db.catalogo_estado.create_index([("org", ASCENDING)])
        for coleccion in ("avisos_eventos", "avisos_envios", "avisos_historial"):
            db[coleccion].create_index("_expira", expireAfterSeconds=0)

    # --- escritura con bloqueo optimista ---------------------------------------------------------

    def _guardar(
        self,
        coleccion: str,
        filtro: dict[str, Any],
        entidad: Any,
        version_esperada: int | None,
        clave: str | None = None,
    ):
        """``filtro`` identifica la entidad; si trae ``_id`` se usa también como clave del documento.

        ``clave`` es el ``_id`` determinista de un documento nuevo cuando el filtro es solo la clave
        natural (perfiles, presupuestos, proveedores de contexto). Editar no lo toca: un documento
        antiguo con ``_id`` ObjectId se reemplaza sin cambiarlo.
        """

        col = self.db[coleccion]
        if version_esperada is None:
            actual = col.find_one(filtro, {"version": 1})
            if actual is not None:
                raise ConflictoVersion(0, int(actual.get("version", 0)))
            doc = _doc(entidad) | _ids(filtro)
            if clave is not None:
                doc["_id"] = clave
            try:
                col.insert_one(doc)
            except DuplicateKeyError:
                # Otra creación se adelantó entre la comprobación y el insert.
                actual = col.find_one(filtro, {"version": 1})
                raise ConflictoVersion(0, int(actual.get("version", 0)) if actual else 0) from None
            return entidad
        r = col.replace_one({**filtro, "version": version_esperada}, _doc(entidad) | _ids(filtro))
        if r.matched_count != 1:
            actual = col.find_one(filtro, {"version": 1})
            raise ConflictoVersion(version_esperada, int(actual["version"]) if actual else 0)
        return entidad

    def _uno(self, coleccion: str, filtro: dict[str, Any], modelo: type):
        doc = self.db[coleccion].find_one(filtro)
        return modelo.model_validate(_limpio(doc)) if doc else None

    def _varios(self, coleccion: str, filtro: dict[str, Any], modelo: type, orden: list) -> list:
        return [modelo.model_validate(_limpio(d)) for d in self.db[coleccion].find(filtro).sort(orden)]

    # --- organizaciones y workspaces -------------------------------------------------------------

    def organizaciones(self, ids: set[str] | None) -> list[Organizacion]:
        filtro = {} if ids is None else {"id": {"$in": sorted(ids)}}
        return self._varios("organizaciones", filtro, Organizacion, [("id", ASCENDING)])

    def organizacion(self, org: str) -> Organizacion | None:
        return self._uno("organizaciones", {"id": org}, Organizacion)

    def guardar_organizacion(self, o: Organizacion, version_esperada: int | None) -> Organizacion:
        return self._guardar("organizaciones", {"id": o.id, "_id": o.id}, o, version_esperada)

    def workspaces(self, org: str) -> list[Workspace]:
        return self._varios("workspaces", {"alcance.org": org}, Workspace, [("alcance.workspace", ASCENDING)])

    def workspace(self, org: str, ws: str) -> Workspace | None:
        return self._uno("workspaces", {"alcance.org": org, "alcance.workspace": ws}, Workspace)

    def guardar_workspace(self, w: Workspace, version_esperada: int | None) -> Workspace:
        a = w.alcance
        filtro = {"alcance.org": a.org, "alcance.workspace": a.workspace, "_id": f"{a.org}/{a.workspace}"}
        return self._guardar("workspaces", filtro, w, version_esperada)

    # --- roles (R3) ------------------------------------------------------------------------------

    def roles(
        self, org: str, workspace: str | None = None, solo_workspace: bool = False
    ) -> list[AsignacionRol]:
        filtro: dict[str, Any] = {"org": org}
        if solo_workspace:
            filtro["workspace"] = workspace
        return self._varios("roles", filtro, AsignacionRol, [("workspace", ASCENDING), ("rol", ASCENDING)])

    def rol(self, org: str, id_: str) -> AsignacionRol | None:
        return self._uno("roles", {"org": org, "_id": id_}, AsignacionRol)

    def guardar_rol(self, a: AsignacionRol) -> AsignacionRol:
        return self._guardar("roles", {"org": a.org, "workspace": a.workspace, "_id": str(a.id)}, a, None)

    def borrar_rol(self, org: str, id_: str) -> bool:
        """Borra la asignación en su propio workspace (el que quien llama ya leyó con ``rol``)."""

        actual = self.rol(org, id_)
        if actual is None:
            return False
        filtro = {"org": org, "workspace": actual.workspace, "_id": id_}
        return self.db.roles.delete_one(filtro).deleted_count == 1

    def asignaciones(self, org: str, github_id: int, equipos: frozenset[int]) -> list[AsignacionRol]:
        filtro = {"org": org, "$or": filtro_sujetos(github_id, equipos)}
        return self._varios("roles", filtro, AsignacionRol, [("workspace", ASCENDING)])

    def asignaciones_de_sujeto(self, github_id: int, equipos: frozenset[int]) -> list[AsignacionRol]:
        filtro = {"$or": filtro_sujetos(github_id, equipos)}
        return self._varios("roles", filtro, AsignacionRol, [("org", ASCENDING)])

    # --- vínculos de repositorio -----------------------------------------------------------------

    def vinculos(self, org: str, ws: str) -> list[VinculoRepositorio]:
        return self._varios(
            "vinculos",
            {"alcance.org": org, "alcance.workspace": ws},
            VinculoRepositorio,
            [("alcance.repositorio", ASCENDING)],
        )

    def vinculo(self, a: AlcanceRepositorio) -> VinculoRepositorio | None:
        return self._uno("vinculos", _filtro_vinculo(a), VinculoRepositorio)

    def guardar_vinculo(self, v: VinculoRepositorio, version_esperada: int | None) -> VinculoRepositorio:
        a = v.alcance
        filtro = {**_filtro_vinculo(a), "_id": f"{a.org}/{a.workspace}/{a.repositorio}"}
        return self._guardar("vinculos", filtro, v, version_esperada)

    def borrar_vinculo(self, a: AlcanceRepositorio) -> bool:
        return self.db.vinculos.delete_one(_filtro_vinculo(a)).deleted_count == 1

    # --- perfiles, presupuestos, proveedores de contexto, catálogo -------------------------------

    def perfiles(self, org: str, ws: str | None) -> list[PerfilConfig]:
        filtro = {"org": org, "workspace": {"$in": [None, ws]} if ws else None}
        return self._varios(
            "perfiles", filtro, PerfilConfig, [("workspace", ASCENDING), ("nombre", ASCENDING)]
        )

    def perfil(self, org: str, ws: str | None, nombre: str) -> PerfilConfig | None:
        return self._uno("perfiles", {"org": org, "workspace": ws, "nombre": nombre}, PerfilConfig)

    def guardar_perfil(self, p: PerfilConfig, version_esperada: int | None) -> PerfilConfig:
        filtro = {"org": p.org, "workspace": p.workspace, "nombre": p.nombre.value}
        clave = clave_perfil(p.org, p.workspace, p.nombre.value)
        return self._guardar("perfiles", filtro, p, version_esperada, clave)

    def presupuestos(self, org: str, ws: str | None) -> list[PresupuestoConfig]:
        filtro = {"org": org, "workspace": {"$in": [None, ws]} if ws else None}
        return self._varios("presupuestos", filtro, PresupuestoConfig, [("workspace", ASCENDING)])

    def presupuesto(self, org: str, ws: str | None) -> PresupuestoConfig | None:
        return self._uno("presupuestos", {"org": org, "workspace": ws}, PresupuestoConfig)

    def guardar_presupuesto(self, p: PresupuestoConfig, version_esperada: int | None) -> PresupuestoConfig:
        filtro = {"org": p.org, "workspace": p.workspace}
        clave = clave_presupuesto(p.org, p.workspace)
        return self._guardar("presupuestos", filtro, p, version_esperada, clave)

    def proveedores_contexto(self, org: str, ws: str | None) -> list[ProveedorContexto]:
        filtro = {"org": org, "workspace": {"$in": [None, ws]} if ws else None}
        orden = [("workspace", ASCENDING), ("rol", ASCENDING), ("nombre", ASCENDING)]
        return self._varios("proveedores_contexto", filtro, ProveedorContexto, orden)

    def proveedor_contexto(self, org: str, ws: str | None, rol: str, nombre: str) -> ProveedorContexto | None:
        filtro = {"org": org, "workspace": ws, "rol": rol, "nombre": nombre}
        return self._uno("proveedores_contexto", filtro, ProveedorContexto)

    def guardar_proveedor_contexto(
        self, p: ProveedorContexto, version_esperada: int | None
    ) -> ProveedorContexto:
        filtro = {"org": p.org, "workspace": p.workspace, "rol": p.rol.value, "nombre": p.nombre}
        clave = clave_proveedor_contexto(p.org, p.workspace, p.rol.value, p.nombre)
        return self._guardar("proveedores_contexto", filtro, p, version_esperada, clave)

    def borrar_proveedor_contexto(self, org: str, ws: str | None, rol: str, nombre: str) -> bool:
        filtro = {"org": org, "workspace": ws, "rol": rol, "nombre": nombre}
        return self.db.proveedores_contexto.delete_one(filtro).deleted_count == 1

    def catalogo(self, org: str) -> list[ModeloCatalogo]:
        orden = [("proveedor", ASCENDING), ("modelo", ASCENDING)]
        return self._varios("catalogo", {"org": org}, ModeloCatalogo, orden)

    def guardar_estado_catalogo(self, estado: Any) -> None:
        """Último intento de lectura del catálogo de un proveedor para una organización (``EstadoLectura``).

        Un documento por ``org/proveedor`` en ``catalogo_estado``: lo que ve la consola es lo mismo en
        todas las réplicas, y no guarda nada del proveedor más que el resultado y un error ya saneado.
        """

        doc = estado.a_doc()
        clave = f"{doc['org']}/{doc['proveedor']}"
        self.db.catalogo_estado.replace_one({"_id": clave}, {"_id": clave, **doc}, upsert=True)

    def estados_catalogo(self, org: str) -> list[dict[str, Any]]:
        cursor = self.db.catalogo_estado.find({"org": org}).sort([("proveedor", ASCENDING)])
        return [_limpio(d) for d in cursor]

    # --- suscripciones de modelos (1.6) ----------------------------------------------------------

    def suscripciones(self, org: str) -> list[SuscripcionModelo]:
        return self._varios("suscripciones", {"org": org}, SuscripcionModelo, [("id", ASCENDING)])

    def suscripcion(self, org: str, id_: str) -> SuscripcionModelo | None:
        return self._uno("suscripciones", {"org": org, "id": id_}, SuscripcionModelo)

    def guardar_suscripcion(self, s: SuscripcionModelo, version_esperada: int | None) -> SuscripcionModelo:
        filtro = {"org": s.org, "id": s.id, "_id": f"{s.org}/{s.id}"}
        return self._guardar("suscripciones", filtro, s, version_esperada)

    def borrar_suscripcion(self, org: str, id_: str) -> bool:
        """Borra la suscripción y su clave cifrada."""

        self.borrar_clave_suscripcion(org, id_)
        return self.db.suscripciones.delete_one({"org": org, "id": id_}).deleted_count == 1

    def perfiles_con_suscripcion(self, org: str, id_: str) -> list[PerfilConfig]:
        """Perfiles de la organización (de cualquier workspace) asociados a la suscripción."""

        orden = [("workspace", ASCENDING), ("nombre", ASCENDING)]
        return self._varios("perfiles", {"org": org, "suscripcion": id_}, PerfilConfig, orden)

    def clave_suscripcion(self, org: str, id_: str) -> str | None:
        """La clave **cifrada** (``proveedores.cifrado``); el valor en claro no se guarda en ningún lado."""

        doc = self.db.suscripciones_claves.find_one({"org": org, "_id": f"{org}/{id_}"})
        return str(doc["cifrada"]) if doc else None

    def guardar_clave_suscripcion(self, org: str, id_: str, cifrada: str) -> None:
        clave = f"{org}/{id_}"
        doc = {"_id": clave, "org": org, "id": id_, "cifrada": cifrada}
        self.db.suscripciones_claves.replace_one({"org": org, "_id": clave}, doc, upsert=True)

    def borrar_clave_suscripcion(self, org: str, id_: str) -> None:
        self.db.suscripciones_claves.delete_one({"org": org, "_id": f"{org}/{id_}"})

    # --- avisos e informes (estado del servidor, no contrato) --------------------------------------
    # Todo cuelga de la organización. ``avisos_secretos`` guarda el URL del webhook de Teams **cifrado**.

    def avisos_config(self, org: str) -> ConfigAvisos | None:
        return self._uno("avisos_config", {"org": org}, ConfigAvisos)

    def guardar_avisos_config(self, c: ConfigAvisos, version_esperada: int | None) -> ConfigAvisos:
        return self._guardar("avisos_config", {"org": c.org, "_id": c.org}, c, version_esperada)

    def avisos_con_informe(self) -> list[ConfigAvisos]:
        """Configuraciones activas con informe semanal, de todas las organizaciones (las barre el fondo)."""

        filtro = {"activo": True, "informe.activo": True}
        return self._varios("avisos_config", filtro, ConfigAvisos, [("org", ASCENDING)])

    def secreto_aviso(self, org: str, nombre: str) -> str | None:
        doc = self.db.avisos_secretos.find_one({"org": org, "_id": f"{org}/{nombre}"})
        return str(doc["cifrado"]) if doc else None

    def guardar_secreto_aviso(self, org: str, nombre: str, cifrado: str) -> None:
        clave = f"{org}/{nombre}"
        doc = {"_id": clave, "org": org, "nombre": nombre, "cifrado": cifrado}
        self.db.avisos_secretos.replace_one({"org": org, "_id": clave}, doc, upsert=True)

    def borrar_secreto_aviso(self, org: str, nombre: str) -> None:
        self.db.avisos_secretos.delete_one({"org": org, "_id": f"{org}/{nombre}"})

    def registrar_evento_aviso(self, org: str, clave: str, doc: dict[str, Any], ahora: datetime) -> bool:
        """Guarda un escalado; ``False`` si esa clave ya estaba (otra réplica o un reintento lo contó)."""

        en = ahora.astimezone(UTC)
        try:
            self.db.avisos_eventos.insert_one(
                {**doc, "_id": f"{org}/{clave}", "org": org, "en": en, "_expira": en + timedelta(days=120)}
            )
        except DuplicateKeyError:
            return False
        return True

    def eventos_aviso(self, org: str, desde: datetime, hasta: datetime) -> list[dict[str, Any]]:
        rango = {"$gte": desde.astimezone(UTC), "$lt": hasta.astimezone(UTC)}
        return [_limpio(d) for d in self.db.avisos_eventos.find({"org": org, "en": rango})]

    def reservar_envio(self, org: str, clave: str, canal: str, ahora: datetime) -> bool:
        """Reserva el envío ``clave`` por ``canal``; ``False`` si otro ya lo tiene (no se envía dos veces)."""

        en = ahora.astimezone(UTC)
        try:
            self.db.avisos_envios.insert_one(
                {
                    "_id": f"{org}/{clave}/{canal}",
                    "org": org,
                    "en": en,
                    "_expira": en + timedelta(days=40),
                }
            )
        except DuplicateKeyError:
            return False
        return True

    def liberar_envio(self, org: str, clave: str, canal: str) -> None:
        self.db.avisos_envios.delete_one({"org": org, "_id": f"{org}/{clave}/{canal}"})

    def envios_desde(self, org: str, desde: datetime) -> int:
        return self.db.avisos_envios.count_documents({"org": org, "en": {"$gte": desde.astimezone(UTC)}})

    def registrar_historial_aviso(self, org: str, doc: dict[str, Any], ahora: datetime) -> None:
        en = ahora.astimezone(UTC)
        self.db.avisos_historial.insert_one(
            {**doc, "_id": str(uuid.uuid4()), "org": org, "en": en, "_expira": en + timedelta(days=30)}
        )

    def historial_avisos(self, org: str, limite: int = 20) -> list[dict[str, Any]]:
        cursor = self.db.avisos_historial.find({"org": org}).sort([("en", DESCENDING)]).limit(limite)
        return [_limpio(d) for d in cursor]

    # --- auditoría -------------------------------------------------------------------------------

    def registrar_auditoria(self, registro: RegistroAuditoria) -> None:
        self.db.auditoria.insert_one({"_id": str(registro.id), **_doc(registro)})

    def auditoria(
        self,
        org: str,
        ws: str,
        *,
        evento: str | None = None,
        repositorio: str | None = None,
        unidad: str | None = None,
        desde: str | None = None,
        hasta: str | None = None,
        cursor: str | None = None,
        limite: int = 50,
    ) -> tuple[list[RegistroAuditoria], str | None]:
        filtro: dict[str, Any] = {"alcance.org": org, "alcance.workspace": ws}
        for campo, valor in (("evento", evento), ("repositorio", repositorio), ("unidad", unidad)):
            if valor:
                filtro[campo] = valor
        rango = {k: v for k, v in (("$gte", desde), ("$lt", hasta)) if v}
        y: list[dict[str, Any]] = []
        if rango:
            y.append({"en": rango})
        if cursor:
            en, id_ = _abrir_cursor(cursor)
            y.append({"$or": [{"en": {"$lt": en}}, {"en": en, "_id": {"$lt": id_}}]})
        if y:
            filtro["$and"] = y
        docs = list(
            self.db.auditoria.find(filtro).sort([("en", DESCENDING), ("_id", DESCENDING)]).limit(limite + 1)
        )
        siguiente = None
        if len(docs) > limite:
            docs = docs[:limite]
            siguiente = _cerrar_cursor(docs[-1]["en"], docs[-1]["_id"])
        return [RegistroAuditoria.model_validate(_limpio(d)) for d in docs], siguiente

    # --- unidades (solo lectura) -----------------------------------------------------------------

    def unidades(self, org: str, ws: str, limite: int = 5000) -> list[dict[str, Any]]:
        proyeccion = {"fase": 1, "estado": 1, "gates": 1, "integracion": 1, "checkpoint_pendiente": 1}
        cursor = self.db.unidades.find({"unidad.org": org, "unidad.workspace": ws}, proyeccion)
        return list(cursor.limit(limite))

    def ordenes(self, a: AlcanceUnidad) -> list[dict[str, Any]]:
        filtro = {"_clave": _clave_unidad(a), "unidad.org": a.org, "unidad.workspace": a.workspace}
        return [_limpio(d) for d in self.db.ordenes.find(filtro).sort("secuencia", ASCENDING)]

    def reportes(self, a: AlcanceUnidad) -> dict[str, dict[str, Any]]:
        filtro = {"_clave": _clave_unidad(a), "unidad.org": a.org, "unidad.workspace": a.workspace}
        return {str(d["orden_id"]): _limpio(d) for d in self.db.reportes.find(filtro)}

    def snapshot(self, a: AlcanceUnidad, snapshot_id: str) -> Snapshot | None:
        filtro = {
            "_id": snapshot_id,
            "unidad.org": a.org,
            "unidad.workspace": a.workspace,
            "unidad.unidad": a.unidad,
        }
        doc = self.db.snapshots.find_one(filtro, SIN_TEXTO_DE_CODIGO)
        return Snapshot.model_validate(_limpio(doc)) if doc else None


def _filtro_vinculo(a: AlcanceRepositorio) -> dict[str, Any]:
    return {"alcance.org": a.org, "alcance.workspace": a.workspace, "alcance.repositorio": a.repositorio}


def _ids(filtro: dict[str, Any]) -> dict[str, Any]:
    return {"_id": filtro["_id"]} if "_id" in filtro else {}


def _cerrar_cursor(en: str, id_: str) -> str:
    return base64.urlsafe_b64encode(json.dumps([en, id_]).encode()).decode()


def _abrir_cursor(cursor: str) -> tuple[str, str]:
    try:
        en, id_ = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(en), str(id_)
    except (ValueError, TypeError) as exc:
        raise ValueError("cursor inválido") from exc
