"""Superposición retenida: el canónico recuerda lo que cubrió, y la que ningún índice cubre caduca con aviso.

Corre contra el doble en memoria y, con ``RAILSPEC_FALKORDB_URL``, contra FalkorDB real. El reloj es
inyectado: nada depende de que pase tiempo de verdad.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
from grafo_fabricas import COMMIT_1, COMMIT_2, UNIDAD, Api, alcances, consulta, delta, simbolo, vinculo
from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.tools import GraphIndexEntrada
from railspec.graph import AccesoGrafo, AlmacenGrafo, IndexadorCanonico, Retenida

COMMIT_3 = "3" * 40
COMMIT_4 = "4" * 40
COMMIT_5 = "5" * 40
OTRA_UNIDAD = "0002-revocar-pdf"
DIA = timedelta(days=1)
T0 = datetime(2026, 10, 1, 12, tzinfo=UTC)
PLAZO = 30 * DIA
BUSCAR = {"verbo": "search", "texto": "pdf"}


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
    org: str

    @property
    def alcance(self) -> AlcanceRepositorio:
        return self.v.alcance

    def indexar(self, commit, anterior=None, cubiertos=None, simbolos=None, idx=None):
        entrada = GraphIndexEntrada(
            alcance=self.alcance,
            rama="main",
            commit=commit,
            commit_anterior=anterior,
            lote=1,
            lotes=1,
            delta=delta(simbolos=simbolos if simbolos is not None else Api().todos),
            commits_cubiertos=cubiertos,
        )
        return (idx or self.idx).recibir(entrada, self.v)

    def reportar(self, unidad: str = UNIDAD, nombre: str = "pdf.marca_agua") -> None:
        """Un snapshot de ``unit.report``: reconstruye la superposición de la unidad."""

        s = simbolo("api", "src/marca.py", "funcion", nombre)
        self.grafo.aplicar_delta(self.alcance, COMMIT_1, delta(simbolos=[s]), unidad)

    def retener(self, unidad: str = UNIDAD, integrado: str = COMMIT_4) -> bool:
        self.reportar(unidad)
        return self.grafo.retener_superposicion(self.alcance, unidad, integrado)

    def barrer(self, retenida: timedelta | None = PLAZO):
        return self.grafo.limpiar_huerfanos(self.alcance, None, None, retenida)

    def unidades(self) -> list[str]:
        return sorted(self.acceso.superposiciones(self.alcance))

    def consultar(self, unidad: str | None = None):
        extra = {"unidad": unidad} if unidad else {}
        return self.grafo.consultar(consulta(self.org, "certificados", BUSCAR, **extra), [self.alcance])


@pytest.fixture(autouse=True)
def _sin_variables(monkeypatch):
    for variable in (
        "RAILSPEC_GRAFO_RETENIDAS_DIAS",
        "RAILSPEC_GRAFO_SUPERPOSICION_DIAS",
        "RAILSPEC_GRAFO_INDEXADO_HORAS",
    ):
        monkeypatch.delenv(variable, raising=False)


@pytest.fixture
def p(motor, org):
    reloj = Reloj()
    acceso = AccesoGrafo(motor)
    grafo = AlmacenGrafo(acceso, reloj=reloj, frescura_horas=0)  # que el aviso de tiempo no se cuele
    v = vinculo(org, NivelCodigo.restringido)
    return Piezas(reloj, acceso, grafo, IndexadorCanonico(acceso, grafo), v, org)


# --- el canónico recuerda lo que cubrió ---------------------------------------------------------


def test_integrar_en_un_commit_que_el_canonico_ya_cubrio_retira_al_momento(p):
    p.indexar(COMMIT_3, cubiertos=[COMMIT_3, COMMIT_2])  # índice completo que declara COMMIT_2
    p.reportar()

    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_2) is False
    assert p.unidades() == []


def test_un_commit_que_nadie_cubrio_sigue_reteniendo(p):
    p.indexar(COMMIT_3, cubiertos=[COMMIT_3, COMMIT_2])
    p.reportar()

    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_4) is True
    assert p.unidades() == [UNIDAD]


def test_los_cubiertos_se_acumulan_en_los_deltas_y_un_completo_los_reemplaza(p):
    p.indexar(COMMIT_1, cubiertos=[COMMIT_1])
    p.indexar(COMMIT_3, anterior=COMMIT_1, cubiertos=[COMMIT_3, COMMIT_2], simbolos=[])
    assert p.acceso.espacio(p.alcance).meta().cubiertos == [COMMIT_3, COMMIT_2, COMMIT_1]

    # Un cliente 1.4 no declara cubiertos: al menos el commit del índice.
    p.indexar(COMMIT_4, anterior=COMMIT_3, simbolos=[])
    assert p.acceso.espacio(p.alcance).meta().cubiertos == [COMMIT_4, COMMIT_3, COMMIT_2, COMMIT_1]

    p.indexar(COMMIT_5, cubiertos=[COMMIT_5, COMMIT_4])  # completo: reemplaza
    assert p.acceso.espacio(p.alcance).meta().cubiertos == [COMMIT_5, COMMIT_4]
    p.reportar()
    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_2) is True  # ya no se acuerda


def test_los_cubiertos_se_acotan_a_los_mil_mas_recientes(p):
    comunes = [f"{i:040x}" for i in range(1, 1001)]
    p.indexar(COMMIT_1, cubiertos=comunes[:1000])
    nuevos = [f"{i:040x}" for i in range(2000, 2300)]

    p.indexar(COMMIT_2, anterior=COMMIT_1, cubiertos=nuevos, simbolos=[])

    cubiertos = p.acceso.espacio(p.alcance).meta().cubiertos
    assert len(cubiertos) == 1000
    assert cubiertos[0] == COMMIT_2 and cubiertos[1:301] == nuevos  # lo más reciente primero
    assert comunes[0] in cubiertos and comunes[-1] not in cubiertos  # se pierde lo más viejo


def test_aplicar_un_delta_de_snapshot_sin_unidad_conserva_los_cubiertos(p):
    p.indexar(COMMIT_1, cubiertos=[COMMIT_1, COMMIT_2])

    p.grafo.aplicar_delta(p.alcance, COMMIT_3, delta(), None)

    assert p.acceso.espacio(p.alcance).meta().cubiertos == [COMMIT_1, COMMIT_2]


# --- retener sella el instante -------------------------------------------------------------------


def test_retener_sella_cuando_y_no_lo_renueva_al_repetir_el_mismo_commit(p):
    assert p.retener() is True
    assert p.acceso.espacio(p.alcance, UNIDAD).meta().retenido_en == T0
    p.reloj.avanzar(3 * DIA)
    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_4) is True
    assert p.acceso.espacio(p.alcance, UNIDAD).meta().retenido_en == T0
    assert p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_5) is True  # otro commit: cuenta de nuevo
    assert p.acceso.espacio(p.alcance, UNIDAD).meta().retenido_en == T0 + 3 * DIA


# --- caducidad por plazo -------------------------------------------------------------------------


def test_la_retenida_que_ningun_indice_cubre_caduca_a_los_treinta_dias(p):
    p.retener()
    p.reloj.avanzar(PLAZO - timedelta(hours=1))
    assert not p.barrer() and p.unidades() == [UNIDAD]

    p.reloj.avanzar(timedelta(hours=1))
    borrado = p.barrer()
    assert borrado.retenidas == [UNIDAD] and borrado.superposiciones == [] and borrado.indexados == []
    assert borrado and p.unidades() == []


def test_el_indexador_la_barre_al_aplicar_un_indice_y_lo_registra(p, caplog):
    p.indexar(COMMIT_1)
    p.retener(integrado=COMMIT_5)
    p.reloj.avanzar(PLAZO)

    with caplog.at_level(logging.INFO, logger="railspec.graph.indexado"):
        p.indexar(COMMIT_2, anterior=COMMIT_1, simbolos=[])

    assert p.unidades() == []
    assert any(UNIDAD in r.getMessage() and "retenidas" in r.getMessage() for r in caplog.records)


def test_el_plazo_cuenta_desde_que_se_retuvo_no_desde_el_ultimo_snapshot(p):
    p.reportar()
    p.reloj.avanzar(25 * DIA)  # el último snapshot es viejo, pero aún no se integra
    p.grafo.retener_superposicion(p.alcance, UNIDAD, COMMIT_4)
    p.reloj.avanzar(10 * DIA)

    assert not p.barrer() and p.unidades() == [UNIDAD]  # 35 días desde el snapshot, 10 desde que se retuvo
    p.reloj.avanzar(20 * DIA)
    assert p.barrer().retenidas == [UNIDAD]


def test_plazo_cero_la_retenida_no_caduca_nunca(p, monkeypatch):
    p.retener()
    p.reloj.avanzar(3650 * DIA)

    assert not p.barrer(retenida=None) and p.unidades() == [UNIDAD]
    # El indexador con 0 días tampoco la barre, ni aunque el índice aplique.
    p.indexar(COMMIT_1, cubiertos=[COMMIT_1], idx=IndexadorCanonico(p.acceso, p.grafo, retenidas_dias=0))
    assert p.unidades() == [UNIDAD]
    # Ni por la variable de entorno.
    monkeypatch.setenv("RAILSPEC_GRAFO_RETENIDAS_DIAS", "0")
    p.indexar(COMMIT_2, anterior=COMMIT_1, idx=IndexadorCanonico(p.acceso, p.grafo))
    assert p.unidades() == [UNIDAD]
    # Y sin variable, el defecto es 30 días.
    monkeypatch.delenv("RAILSPEC_GRAFO_RETENIDAS_DIAS")
    p.indexar(COMMIT_3, anterior=COMMIT_2, idx=IndexadorCanonico(p.acceso, p.grafo))
    assert p.unidades() == []


def test_la_variable_se_lee_al_construir_y_una_invalida_impide_arrancar(p, monkeypatch):
    monkeypatch.setenv("RAILSPEC_GRAFO_RETENIDAS_DIAS", "2")
    p.retener()
    p.reloj.avanzar(2 * DIA)
    p.indexar(COMMIT_1, cubiertos=[COMMIT_1], idx=IndexadorCanonico(p.acceso, p.grafo))
    assert p.unidades() == []

    for valor in ("-1", "una semana", "nan", "inf"):
        monkeypatch.setenv("RAILSPEC_GRAFO_RETENIDAS_DIAS", valor)
        with pytest.raises(ValueError, match="RAILSPEC_GRAFO_RETENIDAS_DIAS"):
            IndexadorCanonico(p.acceso, p.grafo)
        with pytest.raises(ValueError, match="RAILSPEC_GRAFO_RETENIDAS_DIAS"):
            AlmacenGrafo(p.acceso)


def test_una_superposicion_en_curso_no_se_toca_por_el_plazo_de_las_retenidas(p):
    p.reportar(UNIDAD)  # en curso: sin integrado
    p.retener(OTRA_UNIDAD)
    p.reloj.avanzar(PLAZO)

    borrado = p.barrer(retenida=timedelta(days=1))

    assert borrado.retenidas == [OTRA_UNIDAD] and borrado.superposiciones == []
    assert p.unidades() == [UNIDAD]
    # Su plazo es el de las abandonadas: 30 días sin snapshot.
    assert p.grafo.limpiar_huerfanos(p.alcance, PLAZO, None, timedelta(days=1)).superposiciones == [UNIDAD]


def test_la_retenida_anterior_a_esta_version_se_sella_y_se_juzga_en_el_siguiente_barrido(p):
    """Sin ``retenido_en``: el plazo cuenta desde el primer barrido que la ve, no desde nunca."""

    p.reportar()
    sup = p.acceso.espacio(p.alcance, UNIDAD)
    meta = sup.meta()
    meta.integrado = COMMIT_4  # como la dejaba la versión anterior
    sup.fijar_meta(meta)
    assert sup.meta().retenido_en is None
    assert p.grafo.retenidas(p.alcance) == [Retenida(UNIDAD, COMMIT_4, None, None)]

    p.reloj.avanzar(100 * DIA)
    assert not p.barrer()  # la sella, no la borra
    assert sup.meta().retenido_en == p.reloj.ahora and sup.meta().integrado == COMMIT_4
    p.reloj.avanzar(PLAZO)
    assert p.barrer().retenidas == [UNIDAD]


def test_el_barrido_no_toca_el_canonico_ni_las_trazas(p):
    p.indexar(COMMIT_1)
    p.grafo.enlazar_criterios(p.alcance, UNIDAD, ["CA-01"], [Api().emitir.id])
    p.retener()
    p.reloj.avanzar(10 * PLAZO)

    assert p.barrer().retenidas == [UNIDAD]

    assert p.acceso.espacio(p.alcance).meta().commit == COMMIT_1
    assert {s["nombre"] for s in p.acceso.espacio(p.alcance).todos_simbolos()} >= {"pdf.render"}
    assert [t.criterio for t in p.grafo.trazas(p.alcance)] == ["CA-01"]


# --- aviso a mitad de plazo ----------------------------------------------------------------------


def test_graph_query_con_unidad_avisa_de_la_retenida_desde_la_mitad_del_plazo(p):
    p.indexar(COMMIT_1)
    p.retener(integrado=COMMIT_4)

    p.reloj.avanzar(14 * DIA)
    assert p.consultar(UNIDAD).avisos == []

    p.reloj.avanzar(DIA)  # la mitad exacta
    (aviso,) = p.consultar(UNIDAD).avisos
    assert f"api: la superposición de la unidad {UNIDAD} sigue retenida" in aviso
    assert COMMIT_4[:12] in aviso and "faltan 15 días" in aviso
    assert "RAILSPEC_GRAFO_RETENIDAS_DIAS" in aviso

    p.reloj.avanzar(14 * DIA + timedelta(hours=20))
    assert "menos de un día" in p.consultar(UNIDAD).avisos[0]
    p.reloj.avanzar(DIA)
    assert "superó el plazo" in p.consultar(UNIDAD).avisos[0]


def test_el_aviso_es_de_la_unidad_consultada_y_no_sale_sin_ella(p):
    p.indexar(COMMIT_1)
    p.retener(UNIDAD)
    p.retener(OTRA_UNIDAD, integrado=COMMIT_5)
    p.reloj.avanzar(20 * DIA)

    assert p.consultar().avisos == []  # sin unidad no ve ninguna superposición
    (aviso,) = p.consultar(OTRA_UNIDAD).avisos
    assert OTRA_UNIDAD in aviso and UNIDAD not in aviso
    # Una unidad en curso, con superposición, no avisa de nada.
    p.reportar("0003-en-curso")
    assert p.consultar("0003-en-curso").avisos == []


def test_el_aviso_sale_tambien_sin_indice_canonico_y_no_con_plazo_cero(p, motor):
    p.retener()
    p.reloj.avanzar(20 * DIA)

    avisos = p.consultar(UNIDAD).avisos
    assert any("sin índice canónico" in a for a in avisos) and any("sigue retenida" in a for a in avisos)

    sin_plazo = AlmacenGrafo(p.acceso, reloj=p.reloj, retenidas_dias=0)
    salida = sin_plazo.consultar(consulta(p.org, "certificados", BUSCAR, unidad=UNIDAD), [p.alcance])
    assert not any("sigue retenida" in a for a in salida.avisos)


# --- listar las varadas --------------------------------------------------------------------------


def test_retenidas_lista_unidad_commit_y_desde_cuando_y_no_las_unidades_en_curso(p):
    p.retener(UNIDAD, COMMIT_4)
    p.reloj.avanzar(2 * DIA)
    p.retener(OTRA_UNIDAD, COMMIT_5)
    p.reportar("0003-en-curso")

    assert p.grafo.retenidas(p.alcance) == [
        Retenida(UNIDAD, COMMIT_4, T0, T0 + PLAZO),
        Retenida(OTRA_UNIDAD, COMMIT_5, T0 + 2 * DIA, T0 + 2 * DIA + PLAZO),
    ]
    otro = alcances(p.org, "otro-ws", "api")[0]
    assert p.grafo.retenidas(otro) == []


def test_retenidas_sin_plazo_no_dice_cuando_vence(p):
    p.retener()
    sin_plazo = AlmacenGrafo(p.acceso, reloj=p.reloj, retenidas_dias=0)

    assert sin_plazo.retenidas(p.alcance) == [Retenida(UNIDAD, COMMIT_4, T0, None)]


def test_el_instante_de_retencion_sobrevive_a_la_persistencia_de_la_meta(p):
    p.retener()

    meta = p.acceso.espacio(p.alcance, UNIDAD).meta()

    assert (meta.integrado, meta.retenido_en, meta.unidad) == (COMMIT_4, T0, UNIDAD)
