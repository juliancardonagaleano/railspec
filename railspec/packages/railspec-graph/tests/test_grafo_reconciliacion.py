"""Reconciliación del canónico con el repositorio: el resumen de CI se compara con lo que quedó guardado.

Corre contra el doble en memoria y, con ``RAILSPEC_FALKORDB_URL``, contra FalkorDB real. Un índice
con divergencias se aplica igual: se registra y se avisa, nunca se rechaza.
"""

from __future__ import annotations

import json
import logging

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, Api, alcances, consulta, delta, simbolo, vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.resumen import resumir
from railspec.contracts.tools import GraphIndexEntrada
from railspec.graph import AccesoGrafo, AlmacenGrafo, IndexadorCanonico
from railspec.graph.motor import Meta

COMMIT_3 = "3" * 40
BUSCAR = {"verbo": "search", "texto": "pdf"}


def _resumen(simbolos):
    """El resumen que calcularía CI sobre el árbol completo del commit."""

    return resumir((s.id, s.ruta, s.sha256) for s in simbolos)


@pytest.fixture
def p(motor, org):
    acceso = AccesoGrafo(motor)
    grafo = AlmacenGrafo(acceso)
    return acceso, grafo, IndexadorCanonico(acceso, grafo), org


def _indexar(
    p, commit, simbolos, *, anterior=None, resumen=None, lote=1, lotes=1, exclusiones=(), enviados=None
):
    """``simbolos``: el árbol completo del commit (con él se calcula el resumen); ``enviados``: lo que
    el delta lleva de verdad (por defecto, todo). ``resumen=False`` manda el índice sin resumen."""

    _, _, idx, org = p
    v = vinculo(org, NivelCodigo.restringido, exclusiones)
    entrada = GraphIndexEntrada(
        alcance=v.alcance,
        rama="main",
        commit=commit,
        commit_anterior=anterior,
        lote=lote,
        lotes=lotes,
        delta=delta(simbolos=simbolos if enviados is None else enviados),
        resumen=None if resumen is False else (resumen or _resumen(simbolos)),
    )
    return idx.recibir(entrada, v), v


def _frescura(p, v=None):
    _, grafo, _, org = p
    (repo,) = alcances(org, "certificados", "api")
    salida = grafo.consultar(consulta(org, "certificados", BUSCAR), [repo])
    return salida.frescura["api"], salida.avisos


def test_un_canonico_integro_queda_verificado_y_sin_aviso(p):
    _indexar(p, COMMIT_1, Api().todos)

    f, avisos = _frescura(p)

    assert f.contenido_verificado is True
    assert f.divergencias_total == 0 and f.rutas_divergentes == []
    assert avisos == []


def test_un_delta_que_perdio_un_simbolo_se_detecta_con_sus_rutas(p, caplog):
    api = Api()
    _indexar(p, COMMIT_1, api.todos)
    nuevo = simbolo("api", "src/nuevo.py", "funcion", "nuevo.pdf")
    perdido = simbolo("api", "src/perdido.py", "funcion", "perdido.pdf")

    with caplog.at_level(logging.WARNING, logger="railspec.graph.indexado"):
        # En el árbol del commit hay dos símbolos nuevos; el delta solo llevó uno.
        salida, _ = _indexar(p, COMMIT_2, [*api.todos, nuevo, perdido], anterior=COMMIT_1, enviados=[nuevo])

    assert salida.aplicado  # una divergencia nunca hace fallar el índice
    f, avisos = _frescura(p)
    assert f.commit == COMMIT_2
    assert f.contenido_verificado is False
    assert f.divergencias_total == 1 and f.rutas_divergentes == ["src/perdido.py"]
    (aviso,) = avisos
    assert "api:" in aviso and "1 ruta(s)" in aviso and "src/perdido.py" in aviso
    assert "completo" in aviso and COMMIT_2[:12] in aviso
    assert any("src/perdido.py" in r.getMessage() for r in caplog.records if r.levelno == logging.WARNING)


def test_un_canonico_adulterado_a_proposito_se_detecta_en_el_siguiente_indice(p):
    acceso, _, _, org = p
    api = Api()
    _, v = _indexar(p, COMMIT_1, api.todos)
    assert _frescura(p)[0].contenido_verificado is True
    # Alguien borra a mano un símbolo del canónico y cambia el hash de otro.
    canon = acceso.espacio(v.alcance)
    canon.borrar_simbolos([api.render.id])
    canon.upsert_simbolos(
        [
            {
                "id": api.main.id,
                "nombre": api.main.nombre,
                "tipo": api.main.tipo.value,
                "ruta": api.main.ruta,
                "linea_inicio": 1,
                "linea_fin": 2,
                "sha256": "f" * 64,
            }
        ]
    )

    nuevo = simbolo("api", "src/otro.py", "funcion", "otro")
    _indexar(p, COMMIT_2, [*api.todos, nuevo], anterior=COMMIT_1, enviados=[nuevo])

    f, _ = _frescura(p)
    assert f.contenido_verificado is False
    assert f.rutas_divergentes == ["src/main.py", "src/pdf.py"]  # ordenadas; src/otro.py sí coincide
    assert f.divergencias_total == 2


