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


def test_anthropic_solo_para_nivel_abierto():
    from railspec.contracts.comun import NivelCodigo
    from railspec.server.motor.perfiles import perfil_por_defecto, requisito
    from railspec.server.proveedores import PerfilInsatisfacible

    solo_anthropic = Proveedores(
        {Proveedor.anthropic: ProveedorGuionado(lambda p: None, Proveedor.anthropic)}
    )
    from apoyo_motor import WS_ALCANCE

    req = requisito(perfil_por_defecto(WS_ALCANCE, "estandar"), "critico-profundo")
    with pytest.raises(PerfilInsatisfacible):
        solo_anthropic.elegir("critico-profundo", req, NivelCodigo.restringido)
    assert (
        solo_anthropic.elegir("critico-profundo", req, NivelCodigo.abierto).proveedor.proveedor
        == Proveedor.anthropic
    )


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

    config = Configuracion.desde_entorno({"RAILSPEC_TOKENS_DESARROLLO": "t=julian:1"})
    motor, app = ensamblar(config)
    assert motor.n.grafo is None and motor.n.proveedores.configurados == []
    rutas = {getattr(r, "path", None) for r in app.routes}
    assert {"/healthz", "/v1/tools", "/v1/tools/{nombre}", "/mcp"} <= rutas
