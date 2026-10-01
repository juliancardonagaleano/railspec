"""Sesión, tokens y cabeceras de la consola: endurecimiento de la hoja de hallazgos (A3, B1-B10)."""

from __future__ import annotations

import base64
import json
from datetime import timedelta

import httpx
import pytest
from railspec.server.api.identidad import TokenInvalido
from railspec.server.api.superficies import aplicacion
from railspec.server.consola import ConfigConsola, montar_consola
from railspec.server.consola.sesion import COOKIE, Firmador
from test_consola import Montaje, correr

SECRETO = "s" * 40


# --- B1: secreto de sesión fuerte -----------------------------------------------------------------


def test_firmador_rechaza_un_secreto_debil():
    with pytest.raises(ValueError, match="32"):
        Firmador("a")
    with pytest.raises(ValueError, match="32"):
        Firmador("x" * 31)
    assert not Firmador("x" * 32).efimero


def test_sin_secreto_el_firmador_es_efimero():
    assert Firmador(None).efimero and Firmador("").efimero


def test_config_exige_secreto_con_https_o_github_app():
    https = {"RAILSPEC_CONSOLA_URL": "https://railspec.acme.com"}
    with pytest.raises(ValueError, match="RAILSPEC_CONSOLA_SECRETO"):
        ConfigConsola.desde_entorno(https)
    with pytest.raises(ValueError, match="32"):
        ConfigConsola.desde_entorno({**https, "RAILSPEC_CONSOLA_SECRETO": "corto"})
    app = {
        "RAILSPEC_GITHUB_APP_CLIENT_ID": "Iv1.x",
        "RAILSPEC_GITHUB_APP_CLIENT_SECRET": "s",
        "RAILSPEC_CONSOLA_URL": "http://localhost:8080",
    }
    with pytest.raises(ValueError, match="RAILSPEC_CONSOLA_SECRETO"):
        ConfigConsola.desde_entorno(app)
    assert ConfigConsola.desde_entorno({**https, "RAILSPEC_CONSOLA_SECRETO": SECRETO}).cookie_segura
    assert ConfigConsola.desde_entorno({**app, "RAILSPEC_CONSOLA_SECRETO": SECRETO}).github_app is not None


def test_config_de_desarrollo_mantiene_la_clave_efimera():
    # http local y sin GitHub App: sin secreto sigue valiendo (con aviso); uno débil, nunca.
    local = {"RAILSPEC_CONSOLA_URL": "http://localhost:8080"}
    assert ConfigConsola.desde_entorno(local).secreto_sesion is None
    assert ConfigConsola.desde_entorno({}).secreto_sesion is None
    with pytest.raises(ValueError, match="32"):
        ConfigConsola.desde_entorno({**local, "RAILSPEC_CONSOLA_SECRETO": "k"})


def test_montar_consola_rechaza_un_firmador_efimero_con_https():
    m = Montaje()  # URL pública https y GitHub App
    m.ctx.firmador = Firmador(None)
    app = aplicacion(m.ctx.registro, m.ctx.identidad)
    with pytest.raises(ValueError, match="RAILSPEC_CONSOLA_SECRETO"):
        montar_consola(app, m.ctx)


# --- B2: tokens malformados dan 401, no 500 ------------------------------------------------------------

# Latin-1 que no es ASCII: así llega por HTTP un token con "é" en la firma.
FIRMA_NO_ASCII = b"rsc1.e30.firma-\xe9"


def test_firma_no_ascii_es_token_invalido_no_typeerror():
    with pytest.raises(TokenInvalido):
        Firmador(SECRETO).sesion(FIRMA_NO_ASCII.decode("latin-1"), "api")


def test_token_rsc1_malformado_es_401_en_consola_y_en_v1():
    async def caso():
        m = Montaje()
        async with m.cliente() as c:
            bearer = {"Authorization": b"Bearer " + FIRMA_NO_ASCII}
            assert (await c.get("/consola/api/yo", headers=bearer)).status_code == 401
            r = await c.post("/v1/tools/unit.list", json={}, headers=bearer)
            assert r.status_code == 401, r.text
            cookie = {"Cookie": COOKIE.encode() + b"=" + FIRMA_NO_ASCII}
            assert (await c.get("/consola/api/yo", headers=cookie)).status_code == 401

    correr(caso())


def test_ruta_de_la_spa_con_byte_nulo_no_da_500(tmp_path):
    (tmp_path / "index.html").write_text("<!doctype html><title>Railspec</title>")

    async def caso():
        m = Montaje()
        m.ctx.config = ConfigConsola(carpeta_spa=str(tmp_path))
        app = aplicacion(m.ctx.registro, m.ctx.identidad)
        montar_consola(app, m.ctx)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="https://railspec.test"
        ) as c:
            for ruta in ("/consola/%00", "/consola/assets/%00x", "/consola/a%00/b"):
                r = await c.get(ruta)
                assert r.status_code in (200, 404), (ruta, r.status_code)

    correr(caso())


# --- B9: una subclave por tipo y ``abrir`` exige el tipo --------------------------------------------------


def _carga(token: str) -> dict:
    texto = token.split(".")[1]
    return json.loads(base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4)))


def _uno_de_cada_tipo(f: Firmador) -> dict[str, str]:
    vida = timedelta(hours=1)
    return {
        "sesion": f.emitir("ana", 7, frozenset(), vida, "sesion"),
        "api": f.emitir("ana", 7, frozenset(), vida, "api"),
        "oauth": f.firmar("oauth", {"n": "nonce", "v": "/", "exp": int(f.ahora().timestamp()) + 600}),
    }


def test_un_token_solo_se_abre_con_su_tipo():
    f = Firmador(SECRETO)
    tokens = _uno_de_cada_tipo(f)
    for tipo, token in tokens.items():
        assert f.abrir(token, tipo)["t"] == tipo
        for otro in set(tokens) - {tipo}:
            # La firma ya no cuadra: el tipo no es solo un claim, cambia la clave del MAC.
            with pytest.raises(TokenInvalido, match="firma"):
                f.abrir(token, otro)
    # El estado de OAuth es público (lo entrega /auth/github/inicio): no sirve de sesión ni de api.
    for otro in ("sesion", "api"):
        with pytest.raises(TokenInvalido):
            f.sesion(tokens["oauth"], otro)


def test_abrir_exige_el_tipo_esperado():
    f = Firmador(SECRETO)
    token = _uno_de_cada_tipo(f)["api"]
    with pytest.raises(TypeError):
        f.abrir(token)  # type: ignore[call-arg]
    with pytest.raises(ValueError, match="tipo"):
        f.abrir(token, "admin")


def test_cambiar_el_claim_de_tipo_no_convierte_un_token_en_otro():
    f = Firmador(SECRETO)
    token = _uno_de_cada_tipo(f)["api"]
    cabecera, carga, firma = token.split(".")
    datos = _carga(token) | {"t": "sesion"}
    manipulada = base64.urlsafe_b64encode(json.dumps(datos).encode()).decode().rstrip("=")
    with pytest.raises(TokenInvalido):
        f.sesion(f"{cabecera}.{manipulada}.{firma}", "sesion")
    with pytest.raises(TokenInvalido):
        f.sesion(f"{cabecera}.{manipulada}.{firma}", "api")


def test_la_clave_depende_del_secreto_y_el_token_lleva_audiencia():
    token = _uno_de_cada_tipo(Firmador(SECRETO))
    assert (_carga(token["api"])["aud"], _carga(token["sesion"])["aud"]) == ("v1", "consola")
    with pytest.raises(TokenInvalido, match="firma"):
        Firmador("t" * 40).sesion(token["api"], "api")
