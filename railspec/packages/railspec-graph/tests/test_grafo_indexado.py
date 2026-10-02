"""``graph.index``: el canónico avanza por lotes desde CI."""

from __future__ import annotations

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, UNIDAD, Api, alcances, consulta, delta, simbolo, vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.tools import GraphIndexEntrada
from railspec.graph import (
    AccesoGrafo,
    AlmacenGrafo,
    IndexadorCanonico,
    IndiceDesfasado,
    IndiceRechazado,
)
from railspec.graph.motor import Meta

COMMIT_3 = "3" * 40


@pytest.fixture
def piezas(motor, org):
    acceso = AccesoGrafo(motor)
    grafo = AlmacenGrafo(acceso)
    return grafo, IndexadorCanonico(acceso, grafo), vinculo(org, NivelCodigo.restringido)


def _entrada(v, commit, d, lote=1, lotes=1, anterior=None, rama="main"):
    return GraphIndexEntrada(
        alcance=v.alcance, rama=rama, commit=commit, commit_anterior=anterior, lote=lote, lotes=lotes, delta=d
    )


def _buscar(grafo, org, v, texto="."):
    q = consulta(org, "certificados", {"verbo": "search", "texto": texto}, limite=50)
    return grafo.consultar(q, [v.alcance])


def test_indice_completo_por_lotes_solo_avanza_con_el_ultimo(piezas, motor, org):
    grafo, idx, v = piezas
    api = Api()
    mitad = len(api.todos) // 2
    primero = delta(simbolos=api.todos[:mitad])
    segundo = delta(simbolos=api.todos[mitad:], aristas=api.aristas)

    salida = idx.recibir(_entrada(v, COMMIT_1, primero, 1, 2), v)
    assert (salida.lotes_recibidos, salida.aplicado) == (1, False)
    assert _buscar(grafo, org, v).commits == {}

    salida = idx.recibir(_entrada(v, COMMIT_1, segundo, 2, 2), v)
    assert (salida.lotes_recibidos, salida.aplicado) == (2, True)
    resultado = _buscar(grafo, org, v)
    assert resultado.commits == {"api": COMMIT_1}
    assert {r.ref.nombre for r in resultado.resultados} == {s.nombre for s in api.todos}
    assert not [g for g in motor.listar(f"railspec:{org}:") if ":i:" in g]

    related = grafo.consultar(
        consulta(org, "certificados", {"verbo": "related", "simbolo": api.emitir.id}), [v.alcance]
    )
    assert any(r.ref.tipo == "nodo-grafo" and r.ref.clase == "proceso" for r in related.resultados)


def test_lote_repetido_es_idempotente(piezas):
    _, idx, v = piezas
    d = delta(simbolos=[Api().main])
    assert idx.recibir(_entrada(v, COMMIT_1, d, 1, 2), v).lotes_recibidos == 1
    assert idx.recibir(_entrada(v, COMMIT_1, d, 1, 2), v).lotes_recibidos == 1


def test_commit_ya_aplicado_es_idempotente(piezas, org):
    grafo, idx, v = piezas
    d = Api().delta()
    idx.recibir(_entrada(v, COMMIT_1, d), v)
    assert idx.recibir(_entrada(v, COMMIT_1, delta()), v).aplicado
    assert len(_buscar(grafo, org, v).resultados) == 5


def test_incremental_borra_y_avanza(piezas, org):
    grafo, idx, v = piezas
    api = Api()
    idx.recibir(_entrada(v, COMMIT_1, api.delta()), v)
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    d = delta(simbolos=[nuevo], borrados=[api.fuente])
    assert idx.recibir(_entrada(v, COMMIT_2, d, anterior=COMMIT_1), v).aplicado
    salida = _buscar(grafo, org, v, "pdf")
    assert {r.ref.nombre for r in salida.resultados} == {"pdf.render", "pdf.marca_agua"}
    assert salida.commits == {"api": COMMIT_2}


def test_incremental_desfasado_se_rechaza(piezas):
    _, idx, v = piezas
    idx.recibir(_entrada(v, COMMIT_1, Api().delta()), v)
    with pytest.raises(IndiceDesfasado):
        idx.recibir(_entrada(v, COMMIT_3, delta(), anterior=COMMIT_2), v)


