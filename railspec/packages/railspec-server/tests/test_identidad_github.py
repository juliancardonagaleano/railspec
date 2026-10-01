"""Token de usuario de GitHub: pertenencia a la GitHub App de Railspec (M3) y cachés acotadas (M5).

Antes, ``IdentidadGithub`` solo hacía ``GET /user``: cualquier OAuth app de terceros a la que la persona
hubiera dado acceso (aunque sin scopes) podía reutilizar su token como Bearer y suplantarla en /v1, MCP y
/consola/api. Ahora cada token se comprueba con ``POST /applications/{client_id}/token`` (Basic con las
credenciales de la App) y se exige que la respuesta sea de esa App.
"""

from __future__ import annotations

import asyncio
import threading
import time

import httpx
import pytest
from railspec.server.api.identidad import (
    IdentidadCompuesta,
    IdentidadGithub,
    IdentidadNoDisponible,
    TokenInvalido,
)
from railspec.server.api.registro import Registro
from railspec.server.api.superficies import aplicacion
from railspec.server.api.verificacion_github import CacheAcotada
from railspec.server.consola.config import ConfigGithubApp

APP = ConfigGithubApp("Iv1.railspec", "secreto-de-la-app")
OTRA_APP = "Iv1.una-app-de-terceros"
URL_APP = f"https://api.github.com/applications/{APP.client_id}/token"


def autorizacion(token: str, app: str = APP.client_id, usuario: dict | None = ...):
    cuerpo = {
        "id": 1,
        "token": token,
        "app": {"client_id": app, "name": "app", "url": "https://x"},
        "scopes": [],
        "expires_at": None,
    }
    cuerpo["user"] = {"login": "ana", "id": 7} if usuario is ... else usuario
    return cuerpo


class GithubFalso:
    """Doble de api.github.com (``httpx.Client`` síncrono). ``tokens``: token -> JSON de la comprobación."""

    def __init__(self, tokens: dict[str, dict] | None = None, demora_s: float = 0.0) -> None:
        self.tokens = tokens or {}
        self.demora_s = demora_s
        self.post_llamadas: list[dict] = []
        self.get_llamadas: list[dict] = []
        self.fallo: Exception | None = None
        self.estado: int | None = None
        self._bloqueo = threading.Lock()
        self.en_vuelo = self.max_en_vuelo = 0

    def post(self, url, *, auth=None, json=None, headers=None, **_):
        with self._bloqueo:
            self.post_llamadas.append({"url": url, "auth": auth, "json": json, "headers": headers})
            self.en_vuelo += 1
            self.max_en_vuelo = max(self.max_en_vuelo, self.en_vuelo)
        try:
            time.sleep(self.demora_s)
            if self.fallo is not None:
                raise self.fallo
            if self.estado is not None:
                return httpx.Response(self.estado, json={"message": "x"})
            datos = self.tokens.get(json["access_token"])
            if datos is None:
                return httpx.Response(404, json={"message": "Not Found"})
            return httpx.Response(200, json=datos)
        finally:
            with self._bloqueo:
                self.en_vuelo -= 1

    def get(self, url, headers=None, **_):
        self.get_llamadas.append({"url": url, "headers": headers})
        token = (headers or {}).get("Authorization", "").removeprefix("Bearer ")
        if token not in self.tokens:
            return httpx.Response(401)
        return httpx.Response(200, json={"login": "ana", "id": 7})


def identidad(falso: GithubFalso, **kw) -> IdentidadGithub:
    return IdentidadGithub(falso, app=APP, **kw)


# --- M3: el token tiene que ser de la GitHub App de Railspec ------------------------------


def test_token_de_la_app_da_el_actor_y_se_comprueba_con_las_credenciales_de_la_app():
    falso = GithubFalso({"ghu_bueno": autorizacion("ghu_bueno")})
    gh = identidad(falso)
    actor = gh.actor_desde_token("ghu_bueno", "arnes")
    assert (actor.github_id, actor.login, actor.tipo.value, actor.canal.value) == (
        7,
        "ana",
        "humano",
        "arnes",
    )
    (llamada,) = falso.post_llamadas
    assert llamada["url"] == URL_APP
    assert llamada["json"] == {"access_token": "ghu_bueno"}
    # HTTP Basic client_id:client_secret (httpx lo codifica a partir de la tupla).
    assert tuple(llamada["auth"]) == (APP.client_id, APP.client_secret)
    # Ya no hace falta GET /user: lo único que se lee con GET son los equipos, y solo tras verificar.
    assert [g["url"].split("?")[0] for g in falso.get_llamadas] == ["https://api.github.com/user/teams"]
    gh.actor_desde_token("ghu_bueno", "arnes")
    assert len(falso.post_llamadas) == 1 and len(falso.get_llamadas) == 1  # cacheado


