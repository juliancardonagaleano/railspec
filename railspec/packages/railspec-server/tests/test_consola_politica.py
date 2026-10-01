"""Consola: login de GitHub, vínculos de repositorio, política de workspace y proveedores de contexto."""

from __future__ import annotations

import asyncio

import httpx
from apoyo_motor import ORG, WS
from railspec.contracts.repositorio import Rol
from railspec.server.consola.github import ClienteGithub
from test_consola import ANA_ID, CSRF, Montaje, asignar


def correr(coro):
    return asyncio.run(coro)


# --- B6: ``login`` de GitHub ---------------------------------------------------------------------------


def _github_espia():
    llamadas: list[str] = []

    def responder(peticion: httpx.Request) -> httpx.Response:
        llamadas.append(str(peticion.url))
        return httpx.Response(200, json={"id": 4242, "login": "octocat"})

    return llamadas, httpx.Client(transport=httpx.MockTransport(responder))


LOGINS_MALOS = ["../orgs/microsoft", "a/b", "-guion", "con espacio", "a" * 40, "x?y=1", "a%2e%2e", "a#b", "ñ"]


def test_id_de_login_rechaza_logins_que_no_son_de_github_sin_llamar():
    llamadas, http = _github_espia()
    cliente = ClienteGithub(None, http)
    for login in LOGINS_MALOS:
        assert cliente.id_de_login(login) is None, login
    assert llamadas == []
    assert cliente.id_de_login("octocat") == 4242 and llamadas == ["https://api.github.com/users/octocat"]
    assert cliente.id_de_login("a-b-1") == 4242


def test_asignar_rol_por_login_valida_el_formato_en_la_entrada():
    async def caso():
        llamadas, http = _github_espia()
        m = Montaje(github_http=http)
        asignar(m.almacen, Rol.org_admin, ANA_ID, workspace=None)
        async with m.cliente("tk-ana") as c:
            for login in LOGINS_MALOS:
                r = await c.post(
                    f"/consola/api/orgs/{ORG}/roles",
                    json={"workspace": WS, "rol": "lector", "sujeto": {"tipo": "usuario", "login": login}},
                    headers=CSRF,
                )
                assert r.status_code == 422, (login, r.text)
            assert llamadas == []
            r = await c.post(
                f"/consola/api/orgs/{ORG}/roles",
                json={"workspace": WS, "rol": "lector", "sujeto": {"tipo": "usuario", "login": "octocat"}},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            assert r.json()["sujeto"] == {"tipo": "usuario", "github_id": 4242}
            assert llamadas == ["https://api.github.com/users/octocat"]

    correr(caso())
