"""Almacén de documentos sobre Postgres con la API de colecciones de pymongo que usan los almacenes.

``AlmacenMongo``, ``CheckpointsMongo``, ``AlmacenConsola``, ``AlmacenChat`` y ``RevocadosMongo`` hablan con
``db.<colección>.find / find_one / insert_one / replace_one / update_one / find_one_and_update /
delete_one / delete_many / count_documents / aggregate / create_index``. ``BaseDocumentosPg`` ofrece esa
superficie (y solo esa) sobre una tabla ``railspec_docs(n, coleccion, id, doc json, expira)``, así que
los almacenes y su aislamiento por organización y workspace no cambian: solo cambia la base.

Cómo se traduce Mongo:

* Cada documento es una fila; ``_id`` va en la columna ``id`` (texto) y se devuelve dentro del documento.
  El cuerpo es ``json`` y no ``jsonb`` a propósito: ``jsonb`` reordena las claves de los diccionarios y el
  código depende del orden de inserción (``EstadoUnidad.gates``, por ejemplo). Las consultas lo convierten.
  Sin orden explícito, ``find`` devuelve por orden de inserción (columna ``n``), como Mongo.
* Los ``datetime`` se guardan como ``{"$date": "<ISO UTC>"}`` y vuelven como ``datetime`` con huso UTC.
* La consulta se resuelve en dos pasos: Postgres acota por colección, ``_id`` y las igualdades de campos
  escalares (``jsonb_path_exists``, que recorre arreglos como Mongo), y Python aplica el filtro completo
  (``$in``, ``$or``, ``$and``, ``$gt``, ``$gte``, ``$lt``, ``$lte``, ``$ne``, ``$exists``, ``null`` igual
  a ausente), el orden, el límite y la proyección. Basta para un MVP; con mucho volumen, el paso
  siguiente es traducir más filtros a SQL.
* Las escrituras que leen y luego escriben (``replace_one``, ``update_one``, ``find_one_and_update``,
  ``delete_one``) bloquean las filas candidatas con ``FOR UPDATE`` dentro de una transacción.
* Un índice único (``create_index(..., unique=True)``) es un índice único parcial de Postgres sobre
  expresiones; los demás índices de Mongo no se crean (las consultas no los usarían). La violación de
  una clave única se vuelve ``pymongo.errors.DuplicateKeyError``, que es lo que los almacenes esperan.
* El TTL (``_expira`` en la raíz del documento) se copia a la columna ``expira`` y se barre sin hilos: cada
  escritura purga lo vencido como mucho una vez por minuto. Igual que con Mongo, hay un retraso entre
  vencer y desaparecer, y quien necesita exactitud comprueba la fecha al leer.

No cubre: transacciones de varios documentos, ``$lookup``, índices de texto, ni más agregaciones que
``$match`` y ``$group`` con ``$sum``. Un operador no soportado lanza ``NotImplementedError``: nunca se
ignora en silencio.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import re
import time
import uuid
from collections.abc import Iterable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from pymongo import ASCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError

log = logging.getLogger("railspec.estado.postgres")

_IDENTIFICADOR = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,62}$")
_FECHA = "$date"
#: Cada cuánto, como mucho, una escritura purga los documentos vencidos.
PURGA_CADA_S = 60.0
#: Veces que se repite una escritura que chocó con otra concurrente al insertar (carrera de ``upsert``).
_INTENTOS = 2
_AUSENTE = object()


# --- codificación de documentos -------------------------------------------------------------------


def _utc(valor: datetime) -> datetime:
    return valor.astimezone(UTC) if valor.tzinfo else valor.replace(tzinfo=UTC)


def _codificar(valor: Any) -> Any:
    """Documento de Python a JSON de Postgres: fechas etiquetadas y sin el carácter NUL, que JSONB rechaza."""

    if isinstance(valor, datetime):
        return {_FECHA: _utc(valor).isoformat(timespec="microseconds")}
    if isinstance(valor, Mapping):
        return {str(k): _codificar(v) for k, v in valor.items()}
    if isinstance(valor, (list, tuple, set, frozenset)):
        return [_codificar(v) for v in valor]
    if isinstance(valor, str):
        return valor.replace("\x00", "�")
    return valor


def _decodificar(valor: Any) -> Any:
    if isinstance(valor, dict):
        if len(valor) == 1 and _FECHA in valor and isinstance(valor[_FECHA], str):
            return datetime.fromisoformat(valor[_FECHA])
        return {k: _decodificar(v) for k, v in valor.items()}
    if isinstance(valor, list):
        return [_decodificar(v) for v in valor]
    return valor


# --- coincidencia de filtros (semántica de Mongo) ------------------------------------------------


def _candidatos(doc: Any, partes: Sequence[str]) -> Iterator[Any]:
    """Valores del documento en una ruta con puntos; recorre arreglos de subdocumentos como Mongo.

    Rinde ``_AUSENTE`` cuando la ruta no existe.
    """

    if not partes:
        yield doc
        return
    if isinstance(doc, list):
        encontrado = False
        for elemento in doc:
            if isinstance(elemento, (dict, list)):
                for v in _candidatos(elemento, partes):
                    encontrado = True
                    yield v
        if not encontrado:
            yield _AUSENTE
        return
    if isinstance(doc, dict) and partes[0] in doc:
        yield from _candidatos(doc[partes[0]], partes[1:])
        return
    yield _AUSENTE


def _valores(doc: dict[str, Any], ruta: str) -> list[Any]:
    return list(_candidatos(doc, ruta.split(".")))


def _iguales(valor: Any, buscado: Any) -> bool:
    if isinstance(valor, bool) != isinstance(buscado, bool):
        return False
    if isinstance(valor, datetime) and isinstance(buscado, datetime):
        return _utc(valor) == _utc(buscado)
    return bool(valor == buscado)


def _casa_igualdad(valores: list[Any], buscado: Any) -> bool:
    if buscado is None:
        return any(v is _AUSENTE or v is None for v in valores)
    for v in valores:
        if v is _AUSENTE:
            continue
        if _iguales(v, buscado):
            return True
        if isinstance(v, list) and any(_iguales(e, buscado) for e in v):
            return True
    return False


def _comparable(a: Any, b: Any) -> bool:
    if isinstance(a, bool) or isinstance(b, bool):
        return False
    if isinstance(a, (int, float)) and isinstance(b, (int, float)):
        return True
    return type(a) is type(b) and isinstance(a, (str, datetime))


def _comparar(valor: Any, operador: str, limite: Any) -> bool:
    if isinstance(limite, datetime):
        limite = _utc(limite)
    candidatos = []
    for v in _planos(valor):
        candidatos.append(_utc(v) if isinstance(v, datetime) else v)
    for v in candidatos:
        if not _comparable(v, limite):
            continue
        if (
            (operador == "$gt" and v > limite)
            or (operador == "$gte" and v >= limite)
            or (operador == "$lt" and v < limite)
            or (operador == "$lte" and v <= limite)
        ):
            return True
    return False


def _planos(valor: Any) -> list[Any]:
    return list(valor) if isinstance(valor, list) else [valor]


def _casa_operadores(valores: list[Any], operadores: dict[str, Any]) -> bool:
    for op, arg in operadores.items():
        if op == "$in":
            if not any(_casa_igualdad(valores, a) for a in arg):
                return False
        elif op == "$ne":
            if _casa_igualdad(valores, arg):
                return False
        elif op == "$exists":
            existe = any(v is not _AUSENTE for v in valores)
            if existe != bool(arg):
                return False
        elif op in ("$gt", "$gte", "$lt", "$lte"):
            if not any(v is not _AUSENTE and _comparar(v, op, arg) for v in valores):
                return False
        else:
            raise NotImplementedError(f"operador de consulta sin soporte en Postgres: {op}")
    return True


def coincide(doc: dict[str, Any], filtro: Mapping[str, Any]) -> bool:
    """¿Cumple ``doc`` el filtro? Mismo significado que en Mongo para los operadores soportados."""

    for clave, condicion in filtro.items():
        if clave == "$or":
            if not any(coincide(doc, f) for f in condicion):
                return False
        elif clave == "$and":
            if not all(coincide(doc, f) for f in condicion):
                return False
        elif clave.startswith("$"):
            raise NotImplementedError(f"operador de consulta sin soporte en Postgres: {clave}")
        else:
            valores = _valores(doc, clave)
            if isinstance(condicion, dict) and condicion and all(k.startswith("$") for k in condicion):
                if not _casa_operadores(valores, condicion):
                    return False
            elif not _casa_igualdad(valores, condicion):
                return False
    return True


# --- orden, proyección y actualizaciones -----------------------------------------------------------


def _clave_orden(valor: Any) -> tuple[int, Any]:
    if valor is _AUSENTE or valor is None:
        return (0, 0)
    if isinstance(valor, bool):
        return (7, valor)
    if isinstance(valor, (int, float)):
        return (1, valor)
    if isinstance(valor, str):
        return (2, valor)
    if isinstance(valor, datetime):
        return (8, _utc(valor))
    return (3, json.dumps(_codificar(valor), sort_keys=True))


def _ordenar(docs: list[dict[str, Any]], orden: list[tuple[str, int]]) -> list[dict[str, Any]]:
    for campo, sentido in reversed(orden):

        def clave(d: dict[str, Any], campo: str = campo) -> tuple[int, Any]:
            v = _valores(d, campo)
            return _clave_orden(v[0] if v else _AUSENTE)

        docs.sort(key=clave, reverse=sentido < 0)
    return docs


def _normalizar_orden(clave: Any, sentido: int = ASCENDING) -> list[tuple[str, int]]:
    if isinstance(clave, str):
        return [(clave, sentido)]
    return [(c, s) for c, s in clave]


def _proyectar(doc: dict[str, Any], proyeccion: Mapping[str, Any] | None) -> dict[str, Any]:
    if not proyeccion:
        return doc
    incluidos = {k for k, v in proyeccion.items() if v and k != "_id"}
    if incluidos:
        salida = {k: v for k, v in doc.items() if k in incluidos}
        if proyeccion.get("_id", 1):
            salida["_id"] = doc["_id"]
        return salida
    excluidos = {k for k, v in proyeccion.items() if not v}
    return {k: v for k, v in doc.items() if k not in excluidos}


def _fijar(doc: dict[str, Any], ruta: str, valor: Any) -> None:
    *padres, hoja = ruta.split(".")
    actual = doc
    for p in padres:
        siguiente = actual.get(p)
        if not isinstance(siguiente, dict):
            siguiente = actual[p] = {}
        actual = siguiente
    actual[hoja] = valor


def _leer(doc: dict[str, Any], ruta: str) -> Any:
    actual: Any = doc
    for p in ruta.split("."):
        if not isinstance(actual, dict) or p not in actual:
            return None
        actual = actual[p]
    return actual


def _base_de_insercion(filtro: Mapping[str, Any]) -> dict[str, Any]:
    """Documento nuevo de un ``upsert``: las igualdades simples del filtro, como hace Mongo."""

    base: dict[str, Any] = {}
    for clave, valor in filtro.items():
        if clave.startswith("$") or isinstance(valor, dict) and any(k.startswith("$") for k in valor):
            continue
        _fijar(base, clave, valor)
    return base


def _aplicar(doc: dict[str, Any], cambios: Mapping[str, Any], insertando: bool) -> dict[str, Any]:
    nuevo = copy.deepcopy(doc)
    for op, campos in cambios.items():
        if op == "$set":
            for ruta, valor in campos.items():
                _fijar(nuevo, ruta, valor)
        elif op == "$inc":
            for ruta, delta in campos.items():
                _fijar(nuevo, ruta, (_leer(nuevo, ruta) or 0) + delta)
        elif op == "$setOnInsert":
            if insertando:
                for ruta, valor in campos.items():
                    _fijar(nuevo, ruta, valor)
        else:
            raise NotImplementedError(f"operador de actualización sin soporte en Postgres: {op}")
    return nuevo


# --- prefiltro SQL --------------------------------------------------------------------------------


def _ruta_jsonpath(ruta: str) -> str:
    return "$" + "".join("." + json.dumps(p) for p in ruta.split("."))


def _escalar(valor: Any) -> bool:
    return isinstance(valor, (str, int, float, bool)) and not (isinstance(valor, float) and valor != valor)


def _prefiltro(filtro: Mapping[str, Any]) -> tuple[str | None, list[tuple[str, Any]]]:
    """Condiciones que Postgres puede aplicar sin cambiar el resultado: ``_id`` y las igualdades escalares."""

    id_: str | None = None
    igualdades: list[tuple[str, Any]] = []
    for clave, valor in filtro.items():
        if clave.startswith("$"):
            continue
        if clave == "_id":
            if isinstance(valor, str):
                id_ = valor
            continue
        if _escalar(valor):
            igualdades.append((clave, valor))
    return id_, igualdades


def _id_nuevo(filtro: Mapping[str, Any]) -> str:
    """``_id`` de un documento creado por ``upsert``: el del filtro si es una igualdad; si no, aleatorio."""

    id_ = filtro.get("_id")
    return id_ if isinstance(id_, str) else uuid.uuid4().hex


# --- resultados y cursor ------------------------------------------------------------------------------


class _Resultado:
    def __init__(self, matched: int = 0, modified: int = 0, upserted_id: str | None = None, deleted: int = 0):
        self.matched_count = matched
        self.modified_count = modified
        self.upserted_id = upserted_id
        self.deleted_count = deleted
        self.acknowledged = True


class _Insertado:
    def __init__(self, ids: list[str]) -> None:
        self.inserted_ids = ids

    @property
    def inserted_id(self) -> str:
        return self.inserted_ids[0]


class CursorPg:
    """Resultado perezoso de ``find``: acepta ``sort`` y ``limit`` encadenados y se consume una vez."""

    def __init__(
        self, coleccion: ColeccionPg, filtro: Mapping[str, Any], proyeccion: Mapping[str, Any] | None
    ):
        self._coleccion = coleccion
        self._filtro = filtro
        self._proyeccion = proyeccion
        self._orden: list[tuple[str, int]] = []
        self._limite = 0

    def sort(self, clave: Any, sentido: int = ASCENDING) -> CursorPg:
        self._orden = _normalizar_orden(clave, sentido)
        return self

    def limit(self, n: int) -> CursorPg:
        self._limite = int(n)
        return self

    def _documentos(self) -> list[dict[str, Any]]:
        docs = self._coleccion._buscar(self._filtro)
        if self._orden:
            _ordenar(docs, self._orden)
        if self._limite > 0:
            docs = docs[: self._limite]
        return [_proyectar(d, self._proyeccion) for d in docs]

    def __iter__(self) -> Iterator[dict[str, Any]]:
        return iter(self._documentos())


# --- colección y base -------------------------------------------------------------------------------------


class ColeccionPg:
    def __init__(self, base: BaseDocumentosPg, nombre: str) -> None:
        self._base = base
        self.name = nombre

    # lectura
    def _buscar(
        self, filtro: Mapping[str, Any], *, bloquear: bool = False, conn: Any = None
    ) -> list[dict[str, Any]]:
        sql, params = self._base._consulta(self.name, filtro, bloquear)
        if conn is not None:
            filas = conn.execute(sql, params).fetchall()
        else:
            with self._base._conexion() as c:
                filas = c.execute(sql, params).fetchall()
        docs = []
        for id_, doc in filas:
            d = _decodificar(doc)
            d["_id"] = id_
            if coincide(d, filtro):
                docs.append(d)
        return docs

    def find(
        self, filtro: Mapping[str, Any] | None = None, proyeccion: Mapping[str, Any] | None = None
    ) -> CursorPg:
        return CursorPg(self, filtro or {}, proyeccion)

    def find_one(
        self,
        filtro: Mapping[str, Any] | None = None,
        proyeccion: Mapping[str, Any] | None = None,
        sort: Any = None,
    ) -> dict[str, Any] | None:
        cursor = self.find(filtro, proyeccion)
        if sort:
            cursor.sort(sort)
        cursor.limit(1)
        for d in cursor:
            return d
        return None

    def count_documents(self, filtro: Mapping[str, Any]) -> int:
        return len(self._buscar(filtro))

    def aggregate(self, tuberia: Sequence[Mapping[str, Any]]) -> Iterator[dict[str, Any]]:
        etapas = list(tuberia)
        if len(etapas) != 2 or set(etapas[0]) != {"$match"} or set(etapas[1]) != {"$group"}:
            raise NotImplementedError("solo se soporta la tubería [$match, $group] en Postgres")
        docs = self._buscar(etapas[0]["$match"])
        grupo = etapas[1]["$group"]
        clave_grupo = grupo["_id"]
        acumuladores = {k: v for k, v in grupo.items() if k != "_id"}
        grupos: dict[str, dict[str, Any]] = {}
        for d in docs:
            if clave_grupo is None:
                identidad: Any = None
            else:
                identidad = {
                    c: (None if (v := _valores(d, ref[1:])[0]) is _AUSENTE else v)
                    for c, ref in clave_grupo.items()
                }
            llave = json.dumps(_codificar(identidad), sort_keys=True)
            salida = grupos.setdefault(llave, {"_id": identidad, **{k: 0 for k in acumuladores}})
            for nombre, acumulador in acumuladores.items():
                ((op, arg),) = acumulador.items()
                if op != "$sum":
                    raise NotImplementedError(f"acumulador sin soporte en Postgres: {op}")
                if isinstance(arg, str) and arg.startswith("$"):
                    v = _valores(d, arg[1:])[0]
                    salida[nombre] += v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
                else:
                    salida[nombre] += arg
        return iter(grupos.values())

    # escritura
    def insert_one(self, doc: Mapping[str, Any]) -> _Insertado:
        return self.insert_many([doc])

    def insert_many(self, docs: Iterable[Mapping[str, Any]]) -> _Insertado:
        docs = list(docs)
        ids = []
        with self._base._transaccion() as conn:
            for doc in docs:
                id_ = str(doc["_id"]) if "_id" in doc else uuid.uuid4().hex
                self._base._insertar(conn, self.name, id_, doc)
                ids.append(id_)
        self._base._purgar_si_toca()
        return _Insertado(ids)

    def replace_one(
        self, filtro: Mapping[str, Any], reemplazo: Mapping[str, Any], upsert: bool = False
    ) -> _Resultado:
        def operacion(conn: Any) -> _Resultado:
            docs = self._buscar(filtro, bloquear=True, conn=conn)
            if docs:
                actual = docs[0]
                if "_id" in reemplazo and str(reemplazo["_id"]) != actual["_id"]:
                    raise ValueError("replace_one no puede cambiar el _id")
                self._base._escribir(conn, self.name, actual["_id"], reemplazo)
                return _Resultado(matched=1, modified=1)
            if not upsert:
                return _Resultado()
            id_ = str(reemplazo["_id"]) if "_id" in reemplazo else _id_nuevo(filtro)
            self._base._insertar(conn, self.name, id_, reemplazo)
            return _Resultado(upserted_id=id_)

        resultado = self._base._con_reintento(operacion)
        self._base._purgar_si_toca()
        return resultado

    def update_one(
        self, filtro: Mapping[str, Any], cambios: Mapping[str, Any], upsert: bool = False
    ) -> _Resultado:
        return self._actualizar(filtro, cambios, upsert)[2]

    def find_one_and_update(
        self,
        filtro: Mapping[str, Any],
        cambios: Mapping[str, Any],
        upsert: bool = False,
        return_document: bool = ReturnDocument.BEFORE,
        **_: Any,
    ) -> dict[str, Any] | None:
        antes, despues, _resultado = self._actualizar(filtro, cambios, upsert)
        return antes if return_document == ReturnDocument.BEFORE else despues

    def _actualizar(
        self, filtro: Mapping[str, Any], cambios: Mapping[str, Any], upsert: bool
    ) -> tuple[dict[str, Any] | None, dict[str, Any] | None, _Resultado]:
        """Documento antes, documento después (dentro de la misma transacción) y resultado."""

        def operacion(conn: Any) -> tuple[dict[str, Any] | None, dict[str, Any] | None, _Resultado]:
            docs = self._buscar(filtro, bloquear=True, conn=conn)
            if docs:
                actual = docs[0]
                nuevo = _aplicar({k: v for k, v in actual.items() if k != "_id"}, cambios, insertando=False)
                self._base._escribir(conn, self.name, actual["_id"], nuevo)
                return actual, {**nuevo, "_id": actual["_id"]}, _Resultado(matched=1, modified=1)
            if not upsert:
                return None, None, _Resultado()
            base = _base_de_insercion({k: v for k, v in filtro.items() if k != "_id"})
            nuevo = _aplicar(base, cambios, insertando=True)
            id_ = _id_nuevo(filtro)
            self._base._insertar(conn, self.name, id_, nuevo)
            return None, {**nuevo, "_id": id_}, _Resultado(upserted_id=id_)

        salida = self._base._con_reintento(operacion)
        self._base._purgar_si_toca()
        return salida

    def delete_one(self, filtro: Mapping[str, Any]) -> _Resultado:
        with self._base._transaccion() as conn:
            docs = self._buscar(filtro, bloquear=True, conn=conn)
            if not docs:
                return _Resultado()
            self._base._borrar(conn, self.name, [docs[0]["_id"]])
        return _Resultado(deleted=1)

    def delete_many(self, filtro: Mapping[str, Any]) -> _Resultado:
        with self._base._transaccion() as conn:
            docs = self._buscar(filtro, bloquear=True, conn=conn)
            self._base._borrar(conn, self.name, [d["_id"] for d in docs])
        return _Resultado(deleted=len(docs))

    def create_index(
        self, claves: Any, unique: bool = False, expireAfterSeconds: int | None = None, **_: Any
    ) -> str:
        """Registra el índice; en Postgres solo se crean los únicos (ninguna consulta usaría los demás).

        El TTL (``expireAfterSeconds``) no necesita índice propio: ``_expira`` va a la columna ``expira``.
        """

        orden = _normalizar_orden(claves)
        nombre = "_".join(f"{c}_{s}" for c, s in orden)
        info: dict[str, Any] = {"key": orden}
        if unique:
            info["unique"] = True
            self._base._indice_unico(self.name, [c for c, _ in orden])
        if expireAfterSeconds is not None:
            info["expireAfterSeconds"] = expireAfterSeconds
        self._base._indices.setdefault(self.name, {})[nombre] = info
        return nombre

    def index_information(self) -> dict[str, dict[str, Any]]:
        """Los índices declarados por este proceso (como ``pymongo``, con ``_id_`` siempre)."""

        return {"_id_": {"key": [("_id", ASCENDING)]}, **self._base._indices.get(self.name, {})}


class BaseDocumentosPg:
    """Equivalente de ``pymongo.database.Database`` sobre Postgres (ver el docstring del módulo)."""

    def __init__(
        self,
        url: str,
        *,
        esquema: str = "railspec",
        tabla: str = "railspec_docs",
        tamano_pool: int = 5,
        pool: Any | None = None,
    ) -> None:
        if not _IDENTIFICADOR.match(esquema) or not _IDENTIFICADOR.match(tabla):
            raise ValueError("esquema y tabla deben ser identificadores simples")
        from psycopg_pool import ConnectionPool

        self._esquema = esquema
        self._tabla = tabla
        # ``prepare_threshold=None``: el pooler de Supabase en modo transacción no admite sentencias
        # preparadas. Cada operación abre su propia transacción, así que ambos modos del pooler valen.
        self._pool = pool or ConnectionPool(
            url,
            min_size=1,
            max_size=tamano_pool,
            kwargs={"prepare_threshold": None, "autocommit": True},
            check=ConnectionPool.check_connection,
            open=True,
        )
        self._pool.wait(timeout=30)
        self._ultima_purga = 0.0
        self._indices: dict[str, dict[str, dict[str, Any]]] = {}
        self._crear_esquema()

    # --- API de Database ---------------------------------------------------------------------------

    def __getattr__(self, nombre: str) -> ColeccionPg:
        if nombre.startswith("_"):
            raise AttributeError(nombre)
        return ColeccionPg(self, nombre)

    def __getitem__(self, nombre: str) -> ColeccionPg:
        return ColeccionPg(self, nombre)

    def command(self, orden: Any, *_: Any, **__: Any) -> dict[str, Any]:
        if orden not in ("ping", {"ping": 1}):
            raise NotImplementedError(f"comando sin soporte en Postgres: {orden!r}")
        with self._conexion() as conn:
            conn.execute("SELECT 1")
        return {"ok": 1.0}

    def list_collection_names(self) -> list[str]:
        with self._conexion() as conn:
            filas = conn.execute(f"SELECT DISTINCT coleccion FROM {self._nombre_tabla} ORDER BY 1").fetchall()
        return [f[0] for f in filas]

    def close(self) -> None:
        self._pool.close()

    # --- conexiones -----------------------------------------------------------------------------------

    @contextmanager
    def _conexion(self) -> Iterator[Any]:
        with self._pool.connection() as conn:
            yield conn

    @contextmanager
    def _transaccion(self) -> Iterator[Any]:
        with self._pool.connection() as conn, conn.transaction():
            yield conn

    def _con_reintento(self, operacion: Any) -> Any:
        """Repite una escritura que chocó al insertar con otra concurrente; el segundo choque se propaga."""

        for intento in range(_INTENTOS):
            try:
                with self._transaccion() as conn:
                    return operacion(conn)
            except DuplicateKeyError:
                if intento == _INTENTOS - 1:
                    raise
        raise AssertionError("inalcanzable")

    # --- SQL ----------------------------------------------------------------------------------------------

    @property
    def _nombre_tabla(self) -> str:
        return f'"{self._esquema}"."{self._tabla}"'

    def _consulta(self, coleccion: str, filtro: Mapping[str, Any], bloquear: bool) -> tuple[str, list[Any]]:
        from psycopg.types.json import Jsonb

        condiciones = ["coleccion = %s"]
        params: list[Any] = [coleccion]
        id_, igualdades = _prefiltro(filtro)
        if id_ is not None:
            condiciones.append("id = %s")
            params.append(id_)
        for ruta, valor in igualdades:
            condiciones.append(
                "jsonb_path_exists(doc::jsonb, %s::jsonpath, jsonb_build_object('v', %s::jsonb))"
            )
            params.extend([f"{_ruta_jsonpath(ruta)} ? (@ == $v)", Jsonb(valor)])
        sql = f"SELECT id, doc FROM {self._nombre_tabla} WHERE {' AND '.join(condiciones)} ORDER BY n"
        if bloquear:
            sql += " FOR UPDATE"
        return sql, params

    def _insertar(self, conn: Any, coleccion: str, id_: str, doc: Mapping[str, Any]) -> None:
        import psycopg.errors
        from psycopg.types.json import Json

        cuerpo, expira = self._cuerpo(doc)
        try:
            with conn.transaction():
                conn.execute(
                    f"INSERT INTO {self._nombre_tabla} (coleccion, id, doc, expira) VALUES (%s, %s, %s, %s)",
                    (coleccion, id_, Json(cuerpo), expira),
                )
        except psycopg.errors.UniqueViolation as e:
            raise DuplicateKeyError(f"{coleccion}: clave duplicada ({e.diag.constraint_name})") from e

    def _escribir(self, conn: Any, coleccion: str, id_: str, doc: Mapping[str, Any]) -> None:
        import psycopg.errors
        from psycopg.types.json import Json

        cuerpo, expira = self._cuerpo(doc)
        try:
            with conn.transaction():
                conn.execute(
                    f"UPDATE {self._nombre_tabla} SET doc = %s, expira = %s WHERE coleccion = %s AND id = %s",
                    (Json(cuerpo), expira, coleccion, id_),
                )
        except psycopg.errors.UniqueViolation as e:
            raise DuplicateKeyError(f"{coleccion}: clave duplicada ({e.diag.constraint_name})") from e

    def _borrar(self, conn: Any, coleccion: str, ids: list[str]) -> None:
        if ids:
            conn.execute(
                f"DELETE FROM {self._nombre_tabla} WHERE coleccion = %s AND id = ANY(%s)", (coleccion, ids)
            )

    @staticmethod
    def _cuerpo(doc: Mapping[str, Any]) -> tuple[dict[str, Any], datetime | None]:
        sin_id = {k: v for k, v in doc.items() if k != "_id"}
        expira = sin_id.get("_expira")
        return _codificar(sin_id), _utc(expira) if isinstance(expira, datetime) else None

    # --- esquema, índices y purga -----------------------------------------------------------------------

    def _crear_esquema(self) -> None:
        # Un bloqueo asesor serializa la creación entre réplicas que arrancan a la vez.
        with self._transaccion() as conn:
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (_BLOQUEO_ESQUEMA,))
            conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{self._esquema}"')
            conn.execute(
                f"""CREATE TABLE IF NOT EXISTS {self._nombre_tabla} (
                    n bigint GENERATED ALWAYS AS IDENTITY,
                    coleccion text NOT NULL,
                    id text NOT NULL,
                    doc json NOT NULL,
                    expira timestamptz,
                    PRIMARY KEY (coleccion, id)
                )"""
            )
            conn.execute(
                f'CREATE INDEX IF NOT EXISTS "{self._tabla}_expira" ON {self._nombre_tabla} (expira) '
                "WHERE expira IS NOT NULL"
            )
            # Supabase expone por su API REST las tablas del esquema ``public`` sin RLS; sin políticas, el
            # acceso por clave anónima queda denegado. El rol de la conexión es el dueño y no se ve afectado.
            activa = conn.execute(
                "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (self._nombre_tabla,)
            ).fetchone()[0]
            if not activa:
                conn.execute(f"ALTER TABLE {self._nombre_tabla} ENABLE ROW LEVEL SECURITY")

    def _indice_unico(self, coleccion: str, campos: list[str]) -> str:
        from psycopg import sql

        nombre = (
            f"ux_{self._tabla}_{hashlib.sha1((coleccion + '|' + ','.join(campos)).encode()).hexdigest()[:16]}"
        )
        expresiones = [
            sql.SQL("(coalesce((doc::jsonb) #> {}, 'null'::jsonb))").format(
                sql.Literal("{" + ",".join(c.split(".")) + "}")
            )
            for c in campos
        ]
        sentencia = sql.SQL("CREATE UNIQUE INDEX IF NOT EXISTS {} ON {}.{} ({}) WHERE coleccion = {}").format(
            sql.Identifier(nombre),
            sql.Identifier(self._esquema),
            sql.Identifier(self._tabla),
            sql.SQL(", ").join(expresiones),
            sql.Literal(coleccion),
        )
        import psycopg.errors

        try:
            with self._transaccion() as conn:
                conn.execute("SELECT pg_advisory_xact_lock(%s)", (_BLOQUEO_ESQUEMA,))
                conn.execute(sentencia)
        except psycopg.errors.UniqueViolation as e:
            raise DuplicateKeyError(f"{coleccion}: duplicados por {'/'.join(campos)}") from e
        return nombre

    def purgar_vencidos(self) -> int:
        """Borra los documentos cuyo ``_expira`` ya pasó (el TTL de Mongo)."""

        with self._transaccion() as conn:
            n = conn.execute(
                f"DELETE FROM {self._nombre_tabla} WHERE expira IS NOT NULL AND expira <= now()"
            ).rowcount
        self._ultima_purga = time.monotonic()
        return n

    def _purgar_si_toca(self) -> None:
        if time.monotonic() - self._ultima_purga >= PURGA_CADA_S:
            try:
                self.purgar_vencidos()
            except Exception:  # noqa: BLE001 - la purga es oportunista: no debe romper la escritura que la dispara
                log.warning("no se pudo purgar lo vencido", exc_info=True)
                self._ultima_purga = time.monotonic()


#: Clave del bloqueo asesor de Postgres para crear esquema e índices.
_BLOQUEO_ESQUEMA = 7_262_045_001