def test_un_indice_sin_resumen_no_compara_ni_arrastra_el_resultado_anterior(p):
    api = Api()
    perdido = simbolo("api", "src/perdido.py", "funcion", "perdido")
    _indexar(p, COMMIT_1, [*api.todos, perdido], enviados=api.todos)
    assert _frescura(p)[0].contenido_verificado is False

    _indexar(p, COMMIT_2, api.todos, anterior=COMMIT_1, enviados=[], resumen=False)

    f, avisos = _frescura(p)
    assert f.commit == COMMIT_2
    assert f.contenido_verificado is None
    assert f.divergencias_total == 0 and f.rutas_divergentes == []
    assert avisos == []


def test_un_resumen_con_rutas_excluidas_no_cuenta_como_divergencia(p):
    """El servidor filtra el delta por las exclusiones del vínculo; el resumen de CI trae esas rutas."""

    api = Api()
    _, v = _indexar(p, COMMIT_1, api.todos, exclusiones=["tests"])

    f, avisos = _frescura(p)
    canon = p[0].espacio(v.alcance).todos_simbolos()
    assert api.prueba.id not in {s["id"] for s in canon}  # de verdad no está en el canónico
    assert f.contenido_verificado is True and avisos == []


def test_una_exclusion_mal_aplicada_si_se_detecta(p):
    """Si el canónico trae algo que el resumen no tiene (fuera de lo excluido), diverge."""

    api = Api()
    sobrante = simbolo("api", "src/sobrante.py", "funcion", "sobrante")
    _indexar(p, COMMIT_1, api.todos, exclusiones=["tests"], enviados=[*api.todos, sobrante])

    f, _ = _frescura(p)
    assert f.contenido_verificado is False and f.rutas_divergentes == ["src/sobrante.py"]


def test_el_resumen_llega_en_un_lote_que_no_completa_el_commit(p):
    """Los lotes pueden llegar desordenados: el que lleva el resumen no es el que aplica el índice."""

    api = Api()
    primero, segundo = api.todos[:2], api.todos[2:]
    resumen = _resumen(api.todos)
    salida, _ = _indexar(p, COMMIT_1, segundo, lote=2, lotes=2, resumen=resumen)
    assert not salida.aplicado
    salida, _ = _indexar(p, COMMIT_1, primero, lote=1, lotes=2, resumen=False)
    assert salida.aplicado

    assert _frescura(p)[0].contenido_verificado is True


def test_un_resumen_que_llega_con_el_reenvio_de_un_commit_ya_aplicado_no_cambia_nada(p):
    api = Api()
    resumen = _resumen(api.todos)
    _indexar(p, COMMIT_1, api.todos[:2], lote=1, lotes=2, resumen=False)
    _indexar(p, COMMIT_1, api.todos[2:], lote=2, lotes=2, resumen=False)  # aplica, sin resumen
    assert _frescura(p)[0].contenido_verificado is None

    # Un reenvío del commit ya aplicado no cambia nada: el resumen llegó tarde.
    _indexar(p, COMMIT_1, api.todos[2:], lote=2, lotes=2, resumen=resumen)
    assert _frescura(p)[0].contenido_verificado is None


def test_las_rutas_divergentes_son_a_lo_sumo_veinte_y_alfabeticas(p):
    perdidos = [simbolo("api", f"src/m{i:02d}.py", "funcion", f"f{i}") for i in range(25)]

    _indexar(p, COMMIT_1, perdidos, enviados=[])

    f, (aviso,) = _frescura(p)
    assert f.divergencias_total == 25
    assert f.rutas_divergentes == [f"src/m{i:02d}.py" for i in range(20)]
    assert "25 ruta(s)" in aviso and "src/m00.py" in aviso and "y 20 más" in aviso


def test_la_meta_del_canonico_sobrevive_a_la_persistencia(p, motor):
    acceso, _, _, org = p
    (repo,) = alcances(org, "certificados", "api")
    e = acceso.espacio(repo)
    e.fijar_meta(
        Meta(
            commit=COMMIT_1,
            cubiertos=[COMMIT_1, COMMIT_2],
            contenido_verificado=False,
            divergencias_total=3,
            rutas_divergentes=["a.py", "b.py"],
        )
    )

    m = e.meta()
    assert m.cubiertos == [COMMIT_1, COMMIT_2]
    assert (m.contenido_verificado, m.divergencias_total, m.rutas_divergentes) == (False, 3, ["a.py", "b.py"])
    e.fijar_meta(Meta(commit=COMMIT_1))  # escribir sin nada lo deja en el defecto
    m = e.meta()
    assert m.cubiertos == [] and m.contenido_verificado is None and m.divergencias_total == 0


