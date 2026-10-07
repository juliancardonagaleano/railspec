"""Frescura del grafo en ``graph.query``: sin índice, instante del último índice y caducidad configurable.

Corre contra el doble en memoria y, con ``RAILSPEC_FALKORDB_URL``, contra FalkorDB real. El reloj es
inyectado: nada depende de que pase tiempo de verdad.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, UNIDAD, Api, alcances, consulta, delta, simbolo, vinculo
from railspec.contracts.comun import NivelCodigo
from railspec.contracts.tools import GraphIndexEntrada, GraphQuerySalida
from railspec.graph import AccesoGrafo, AlmacenGrafo, IndexadorCanonico

T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
HORA = timedelta(hours=1)
BUSCAR = {"verbo": "search", "texto": "pdf"}


class Reloj:
    def __init__(self) -> None:
        self.ahora = T0

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, plazo: timedelta) -> None:
        self.ahora += plazo


@pytest.fixture(autouse=True)
def _sin_variables(monkeypatch):
    monkeypatch.delenv("RAILSPEC_GRAFO_FRESCURA_HORAS", raising=False)


@pytest.fixture
def reloj():
    return Reloj()


@pytest.fixture
def acceso(motor):
    return AccesoGrafo(motor)


def _consultar(grafo: AlmacenGrafo, org: str, *repos: str, **extra) -> GraphQuerySalida:
    return grafo.consultar(
        consulta(org, "certificados", BUSCAR, **extra), alcances(org, "certificados", *(repos or ("api",)))
    )


def _indexar(acceso, grafo, org, commit=COMMIT_1) -> None:
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(repo, commit, Api().delta(), None)


def test_un_repositorio_sin_indice_lo_dice_en_vez_de_devolver_vacio_en_silencio(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj)

    salida = _consultar(grafo, org)

    assert salida.resultados == [] and salida.commits == {}
    assert salida.frescura["api"].indexado is False
    assert salida.frescura["api"].commit is None and salida.frescura["api"].indexado_en is None
    (aviso,) = salida.avisos
    assert "api: sin índice canónico" in aviso and "no prueba" in aviso


def test_el_canonico_indexado_dice_su_commit_y_su_instante(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj)
    _indexar(acceso, grafo, org)

    salida = _consultar(grafo, org)

    f = salida.frescura["api"]
    assert f.indexado and f.commit == COMMIT_1 and f.indexado_en == T0 and not f.desactualizado
    assert salida.commits == {"api": COMMIT_1}  # `commits` no cambia
    assert salida.avisos == []


def test_graph_index_sella_el_instante_del_indice_aplicado(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj)
    v = vinculo(org, NivelCodigo.restringido)
    idx = IndexadorCanonico(acceso, grafo)

    def lote(commit, anterior=None):
        return GraphIndexEntrada(
            alcance=v.alcance, rama="main", commit=commit, commit_anterior=anterior, lote=1, lotes=1,
            delta=delta(simbolos=[simbolo("api", "src/pdf.py", "funcion", "pdf.render")]),
        )  # fmt: skip

    idx.recibir(lote(COMMIT_1), v)
    assert _consultar(grafo, org).frescura["api"].indexado_en == T0
    reloj.avanzar(5 * HORA)
    idx.recibir(lote(COMMIT_1), v)  # reenvío idempotente: no renueva el instante
    assert _consultar(grafo, org).frescura["api"].indexado_en == T0
    idx.recibir(lote(COMMIT_2, COMMIT_1), v)
    f = _consultar(grafo, org).frescura["api"]
    assert f.commit == COMMIT_2 and f.indexado_en == T0 + 5 * HORA


def test_pasado_el_plazo_marca_desactualizado_y_avisa(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj, frescura_horas=24)
    _indexar(acceso, grafo, org)

    reloj.avanzar(24 * HORA)  # justo en el plazo: todavía vale
    assert not _consultar(grafo, org).frescura["api"].desactualizado
    reloj.avanzar(HORA)
    salida = _consultar(grafo, org)

    assert salida.frescura["api"].desactualizado
    assert salida.frescura["api"].indexado  # sigue habiendo grafo: solo es viejo
    (aviso,) = salida.avisos
    assert "api: el índice canónico" in aviso and COMMIT_1[:12] in aviso
    assert "25 h" in aviso and "24 h" in aviso and "RAILSPEC_GRAFO_FRESCURA_HORAS" in aviso


def test_un_indice_nuevo_renueva_la_frescura(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj, frescura_horas=24)
    _indexar(acceso, grafo, org)
    reloj.avanzar(48 * HORA)
    assert _consultar(grafo, org).frescura["api"].desactualizado
    _indexar(acceso, grafo, org, COMMIT_2)
    assert not _consultar(grafo, org).frescura["api"].desactualizado


def test_el_plazo_viene_de_la_variable_de_entorno_y_cero_no_avisa(acceso, org, reloj, monkeypatch):
    _indexar(acceso, AlmacenGrafo(acceso, reloj=reloj), org)
    reloj.avanzar(100 * HORA)

    # Defecto: 72 horas.
    assert _consultar(AlmacenGrafo(acceso, reloj=reloj), org).frescura["api"].desactualizado
    monkeypatch.setenv("RAILSPEC_GRAFO_FRESCURA_HORAS", "200")
    assert not _consultar(AlmacenGrafo(acceso, reloj=reloj), org).frescura["api"].desactualizado
    monkeypatch.setenv("RAILSPEC_GRAFO_FRESCURA_HORAS", "0.5")
    assert _consultar(AlmacenGrafo(acceso, reloj=reloj), org).frescura["api"].desactualizado
    monkeypatch.setenv("RAILSPEC_GRAFO_FRESCURA_HORAS", "0")
    sin_aviso = _consultar(AlmacenGrafo(acceso, reloj=reloj), org)
    assert not sin_aviso.frescura["api"].desactualizado and sin_aviso.avisos == []
    # El argumento explícito gana a la variable.
    assert _consultar(AlmacenGrafo(acceso, reloj=reloj, frescura_horas=1), org).frescura["api"].desactualizado


@pytest.mark.parametrize("valor", ["-1", "una semana", "inf", "nan"])
def test_una_variable_invalida_impide_construir_el_almacen(acceso, monkeypatch, valor):
    monkeypatch.setenv("RAILSPEC_GRAFO_FRESCURA_HORAS", valor)
    with pytest.raises(ValueError, match="RAILSPEC_GRAFO_FRESCURA_HORAS"):
        AlmacenGrafo(acceso)


def test_un_canonico_sin_instante_guardado_no_se_marca(acceso, org, reloj):
    """Un índice anterior a que el servidor guardara el instante: no hay con qué comparar."""

    grafo = AlmacenGrafo(acceso, reloj=reloj, frescura_horas=1)
    _indexar(acceso, grafo, org)
    (repo,) = alcances(org, "certificados", "api")
    canon = acceso.espacio(repo)
    meta = canon.meta()
    meta.actualizado = None
    canon.fijar_meta(meta)
    reloj.avanzar(1000 * HORA)

    f = _consultar(grafo, org).frescura["api"]
    assert f.indexado and f.indexado_en is None and not f.desactualizado


def test_varios_repositorios_se_describen_cada_uno(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj, frescura_horas=24)
    _indexar(acceso, grafo, org)
    reloj.avanzar(30 * HORA)
    (web,) = alcances(org, "certificados", "web")
    grafo.aplicar_delta(web, COMMIT_2, delta(simbolos=[simbolo("web", "src/pdf.js", "funcion", "pdf")]), None)

    salida = _consultar(grafo, org, "api", "web", "nuevo")  # `nuevo` no se indexó nunca

    assert {k: (v.indexado, v.desactualizado) for k, v in salida.frescura.items()} == {
        "api": (True, True),
        "nuevo": (False, False),
        "web": (True, False),
    }
    assert salida.commits == {"api": COMMIT_1, "web": COMMIT_2}
    assert len(salida.avisos) == 2


def test_la_superposicion_de_una_unidad_sin_canonico_se_ve_pero_se_avisa(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj)
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(
        repo, COMMIT_1, delta(simbolos=[simbolo("api", "src/pdf.py", "funcion", "pdf.x")]), UNIDAD
    )

    salida = _consultar(grafo, org, unidad=UNIDAD)

    assert [r.ref.nombre for r in salida.resultados] == ["pdf.x"]
    assert salida.frescura["api"].indexado is False
    assert "solo se ve la superposición" in salida.avisos[0]


def test_la_frescura_describe_el_canonico_aunque_se_consulte_con_unidad(acceso, org, reloj):
    grafo = AlmacenGrafo(acceso, reloj=reloj)
    _indexar(acceso, grafo, org)
    (repo,) = alcances(org, "certificados", "api")
    grafo.aplicar_delta(
        repo, COMMIT_2, delta(simbolos=[simbolo("api", "src/pdf.py", "funcion", "pdf.x")]), UNIDAD
    )

    salida = _consultar(grafo, org, unidad=UNIDAD)

    assert salida.commits == {"api": COMMIT_2}  # el de la superposición, como hasta ahora
    assert salida.frescura["api"].commit == COMMIT_1  # el del canónico, que es el que indexa CI
