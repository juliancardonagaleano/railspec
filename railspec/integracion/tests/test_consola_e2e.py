"""La consola web contra el servidor real: Mongo y FalkorDB de verdad, unidad recorrida por el proxy.

Recorre una unidad completa en ``restringido`` (como ``test_ciclo_restringido``)
y la lee por la API de la consola (``/consola/api``) con el token de
desarrollo como Bearer: detalle, línea de tiempo, trazabilidad CA-NN,
resumen del workspace, repositorios del grafo y tools por el registro. Ninguna
respuesta puede llevar la marca que solo existe en el código.
"""

from __future__ import annotations

import asyncio
import json
import shutil

import httpx
import pytest
from railspec_e2e import ORG, REPO, WS
from railspec_e2e.arnes import MARCA_CODIGO, preparar_repositorio
from test_ciclo_restringido import _recorrer

CON_INDEXADOR = shutil.which("codebase-memory-mcp") is not None


@pytest.fixture(scope="module")
def consola(entorno, tmp_path_factory):
    clon = preparar_repositorio(tmp_path_factory.mktemp("repo-consola"), ORG, WS, REPO)
    base = entorno.url.removesuffix("/").removesuffix("/mcp")
    with httpx.Client(base_url=base, headers={"Authorization": f"Bearer {entorno.token}"}, timeout=30) as c:

        def buscar_en_la_superposicion(unidad: str) -> httpx.Response:
            # Se busca antes de integrar, con la unidad en curso. Tras unit.integrate con
            # commit_integrado la superposición no se descarta: queda retenida hasta que CI
            # reindexa el merge (ver test_ciclo_restringido).
            return c.post(
                "/consola/api/tools/graph.query",
                json={
                    "alcance": {"org": ORG, "workspace": WS},
                    "unidad": unidad,
                    "consulta": {"verbo": "search", "texto": "firmar"},
                },
            )

        ciclo = asyncio.run(_recorrer(clon, entorno.url, entorno.token, buscar_en_la_superposicion))
        yield ciclo, c


def _sin_codigo(r: httpx.Response) -> dict | list:
    assert r.status_code == 200, r.text
    assert MARCA_CODIGO not in r.text
    return r.json()


def test_la_consola_ve_la_unidad_sin_codigo(consola):
    ciclo, c = consola
    ws = f"/consola/api/orgs/{ORG}/workspaces/{WS}"
    yo = _sin_codigo(c.get("/consola/api/yo"))
    assert yo["organizaciones"][0]["workspaces"][0] == {"workspace": WS, "nombre": WS, "rol": "desarrollador"}

    detalle = _sin_codigo(c.get(f"{ws}/unidades/{ciclo.unidad}"))
    assert detalle["estado"]["fase"] == "done" and detalle["estado"]["integracion"]["pr_url"].endswith(
        "/pull/1"
    )

    linea = _sin_codigo(c.get(f"{ws}/unidades/{ciclo.unidad}/linea-de-tiempo"))
    assert [o["tipo"] for o in linea["ordenes"]] == [
        "redactar",
        "redactar",
        "redactar",
        "implementar",
        "validar",
    ]
    tipos = {e["carga"]["tipo"] for e in linea["eventos"]}
    assert {"orden.emitida", "orden.reportada", "commit.empujado", "unidad.integrada"} <= tipos

    traza = _sin_codigo(c.get(f"{ws}/unidades/{ciclo.unidad}/trazabilidad"))
    tareas = [t for ca in traza["criterios"] for t in ca["tareas"]] + traza["sin_criterio"]["tareas"]
    assert tareas and all(t["completada"] for t in tareas)
    if CON_INDEXADOR:
        simbolos = {s["nombre"] for ca in traza["criterios"] for s in ca["simbolos"]}
        assert simbolos & {"src.firma.firmar", "src.firma.verificar"}

    resumen = _sin_codigo(c.get(f"{ws}/resumen"))
    assert resumen["unidades"]["por_fase"]["done"] >= 1 and resumen["unidades"]["integradas"] >= 1
    assert resumen["gates"]["codigo"]["aprobado"] >= 1

    repos = _sin_codigo(c.get(f"{ws}/grafo/repositorios"))
    assert repos[0]["repositorio"] == REPO and repos[0]["nivel_codigo"] == "restringido"


def test_tools_y_token_de_consola(consola, entorno):
    ciclo, c = consola
    lista = _sin_codigo(
        c.post("/consola/api/tools/unit.list", json={"alcance": {"org": ORG, "workspace": WS}})
    )
    assert ciclo.unidad in {u["unidad"] for u in lista["unidades"]}
    if CON_INDEXADOR:
        resultados = _sin_codigo(ciclo.previo)["resultados"]
        assert any(x["ref"].get("nombre") == "src.firma.firmar" for x in resultados)

    # El token rsc1 de la consola vale como Bearer en /v1 (lo usará el chat), pero solo se emite
    # con la cookie de sesión y la cabecera anti-CSRF: un Bearer no basta para acuñarlo.
    csrf = {"X-Railspec-Consola": "1"}
    assert c.post("/consola/api/auth/token", headers=csrf).status_code == 401
    with httpx.Client(base_url=c.base_url, timeout=30) as navegador:
        r = navegador.post("/consola/api/auth/desarrollo", json={"token": entorno.token})
        assert r.status_code == 204, r.text
        token = _sin_codigo(navegador.post("/consola/api/auth/token", headers=csrf))["token"]
    r = httpx.post(
        f"{c.base_url}/v1/tools/unit.status",
        json={"unidad": {"org": ORG, "workspace": WS, "unidad": ciclo.unidad}},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200 and json.loads(r.text)["estado"]["dueno"]["login"]
