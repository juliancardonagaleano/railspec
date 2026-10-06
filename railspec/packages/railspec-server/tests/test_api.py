"""Registro único de tools expuesto por HTTP (consola) y MCP (proxy local)."""

from __future__ import annotations

import asyncio
import contextlib
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from apoyo_motor import ORG, WS, construir, entrada_start
from railspec.contracts.comun import Actor, Canal, OidcGithubActions, TipoActor
from railspec.contracts.repositorio import AsignacionRol, Auditoria, Rol, SujetoEquipo, SujetoUsuario
from railspec.contracts.tools import TOOLS, Superficie
from railspec.server.api import AutorizadorRoles, IdentidadDesarrollo, Registro
from railspec.server.api.identidad import IdentidadGithub, TokenInvalido, con_equipos, token_de_cabecera
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


def _asignacion_equipo(equipo_id: int, rol: Rol, workspace: str | None, org: str = ORG) -> AsignacionRol:
    return _asignacion(1, rol, workspace).model_copy(
        update={
            "org": org,
            "sujeto": SujetoEquipo(github_org="acme", equipo="plataforma", equipo_id=equipo_id),
        }
    )


def _humano(github_id: int = 9, equipos=()) -> Actor:
    base = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=github_id, login="luis")
    return con_equipos(base, frozenset(equipos))


def test_autorizador_suma_los_roles_de_los_equipos_del_actor():
    _, registro, _ = montar(abierto=False)
    almacen = registro._autorizador._almacen
    almacen.guardar_configuracion(
        [
            _asignacion_equipo(4242, Rol.lector, WS),
            _asignacion_equipo(4243, Rol.desarrollador, "reportes"),
            _asignacion_equipo(777, Rol.org_admin, None),
            _asignacion_equipo(4242, Rol.org_admin, None, org="otra"),
            _asignacion(9, Rol.workspace_admin, WS),
        ]
    )
    autorizador = AutorizadorRoles(almacen)
    # Sin equipos solo cuenta la persona; los equipos de las asignaciones no se le atribuyen.
    assert autorizador.rol(_humano(), ORG, WS) == Rol.workspace_admin
    assert autorizador.rol(_humano(10), ORG, WS) is None
    # El equipo da su rol, en su workspace y en su organización, y no más.
    luis = _humano(10, {4242})
    assert autorizador.rol(luis, ORG, WS) == Rol.lector
    assert autorizador.rol(luis, ORG, "reportes") is None
    assert autorizador.rol(luis, ORG, None) is None
    assert autorizador.rol(luis, "otra", WS) == Rol.org_admin  # asignación explícita de ese equipo en "otra"
    assert autorizador.rol(luis, "tercera", WS) is None
    # Un rol de organización del equipo cubre todos los workspaces de esa organización.
    admin = _humano(10, {777})
    assert [autorizador.rol(admin, ORG, w) for w in (WS, "reportes", None)] == [Rol.org_admin] * 3
    assert autorizador.rol(admin, "otra", WS) is None
    # Persona y equipos: el mayor.
    assert autorizador.rol(_humano(9, {4242, 4243}), ORG, WS) == Rol.workspace_admin
    assert autorizador.rol(_humano(10, {4242, 4243}), ORG, "reportes") == Rol.desarrollador
    # Un actor de servicio no recibe equipos ni rol en ninguna org: solo alcanza ``graph.index``.
    servicio = Actor(
        tipo=TipoActor.servicio,
        canal=Canal.ci,
        oidc=OidcGithubActions(repositorio="acme/certificados-api", workflow="ci.yml"),
    )
    assert con_equipos(servicio, frozenset({777})) is servicio
    assert autorizador.rol(servicio, "otra", WS) is None
    # ``abierto`` solo rellena a quien no tiene ninguna asignación, ni propia ni de equipo.
    abierto = AutorizadorRoles(almacen, abierto=True)
    assert abierto.rol(_humano(10), ORG, WS) == Rol.desarrollador
    assert abierto.rol(luis, ORG, "reportes") == Rol.desarrollador
    assert abierto.rol(luis, ORG, WS) == Rol.lector


def test_actor_con_equipos_no_se_serializa_ni_entra_por_la_entrada():
    import warnings

    from apoyo_motor import JULIAN
    from railspec.contracts.repositorio import Auditoria
    from railspec.server.api.identidad import ActorConEquipos

    base = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=9, login="luis")
    actor = con_equipos(base, frozenset({4242}))
    assert isinstance(actor, ActorConEquipos) and actor.equipos == {4242}
    assert con_equipos(base, frozenset()) is base
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        assert actor.model_dump() == base.model_dump()
        assert "equipos" not in actor.model_dump_json() and "4242" not in actor.model_dump_json()
        # Dentro de otro contrato (estado, auditoría) tampoco sale.
        ahora = datetime.now(UTC)
        auditoria = Auditoria(creado_por=actor, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora)
        assert "equipos" not in auditoria.model_dump_json() and "4242" not in auditoria.model_dump_json()
    # El contrato ``Actor`` rechaza campos desconocidos: una entrada no puede declarar equipos.
    with pytest.raises(ValueError):
        Actor.model_validate({**base.model_dump(mode="json"), "equipos": [4242]})


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


