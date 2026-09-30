"""Una unidad completa en nivel ``restringido``, del arnés al servidor y vuelta.

El arnés simulado lanza ``railspec mcp`` por stdio (como Claude Code), el
proxy habla con ``railspec-server`` por MCP Streamable HTTP y el servidor
guarda en Mongo y FalkorDB reales. El recorrido se corre una vez por módulo
y cada prueba comprueba una parte:

unit.start → orden de trabajo → unit.report → sync.push / sync.pull →
checkpoints por formulario → cierre → commit empujado → unit.integrate.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from railspec_e2e import ORG, REPO, WS
from railspec_e2e.arnes import CODIGO, MARCA_CODIGO, ArnesSimulado, git, preparar_repositorio

CON_INDEXADOR = shutil.which("codebase-memory-mcp") is not None

RECORRIDO_INTERACTIVO = [
    "orden:redactar:spec",
    "checkpoint:aprobar-spec",
    "orden:redactar:plan",
    "checkpoint:aprobar-plan",
    "orden:redactar:tasks",
    "orden:implementar:implement",
    "orden:validar:implement",
    "cerrada",
]


@dataclass
class Resultado:
    clon: Path
    unidad: str
    worktree: Path
    tools: set[str]
    recorrido: list[str]
    preguntas: list[str]
    sync: dict[str, Any]
    integracion: dict[str, Any]
    estado: dict[str, Any]
    local: dict[str, Any] = field(default_factory=dict)


async def _recorrer(clon: Path, url: str, token: str) -> Resultado:
    async with ArnesSimulado(clon, url, token) as arnes:
        tools = await arnes.tools()
        inicio = await arnes.llamar(
            "unit_start", {"titulo": "Emitir PDF firmado", "pedido": "Los certificados deben salir firmados."}
        )
        unidad, worktree = inicio["unidad"], Path(inicio["worktree"])
        recorrido = await arnes.recorrer(unidad, worktree)

        # El desarrollador commitea y empuja la rama de la unidad; el proxy lo avisa
        # como commit.empujado por sync.push en la siguiente sincronización.
        git(worktree, "add", "-A")
        git(worktree, "commit", "-q", "-m", f"railspec: {unidad}")
        git(worktree, "push", "-q", "origin", f"railspec/{unidad}")
        sync = await arnes.llamar("railspec_sync", {"unidad": unidad})

        integracion = await arnes.llamar(
            "unit_integrate",
            {
                "unidad": unidad,
                "especificacion_viva": f".railspec/unidades/{unidad}/spec.md",
                "pr_url": f"https://github.com/{ORG}/{REPO}/pull/1",
            },
        )
        # Lo que emitió unit.integrate llega al espejo local con el siguiente sync.pull.
        await arnes.llamar("railspec_sync", {"unidad": unidad})
        estado = await arnes.llamar("unit_status", {"unidad": unidad})
        return Resultado(
            clon, unidad, worktree, tools, recorrido, arnes.preguntas, sync, integracion, estado["estado"]
        )


@pytest.fixture(scope="module")
def ciclo(entorno, tmp_path_factory) -> Resultado:
    clon = preparar_repositorio(tmp_path_factory.mktemp("repo"), ORG, WS, REPO)
    resultado = asyncio.run(_recorrer(clon, entorno.url, entorno.token))
    resultado.local = json.loads((resultado.worktree / ".railspec" / "estado-local.json").read_text())
    yield resultado
    if not _usa_servidor_externo():
        grafo = f"railspec:{ORG}:{WS}:{REPO}:u:{resultado.unidad}"
        db = entorno.falkordb()
        if grafo in db.list_graphs():
            db.select_graph(grafo).delete()


def _usa_servidor_externo() -> bool:
    import os

    return bool(os.environ.get("RAILSPEC_E2E_URL"))


def _filtro(r: Resultado) -> dict[str, str]:
    return {"unidad.org": ORG, "unidad.workspace": WS, "unidad.unidad": r.unidad}


def test_el_arnes_ve_las_tools_con_alias_mcp(ciclo):
    assert {"unit_start", "unit_advance", "unit_report", "unit_checkpoint", "railspec_sync"} <= ciclo.tools
    assert not any("." in t for t in ciclo.tools)


def test_recorrido_interactivo_hasta_el_cierre(ciclo):
    assert ciclo.recorrido == RECORRIDO_INTERACTIVO
    # Los dos checkpoints los resolvió el humano por formulario (elicitation), no el modelo.
    assert len(ciclo.preguntas) == 2
    assert "aprobar-spec" in ciclo.preguntas[0] and "aprobar-plan" in ciclo.preguntas[1]


def test_estado_final_cerrado_e_integrado(ciclo, entorno):
    estado = ciclo.estado
    assert estado["fase"] == "done" and estado["estado"] == "completado"
    assert estado["integracion"]["pr_url"].endswith("/pull/1")
    assert ciclo.integracion["integrada"] is True
    assert {g: v["veredicto"] for g, v in estado["gates"].items()} == {
        "spec": "aprobado",
        "plan": "aprobado",
        "tasks": "aprobado",
        "codigo": "aprobado",
    }
    assert [r["decision"] for r in estado["resoluciones"]] == ["aprobado", "aprobado"]


def test_sync_push_lleva_la_cola_numerada_por_el_proxy(ciclo, entorno):
    db = entorno.mongo()
    subidos = list(db.eventos.find({**_filtro(ciclo), "direccion": "local-a-remoto"}).sort("secuencia", 1))
    tipos = [e["carga"]["tipo"] for e in subidos]
    assert [e["secuencia"] for e in subidos] == list(range(1, len(subidos) + 1))
    # Una orden.reportada por orden ejecutada, un snapshot (implementar) y el empuje.
    ordenes = sum(1 for paso in ciclo.recorrido if paso.startswith("orden:"))
    assert tipos.count("orden.reportada") == ordenes
    assert tipos.count("snapshot.subido") == 1
    assert tipos[-1] == "commit.empujado"
    empuje = subidos[-1]["carga"]
    assert empuje["rama"] == f"railspec/{ciclo.unidad}"
    assert empuje["commit"] == git(ciclo.worktree, "rev-parse", "HEAD")
    # La cola local quedó vacía y confirmada hasta el último evento subido.
    assert ciclo.sync["pendientes"] == 0 and ciclo.local["cola_pendiente"] == []
    assert ciclo.local["ultima_secuencia_confirmada"] == len(subidos)


def test_sync_pull_trae_todos_los_eventos_remotos(ciclo, entorno):
    db = entorno.mongo()
    remotos = list(db.eventos.find({**_filtro(ciclo), "direccion": "remoto-a-local"}).sort("secuencia", 1))
    tipos = {e["carga"]["tipo"] for e in remotos}
    assert [e["secuencia"] for e in remotos] == list(range(1, len(remotos) + 1))
    assert {"orden.emitida", "veredicto.emitido", "checkpoint.solicitado", "checkpoint.resuelto"} <= tipos
    assert ciclo.local["ultima_secuencia_recibida"] == remotos[-1]["secuencia"]


def test_en_restringido_no_viaja_codigo(ciclo, entorno):
    db = entorno.mongo()
    for coleccion in db.list_collection_names():
        for doc in db[coleccion].find():
            assert MARCA_CODIGO not in json.dumps(doc, default=str), f"código en Mongo: {coleccion}"

    snapshots = list(db.snapshots.find(_filtro(ciclo)))
    assert len(snapshots) == 1
    s = snapshots[0]
    assert s["nivel_codigo"] == "restringido"
    assert s["diff"] is None and s["fragmentos"] is None
    assert sorted(a["ruta"] for a in s["archivos"]) == sorted(CODIGO)
    assert all(a["sha256_despues"] for a in s["archivos"])

    # La salida de las pruebas imprime la marca: en restringido solo viajan código de salida y duración.
    validacion = db.reportes.find_one({**_filtro(ciclo), "validacion": {"$ne": None}})["validacion"]
    assert validacion["codigo_salida"] == 0 and validacion["salida"] == ""
    log = next((ciclo.worktree / ".railspec" / "validacion").glob("*.log"))
    assert MARCA_CODIGO in log.read_text()  # la salida completa se queda en local

    fk = entorno.falkordb()
    for nombre in fk.list_graphs():
        if f":{WS}:" not in nombre:
            continue
        filas = fk.select_graph(nombre).query("MATCH (n) RETURN properties(n)").result_set
        assert MARCA_CODIGO not in json.dumps(filas, default=str), f"código en el grafo {nombre}"


@pytest.mark.skipif(not CON_INDEXADOR, reason="sin codebase-memory-mcp el snapshot va en solo-hashes")
def test_delta_del_indice_llega_a_la_superposicion_del_grafo(ciclo, entorno):
    s = entorno.mongo().snapshots.find_one(_filtro(ciclo))
    assert s["modo_delta"] == "completo"
    nombres = {x["nombre"] for x in s["delta_indice"]["simbolos_upsert"]}
    assert {"src.firma.firmar", "src.firma.verificar"} <= nombres

    grafo = entorno.falkordb().select_graph(f"railspec:{ORG}:{WS}:{REPO}:u:{ciclo.unidad}")
    filas = grafo.query("MATCH (n) RETURN n.nombre").result_set
    assert {"src.firma.firmar", "src.firma.verificar"} <= {f[0] for f in filas}