def test_indice_completo_reemplaza_el_canonico(piezas, org):
    grafo, idx, v = piezas
    api = Api()
    idx.recibir(_entrada(v, COMMIT_1, api.delta()), v)
    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[api.main])), v)
    assert [r.ref.nombre for r in _buscar(grafo, org, v).resultados] == ["app.main"]


def test_rama_o_repositorio_ajenos_se_rechazan(piezas, org):
    _, idx, v = piezas
    with pytest.raises(IndiceRechazado):
        idx.recibir(_entrada(v, COMMIT_1, delta(), rama="feature/x"), v)
    otro = vinculo(org, NivelCodigo.restringido).model_copy(
        update={"alcance": alcances(org, "certificados", "reporteria")[0]}
    )
    with pytest.raises(IndiceRechazado):
        idx.recibir(_entrada(v, COMMIT_1, delta()), otro)


def test_lotes_incoherentes_se_rechazan(piezas):
    _, idx, v = piezas
    idx.recibir(_entrada(v, COMMIT_1, delta(), 1, 3), v)
    with pytest.raises(IndiceRechazado):
        idx.recibir(_entrada(v, COMMIT_1, delta(), 2, 4), v)


def test_exclusiones_del_vinculo(piezas, org):
    grafo, idx, _ = piezas
    v = vinculo(org, NivelCodigo.restringido, ["vendor/"])
    oculto = simbolo("api", "vendor/lib.py", "funcion", "lib.oculta")
    idx.recibir(_entrada(v, COMMIT_1, delta(simbolos=[Api().main, oculto])), v)
    assert [r.ref.nombre for r in _buscar(grafo, org, v).resultados] == ["app.main"]


def test_borrar_repositorio_borra_lotes_pendientes(piezas, motor, org):
    grafo, idx, v = piezas
    idx.recibir(_entrada(v, COMMIT_1, delta(simbolos=[Api().main]), 1, 2), v)
    grafo.borrar_repositorio(v.alcance)
    assert motor.listar(f"railspec:{org}:") == []


# --- superposiciones de unidades integradas ------------------------------------------------


OTRA_UNIDAD = "0002-revocar-pdf"


def _sups(motor, v):
    return AccesoGrafo(motor).superposiciones(v.alcance)


def _con_unidad(grafo, org, v, unidad=UNIDAD, texto="pdf"):
    q = consulta(org, "certificados", {"verbo": "search", "texto": texto}, unidad=unidad, limite=50)
    return {r.ref.nombre for r in grafo.consultar(q, [v.alcance]).resultados}


def _sin_unidad(grafo, org, v, texto="pdf"):
    return {r.ref.nombre for r in _buscar(grafo, org, v, texto).resultados}


def _unidad_con_marca_de_agua(piezas, unidad=UNIDAD):
    """Canónico en COMMIT_1 y una unidad cuyo snapshot agrega ``pdf.marca_agua``."""

    grafo, idx, v = piezas
    idx.recibir(_entrada(v, COMMIT_1, Api().delta()), v)
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    grafo.aplicar_delta(v.alcance, COMMIT_1, delta(simbolos=[nuevo]), unidad)
    return nuevo


def test_integrada_la_superposicion_sigue_visible_hasta_que_el_canonico_alcanza_su_commit(piezas, motor, org):
    grafo, idx, v = piezas
    nuevo = _unidad_con_marca_de_agua(piezas)
    assert "pdf.marca_agua" not in _sin_unidad(grafo, org, v)

    assert grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2) is True
    assert "pdf.marca_agua" in _con_unidad(grafo, org, v)  # antes: la integración la descartaba
    assert "pdf.marca_agua" not in _sin_unidad(grafo, org, v)

    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[nuevo]), anterior=COMMIT_1), v)
    assert _sups(motor, v) == []
    assert "pdf.marca_agua" in _sin_unidad(grafo, org, v)
    assert "pdf.marca_agua" in _con_unidad(grafo, org, v)  # ahora sale del canónico


