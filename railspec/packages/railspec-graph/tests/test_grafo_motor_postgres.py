"""Lo que solo el motor de Postgres puede romper: SQL, concurrencia y persistencia entre instancias.

El resto de las pruebas del grafo ya corre contra Postgres con ``RAILSPEC_PRUEBAS_POSTGRES=<url>``
(``conftest.py``); aquí van los casos propios. Sin esa variable se saltan.
"""

from __future__ import annotations

import os
import threading
import uuid
from datetime import UTC, datetime

import pytest
from railspec.graph.motor import AristaMotor, Cluster, Meta, Proceso, Traza

URL = os.environ.get("RAILSPEC_PRUEBAS_POSTGRES")
pytestmark = pytest.mark.skipif(not URL, reason="sin RAILSPEC_PRUEBAS_POSTGRES")


def _simbolo(i: str, nombre: str | None = None, **extra) -> dict:
    return {
        "id": i,
        "nombre": nombre or i,
        "tipo": "function",
        "ruta": "src/a.py",
        "linea_inicio": 1,
        "linea_fin": 5,
        "sha256": "0" * 64,
        **extra,
    }


@pytest.fixture
def motor():
    from railspec.graph.motor_postgres import MotorPostgres

    # Un esquema por prueba: ``DROP SCHEMA`` limpia todo y las pruebas no se pisan.
    esquema = "t_" + uuid.uuid4().hex[:12]
    m = MotorPostgres.desde_url(URL, esquema=esquema, tamano_pool=4)
    yield m
    with m._pool.connection() as conn:
        conn.execute(f'DROP SCHEMA "{esquema}" CASCADE')
    m.cerrar()


