"""Registro único de tools expuesto por HTTP (consola) y MCP (proxy local)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid

import httpx
import pytest
from apoyo_motor import ORG, WS, construir, entrada_start
from railspec.contracts.comun import Actor, Canal, OidcGithubActions, TipoActor
from railspec.contracts.repositorio import AsignacionRol, Auditoria, Rol, SujetoUsuario
from railspec.contracts.tools import TOOLS, Superficie
from railspec.server.api import AutorizadorRoles, IdentidadDesarrollo, Registro
from railspec.server.api.identidad import IdentidadGithub, TokenInvalido, token_de_cabecera
from railspec.server.api.superficies import aplicacion

TOKENS = {"tk-julian": ("juliancardonagaleano", 83125327), "tk-ana": ("ana", 7)}
CABECERA = {"Authorization": "Bearer tk-julian"}


def montar(abierto=True):
    motor, proveedor = construir()
    registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=abierto))
    return motor, registro, aplicacion(registro, IdentidadDesarrollo(TOKENS))


@contextlib.asynccontextmanager
async def cliente(app):
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://railspec") as c:
            yield c


def cuerpo_start():
    return entrada_start().model_dump(mode="json")


def test_http_start_status_y_manifiesto():
    async def caso():
        _, registro, app = montar()
        async with cliente(app) as c:
            assert (await c.get("/healthz")).json() == {"estado": "ok"}
            nombres = {t["name"] for t in (await c.get("/v1/tools")).json()["tools"]}
            assert {"unit.start", "unit.status", "telemetry.query"} <= nombres
            # Sin manejador, la tool no se anuncia.
            assert "code.read" not in nombres and "graph.query" not in nombres
            r = await c.post("/v1/tools/unit.start", json=cuerpo_start(), headers=CABECERA)
            assert r.status_code == 200, r.text
            unidad = r.json()["estado"]["unidad"]
            r = await c.post("/v1/tools/unit.status", json={"unidad": unidad}, headers=CABECERA)
            assert r.status_code == 200 and r.json()["estado"]["fase"] == "spec"

    asyncio.run(caso())


def test_http_errores():
    async def caso():
        _, _, app = montar()
        async with cliente(app) as c:
            assert (await c.post("/v1/tools/unit.start", json=cuerpo_start())).status_code == 401
            r = await c.post(
                "/v1/tools/unit.start", json=cuerpo_start(), headers={"Authorization": "Bearer otro"}
            )
            assert r.status_code == 401
            r = await c.post("/v1/tools/unit.start", json={"alcance": {"org": ORG}}, headers=CABECERA)
            assert r.status_code == 422 and r.json()["errores"]
            r = await c.post("/v1/tools/unit.start", content=b"{", headers=CABECERA)
            assert r.status_code == 422
            r = await c.post("/v1/tools/no.existe", json={}, headers=CABECERA)
            assert r.status_code == 404 and r.json()["codigo"] == "no-encontrado"
            unidad = {"org": ORG, "workspace": WS, "unidad": "9999-nada"}
            r = await c.post("/v1/tools/unit.status", json={"unidad": unidad}, headers=CABECERA)
            assert r.status_code == 404
            r = await c.post(
                "/v1/tools/unit.start",
                json=cuerpo_start() | {"version_contrato_cliente": "2.0"},
                headers=CABECERA,
            )
            assert r.status_code == 400 and r.json()["codigo"] == "version-contrato-no-soportada"

    asyncio.run(caso())


def _asignacion(github_id: int, rol: Rol, workspace: str | None) -> AsignacionRol:
    from datetime import UTC, datetime

    from apoyo_motor import JULIAN

    ahora = datetime(2026, 9, 30, tzinfo=UTC)
    return AsignacionRol(
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora),
        id=uuid.uuid4(),
        org=ORG,
        workspace=workspace,
        rol=rol,
        sujeto=SujetoUsuario(github_id=github_id),
    )


def test_roles_desde_asignaciones():
    async def caso():
        motor, _, app = montar(abierto=False)
        motor.n.almacen.guardar_configuracion(
            [
                _asignacion(83125327, Rol.desarrollador, WS),
                _asignacion(7, Rol.lector, WS),
            ]
        )
        async with cliente(app) as c:
            r = await c.post("/v1/tools/unit.start", json=cuerpo_start(), headers=CABECERA)
            assert r.status_code == 200, r.text
            unidad = r.json()["estado"]["unidad"]
            ana = {"Authorization": "Bearer tk-ana"}
            r = await c.post("/v1/tools/unit.start", json=cuerpo_start(), headers=ana)
            assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance"
            r = await c.post("/v1/tools/unit.status", json={"unidad": unidad}, headers=ana)
            assert r.status_code == 200
            otro_ws = cuerpo_start() | {"alcance": {"org": ORG, "workspace": "reportes"}}
            r = await c.post("/v1/tools/unit.start", json=otro_ws, headers=CABECERA)
            assert r.status_code == 403

    asyncio.run(caso())


def test_superficie_y_tipo_de_actor():
    async def caso():
        motor, registro, _ = montar()
        servicio = Actor(
            tipo=TipoActor.servicio,
            canal=Canal.servidor,
            oidc=OidcGithubActions(repositorio="acme/certificados-api", workflow="ci.yml"),
        )
        # Todas las tools del motor admiten humanos; un tipo de actor no admitido se rechaza.
        from railspec.contracts.tools import TOOLS

        restringidas = [
            n
            for n, t in TOOLS.items()
            if getattr(t, "tipos_actor", None)
            and TipoActor.servicio not in t.tipos_actor
            and n in registro._manejadores
        ]
        for nombre in restringidas:
            r = await registro.invocar(nombre, {}, servicio, Superficie.http)
            assert r.cuerpo["codigo"] == "fuera-de-alcance"
        solo_http = [t for t in registro.tools(Superficie.http) if Superficie.mcp not in t.superficies]
        for t in solo_http:
            r = await registro.invocar(t.nombre, {}, servicio, Superficie.mcp)
            assert r.cuerpo["codigo"] == "fuera-de-alcance"

    asyncio.run(caso())


def test_actor_de_servicio_solo_alcanza_graph_index():
    """A1: un OIDC de CI (de cualquier repositorio) no tiene rol en ninguna organización.

    Antes cualquier actor ``servicio`` era ``desarrollador`` en toda org y workspace, así que
    un workflow ajeno leía unit.list, unit.status (orden completa), unit.export, insumo.get,
    telemetry.query y graph.query de cualquier tenant cuyos slugs adivinara."""

    servicio = Actor(
        tipo=TipoActor.servicio,
        canal=Canal.ci,
        oidc=OidcGithubActions(repositorio="atacante/repo", workflow="x.yml@refs/heads/main"),
    )
    alcance = {"org": ORG, "workspace": WS}
    unidad = {"org": ORG, "workspace": WS, "unidad": "0001-emitir-pdf"}
    lecturas = {
        "unit.list": {"alcance": alcance},
        "unit.status": {"unidad": unidad},
        "unit.export": {"unidad": unidad},
        "insumo.get": {"alcance": alcance, "id": str(uuid.uuid4())},
        "telemetry.query": {
            "org": ORG,
            "workspace": WS,
            "desde": "2026-01-01T00:00:00Z",
            "hasta": "2026-12-31T00:00:00Z",
            "agrupar_por": ["fase"],
        },
        "graph.query": {"alcance": alcance, "consulta": {"verbo": "resolve", "nombre": "firmar"}},
    }
    ejecutados: list[str] = []

    def espia(nombre):
        async def manejador(entrada, actor):
            ejecutados.append(nombre)
            raise AssertionError(f"{nombre} se ejecutó para un actor de servicio")

        return manejador

    async def caso():
        for abierto in (False, True):
            motor, _ = construir()
            autorizador = AutorizadorRoles(motor.n.almacen, abierto=abierto)
            extra = {n: espia(n) for n in ("unit.export", "insumo.get", "graph.query")}
            registro = Registro.del_motor(motor, autorizador, extra)
            for nombre, entrada in lecturas.items():
                for superficie in (Superficie.http, Superficie.mcp):
                    if superficie not in TOOLS[nombre].superficies:
                        continue
                    r = await registro.invocar(nombre, entrada, servicio, superficie)
                    assert r.estado_http == 403, (nombre, superficie, abierto, r.cuerpo)
                    assert r.cuerpo["codigo"] == "fuera-de-alcance"
            assert not ejecutados
            # Ninguna tool expuesta (salvo graph.index) admite al actor de servicio.
            for superficie in (Superficie.http, Superficie.mcp):
                for tool in registro.tools(superficie):
                    if tool.nombre != "graph.index":
                        assert TipoActor.servicio not in tool.tipos_actor, tool.nombre
            # El autorizador tampoco le da rol por su cuenta (defensa en profundidad).
            assert autorizador.rol(servicio, ORG, WS) is None

    asyncio.run(caso())


def test_registro_rechaza_manejadores_fuera_de_contrato():
    motor, _ = construir()
    with pytest.raises(ValueError):
        Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen), {"unit.inventada": motor.status})


def test_mcp_lista_y_llama():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def caso():
        _, _, app = montar()
        async with app.router.lifespan_context(app):
            http = httpx2.AsyncClient(
                transport=httpx2.ASGITransport(app=app), base_url="http://railspec", headers=CABECERA
            )
            async with http, streamable_http_client("http://railspec/mcp/", http_client=http) as flujos:
                lectura, escritura = flujos[0], flujos[1]
                async with ClientSession(lectura, escritura) as sesion:
                    await sesion.initialize()
                    tools = {t.name for t in (await sesion.list_tools()).tools}
                    assert {"unit_start", "unit_report", "unit_advance", "sync_pull", "sync_push"} <= tools
                    assert not any("." in t for t in tools)
                    r = await sesion.call_tool("unit_start", cuerpo_start())
                    assert not r.is_error, r
                    assert r.structured_content["estado"]["fase"] == "spec"
                    r = await sesion.call_tool(
                        "unit_status", {"unidad": {"org": ORG, "workspace": WS, "unidad": "9999-nada"}}
                    )
                    assert r.is_error and r.structured_content["codigo"] == "no-encontrado"

    asyncio.run(caso())


def test_identidad():
    assert token_de_cabecera("Bearer abc") == "abc"
    assert token_de_cabecera("Basic abc") is None and token_de_cabecera(None) is None
    ident = IdentidadDesarrollo(TOKENS)
    assert ident.actor_desde_token("tk-ana", "consola").github_id == 7
    with pytest.raises(TokenInvalido):
        ident.actor_desde_token("x", "consola")

    llamadas = []

    class Falso:
        """api.github.com: la comprobación de la GitHub App (M3; antes bastaba ``GET /user``)."""

        def post(self, url, *, auth, json, headers):
            llamadas.append(json["access_token"])
            if json["access_token"] == "malo":
                return httpx.Response(404)
            usuario = {"login": "julian", "id": 83125327}
            return httpx.Response(200, json={"app": {"client_id": "Iv1.x"}, "user": usuario})

    from railspec.server.consola.config import ConfigGithubApp

    gh = IdentidadGithub(Falso(), app=ConfigGithubApp("Iv1.x", "secreto"))
    assert gh.actor_desde_token("bueno", "arnes").login == "julian"
    gh.actor_desde_token("bueno", "arnes")
    assert len(llamadas) == 1  # cacheado
    with pytest.raises(TokenInvalido):
        gh.actor_desde_token("malo", "arnes")


def test_graph_query_con_repositorios_vinculados():
    from apoyo_motor import JULIAN
    from railspec.contracts.comun import NivelCodigo
    from railspec.contracts.tools import GraphQuerySalida
    from railspec.server.api.grafo import manejador_graph_query

    vistos = []

    class GrafoFalso:
        def consultar(self, entrada, visibles):
            vistos.append([v.repositorio for v in visibles])
            if entrada.consulta.nombre == "prohibido":
                raise PermissionError("repositorio no visible")
            return GraphQuerySalida(resultados=[], commits={})

    async def caso():
        motor, _ = construir(nivel=NivelCodigo.interno)
        extra = {"graph.query": manejador_graph_query(GrafoFalso(), motor.n.almacen)}
        registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=True), extra)
        assert "graph.query" in {t.nombre for t in registro.tools(Superficie.mcp)}
        entrada = {
            "alcance": {"org": ORG, "workspace": WS},
            "consulta": {"verbo": "resolve", "nombre": "firmar"},
        }
        r = await registro.invocar("graph.query", entrada, JULIAN, Superficie.mcp)
        assert r.ok and vistos == [["certificados-api"]]
        entrada["consulta"]["nombre"] = "prohibido"
        r = await registro.invocar("graph.query", entrada, JULIAN, Superficie.mcp)
        assert r.estado_http == 403 and r.cuerpo["codigo"] == "fuera-de-alcance"

    asyncio.run(caso())
