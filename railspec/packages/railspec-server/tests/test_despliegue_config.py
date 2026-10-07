"""El ConfigMap que renderiza ``railspec/deploy`` lo acepta ``Configuracion.desde_entorno`` tal cual.

Las pruebas de ``railspec/deploy/tests`` comprueban que cada variable sale en el manifiesto; estas
comprueban el otro extremo: que el servidor las lee con el mismo significado y que los defectos del
renderizador no se desvían de los del servidor.
"""

from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest
from railspec.server.config import Configuracion
from railspec.server.consola.config import ConfigConsola

RENDERIZAR = Path(__file__).resolve().parents[3] / "deploy" / "renderizar.py"
if not RENDERIZAR.is_file():  # el paquete probado fuera del repositorio
    pytest.skip("sin railspec/deploy/renderizar.py", allow_module_level=True)
_spec = importlib.util.spec_from_file_location("railspec_deploy_renderizar_servidor", RENDERIZAR)
renderizar = importlib.util.module_from_spec(_spec)
sys.modules[_spec.name] = renderizar
_spec.loader.exec_module(renderizar)

MINIMO = {
    "RAILSPEC_IMAGEN": "registro.azurecr.io/railspec-server:ci",
    "RAILSPEC_DOMINIO": "railspec.example.com",
    "RAILSPEC_TLS_SECRETO": "railspec-tls",
}
#: Lo que aporta el Secret: con URL pública https el servidor exige la clave de sesión.
SECRETO = {"RAILSPEC_MONGO_URI": "mongodb://mongo", "RAILSPEC_CONSOLA_SECRETO": "s" * 48}


def _configmap(entorno: dict[str, str]) -> dict[str, str]:
    texto = renderizar.renderizar({**MINIMO, **entorno})
    documento = next(d for d in texto.split("---\n") if "kind: ConfigMap" in d)
    return dict(re.findall(r'^  ([A-Z][A-Z0-9_]*): "(.*)"$', documento, re.M))


def test_los_defectos_del_renderizador_son_los_del_servidor():
    config = Configuracion.desde_entorno({**_configmap({}), **SECRETO})
    por_defecto = Configuracion()
    assert config.consola.horas_sesion == ConfigConsola().horas_sesion
    assert config.consola.limite_auth_minuto == ConfigConsola().limite_auth_minuto
    assert config.consola.sse_max_por_usuario == ConfigConsola().sse_max_por_usuario
    assert config.consola.sse_max_global == ConfigConsola().sse_max_global
    assert config.consola.sse_revalidar_s == ConfigConsola().sse_revalidar_s
    # Sin clones el chat no lee código; ya no hay zona de datos que exigirle (decisión del 2026-10-06).
    assert config.chat_clones is None and not hasattr(config, "chat_zona_datos")
    assert config.chat_modelo == por_defecto.chat_modelo


def test_el_servidor_lee_lo_que_renderiza_el_chat_y_la_consola():
    config = Configuracion.desde_entorno(
        {
            **_configmap(
                {
                    "RAILSPEC_CHAT_MODELO": "claude-opus-5-5",
                    "RAILSPEC_CHAT_CLONES_PVC": "railspec-clones",
                    "RAILSPEC_CONSOLA_SESION_HORAS": "8",
                    "RAILSPEC_CONSOLA_AUTH_LIMITE": "0",
                    "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": "3",
                    "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL": "50",
                    "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": "12.5",
                }
            ),
            **SECRETO,
        }
    )
    assert config.chat_modelo == "claude-opus-5-5"
    assert config.chat_clones == renderizar.RUTA_CLONES
    consola = config.consola
    assert (consola.horas_sesion, consola.limite_auth_minuto) == (8, 0)
    assert (consola.sse_max_por_usuario, consola.sse_max_global, consola.sse_revalidar_s) == (3, 50, 12.5)


@pytest.mark.parametrize(
    "entorno",
    [
        {"RAILSPEC_CONSOLA_SESION_HORAS": "24", "RAILSPEC_CONSOLA_SSE_REVALIDAR_S": "0.5"},
        {"RAILSPEC_CONSOLA_AUTH_LIMITE": "0", "RAILSPEC_CONSOLA_SSE_MAX_USUARIO": "1"},
    ],
)
def test_lo_que_el_renderizador_admite_en_los_limites_el_servidor_tambien(entorno):
    assert Configuracion.desde_entorno({**_configmap(entorno), **SECRETO})