def test_el_esquema_se_crea_una_vez_aunque_arranquen_varias_instancias_a_la_vez(motor):
    from railspec.graph.motor_postgres import MotorPostgres

    errores: list[Exception] = []

    def arrancar() -> None:
        try:
            MotorPostgres.desde_url(URL, esquema=motor._esquema, tamano_pool=1).cerrar()
        except Exception as e:  # noqa: BLE001
            errores.append(e)

    hilos = [threading.Thread(target=arrancar) for _ in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == []


def test_dos_instancias_ven_el_mismo_grafo(motor):
    from railspec.graph.motor_postgres import MotorPostgres

    motor.upsert_simbolos("g", [_simbolo("a")])
    otra = MotorPostgres.desde_url(URL, esquema=motor._esquema, tamano_pool=1)
    try:
        assert otra.existe("g") and set(otra.simbolos("g", ["a"])) == {"a"}
    finally:
        otra.cerrar()


def test_rls_activa_en_todas_las_tablas(motor):
    with motor._pool.connection() as conn:
        filas = conn.execute(
            "SELECT relname, relrowsecurity FROM pg_class c JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE n.nspname = %s AND relkind = 'r'",
            (motor._esquema,),
        ).fetchall()
    assert len(filas) == 6 and all(activa for _, activa in filas)


def test_un_lote_con_el_mismo_id_repetido_no_falla_y_gana_el_ultimo(motor):
    motor.upsert_simbolos("g", [_simbolo("a", "viejo"), _simbolo("a", "nuevo")])
    assert motor.simbolos("g", ["a"])["a"]["nombre"] == "nuevo"


def test_la_busqueda_exacta_trata_porcentaje_y_guion_bajo_como_texto(motor):
    motor.upsert_simbolos(
        "g",
        [
            _simbolo("1", "pkg.f_x"),
            _simbolo("2", "pkg.fax"),
            _simbolo("3", "100%"),
            _simbolo("4", "ñandú.cálculo"),
        ],
    )
    assert [s["id"] for s in motor.buscar_nombre("g", "f_x", [], True, 10)] == ["1"]
    assert [s["id"] for s in motor.buscar_nombre("g", "%", [], False, 10)] == ["3"]
    assert [s["id"] for s in motor.buscar_nombre("g", "cálculo", [], True, 10)] == ["4"]
    assert [s["id"] for s in motor.buscar_nombre("g", "CÁLCULO", [], False, 10)] == ["4"]


def test_upsert_conserva_el_embedding_y_borrar_lo_quita(motor):
    motor.upsert_simbolos("g", [_simbolo("a")])
    motor.fijar_embeddings("g", {"a": [1.0, 0.0], "no-existe": [1.0, 1.0]})
    motor.upsert_simbolos("g", [_simbolo("a", "renombrado")])
    assert motor.leer_embeddings("g", ["a", "no-existe"]) == {"a": [1.0, 0.0]}
    assert motor.knn("g", [1.0, 0.0], 5)[0][0] == "a"
    motor.borrar_simbolos("g", ["a"])
    assert motor.leer_embeddings("g", ["a"]) == {}


def test_borrar_simbolos_quita_las_aristas_que_lo_tocan_y_deja_el_resto(motor):
    motor.upsert_simbolos("g", [_simbolo("a"), _simbolo("b")])
    motor.agregar_aristas(
        "g",
        [
            AristaMotor("a", "b", "calls"),
            AristaMotor("a", "otro-repo::x", "calls"),
            AristaMotor("b", "b", "calls"),
        ],
    )
    motor.borrar_simbolos("g", ["b"])
    assert motor.todas_aristas("g") == [AristaMotor("a", "otro-repo::x", "calls")]


def test_borrar_el_grafo_arrastra_todo(motor):
    motor.upsert_simbolos("g", [_simbolo("a")])
    motor.agregar_aristas("g", [AristaMotor("a", "x", "calls")])
    motor.reemplazar_analitica("g", [Cluster("c", "c", ("a",))], [Proceso("p", "p", "a", ("a",))])
    motor.agregar_trazas("g", [Traza("u", "CA-01", "a")])
    motor.escribir_meta("g", Meta(commit="1" * 40))
    motor.upsert_simbolos("h", [_simbolo("a")])
    motor.borrar("g")
    assert not motor.existe("g") and motor.existe("h")
    with motor._pool.connection() as conn:
        for tabla in ("grafo_simbolos", "grafo_aristas", "grafo_clusters", "grafo_procesos", "grafo_trazas"):
            n = conn.execute(
                f'SELECT count(*) FROM "{motor._esquema}"."{tabla}" WHERE grafo = %s', ("g",)
            ).fetchone()[0]
            assert n == 0, tabla


def test_la_meta_completa_vuelve_igual_y_sellar_solo_rellena_lo_que_falta(motor):
    sello = datetime(2026, 10, 1, 12, tzinfo=UTC)
    otro = datetime(2026, 11, 1, 12, tzinfo=UTC)
    motor.upsert_simbolos("g", [_simbolo("a")])
    motor.sellar("g", sello)  # antes de la primera meta: no hay json
    assert motor.leer_meta("g") == Meta(actualizado=sello)
    meta = Meta(
        commit="1" * 40,
        unidad="0001-x",
        borrados=["b", "a"],
        aristas_borradas=[("a", "b", "calls")],
        base="2" * 40,
        lotes=3,
        recibidos=[2, 0],
        integrado="3" * 40,
        cubiertos=["4" * 40, "1" * 40],
        contenido_verificado=False,
        divergencias_total=2,
        rutas_divergentes=["a.py", "b.py"],
        resumen=[("a.py", 1, "0" * 16)],
        retenido_en=datetime(2026, 10, 2, tzinfo=UTC),
    )
    motor.escribir_meta("g", meta)  # sin ``actualizado``: la meta lo pisa con ``None``
    leida = motor.leer_meta("g")
    assert leida.borrados == ["a", "b"] and leida.recibidos == [0, 2] and leida.actualizado is None
    for campo in (
        "commit",
        "unidad",
        "base",
        "lotes",
        "integrado",
        "contenido_verificado",
        "divergencias_total",
    ):
        assert getattr(leida, campo) == getattr(meta, campo), campo
    assert leida.cubiertos == meta.cubiertos and leida.aristas_borradas == meta.aristas_borradas
    assert leida.resumen == meta.resumen and leida.retenido_en == meta.retenido_en
    motor.sellar("g", sello)
    motor.sellar("g", otro)
    assert motor.leer_meta("g").actualizado == sello
    motor.sellar("no-existe", sello)
    assert not motor.existe("no-existe")


def test_listar_filtra_por_prefijo_literal(motor):
    for g in ("railspec:a:x", "railspec:a:y", "railspec:ab:x", "railspec_a"):
        motor.upsert_simbolos(g, [_simbolo("s")])
    assert motor.listar("railspec:a:") == ["railspec:a:x", "railspec:a:y"]
    assert motor.listar("railspec_") == ["railspec_a"]


def test_escrituras_concurrentes_sobre_el_mismo_grafo_no_se_pisan(motor):
    errores: list[Exception] = []

    def escribir(n: int) -> None:
        try:
            motor.upsert_simbolos("g", [_simbolo(f"s{n}-{i}") for i in range(50)])
            motor.agregar_aristas("g", [AristaMotor(f"s{n}-{i}", f"s{n}-0", "calls") for i in range(50)])
        except Exception as e:  # noqa: BLE001
            errores.append(e)

    hilos = [threading.Thread(target=escribir, args=(n,)) for n in range(4)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert errores == []
    assert len(motor.todos_simbolos("g")) == 200 and len(motor.todas_aristas("g")) == 200


def test_el_ping_responde_y_el_arranque_lanza_si_la_base_no_esta(motor):
    motor.ping()

    from railspec.graph.motor_postgres import MotorPostgres

    class _PoolRoto:
        def wait(self, timeout=None):
            return None

        def connection(self):
            raise ConnectionError("sin base")

    with pytest.raises(ConnectionError):
        MotorPostgres(pool=_PoolRoto())  # type: ignore[arg-type]


def test_un_esquema_no_valido_se_rechaza_antes_de_conectar():
    from railspec.graph.motor_postgres import MotorPostgres

    with pytest.raises(ValueError):
        MotorPostgres("postgresql://x", esquema='a"; DROP SCHEMA public; --')
