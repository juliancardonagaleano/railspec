"""Fase 5: comparación base contra snapshot (impacto) y trazas ``CA-NN``."""

from __future__ import annotations

from uuid import UUID

import pytest
from grafo_fabricas import (
    COMMIT_1,
    COMMIT_2,
    T0,
    UNIDAD,
    Api,
    alcances,
    arista,
    consulta,
    delta,
    simbolo,
    vinculo,
)
from railspec.contracts import tools
from railspec.contracts.comun import AlcanceUnidad, NivelCodigo
from railspec.contracts.snapshot import EscaneoSecretos, ModoDelta, Snapshot
from railspec.graph import AccesoGrafo, AlmacenGrafo, Traza, ingerir_snapshot

#: Los verbos ``impact`` y ``trace`` llegan con el contrato 1.4.
contrato_14 = pytest.mark.skipif(
    not hasattr(tools, "ConsultaImpact"), reason="el contrato instalado no trae impact/trace (1.4)"
)


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


def _snapshot(org, d, n=1) -> Snapshot:
    return Snapshot(
        id=UUID(f"00000000-0000-4000-8000-{n:012d}"),
        unidad=AlcanceUnidad(org=org, workspace="certificados", unidad=UNIDAD),
        repositorio="api",
        base_commit=COMMIT_1,
        hash_arbol="a" * 40,
        creado_en=T0,
        nivel_codigo=NivelCodigo.restringido,
        modo_delta=ModoDelta.completo,
        archivos=[],
        delta_indice=d,
        escaneo_secretos=EscaneoSecretos(herramienta="gitleaks", version="8.0.0", hallazgos=0),
    )


def _cambiado(s):
    return s.model_copy(update={"sha256": "f" * 64})


# --- impacto -------------------------------------------------------------------------


