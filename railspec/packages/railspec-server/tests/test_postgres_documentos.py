"""Capa de documentos sobre Postgres (``estado/postgres.py``).

La coincidencia de filtros, el orden y las actualizaciones son Python puro y corren siempre. Lo que toca la
base necesita ``RAILSPEC_PRUEBAS_POSTGRES=<url>`` (ver ``conftest.py``): sin ella esas pruebas se saltan.
La suite entera de los almacenes corre contra Postgres con la misma variable.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from pymongo import DESCENDING, ReturnDocument
from pymongo.errors import DuplicateKeyError
from railspec.server.config import Configuracion, ErrorConfiguracion, validar_arranque
from railspec.server.estado.postgres import (
    _AUSENTE,
    BaseDocumentosPg,
    _aplicar,
    _ordenar,
    _prefiltro,
    coincide,
)

URL = os.environ.get("RAILSPEC_PRUEBAS_POSTGRES")
necesita_postgres = pytest.mark.skipif(not URL, reason="sin RAILSPEC_PRUEBAS_POSTGRES")

AHORA = datetime(2026, 10, 2, 12, 0, tzinfo=UTC)


# --- coincidencia de filtros (sin base) -------------------------------------------------------------------


def test_igualdad_y_ruta_con_puntos_recorren_subdocumentos_y_arreglos():
    doc = {
        "unidad": {"org": "acme"},
        "repositorios": [{"repositorio": "a"}, {"repositorio": "b"}],
        "tags": ["x", "y"],
    }
    assert coincide(doc, {"unidad.org": "acme"})
    assert not coincide(doc, {"unidad.org": "otra"})
    assert coincide(doc, {"repositorios.repositorio": "b"})
    assert not coincide(doc, {"repositorios.repositorio": "c"})
    assert coincide(doc, {"tags": "y"})


def test_null_casa_con_ausente_y_ne_null_exige_valor():
    assert coincide({"a": None}, {"a": None}) and coincide({}, {"a": None})
    assert not coincide({"a": 1}, {"a": None})
    assert coincide({"a": 1}, {"a": {"$ne": None}}) and not coincide({}, {"a": {"$ne": None}})
    assert coincide({"a": None}, {"a": {"$in": [None, "x"]}}) and coincide(
        {"a": "x"}, {"a": {"$in": [None, "x"]}}
    )


def test_comparaciones_solo_entre_tipos_comparables_y_fechas_con_huso():
    assert coincide({"n": 5}, {"n": {"$gt": 4, "$lte": 5}}) and not coincide({"n": 5}, {"n": {"$gt": 5}})
    assert not coincide({"n": "5"}, {"n": {"$gt": 4}})  # texto contra número: no casa, como Mongo
    assert not coincide({"n": True}, {"n": {"$gte": 0}})
    assert coincide(
        {"f": AHORA}, {"f": {"$gte": AHORA - timedelta(seconds=1), "$lt": AHORA + timedelta(seconds=1)}}
    )
    assert coincide({"f": AHORA.replace(tzinfo=None)}, {"f": {"$lte": AHORA}})  # sin huso se toma UTC
    assert coincide({"en": "2026-10-02"}, {"en": {"$gte": "2026-10-01", "$lt": "2026-10-03"}})


def test_exists_or_and_y_bool_distinto_de_entero():
    doc = {"a": 1, "b": {"c": 2}}
    assert coincide(doc, {"b.c": {"$exists": True}}) and coincide(doc, {"b.x": {"$exists": False}})
    assert coincide(doc, {"$or": [{"a": 9}, {"b.c": 2}]}) and not coincide(
        doc, {"$or": [{"a": 9}, {"b.c": 9}]}
    )
    assert coincide(doc, {"$and": [{"a": 1}, {"b.c": 2}]}) and not coincide(
        doc, {"$and": [{"a": 1}, {"b.c": 3}]}
    )
    assert not coincide({"a": True}, {"a": 1}) and not coincide({"a": 1}, {"a": True})


def test_un_operador_sin_soporte_falla_en_voz_alta():
    with pytest.raises(NotImplementedError):
        coincide({"a": 1}, {"a": {"$regex": "x"}})
    with pytest.raises(NotImplementedError):
        coincide({"a": 1}, {"$nor": []})


def test_orden_multiple_con_ausentes_primero_y_descendente():
    docs = [
        {"_id": "1", "a": 2, "b": "x"},
        {"_id": "2", "a": 1, "b": "y"},
        {"_id": "3", "b": "z"},
        {"_id": "4", "a": 2, "b": "a"},
    ]
    assert [d["_id"] for d in _ordenar(list(docs), [("a", 1), ("b", 1)])] == ["3", "2", "4", "1"]
    assert [d["_id"] for d in _ordenar(list(docs), [("a", DESCENDING), ("_id", 1)])] == ["1", "4", "2", "3"]


def test_actualizaciones_set_inc_set_on_insert_y_rutas_anidadas():
    assert _aplicar({}, {"$inc": {"n": 2}}, insertando=True) == {"n": 2}
    assert _aplicar({"n": 2}, {"$inc": {"n": 3}}, insertando=False) == {"n": 5}
    assert _aplicar({"a": 1}, {"$set": {"_commits.repo": "abc"}}, insertando=False) == {
        "a": 1,
        "_commits": {"repo": "abc"},
    }
    assert _aplicar({"a": 1}, {"$setOnInsert": {"u": 1}}, insertando=False) == {"a": 1}
    assert _aplicar({}, {"$setOnInsert": {"u": 1}}, insertando=True) == {"u": 1}
    original = {"a": {"b": 1}}
    _aplicar(original, {"$set": {"a.b": 2}}, insertando=False)
    assert original == {"a": {"b": 1}}  # no muta el documento leído


def test_prefiltro_solo_deja_pasar_lo_que_no_cambia_el_resultado():
    id_, igualdades = _prefiltro(
        {
            "_id": "x",
            "unidad.org": "acme",
            "n": 3,
            "nulo": None,
            "f": {"$gt": 1},
            "$or": [{"a": 1}],
            "d": AHORA,
        }
    )
    assert id_ == "x" and igualdades == [("unidad.org", "acme"), ("n", 3)]
    assert _AUSENTE is not None


# --- configuración --------------------------------------------------------------------------------


def test_configuracion_postgres_desde_el_entorno_y_exclusion_con_mongo():
    c = Configuracion.desde_entorno({"RAILSPEC_POSTGRES_URL": "postgresql://u@h/db"})
    assert c.postgres_url == "postgresql://u@h/db" and c.postgres_esquema == "railspec" and not c.modo_memoria
    validar_arranque(c)
    with pytest.raises(ErrorConfiguracion, match="a la vez"):
        validar_arranque(Configuracion(mongo_uri="mongodb://m", postgres_url="postgresql://u@h/db"))
    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_POSTGRES_URL"):
        validar_arranque(Configuracion())
    con_tokens = Configuracion(postgres_url="postgresql://u@h/db", tokens_desarrollo={"t": ("ana", 1)})
    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_TOKENS_DESARROLLO"):
        validar_arranque(con_tokens)


def test_esquema_y_tabla_deben_ser_identificadores_simples():
    with pytest.raises(ValueError):
        BaseDocumentosPg("postgresql://u@h/db", esquema='x"; drop table y; --')


# --- contra Postgres ------------------------------------------------------------------------------


@pytest.fixture
def bd():
    esquema = "d_" + uuid.uuid4().hex[:10]
    base = BaseDocumentosPg(URL, esquema=esquema, tamano_pool=6)
    yield base
    with base._conexion() as conn:
        conn.execute(f'DROP SCHEMA "{esquema}" CASCADE')
    base.close()


@necesita_postgres
def test_fechas_viajan_como_datetime_con_huso_utc_y_las_claves_conservan_su_orden(bd):
    bd.c.insert_one({"_id": "1", "_en": AHORA, "gates": {"spec": 1, "plan": 2}, "anidado": {"f": AHORA}})
    doc = bd.c.find_one({"_id": "1"})
    assert doc["_en"] == AHORA and doc["_en"].tzinfo is not None and doc["anidado"]["f"] == AHORA
    assert list(doc["gates"]) == ["spec", "plan"]  # jsonb las reordenaría


@necesita_postgres
def test_sin_orden_find_devuelve_por_insercion_y_con_orden_respeta_sort_limit_y_proyeccion(bd):
    for i, n in enumerate([3, 1, 2]):
        bd.c.insert_one({"_id": f"id{i}", "n": n, "grande": "x" * 10})
    assert [d["_id"] for d in bd.c.find({})] == ["id0", "id1", "id2"]
    assert [d["n"] for d in bd.c.find({}).sort("n", 1).limit(2)] == [1, 2]
    assert bd.c.find_one({"_id": "id1"}, {"n": 1}) == {"_id": "id1", "n": 1}
    assert "grande" not in bd.c.find_one({"_id": "id1"}, {"grande": 0})
    assert bd.c.find_one({}, sort=[("n", DESCENDING)])["n"] == 3
    assert bd.c.count_documents({"n": {"$gte": 2}}) == 2


@necesita_postgres
def test_filtros_con_subdocumentos_arreglos_null_y_operadores_contra_la_base(bd):
    bd.c.insert_one({"_id": "a", "unidad": {"org": "acme"}, "repos": [{"r": "x"}], "w": None, "n": 1})
    bd.c.insert_one({"_id": "b", "unidad": {"org": "otra"}, "repos": [{"r": "y"}], "n": 5})
    assert [d["_id"] for d in bd.c.find({"unidad.org": "acme"})] == ["a"]
    assert [d["_id"] for d in bd.c.find({"repos.r": "y"})] == ["b"]
    assert [d["_id"] for d in bd.c.find({"w": None}).sort("_id", 1)] == ["a", "b"]
    assert [d["_id"] for d in bd.c.find({"$or": [{"n": 1}, {"n": {"$gt": 4}}]}).sort("_id", 1)] == ["a", "b"]
    assert [d["_id"] for d in bd.c.find({"_id": {"$gt": "a"}})] == ["b"]


@necesita_postgres
def test_indice_unico_rechaza_duplicados_y_replace_con_upsert_no_pisa_un_id_ajeno(bd):
    assert bd.perfiles.create_index([("org", 1), ("ws", 1)], unique=True)
    bd.perfiles.insert_one({"_id": "1", "org": "acme", "ws": None})
    with pytest.raises(DuplicateKeyError):
        bd.perfiles.insert_one(
            {"_id": "2", "org": "acme", "ws": None}
        )  # null cuenta como valor, como en Mongo
    bd.perfiles.insert_one({"_id": "3", "org": "acme", "ws": "w"})
    # Mismo _id, otro workspace: el filtro no casa, el insert choca con la clave y no se pisa el ajeno.
    bd.docs.insert_one({"_id": "x", "alcance": {"ws": "a"}, "v": 1})
    with pytest.raises(DuplicateKeyError):
        bd.docs.replace_one(
            {"_id": "x", "alcance.ws": "b"}, {"_id": "x", "alcance": {"ws": "b"}, "v": 2}, upsert=True
        )
    assert bd.docs.find_one({"_id": "x"})["v"] == 1
    info = bd.perfiles.index_information()
    assert any(i.get("unique") and i["key"] == [("org", 1), ("ws", 1)] for i in info.values())


@necesita_postgres
def test_find_one_and_update_cuenta_sin_repetir_con_hilos_concurrentes(bd):
    resultados: list[int] = []

    def tomar() -> None:
        for _ in range(10):
            doc = bd.contadores.find_one_and_update(
                {"_id": "unidades"}, {"$inc": {"valor": 1}}, upsert=True, return_document=ReturnDocument.AFTER
            )
            resultados.append(doc["valor"])

    hilos = [threading.Thread(target=tomar) for _ in range(5)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    assert sorted(resultados) == list(range(1, 51))


@necesita_postgres
def test_turno_exclusivo_el_segundo_dueno_choca_y_el_mismo_dueno_renueva(bd):
    def tomar(dueno: str, ahora: datetime):
        return bd.turnos.find_one_and_update(
            {"_id": "t", "$or": [{"dueno": dueno}, {"vence": {"$lte": ahora}}]},
            {"$set": {"dueno": dueno, "vence": ahora + timedelta(seconds=30)}},
            upsert=True,
            return_document=ReturnDocument.AFTER,
        )

    assert tomar("A", AHORA)["dueno"] == "A"
    with pytest.raises(DuplicateKeyError):
        tomar("B", AHORA + timedelta(seconds=1))
    assert tomar("A", AHORA + timedelta(seconds=2))["dueno"] == "A"
    assert tomar("B", AHORA + timedelta(minutes=5))["dueno"] == "B"  # vencido


@necesita_postgres
def test_update_one_con_exists_false_fija_una_vez_y_upsert_incrementa(bd):
    bd.conv.insert_one({"_id": "c", "alcance": {"org": "acme"}})
    filtro = {"_id": "c", "alcance.org": "acme", "_commits.r": {"$exists": False}}
    assert bd.conv.update_one(filtro, {"$set": {"_commits.r": "abc"}}).matched_count == 1
    assert bd.conv.update_one(filtro, {"$set": {"_commits.r": "zzz"}}).matched_count == 0
    assert bd.conv.find_one({"_id": "c"})["_commits"] == {"r": "abc"}
    for _ in range(2):
        bd.fuga.update_one(
            {"_id": "d"}, {"$inc": {"n": 7}, "$set": {"_expira": AHORA + timedelta(days=2)}}, upsert=True
        )
    assert bd.fuga.find_one({"_id": "d"})["n"] == 14


@necesita_postgres
def test_ttl_purga_lo_vencido_y_lo_vigente_se_queda(bd):
    bd.c.insert_one({"_id": "vigente", "_expira": datetime.now(UTC) + timedelta(days=1)})
    bd.c.insert_one({"_id": "vencido", "_expira": datetime.now(UTC) - timedelta(seconds=5)})
    bd.c.insert_one({"_id": "sin-ttl"})
    bd.purgar_vencidos()
    assert sorted(d["_id"] for d in bd.c.find({})) == ["sin-ttl", "vigente"]
    # Un $set de _expira en una actualización también llega a la columna.
    bd.c.update_one({"_id": "vigente"}, {"$set": {"_expira": datetime.now(UTC) - timedelta(seconds=1)}})
    assert bd.purgar_vencidos() == 1


@necesita_postgres
def test_agregacion_match_group_sum_borrado_catalogo_y_ping(bd):
    bd.t.insert_many(
        [{"org": "a", "tokens": 3, "c": 1.5}, {"org": "a", "tokens": 4, "c": 0.5}, {"org": "b", "tokens": 1}]
    )
    filas = list(
        bd.t.aggregate(
            [
                {"$match": {"org": {"$in": ["a", "b"]}}},
                {
                    "$group": {
                        "_id": {"o": "$org"},
                        "n": {"$sum": 1},
                        "t": {"$sum": "$tokens"},
                        "c": {"$sum": "$c"},
                    }
                },
            ]
        )
    )
    assert {f["_id"]["o"]: (f["n"], f["t"], f["c"]) for f in filas} == {"a": (2, 7, 2.0), "b": (1, 1, 0)}
    assert (
        bd.t.delete_one({"org": "b"}).deleted_count == 1 and bd.t.delete_one({"org": "b"}).deleted_count == 0
    )
    assert bd.t.delete_many({"org": "a"}).deleted_count == 2
    assert bd.command("ping") == {"ok": 1.0} and bd.list_collection_names() == []


@necesita_postgres
def test_caracter_nul_no_rompe_la_escritura_y_la_tabla_nace_con_rls(bd):
    bd.c.insert_one({"_id": "n", "texto": "a\x00b"})
    assert bd.c.find_one({"_id": "n"})["texto"] == "a�b"
    with bd._conexion() as conn:
        activa = conn.execute(
            "SELECT relrowsecurity FROM pg_class WHERE oid = %s::regclass", (bd._nombre_tabla,)
        ).fetchone()[0]
    assert activa  # Supabase expone ``public`` por su API REST: sin políticas, el acceso anónimo se deniega


@necesita_postgres
def test_dos_bases_sobre_el_mismo_esquema_arrancan_a_la_vez_sin_chocar():
    esquema = "d_" + uuid.uuid4().hex[:10]
    errores: list[BaseException] = []

    def abrir() -> None:
        try:
            b = BaseDocumentosPg(URL, esquema=esquema, tamano_pool=2)
            b.x.create_index([("a", 1)], unique=True)
            b.close()
        except BaseException as e:  # noqa: BLE001
            errores.append(e)

    hilos = [threading.Thread(target=abrir) for _ in range(4)]
    [h.start() for h in hilos]
    [h.join() for h in hilos]
    limpio = BaseDocumentosPg(URL, esquema=esquema, tamano_pool=1)
    with limpio._conexion() as conn:
        conn.execute(f'DROP SCHEMA "{esquema}" CASCADE')
    limpio.close()
    assert not errores
