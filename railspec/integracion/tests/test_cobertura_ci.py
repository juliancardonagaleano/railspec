"""Contrato 1.5: CI declara los commits que cada índice cubre y el servidor retira lo que cubren.

Sin el arnés: un historial git real con el ``reindexar.py`` real (indexador real y HTTP real) contra
el servidor del e2e, con Mongo y FalkorDB reales. Las superposiciones de unidades ya integradas se
crean con ``railspec-graph`` sobre el mismo FalkorDB, como las deja ``unit.integrate``. Cada prueba
parte de su propio índice completo y borra sus superposiciones al terminar. Solo mira las de sus
unidades: en un FalkorDB que se reusa entre corridas (el flujo local) pueden quedar las retenidas de
otros módulos, p. ej. la de ``test_consola_e2e``, que nunca indexa su merge.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

import pytest
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.snapshot import DeltaIndice, MotorIndice, Simbolo, TipoSimbolo, id_simbolo
from railspec.graph import AccesoGrafo, AlmacenGrafo
from railspec.graph.motor_falkordb import MotorFalkor
from railspec_e2e import ORG, REPO, WS
from railspec_e2e.arnes import git, preparar_repositorio

pytestmark = pytest.mark.skipif(
    shutil.which("codebase-memory-mcp") is None, reason="sin codebase-memory-mcp no hay índice de CI"
)

ALCANCE = AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO)
CANONICO = f"railspec:{ORG}:{WS}:{REPO}"
UNIDADES = ("0001-salto", "0002-despues")


@dataclass
class Historia:
    ci: object
    grafo: AlmacenGrafo
    falkordb: object
    base: str
    m2: str
    m3: str
    m4: str

    def superposiciones(self) -> set[str]:
        """Las superposiciones que quedan de las unidades de esta prueba."""

        prefijo = f"{CANONICO}:u:"
        existentes = {g.removeprefix(prefijo) for g in self.falkordb.list_graphs() if g.startswith(prefijo)}
        return existentes & set(UNIDADES)

    def limpiar(self) -> None:
        for unidad in UNIDADES:
            self.grafo.descartar_superposicion(ALCANCE, unidad, self.base)

    def retener(self, unidad: str, integrado: str) -> None:
        """La superposición de ``unidad`` tal como la deja ``unit.integrate`` en ``integrado``."""

        nombre = unidad.replace("-", "_")
        simbolo = Simbolo(
            id=id_simbolo(REPO, f"src/{nombre}.py", "funcion", nombre),
            nombre=nombre,
            tipo=TipoSimbolo.funcion,
            ruta=f"src/{nombre}.py",
            linea_inicio=1,
            linea_fin=2,
            sha256="0" * 64,
        )
        delta = DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=[simbolo])
        self.grafo.aplicar_delta(ALCANCE, self.base, delta, unidad)
        assert self.grafo.retener_superposicion(ALCANCE, unidad, integrado) is True


def _commit(clon: Path, nombre: str) -> str:
    (clon / "src" / f"{nombre}.py").write_text(f"def {nombre}():\n    return 1\n", encoding="utf-8")
    git(clon, "add", "-A")
    git(clon, "commit", "-q", "-m", nombre)
    git(clon, "push", "-q", "origin", "main")
    return git(clon, "rev-parse", "HEAD")


@pytest.fixture
def historia(entorno, tmp_path):
    clon = preparar_repositorio(tmp_path, ORG, WS, REPO)
    ci = entorno.ci(tmp_path / "remoto.git", tmp_path)
    if ci is None:
        pytest.skip("el servidor externo no acepta el OIDC local de CI")
    base = git(clon, "rev-parse", "HEAD")
    m2, m3, m4 = (_commit(clon, n) for n in ("firma", "sello", "marca"))
    fk = entorno.falkordb()
    grafo = AlmacenGrafo(AccesoGrafo(MotorFalkor(fk)))
    ci.reindexar(base)  # índice completo: el canónico parte de ``base``
    h = Historia(ci, grafo, fk, base, m2, m3, m4)
    h.limpiar()  # restos de una corrida anterior interrumpida
    yield h
    h.limpiar()


def test_un_delta_que_salta_un_commit_retira_la_unidad_integrada_en_el_salto(historia):
    """CI no corrió para m2 (una corrida cancelada): m3 llega como delta desde ``base``."""

    h = historia
    h.retener("0001-salto", h.m2)
    h.retener("0002-despues", h.m4)  # integrada en un commit que CI aún no indexa

    assert h.ci.reindexar(h.m3, anterior=h.base).aplicado
    assert h.superposiciones() == {"0002-despues"}

    assert h.ci.reindexar(h.m4, anterior=h.m3).aplicado
    assert h.superposiciones() == set()


def test_un_indice_completo_con_cobertura_no_retira_lo_integrado_despues(historia):
    h = historia
    h.retener("0001-salto", h.m2)
    h.retener("0002-despues", h.m4)

    assert h.ci.reindexar(h.m3).aplicado  # completo de m3, a la mitad de la historia
    assert h.superposiciones() == {"0002-despues"}


def test_sin_cobertura_el_indice_completo_retira_todas_las_retenidas(historia):
    """La limpieza manual (``workflow_dispatch`` con ``retirar_todas``): regla de 1.4."""

    h = historia
    h.retener("0001-salto", h.m2)
    h.retener("0002-despues", h.m4)

    assert h.ci.reindexar(h.m3, cobertura=False).aplicado
    assert h.superposiciones() == set()