def test_token_de_otra_oauth_app_se_rechaza():
    """El token vale en GitHub, pero lo emitió otra app: no es de Railspec."""

    falso = GithubFalso({"gho_de_terceros": autorizacion("gho_de_terceros", app=OTRA_APP)})
    with pytest.raises(TokenInvalido, match="no pertenece a la GitHub App"):
        identidad(falso).actor_desde_token("gho_de_terceros", "consola")


def test_token_que_github_no_reconoce_para_esa_app_se_rechaza():
    # 404: token inexistente, revocado o emitido por otra app (así responde la comprobación de GitHub).
    with pytest.raises(TokenInvalido):
        identidad(GithubFalso()).actor_desde_token("ghp_personal", "consola")
    # Respuesta de la app correcta pero sin persona (token de instalación): no es un usuario.
    falso = GithubFalso({"ghs_instalacion": autorizacion("ghs_instalacion", usuario=None)})
    with pytest.raises(TokenInvalido, match="persona"):
        identidad(falso).actor_desde_token("ghs_instalacion", "consola")


def test_sin_credenciales_de_la_app_se_rechazan_los_tokens_de_github():
    falso = GithubFalso({"ghu_bueno": autorizacion("ghu_bueno")})
    gh = IdentidadGithub(falso)  # sin app y sin modo desarrollo
    with pytest.raises(TokenInvalido, match="GitHub App"):
        gh.actor_desde_token("ghu_bueno", "arnes")
    assert not falso.post_llamadas and not falso.get_llamadas  # ni siquiera pregunta a GitHub


def test_modo_desarrollo_sin_app_acepta_el_token_de_usuario_sin_comprobar_la_app():
    falso = GithubFalso({"ghp_personal": {}})
    gh = IdentidadGithub(falso, permitir_sin_app=True)
    assert gh.actor_desde_token("ghp_personal", "arnes").login == "ana"
    assert falso.get_llamadas and not falso.post_llamadas
    with pytest.raises(TokenInvalido):
        gh.actor_desde_token("otro", "arnes")


def test_si_no_se_puede_validar_se_rechaza_cerrado():
    falso = GithubFalso({"ghu_bueno": autorizacion("ghu_bueno")})
    gh = identidad(falso)
    # Fallo de red o timeout.
    falso.fallo = httpx.ConnectTimeout("sin ruta")
    with pytest.raises(IdentidadNoDisponible) as exc:
        gh.actor_desde_token("ghu_bueno", "arnes")
    assert isinstance(exc.value, TokenInvalido)  # quien captura TokenInvalido sigue rechazando
    # Credenciales de la App rechazadas por GitHub (401), límite de tasa (403) o 5xx: tampoco pasa.
    falso.fallo = None
    for estado in (401, 403, 500, 502):
        falso.estado = estado
        with pytest.raises(IdentidadNoDisponible):
            gh.actor_desde_token("ghu_bueno", "arnes")
    # Y se recupera sola: un fallo de red no se recuerda como si el token fuera malo.
    falso.estado = None
    assert gh.actor_desde_token("ghu_bueno", "arnes").login == "ana"


def test_un_fallo_de_red_no_tumba_el_servidor_ni_da_acceso():
    async def caso():
        falso = GithubFalso({"ghu_bueno": autorizacion("ghu_bueno")})
        falso.fallo = httpx.ConnectError("GitHub caído")
        app = aplicacion(Registro({}, None), IdentidadCompuesta(identidad(falso), None))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
                for _ in range(3):
                    r = await c.post(
                        "/v1/tools/unit.list", json={}, headers={"Authorization": "Bearer ghu_bueno"}
                    )
                    assert r.status_code in (401, 503) and "GitHub" in r.json()["detalle"]
                assert r.status_code == 503  # indisponible, no "token malo"
                assert (await c.get("/livez")).status_code == 200  # el servidor sigue en pie
                # Con GitHub de vuelta, el mismo token funciona.
                falso.fallo = None
                r = await c.post(
                    "/v1/tools/unit.list", json={}, headers={"Authorization": "Bearer ghu_bueno"}
                )
                assert r.status_code != 401 and r.status_code != 503  # autenticó (422: entrada vacía)

    asyncio.run(caso())


