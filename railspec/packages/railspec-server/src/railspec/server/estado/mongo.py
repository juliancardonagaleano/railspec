"""``StateStore`` y ``AlmacenMotor`` sobre Mongo.

Es el único módulo de acceso a datos del motor: toda consulta lleva org y
workspace, ya sea por ``_filtro_*`` o por una clave ``_id`` compuesta que los
contiene, y también las lecturas y los reemplazos por ``_id``: un id ajeno a su
espacio de nombres no encuentra nada (y un reemplazo con upsert falla con
``DuplicateKeyError`` en vez de pisar el documento ajeno), según la política
aprobada el 2026-09-30. Los documentos se guardan como el JSON del contrato más
campos internos con prefijo ``_`` (clave, fechas BSON para TTL y rangos).

Los datos que son de la organización entera, no de un workspace, filtran solo por
org y es a propósito: ``asignaciones`` (los roles de una persona en todos los
workspaces de la organización; el autorizador decide cuáles aplican), ``catalogo`` y la
caché de nodos (por organización), y ``consultar_telemetria`` cuando la consulta no
fija workspace (el contrato la admite para las estadísticas de la organización).
``test_aislamiento_almacenes`` lista cada método con su regla.

En desarrollo y pruebas corre igual sobre ``mongomock``; ``RAILSPEC_MONGO_URI``
lo conecta al Mongo comunitario en AKS.
"""

from __future__ import annotations

import base64
import json
import logging
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from pymongo import ASCENDING, DESCENDING, ReturnDocument
from pymongo.database import Database
from pymongo.errors import DuplicateKeyError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace, Perfil, Proveedor
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.eventos import Direccion, EventoSync
from railspec.contracts.mandato import EstadoMandato, Mandato
from railspec.contracts.orden import OrdenDeTrabajo
from railspec.contracts.reporte import ReporteOrden
from railspec.contracts.repositorio import (
    AsignacionRol,
    ModeloCatalogo,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    RegistroAuditoria,
    TelemetriaNodo,
    VinculoRepositorio,
    Workspace,
)
from railspec.contracts.snapshot import Snapshot
from railspec.contracts.tools import (
    FilaTelemetria,
    TelemetryQueryEntrada,
    TelemetryQuerySalida,
    UnitListEntrada,
)

from .interfaces import ADAPTADOR_ORDEN, EntradaPendiente, TipoEntrada

log = logging.getLogger("railspec.estado")

#: Retención por defecto de snapshots cuando el vínculo no fija otra (días).
RETENCION_SNAPSHOTS_DIAS = 30


def clave_perfil(org: str, workspace: str | None, nombre: str) -> str:
    """``_id`` de un perfil: único por (organización, workspace o ``*``, nombre)."""

    return f"{org}/{workspace or '*'}/{nombre}"


def clave_presupuesto(org: str, workspace: str | None) -> str:
    return f"{org}/{workspace or '*'}"


def clave_proveedor_contexto(org: str, workspace: str | None, rol: str, nombre: str) -> str:
    return f"{org}/{workspace or '*'}/{rol}/{nombre}"


#: Clave natural de la configuración por entidad (lo que identifica un documento aunque su ``_id`` sea
#: antiguo): índice único para que ni un escritor anterior al ``_id`` determinista pueda duplicarla.
CLAVES_NATURALES = {
    "perfiles": ("org", "workspace", "nombre"),
    "presupuestos": ("org", "workspace"),
    "proveedores_contexto": ("org", "workspace", "rol", "nombre"),
}


def _doc(modelo: Any) -> dict[str, Any]:
    return json.loads(modelo.model_dump_json())


def _limpio(doc: dict[str, Any] | None) -> dict[str, Any] | None:
    if doc is None:
        return None
    return {k: v for k, v in doc.items() if not k.startswith("_")}


