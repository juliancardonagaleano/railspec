"""Sesión, tokens y cabeceras de la consola: endurecimiento de la hoja de hallazgos (A3, B1-B10)."""

from __future__ import annotations

import pytest
from railspec.server.api.superficies import aplicacion
from railspec.server.consola import ConfigConsola, montar_consola
from railspec.server.consola.sesion import Firmador
from test_consola import Montaje

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