def test_token_de_otra_app_es_401_en_http():
    async def caso():
        falso = GithubFalso({"gho_de_terceros": autorizacion("gho_de_terceros", app=OTRA_APP)})
        app = aplicacion(Registro({}, None), IdentidadCompuesta(identidad(falso), None))
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
                r = await c.post(
                    "/v1/tools/unit.list", json={}, headers={"Authorization": "Bearer gho_de_terceros"}
                )
                assert r.status_code == 401 and "no pertenece a la GitHub App" in r.json()["detalle"]

    asyncio.run(caso())


# --- M5: cachés con tope, caché negativa y concurrencia acotada ----------------------------


def test_cache_acotada_expira_y_expulsa_los_menos_usados():
    ahora = [0.0]
    cache = CacheAcotada(ttl_s=10, max_entradas=3, reloj=lambda: ahora[0])
    for clave in "abc":
        cache.poner(clave, clave.upper())
    assert cache.obtener("a") == "A"  # "a" pasa a ser la más reciente
    cache.poner("d", "D")  # expulsa "b", la menos usada
    assert len(cache) == 3 and cache.obtener("b") is None and cache.obtener("a") == "A"
    ahora[0] = 11
    assert cache.obtener("a") is None and len(cache) <= 3
    cache.poner("e", "E", ttl_s=1)
    ahora[0] = 13
    assert cache.obtener("e") is None  # ttl propio


def test_las_cachés_de_identidad_no_crecen_sin_limite():
    tokens = {f"ghu_{i}": autorizacion(f"ghu_{i}") for i in range(40)}
    falso = GithubFalso(tokens)
    gh = identidad(falso, max_entradas=5)
    for i in range(40):
        gh.actor_desde_token(f"ghu_{i}", "arnes")
        with pytest.raises(TokenInvalido):
            gh.actor_desde_token(f"basura_{i}", "arnes")
    positivas, negativas = gh.tamanos_de_cache()
    assert 0 < positivas <= 5 and 0 < negativas <= 5


def test_un_token_rechazado_se_recuerda_poco_tiempo():
    reloj = [0.0]
    falso = GithubFalso({"ghu_nuevo": autorizacion("ghu_nuevo")})
    gh = identidad(falso, ttl_negativo_s=30, reloj=lambda: reloj[0])
    for _ in range(5):
        with pytest.raises(TokenInvalido):
            gh.actor_desde_token("ghu_nuevo_aun_no_valido", "arnes")
    assert len(falso.post_llamadas) == 1  # cuatro de cinco no llegan a GitHub
    reloj[0] = 31  # pasado el TTL corto vuelve a preguntar
    with pytest.raises(TokenInvalido):
        gh.actor_desde_token("ghu_nuevo_aun_no_valido", "arnes")
    assert len(falso.post_llamadas) == 2


def test_la_concurrencia_hacia_github_esta_acotada():
    falso = GithubFalso(demora_s=0.2)
    gh = identidad(falso, max_concurrentes=3, espera_s=0.05)
    resultados: list[str] = []

    def intento(i: int) -> None:
        try:
            gh.actor_desde_token(f"basura_{i}", "arnes")
        except IdentidadNoDisponible:
            resultados.append("saturada")
        except TokenInvalido:
            resultados.append("rechazado")

    hilos = [threading.Thread(target=intento, args=(i,)) for i in range(12)]
    for h in hilos:
        h.start()
    for h in hilos:
        h.join()
    assert falso.max_en_vuelo <= 3
    # Los que no consiguieron cupo en 50 ms se rechazaron sin esperar a GitHub: cierra, no se cuelga.
    assert resultados.count("saturada") >= 1 and len(resultados) == 12


def test_rafaga_de_tokens_basura_con_github_lento_solo_da_401_o_503():
    """Con la concurrencia acotada, la ráfaga se resuelve (rechazada o saturada) y el servidor sigue vivo."""

    async def caso():
        falso = GithubFalso(demora_s=0.01)
        compuesta = IdentidadCompuesta(identidad(falso, max_concurrentes=4, espera_s=0.5), None)
        app = aplicacion(Registro({}, None), compuesta)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
                respuestas = await asyncio.gather(
                    *(
                        c.post("/v1/tools/unit.list", json={}, headers={"Authorization": f"Bearer b{i}"})
                        for i in range(60)
                    )
                )
                assert {r.status_code for r in respuestas} <= {401, 503}
                assert (await c.get("/livez")).status_code == 200

    asyncio.run(caso())
