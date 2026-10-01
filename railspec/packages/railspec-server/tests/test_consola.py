"""API de la consola web: sesión, roles, administración, configuración y exploración."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from apoyo_motor import JULIAN, ORG, REPO, WS, aprobar, avanzar, construir, entrada_start, reporte, snapshot
from railspec.contracts.comun import Actor, Canal, NivelCodigo, Proveedor, TipoActor
from railspec.contracts.repositorio import (
    AsignacionRol,
    Auditoria,
    Capacidades,
    ModeloCatalogo,
    Rol,
    SujetoEquipo,
    SujetoUsuario,
)
from railspec.contracts.snapshot import DeltaIndice, ModoDelta, MotorIndice, Simbolo, id_simbolo
from railspec.server.api import AutorizadorRoles, IdentidadDesarrollo, Registro
from railspec.server.api.identidad import IdentidadCompuesta, IdentidadGithub, TokenInvalido
from railspec.server.api.superficies import aplicacion
from railspec.server.consola import ConfigConsola, ConfigGithubApp, montar_consola
from railspec.server.consola.almacen import AlmacenConsola
from railspec.server.consola.contexto import ContextoConsola
from railspec.server.consola.github import ClienteGithub
from railspec.server.consola.sesion import COOKIE, Firmador, IdentidadConConsola
from railspec.server.proveedores import Proveedores

JULIAN_ID, ANA_ID, LUIS_ID = 83125327, 7, 9
TOKENS = {
    "tk-julian": ("juliancardonagaleano", JULIAN_ID),
    "tk-ana": ("ana", ANA_ID),
    "tk-luis": ("luis", LUIS_ID),
}
CSRF = {"X-Railspec-Consola": "1"}
SECRETO = "secreto-de-pruebas-de-la-consola-0123456789"
OTRO_SECRETO = "otro-secreto-de-pruebas-de-la-consola-9876"
AHORA = datetime(2026, 9, 30, 12, tzinfo=UTC)


def _auditoria():
    return Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA)


def asignar(
    almacen, rol: Rol, github_id: int | None = None, workspace: str | None = WS, equipo: int | None = None
):
    sujeto = (
        SujetoUsuario(github_id=github_id)
        if equipo is None
        else SujetoEquipo(github_org="acme", equipo="plataforma", equipo_id=equipo)
    )
    a = AsignacionRol(
        id=uuid.uuid4(),
        org=ORG,
        workspace=workspace,
        rol=rol,
        sujeto=sujeto,
        version=1,
        auditoria=_auditoria(),
    )
    AlmacenConsola(almacen.db).guardar_rol(a)
    return a


class Montaje:
    def __init__(
        self, admins=frozenset({JULIAN_ID}), github_http=None, nivel=NivelCodigo.restringido, **ctx_extra
    ):
        self.motor, _ = construir(nivel=nivel)
        self.almacen = self.motor.n.almacen
        self.reloj = lambda: datetime.now(UTC)
        self.firmador = Firmador(SECRETO)
        registro = Registro.del_motor(self.motor, AutorizadorRoles(self.almacen))
        identidad = IdentidadConConsola(IdentidadCompuesta(IdentidadDesarrollo(TOKENS), None), self.firmador)
        app_github = ConfigGithubApp("Iv1.cliente", "secreto-app") if github_http else None
        config = ConfigConsola(
            administradores=frozenset(admins), github_app=app_github, url_publica="https://railspec.test"
        )
        self.ctx = ContextoConsola(
            config=config,
            firmador=self.firmador,
            datos=AlmacenConsola(self.almacen.db),
            almacen=self.almacen,
            registro=registro,
            identidad=identidad,
            github=ClienteGithub(app_github, github_http),
            logins_desarrollo={login: gid for login, gid in TOKENS.values()},
            tokens_desarrollo=TOKENS,
            sse_intervalo_s=0.01,
            sse_duracion_max_s=0.2,
            **ctx_extra,
        )
        self.app = aplicacion(registro, identidad)
        montar_consola(self.app, self.ctx)

    @contextlib.asynccontextmanager
    async def cliente(self, token: str | None = None):
        # Sin lifespan: el MCP montado no hace falta y su gestor solo arranca una vez.
        transporte = httpx.ASGITransport(app=self.app)
        async with httpx.AsyncClient(transport=transporte, base_url="https://railspec.test") as c:
            if token is not None:
                r = await c.post("/consola/api/auth/desarrollo", json={"token": token})
                assert r.status_code == 204, r.text
            yield c


def correr(coro):
    return asyncio.run(coro)


async def _unidad_cerrada(m: Montaje, con_delta: bool = True):
    """Recorre una unidad completa; el reporte de implementar lleva un delta con un símbolo."""

    salida = await m.motor.start(entrada_start(), JULIAN)
    alcance = salida.estado.unidad
    for _ in range(20):
        av = await avanzar(m.motor, alcance)
        if av.tipo == "cerrada":
            break
        if av.tipo == "orden":
            r = reporte(av.orden)
            if av.orden.tipo == "implementar" and con_delta:
                r = r.model_copy(update={"snapshot": _snapshot_con_delta(av.orden)})
            await m.motor.report(r, JULIAN)
        elif av.tipo == "checkpoint":
            await aprobar(m.motor, alcance, av.checkpoint.id)
    return alcance


def _snapshot_con_delta(orden):
    base = snapshot(orden)
    simbolo = Simbolo(
        id=id_simbolo(REPO, "src/pdf.py", "funcion", "firmar_pdf"),
        nombre="firmar_pdf",
        tipo="funcion",
        ruta="src/pdf.py",
        linea_inicio=1,
        linea_fin=9,
        sha256=hashlib.sha256(b"x").hexdigest(),
    )
    delta = DeltaIndice(motor=MotorIndice(version="0.6.0"), simbolos_upsert=[simbolo])
    datos = base.model_dump()
    datos.update(modo_delta=ModoDelta.completo, delta_indice=delta.model_dump())
    return type(base).model_validate(datos)


# --- sesión ---------------------------------------------------------------------------------------


def test_login_desarrollo_cookie_y_csrf():
    async def caso():
        m = Montaje()
        async with m.cliente() as c:
            assert (await c.get("/consola/api/auth/config")).json() == {"github": False, "desarrollo": True}
            assert (await c.get("/consola/api/yo")).status_code == 401
            r = await c.post("/consola/api/auth/desarrollo", json={"token": "otro"})
            assert r.status_code == 401
            r = await c.post("/consola/api/auth/desarrollo", json={"token": "tk-ana"})
            assert r.status_code == 204
            cookie = r.headers["set-cookie"]
            assert "HttpOnly" in cookie and "Path=/consola" in cookie and "samesite=lax" in cookie.lower()
            yo = (await c.get("/consola/api/yo")).json()
            assert yo["login"] == "ana" and yo["plataforma_admin"] is False and yo["organizaciones"] == []
            # Escritura con cookie y sin cabecera anti-CSRF: rechazada.
            r = await c.post("/consola/api/auth/token")
            assert r.status_code == 403
            r = await c.post("/consola/api/auth/salir", headers=CSRF)
            assert r.status_code == 204
            assert (await c.get("/consola/api/yo")).status_code == 401

    correr(caso())


def test_token_api_vale_como_bearer_en_v1_y_no_en_consola():
    async def caso():
        m = Montaje()
        asignar(m.almacen, Rol.desarrollador, ANA_ID)
        async with m.cliente("tk-ana") as c:
            r = await c.post("/consola/api/auth/token", headers=CSRF)
            assert r.status_code == 200
            token = r.json()["token"]
            assert token.startswith("rsc1.")
            bearer = {"Authorization": f"Bearer {token}"}
        async with m.cliente() as c:
            r = await c.post(
                "/v1/tools/unit.start", json=entrada_start().model_dump(mode="json"), headers=bearer
            )
            assert r.status_code == 200, r.text
            assert r.json()["estado"]["dueno"]["login"] == "ana"
            assert r.json()["estado"]["dueno"]["canal"] == "consola"
            # El token api es la credencial de /v1 y del chat: en /consola/api no vale (la consola
            # usa la cookie; los scripts, un token de GitHub). Ver test_consola_sesion.py.
            assert (await c.get("/consola/api/yo", headers=bearer)).status_code == 401
        # Un token de sesión (cookie) no vale como Bearer en /v1.
        sesion = m.firmador.emitir("ana", ANA_ID, frozenset(), timedelta(hours=1), "sesion")
        with pytest.raises(TokenInvalido):
            m.ctx.identidad.actor_desde_token(sesion, "consola")
        vencido = Firmador(SECRETO, reloj=lambda: datetime.now(UTC) + timedelta(hours=2))
        with pytest.raises(TokenInvalido, match="expirado"):
            vencido.sesion(token, "api")
        with pytest.raises(TokenInvalido, match="firma"):
            Firmador(OTRO_SECRETO).sesion(token, "api")

    correr(caso())


def test_login_github_app_con_equipos():
    llamadas = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(str(peticion.url))
        if peticion.url.path == "/login/oauth/access_token":
            assert b"code=codigo-1" in peticion.content and b"client_secret=secreto-app" in peticion.content
            return httpx.Response(200, json={"access_token": "ghu_x"})
        if peticion.url.path == "/user":
            return httpx.Response(200, json={"login": "luis", "id": LUIS_ID})
        if peticion.url.path == "/user/teams":
            return httpx.Response(200, json=[{"id": 4242, "slug": "plataforma"}])
        return httpx.Response(404)

    async def caso():
        m = Montaje(github_http=httpx.Client(transport=httpx.MockTransport(responder)))
        asignar(m.almacen, Rol.workspace_admin, equipo=4242)
        async with m.cliente() as c:
            r = await c.get("/consola/api/auth/github/inicio", params={"volver": "//malo.example"})
            assert r.status_code == 302
            destino = httpx.URL(r.headers["location"])
            assert destino.host == "github.com" and destino.params["client_id"] == "Iv1.cliente"
            assert destino.params["redirect_uri"] == "https://railspec.test/consola/api/auth/github/callback"
            estado = destino.params["state"]
            # Sin la cookie de estado del mismo navegador, el callback se rechaza.
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=m.app), base_url="https://railspec.test"
            ) as otro:
                r = await otro.get(
                    "/consola/api/auth/github/callback", params={"code": "codigo-1", "state": estado}
                )
                assert r.status_code == 400
            r = await c.get("/consola/api/auth/github/callback", params={"code": "codigo-1", "state": estado})
            assert r.status_code == 302 and r.headers["location"] == "/consola/"
            assert m.ctx.config.nombre_cookie(COOKIE) in c.cookies
            yo = (await c.get("/consola/api/yo")).json()
            assert yo["login"] == "luis"
            # El rol llega por el equipo de GitHub (R3).
            assert yo["organizaciones"][0]["workspaces"] == [
                {"workspace": WS, "nombre": WS, "rol": "workspace-admin"}
            ]
        assert any("/user/teams" in u for u in llamadas)

    correr(caso())


# --- administración ---------------------------------------------------------------------------------


def test_organizacion_workspace_y_roles():
    async def caso():
        m = Montaje()
        async with m.cliente("tk-ana") as c:
            r = await c.post(
                "/consola/api/orgs",
                json={"id": ORG, "nombre": "ACME", "region_datos": "eastus2"},
                headers=CSRF,
            )
            assert r.status_code == 403
        async with m.cliente("tk-julian") as c:
            r = await c.post(
                "/consola/api/orgs",
                json={"id": ORG, "nombre": "ACME", "region_datos": "eastus2"},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            assert (
                r.json()["version"] == 1
                and r.json()["auditoria"]["creado_por"]["login"] == "juliancardonagaleano"
            )
            r = await c.post(
                "/consola/api/orgs",
                json={"id": ORG, "nombre": "otra", "region_datos": "eastus2"},
                headers=CSRF,
            )
            assert r.status_code == 409
            r = await c.put(
                f"/consola/api/orgs/{ORG}",
                json={"nombre": "ACME SA", "region_datos": "eastus2", "version": 1},
                headers=CSRF,
            )
            assert r.json()["version"] == 2
            r = await c.put(
                f"/consola/api/orgs/{ORG}",
                json={"nombre": "viejo", "region_datos": "eastus2", "version": 1},
                headers=CSRF,
            )
            assert r.status_code == 409 and r.json()["version_actual"] == 2
            r = await c.post(
                f"/consola/api/orgs/{ORG}/workspaces", json={"workspace": "org", "nombre": "x"}, headers=CSRF
            )
            assert r.status_code == 422
            r = await c.post(
                f"/consola/api/orgs/{ORG}/workspaces",
                json={"workspace": WS, "nombre": "Certificados"},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            # Asignar por login: el servidor resuelve el github_id.
            r = await c.post(
                f"/consola/api/orgs/{ORG}/roles",
                json={"workspace": WS, "rol": "lector", "sujeto": {"tipo": "usuario", "login": "ana"}},
                headers=CSRF,
            )
            assert r.status_code == 200 and r.json()["sujeto"] == {"tipo": "usuario", "github_id": ANA_ID}
            id_lector = r.json()["id"]
            r = await c.post(
                f"/consola/api/orgs/{ORG}/roles",
                json={"workspace": WS, "rol": "org-admin", "sujeto": {"tipo": "usuario", "github_id": 5}},
                headers=CSRF,
            )
            assert r.status_code == 422  # org-admin va a nivel organización
        async with m.cliente("tk-ana") as c:
            yo = (await c.get("/consola/api/yo")).json()
            assert yo["organizaciones"] == [
                {
                    "id": ORG,
                    "nombre": "ACME SA",
                    "rol": None,
                    "workspaces": [{"workspace": WS, "nombre": "Certificados", "rol": "lector"}],
                }
            ]
            # Lectora: no administra roles ni edita el workspace.
            assert (
                await c.get(f"/consola/api/orgs/{ORG}/roles", params={"workspace": WS})
            ).status_code == 403
            r = await c.put(
                f"/consola/api/orgs/{ORG}/workspaces/{WS}", json={"nombre": "x", "version": 1}, headers=CSRF
            )
            assert r.status_code == 403
        async with m.cliente("tk-julian") as c:
            assert (
                await c.delete(f"/consola/api/orgs/{ORG}/roles/{id_lector}", headers=CSRF)
            ).status_code == 204
            auditoria = (await c.get(f"/consola/api/orgs/{ORG}/workspaces/org/auditoria")).json()["registros"]
            assert [r["detalle"]["accion"] for r in auditoria] == ["editar", "crear"]

    correr(caso())


def test_ultimo_org_admin_no_se_quita():
    async def caso():
        m = Montaje(admins=frozenset())
        unico = asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
        async with m.cliente("tk-ana") as c:
            r = await c.delete(f"/consola/api/orgs/{ORG}/roles/{unico.id}", headers=CSRF)
            assert r.status_code == 409

    correr(caso())


def test_vinculo_nivel_motivo_y_desvinculo_auditado():
    async def caso():
        m = Montaje(nivel=None)
        asignar(m.almacen, Rol.workspace_admin, ANA_ID)
        asignar(m.almacen, Rol.desarrollador, LUIS_ID)
        base = f"/consola/api/orgs/{ORG}/workspaces/{WS}/repositorios"
        cuerpo = {"url": "https://github.com/acme/certificados-api", "rol": "primario"}
        async with m.cliente("tk-luis") as c:
            assert (await c.put(f"{base}/{REPO}", json=cuerpo, headers=CSRF)).status_code == 403
        async with m.cliente("tk-ana") as c:
            r = await c.put(f"{base}/{REPO}", json=cuerpo, headers=CSRF)
            assert r.status_code == 200, r.text
            v = r.json()
            assert (
                v["nivel_codigo"] == "restringido"
                and v["chat_contexto_codigo"]["hosting"] == "azure-zona-datos"
            )
            # El motor lee el mismo vínculo.
            assert m.almacen.vinculos(ORG, WS)[0].url == cuerpo["url"]
            r = await c.put(f"{base}/{REPO}", json=cuerpo, headers=CSRF)
            assert r.status_code == 409  # crear de nuevo sin version
            abierto = cuerpo | {"nivel_codigo": "abierto", "version": 1}
            r = await c.put(f"{base}/{REPO}", json=abierto, headers=CSRF)
            assert r.status_code == 422  # falta motivo
            r = await c.put(f"{base}/{REPO}", json=abierto | {"motivo": "repo público"}, headers=CSRF)
            assert r.status_code == 200 and r.json()["chat_contexto_codigo"]["hosting"] == "cualquiera"
            # Política incoherente con el nivel: la rechaza el contrato.
            politica = r.json()["chat_contexto_codigo"]
            r = await c.put(
                f"{base}/{REPO}",
                json=cuerpo
                | {"version": 2, "nivel_codigo": "interno", "motivo": "x", "chat_contexto_codigo": politica},
                headers=CSRF,
            )
            assert r.status_code == 422
            assert (await c.delete(f"{base}/{REPO}", headers=CSRF)).status_code == 422
            assert (
                await c.delete(f"{base}/{REPO}", params={"motivo": "migrado"}, headers=CSRF)
            ).status_code == 204
            assert (await c.get(base)).json() == []
            registros = (await c.get(f"/consola/api/orgs/{ORG}/workspaces/{WS}/auditoria")).json()[
                "registros"
            ]
            assert [r["evento"] for r in registros] == [
                "desvinculo-repositorio",
                "cambio-nivel",
                "cambio-configuracion",
            ]
            assert registros[1]["detalle"] == {"de": "restringido", "a": "abierto", "motivo": "repo público"}
            assert registros[0]["actor"]["canal"] == "consola"
            filtrados = (
                await c.get(
                    f"/consola/api/orgs/{ORG}/workspaces/{WS}/auditoria", params={"evento": "cambio-nivel"}
                )
            ).json()["registros"]
            assert len(filtrados) == 1
            pagina = (
                await c.get(f"/consola/api/orgs/{ORG}/workspaces/{WS}/auditoria", params={"limite": 2})
            ).json()
            assert len(pagina["registros"]) == 2 and pagina["cursor_siguiente"]
            resto = (
                await c.get(
                    f"/consola/api/orgs/{ORG}/workspaces/{WS}/auditoria",
                    params={"limite": 2, "cursor": pagina["cursor_siguiente"]},
                )
            ).json()
            assert [r["evento"] for r in resto["registros"]] == ["cambio-configuracion"]

    correr(caso())


# --- configuración ----------------------------------------------------------------------------------


PERFIL = {
    "roles": {"redactor": {"modelo": {"foundry": "gpt-5"}, "effort": "high", "structured_outputs": True}},
    "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
    "exploradores": {"bajo": 1},
}


def test_perfil_validado_contra_catalogo_y_presupuesto():
    async def caso():
        m = Montaje()
        async with m.cliente("tk-julian") as c:
            r = await c.put(f"/consola/api/orgs/{ORG}/perfiles/estandar", json=PERFIL, headers=CSRF)
            assert r.status_code == 200, r.text
            assert r.json()["avisos"] == ["redactor: sin catálogo de foundry; gpt-5 no se pudo validar"]
            m.almacen.db.catalogo.insert_one(
                ModeloCatalogo(
                    org=ORG,
                    proveedor=Proveedor.foundry,
                    modelo="gpt-5",
                    hosting="azure",
                    region="eastus2",
                    capacidades=Capacidades(
                        efforts=["low"], structured_outputs=True, contexto_max_tokens=200000
                    ),
                    leido_en=AHORA,
                ).model_dump(mode="json")
            )
            r = await c.put(
                f"/consola/api/orgs/{ORG}/perfiles/estandar", json=PERFIL | {"version": 1}, headers=CSRF
            )
            assert r.status_code == 422 and "effort high" in r.json()["detalle"]
            sin_effort = {**PERFIL, "roles": {"redactor": {"modelo": {"foundry": "gpt-5"}}}, "version": 1}
            r = await c.put(
                f"/consola/api/orgs/{ORG}/perfiles/estandar",
                params={"workspace": WS},
                json=sin_effort | {"version": None},
                headers=CSRF,
            )
            assert r.status_code == 200 and r.json()["avisos"] == []
            # El motor ve el perfil del workspace antes que el de la organización.
            from railspec.contracts.comun import AlcanceWorkspace, Perfil

            assert m.almacen.perfil(AlcanceWorkspace(org=ORG, workspace=WS), Perfil.estandar).workspace == WS
            assert (
                len((await c.get(f"/consola/api/orgs/{ORG}/perfiles", params={"workspace": WS})).json()) == 2
            )
            assert (await c.get(f"/consola/api/orgs/{ORG}/catalogo")).json()[0]["modelo"] == "gpt-5"
            assert (
                await c.post(f"/consola/api/orgs/{ORG}/catalogo/sincronizar", headers=CSRF)
            ).status_code == 501
            r = await c.put(
                f"/consola/api/orgs/{ORG}/presupuestos",
                params={"workspace": WS},
                json={"por_unidad": {"costo_usd_max": 5}, "mensual_usd": 100},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            r = await c.put(
                f"/consola/api/orgs/{ORG}/proveedores-contexto/gobernanza/pce",
                params={"workspace": WS},
                json={"url": "https://pce.example", "politica_fallo": "blanda"},
                headers=CSRF,
            )
            assert r.status_code == 422  # gobernanza siempre estricta
            r = await c.put(
                f"/consola/api/orgs/{ORG}/proveedores-contexto/gobernanza/pce",
                params={"workspace": WS},
                json={
                    "url": "https://pce.example",
                    "politica_fallo": "estricta",
                    "credencial_ref": "secret://pce/api-key",
                },
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            lista = (
                await c.get(f"/consola/api/orgs/{ORG}/proveedores-contexto", params={"workspace": WS})
            ).json()
            assert [p["nombre"] for p in lista] == ["pce"]
            r = await c.delete(
                f"/consola/api/orgs/{ORG}/proveedores-contexto/gobernanza/pce",
                params={"workspace": WS},
                headers=CSRF,
            )
            assert r.status_code == 204

    correr(caso())


# --- exploración y estadísticas ---------------------------------------------------------------------------


def test_tools_por_la_consola_con_rol_de_equipo():
    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.desarrollador, equipo=4242)
        sesion_luis = m.firmador.emitir("luis", LUIS_ID, frozenset({4242}), timedelta(hours=1), "sesion")
        async with m.cliente() as c:
            nombre = m.ctx.config.nombre_cookie(COOKIE)
            c.cookies.set(nombre, sesion_luis, domain="railspec.test", path="/consola")
            r = await c.post(
                "/consola/api/tools/unit.start", json=entrada_start().model_dump(mode="json"), headers=CSRF
            )
            assert r.status_code == 200, r.text
            unidad = r.json()["estado"]["unidad"]
            r = await c.post(
                "/consola/api/tools/unit.list", json={"alcance": {"org": ORG, "workspace": WS}}, headers=CSRF
            )
            assert [u["unidad"] for u in r.json()["unidades"]] == [unidad["unidad"]]
            # Fuera de su workspace: el registro rechaza.
            r = await c.post(
                "/consola/api/tools/unit.list",
                json={"alcance": {"org": ORG, "workspace": "otro"}},
                headers=CSRF,
            )
            assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance"
            nombres = {t["name"] for t in (await c.get("/consola/api/tools")).json()["tools"]}
            assert "unit.approve" in nombres and "unit.report" not in nombres
        # Un actor sin equipos (token de desarrollo) solo tiene lo asignado a la persona.
        actor = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=LUIS_ID, login="luis")
        assert AutorizadorRoles(m.almacen).rol(actor, ORG, WS) is None

    correr(caso())


# --- roles por equipo en /v1/tools, MCP y chat (token rsc1 con equipos firmados) -------------------------


def _bearer_rsc1(m: Montaje, github_id: int, login: str, equipos, vida=timedelta(hours=1)) -> dict[str, str]:
    """Lo que la SPA obtiene de ``POST /consola/api/auth/token``: el token lleva los equipos del login."""

    token = m.firmador.emitir(login, github_id, frozenset(equipos), vida, "api")
    return {"Authorization": f"Bearer {token}"}


LISTAR = {"alcance": {"org": ORG, "workspace": WS}}


def test_v1_tools_resuelve_roles_por_equipo_con_el_token_rsc1():
    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.desarrollador, equipo=4242)
        async with m.cliente() as c:
            luis = _bearer_rsc1(m, LUIS_ID, "luis", {4242, 11})
            entrada = entrada_start().model_dump(mode="json")
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=luis)
            assert r.status_code == 200, r.text
            assert r.json()["estado"]["dueno"]["login"] == "luis"
            assert "equipos" not in r.json()["estado"]["dueno"]
            assert (await c.post("/v1/tools/unit.list", json=LISTAR, headers=luis)).status_code == 200
            # Los equipos son del token, no del actor persistido: no quedan en ninguna colección.
            volcado = json.dumps(
                [d for n in m.almacen.db.list_collection_names() for d in m.almacen.db[n].find()], default=str
            )
            assert '"equipos"' not in volcado

            # Sin el equipo no hay rol: ni sin equipos, ni con equipos ajenos, aunque sea la misma persona.
            for equipos in (set(), {9999}):
                sin = _bearer_rsc1(m, LUIS_ID, "luis", equipos)
                r = await c.post("/v1/tools/unit.list", json=LISTAR, headers=sin)
                assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance", equipos
            # Otra persona con el mismo equipo sí lo tiene (el rol es del equipo, no de quien lo asignó).
            ana = _bearer_rsc1(m, ANA_ID, "ana", {4242})
            assert (await c.post("/v1/tools/unit.list", json=LISTAR, headers=ana)).status_code == 200

    correr(caso())


def test_v1_tools_roles_por_equipo_aislados_por_org_y_workspace_y_se_acotan_por_rol():
    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.lector, equipo=4242)  # lector en WS
        asignar(m.almacen, Rol.org_admin, workspace=None, equipo=777)  # org-admin en toda ORG
        entrada = entrada_start().model_dump(mode="json")
        async with m.cliente() as c:
            lector = _bearer_rsc1(m, LUIS_ID, "luis", {4242})
            assert (await c.post("/v1/tools/unit.list", json=LISTAR, headers=lector)).status_code == 200
            # El rol del equipo se respeta: un lector no arranca unidades.
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=lector)
            assert r.status_code == 403 and "desarrollador" in r.json()["detalle"]
            # Otro workspace u otra organización: nada, aunque exista rol en el suyo.
            for alcance in ({"org": ORG, "workspace": "otro"}, {"org": "otra-org", "workspace": WS}):
                r = await c.post("/v1/tools/unit.list", json={"alcance": alcance}, headers=lector)
                assert r.status_code == 403, alcance
            # Un rol de equipo de organización vale en todos sus workspaces, pero no en otra organización.
            admin = _bearer_rsc1(m, ANA_ID, "ana", {777})
            for alcance in (LISTAR["alcance"], {"org": ORG, "workspace": "otro"}):
                r = await c.post("/v1/tools/unit.list", json={"alcance": alcance}, headers=admin)
                assert r.status_code == 200, alcance
            otra = {"alcance": {"org": "otra-org", "workspace": WS}}
            assert (await c.post("/v1/tools/unit.list", json=otra, headers=admin)).status_code == 403
            # Persona y equipo suman: gana el rol mayor.
            asignar(m.almacen, Rol.desarrollador, LUIS_ID)
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=lector)
            assert r.status_code == 200, r.text

    correr(caso())


def test_v1_tools_token_vencido_o_alterado_no_concede_nada():
    def con(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.desarrollador, equipo=4242)
        entrada = entrada_start().model_dump(mode="json")
        async with m.cliente() as c:
            # Vencido: aunque lleve el equipo, no llega a ser actor (401), y no se crea nada.
            pasado = Firmador(SECRETO, reloj=lambda: datetime.now(UTC) - timedelta(hours=2))
            vencido = pasado.emitir("luis", LUIS_ID, frozenset({4242}), timedelta(hours=1), "api")
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=con(vencido))
            assert r.status_code == 401 and "expirado" in r.json()["detalle"]
            # Equipos agregados a mano a un token de una persona sin ellos: la firma ya no cuadra.
            valido = _bearer_rsc1(m, LUIS_ID, "luis", set())["Authorization"].removeprefix("Bearer ")
            prefijo, carga, firma = valido.split(".")
            datos = json.loads(base64.urlsafe_b64decode(carga + "=" * (-len(carga) % 4)))
            datos["e"] = [4242]
            falsa = base64.urlsafe_b64encode(json.dumps(datos, separators=(",", ":")).encode()).decode()
            r = await c.post(
                "/v1/tools/unit.start", json=entrada, headers=con(f"{prefijo}.{falsa.rstrip('=')}.{firma}")
            )
            assert r.status_code == 401 and "firma" in r.json()["detalle"]
            # Token de otra instalación (otro secreto) con el equipo: tampoco.
            ajeno = Firmador(OTRO_SECRETO).emitir(
                "luis", LUIS_ID, frozenset({4242}), timedelta(hours=1), "api"
            )
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=con(ajeno))
            assert r.status_code == 401
            # La cookie de sesión (lleva equipos) no vale como Bearer.
            sesion = m.firmador.emitir("luis", LUIS_ID, frozenset({4242}), timedelta(hours=1), "sesion")
            r = await c.post("/v1/tools/unit.start", json=entrada, headers=con(sesion))
            assert r.status_code == 401
            # Nada de lo anterior creó unidades.
            listar = _bearer_rsc1(m, LUIS_ID, "luis", {4242})
            r = await c.post("/v1/tools/unit.list", json=LISTAR, headers=listar)
            assert r.status_code == 200 and r.json()["unidades"] == []

    correr(caso())


def test_mcp_no_acepta_el_token_rsc1_de_la_consola_aunque_lleve_el_equipo():
    import httpx2
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    async def llamar(m: Montaje, cabecera: dict[str, str]):
        http = httpx2.AsyncClient(
            transport=httpx2.ASGITransport(app=m.app), base_url="http://railspec", headers=cabecera
        )
        async with http, streamable_http_client("http://railspec/mcp/", http_client=http) as flujos:
            async with ClientSession(flujos[0], flujos[1]) as sesion:
                await sesion.initialize()
                return await sesion.call_tool("unit_start", entrada_start().model_dump(mode="json"))

    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.desarrollador, equipo=4242)
        async with m.app.router.lifespan_context(m.app):
            # El token ``api`` es la credencial de /v1 y del chat (canal consola), no del arnés: por
            # MCP no abre nada, ni con el equipo que da rol. Los roles por equipo por MCP llegan con
            # un token de GitHub de la App (ver test_api).
            for equipos in ({4242}, set()):
                r = await llamar(m, _bearer_rsc1(m, LUIS_ID, "luis", equipos))
                assert r.is_error and "canal consola" in r.content[0].text

    correr(caso())


def test_chat_resuelve_roles_por_equipo_con_el_token_rsc1():
    from railspec.server.chat.almacen import AlmacenChat
    from railspec.server.chat.http import router_chat
    from railspec.server.chat.servicio import ConfigChat, ServicioChat

    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.lector, equipo=4242)
        servicio = ServicioChat(
            almacen=m.almacen,
            chat=AlmacenChat(m.almacen.db),
            registro=m.ctx.registro,
            autorizador=AutorizadorRoles(m.almacen),
            proveedores=Proveedores({}),
            config=ConfigChat(),
        )
        m.app.include_router(router_chat(servicio, m.ctx.identidad))
        cuerpo = {"alcance": {"org": ORG, "workspace": WS}}
        async with m.cliente() as c:
            # Con el equipo (lector) se puede abrir una conversación; sin él, 403.
            con_equipo = _bearer_rsc1(m, LUIS_ID, "luis", {4242})
            r = await c.post("/v1/chat/conversaciones", json=cuerpo, headers=con_equipo)
            assert r.status_code == 201, r.text
            assert r.json()["conversacion"]["autor"]["github_id"] == LUIS_ID
            sin_equipo = _bearer_rsc1(m, LUIS_ID, "luis", set())
            r = await c.post("/v1/chat/conversaciones", json=cuerpo, headers=sin_equipo)
            assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance"

    correr(caso())


def test_bearer_de_github_en_la_consola_trae_los_equipos_del_usuario():
    from apoyo_github import APP, equipos_fijos, github_simulado

    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.workspace_admin, equipo=4242)
        cliente_gh, _ = github_simulado(
            {"t-luis": ("luis", LUIS_ID, equipos_fijos(4242)), "t-ana": ("ana", ANA_ID, equipos_fijos(1))}
        )
        m.ctx.identidad = IdentidadConConsola(
            IdentidadCompuesta(IdentidadGithub(cliente_gh, app=APP), None), m.firmador
        )
        async with m.cliente() as c:
            yo = (await c.get("/consola/api/yo", headers={"Authorization": "Bearer t-luis"})).json()
            assert yo["organizaciones"][0]["workspaces"] == [
                {"workspace": WS, "nombre": WS, "rol": "workspace-admin"}
            ]
            yo = (await c.get("/consola/api/yo", headers={"Authorization": "Bearer t-ana"})).json()
            assert yo["organizaciones"] == []
            # Los equipos resueltos también valen para las tools por la consola.
            r = await c.post(
                "/consola/api/tools/unit.list", json=LISTAR, headers={"Authorization": "Bearer t-luis"}
            )
            assert r.status_code == 200, r.text
            r = await c.post(
                "/consola/api/tools/unit.list", json=LISTAR, headers={"Authorization": "Bearer t-ana"}
            )
            assert r.status_code == 403

    correr(caso())


def test_aprobar_checkpoint_desde_la_consola_y_gana_la_primera():
    async def caso():
        m = Montaje()
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        av = await avanzar(m.motor, alcance)
        await m.motor.report(reporte(av.orden), JULIAN)
        av = await avanzar(m.motor, alcance)
        assert av.tipo == "checkpoint"
        cuerpo = {
            "unidad": alcance.model_dump(mode="json", exclude_none=True),
            "checkpoint": str(av.checkpoint.id),
            "decision": "aprobado",
        }
        async with m.cliente("tk-julian") as c:
            detalle = (
                await c.get(f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}")
            ).json()
            assert detalle["estado"]["checkpoint_pendiente"]["id"] == str(av.checkpoint.id)
            r = await c.post("/consola/api/tools/unit.approve", json=cuerpo, headers=CSRF)
            assert r.status_code == 200, r.text
            r = await c.post("/consola/api/tools/unit.approve", json=cuerpo, headers=CSRF)
            assert r.status_code == 409 and r.json()["codigo"] == "checkpoint-ya-resuelto"
        estado = m.almacen.obtener_estado(alcance)
        assert estado.resoluciones[0].actor.canal == Canal.consola

    correr(caso())


def test_detalle_linea_de_tiempo_trazabilidad_y_resumen():
    async def caso():
        m = Montaje()
        alcance = await _unidad_cerrada(m)
        base = f"/consola/api/orgs/{ORG}/workspaces/{WS}"
        asignar(m.almacen, Rol.lector, ANA_ID)
        async with m.cliente("tk-ana") as c:
            r = await c.get(f"{base}/unidades/{alcance.unidad}")
            assert r.status_code == 200 and r.json()["estado"]["fase"] == "done"
            assert (await c.get(f"{base}/unidades/0099-no-existe")).status_code == 404
            assert (await c.get(f"{base}/unidades/NO")).status_code == 422
            linea = (await c.get(f"{base}/unidades/{alcance.unidad}/linea-de-tiempo")).json()
            tipos = [o["tipo"] for o in linea["ordenes"]]
            assert tipos == ["redactar", "redactar", "redactar", "implementar", "validar"]
            implementar = linea["ordenes"][3]
            assert implementar["tareas"] and implementar["reporte"]["archivos"] == [
                {"ruta": "src/pdf.py", "estado": "agregado"}
            ]
            # Nunca sale texto de instrucciones, plantilla ni contexto de una orden.
            assert all(not ({"instrucciones", "plantilla", "contexto"} & set(o)) for o in linea["ordenes"])
            emitidos = [e["emitido_en"] for e in linea["eventos"]]
            assert emitidos == sorted(emitidos) and len(linea["eventos"]) > 5
            traza = (await c.get(f"{base}/unidades/{alcance.unidad}/trazabilidad")).json()
            assert traza["criterios"], traza
            ca = traza["criterios"][0]
            assert ca["id"].startswith("CA-") and ca["tareas"][0]["completada"] is True
            assert ca["archivos"] == [{"repositorio": REPO, "ruta": "src/pdf.py", "estado": "agregado"}]
            assert ca["simbolos"][0]["nombre"] == "firmar_pdf"
            resumen = (await c.get(f"{base}/resumen")).json()
            assert resumen["unidades"]["total"] == 1 and resumen["unidades"]["por_fase"]["done"] == 1
            assert resumen["gates"]["codigo"]["aprobado"] == 1 and resumen["gates"]["spec"]["total"] == 1
            assert resumen["grafo"] == [
                {"repositorio": REPO, "nivel_codigo": "restringido", "rol": "primario", "commit": None}
            ]
            assert resumen["gasto"]["presupuesto_mensual_usd"] is None
            # Integrar exige desarrollador: una lectora no puede.
            r = await c.post(
                "/consola/api/tools/unit.integrate",
                json={
                    "unidad": alcance.model_dump(mode="json", exclude_none=True),
                    "especificacion_viva": "specs/pdf.md",
                },
                headers=CSRF,
            )
            assert r.status_code == 403

    correr(caso())


def test_eventos_en_vivo_sse_y_last_event_id():
    async def caso():
        m = Montaje()
        salida = await m.motor.start(entrada_start(), JULIAN)
        alcance = salida.estado.unidad
        await avanzar(m.motor, alcance)
        url = f"/consola/api/orgs/{ORG}/workspaces/{WS}/unidades/{alcance.unidad}/eventos"
        async with m.cliente("tk-julian") as c:
            r = await c.get(url)
            assert r.headers["content-type"].startswith("text/event-stream")
            bloques = [b for b in r.text.split("\n\n") if b.startswith("event: sync")]
            assert bloques
            ultimo = bloques[-1].split("\n")[1].removeprefix("id: ")
            remoto, _, local = ultimo.partition(":")
            assert int(remoto) == len(bloques) and local == "0"
            r = await c.get(url, headers={"Last-Event-ID": ultimo})
            assert "event: sync" not in r.text

    correr(caso())


def test_spa_estatica_con_vuelta_a_index(tmp_path):
    (tmp_path / "assets").mkdir()
    (tmp_path / "index.html").write_text("<!doctype html><title>Railspec</title>")
    (tmp_path / "assets" / "app.js").write_text("console.log(1)")

    async def caso():
        m = Montaje()
        m.ctx.config = ConfigConsola(carpeta_spa=str(tmp_path))
        from railspec.server.api.superficies import aplicacion as app_base

        app = app_base(m.ctx.registro, m.ctx.identidad)
        montar_consola(app, m.ctx)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://railspec.test"
        ) as c:
            assert (await c.get("/consola")).status_code == 308
            r = await c.get("/consola/acme/certificados/unidades")
            assert r.status_code == 200 and "Railspec" in r.text and r.headers["cache-control"] == "no-cache"
            r = await c.get("/consola/assets/app.js")
            assert "immutable" in r.headers["cache-control"]
            r = await c.get("/consola/../../etc/passwd")
            assert "Railspec" in r.text or r.status_code == 404
            assert (await c.get("/consola/api/auth/config")).status_code == 200

    correr(caso())


def test_configuracion_desde_entorno():
    c = ConfigConsola.desde_entorno(
        {
            "RAILSPEC_GITHUB_APP_CLIENT_ID": "Iv1.x",
            "RAILSPEC_GITHUB_APP_CLIENT_SECRET": "s",
            "RAILSPEC_CONSOLA_URL": "https://railspec.acme.com/",
            "RAILSPEC_CONSOLA_ADMINS": "83125327, 7",
            "RAILSPEC_CONSOLA_SECRETO": SECRETO,
        }
    )
    assert c.github_app == ConfigGithubApp("Iv1.x", "s") and c.administradores == {83125327, 7}
    assert c.url_publica == "https://railspec.acme.com" and c.cookie_segura
    with pytest.raises(ValueError, match="RAILSPEC_CONSOLA_URL"):
        ConfigConsola.desde_entorno(
            {"RAILSPEC_GITHUB_APP_CLIENT_ID": "a", "RAILSPEC_GITHUB_APP_CLIENT_SECRET": "s"}
        )
    with pytest.raises(ValueError, match="numéricos"):
        ConfigConsola.desde_entorno({"RAILSPEC_CONSOLA_ADMINS": "julian"})
    assert ConfigConsola.desde_entorno({}) == ConfigConsola()
