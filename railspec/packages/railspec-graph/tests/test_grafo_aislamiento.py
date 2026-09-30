"""Filtro de workspace, referencias entre repositorios y política de código."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from grafo_fabricas import COMMIT_1, UNIDAD, Api, alcances, arista, consulta, delta, simbolo
from railspec.contracts.comun import NivelCodigo
from railspec.graph import AccesoGrafo, AlmacenGrafo, FueraDeWorkspace, RepositorioNoVisible
from railspec.graph.motor import PROPIEDADES_SIMBOLO

FUENTES = Path(__file__).resolve().parents[1] / "src" / "railspec" / "graph"


@pytest.fixture
def mundo(motor, org):
    """acme/certificados: api (primario) → reporteria (transversal); acme/otro: api con los mismos ids."""

    g = AlmacenGrafo(AccesoGrafo(motor))
    api, otro = Api(), Api()
    moneda = simbolo("reporteria", "fmt/moneda.py", "funcion", "fmt.moneda")
    api_repo, rep_repo = alcances(org, "certificados", "api", "reporteria")
    g.aplicar_delta(api_repo, COMMIT_1, api.delta(aristas=[arista(api.render, moneda)]), None)
    g.aplicar_delta(rep_repo, COMMIT_1, delta(simbolos=[moneda]), None)
    (otro_repo,) = alcances(org, "otro", "api")
    secreto = simbolo("api", "src/secreto.py", "funcion", "secreto.solo_otro")
    g.aplicar_delta(otro_repo, COMMIT_1, otro.delta(simbolos=[secreto]), None)
    return g, api, moneda


def test_visibles_de_otro_workspace_es_error(mundo, org):
    g, _, _ = mundo
    q = consulta(org, "certificados", {"verbo": "search", "texto": "secreto"})
    with pytest.raises(FueraDeWorkspace):
        g.consultar(q, alcances(org, "otro", "api"))
    with pytest.raises(FueraDeWorkspace):
        g.consultar(q, alcances("otra-org", "certificados", "api"))


def test_repositorio_pedido_no_visible_es_error(mundo, org):
    g, _, _ = mundo
    q = consulta(org, "certificados", {"verbo": "search", "texto": "x"}, repositorios=["reporteria"])
    with pytest.raises(RepositorioNoVisible):
        g.consultar(q, alcances(org, "certificados", "api"))


def test_mismos_ids_en_otro_workspace_no_se_mezclan(mundo, org):
    g, _, _ = mundo
    q = consulta(org, "certificados", {"verbo": "search", "texto": "secreto"})
    assert g.consultar(q, alcances(org, "certificados", "api", "reporteria")).resultados == []
    q_otro = consulta(org, "otro", {"verbo": "search", "texto": "secreto"})
    assert [r.ref.nombre for r in g.consultar(q_otro, alcances(org, "otro", "api")).resultados] == [
        "secreto.solo_otro"
    ]


def test_referencia_cruzada_entre_repositorios_del_workspace(mundo, org):
    g, api, moneda = mundo
    q = consulta(org, "certificados", {"verbo": "traverse", "simbolo": moneda.id, "direccion": "upstream"})
    salida = g.consultar(q, alcances(org, "certificados", "api", "reporteria"))
    por_nombre = {r.ref.nombre: r.ref for r in salida.resultados}
    assert por_nombre["pdf.render"].repositorio == "api"
    abajo = consulta(
        org, "certificados", {"verbo": "traverse", "simbolo": api.render.id, "direccion": "downstream"}
    )
    refs = {
        r.ref.nombre: r.ref.repositorio
        for r in g.consultar(abajo, alcances(org, "certificados", "api", "reporteria")).resultados
    }
    assert refs["fmt.moneda"] == "reporteria"


def test_repositorio_no_visible_corta_la_referencia_cruzada(mundo, org):
    g, api, _ = mundo
    abajo = consulta(
        org, "certificados", {"verbo": "traverse", "simbolo": api.render.id, "direccion": "downstream"}
    )
    nombres = [r.ref.nombre for r in g.consultar(abajo, alcances(org, "certificados", "api")).resultados]
    assert "fmt.moneda" not in nombres and "pdf.fuente" in nombres


def test_unidad_invalida_no_construye_grafo(motor, org):
    (repo,) = alcances(org, "certificados", "api")
    with pytest.raises(ValueError):
        AccesoGrafo(motor).espacio(repo, "../otro")


def test_el_motor_no_guarda_texto_de_codigo(mundo, motor, org):
    g, _, _ = mundo
    (repo,) = alcances(org, "certificados", "api")
    g.aplicar_delta(repo, COMMIT_1, Api().delta(), UNIDAD)
    for nombre in motor.listar(f"railspec:{org}:"):
        for s in motor.todos_simbolos(nombre):
            assert set(s) == set(PROPIEDADES_SIMBOLO)
    assert NivelCodigo.restringido  # el grafo es igual en todos los niveles: solo estructura


def test_solo_acceso_construye_nombres_de_grafo():
    """El filtro de workspace vive en un único módulo: nadie más llama a nombre_grafo ni al motor."""

    for fuente in FUENTES.glob("*.py"):
        if fuente.name in ("__init__.py", "acceso.py", "motor.py", "memoria.py", "motor_falkordb.py"):
            continue
        texto = fuente.read_text()
        assert "nombre_grafo" not in texto, fuente.name
        assert not re.search(r"\._motor\b|import[^\n]*MotorGrafo|[\"']railspec:", texto), fuente.name
