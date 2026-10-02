"""Paridad de la autorización entre el arnés (MCP y /v1) y la consola.

Los roles por equipo de GitHub (R3) valen igual por todos los caminos porque todos resuelven el rol con la
misma función (``api.roles.rol_efectivo``). Aquí se comprueba comparando los dos autorizadores sobre las
mismas asignaciones, y de punta a punta con un mismo token de GitHub por ``/v1/tools`` y ``/consola/api``.
Lo único que la consola añade (administrar la plataforma) queda fuera del arnés a propósito.
"""

from __future__ import annotations

import asyncio
import contextlib
import itertools
import uuid
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from apoyo_github import (
    APP,
    equipos_fijos,
    equipos_paginados,
    github_simulado,
    sin_permiso,
)
from apoyo_motor import JULIAN, ORG, WS, construir, entrada_start
from railspec.contracts.comun import Actor, Canal, TipoActor
from railspec.contracts.repositorio import AsignacionRol, Auditoria, Rol, SujetoEquipo, SujetoUsuario
from railspec.contracts.tools import Superficie
from railspec.server.api import AutorizadorRoles, Registro
from railspec.server.api.identidad import (
    ActorConEquipos,
    IdentidadCompuesta,
    IdentidadGithub,
    con_equipos,
)
from railspec.server.api.roles import alcanza, rol_efectivo
from railspec.server.api.superficies import aplicacion
from railspec.server.consola import ConfigConsola, montar_consola
from railspec.server.consola.almacen import AlmacenConsola
from railspec.server.consola.contexto import AutorizadorConsola, ContextoConsola
from railspec.server.consola.github import ClienteGithub
from railspec.server.consola.sesion import Firmador, IdentidadConConsola, Sesion
from railspec.server.estado import almacen_en_memoria
from test_consola import CSRF, JULIAN_ID, LUIS_ID, SECRETO, Montaje, asignar

AHORA = datetime(2026, 9, 30, 12, tzinfo=UTC)
LISTAR = {"alcance": {"org": ORG, "workspace": WS}}


def correr(coro):
    return asyncio.run(coro)


def _asignacion(rol: Rol, *, org=ORG, workspace=None, github_id=1, equipo=None) -> AsignacionRol:
    sujeto = (
        SujetoUsuario(github_id=github_id)
        if equipo is None
        else SujetoEquipo(github_org="acme", equipo=f"equipo-{equipo}", equipo_id=equipo)
    )
    return AsignacionRol(
        id=uuid.uuid4(),
        org=org,
        workspace=workspace,
        rol=rol,
        sujeto=sujeto,
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA),
    )


# --- la misma función ------------------------------------------------------------------------------------


def test_rol_efectivo_es_el_mayor_de_la_organizacion_y_del_workspace():
    org_admin = _asignacion(Rol.org_admin)  # org-admin solo existe a nivel organización
    dev = _asignacion(Rol.desarrollador, workspace=WS)
    ws_admin = _asignacion(Rol.workspace_admin, workspace=WS)
    otro_ws = _asignacion(Rol.workspace_admin, workspace="otro")
    assert rol_efectivo([], WS) is None
    assert rol_efectivo([dev], WS) == Rol.desarrollador
    assert rol_efectivo([dev, ws_admin], WS) == Rol.workspace_admin
    assert rol_efectivo([dev, otro_ws], WS) == Rol.desarrollador  # el de otro workspace no cuenta
    assert rol_efectivo([dev, org_admin], WS) == Rol.org_admin
    assert rol_efectivo([ws_admin], None) is None  # a nivel organización solo valen las de la organización
    assert rol_efectivo([org_admin], None) == Rol.org_admin
    assert rol_efectivo([], WS, abierto=True) == Rol.desarrollador
    assert rol_efectivo([otro_ws], WS, abierto=True) == Rol.desarrollador
    assert alcanza(Rol.workspace_admin, Rol.desarrollador) and not alcanza(Rol.lector, Rol.desarrollador)
    assert not alcanza(None, Rol.lector)


