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
    cliente_github: Any | None = None,
) -> tuple[Motor, Any]:
    """Construye motor y app ASGI. ``proveedores``, ``gobernanza``, ``motor_grafo`` (un
    ``MotorGrafo`` de railspec-graph), ``verificador_oidc`` y ``cliente_github`` (HTTP
    del inicio de sesión de la consola) permiten inyectar dobles (pruebas y entorno de
    integración sin credenciales)."""

    from .api.identidad import (
        IdentidadCompuesta,
        IdentidadDesarrollo,
        IdentidadGithub,
        VerificadorOidcActions,
    )
    from .api.registro import AutorizadorRoles, Registro
    from .api.superficies import aplicacion
    from .consola import montar_consola
    from .consola.almacen import AlmacenConsola
    from .consola.contexto import ContextoConsola
    from .consola.github import ClienteGithub
    from .consola.sesion import Firmador, IdentidadConConsola

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
    firmador = Firmador(config.consola.secreto_sesion)
    # Los tokens ``rsc1`` de la consola valen como Bearer en /v1 (tools y chat).
    identidad = IdentidadConConsola(IdentidadCompuesta(humana, verificador_oidc), firmador)
    extra = {}
    if grafo is not None:
        from railspec.graph import IndexadorCanonico

        from .api.grafo import manejador_graph_index, manejador_graph_query

        extra["graph.query"] = manejador_graph_query(grafo, almacen)
        extra["graph.index"] = manejador_graph_index(IndexadorCanonico(acceso, grafo), almacen)
    registro = Registro.del_motor(motor, AutorizadorRoles(almacen, abierto=config.modo_memoria), extra)
    sondas = _sondas(config, almacen, motor_grafo)
    app = aplicacion(registro, identidad, host=config.host, sondas=sondas)
    consola = ContextoConsola(
        config=config.consola,
        firmador=firmador,
        datos=AlmacenConsola(almacen.db),
        almacen=almacen,
        registro=registro,
        identidad=identidad,
        github=ClienteGithub(config.consola.github_app, cliente_github),
        logins_desarrollo={login: gid for login, gid in config.tokens_desarrollo.values()},
        tokens_desarrollo=dict(config.tokens_desarrollo),
        grafo=grafo,
        acceso_grafo=acceso,
        abierto=config.modo_memoria,
    )
    montar_consola(app, consola)
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
