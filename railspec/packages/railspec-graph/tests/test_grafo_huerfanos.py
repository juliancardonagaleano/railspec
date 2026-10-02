"""Lo abandonado se borra: superposiciones que nadie retomó y preparaciones de índices que no completan.

Corre contra el doble en memoria y, con ``RAILSPEC_FALKORDB_URL``, contra FalkorDB real. El reloj es
inyectado: ningún barrido depende de que pase tiempo de verdad.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, UNIDAD, Api, delta, simbolo, vinculo
from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.tools import GraphIndexEntrada
from railspec.graph import AccesoGrafo, AlmacenGrafo, IndexadorCanonico
from railspec.graph.motor import Meta

COMMIT_3 = "3" * 40
OTRA_UNIDAD = "0002-revocar-pdf"
DIA = timedelta(days=1)
HORA = timedelta(hours=1)
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
#: Los plazos por defecto del indexador (30 días, 24 horas), para barrer a mano.
SUPERPOSICION = 30 * DIA
INDEXADO = 24 * HORA


class Reloj:
    def __init__(self) -> None:
        self.ahora = T0

    def __call__(self) -> datetime:
        return self.ahora

    def avanzar(self, plazo: timedelta) -> None:
        self.ahora += plazo


@dataclass
class Piezas:
    reloj: Reloj
    acceso: AccesoGrafo
    grafo: AlmacenGrafo
    idx: IndexadorCanonico
    v: VinculoRepositorio

    @property
    def alcance(self) -> AlcanceRepositorio:
        return self.v.alcance

    def reportar(self, unidad: str = UNIDAD, nombre: str = "pdf.marca_agua") -> None:
        """Un snapshot de ``unit.report``: reconstruye la superposición de la unidad."""

        s = simbolo("api", "src/marca.py", "funcion", nombre)
        self.grafo.aplicar_delta(self.alcance, COMMIT_1, delta(simbolos=[s]), unidad)

    def barrer(self):
        return self.grafo.limpiar_huerfanos(self.alcance, SUPERPOSICION, INDEXADO)

    def unidades(self) -> list[str]:
        return sorted(self.acceso.superposiciones(self.alcance))

    def preparaciones(self) -> list[str]:
        return sorted(self.acceso.indexados(self.alcance))

    def lote(self, commit: str, lote: int, lotes: int, d=None):
        entrada = GraphIndexEntrada(
            alcance=self.alcance,
            rama="main",
            commit=commit,
            lote=lote,
            lotes=lotes,
            delta=d or delta(simbolos=[simbolo("api", f"src/l{lote}.py", "funcion", f"l{lote}")]),
        )
        return self.idx.recibir(entrada, self.v)


@pytest.fixture(autouse=True)
def _sin_variables(monkeypatch):
    monkeypatch.delenv("RAILSPEC_GRAFO_SUPERPOSICION_DIAS", raising=False)
    monkeypatch.delenv("RAILSPEC_GRAFO_INDEXADO_HORAS", raising=False)


@pytest.fixture
def p(motor, org):
    reloj = Reloj()
    acceso = AccesoGrafo(motor)
    grafo = AlmacenGrafo(acceso, reloj=reloj)
    return Piezas(
        reloj, acceso, grafo, IndexadorCanonico(acceso, grafo), vinculo(org, NivelCodigo.restringido)
    )


# --- superposiciones ----------------------------------------------------------------------


def test_la_superposicion_abandonada_se_borra_y_la_reciente_no(p):
    p.reportar(UNIDAD)
    p.reloj.avanzar(20 * DIA)
    p.reportar(OTRA_UNIDAD, "pdf.revocar")
    p.reloj.avanzar(10 * DIA)  # UNIDAD lleva 30 días sin actividad, OTRA_UNIDAD 10

    borrado = p.barrer()
    assert borrado.superposiciones == [UNIDAD]
    assert borrado.indexados == []
    assert p.unidades() == [OTRA_UNIDAD]


def test_un_dia_antes_del_plazo_todavia_no(p):
    p.reportar()
    p.reloj.avanzar(SUPERPOSICION - HORA)
    assert not p.barrer()
    p.reloj.avanzar(HORA)
    assert p.barrer().superposiciones == [UNIDAD]


def test_un_snapshot_nuevo_renueva_la_actividad(p):
    p.reportar()
    p.reloj.avanzar(20 * DIA)
    p.reportar()  # la unidad sigue viva: reconstruye su superposición
    p.reloj.avanzar(20 * DIA)
    assert not p.barrer()  # 20 días desde el último snapshot, 40 desde el primero
    p.reloj.avanzar(10 * DIA)
    assert p.barrer().superposiciones == [UNIDAD]


def test_la_retenida_no_caduca_por_tiempo_sino_por_cobertura(p):
    p.reportar()
    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_2) is True
    p.reloj.avanzar(365 * DIA)

    assert not p.barrer()
    assert p.unidades() == [UNIDAD]
    # Sale cuando un índice cubre su commit, y no antes.
    assert p.grafo.retirar_superposiciones(p.alcance, COMMIT_2, False, None) == [UNIDAD]
    assert p.unidades() == []


def test_el_barrido_es_idempotente(p):
    p.reportar()
    p.reloj.avanzar(31 * DIA)
    assert p.barrer().superposiciones == [UNIDAD]
    assert not p.barrer()
    assert not p.barrer()
    assert p.unidades() == []


# --- preparaciones de graph.index ---------------------------------------------------------


def test_la_preparacion_de_un_indice_que_no_completa_caduca(p):
    p.lote(COMMIT_1, 1, 3)
    p.reloj.avanzar(23 * HORA)
    assert p.lote(COMMIT_1, 2, 3).lotes_recibidos == 2  # un lote nuevo renueva
    p.reloj.avanzar(2 * HORA)
    assert not p.barrer()  # 2 horas desde el último lote nuevo
    assert p.preparaciones() == [COMMIT_1]

    p.reloj.avanzar(15 * HORA)  # 17 horas desde el último lote
    assert p.lote(COMMIT_1, 2, 3).lotes_recibidos == 2  # un lote repetido no cuenta como actividad
    p.reloj.avanzar(6 * HORA)  # 23 horas
    assert not p.barrer()
    p.reloj.avanzar(HORA)  # 24 horas
    assert p.barrer().indexados == [COMMIT_1]
    assert p.preparaciones() == []
    assert p.acceso.espacio(p.alcance).meta().commit is None  # el canónico nunca avanzó


def test_el_primer_lote_de_un_commit_nuevo_barre_la_preparacion_de_uno_que_no_completo(p):
    """Un repositorio cuyo índice nunca completa no llega al barrido de «índice aplicado»."""

    p.lote(COMMIT_1, 1, 2)
    p.reloj.avanzar(25 * HORA)
    assert p.lote(COMMIT_2, 1, 2).aplicado is False
    assert p.preparaciones() == [COMMIT_2]


def test_un_lote_que_no_es_el_primero_de_su_commit_no_barre(p):
    """El barrido cuesta listar los grafos del repositorio: no se paga en cada lote."""

    p.lote(COMMIT_1, 1, 3)
    p.reloj.avanzar(HORA)
    p.lote(COMMIT_2, 1, 3)
    p.reloj.avanzar(25 * HORA)
    p.lote(COMMIT_2, 2, 3)  # COMMIT_1 lleva 26 horas abandonado y sigue ahí
    assert p.preparaciones() == [COMMIT_1, COMMIT_2]
    assert p.barrer().indexados == [COMMIT_1]


# --- sin sello ---------------------------------------------------------------------------


def test_lo_que_no_tiene_sello_se_sella_en_el_primer_barrido_y_caduca_despues(p):
    """Una superposición y una preparación de antes de esta versión: nadie sabe cuánto llevan ahí."""

    sup = p.acceso.espacio(p.alcance, UNIDAD)
    sup.upsert_simbolos([_props(simbolo("api", "src/a.py", "funcion", "a"))])
    sup.fijar_meta(Meta(commit=COMMIT_1, unidad=UNIDAD, borrados=["x" * 64]))
    prep = p.acceso.espacio_indexado(p.alcance, COMMIT_1)
    prep.fijar_meta(Meta(commit=COMMIT_1, base="completo", lotes=2, recibidos=[1]))
    assert sup.meta().actualizado is None and prep.meta().actualizado is None

    p.reloj.avanzar(100 * DIA)
    sellado = p.reloj.ahora
    assert not p.barrer()  # no se borra por un tiempo que no se vio pasar
    assert sup.meta().actualizado == sellado
    assert prep.meta().actualizado == sellado
    # Sellar no toca el resto de la meta.
    assert (sup.meta().commit, sup.meta().unidad, sup.meta().borrados) == (COMMIT_1, UNIDAD, ["x" * 64])
    assert (prep.meta().lotes, prep.meta().recibidos) == (2, [1])

    p.reloj.avanzar(23 * HORA)
    assert not p.barrer()
    assert sup.meta().actualizado == sellado  # el segundo barrido no mueve el sello
    p.reloj.avanzar(HORA)
    assert p.barrer().indexados == [COMMIT_1]  # 24 horas desde el sello
    p.reloj.avanzar(29 * DIA)
    assert p.barrer().superposiciones == [UNIDAD]  # 30 días


def _props(s) -> dict:
    return {
        "id": s.id,
        "nombre": s.nombre,
        "tipo": s.tipo.value,
        "ruta": s.ruta,
        "linea_inicio": s.linea_inicio,
        "linea_fin": s.linea_fin,
        "sha256": s.sha256,
    }


# --- IndexadorCanonico --------------------------------------------------------------------


def test_un_indice_aplicado_barre_lo_abandonado_sin_cambiar_su_respuesta(p, caplog):
    p.reportar(UNIDAD)
    p.reportar(OTRA_UNIDAD, "pdf.revocar")
    p.grafo.enlazar_criterios(
        p.alcance, UNIDAD, ["CA-01"], [simbolo("api", "src/marca.py", "funcion", "x").id]
    )
    p.lote(COMMIT_3, 1, 2)  # un índice que nunca llegó a completar
    p.reloj.avanzar(20 * DIA)
    p.reportar(OTRA_UNIDAD, "pdf.revocar")  # esta sí sigue viva
    p.reloj.avanzar(11 * DIA)

    with caplog.at_level(logging.INFO, logger="railspec.graph.indexado"):
        salida = p.lote(COMMIT_1, 1, 1, Api().delta())

    assert (salida.commit, salida.lotes_recibidos, salida.aplicado) == (COMMIT_1, 1, True)
    assert p.unidades() == [OTRA_UNIDAD]
    assert p.preparaciones() == []
    assert p.acceso.espacio(p.alcance).meta().commit == COMMIT_1
    assert p.grafo.trazas(p.alcance)  # las trazas CA-NN sobreviven
    registro = "\n".join(r.getMessage() for r in caplog.records)
    assert UNIDAD in registro and COMMIT_3 in registro and OTRA_UNIDAD not in registro


def test_el_reenvio_de_un_commit_ya_aplicado_tambien_barre(p):
    p.lote(COMMIT_1, 1, 1, Api().delta())
    p.reportar()
    p.reloj.avanzar(31 * DIA)
    assert p.lote(COMMIT_1, 1, 1, Api().delta()).aplicado
    assert p.unidades() == []


def test_solo_barre_el_repositorio_del_indice(p, org):
    otro = AlcanceRepositorio(org=org, workspace="certificados", repositorio="web")
    p.acceso.espacio(otro, UNIDAD).fijar_meta(Meta(commit=COMMIT_1, unidad=UNIDAD, actualizado=T0))
    p.acceso.espacio_indexado(otro, COMMIT_1).fijar_meta(Meta(commit=COMMIT_1, actualizado=T0))
    p.reportar()
    p.reloj.avanzar(31 * DIA)

    assert p.barrer().superposiciones == [UNIDAD]
    assert p.acceso.superposiciones(otro) == [UNIDAD]
    assert p.acceso.indexados(otro) == [COMMIT_1]


def test_cero_desactiva_cada_lado(p):
    sin_nada = IndexadorCanonico(p.acceso, p.grafo, superposicion_dias=0, indexado_horas=0)
    p.reportar()
    p.lote(COMMIT_3, 1, 2)
    p.reloj.avanzar(400 * DIA)
    p.idx = sin_nada
    p.lote(COMMIT_1, 1, 1, Api().delta())
    assert p.unidades() == [UNIDAD]
    assert p.preparaciones() == [COMMIT_3]

    solo_preparacion = IndexadorCanonico(p.acceso, p.grafo, superposicion_dias=0)
    p.idx = solo_preparacion
    p.lote(COMMIT_2, 1, 1, Api().delta())
    assert p.unidades() == [UNIDAD]
    assert p.preparaciones() == []

    assert not p.grafo.limpiar_huerfanos(p.alcance, None, None)


def test_las_variables_de_entorno_fijan_los_plazos(p, monkeypatch):
    monkeypatch.setenv("RAILSPEC_GRAFO_SUPERPOSICION_DIAS", "2")
    monkeypatch.setenv("RAILSPEC_GRAFO_INDEXADO_HORAS", "0.5")
    p.idx = IndexadorCanonico(p.acceso, p.grafo)
    p.reportar()
    p.lote(COMMIT_3, 1, 2)

    p.reloj.avanzar(29 * timedelta(minutes=1))
    p.lote(COMMIT_1, 1, 1, Api().delta())
    assert (p.unidades(), p.preparaciones()) == ([UNIDAD], [COMMIT_3])

    p.reloj.avanzar(timedelta(minutes=1))  # media hora para la preparación
    p.lote(COMMIT_2, 1, 1, Api().delta())
    assert (p.unidades(), p.preparaciones()) == ([UNIDAD], [])

    p.reloj.avanzar(2 * DIA)
    p.lote(COMMIT_2, 1, 1, Api().delta())
    assert p.unidades() == []


@pytest.mark.parametrize("valor", ["abc", "-1", "nan", "inf", "1e999", "1e12"])
def test_una_variable_invalida_impide_construir_el_indexador(p, monkeypatch, valor):
    monkeypatch.setenv("RAILSPEC_GRAFO_SUPERPOSICION_DIAS", valor)
    with pytest.raises(ValueError, match="RAILSPEC_GRAFO_SUPERPOSICION_DIAS"):
        IndexadorCanonico(p.acceso, p.grafo)
    monkeypatch.delenv("RAILSPEC_GRAFO_SUPERPOSICION_DIAS")
    monkeypatch.setenv("RAILSPEC_GRAFO_INDEXADO_HORAS", valor)
    with pytest.raises(ValueError, match="RAILSPEC_GRAFO_INDEXADO_HORAS"):
        IndexadorCanonico(p.acceso, p.grafo)


def test_una_variable_vacia_vale_el_defecto(p, monkeypatch):
    monkeypatch.setenv("RAILSPEC_GRAFO_SUPERPOSICION_DIAS", "")
    p.idx = IndexadorCanonico(p.acceso, p.grafo)
    p.reportar()
    p.reloj.avanzar(31 * DIA)
    p.lote(COMMIT_1, 1, 1, Api().delta())
    assert p.unidades() == []


def test_un_fallo_del_barrido_no_falla_el_indice(p, monkeypatch, caplog):
    def roto(*_a, **_k):
        raise RuntimeError("FalkorDB se cayó")

    monkeypatch.setattr(p.grafo, "limpiar_huerfanos", roto)
    with caplog.at_level(logging.ERROR, logger="railspec.graph.indexado"):
        salida = p.lote(COMMIT_1, 1, 1, Api().delta())

    assert salida.aplicado is True
    assert p.acceso.espacio(p.alcance).meta().commit == COMMIT_1
    assert any("no se pudo limpiar" in r.getMessage() for r in caplog.records)


# --- motor: sellar y la meta ---------------------------------------------------------------


def test_sellar_no_crea_el_grafo_ni_pisa_un_sello_ni_toca_el_resto(p):
    e = p.acceso.espacio(p.alcance, UNIDAD)
    e.sellar(T0)
    assert not e.existe()

    e.upsert_simbolos([_props(simbolo("api", "src/a.py", "funcion", "a"))])
    e.fijar_meta(Meta(commit=COMMIT_1, unidad=UNIDAD, borrados=["b"]))
    e.sellar(T0)
    e.sellar(T0 + DIA)
    meta = e.meta()
    assert meta.actualizado == T0
    assert (meta.commit, meta.unidad, meta.borrados) == (COMMIT_1, UNIDAD, ["b"])

    e.fijar_meta(Meta(commit=COMMIT_2, actualizado=T0 + 2 * DIA))
    assert (e.meta().commit, e.meta().actualizado) == (COMMIT_2, T0 + 2 * DIA)
    e.fijar_meta(Meta(commit=COMMIT_2))  # escribir sin sello lo quita
    assert e.meta().actualizado is None


def test_sellar_un_grafo_sin_meta_deja_que_la_primera_escritura_la_complete(p):
    """El barrido puede llegar entre el primer upsert de un lote y la escritura de su meta."""

    e = p.acceso.espacio_indexado(p.alcance, COMMIT_1)
    e.upsert_simbolos([_props(simbolo("api", "src/a.py", "funcion", "a"))])
    e.sellar(T0)
    assert (e.meta().actualizado, e.meta().commit, e.meta().lotes) == (T0, None, None)

    e.fijar_meta(Meta(commit=COMMIT_1, base="completo", lotes=2, recibidos=[1], actualizado=T0 + HORA))
    assert (e.meta().commit, e.meta().lotes, e.meta().actualizado) == (COMMIT_1, 2, T0 + HORA)


def test_el_json_de_la_meta_no_gana_claves_para_las_replicas_anteriores(p, motor):
    """Una réplica sin esta versión lee la meta con ``Meta(**json)``: una clave nueva la tumbaría."""

    if not hasattr(motor, "_leer"):
        pytest.skip("solo FalkorDB guarda la meta como json")
    p.reportar()
    nombre = p.acceso.espacio(p.alcance, UNIDAD)._grafo
    (fila,) = motor._leer(nombre, "MATCH (m:Meta) RETURN m.json, m.actualizado")
    assert "actualizado" not in json.loads(fila[0])
    assert fila[1] == T0.isoformat()
