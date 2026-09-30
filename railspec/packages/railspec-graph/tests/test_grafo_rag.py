"""Vectores, búsqueda semántica, RAG por referencias e ingesta de snapshots."""

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
    consulta,
    delta,
    embedding,
    simbolo,
    vector,
    vinculo,
)
from railspec.contracts.comun import AlcanceUnidad, NivelCodigo
from railspec.contracts.snapshot import EscaneoSecretos, ModoDelta, Snapshot
from railspec.graph import (
    AccesoGrafo,
    AlmacenGrafo,
    AlmacenVectores,
    RecuperadorContexto,
    SnapshotRechazado,
    ingerir_snapshot,
)
from railspec.graph.almacen import decodificar


@pytest.fixture
def piezas(motor, org):
    acceso = AccesoGrafo(motor)
    g = AlmacenGrafo(acceso, codificador=_Codificador())
    api = Api()
    (repo,) = alcances(org, "certificados", "api")
    g.aplicar_delta(repo, COMMIT_1, api.delta(), None)
    return g, AlmacenVectores(acceso), api, repo


class _Codificador:
    """Doble del embebedor de consultas: 'pdf' apunta al eje de pdf.render (2)."""

    def codificar(self, texto: str) -> str:
        return vector(2 if "pdf" in texto else 700)


def test_decodificar_int8_con_signo():
    import base64

    assert decodificar(base64.b64encode(bytes([127, 129, 0])).decode()) == [1.0, -1.0, 0.0]


def test_buscar_ordena_por_similitud_y_no_cruza_repositorios(piezas, org):
    _, vec, api, repo = piezas
    hits = vec.buscar(repo, vector(1, ruido=10), 2)
    assert hits[0][0] == api.emitir.id and hits[0][1] > 0.95
    (otro,) = alcances(org, "certificados", "reporteria")
    assert vec.buscar(otro, vector(1), 2) == []


def test_upsert_exige_el_commit_del_grafo(piezas):
    _, vec, api, repo = piezas
    with pytest.raises(ValueError):
        vec.upsert(repo, COMMIT_2, [embedding(api.main, 9)])
    vec.upsert(repo, COMMIT_1, [embedding(api.main, 9)])
    assert vec.buscar(repo, vector(9), 1)[0][0] == api.main.id


def test_search_semantico_con_codificador(piezas, org):
    g, _, _, repo = piezas
    q = consulta(org, "certificados", {"verbo": "search", "texto": "generar pdf", "semantica": True})
    salida = g.consultar(q, [repo])
    assert salida.resultados[0].ref.nombre == "pdf.render"


def test_search_semantico_con_vector_del_proxy_sin_codificador(motor, org):
    g = AlmacenGrafo(AccesoGrafo(motor))
    api = Api()
    (repo,) = alcances(org, "certificados", "api")
    g.aplicar_delta(repo, COMMIT_1, api.delta(), None)
    cuerpo = {
        "verbo": "search",
        "texto": "zzz",
        "semantica": True,
        "vector_b64": vector(3),
        "modelo_embedding": "nomic-embed-code",
    }
    salida = g.consultar(consulta(org, "certificados", cuerpo), [repo])
    assert salida.resultados[0].ref.nombre == "pdf.fuente"


def test_rag_devuelve_referencias_y_expande_por_el_grafo(piezas, org):
    g, _, api, repo = piezas
    rag = RecuperadorContexto(g)
    ws = consulta(org, "certificados", {"verbo": "resolve", "nombre": "x"}).alcance
    resultados = rag.recuperar(ws, [repo], vector(2), k=1)
    assert resultados[0].ref.nombre == "pdf.render" and resultados[0].distancia == 0
    vecinos = {r.ref.nombre for r in resultados[1:]}
    assert vecinos == {"servicio.emitir", "pdf.fuente"}
    assert all(r.ref.tipo == "simbolo" for r in resultados)


def test_rag_con_unidad_ve_la_superposicion(piezas, org):
    g, _, _, repo = piezas
    nuevo = simbolo("api", "src/firma.py", "funcion", "firma.aplicar")
    g.aplicar_delta(repo, COMMIT_1, delta(simbolos=[nuevo], embeddings=[embedding(nuevo, 300)]), UNIDAD)
    rag = RecuperadorContexto(g)
    ws = consulta(org, "certificados", {"verbo": "resolve", "nombre": "x"}).alcance
    assert rag.recuperar(ws, [repo], vector(300), k=1)[0].ref.nombre != "firma.aplicar"
    assert rag.recuperar(ws, [repo], vector(300), k=1, unidad=UNIDAD)[0].ref.nombre == "firma.aplicar"


# --- ingesta -----------------------------------------------------------------


def _snapshot(org: str, nivel: NivelCodigo, d=None, repo="api", workspace="certificados") -> Snapshot:
    return Snapshot(
        id=UUID("00000000-0000-4000-8000-000000000001"),
        unidad=AlcanceUnidad(org=org, workspace=workspace, unidad=UNIDAD),
        repositorio=repo,
        base_commit=COMMIT_1,
        hash_arbol="a" * 40,
        creado_en=T0,
        nivel_codigo=nivel,
        modo_delta=ModoDelta.completo if d is not None else ModoDelta.solo_hashes,
        archivos=[],
        delta_indice=d,
        escaneo_secretos=EscaneoSecretos(herramienta="gitleaks", version="8.0.0", hallazgos=0),
    )


def test_ingesta_aplica_a_la_superposicion_y_respeta_exclusiones(piezas, org):
    g, _, _, repo = piezas
    visible = simbolo("api", "src/firma.py", "funcion", "firma.aplicar")
    oculto = simbolo("api", "vendor/lib.py", "funcion", "lib.oculta")
    snap = _snapshot(org, NivelCodigo.restringido, delta(simbolos=[visible, oculto]))
    assert ingerir_snapshot(snap, vinculo(org, NivelCodigo.restringido, ["vendor/"]), g)
    q = consulta(org, "certificados", {"verbo": "search", "texto": "a"}, unidad=UNIDAD, limite=50)
    nombres = [r.ref.nombre for r in g.consultar(q, [repo]).resultados]
    assert "firma.aplicar" in nombres and "lib.oculta" not in nombres


def test_ingesta_solo_hashes_no_toca_el_grafo(piezas, org):
    g, _, _, _ = piezas
    assert not ingerir_snapshot(
        _snapshot(org, NivelCodigo.restringido), vinculo(org, NivelCodigo.restringido), g
    )


def test_ingesta_rechaza_nivel_mas_abierto_que_elvinculo(piezas, org):
    g, _, _, _ = piezas
    snap = _snapshot(org, NivelCodigo.interno, delta())
    with pytest.raises(SnapshotRechazado):
        ingerir_snapshot(snap, vinculo(org, NivelCodigo.restringido), g)


def test_ingesta_rechaza_otro_repositorio_o_workspace(piezas, org):
    g, _, _, _ = piezas
    vinc = vinculo(org, NivelCodigo.restringido)
    with pytest.raises(SnapshotRechazado):
        ingerir_snapshot(_snapshot(org, NivelCodigo.restringido, delta(), repo="otro"), vinc, g)
    with pytest.raises(SnapshotRechazado):
        ingerir_snapshot(_snapshot(org, NivelCodigo.restringido, delta(), workspace="otro"), vinc, g)
