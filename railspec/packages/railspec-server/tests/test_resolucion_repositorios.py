"""``GET /v1/repositorios/resolver``: a qué vínculo pertenece un clon, solo con lo que la persona ve."""

from __future__ import annotations

import asyncio
import uuid
from datetime import UTC, datetime

import httpx
import pytest
from apoyo_motor import JULIAN, ORG, REPO, WS, vinculo
from fastapi import FastAPI
from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
from railspec.contracts.repositorio import AsignacionRol, Auditoria, Rol, SujetoEquipo, SujetoUsuario
from railspec.server.api.identidad import ActorConEquipos, IdentidadDesarrollo
from railspec.server.api.resolucion import router_resolucion
from railspec.server.consola.almacen import AlmacenConsola
from railspec.server.estado import almacen_en_memoria

ANA, LUIS, SIN_ROL = 7, 9, 11
TOKENS = {"tk-ana": ("ana", ANA), "tk-luis": ("luis", LUIS), "tk-nadie": ("nadie", SIN_ROL)}
RUTA = "/v1/repositorios/resolver"
URL = "https://github.com/acme/certificados-api"
AHORA = datetime(2026, 10, 9, tzinfo=UTC)


def _asignar(datos: AlmacenConsola, org: str, rol: Rol, sujeto, workspace: str | None = WS) -> None:
    datos.guardar_rol(
        AsignacionRol(
            id=uuid.uuid4(),
            org=org,
            workspace=workspace,
            rol=rol,
            sujeto=sujeto,
            version=1,
            auditoria=Auditoria(
                creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA
            ),
        )
    )


def _vincular(datos: AlmacenConsola, org: str, workspace: str, repositorio: str, url: str, **extra) -> None:
    v = vinculo(NivelCodigo.restringido)
    datos.guardar_vinculo(
        v.model_copy(
            update={
                "alcance": AlcanceRepositorio(org=org, workspace=workspace, repositorio=repositorio),
                "url": url,
                **extra,
            }
        ),
        None,
    )


@pytest.fixture
def datos() -> AlmacenConsola:
    almacen = almacen_en_memoria()
    d = AlmacenConsola(almacen.db)
    _vincular(d, ORG, WS, REPO, URL, nivel_codigo=NivelCodigo.interno)
    _vincular(d, "otra", "ws-ajeno", "secreto", "https://github.com/otra/secreto")
    _asignar(d, ORG, Rol.lector, SujetoUsuario(github_id=ANA))  # en un workspace de acme
    _asignar(d, "otra", Rol.org_admin, SujetoUsuario(github_id=LUIS), workspace=None)  # en toda "otra"
    return d


class IdentidadConEquipo(IdentidadDesarrollo):
    """Como la de GitHub: la persona con un equipo (el rol puede venir del equipo)."""

    def __init__(self, tokens, equipos):
        super().__init__(tokens)
        self._equipos = equipos

    def actor_desde_token(self, token, canal):
        actor = super().actor_desde_token(token, canal)
        equipos = self._equipos.get(actor.github_id, frozenset())
        return ActorConEquipos(**actor.model_dump(), equipos=equipos) if equipos else actor


async def _get(datos, token: str | None = "tk-ana", url: str | None = URL, identidad=None) -> httpx.Response:
    api = FastAPI()
    api.include_router(router_resolucion(identidad or IdentidadDesarrollo(TOKENS), datos))
    cabeceras = {"Authorization": f"Bearer {token}"} if token else {}
    params = {} if url is None else {"url": url}
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="https://railspec.test"
    ) as c:
        return await c.get(RUTA, params=params, headers=cabeceras)


def _resolver(datos, **kw) -> httpx.Response:
    return asyncio.run(_get(datos, **kw))


def test_devuelve_el_vinculo_del_repositorio_a_quien_tiene_rol_en_su_workspace(datos):
    r = _resolver(datos)
    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "coincidencias": [
            {
                "org": ORG,
                "workspace": WS,
                "repositorio": REPO,
                "nivel_codigo": "interno",
                "rama_por_defecto": "main",
            }
        ]
    }


@pytest.mark.parametrize(
    "url",
    [
        URL + ".git",
        URL + "/",
        "https://github.com/ACME/Certificados-API",
        "  " + URL + " ",
    ],
)
def test_la_url_se_compara_sin_mayusculas_ni_git_ni_barra_final(datos, url):
    assert [c["repositorio"] for c in _resolver(datos, url=url).json()["coincidencias"]] == [REPO]


def test_la_url_guardada_con_git_o_mayusculas_tambien_coincide(datos):
    _vincular(datos, ORG, WS, "otro-repo", "https://github.com/Acme/Otro-Repo.git")
    r = _resolver(datos, url="https://github.com/acme/otro-repo")
    assert [c["repositorio"] for c in r.json()["coincidencias"]] == ["otro-repo"]


def test_no_revela_vinculos_de_workspaces_donde_la_persona_no_tiene_rol(datos):
    ajeno = "https://github.com/otra/secreto"
    assert _resolver(datos, token="tk-ana", url=ajeno).json() == {"coincidencias": []}
    assert _resolver(datos, token="tk-nadie").json() == {"coincidencias": []}
    # Un rol de toda la organización (workspace nulo) sí vale para sus workspaces.
    assert [c["workspace"] for c in _resolver(datos, token="tk-luis", url=ajeno).json()["coincidencias"]] == [
        "ws-ajeno"
    ]
    # Y el rol en una organización no abre los vínculos de otra.
    assert _resolver(datos, token="tk-luis").json() == {"coincidencias": []}


def test_un_rol_asignado_a_un_equipo_de_la_persona_cuenta(datos):
    equipo = SujetoEquipo(github_org="acme", equipo="plataforma", equipo_id=555)
    _asignar(datos, ORG, Rol.desarrollador, equipo)
    sin = _resolver(datos, token="tk-nadie")
    assert sin.json() == {"coincidencias": []}
    identidad = IdentidadConEquipo(TOKENS, {SIN_ROL: frozenset({555})})
    con = asyncio.run(_get(datos, token="tk-nadie", identidad=identidad))
    assert [c["repositorio"] for c in con.json()["coincidencias"]] == [REPO]


def test_el_mismo_repositorio_en_dos_workspaces_devuelve_ambos_para_que_la_persona_elija(datos):
    _vincular(datos, ORG, "otro-ws", REPO, URL)
    _asignar(datos, ORG, Rol.lector, SujetoUsuario(github_id=ANA), workspace="otro-ws")
    r = _resolver(datos)
    assert sorted(c["workspace"] for c in r.json()["coincidencias"]) == sorted([WS, "otro-ws"])


@pytest.mark.parametrize(
    "url",
    [
        None,
        "",
        "http://github.com/acme/certificados-api",
        "https://gitlab.com/acme/certificados-api",
        "https://user:tok@github.com/acme/certificados-api",
        "https://github.com/acme/certificados-api/extra",
        "https://github.com/acme/..",
        "https://github.com/" + "a" * 600,
    ],
    ids=[
        "sin-url",
        "vacia",
        "http",
        "gitlab",
        "con-userinfo",
        "segmento-de-mas",
        "puntos",
        "demasiado-larga",
    ],
)
def test_una_url_que_no_es_de_github_se_rechaza(datos, url):
    r = _resolver(datos, url=url)
    assert r.status_code == 422 and r.json()["codigo"] == "entrada-invalida"


def test_exige_el_token_de_una_persona(datos):
    sin = _resolver(datos, token=None)
    assert sin.status_code == 401
    mala = _resolver(datos, token="tk-desconocido")
    assert mala.status_code == 401
