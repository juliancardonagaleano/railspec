"""``POST /v1/auth/renovar``: puente entre el refresh token del arnés y GitHub (el secret no sale)."""

from __future__ import annotations

import asyncio
import threading
from urllib.parse import parse_qs

import httpx
import pytest
from apoyo_github import APP
from fastapi import FastAPI
from railspec.server.api.renovacion import RenovadorGithub, router_renovacion

REFRESCO = "ghr_" + "r" * 36
NUEVO_TOKEN = "ghu_" + "n" * 36
NUEVO_REFRESCO = "ghr_" + "m" * 36
RUTA = "/v1/auth/renovar"


class GithubFalso:
    def __init__(self, respuesta=None):
        self.respuesta = respuesta or (
            lambda _: httpx.Response(
                200,
                json={
                    "access_token": NUEVO_TOKEN,
                    "expires_in": 28800,
                    "refresh_token": NUEVO_REFRESCO,
                    "refresh_token_expires_in": 15811200,
                    "token_type": "bearer",
                },
            )
        )
        self.peticiones: list[httpx.Request] = []

    def __call__(self, peticion: httpx.Request) -> httpx.Response:
        self.peticiones.append(peticion)
        return self.respuesta(peticion)

    def cliente(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))

    def formulario(self, i: int = 0) -> dict[str, str]:
        return {k: v[0] for k, v in parse_qs(self.peticiones[i].content.decode()).items()}


def _app(github: GithubFalso, app=APP, **kw) -> FastAPI:
    api = FastAPI()
    api.include_router(router_renovacion(RenovadorGithub(app, github.cliente(), **kw)))
    return api


async def _post(api: FastAPI, **kw) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=api), base_url="https://railspec.test"
    ) as c:
        return await c.post(RUTA, **kw)


def llamar(api: FastAPI, cuerpo=None, **kw) -> httpx.Response:
    return asyncio.run(
        _post(
            api,
            json={"refresh_token": REFRESCO, "client_id": APP.client_id} if cuerpo is None else cuerpo,
            **kw,
        )
    )


def test_renueva_con_las_credenciales_de_la_app_y_devuelve_los_tokens_nuevos():
    github = GithubFalso()

    r = llamar(_app(github))  # sin Authorization: la credencial es el refresh token

    assert r.status_code == 200 and r.headers["cache-control"] == "no-store"
    assert r.json() == {
        "access_token": NUEVO_TOKEN,
        "expires_in": 28800,
        "refresh_token": NUEVO_REFRESCO,
        "refresh_token_expires_in": 15811200,
    }
    (peticion,) = github.peticiones
    assert (peticion.method, str(peticion.url)) == ("POST", "https://github.com/login/oauth/access_token")
    assert github.formulario() == {
        "client_id": APP.client_id,
        "client_secret": APP.client_secret,
        "grant_type": "refresh_token",
        "refresh_token": REFRESCO,
    }


def test_el_client_id_es_opcional():
    github = GithubFalso()
    assert llamar(_app(github), {"refresh_token": REFRESCO}).status_code == 200
    assert github.formulario()["client_id"] == APP.client_id  # siempre el de la App del servidor


def test_la_respuesta_sin_refresh_token_nuevo_lo_deja_vacio():
    github = GithubFalso(lambda _: httpx.Response(200, json={"access_token": NUEVO_TOKEN}))
    assert llamar(_app(github)).json() == {
        "access_token": NUEVO_TOKEN,
        "expires_in": None,
        "refresh_token": None,
        "refresh_token_expires_in": None,
    }


def test_un_refresh_token_malo_es_401_sin_repetirlo():
    github = GithubFalso(
        lambda _: httpx.Response(
            200,
            json={
                "error": "bad_refresh_token",
                "error_description": f"The refresh token passed is incorrect {REFRESCO}",
            },
        )
    )

    r = llamar(_app(github))

    assert r.status_code == 401 and r.json()["codigo"] == "refresh-token-invalido"
    assert REFRESCO not in r.text and "GitHub" in r.json()["detalle"]


def test_un_client_id_de_otra_app_es_400_y_no_llama_a_github():
    github = GithubFalso()

    r = llamar(_app(github), {"refresh_token": REFRESCO, "client_id": "Iv-otra"})

    assert r.status_code == 400 and r.json()["codigo"] == "client-id-incorrecto"
    assert "railspec login" in r.json()["detalle"] and github.peticiones == []