def test_mcp_con_token_de_github_resuelve_roles_por_equipo():
    import httpx2
    from apoyo_github import APP, equipos_fijos, github_simulado, sin_permiso
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client
    from railspec.server.api.identidad import IdentidadCompuesta

    async def llamar(app, token: str):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=app),
            base_url="http://railspec",
            headers={"Authorization": f"Bearer {token}"},
        )
        async with http, streamable_http_client("http://railspec/mcp/", http_client=http) as flujos:
            async with ClientSession(flujos[0], flujos[1]) as sesion:
                await sesion.initialize()
                return await sesion.call_tool("unit_start", cuerpo_start())

    async def caso():
        motor, _ = construir()
        almacen = motor.n.almacen
        almacen.guardar_configuracion([_asignacion_equipo(4242, Rol.desarrollador, WS)])
        cliente_gh, _ = github_simulado(
            {
                "t-luis": ("luis", 9, equipos_fijos(4242)),
                "t-ana": ("ana", 7, equipos_fijos(31337)),
                "t-pablo": ("pablo", 11, sin_permiso),
            }
        )
        registro = Registro.del_motor(motor, AutorizadorRoles(almacen))
        app = aplicacion(registro, IdentidadCompuesta(IdentidadGithub(cliente_gh, app=APP), None))
        async with app.router.lifespan_context(app):
            r = await llamar(app, "t-luis")
            assert not r.is_error, r
            assert r.structured_content["estado"]["dueno"]["login"] == "luis"
            # Sin el equipo (otro equipo, o sin poder leer los suyos) no hay rol.
            for token in ("t-ana", "t-pablo"):
                r = await llamar(app, token)
                assert r.is_error and r.structured_content["codigo"] == "fuera-de-alcance", token

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
        """api.github.com: la comprobación de la GitHub App (M3; antes bastaba ``GET /user``) y equipos."""

        def get(self, url, headers):
            llamadas.append("teams")
            assert "/user/teams" in url
            return httpx.Response(200, json=[])

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
    assert llamadas == ["bueno", "teams"]  # una vez cada una: la segunda llamada sale del caché
    with pytest.raises(TokenInvalido):
        gh.actor_desde_token("malo", "arnes")


def test_identidad_github_resuelve_los_equipos_con_el_token_del_usuario():
    from apoyo_github import (
        APP,
        equipos_fijos,
        equipos_paginados,
        github_simulado,
        respuesta_inesperada,
        sin_permiso,
        sin_red,
    )
    from railspec.server.api.identidad import ActorConEquipos

    usuarios = {
        "t-luis": ("luis", 9, equipos_paginados([4242], [777])),
        "t-ana": ("ana", 7, equipos_fijos()),
        "t-sin-permiso": ("pablo", 11, sin_permiso),
        "t-rara": ("rita", 12, respuesta_inesperada),
        "t-sin-red": ("ruben", 13, sin_red),
    }
    cliente_gh, llamadas = github_simulado(usuarios)
    gh = IdentidadGithub(cliente_gh, app=APP)

    luis = gh.actor_desde_token("t-luis", "arnes")
    assert isinstance(luis, ActorConEquipos) and luis.github_id == 9
    assert luis.equipos == {4242, 777}  # sigue el Link de la segunda página
    assert llamadas == ["/applications/Iv1.x/token", "/user/teams", "/user/teams"]

    # Cada token tiene los suyos. Sin equipos, o sin poder leerlos (permiso, respuesta rara, red), el
    # actor es el de siempre: el token sigue autenticando y solo cuentan las asignaciones personales.
    for token, github_id in (("t-ana", 7), ("t-sin-permiso", 11), ("t-rara", 12), ("t-sin-red", 13)):
        actor = gh.actor_desde_token(token, "arnes")
        assert type(actor) is Actor and actor.github_id == github_id, token

    # Cacheado por token, y el token no queda guardado (solo su hash).
    antes = len(llamadas)
    assert gh.actor_desde_token("t-luis", "arnes").equipos == {4242, 777}
    assert len(llamadas) == antes
    assert not any("t-luis" in clave for clave in gh._verificador._equipos._datos)
    # Un token que GitHub rechaza no es actor, y no se le piden equipos.
    antes = len(llamadas)
    with pytest.raises(TokenInvalido):
        gh.actor_desde_token("t-malo", "arnes")
    assert llamadas[antes:] == ["/applications/Iv1.x/token"]


