"""Ensamblado del servidor desde ``Configuracion`` y punto de entrada ``railspec-server``."""

from __future__ import annotations

import logging
from typing import Any

from railspec.contracts.comun import Proveedor

from .config import Configuracion
from .estado import CheckpointsMongo, almacen_desde_uri, almacen_en_memoria
from .motor import Motor, Nucleo
from .motor.gobernanza import GobernanzaNoConfigurada, GobernanzaPceMcp
from .proveedores import Proveedores

log = logging.getLogger("railspec.server")


def ensamblar(config: Configuracion, *, proveedores: Proveedores | None = None) -> tuple[Motor, Any]:
    """Construye motor y app ASGI. ``proveedores`` permite inyectar dobles."""

    from .api.identidad import IdentidadDesarrollo, IdentidadGithub
    from .api.registro import AutorizadorRoles, Registro
    from .api.superficies import aplicacion

    if config.modo_memoria:
        log.warning("sin RAILSPEC_MONGO_URI: estado en memoria, solo para desarrollo")
        almacen = almacen_en_memoria()
    else:
        almacen = almacen_desde_uri(config.mongo_uri, config.mongo_db)
    if proveedores is None:
        proveedores = _proveedores(config)
    gobernanza = (
        GobernanzaPceMcp(config.pce_url, config.pce_api_key) if config.pce_url else GobernanzaNoConfigurada()
    )
    nucleo = Nucleo(almacen=almacen, proveedores=proveedores, gobernanza=gobernanza, grafo=_grafo(config))
    motor = Motor(nucleo, CheckpointsMongo(almacen.db))
    identidad = (
        IdentidadDesarrollo(config.tokens_desarrollo) if config.tokens_desarrollo else IdentidadGithub()
    )
    extra = {}
    if nucleo.grafo is not None:
        from .api.grafo import manejador_graph_query

        extra["graph.query"] = manejador_graph_query(nucleo.grafo, almacen)
    registro = Registro.del_motor(motor, AutorizadorRoles(almacen, abierto=config.modo_memoria), extra)
    return motor, aplicacion(registro, identidad, host=config.host)


def _grafo(config: Configuracion) -> Any:
    if config.falkordb_url is None:
        return None
    from railspec.graph import AccesoGrafo, AlmacenGrafo
    from railspec.graph.motor_falkordb import MotorFalkor

    return AlmacenGrafo(AccesoGrafo(MotorFalkor.desde_url(config.falkordb_url)))


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