# --- los dos autorizadores, sobre las mismas asignaciones -------------------------------------------------

ASIGNACIONES = [
    _asignacion(Rol.lector, workspace=WS, equipo=100),
    _asignacion(Rol.desarrollador, workspace=WS, equipo=200),
    _asignacion(Rol.workspace_admin, workspace="otro", github_id=7),
    _asignacion(Rol.org_admin, org="otra-org", equipo=300),
    _asignacion(Rol.lector, workspace=WS, github_id=11),
    _asignacion(Rol.org_admin, equipo=400),  # a nivel organización
]
PERSONAS = [  # (github_id, equipos que resolvió su identidad)
    (7, frozenset()),
    (7, frozenset({100})),
    (9, frozenset({200})),
    (9, frozenset({100, 200})),
    (11, frozenset()),
    (11, frozenset({300})),
    (12, frozenset({999})),
    (13, frozenset({400, 200})),
]
ALCANCES = [(ORG, WS), (ORG, "otro"), (ORG, None), ("otra-org", WS), ("sin-org", None)]


@pytest.mark.parametrize("abierto", [False, True])
def test_arnes_y_consola_dan_el_mismo_rol_con_las_mismas_asignaciones(abierto):
    m = Montaje(admins=frozenset())
    m.almacen.guardar_configuracion(ASIGNACIONES)
    m.ctx.abierto = abierto
    arnes = AutorizadorRoles(m.almacen, abierto=abierto)
    vistos: set[Rol | None] = set()
    for (github_id, equipos), (org, ws) in itertools.product(PERSONAS, ALCANCES):
        base = Actor(tipo=TipoActor.humano, canal=Canal.arnes, github_id=github_id, login=f"u{github_id}")
        sesion = Sesion(f"u{github_id}", github_id, AHORA, equipos)
        consola = AutorizadorConsola(m.ctx.permisos(sesion))
        desde_arnes = arnes.rol(con_equipos(base, equipos), org, ws)
        desde_consola = consola.rol(sesion.actor(), org, ws)
        assert desde_arnes == desde_consola, (github_id, sorted(equipos), org, ws)
        vistos.add(desde_arnes)
    # La matriz no es trivial: cubre sin rol y los cuatro roles.
    assert vistos >= {None, Rol.lector, Rol.desarrollador, Rol.workspace_admin, Rol.org_admin} or abierto


def test_quien_administra_la_plataforma_solo_lo_es_en_la_consola():
    m = Montaje(admins=frozenset({JULIAN_ID}))
    sesion = Sesion("juliancardonagaleano", JULIAN_ID, AHORA)
    assert m.ctx.permisos(sesion).rol(ORG, WS) == Rol.org_admin
    assert AutorizadorRoles(m.almacen).rol(JULIAN, ORG, WS) is None

    async def caso():
        async with m.cliente("tk-julian") as c:
            # Por la consola administra: org-admin en todas.
            assert (
                await c.post("/consola/api/tools/unit.list", json=LISTAR, headers=CSRF)
            ).status_code == 200
            # Por el arnés (/v1, el mismo registro que MCP) necesita un rol asignado como cualquiera.
            r = await c.post(
                "/v1/tools/unit.list", json=LISTAR, headers={"Authorization": "Bearer tk-julian"}
            )
            assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance"
            asignar(m.almacen, Rol.desarrollador, JULIAN_ID)
            r = await c.post(
                "/v1/tools/unit.list", json=LISTAR, headers={"Authorization": "Bearer tk-julian"}
            )
            assert r.status_code == 200

    correr(caso())


# --- un mismo token de GitHub por /v1 y por la consola ----------------------------------------------------