def filtro_sujetos(github_id: int, equipos: frozenset[int] = frozenset()) -> list[dict[str, Any]]:
    """Alternativas de ``$or`` que casan las asignaciones de una persona y de sus equipos.

    Única definición de "a quién le toca una asignación" (R3): la usan el
    autorizador de ``/v1`` y MCP y la consola. Los equipos se casan por
    ``equipo_id`` (único en GitHub); la organización y el workspace los filtra
    quien llama.
    """

    sujetos: list[dict[str, Any]] = [{"sujeto.tipo": "usuario", "sujeto.github_id": github_id}]
    if equipos:
        sujetos.append({"sujeto.tipo": "equipo", "sujeto.equipo_id": {"$in": sorted(equipos)}})
    return sujetos


def _filtro_ws(org: str, workspace: str, prefijo: str) -> dict[str, Any]:
    return {f"{prefijo}.org": org, f"{prefijo}.workspace": workspace}


def _filtro_unidad(alcance: AlcanceUnidad, prefijo: str = "unidad") -> dict[str, Any]:
    return {**_filtro_ws(alcance.org, alcance.workspace, prefijo), f"{prefijo}.unidad": alcance.unidad}


def _filtro_repositorio(alcance: AlcanceRepositorio, prefijo: str = "alcance") -> dict[str, Any]:
    filtro = _filtro_ws(alcance.org, alcance.workspace, prefijo)
    return {**filtro, f"{prefijo}.repositorio": alcance.repositorio}


def _clave_unidad(alcance: AlcanceUnidad) -> str:
    return f"{alcance.org}/{alcance.workspace}/{alcance.unidad}"


def _clave_mandato(alcance: AlcanceWorkspace, id_: str) -> str:
    return f"{alcance.org}/{alcance.workspace}/{id_}"