def test_el_resumen_de_la_preparacion_sobrevive_a_la_persistencia(p):
    acceso, _, _, org = p
    (repo,) = alcances(org, "certificados", "api")
    e = acceso.espacio_indexado(repo, COMMIT_1)
    e.fijar_meta(Meta(commit=COMMIT_1, lotes=2, recibidos=[1], resumen=[("src/a.py", 2, "0" * 16)]))

    assert e.meta().resumen == [("src/a.py", 2, "0" * 16)]


def test_el_json_de_la_meta_del_canonico_no_gana_claves_para_las_replicas_anteriores(p, motor):
    """Una réplica anterior lee el json con ``Meta(**json)``: lo nuevo va en una propiedad aparte."""

    if not hasattr(motor, "_leer"):
        pytest.skip("solo FalkorDB guarda la meta como json")
    acceso, _, _, org = p
    _, v = _indexar(p, COMMIT_1, Api().todos, enviados=[])
    nombre = acceso.espacio(v.alcance)._grafo

    (fila,) = motor._leer(nombre, "MATCH (m:Meta) RETURN m.json, m.ext")
    assert set(json.loads(fila[0])) == {
        "commit", "unidad", "borrados", "aristas_borradas", "base", "lotes", "recibidos",
    }  # fmt: skip
    assert json.loads(fila[1])["contenido_verificado"] is False


def test_una_replica_anterior_que_reescribe_la_meta_no_deja_un_resultado_ajeno(p, motor):
    """Si otra réplica (sin esta versión) avanza el canónico, ``ext`` describe otro commit y no vale."""

    if not hasattr(motor, "_escribir"):
        pytest.skip("solo FalkorDB guarda la meta como json")
    acceso, _, _, org = p
    _, v = _indexar(p, COMMIT_1, Api().todos, enviados=[])
    assert _frescura(p)[0].contenido_verificado is False
    nombre = acceso.espacio(v.alcance)._grafo

    # Lo que haría ``escribir_meta`` de una réplica anterior: reescribe el json y no toca ``ext``.
    motor._escribir(nombre, "MATCH (m:Meta) SET m.json = $j", {"j": json.dumps({"commit": COMMIT_2})})

    meta = acceso.espacio(v.alcance).meta()
    assert meta.commit == COMMIT_2
    assert meta.contenido_verificado is None and meta.divergencias_total == 0 and meta.cubiertos == []


# --- MotorFalkor sin servidor: la forma de la meta en el nodo ---------------------------------


class _FalkorDeMeta:
    """Un FalkorDB de mentira que solo sabe del nodo ``Meta``: lo justo para ver cómo se guarda."""

    def __init__(self) -> None:
        self.nodo: dict = {}
        self.connection = self
        self.consultas: list[str] = []

    def exists(self, _grafo):
        return True

    def select_graph(self, _grafo):
        return self

    def query(self, cypher, params=None):
        self.consultas.append(cypher)
        if cypher.startswith("MERGE (m:Meta) SET m.json"):
            self.nodo = {"json": params["j"], "actualizado": params["a"], "ext": params["e"]}
        resultado = type("R", (), {})()
        resultado.result_set = []
        return resultado

    def ro_query(self, cypher, params=None):
        resultado = type("R", (), {})()
        n = self.nodo
        resultado.result_set = [[n["json"], n["actualizado"], n["ext"]]] if n else []
        return resultado


def test_motor_falkor_guarda_lo_nuevo_fuera_del_json_y_lo_lee_de_vuelta():
    from datetime import UTC, datetime

    from railspec.graph.motor_falkordb import MotorFalkor

    db = _FalkorDeMeta()
    m = MotorFalkor(db)
    retenido = datetime(2026, 10, 1, 12, tzinfo=UTC)
    meta = Meta(
        commit=COMMIT_1,
        unidad="0001-emitir-pdf",
        integrado=COMMIT_2,
        retenido_en=retenido,
        cubiertos=[COMMIT_1, COMMIT_2],
        contenido_verificado=False,
        divergencias_total=2,
        rutas_divergentes=["a.py", "b.py"],
        resumen=[("a.py", 1, "0" * 16)],
    )
    m.escribir_meta("g", meta)

    assert set(json.loads(db.nodo["json"])) == {
        "commit", "unidad", "borrados", "aristas_borradas", "base", "lotes", "recibidos", "integrado",
    }  # fmt: skip
    leida = m.leer_meta("g")
    assert leida.retenido_en == retenido
    assert (leida.cubiertos, leida.contenido_verificado, leida.divergencias_total) == (
        [COMMIT_1, COMMIT_2],
        False,
        2,
    )
    assert leida.rutas_divergentes == ["a.py", "b.py"] and leida.resumen == [("a.py", 1, "0" * 16)]

    m.escribir_meta("g", Meta(commit=COMMIT_1))  # sin nada nuevo: ``ext`` se borra
    assert db.nodo["ext"] is None
    assert m.leer_meta("g").cubiertos == []

    # Una réplica anterior reescribió el json con otro commit y no tocó ``ext``: no vale.
    m.escribir_meta("g", meta)
    db.nodo["json"] = json.dumps({"commit": COMMIT_3})
    assert m.leer_meta("g").contenido_verificado is None
