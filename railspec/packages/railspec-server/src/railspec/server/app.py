"""Ensamblado del servidor desde ``Configuracion`` y punto de entrada ``railspec-server``."""

from __future__ import annotations

import logging
from typing import Any

from railspec.contracts.comun import Proveedor

from .config import Configuracion
from .estado import CheckpointsMongo, almacen_desde_uri, almacen_en_memoria
from .motor import Motor, Nucleo
from .motor.gobernanza import GobernanzaNoConfigurada, GobernanzaPceMcp, ProveedorGobernanza
from .proveedores import Proveedores

log = logging.getLogger("railspec.server")


def ensamblar(
    config: Configuracion,
    *,
    proveedores: Proveedores | None = None,
    gobernanza: ProveedorGobernanza | None = None,
    motor_grafo: Any | None = None,
    verificador_oidc: Any | None = None,
    fuente_codigo: Any | None = None,
) -> tuple[Motor, Any]:
    """Construye motor y app ASGI. ``proveedores``, ``gobernanza``, ``motor_grafo`` (un
    ``MotorGrafo`` de railspec-graph), ``verificador_oidc`` y ``fuente_codigo`` (clones
    canónicos para ``code.read``) permiten inyectar dobles (pruebas y entorno de
    integración sin credenciales)."""

    from .api.identidad import (
        IdentidadCompuesta,
        IdentidadDesarrollo,
        IdentidadGithub,
        VerificadorOidcActions,
    )
    from .api.registro import AutorizadorRoles, Registro
    from .api.superficies import aplicacion

    if config.modo_memoria:
        log.warning("sin RAILSPEC_MONGO_URI: estado en memoria, solo para desarrollo")
        almacen = almacen_en_memoria()
    else:
        almacen = almacen_desde_uri(config.mongo_uri, config.mongo_db)
    if proveedores is None:
        proveedores = _proveedores(config)
    if gobernanza is None:
        gobernanza = (
            GobernanzaPceMcp(config.pce_url, config.pce_api_key)
            if config.pce_url
            else GobernanzaNoConfigurada()
        )
    if motor_grafo is None and config.falkordb_url is not None:
        from railspec.graph.motor_falkordb import MotorFalkor

        motor_grafo = MotorFalkor.desde_url(config.falkordb_url)
    acceso = grafo = None
    if motor_grafo is not None:
        from railspec.graph import AccesoGrafo, AlmacenGrafo

        acceso = AccesoGrafo(motor_grafo)
        grafo = AlmacenGrafo(acceso)
    nucleo = Nucleo(almacen=almacen, proveedores=proveedores, gobernanza=gobernanza, grafo=grafo)
    motor = Motor(nucleo, CheckpointsMongo(almacen.db))
    humana = IdentidadDesarrollo(config.tokens_desarrollo) if config.tokens_desarrollo else IdentidadGithub()
    if verificador_oidc is None and config.oidc_audiencia:
        verificador_oidc = VerificadorOidcActions(
            config.oidc_audiencia, config.oidc_emisor, config.oidc_repositorios
        )
    identidad = IdentidadCompuesta(humana, verificador_oidc)
    extra = {}
    if grafo is not None:
        from railspec.graph import IndexadorCanonico

        from .api.grafo import manejador_graph_index, manejador_graph_query

        extra["graph.query"] = manejador_graph_query(grafo, almacen)
        extra["graph.index"] = manejador_graph_index(IndexadorCanonico(acceso, grafo), almacen)
    from .chat.almacen import AlmacenChat
    from .chat.codigo import ClonesGit, manejador_code_read
    from .chat.http import router_chat
    from .chat.resolucion import ResolutorInsumos
    from .chat.servicio import ConfigChat, ServicioChat, manejador_insumo_get

    chat = AlmacenChat(almacen.db)
    if fuente_codigo is None and config.chat_clones:
        fuente_codigo = ClonesGit(config.chat_clones)
    nucleo.insumos = ResolutorInsumos(chat, almacen, fuente_codigo)
    if fuente_codigo is not None:
        extra["code.read"] = manejador_code_read(fuente_codigo, almacen)
    extra["insumo.get"] = manejador_insumo_get(chat)
    autorizador = AutorizadorRoles(almacen, abierto=config.modo_memoria)
    registro = Registro.del_motor(motor, autorizador, extra)
    servicio_chat = ServicioChat(
        almacen=almacen,
        chat=chat,
        registro=registro,
        autorizador=autorizador,
        proveedores=proveedores,
        fuente=fuente_codigo,
        config=ConfigChat(modelos={Proveedor.foundry: config.chat_modelo}, zona_datos=config.chat_zona_datos),
    )
    sondas = _sondas(config, almacen, motor_grafo)
    app = aplicacion(registro, identidad, host=config.host, sondas=sondas)
    app.include_router(router_chat(servicio_chat, identidad))
    return motor, app


def _sondas(config: Configuracion, almacen: Any, motor_grafo: Any | None) -> dict[str, Any]:
    """Comprobaciones ligeras de ``/healthz``: una réplica sin sus bases no debe recibir tráfico."""

    sondas: dict[str, Any] = {}
    if not config.modo_memoria:
        sondas["mongo"] = lambda: almacen.db.command("ping")
    if motor_grafo is not None and hasattr(motor_grafo, "ping"):
        sondas["falkordb"] = motor_grafo.ping
    return sondas


def _proveedores(config: Configuracion) -> Proveedores:
    from .proveedores.maf import adaptador_anthropic, adaptador_foundry

    disponibles = {}
    if config.foundry is not None:
        disponibles[Proveedor.foundry] = adaptador_foundry(config.foundry.endpoint, config.foundry.api_key)
    if config.anthropic is not None:
        disponibles[Proveedor.anthropic] = adaptador_anthropic(config.anthropic.api_key)
    if not disponibles:
        log.warning("sin proveedores de modelo: todo gate escalará con error-proveedor")
    return Proveedores(disponibles)


def main() -> None:  # pragma: no cover - arranque real
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    config = Configuracion.desde_entorno()
    _, app = ensamblar(config)
    uvicorn.run(app, host=config.host, port=config.puerto)


if __name__ == "__main__":  # pragma: no cover
    main()
