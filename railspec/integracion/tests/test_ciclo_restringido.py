"""Una unidad completa en nivel ``restringido``, del arnés al servidor y vuelta.

El arnés simulado lanza ``railspec mcp`` por stdio (como Claude Code), el
proxy habla con ``railspec-server`` por MCP Streamable HTTP y el servidor
guarda en Mongo y FalkorDB reales. El recorrido se corre una vez por módulo
y cada prueba comprueba una parte:

unit.start → orden de trabajo → unit.report → sync.push / sync.pull →
checkpoints por formulario → cierre → commit empujado → merge del PR →
unit.integrate. Si el servidor acepta el OIDC local (el del subproceso), CI reindexa
la rama por defecto con ``reindexar.py`` real: antes de la unidad y tras el merge.
"""

from __future__ import annotations

import asyncio
import json
import shutil
from collections.abc import Callable
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
    base: str  # commit de la rama por defecto cuando CI la indexó por primera vez
    integrado: str  # commit del merge de la unidad en la rama por defecto
    ci: Any = None
    local: dict[str, Any] = field(default_factory=dict)
    previo: Any = None  # lo que devolvió ``antes_de_integrar``


async def _recorrer(
    clon: Path,
    url: str,
    token: str,
    antes_de_integrar: Callable[[str], Any] | None = None,
    ci: Any = None,
) -> Resultado:
    """``antes_de_integrar(unidad)`` lee el estado previo a ``unit.integrate`` (p. ej. la superposición
    de la unidad en el grafo, que tras integrar queda retenida hasta que CI reindexa el merge)."""

    base = git(clon, "rev-parse", "HEAD")
    if ci is not None:
        ci.reindexar(base)  # el canónico parte del commit inicial, como tras el primer push a main
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
        previo = antes_de_integrar(unidad) if antes_de_integrar else None

        # El PR se mergea (avance rápido de main): el commit que CI reindexará es el de la unidad.
        integrado = git(worktree, "rev-parse", "HEAD")
        git(worktree, "push", "-q", "origin", "HEAD:main")

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
            clon,
            unidad,
            worktree,
            tools,
            recorrido,
            arnes.preguntas,
            sync,
            integracion,
            estado["estado"],
            base,
            integrado,
            ci,
            previo=previo,
        )


def _superposicion(entorno, unidad: str) -> set[str | None]:
    grafo = entorno.falkordb().select_graph(f"railspec:{ORG}:{WS}:{REPO}:u:{unidad}")
    return {f[0] for f in grafo.query("MATCH (n) RETURN n.nombre").result_set}


@pytest.fixture(scope="module")
def ciclo(entorno, tmp_path_factory) -> Resultado:
    clon = preparar_repositorio(tmp_path_factory.mktemp("repo"), ORG, WS, REPO)
    ci = entorno.ci(clon.parent / "remoto.git", clon.parent) if CON_INDEXADOR else None
    resultado = asyncio.run(
        _recorrer(clon, entorno.url, entorno.token, lambda unidad: _superposicion(entorno, unidad), ci)
    )
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
    # El nivel se congela al crear la unidad (1.7) y es lo que rige el material, no el proveedor.
    unidad = db.unidades.find_one(_filtro(ciclo))
    assert unidad is not None and unidad["nivel_efectivo"] == "restringido"
    assert [r["nivel_codigo"] for r in unidad["repositorios"]] == ["restringido"]
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

    # Estaba en la superposición al llegar a unit.integrate y sigue ahí tras integrar: se retiene
    # hasta que CI reindexa el commit integrado (la retirada la comprueba la prueba de más abajo).
    nuevos = {"src.firma.firmar", "src.firma.verificar"}
    assert nuevos <= ciclo.previo
    assert nuevos <= _superposicion(entorno, ciclo.unidad)


def _meta(fk, grafo: str) -> dict[str, Any]:
    return json.loads(fk.select_graph(grafo).query("MATCH (m:Meta) RETURN m.json").result_set[0][0])


def _nombres(fk, grafo: str) -> set[str]:
    return {
        f[0]
        for f in fk.select_graph(grafo)
        .query("MATCH (n) WHERE n.nombre IS NOT NULL RETURN n.nombre")
        .result_set
    }


def test_integrar_envia_el_commit_del_merge_y_queda_auditado(ciclo, entorno):
    # El proxy trajo la punta de origin/main (el merge) sin que el arnés se lo diera.
    assert ciclo.integracion["commit_integrado"] == ciclo.integrado
    auditoria = entorno.mongo().auditoria.find_one(
        {"unidad": ciclo.unidad, "evento": "integracion"}, {"detalle": 1}
    )
    assert auditoria["detalle"]["commit_integrado"] == ciclo.integrado


@pytest.mark.skipif(not CON_INDEXADOR, reason="sin codebase-memory-mcp no hay delta ni superposición")
def test_la_superposicion_sigue_visible_tras_integrar_hasta_que_ci_reindexa_el_merge(ciclo, entorno):
    if ciclo.ci is None:
        pytest.skip("el servidor externo no acepta el OIDC local de CI")
    fk = entorno.falkordb()
    canonico = f"railspec:{ORG}:{WS}:{REPO}"
    superposicion = f"{canonico}:u:{ciclo.unidad}"
    nuevos = {"src.firma.firmar", "src.firma.verificar"}

    # Integrada pero aún sin reindexar el merge: la unidad conserva su código; el canónico, no.
    assert superposicion in fk.list_graphs()
    assert _meta(fk, superposicion)["integrado"] == ciclo.integrado
    assert nuevos <= _nombres(fk, superposicion)
    assert _meta(fk, canonico)["commit"] == ciclo.base and not nuevos & _nombres(fk, canonico)

    # CI reindexa el merge (delta desde el commit que ya tenía): el canónico lo cubre y la
    # superposición se retira.
    salida = ciclo.ci.reindexar(ciclo.integrado, anterior=ciclo.base)
    assert salida.aplicado and salida.commit == ciclo.integrado
    assert superposicion not in fk.list_graphs()
    assert _meta(fk, canonico)["commit"] == ciclo.integrado
    assert nuevos <= _nombres(fk, canonico)