def test_otro_commit_no_retira_la_superposicion_retenida(piezas, motor, org):
    grafo, idx, v = piezas
    nuevo = _unidad_con_marca_de_agua(piezas)
    grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_3)

    # CI aplica primero un commit intermedio: la unidad todavía no está en el canónico.
    otro = simbolo("api", "src/otro.py", "funcion", "otro.paso")
    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[otro]), anterior=COMMIT_1), v)
    assert _sups(motor, v) == [UNIDAD]
    assert "pdf.marca_agua" in _con_unidad(grafo, org, v)

    idx.recibir(_entrada(v, COMMIT_3, delta(simbolos=[nuevo]), anterior=COMMIT_2), v)
    assert _sups(motor, v) == []


def test_retener_con_el_canonico_ya_en_ese_commit_borra_al_momento(piezas, motor, org):
    grafo, idx, v = piezas
    nuevo = _unidad_con_marca_de_agua(piezas)
    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[nuevo]), anterior=COMMIT_1), v)
    assert _sups(motor, v) == [UNIDAD]  # en curso: el índice no la toca

    assert grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2) is False
    assert _sups(motor, v) == []


def test_retener_sin_superposicion_no_hace_nada(piezas, motor, org):
    grafo, idx, v = piezas
    idx.recibir(_entrada(v, COMMIT_1, Api().delta()), v)
    assert grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2) is False
    assert _sups(motor, v) == []


def test_las_unidades_en_curso_no_se_tocan_al_avanzar_el_canonico(piezas, motor, org):
    grafo, idx, v = piezas
    nuevo = _unidad_con_marca_de_agua(piezas)
    en_curso = simbolo("api", "src/rev.py", "funcion", "pdf.revocar")
    grafo.aplicar_delta(v.alcance, COMMIT_1, delta(simbolos=[en_curso]), OTRA_UNIDAD)
    grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2)

    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[nuevo]), anterior=COMMIT_1), v)
    assert _sups(motor, v) == [OTRA_UNIDAD]
    assert "pdf.revocar" in _con_unidad(grafo, org, v, OTRA_UNIDAD)


def test_un_indice_completo_retira_todas_las_retenidas_aunque_no_sean_su_commit(piezas, motor, org):
    """CI pierde la cadena (corridas saltadas o fallidas) y re-afirma la rama con un índice completo."""

    grafo, idx, v = piezas
    _unidad_con_marca_de_agua(piezas)
    grafo.aplicar_delta(
        v.alcance,
        COMMIT_1,
        delta(simbolos=[simbolo("api", "src/rev.py", "funcion", "pdf.revocar")]),
        OTRA_UNIDAD,
    )
    grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2)  # CI se saltó COMMIT_2
    en_curso = simbolo("api", "src/ver.py", "funcion", "pdf.verificar")
    grafo.aplicar_delta(v.alcance, COMMIT_1, delta(simbolos=[en_curso]), "0003-verificar-pdf")

    idx.recibir(_entrada(v, COMMIT_3, Api().delta()), v)  # sin commit_anterior: índice completo
    assert _sups(motor, v) == [OTRA_UNIDAD, "0003-verificar-pdf"]


def test_reenvio_del_commit_aplicado_retira_lo_que_una_caida_dejo(piezas, motor, org):
    """El canónico avanzó pero el proceso cayó antes de retirar: el reenvío de CI lo completa."""

    grafo, idx, v = piezas
    nuevo = _unidad_con_marca_de_agua(piezas)
    idx.recibir(_entrada(v, COMMIT_2, delta(simbolos=[nuevo]), anterior=COMMIT_1), v)
    sup = AccesoGrafo(motor).espacio(v.alcance, UNIDAD)
    sup.fijar_meta(Meta(commit=COMMIT_1, unidad=UNIDAD, integrado=COMMIT_2))

    assert idx.recibir(_entrada(v, COMMIT_2, delta(), anterior=COMMIT_1), v).aplicado
    assert _sups(motor, v) == []


def test_el_commit_integrado_sobrevive_a_la_persistencia_de_la_meta(piezas, motor, org):
    grafo, _, v = piezas
    _unidad_con_marca_de_agua(piezas)
    grafo.retener_superposicion(v.alcance, UNIDAD, COMMIT_2)
    meta = AccesoGrafo(motor).espacio(v.alcance, UNIDAD).meta()
    assert (meta.integrado, meta.unidad, meta.commit) == (COMMIT_2, UNIDAD, COMMIT_1)
    assert meta.borrados == []