def test_impacto_incluye_tocados_borrados_procesos_y_riesgo(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(
        repo, COMMIT_1, delta(simbolos=[_cambiado(api.render)], borrados=[api.fuente]), UNIDAD
    )
    i = grafo.impacto(_q(org, {"verbo": "resolve", "nombre": "x"}, unidad=UNIDAD), [repo])
    assert i.tocados == sorted([api.render.id, api.fuente.id])
    # El borrado se resuelve contra el canónico: la superposición lo oculta.
    assert {r.nombre for r in i.refs_tocados} == {"pdf.render", "pdf.fuente"}
    assert {r.ref.nombre for r in i.afectados} == {"servicio.emitir", "app.main", "test_servicio.test_emitir"}
    assert [r.distancia for r in i.afectados] == sorted(r.distancia for r in i.afectados)
    assert i.procesos and i.riesgo == "bajo"
    assert {r.riesgo for r in i.afectados} == {"bajo"}


def test_impacto_sin_superposicion_esta_vacio(grafo, org):
    (repo,) = alcances(org, "certificados", "api")
    i = grafo.impacto(_q(org, {"verbo": "resolve", "nombre": "x"}, unidad=UNIDAD), [repo])
    assert i.tocados == [] and i.afectados == [] and i.riesgo == "bajo"


def test_impacto_exige_unidad(grafo, org):
    with pytest.raises(ValueError):
        grafo.impacto(_q(org, {"verbo": "resolve", "nombre": "x"}), alcances(org, "certificados", "api"))


def test_impacto_superposicion_conserva_su_forma(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(repo, COMMIT_1, delta(simbolos=[_cambiado(api.render)]), UNIDAD)
    tocados, afectados = grafo.impacto_superposicion(
        _q(org, {"verbo": "resolve", "nombre": "x"}, unidad=UNIDAD), [repo]
    )
    assert tocados == [api.render.id] and len(afectados) == 3


@contrato_14
def test_verbo_impact_devuelve_tocados_a_distancia_cero(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    sup = delta(simbolos=[_cambiado(api.render), nuevo], aristas=[arista(api.render, nuevo)])
    grafo.aplicar_delta(repo, COMMIT_1, sup, UNIDAD)
    salida = grafo.consultar(_q(org, {"verbo": "impact", "profundidad": 1}, unidad=UNIDAD), [repo])
    por_distancia = {}
    for r in salida.resultados:
        por_distancia.setdefault(r.distancia, set()).add(r.ref.nombre)
    assert por_distancia == {0: {"pdf.render", "pdf.marca_agua"}, 1: {"servicio.emitir"}}
    assert {r.riesgo for r in salida.resultados} == {"bajo"}


# --- trazas CA-NN --------------------------------------------------------------------


def test_ingesta_enlaza_criterios_solo_con_lo_que_cambia_el_reporte(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    vinc = vinculo(org, NivelCodigo.restringido)
    render = _cambiado(api.render)
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    assert ingerir_snapshot(_snapshot(org, delta(simbolos=[render]), 1), vinc, grafo, ["CA-01"])
    # El segundo snapshot es base..árbol: vuelve a traer render igual; solo lo nuevo es del reporte.
    d2 = delta(simbolos=[render, nuevo], borrados=[api.fuente])
    assert ingerir_snapshot(_snapshot(org, d2, 2), vinc, grafo, ["CA-02", "CA-02"])
    assert grafo.trazas(repo, UNIDAD) == [
        Traza(UNIDAD, "CA-01", api.render.id),
        *sorted(
            [Traza(UNIDAD, "CA-02", api.fuente.id), Traza(UNIDAD, "CA-02", nuevo.id)],
            key=lambda t: t.simbolo,
        ),
    ]


def test_ingesta_sin_criterios_no_traza(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    ingerir_snapshot(
        _snapshot(org, delta(simbolos=[_cambiado(api.render)])), vinculo(org, NivelCodigo.restringido), grafo
    )
    assert grafo.trazas(repo) == []


def test_trazas_respetan_exclusiones_del_vinculo(grafo, org):
    (repo,) = alcances(org, "certificados", "api")
    oculto = simbolo("api", "vendor/lib.py", "funcion", "lib.oculta")
    vinc = vinculo(org, NivelCodigo.restringido, ["vendor/"])
    ingerir_snapshot(_snapshot(org, delta(simbolos=[oculto])), vinc, grafo, ["CA-01"])
    assert grafo.trazas(repo) == []


def test_trazas_sobreviven_a_descartar_la_superposicion_y_se_borran_con_el_repo(grafo, motor, org, api):
    (repo,) = alcances(org, "certificados", "api")
    ingerir_snapshot(
        _snapshot(org, delta(simbolos=[_cambiado(api.render)])),
        vinculo(org, NivelCodigo.restringido),
        grafo,
        ["CA-01"],
    )
    grafo.descartar_superposicion(repo, UNIDAD, COMMIT_2)
    assert grafo.trazas(repo, UNIDAD) == [Traza(UNIDAD, "CA-01", api.render.id)]
    grafo.borrar_repositorio(repo)
    assert motor.listar(f"railspec:{org}:") == []


@contrato_14
def test_verbo_trace_por_criterio_y_por_simbolo(grafo, org, api):
    (repo,) = alcances(org, "certificados", "api")
    nuevo = simbolo("api", "src/pdf.py", "funcion", "pdf.marca_agua")
    vinc = vinculo(org, NivelCodigo.restringido)
    ingerir_snapshot(_snapshot(org, delta(simbolos=[_cambiado(api.render), nuevo])), vinc, grafo, ["CA-01"])

    por_criterio = grafo.consultar(_q(org, {"verbo": "trace", "criterio": "CA-01"}, unidad=UNIDAD), [repo])
    assert [r.ref.nombre for r in por_criterio.resultados] == ["pdf.marca_agua", "pdf.render"]

    por_simbolo = grafo.consultar(_q(org, {"verbo": "trace", "simbolo": api.render.id}), [repo])
    assert [(r.ref.tipo, r.ref.unidad, r.ref.criterio) for r in por_simbolo.resultados] == [
        ("criterio", UNIDAD, "CA-01")
    ]

    # Integrada la unidad, el criterio sigue apuntando a lo que ya está en el canónico.
    grafo.descartar_superposicion(repo, UNIDAD, COMMIT_2)
    tras = grafo.consultar(_q(org, {"verbo": "trace", "criterio": "CA-01"}, unidad=UNIDAD), [repo])
    assert [r.ref.nombre for r in tras.resultados] == ["pdf.render"]


def test_el_recorrido_pide_las_aristas_por_nivel_y_no_por_simbolo(motor, org, api):
    """Con un motor remoto cada ``aristas`` es un viaje de red: el BFS no puede hacer uno por nodo."""

    from railspec.graph.almacen import recorrer

    g = AlmacenGrafo(AccesoGrafo(motor))
    (repo,) = alcances(org, "certificados", "api")
    g.aplicar_delta(repo, COMMIT_1, api.delta(), None)
    vistas = g.vistas(_q(org, {"verbo": "search", "texto": "pdf"}), [repo])

    pedidos: list[list[str]] = []
    original = type(vistas[0]).aristas

    def contar(self, ids, relaciones, direccion):
        pedidos.append(list(ids))
        return original(self, ids, relaciones, direccion)

    type(vistas[0]).aristas = contar
    try:
        alcanzados = recorrer(vistas, [api.fuente.id], ["llama"], "upstream", 3)
    finally:
        type(vistas[0]).aristas = original

    assert {i: a.distancia for i, a in alcanzados.items()} == {
        api.render.id: 1,
        api.emitir.id: 2,
        api.main.id: 3,
        api.prueba.id: 3,
    }
    assert pedidos == [[api.fuente.id], [api.render.id], [api.emitir.id]]
    # Varios inicios: una sola consulta para todo el nivel.
    pedidos.clear()
    type(vistas[0]).aristas = contar
    try:
        recorrer(vistas, [api.fuente.id, api.render.id], ["llama"], "upstream", 1)
    finally:
        type(vistas[0]).aristas = original
    assert len(pedidos) == 1 and sorted(pedidos[0]) == sorted([api.fuente.id, api.render.id])