@contextlib.asynccontextmanager
async def _servidor(usuarios):
    motor, _ = construir()
    almacen = motor.n.almacen
    almacen.guardar_configuracion(
        [
            _asignacion(Rol.desarrollador, workspace=WS, equipo=4242),
            _asignacion(Rol.lector, workspace="solo-lectura", equipo=4242),
        ]
    )
    cliente_gh, llamadas = github_simulado(usuarios)
    firmador = Firmador(SECRETO)
    identidad = IdentidadConConsola(IdentidadCompuesta(IdentidadGithub(cliente_gh, app=APP), None), firmador)
    registro = Registro.del_motor(motor, AutorizadorRoles(almacen))
    ctx = ContextoConsola(
        config=ConfigConsola(github_app=APP, url_publica="https://railspec.test"),
        firmador=firmador,
        datos=AlmacenConsola(almacen.db),
        almacen=almacen,
        registro=registro,
        identidad=identidad,
        github=ClienteGithub(APP, cliente_gh),
    )
    app = aplicacion(registro, identidad)
    montar_consola(app, ctx)
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="https://railspec.test") as c:
        yield c, llamadas


def test_un_token_de_github_da_el_mismo_resultado_por_v1_y_por_la_consola():
    usuarios = {
        "t-luis": ("luis", 9, equipos_fijos(4242)),
        "t-ana": ("ana", 7, equipos_fijos(31337)),
        "t-pablo": ("pablo", 11, sin_permiso),
        "t-rosa": ("rosa", 13, equipos_paginados([1], [4242])),  # el equipo viene en la segunda página
    }

    async def caso():
        async with _servidor(usuarios) as (c, _):
            for token, ws, permitido in (
                ("t-luis", WS, True),
                ("t-luis", "solo-lectura", True),  # lector basta para listar
                ("t-luis", "otro", False),
                ("t-ana", WS, False),
                ("t-pablo", WS, False),
                ("t-rosa", WS, True),
            ):
                cuerpo = {"alcance": {"org": ORG, "workspace": ws}}
                cabecera = {"Authorization": f"Bearer {token}"}
                v1 = await c.post("/v1/tools/unit.list", json=cuerpo, headers=cabecera)
                consola = await c.post("/consola/api/tools/unit.list", json=cuerpo, headers=cabecera)
                assert (v1.status_code == 200) == permitido == (consola.status_code == 200), (token, ws)
                if not permitido:
                    assert v1.status_code == consola.status_code == 403, (token, ws)
            # El rol mínimo de la tool lo pone el contrato, igual por los dos caminos: el rol del equipo en
            # "solo-lectura" es lector, así que no arranca unidades ni por /v1 ni por la consola.
            arrancar = entrada_start().model_dump(mode="json")
            arrancar["alcance"] = {"org": ORG, "workspace": "solo-lectura"}
            cabecera = {"Authorization": "Bearer t-luis"}
            for ruta in ("/v1/tools/unit.start", "/consola/api/tools/unit.start"):
                r = await c.post(ruta, json=arrancar, headers=cabecera)
                assert r.status_code in (403, 422), (ruta, r.text)

    correr(caso())


# --- no bloquear al desarrollador por un fallo pasajero de GitHub -----------------------------------------


def _respuestas(*respuestas):
    """``GET /user/teams`` que contesta con cada respuesta en orden y repite la última."""

    pendientes = list(respuestas)

    def responder(peticion: httpx.Request) -> httpx.Response:
        r = pendientes.pop(0) if len(pendientes) > 1 else pendientes[0]
        return r(peticion) if callable(r) else r

    return responder


@pytest.mark.parametrize(
    "fallo",
    [
        httpx.Response(503, json={"message": "caído"}),
        httpx.Response(429, json={"message": "demasiadas"}),
        httpx.Response(403, json={"message": "rate limit"}, headers={"Retry-After": "30"}),
        httpx.Response(403, json={"message": "rate limit"}, headers={"X-RateLimit-Remaining": "0"}),
    ],
)
def test_un_fallo_pasajero_de_github_no_deja_sin_roles_de_equipo_hasta_que_expire_el_cache(fallo):
    usuarios = {"t-luis": ("luis", 9, _respuestas(fallo, equipos_fijos(4242)))}
    cliente_gh, llamadas = github_simulado(usuarios)
    gh = IdentidadGithub(cliente_gh, app=APP)
    primera = gh.actor_desde_token("t-luis", "arnes")
    assert type(primera) is Actor  # falla cerrado: sin equipos, pero el token sigue autenticando
    segunda = gh.actor_desde_token("t-luis", "arnes")  # no se recordó el fallo: reintenta y lo logra
    assert isinstance(segunda, ActorConEquipos) and segunda.equipos == {4242}
    assert llamadas.count("/user/teams") == 2
    gh.actor_desde_token("t-luis", "arnes")
    assert llamadas.count("/user/teams") == 2  # ahora sí, definitivo y cacheado