class AlmacenMongo:
    """Implementa ``StateStore`` (contratos) y ``AlmacenMotor`` (motor)."""

    def __init__(self, db: Database, *, crear_indices: bool = True) -> None:
        self.db = db
        if crear_indices:
            self.crear_indices()

    # --- índices -----------------------------------------------------------------

    def crear_indices(self) -> None:
        db = self.db
        db.unidades.create_index([("unidad.org", ASCENDING), ("unidad.workspace", ASCENDING)])
        db.unidades.create_index(
            [("unidad.org", ASCENDING), ("unidad.workspace", ASCENDING), ("unidad.plan", ASCENDING)]
        )
        db.mandatos.create_index([("alcance.org", ASCENDING), ("alcance.workspace", ASCENDING)])
        db.eventos.create_index(
            [("_clave", ASCENDING), ("direccion", ASCENDING), ("secuencia", ASCENDING)], unique=True
        )
        db.ordenes.create_index([("_clave", ASCENDING), ("secuencia", ASCENDING)], unique=True)
        db.snapshots.create_index("_expira", expireAfterSeconds=0)
        db.snapshots.create_index(
            [("unidad.org", ASCENDING), ("unidad.workspace", ASCENDING), ("repositorio", ASCENDING)]
        )
        db.telemetria.create_index([("org", ASCENDING), ("workspace", ASCENDING), ("_en", ASCENDING)])
        db.auditoria.create_index([("alcance.org", ASCENDING), ("alcance.workspace", ASCENDING)])
        db.entradas.create_index([("_clave", ASCENDING), ("_recibida", ASCENDING)])
        db.catalogo.create_index([("org", ASCENDING), ("proveedor", ASCENDING)])
        db.cache_nodos.create_index("_expira", expireAfterSeconds=0)
        for coleccion, campos in CLAVES_NATURALES.items():
            try:
                db[coleccion].create_index([(c, ASCENDING) for c in campos], unique=True)
            except DuplicateKeyError:
                # Ya hay duplicados (creados por la carrera de antes del ``_id`` determinista): no se
                # tumba el arranque; el ``_id`` determinista ya impide nuevos. Hay que resolverlos a mano.
                log.warning(
                    "%s: hay documentos duplicados por %s; sin índice único hasta que se resuelvan",
                    coleccion,
                    "/".join(campos),
                )

    # --- StateStore: estado ----------------------------------------------------------

    def obtener_estado(self, alcance: AlcanceUnidad) -> EstadoUnidad | None:
        doc = self.db.unidades.find_one({"_id": _clave_unidad(alcance), **_filtro_unidad(alcance)})
        return EstadoUnidad.model_validate(_limpio(doc)) if doc else None

    def guardar_estado(self, estado: EstadoUnidad, version_esperada: int | None) -> EstadoUnidad:
        nueva = 1 if version_esperada is None else version_esperada + 1
        estado = estado.model_copy(update={"version": nueva})
        # Revalida: model_copy no corre validadores y el estado es el contrato.
        estado = EstadoUnidad.model_validate(_doc(estado))
        clave = _clave_unidad(estado.unidad)
        doc = {"_id": clave, **_doc(estado)}
        if version_esperada is None:
            try:
                self.db.unidades.insert_one(doc)
            except DuplicateKeyError:
                actual = self.obtener_estado(estado.unidad)
                raise ConflictoVersion(0, actual.version if actual else 0) from None
            return estado
        resultado = self.db.unidades.replace_one(
            {"_id": clave, "version": version_esperada, **_filtro_unidad(estado.unidad)}, doc
        )
        if resultado.matched_count != 1:
            actual = self.obtener_estado(estado.unidad)
            raise ConflictoVersion(version_esperada, actual.version if actual else 0)
        return estado

    # --- StateStore: eventos ------------------------------------------------------------

    def registrar_evento(self, evento: EventoSync) -> bool:
        doc = {"_id": str(evento.id), "_clave": _clave_unidad(evento.unidad), **_doc(evento)}
        try:
            self.db.eventos.insert_one(doc)
        except DuplicateKeyError:
            # Duplicado solo si el evento ya está en esta unidad; el mismo id en otro workspace
            # (o una colisión de secuencia) no es un reenvío y se propaga.
            if self.existe_evento(evento.unidad, evento.id):
                return False
            raise
        return True

    def existe_evento(self, alcance: AlcanceUnidad, evento_id: Any) -> bool:
        filtro = {"_id": str(evento_id), "_clave": _clave_unidad(alcance), **_filtro_unidad(alcance)}
        return self.db.eventos.find_one(filtro, {"_id": 1}) is not None

    def eventos_desde(
        self, alcance: AlcanceUnidad, direccion: Direccion, secuencia: int, limite: int = 0
    ) -> list[EventoSync]:
        cursor = self.db.eventos.find(
            {
                "_clave": _clave_unidad(alcance),
                **_filtro_unidad(alcance),
                "direccion": direccion.value,
                "secuencia": {"$gt": secuencia},
            }
        ).sort("secuencia", ASCENDING)
        if limite:
            cursor = cursor.limit(limite)
        return [EventoSync.model_validate(_limpio(d)) for d in cursor]

    def ultima_secuencia(self, alcance: AlcanceUnidad, direccion: Direccion) -> int:
        doc = self.db.eventos.find_one(
            {"_clave": _clave_unidad(alcance), **_filtro_unidad(alcance), "direccion": direccion.value},
            sort=[("secuencia", DESCENDING)],
        )
        return int(doc["secuencia"]) if doc else 0

    # --- StateStore: auditoría y telemetría ---------------------------------------------

    def registrar_auditoria(self, registro: RegistroAuditoria) -> None:
        self.db.auditoria.insert_one({"_id": str(registro.id), **_doc(registro)})

    def registrar_telemetria(self, fila: TelemetriaNodo) -> None:
        self.db.telemetria.insert_one({"_id": str(fila.id), "_en": fila.en.astimezone(UTC), **_doc(fila)})

    def consultar_telemetria(self, consulta: TelemetryQueryEntrada) -> TelemetryQuerySalida:
        filtro: dict[str, Any] = {
            "org": consulta.org,
            "_en": {"$gte": consulta.desde.astimezone(UTC), "$lt": consulta.hasta.astimezone(UTC)},
        }
        if consulta.workspace is not None:
            filtro["workspace"] = consulta.workspace
        for clave, valor in consulta.filtros.items():
            filtro[clave] = valor
        grupo: dict[str, Any] = {
            "_id": {c: f"${c}" for c in consulta.agrupar_por} or None,
            "llamadas": {"$sum": 1},
            "tokens_entrada": {"$sum": "$tokens_entrada"},
            "tokens_salida": {"$sum": "$tokens_salida"},
            "tokens_cache_lectura": {"$sum": "$tokens_cache_lectura"},
            "costo_usd": {"$sum": "$costo_usd"},
            "duracion_ms": {"$sum": "$duracion_ms"},
        }
        filas = []
        for d in self.db.telemetria.aggregate([{"$match": filtro}, {"$group": grupo}]):
            claves = d["_id"] or {}
            filas.append(
                FilaTelemetria(
                    claves={
                        c: (None if claves.get(c) is None else str(claves.get(c)))
                        for c in consulta.agrupar_por
                    },
                    llamadas=d["llamadas"],
                    tokens_entrada=d["tokens_entrada"],
                    tokens_salida=d["tokens_salida"],
                    tokens_cache_lectura=d["tokens_cache_lectura"],
                    costo_usd=round(float(d["costo_usd"]), 6),
                    duracion_ms=d["duracion_ms"],
                )
            )
        filas.sort(key=lambda f: sorted((k, v or "") for k, v in f.claves.items()))
        return TelemetryQuerySalida(filas=filas)

    # --- AlmacenMotor: unidades -----------------------------------------------------------

    def siguiente_numero_unidad(self, alcance: AlcanceWorkspace) -> int:
        doc = self.db.contadores.find_one_and_update(
            {"_id": f"unidades/{alcance.org}/{alcance.workspace}"},
            {"$inc": {"valor": 1}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )
        return int(doc["valor"])

    def reclamar_importacion(
        self, alcance: AlcanceWorkspace, repositorio: str, tipo: str, id_original: str, unidad: str
    ) -> str:
        # El _id es el origen completo: el upsert es atómico y dos importaciones a la vez dan la misma unidad.
        clave = json.dumps([alcance.org, alcance.workspace, repositorio, tipo, id_original])
        cambio = {"$setOnInsert": {"org": alcance.org, "workspace": alcance.workspace, "unidad": unidad}}
        for intento in range(2):
            try:
                doc = self.db.importaciones.find_one_and_update(
                    {"_id": clave}, cambio, upsert=True, return_document=ReturnDocument.AFTER
                )
                return str(doc["unidad"])
            except DuplicateKeyError:
                if intento:
                    raise
        raise AssertionError("inalcanzable")

    def listar_estados(self, consulta: UnitListEntrada) -> tuple[list[EstadoUnidad], str | None]:
        filtro: dict[str, Any] = _filtro_ws(consulta.alcance.org, consulta.alcance.workspace, "unidad")
        if consulta.repositorio:
            filtro["repositorios.repositorio"] = consulta.repositorio
        if consulta.fase:
            filtro["fase"] = {"$in": [f.value for f in consulta.fase]}
        if consulta.estado:
            filtro["estado"] = {"$in": [e.value for e in consulta.estado]}
        if consulta.integradas is not None:
            filtro["integracion"] = {"$ne": None} if consulta.integradas else None
        desde = _decodificar_cursor(consulta.cursor)
        if desde:
            filtro["_id"] = {"$gt": desde}
        docs = list(self.db.unidades.find(filtro).sort("_id", ASCENDING).limit(consulta.limite + 1))
        siguiente = None
        if len(docs) > consulta.limite:
            docs = docs[: consulta.limite]
            siguiente = _codificar_cursor(docs[-1]["_id"])
        return [EstadoUnidad.model_validate(_limpio(d)) for d in docs], siguiente

    # --- AlmacenMotor: mandatos (contrato 1.11) ---------------------------------------------

    def obtener_mandato(self, alcance: AlcanceWorkspace, id_: str) -> Mandato | None:
        filtro = {
            "_id": _clave_mandato(alcance, id_),
            **_filtro_ws(alcance.org, alcance.workspace, "alcance"),
        }
        doc = self.db.mandatos.find_one(filtro)
        return Mandato.model_validate(_limpio(doc)) if doc else None

    def guardar_mandato(self, mandato: Mandato, version_esperada: int | None) -> Mandato:
        """Crea (``version_esperada=None``) o reemplaza el mandato con bloqueo optimista."""

        nueva = 1 if version_esperada is None else version_esperada + 1
        mandato = Mandato.model_validate(_doc(mandato.model_copy(update={"version": nueva})))
        clave = _clave_mandato(mandato.alcance, mandato.id)
        doc = {"_id": clave, **_doc(mandato)}
        if version_esperada is None:
            try:
                self.db.mandatos.insert_one(doc)
            except DuplicateKeyError:
                actual = self.obtener_mandato(mandato.alcance, mandato.id)
                raise ConflictoVersion(0, actual.version if actual else 0) from None
            return mandato
        filtro_ws = _filtro_ws(mandato.alcance.org, mandato.alcance.workspace, "alcance")
        resultado = self.db.mandatos.replace_one(
            {"_id": clave, "version": version_esperada, **filtro_ws}, doc
        )
        if resultado.matched_count != 1:
            actual = self.obtener_mandato(mandato.alcance, mandato.id)
            raise ConflictoVersion(version_esperada, actual.version if actual else 0)
        return mandato

    def listar_mandatos(
        self, alcance: AlcanceWorkspace, estados: Iterable[EstadoMandato] = (), limite: int = 200
    ) -> list[Mandato]:
        filtro: dict[str, Any] = _filtro_ws(alcance.org, alcance.workspace, "alcance")
        if estados := [e.value for e in estados]:
            filtro["estado"] = {"$in": estados}
        docs = self.db.mandatos.find(filtro).sort("_id", ASCENDING).limit(limite)
        return [Mandato.model_validate(_limpio(d)) for d in docs]

    def estados_de_plan(self, alcance: AlcanceWorkspace, plan: str) -> list[EstadoUnidad]:
        """Las unidades del workspace cuyo ``unidad.plan`` es ``plan`` (las que ampara ese mandato)."""

        filtro = {**_filtro_ws(alcance.org, alcance.workspace, "unidad"), "unidad.plan": plan}
        docs = self.db.unidades.find(filtro).sort("_id", ASCENDING)
        return [EstadoUnidad.model_validate(_limpio(d)) for d in docs]

    # --- AlmacenMotor: órdenes, reportes, snapshots -----------------------------------------

    def guardar_orden(self, orden: OrdenDeTrabajo) -> None:
        self.db.ordenes.replace_one(
            {"_id": str(orden.id), "_clave": _clave_unidad(orden.unidad), **_filtro_unidad(orden.unidad)},
            {"_id": str(orden.id), "_clave": _clave_unidad(orden.unidad), **_doc(orden)},
            upsert=True,
        )

    def obtener_orden(self, alcance: AlcanceUnidad, orden_id: str) -> OrdenDeTrabajo | None:
        doc = self.db.ordenes.find_one({"_id": str(orden_id), **_filtro_unidad(alcance)})
        return ADAPTADOR_ORDEN.validate_python(_limpio(doc)) if doc else None

    def reporte_aceptado(self, alcance: AlcanceUnidad, orden_id: Any, secuencia: int) -> bool:
        filtro = {
            "_id": str(orden_id),
            "_clave": _clave_unidad(alcance),
            **_filtro_unidad(alcance),
            "secuencia": secuencia,
        }
        return self.db.reportes.find_one(filtro, {"_id": 1}) is not None

    def guardar_reporte(self, reporte: ReporteOrden) -> None:
        doc = _doc(reporte.model_copy(update={"snapshot": None}))
        doc["snapshot_id"] = str(reporte.snapshot.id) if reporte.snapshot else None
        self.db.reportes.replace_one(
            {
                "_id": str(reporte.orden_id),
                "_clave": _clave_unidad(reporte.unidad),
                **_filtro_unidad(reporte.unidad),
            },
            {"_id": str(reporte.orden_id), "_clave": _clave_unidad(reporte.unidad), **doc},
            upsert=True,
        )

    def guardar_snapshot(self, snapshot: Snapshot, retencion_dias: int = RETENCION_SNAPSHOTS_DIAS) -> None:
        creado = snapshot.creado_en.astimezone(UTC)
        self.db.snapshots.replace_one(
            {"_id": str(snapshot.id), **_filtro_unidad(snapshot.unidad)},
            {
                "_id": str(snapshot.id),
                "_clave": _clave_unidad(snapshot.unidad),
                "_expira": creado + timedelta(days=retencion_dias),
                **_doc(snapshot),
            },
            upsert=True,
        )

    def obtener_snapshot(self, alcance: AlcanceUnidad, snapshot_id: str) -> Snapshot | None:
        doc = self.db.snapshots.find_one({"_id": str(snapshot_id), **_filtro_unidad(alcance)})
        return Snapshot.model_validate(_limpio(doc)) if doc else None

    def borrar_snapshots_repositorio(self, alcance: AlcanceRepositorio) -> int:
        """Borra los snapshots del repositorio en todas sus unidades; devuelve cuántos.

        Idempotente: sin snapshots devuelve 0. Los de ``interno`` y ``abierto`` llevan diff y
        fragmentos, así que desvincular no puede esperar a la caducidad.
        """

        filtro = {**_filtro_ws(alcance.org, alcance.workspace, "unidad"), "repositorio": alcance.repositorio}
        return self.db.snapshots.delete_many(filtro).deleted_count

    # --- AlmacenMotor: entradas y turno ----------------------------------------------------

    def registrar_entrada(self, entrada: EntradaPendiente) -> bool:
        try:
            self.db.entradas.insert_one(
                {
                    "_id": f"{_clave_unidad(entrada.alcance)}/{entrada.request_id}",
                    "_clave": _clave_unidad(entrada.alcance),
                    "_recibida": entrada.recibida_en.astimezone(UTC),
                    **_filtro_unidad_doc(entrada.alcance),
                    "request_id": entrada.request_id,
                    "tipo": entrada.tipo.value,
                    "carga": entrada.carga,
                }
            )
        except DuplicateKeyError:
            return False
        return True

    def entradas_pendientes(self, alcance: AlcanceUnidad) -> list[EntradaPendiente]:
        cursor = self.db.entradas.find({"_clave": _clave_unidad(alcance), **_filtro_unidad(alcance)}).sort(
            "_recibida", ASCENDING
        )
        return [
            EntradaPendiente(
                alcance=alcance,
                request_id=d["request_id"],
                tipo=TipoEntrada(d["tipo"]),
                carga=d["carga"],
                recibida_en=_aware(d["_recibida"]),
            )
            for d in cursor
        ]

    def consumir_entrada(self, alcance: AlcanceUnidad, request_id: str) -> None:
        self.db.entradas.delete_one(
            {"_id": f"{_clave_unidad(alcance)}/{request_id}", **_filtro_unidad(alcance)}
        )

    def tomar_turno(self, alcance: AlcanceUnidad, dueno: str, ahora: datetime, ttl_s: int) -> bool:
        clave = f"turno/{_clave_unidad(alcance)}"
        ahora = ahora.astimezone(UTC)
        vence = ahora + timedelta(seconds=ttl_s)
        try:
            doc = self.db.turnos.find_one_and_update(
                {"_id": clave, "$or": [{"dueno": dueno}, {"vence": {"$lte": ahora}}]},
                {"$set": {"dueno": dueno, "vence": vence}},
                upsert=True,
                return_document=ReturnDocument.AFTER,
            )
        except DuplicateKeyError:
            return False
        return doc is not None and doc.get("dueno") == dueno

    def soltar_turno(self, alcance: AlcanceUnidad, dueno: str) -> None:
        self.db.turnos.delete_one({"_id": f"turno/{_clave_unidad(alcance)}", "dueno": dueno})

    # --- AlmacenMotor: configuración ----------------------------------------------------------

    def perfil(self, alcance: AlcanceWorkspace, nombre: Perfil) -> PerfilConfig | None:
        for workspace in (alcance.workspace, None):
            doc = self.db.perfiles.find_one(
                {"org": alcance.org, "workspace": workspace, "nombre": nombre.value}
            )
            if doc:
                return PerfilConfig.model_validate(_limpio(doc))
        return None

    def presupuesto(self, alcance: AlcanceWorkspace) -> PresupuestoConfig | None:
        for workspace in (alcance.workspace, None):
            doc = self.db.presupuestos.find_one({"org": alcance.org, "workspace": workspace})
            if doc:
                return PresupuestoConfig.model_validate(_limpio(doc))
        return None

    def vinculos(self, org: str, workspace: str) -> list[VinculoRepositorio]:
        cursor = self.db.vinculos.find(_filtro_ws(org, workspace, "alcance")).sort("alcance.repositorio", 1)
        return [VinculoRepositorio.model_validate(_limpio(d)) for d in cursor]

    def vinculo(self, alcance: AlcanceRepositorio) -> VinculoRepositorio | None:
        doc = self.db.vinculos.find_one(_filtro_repositorio(alcance))
        return VinculoRepositorio.model_validate(_limpio(doc)) if doc else None

    def asignaciones(
        self, org: str, github_id: int, equipos: frozenset[int] = frozenset()
    ) -> list[AsignacionRol]:
        """Asignaciones de la organización a la persona y a los ``equipos`` (ids) que se le conocen."""

        cursor = self.db.roles.find({"org": org, "$or": filtro_sujetos(github_id, equipos)})
        return [AsignacionRol.model_validate(_limpio(d)) for d in cursor]

    def workspace(self, alcance: AlcanceWorkspace) -> Workspace | None:
        doc = self.db.workspaces.find_one(_filtro_ws(alcance.org, alcance.workspace, "alcance"))
        return Workspace.model_validate(_limpio(doc)) if doc else None

    def proveedores_contexto(self, alcance: AlcanceWorkspace) -> list[ProveedorContexto]:
        """Los de la organización con los del workspace encima (mismo rol y nombre: gana el workspace)."""

        elegidos: dict[tuple[str, str], ProveedorContexto] = {}
        for workspace in (None, alcance.workspace):
            cursor = self.db.proveedores_contexto.find({"org": alcance.org, "workspace": workspace})
            for d in cursor.sort("nombre", 1):
                p = ProveedorContexto.model_validate(_limpio(d))
                elegidos[(p.rol.value, p.nombre)] = p
        return [elegidos[k] for k in sorted(elegidos)]

    # --- Caché de nodos de modelo (por organización, con caducidad) ----------------------------

    def nodo_en_cache(self, org: str, clave: str, ahora: datetime) -> dict[str, Any] | None:
        doc = self.db.cache_nodos.find_one({"_id": f"{org}/{clave}", "org": org})
        # El índice TTL de Mongo borra con retraso: la caducidad se comprueba también al leer.
        if doc is None or _aware(doc["_expira"]) <= ahora:
            return None
        return doc["respuesta"]

    def guardar_nodo_en_cache(
        self, org: str, clave: str, respuesta: dict[str, Any], expira: datetime
    ) -> None:
        self.db.cache_nodos.replace_one(
            {"_id": f"{org}/{clave}"},
            {"_id": f"{org}/{clave}", "org": org, "_expira": expira, "respuesta": respuesta},
            upsert=True,
        )

    # --- Catálogo de modelos (leído por API del proveedor, por organización) ------------------

    def catalogo(self, org: str) -> list[ModeloCatalogo]:
        cursor = self.db.catalogo.find({"org": org}).sort(
            [("proveedor", 1), ("modelo", 1), ("despliegue", 1)]
        )
        return [ModeloCatalogo.model_validate(_limpio(d)) for d in cursor]

    def guardar_catalogo(self, org: str, proveedor: Proveedor, modelos: Iterable[ModeloCatalogo]) -> None:
        """Reemplaza el catálogo de un proveedor en la organización: lo que ya no lista, sale."""

        docs = [_doc(m) for m in modelos]
        if any(d["org"] != org or d["proveedor"] != proveedor.value for d in docs):
            raise ValueError("catálogo de otra organización o proveedor")
        self.db.catalogo.delete_many({"org": org, "proveedor": proveedor.value})
        if docs:
            self.db.catalogo.insert_many(docs)

    # --- Escritura de configuración (consola y pruebas) ----------------------------------------

    def guardar_configuracion(self, entidades: Iterable[Any]) -> None:
        """Upsert de entidades de configuración; la consola usará su propio flujo con versión."""

        for e in entidades:
            if isinstance(e, PerfilConfig):
                self._guardar_por_clave_natural(
                    "perfiles",
                    {"org": e.org, "workspace": e.workspace, "nombre": e.nombre.value},
                    clave_perfil(e.org, e.workspace, e.nombre.value),
                    _doc(e),
                )
            elif isinstance(e, PresupuestoConfig):
                self._guardar_por_clave_natural(
                    "presupuestos",
                    {"org": e.org, "workspace": e.workspace},
                    clave_presupuesto(e.org, e.workspace),
                    _doc(e),
                )
            elif isinstance(e, VinculoRepositorio):
                a = e.alcance
                self.db.vinculos.replace_one(
                    {"_id": f"{a.org}/{a.workspace}/{a.repositorio}", **_filtro_repositorio(a)},
                    {"_id": f"{a.org}/{a.workspace}/{a.repositorio}", **_doc(e)},
                    upsert=True,
                )
            elif isinstance(e, Workspace):
                a = e.alcance
                self.db.workspaces.replace_one(
                    {"_id": f"{a.org}/{a.workspace}", **_filtro_ws(a.org, a.workspace, "alcance")},
                    {"_id": f"{a.org}/{a.workspace}", **_doc(e)},
                    upsert=True,
                )
            elif isinstance(e, ProveedorContexto):
                self._guardar_por_clave_natural(
                    "proveedores_contexto",
                    {"org": e.org, "workspace": e.workspace, "rol": e.rol.value, "nombre": e.nombre},
                    clave_proveedor_contexto(e.org, e.workspace, e.rol.value, e.nombre),
                    _doc(e),
                )
            elif isinstance(e, AsignacionRol):
                self.db.roles.replace_one(
                    {"_id": str(e.id), "org": e.org, "workspace": e.workspace},
                    {"_id": str(e.id), **_doc(e)},
                    upsert=True,
                )
            else:
                raise TypeError(f"no es configuración: {type(e).__name__}")

    def _guardar_por_clave_natural(
        self, coleccion: str, filtro: dict[str, Any], clave: str, doc: dict[str, Any]
    ) -> None:
        """Upsert por clave natural con ``_id`` determinista.

        Si ya hay un documento con esa clave natural se reescribe conservando su ``_id`` (puede ser
        un ObjectId de antes del ``_id`` determinista); si no, se crea con ``clave``, el mismo
        ``_id`` con que crea la consola, así que no existen dos documentos para la misma entidad.
        """

        col = self.db[coleccion]
        existente = col.find_one(filtro, {"_id": 1})
        if existente is not None:
            col.replace_one({"_id": existente["_id"], **filtro}, doc)
        else:
            col.replace_one({"_id": clave, **filtro}, {"_id": clave, **doc}, upsert=True)


def _filtro_unidad_doc(alcance: AlcanceUnidad) -> dict[str, Any]:
    return {"unidad": {"org": alcance.org, "workspace": alcance.workspace, "unidad": alcance.unidad}}


def _aware(valor: datetime) -> datetime:
    return valor if valor.tzinfo else valor.replace(tzinfo=UTC)


def _codificar_cursor(clave: str) -> str:
    return base64.urlsafe_b64encode(clave.encode()).decode()


def _decodificar_cursor(cursor: str | None) -> str | None:
    if not cursor:
        return None
    try:
        return base64.urlsafe_b64decode(cursor.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None


def almacen_en_memoria() -> AlmacenMongo:
    """Mongo simulado con ``mongomock``: desarrollo local y pruebas."""

    import mongomock

    return AlmacenMongo(mongomock.MongoClient(tz_aware=True)["railspec"])


def almacen_desde_uri(uri: str, db: str) -> AlmacenMongo:
    from pymongo import MongoClient

    return AlmacenMongo(MongoClient(uri, tz_aware=True)[db])


def almacen_desde_postgres(url: str, esquema: str = "railspec") -> AlmacenMongo:
    """Los mismos almacenes sobre Postgres (Supabase, Neon, Azure Database for PostgreSQL...).

    ``AlmacenMongo`` solo usa la API de colecciones de pymongo; ``BaseDocumentosPg`` la ofrece sobre una
    tabla JSONB (ver ``estado/postgres.py``), así que no hay una segunda implementación de las reglas.
    """

    from .postgres import BaseDocumentosPg

    return AlmacenMongo(BaseDocumentosPg(url, esquema=esquema))


__all__ = ["AlmacenMongo", "almacen_desde_postgres", "almacen_desde_uri", "almacen_en_memoria"]
