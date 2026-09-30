"""``graph.index``: el canónico avanza por lotes desde CI."""

from __future__ import annotations

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, Api, alcances, consulta, delta, simbolo, vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.tools import GraphIndexEntrada
from railspec.graph import (
    AccesoGrafo,
    AlmacenGrafo,
    IndexadorCanonico,
    IndiceDesfasado,
    IndiceRechazado,
)

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
