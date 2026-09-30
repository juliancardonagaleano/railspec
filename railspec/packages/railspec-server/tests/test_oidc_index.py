"""Identidad OIDC de GitHub Actions, ``graph.index`` por HTTP y sondas de ``/healthz``."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import time

import httpx
import jwt
import pytest
from apoyo_motor import ORG, REPO, WS, vinculo
from cryptography.hazmat.primitives.asymmetric import rsa
from railspec.contracts.comun import AlcanceRepositorio, Canal, NivelCodigo, TipoActor
from railspec.contracts.snapshot import DeltaIndice, MotorIndice, Simbolo, TipoSimbolo, id_simbolo
from railspec.graph import AccesoGrafo, MotorMemoria
from railspec.server.api.identidad import (
    EMISOR_ACTIONS,
    IdentidadCompuesta,
    IdentidadDesarrollo,
    TokenInvalido,
    VerificadorOidcActions,
)
from railspec.server.api.superficies import aplicacion
from railspec.server.app import ensamblar
from railspec.server.config import Configuracion

CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
OTRA_CLAVE = rsa.generate_private_key(public_exponent=65537, key_size=2048)
REPO_GH = f"{ORG}/{REPO}"
ALCANCE = AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO)
COMMIT_1, COMMIT_2 = "1" * 40, "2" * 40


class ClavesLocales:
    """Sustituye al ``PyJWKClient`` del JWKS de GitHub."""

    class _Clave:
        key = CLAVE.public_key()

    def get_signing_key_from_jwt(self, token):
        return self._Clave()


def token_oidc(clave=CLAVE, **reclamos) -> str:
    ahora = int(time.time())
    datos = {
        "iss": EMISOR_ACTIONS,
        "aud": "railspec",
        "iat": ahora,
        "nbf": ahora,
        "exp": ahora + 300,
        "repository": REPO_GH,
        "ref": "refs/heads/main",
        "workflow_ref": f"{REPO_GH}/.github/workflows/railspec-reindexar.yml@refs/heads/main",
        "run_id": "42",
    }
    datos.update(reclamos)
    return jwt.encode(datos, clave, algorithm="RS256", headers={"kid": "k1"})


def verificador(**extra) -> VerificadorOidcActions:
    return VerificadorOidcActions("railspec", claves=ClavesLocales(), **extra)


# --- verificador ---------------------------------------------------------------------


def test_token_valido_da_un_actor_de_servicio():
    actor = verificador().actor_desde_token(token_oidc(), "consola")
    assert actor.tipo == TipoActor.servicio and actor.canal == Canal.ci
    assert actor.oidc.repositorio == REPO_GH and actor.oidc.run_id == 42
    assert actor.oidc.workflow.endswith("@refs/heads/main")


@pytest.mark.parametrize(
    "token",
    [
        token_oidc(aud="otra"),
        token_oidc(exp=int(time.time()) - 3600),
        token_oidc(clave=OTRA_CLAVE),
        token_oidc(repository=None),
    ],
    ids=["audiencia", "caducado", "firma", "sin-repositorio"],
)
def test_token_invalido_se_rechaza(token):
    with pytest.raises(TokenInvalido):
        verificador().actor_desde_token(token, "consola")


def test_lista_de_repositorios_permitidos():
    v = verificador(repositorios=frozenset({"Acme/Otro"}))
    with pytest.raises(TokenInvalido, match="no está autorizado"):
        v.actor_desde_token(token_oidc(), "consola")
    assert verificador(repositorios=frozenset({"ACME/certificados-api"})).actor_desde_token(
        token_oidc(), "consola"
    )


def test_identidad_compuesta_reparte_por_emisor():
    v = verificador()
    identidad = IdentidadCompuesta(IdentidadDesarrollo({"tk": ("julian", 1)}), v)
    assert identidad.actor_desde_token("tk", "consola").tipo == TipoActor.humano
    assert identidad.actor_desde_token(token_oidc(), "consola").tipo == TipoActor.servicio
    # Un JWT de otro emisor no llega al verificador OIDC: lo juzga la identidad humana.
    with pytest.raises(TokenInvalido, match="desconocido"):
        identidad.actor_desde_token(token_oidc(iss="https://otro.example"), "consola")


def test_configuracion_oidc_desde_entorno():
    c = Configuracion.desde_entorno({})
    assert c.oidc_audiencia == "railspec" and c.oidc_emisor == EMISOR_ACTIONS and not c.oidc_repositorios
    c = Configuracion.desde_entorno(
        {"RAILSPEC_OIDC_AUDIENCIA": "", "RAILSPEC_OIDC_REPOSITORIOS": "acme/a, acme/b"}
    )
    assert c.oidc_audiencia is None and c.oidc_repositorios == {"acme/a", "acme/b"}


# --- graph.index por HTTP ------------------------------------------------------------


def simbolo(nombre: str) -> Simbolo:
    return Simbolo(
        id=id_simbolo(REPO, "src/pdf.py", "funcion", nombre),
        nombre=nombre,
        tipo=TipoSimbolo.funcion,
        ruta="src/pdf.py",
        linea_inicio=1,
        linea_fin=4,
        sha256=hashlib.sha256(nombre.encode()).hexdigest(),
    )


def lote(i: int, n: int, *simbolos: Simbolo, commit=COMMIT_1, anterior=None, rama="main") -> dict:
    delta = DeltaIndice(motor=MotorIndice(version="0.11.0"), simbolos_upsert=list(simbolos))
    return {
        "alcance": ALCANCE.model_dump(),
        "rama": rama,
        "commit": commit,
        "commit_anterior": anterior,
        "lote": i,
        "lotes": n,
        "delta": delta.model_dump(mode="json", exclude_none=True),
    }


@contextlib.asynccontextmanager
async def servidor():
    motor_grafo = MotorMemoria()
    config = Configuracion.desde_entorno({"RAILSPEC_TOKENS_DESARROLLO": "tk-julian=juliancardonagaleano:1"})
    motor, app = ensamblar(config, motor_grafo=motor_grafo, verificador_oidc=verificador())
    motor.n.almacen.guardar_configuracion([vinculo(NivelCodigo.restringido)])
    async with app.router.lifespan_context(app):
        transporte = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transporte, base_url="http://railspec") as c:
            yield c, AccesoGrafo(motor_grafo)


def _oidc(token: str | None = None) -> dict:
    return {"Authorization": f"Bearer {token or token_oidc()}"}


def test_graph_index_por_lotes_avanza_el_canonico():
    async def caso():
        async with servidor() as (c, acceso):
            nombres = {t["name"] for t in (await c.get("/v1/tools")).json()["tools"]}
            assert {"graph.index", "graph.query"} <= nombres

            r = await c.post("/v1/tools/graph.index", json=lote(1, 2, simbolo("firmar")), headers=_oidc())
            assert r.status_code == 200, r.text
            assert r.json() == {
                "version_contrato": r.json()["version_contrato"],
                "commit": COMMIT_1,
                "lotes_recibidos": 1,
                "aplicado": False,
            }
            assert acceso.espacio(ALCANCE).meta().commit is None

            r = await c.post("/v1/tools/graph.index", json=lote(2, 2, simbolo("verificar")), headers=_oidc())
            assert r.status_code == 200 and r.json()["aplicado"] is True
            assert acceso.espacio(ALCANCE).meta().commit == COMMIT_1

            # Delta sobre el canónico vigente.
            r = await c.post(
                "/v1/tools/graph.index",
                json=lote(1, 1, simbolo("revocar"), commit=COMMIT_2, anterior=COMMIT_1),
                headers=_oidc(),
            )
            assert r.status_code == 200 and r.json()["aplicado"] is True
            assert acceso.espacio(ALCANCE).meta().commit == COMMIT_2

    asyncio.run(caso())


def test_graph_index_rechazos():
    async def caso():
        async with servidor() as (c, _):
            # Un humano no puede indexar el canónico.
            r = await c.post(
                "/v1/tools/graph.index",
                json=lote(1, 1, simbolo("x")),
                headers={"Authorization": "Bearer tk-julian"},
            )
            assert r.status_code == 403 and r.json()["codigo"] == "fuera-de-alcance"

            # Token de otro repositorio.
            r = await c.post(
                "/v1/tools/graph.index",
                json=lote(1, 1, simbolo("x")),
                headers=_oidc(token_oidc(repository="acme/otro")),
            )
            assert r.status_code == 403 and "acme/otro" in r.json()["detalle"]

            # Workflow corrido en otra rama.
            otra = f"{REPO_GH}/.github/workflows/railspec-reindexar.yml@refs/heads/feature"
            r = await c.post(
                "/v1/tools/graph.index",
                json=lote(1, 1, simbolo("x")),
                headers=_oidc(token_oidc(workflow_ref=otra, ref="refs/heads/feature")),
            )
            assert r.status_code == 403 and "refs/heads/feature" in r.json()["detalle"]

            # Token inválido: 401 antes de llegar a la tool.
            r = await c.post(
                "/v1/tools/graph.index", json=lote(1, 1, simbolo("x")), headers=_oidc(token_oidc(aud="otra"))
            )
            assert r.status_code == 401

            # Delta cuya base no es el canónico vigente.
            r = await c.post(
                "/v1/tools/graph.index",
                json=lote(1, 1, simbolo("x"), commit=COMMIT_2, anterior=COMMIT_1),
                headers=_oidc(),
            )
            assert r.status_code == 409 and r.json()["codigo"] == "base-commit-distinto"

            # Repositorio sin vínculo.
            cuerpo = lote(1, 1, simbolo("x"))
            cuerpo["alcance"]["repositorio"] = "sin-vinculo"
            r = await c.post("/v1/tools/graph.index", json=cuerpo, headers=_oidc())
            assert r.status_code == 404

    asyncio.run(caso())


# --- sondas ---------------------------------------------------------------------------


def _app(sondas, tope_s=0.5):
    from railspec.server.api.registro import Registro

    return aplicacion(Registro({}, None), IdentidadDesarrollo({}), sondas=sondas, tope_sonda_s=tope_s)


async def _get(app, ruta):
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://r") as c:
            return await c.get(ruta)


def test_healthz_verifica_las_sondas():
    def caida():
        raise ConnectionError("sin mongo")

    sano = asyncio.run(_get(_app({"mongo": lambda: None, "falkordb": lambda: None}), "/healthz"))
    assert sano.status_code == 200 and sano.json() == {"estado": "ok", "mongo": "ok", "falkordb": "ok"}

    malo = asyncio.run(_get(_app({"mongo": caida, "falkordb": lambda: time.sleep(2)}, 0.2), "/healthz"))
    assert malo.status_code == 503
    assert malo.json() == {
        "estado": "degradado",
        "mongo": "error: ConnectionError",
        "falkordb": "sin respuesta",
    }

    vida = asyncio.run(_get(_app({"mongo": caida}), "/livez"))
    assert vida.status_code == 200


def test_ensamblado_declara_las_sondas_de_sus_bases():
    from railspec.server.app import _sondas

    config = Configuracion.desde_entorno({"RAILSPEC_MONGO_URI": "mongodb://x"})
    assert set(_sondas(config, object(), MotorMemoria())) == {"mongo", "falkordb"}
    assert _sondas(Configuracion.desde_entorno({}), object(), None) == {}
