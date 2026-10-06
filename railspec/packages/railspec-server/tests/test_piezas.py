"""Configuración, adaptador de proveedor MAF, checkpoints y ensamblado."""

from __future__ import annotations

import asyncio

import pytest
from railspec.contracts.comun import Proveedor
from railspec.server.config import Configuracion
from railspec.server.proveedores import Proveedores
from railspec.server.proveedores.falso import ProveedorGuionado


def test_configuracion_desde_entorno():
    c = Configuracion.desde_entorno({})
    assert c.modo_memoria and c.foundry is None and c.anthropic is None and c.puerto == 8080
    c = Configuracion.desde_entorno(
        {
            "RAILSPEC_MONGO_URI": "mongodb://m:27017",
            "RAILSPEC_FOUNDRY_ENDPOINT": "https://acme.services.ai.azure.com/",
            "RAILSPEC_ANTHROPIC_HABILITADO": "si",
            "RAILSPEC_ANTHROPIC_API_KEY": "sk",
            "RAILSPEC_TOKENS_DESARROLLO": "a=julian:83125327, b=ana:7",
            "RAILSPEC_FALKORDB_URL": "redis://falkor:6379",
            "RAILSPEC_PUERTO": "9000",
        }
    )
    assert not c.modo_memoria and c.foundry.endpoint == "https://acme.services.ai.azure.com"
    assert c.foundry.api_key is None and c.anthropic.api_key == "sk"
    assert c.tokens_desarrollo == {"a": ("julian", 83125327), "b": ("ana", 7)}
    assert c.falkordb_url == "redis://falkor:6379" and c.puerto == 9000
    with pytest.raises(ValueError):
        Configuracion.desde_entorno({"RAILSPEC_ANTHROPIC_HABILITADO": "1"})
    with pytest.raises(ValueError):
        Configuracion.desde_entorno({"RAILSPEC_TOKENS_DESARROLLO": "a=julian"})


def test_anthropic_directo_sirve_a_cualquier_repositorio_sin_suscripcion():
    from railspec.server.motor.perfiles import perfil_por_defecto, requisito
    from railspec.server.proveedores import PerfilInsatisfacible

    solo_anthropic = Proveedores(
        {Proveedor.anthropic: ProveedorGuionado(lambda p: None, Proveedor.anthropic, region="global")}
    )
    from apoyo_motor import WS_ALCANCE

    req = requisito(perfil_por_defecto(WS_ALCANCE, "estandar"), "critico-profundo")
    # Sin nivel ni zona: lo sirven por igual los repositorios restringidos, internos y abiertos.
    e = solo_anthropic.elegir("critico-profundo", req)
    assert e.proveedor.proveedor == Proveedor.anthropic and e.region == "global"
    assert solo_anthropic.validar([("critico-profundo", req)]) == []
    # Foundry va primero; sin él ni Anthropic el motivo nombra a los dos.
    with pytest.raises(PerfilInsatisfacible, match="foundry no configurado"):
        Proveedores({}).elegir("critico-profundo", req)


def test_checkpoints_mongo_ida_y_vuelta():
    from agent_framework import WorkflowCheckpoint
    from apoyo_motor import ORG, WS
    from railspec.contracts.comun import AlcanceUnidad
    from railspec.server.estado import CheckpointsMongo, almacen_en_memoria, nombre_workflow

    almacen = almacen_en_memoria()
    cps = CheckpointsMongo(almacen.db)
    nombre = nombre_workflow(AlcanceUnidad(org=ORG, workspace=WS, unidad="0001-x"))

    async def caso():
        a = WorkflowCheckpoint(workflow_name=nombre, graph_signature_hash="h", state={"k": [1, 2]})
        b = WorkflowCheckpoint(
            workflow_name=nombre, graph_signature_hash="h", previous_checkpoint_id=a.checkpoint_id
        )
        await cps.save(a)
        await cps.save(b)
        leido = await cps.load(a.checkpoint_id)
        assert leido.state == {"k": [1, 2]}
        assert (await cps.get_latest(workflow_name=nombre)).checkpoint_id == b.checkpoint_id
        assert len(await cps.list_checkpoints(workflow_name=nombre)) == 2
        assert await cps.delete(a.checkpoint_id)
        assert await cps.list_checkpoint_ids(workflow_name=nombre) == [b.checkpoint_id]

    asyncio.run(caso())
    doc = almacen.db.checkpoints.find_one({})
    assert doc["org"] == ORG and doc["workspace"] == WS and doc["unidad"] == "0001-x"


def test_ensamblado_en_memoria():
    from railspec.server.app import ensamblar

    config = Configuracion.desde_entorno(
        {"RAILSPEC_TOKENS_DESARROLLO": "t=julian:1", "RAILSPEC_PERMITIR_DESARROLLO": "1"}
    )
    motor, app = ensamblar(config)
    assert motor.n.grafo is None and motor.n.proveedores.configurados == []
    rutas = {getattr(r, "path", None) for r in app.routes}
    assert {"/healthz", "/v1/tools", "/v1/tools/{nombre}", "/mcp"} <= rutas


