from __future__ import annotations

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, UNIDAD, Api, alcances, arista, consulta, delta, simbolo
from railspec.contracts.almacen import GraphStore, VectorStore
from railspec.graph import AccesoGrafo, AlmacenGrafo, AlmacenVectores


@pytest.fixture
def api():
    return Api()


@pytest.fixture
def grafo(motor, org, api):
    g = AlmacenGrafo(AccesoGrafo(motor))
    (repo,) = alcances(org, "certificados", "api")
    g.aplicar_delta(repo, COMMIT_1, api.delta(), None)
    return g


def _q(org, cuerpo, **extra):
    return consulta(org, "certificados", cuerpo, **extra)


def _nombres(salida):
    return [r.ref.nombre for r in salida.resultados]


def test_implementa_los_protocolos(motor):
    acceso = AccesoGrafo(motor)
    assert isinstance(AlmacenGrafo(acceso), GraphStore)
    assert isinstance(AlmacenVectores(acceso), VectorStore)


def test_resolve_por_nombre_calificado_y_sufijo(grafo, org):
    vis = alcances(org, "certificados", "api")
    salida = grafo.consultar(_q(org, {"verbo": "resolve", "nombre": "emitir"}), vis)
    assert _nombres(salida) == ["servicio.emitir"]
    assert salida.commits == {"api": COMMIT_1}
    assert salida.resultados[0].ref.commit == COMMIT_1


def test_search_por_texto_y_tipo(grafo, org):
    vis = alcances(org, "certificados", "api")
    salida = grafo.consultar(_q(org, {"verbo": "search", "texto": "PDF"}), vis)
    assert set(_nombres(salida)) == {"pdf.render", "pdf.fuente"}
    vacio = grafo.consultar(_q(org, {"verbo": "search", "texto": "pdf", "tipos": ["clase"]}), vis)
    assert vacio.resultados == []


def test_traverse_upstream_con_distancia_y_riesgo(grafo, org, api):
    vis = alcances(org, "certificados", "api")
    q = _q(org, {"verbo": "traverse", "simbolo": api.render.id, "direccion": "upstream", "profundidad": 3})
    salida = grafo.consultar(q, vis)
    por_nombre = {r.ref.nombre: r for r in salida.resultados}
    assert por_nombre["servicio.emitir"].distancia == 1
    assert por_nombre["app.main"].distancia == 2
    assert por_nombre["test_servicio.test_emitir"].distancia == 2
    assert {r.riesgo for r in salida.resultados} == {"bajo"}


def test_traverse_downstream_filtra_relaciones(grafo, org, api):
    vis = alcances(org, "certificados", "api")
    q = _q(
        org,
        {"verbo": "traverse", "simbolo": api.prueba.id, "direccion": "downstream", "relaciones": ["prueba"]},
    )
    salida = grafo.consultar(q, vis)
    assert _nombres(salida) == ["servicio.emitir"]
    assert salida.resultados[0].riesgo is None


def test_limite_marca_truncado(grafo, org):
    vis = alcances(org, "certificados", "api")
    salida = grafo.consultar(_q(org, {"verbo": "search", "texto": "."}, limite=2), vis)
    assert len(salida.resultados) == 2 and salida.truncado


def test_related_devuelve_proceso_cluster_y_vecinos(grafo, org, api):
    vis = alcances(org, "certificados", "api")
    salida = grafo.consultar(_q(org, {"verbo": "related", "simbolo": api.emitir.id}), vis)
    clases = [r.ref.clase for r in salida.resultados if r.ref.tipo == "nodo-grafo"]
    assert "proceso" in clases and "cluster" in clases
    proceso = next(
        r.ref for r in salida.resultados if r.ref.tipo == "nodo-grafo" and r.ref.clase == "proceso"
    )
    assert proceso.nombre == "app.main"
    vecinos = {r.ref.nombre for r in salida.resultados if r.distancia == 1}
    assert vecinos == {"app.main", "pdf.render", "test_servicio.test_emitir"}


def test_delta_canonico_borra_y_avanza_commit(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(repo, COMMIT_2, delta(borrados=[api.fuente]), None)
    salida = grafo.consultar(_q(org, {"verbo": "search", "texto": "pdf"}), [repo])
    assert _nombres(salida) == ["pdf.render"]
    assert salida.commits == {"api": COMMIT_2}


def test_superposicion_solo_se_ve_con_su_unidad(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    sup = delta(simbolos=[nuevo], aristas=[arista(api.render, nuevo)], borrados=[api.fuente])
    grafo.aplicar_delta(repo, COMMIT_1, sup, UNIDAD)

    canon = grafo.consultar(_q(org, {"verbo": "search", "texto": "pdf"}), [repo])
    assert set(_nombres(canon)) == {"pdf.render", "pdf.fuente"}

    con_unidad = grafo.consultar(_q(org, {"verbo": "search", "texto": "pdf"}, unidad=UNIDAD), [repo])
    assert set(_nombres(con_unidad)) == {"pdf.render", "pdf.marca_agua"}

    abajo = grafo.consultar(
        _q(org, {"verbo": "traverse", "simbolo": api.render.id, "direccion": "downstream"}, unidad=UNIDAD),
        [repo],
    )
    assert _nombres(abajo) == ["pdf.marca_agua"]


def test_superposicion_se_reconstruye_con_cada_snapshot(grafo, org):
    (repo,) = alcances(org, "certificados", "api")
    a = simbolo("api", "src/a.py", "funcion", "a.uno")
    b = simbolo("api", "src/b.py", "funcion", "b.dos")
    grafo.aplicar_delta(repo, COMMIT_1, delta(simbolos=[a]), UNIDAD)
    grafo.aplicar_delta(repo, COMMIT_1, delta(simbolos=[b]), UNIDAD)
    salida = grafo.consultar(_q(org, {"verbo": "search", "texto": "."}, unidad=UNIDAD, limite=50), [repo])
    assert "b.dos" in _nombres(salida) and "a.uno" not in _nombres(salida)


def test_descartar_superposicion(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(repo, COMMIT_1, delta(borrados=[api.fuente]), UNIDAD)
    grafo.descartar_superposicion(repo, UNIDAD, COMMIT_2)
    salida = grafo.consultar(_q(org, {"verbo": "search", "texto": "fuente"}, unidad=UNIDAD), [repo])
    assert _nombres(salida) == ["pdf.fuente"]


def test_impacto_superposicion_para_el_gate(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    tocado = api.render.model_copy(update={"sha256": "f" * 64})
    grafo.aplicar_delta(repo, COMMIT_1, delta(simbolos=[tocado]), UNIDAD)
    q = _q(org, {"verbo": "resolve", "nombre": "x"}, unidad=UNIDAD)
    tocados, impacto = grafo.impacto_superposicion(q, [repo])
    assert tocados == [api.render.id]
    assert {r.ref.nombre for r in impacto} == {"servicio.emitir", "app.main", "test_servicio.test_emitir"}
    assert {r.riesgo for r in impacto} == {"bajo"}


def test_repositorio_sin_indexar_no_aparece(motor, org):
    g = AlmacenGrafo(AccesoGrafo(motor))
    salida = g.consultar(_q(org, {"verbo": "search", "texto": "x"}), alcances(org, "certificados", "api"))
    assert salida.resultados == [] and salida.commits == {}


def test_borrar_repositorio_borra_canonico_y_superposiciones(grafo, motor, org, api):
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(repo, COMMIT_1, delta(simbolos=[api.main]), UNIDAD)
    grafo.borrar_repositorio(repo)
    assert motor.listar(f"railspec:{org}:") == []