def test_sin_github_app_configurada_el_servidor_no_renueva():
    github = GithubFalso()

    r = llamar(_app(github, app=None))

    assert r.status_code == 404 and r.json()["codigo"] == "renovacion-no-disponible"
    assert github.peticiones == []


def _sin_red(peticion: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("sin red", request=peticion)


@pytest.mark.parametrize(
    "respuesta",
    [
        _sin_red,
        lambda _: httpx.Response(502, text="<html>bad gateway</html>"),
        lambda _: httpx.Response(200, json=["no", "es", "un", "objeto"]),
        lambda _: httpx.Response(200, json={"error": "incorrect_client_credentials"}),
        lambda _: httpx.Response(200, json={"token_type": "bearer"}),
        lambda _: httpx.Response(200, json={"access_token": 7}),
    ],
)
def test_si_github_no_responde_bien_es_503_para_que_el_proxy_reintente(respuesta):
    r = llamar(_app(GithubFalso(respuesta)))

    assert r.status_code == 503 and r.json()["codigo"] == "github-no-disponible"
    assert REFRESCO not in r.text


@pytest.mark.parametrize(
    "cuerpo",
    [
        {},
        {"refresh_token": ""},
        {"refresh_token": 7},
        {"refresh_token": "x" * 513},
        {"refresh_token": REFRESCO, "client_id": 7},
        {"refresh_token": REFRESCO, "client_id": "x" * 129},
        ["refresh_token"],
        "ghr_suelto",
    ],
)
def test_una_entrada_invalida_es_422_y_no_llega_a_github(cuerpo):
    github = GithubFalso()

    r = llamar(_app(github), cuerpo)

    assert r.status_code == 422 and r.json()["codigo"] == "entrada-invalida"
    assert github.peticiones == []
    assert "x" * 100 not in r.text  # no se devuelve lo que mandaron


def test_un_cuerpo_que_no_es_json_es_422():
    github = GithubFalso()
    r = llamar(_app(github), content=b"no es json", headers={"content-type": "application/json"})
    assert r.status_code == 422 and github.peticiones == []


def test_las_llamadas_simultaneas_a_github_estan_acotadas():
    dentro, soltar = threading.Event(), threading.Event()

    def lenta(_):
        dentro.set()
        soltar.wait(5)
        return httpx.Response(200, json={"access_token": NUEVO_TOKEN})

    renovador = RenovadorGithub(APP, GithubFalso(lenta).cliente(), max_concurrentes=1, espera_s=0.05)
    primero = threading.Thread(target=renovador.renovar, args=(REFRESCO, None))
    primero.start()
    try:
        assert dentro.wait(5)
        with pytest.raises(Exception, match="demasiadas renovaciones") as exc:
            renovador.renovar(REFRESCO, None)
        assert exc.value.estado == 503
    finally:
        soltar.set()
        primero.join(5)
    assert renovador.renovar(REFRESCO, None)["access_token"] == NUEVO_TOKEN  # el cupo se liberó


def test_ensamblar_publica_la_ruta_con_la_github_app_del_servidor():
    from apoyo_motor import GobernanzaFija, ProveedorGuionado, critico_sin_hallazgos
    from railspec.contracts.comun import Proveedor
    from railspec.server.app import ensamblar
    from railspec.server.config import Configuracion
    from railspec.server.consola import ConfigConsola
    from railspec.server.proveedores import Proveedores

    github = GithubFalso()
    config = Configuracion(
        tokens_desarrollo={"tk": ("ana", 7)},
        permitir_desarrollo=True,
        consola=ConfigConsola(github_app=APP, secreto_sesion="s" * 40),
    )
    _, app = ensamblar(
        config,
        proveedores=Proveedores({Proveedor.foundry: ProveedorGuionado(critico_sin_hallazgos)}),
        gobernanza=GobernanzaFija(),
        cliente_github=github.cliente(),
    )

    r = llamar(app)

    assert r.status_code == 200 and r.json()["access_token"] == NUEVO_TOKEN
    assert github.formulario()["client_secret"] == APP.client_secret