def test_identidad_github_refresca_los_equipos_al_vencer_el_cache():
    from apoyo_github import APP, equipos_fijos, github_simulado

    usuarios = {"t-luis": ("luis", 9, equipos_fijos(4242))}
    cliente_gh, _ = github_simulado(usuarios)
    gh = IdentidadGithub(cliente_gh, ttl_s=-1, app=APP)  # vencido de inmediato
    assert gh.actor_desde_token("t-luis", "arnes").equipos == {4242}
    # Lo sacan del equipo: en la próxima resolución ya no lo tiene.
    usuarios["t-luis"] = ("luis", 9, equipos_fijos())
    assert type(gh.actor_desde_token("t-luis", "arnes")) is Actor


def test_v1_tools_con_token_de_github_resuelve_roles_por_equipo():
    from apoyo_github import APP, equipos_fijos, github_simulado, sin_permiso
    from railspec.server.api.identidad import IdentidadCompuesta

    async def caso():
        motor, _ = construir()
        almacen = motor.n.almacen
        almacen.guardar_configuracion([_asignacion_equipo(4242, Rol.desarrollador, WS)])
        cliente_gh, _ = github_simulado(
            {
                "t-luis": ("luis", 9, equipos_fijos(4242)),
                "t-ana": ("ana", 7, equipos_fijos(31337)),
                "t-pablo": ("pablo", 11, sin_permiso),
            }
        )
        registro = Registro.del_motor(motor, AutorizadorRoles(almacen))
        app = aplicacion(registro, IdentidadCompuesta(IdentidadGithub(cliente_gh, app=APP), None))
        async with cliente(app) as c:
            r = await c.post(
                "/v1/tools/unit.start", json=cuerpo_start(), headers={"Authorization": "Bearer t-luis"}
            )
            assert r.status_code == 200, r.text
            assert r.json()["estado"]["dueno"]["login"] == "luis"
            # Sin el equipo (otro equipo, o sin poder leer los suyos) no hay rol.
            for token in ("t-ana", "t-pablo"):
                r = await c.post(
                    "/v1/tools/unit.start", json=cuerpo_start(), headers={"Authorization": f"Bearer {token}"}
                )
                assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance", token
        # Un token de desarrollo no trae equipos: la misma persona, sin asignación propia, no tiene rol.
        app = aplicacion(registro, IdentidadDesarrollo({"tk-luis": ("luis", 9)}))
        async with cliente(app) as c:
            r = await c.post(
                "/v1/tools/unit.start", json=cuerpo_start(), headers={"Authorization": "Bearer tk-luis"}
            )
            assert r.status_code == 403

    asyncio.run(caso())


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


def test_graph_query_solo_ve_los_repositorios_vinculados_al_workspace_pedido():
    """El workspace es el límite de visibilidad: un ``lector`` ve los repositorios vinculados a *su*
    workspace (los de otro workspace de la organización no entran) y no hay rol por repositorio."""

    from apoyo_motor import JULIAN, vinculo
    from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
    from railspec.contracts.tools import GraphQuerySalida
    from railspec.server.api.grafo import manejador_graph_query

    vistos = []

    class GrafoFalso:
        def consultar(self, entrada, visibles):
            vistos.append(sorted((v.workspace, v.repositorio) for v in visibles))
            return GraphQuerySalida(resultados=[], commits={})

    def en(workspace, repositorio):
        v = vinculo(NivelCodigo.interno)
        return v.model_copy(
            update={"alcance": AlcanceRepositorio(org=ORG, workspace=workspace, repositorio=repositorio)}
        )

    async def caso():
        motor, _ = construir()
        motor.n.almacen.guardar_configuracion(
            [en(WS, "certificados-api"), en(WS, "pagos-api"), en("otro-workspace", "secreto-api")]
        )
        extra = {"graph.query": manejador_graph_query(GrafoFalso(), motor.n.almacen)}
        registro = Registro.del_motor(motor, AutorizadorRoles(motor.n.almacen, abierto=True), extra)
        entrada = {
            "alcance": {"org": ORG, "workspace": WS},
            "consulta": {"verbo": "resolve", "nombre": "firmar"},
        }
        r = await registro.invocar("graph.query", entrada, JULIAN, Superficie.mcp)
        assert r.ok
        assert vistos == [[(WS, "certificados-api"), (WS, "pagos-api")]]

    asyncio.run(caso())