# --- arranque seguro: modo desarrollo explícito (M4, B11) ------------------------------


def test_permitir_desarrollo_desde_entorno():
    assert Configuracion.desde_entorno({}).permitir_desarrollo is False
    for valor in ("", "0", "no", "false"):
        assert not Configuracion.desde_entorno({"RAILSPEC_PERMITIR_DESARROLLO": valor}).permitir_desarrollo
    for valor in ("1", "true", "si"):
        assert Configuracion.desde_entorno({"RAILSPEC_PERMITIR_DESARROLLO": valor}).permitir_desarrollo


def test_sin_mongo_no_arranca_sin_la_bandera_de_desarrollo():
    """B11: sin Mongo todo el mundo autenticado era ``desarrollador`` en cualquier org y /healthz daba 200."""

    from railspec.server.app import ensamblar
    from railspec.server.config import ErrorConfiguracion

    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_PERMITIR_DESARROLLO=1"):
        ensamblar(Configuracion())
    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_MONGO_URI"):
        ensamblar(Configuracion(permitir_desarrollo=False))
    motor, app = ensamblar(Configuracion(permitir_desarrollo=True))
    assert motor is not None and app is not None


def test_tokens_de_desarrollo_con_mongo_o_github_app_no_arrancan_sin_la_bandera():
    """M4: una clave sobrante en el Secret sustituía la identidad de GitHub y abría POST /auth/desarrollo."""

    from railspec.server.config import ErrorConfiguracion, validar_arranque
    from railspec.server.consola.config import ConfigConsola, ConfigGithubApp

    tokens = {"tk": ("ana", 7)}
    app = ConfigConsola(github_app=ConfigGithubApp("Iv1.x", "s"), url_publica="https://r.example")
    for config in (
        Configuracion(mongo_uri="mongodb://m", tokens_desarrollo=tokens),
        Configuracion(mongo_uri="mongodb://m", tokens_desarrollo=tokens, consola=app),
    ):
        with pytest.raises(ErrorConfiguracion, match="RAILSPEC_TOKENS_DESARROLLO"):
            validar_arranque(config)
    # GitHub App sin Mongo: ya lo frena el modo memoria, también sin la bandera.
    with pytest.raises(ErrorConfiguracion, match="RAILSPEC_PERMITIR_DESARROLLO=1"):
        validar_arranque(Configuracion(tokens_desarrollo=tokens, consola=app))
    # Con Mongo y sin tokens, o en memoria con la bandera, no hay nada que objetar.
    validar_arranque(Configuracion(mongo_uri="mongodb://m"))
    validar_arranque(Configuracion(permitir_desarrollo=True, tokens_desarrollo=tokens))


def test_la_bandera_de_desarrollo_deja_un_warning_en_el_arranque(caplog):
    import logging

    from railspec.server.config import validar_arranque

    tokens = {"tk": ("ana", 7)}
    with caplog.at_level(logging.WARNING, logger="railspec.server"):
        validar_arranque(Configuracion(mongo_uri="mongodb://m"))
    assert not caplog.records
    with caplog.at_level(logging.WARNING, logger="railspec.server"):
        validar_arranque(
            Configuracion(mongo_uri="mongodb://m", permitir_desarrollo=True, tokens_desarrollo=tokens)
        )
    avisos = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert avisos and "RAILSPEC_PERMITIR_DESARROLLO" in avisos[0].getMessage()
    assert "tokens de desarrollo" in " ".join(r.getMessage() for r in avisos)


def test_auth_config_solo_anuncia_desarrollo_si_el_modo_esta_permitido():
    import httpx
    from railspec.server.app import ensamblar

    async def auth_config(config):
        _, app = ensamblar(config)
        async with app.router.lifespan_context(app):
            transporte = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transporte, base_url="http://r") as c:
                r = await c.get("/consola/api/auth/config")
                intento = await c.post(
                    "/consola/api/auth/desarrollo",
                    json={"token": "tk"},
                    headers={"X-Railspec-Consola": "1"},
                )
                return r.json(), intento.status_code

    permitido = Configuracion(permitir_desarrollo=True, tokens_desarrollo={"tk": ("ana", 7)})
    anuncio, intento = asyncio.run(auth_config(permitido))
    assert anuncio == {"github": False, "desarrollo": True} and intento == 204
    # Modo permitido pero sin tokens: nada que anunciar ni aceptar.
    anuncio, intento = asyncio.run(auth_config(Configuracion(permitir_desarrollo=True)))
    assert anuncio == {"github": False, "desarrollo": False} and intento == 401