def test_un_fallo_a_mitad_de_la_paginacion_no_recuerda_equipos_a_medias():
    primera_pagina = equipos_paginados([1], [4242])

    def a_medias(peticion: httpx.Request) -> httpx.Response:
        if peticion.url.params.get("page", "1") == "1":
            return primera_pagina(peticion)
        return httpx.Response(502)

    usuarios = {"t-luis": ("luis", 9, a_medias)}
    cliente_gh, _ = github_simulado(usuarios)
    gh = IdentidadGithub(cliente_gh, app=APP)
    actor = gh.actor_desde_token("t-luis", "arnes")
    assert isinstance(actor, ActorConEquipos) and actor.equipos == {1}  # lo leído, nunca de más
    usuarios["t-luis"] = ("luis", 9, equipos_paginados([1], [4242]))
    assert gh.actor_desde_token("t-luis", "arnes").equipos == {1, 4242}  # el siguiente intento lo completa


def test_sin_permiso_es_definitivo_y_se_recuerda():
    cliente_gh, llamadas = github_simulado({"t-pablo": ("pablo", 11, sin_permiso)})
    gh = IdentidadGithub(cliente_gh, app=APP)
    for _ in range(3):
        assert type(gh.actor_desde_token("t-pablo", "arnes")) is Actor
    assert llamadas.count("/user/teams") == 1


# --- la denegación le dice al arnés qué hacer -----------------------------------------------------------


def _rsc1(m: Montaje, github_id: int, equipos) -> dict[str, str]:
    token = m.firmador.emitir("luis", github_id, frozenset(equipos), timedelta(hours=1), "api")
    return {"Authorization": f"Bearer {token}"}


def test_la_denegacion_dice_el_rol_que_tienes_y_si_no_se_resolvieron_equipos():
    async def caso():
        m = Montaje(admins=frozenset())
        asignar(m.almacen, Rol.lector, LUIS_ID)
        async with m.cliente() as c:
            arrancar = entrada_start().model_dump(mode="json")
            r = await c.post("/v1/tools/unit.start", json=arrancar, headers=_rsc1(m, LUIS_ID, {5}))
            assert r.status_code == 403
            assert r.json()["detalle"] == f"unit.start exige rol desarrollador en {ORG}/{WS}; tienes lector"
            # Sin ningún rol: con equipos resueltos no se culpa a GitHub; sin equipos, se explica.
            con = await c.post("/v1/tools/unit.list", json=LISTAR, headers=_rsc1(m, 99, {5}))
            sin = await c.post("/v1/tools/unit.list", json=LISTAR, headers=_rsc1(m, 99, set()))
            assert con.json()["detalle"] == f"unit.list exige rol lector en {ORG}/{WS}; no tienes ninguno"
            assert "no tienes ninguno" in sin.json()["detalle"]
            assert "no se resolvieron equipos de GitHub" in sin.json()["detalle"]

    correr(caso())


def test_registro_direct_la_denegacion_no_filtra_nada_mas_que_el_ambito_del_llamante():
    motor, _ = construir()
    registro = Registro.del_motor(motor, AutorizadorRoles(almacen_en_memoria()))
    r = correr(registro.invocar("unit.list", LISTAR, JULIAN, Superficie.mcp))
    assert not r.ok and r.estado_http == 403
    assert r.cuerpo["detalle"].startswith(f"unit.list exige rol lector en {ORG}/{WS}; no tienes ninguno")
