"""Ensamblado del servidor desde ``Configuracion`` y punto de entrada ``railspec-server``."""

from __future__ import annotations

import logging
from typing import Any

from railspec.contracts.comun import Proveedor

from .config import Configuracion
from .estado import CheckpointsMongo, almacen_desde_uri, almacen_en_memoria
from .motor import Motor, Nucleo
from .motor.gobernanza import ProveedorGobernanza
from .proveedores import Proveedores

log = logging.getLogger("railspec.server")


def ensamblar(
    config: Configuracion,
    *,
    proveedores: Proveedores | None = None,
    gobernanza: ProveedorGobernanza | None = None,
    motor_grafo: Any | None = None,
    verificador_oidc: Any | None = None,
) -> tuple[Motor, Any]:
    """Construye motor y app ASGI. ``proveedores``, ``gobernanza``, ``motor_grafo`` (un
    ``MotorGrafo`` de railspec-graph) y ``verificador_oidc`` permiten inyectar dobles
    (pruebas y entorno de integración sin credenciales)."""

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
        proveedores = _proveedores(config, almacen)
    if gobernanza is None:
        gobernanza = _contexto(config, almacen)
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
    from .portabilidad import manejadores as manejadores_portabilidad

    extra = manejadores_portabilidad(motor)
    if grafo is not None:
        from railspec.graph import IndexadorCanonico

        from .api.grafo import manejador_graph_index, manejador_graph_query

        extra["graph.query"] = manejador_graph_query(grafo, almacen)
        extra["graph.index"] = manejador_graph_index(IndexadorCanonico(acceso, grafo), almacen)
    registro = Registro.del_motor(motor, AutorizadorRoles(almacen, abierto=config.modo_memoria), extra)
    sondas = _sondas(config, almacen, motor_grafo)
    return motor, aplicacion(registro, identidad, host=config.host, sondas=sondas)


def _sondas(config: Configuracion, almacen: Any, motor_grafo: Any | None) -> dict[str, Any]:
    """Comprobaciones ligeras de ``/healthz``: una réplica sin sus bases no debe recibir tráfico."""

    sondas: dict[str, Any] = {}
    if not config.modo_memoria:
        sondas["mongo"] = lambda: almacen.db.command("ping")
    if motor_grafo is not None and hasattr(motor_grafo, "ping"):
        sondas["falkordb"] = motor_grafo.ping
    return sondas


def _proveedores(config: Configuracion, almacen: Any) -> Proveedores:
    from .proveedores.catalogo import Catalogo, FuenteAnthropic, FuenteFoundryDeclarada, FuenteFoundryProyecto
    from .proveedores.claude import AdaptadorClaude, cliente_anthropic
    from .proveedores.foundry import proveedor_foundry, proveedor_token_entra

    disponibles: dict[Proveedor, Any] = {}
    fuentes: list[Any] = []
    f = config.foundry
    if f is not None:
        disponibles[Proveedor.foundry] = proveedor_foundry(f.endpoint, f.api_key, f.region)
        if f.region is None:
            log.warning("sin RAILSPEC_FOUNDRY_REGION: restringido e interno no tendrán modelo en zona")
        # Siempre con catálogo: sin despliegues declarados ni proyecto, el catálogo
        # queda vacío y ningún perfil se satisface. Mejor que adivinar el SKU (un
        # despliegue Global no sirve a restringido ni a interno).
        if not (f.proyecto or f.despliegues):
            log.warning(
                "Foundry sin RAILSPEC_FOUNDRY_PROYECTO ni RAILSPEC_FOUNDRY_DESPLIEGUES: catálogo vacío"
            )
        fuentes.append(
            _FuentesFoundry(config, FuenteFoundryDeclarada, FuenteFoundryProyecto, proveedor_token_entra)
        )
    if config.anthropic is not None:
        cliente = cliente_anthropic(config.anthropic.api_key)
        disponibles[Proveedor.anthropic] = AdaptadorClaude(Proveedor.anthropic, cliente, region="global")
        fuentes.append(FuenteAnthropic(cliente))
    if not disponibles:
        log.warning("sin proveedores de modelo: unit.start rechazará los perfiles (perfil-insatisfacible)")
    catalogo = Catalogo(fuentes, almacen, ttl_s=config.catalogo_ttl_s) if fuentes else None
    from .proveedores.cache import CacheNodos

    cache = CacheNodos(almacen, config.cache_nodos_s) if config.cache_nodos_s > 0 else None
    return Proveedores(disponibles, catalogo, zona_recurso=f.zona_datos if f else None, cache=cache)


class _FuentesFoundry:
    """Despliegues del proyecto (API) más los declarados; los declarados ganan por nombre."""

    proveedor = Proveedor.foundry

    def __init__(self, config: Configuracion, declarada: Any, proyecto: Any, token: Any) -> None:
        f = config.foundry
        assert f is not None
        self._declarada = declarada(f.despliegues, f.region, f.zona_datos) if f.despliegues else None
        self._proyecto = None
        if f.proyecto:
            from .proveedores.claude import SCOPE_FOUNDRY_ANTHROPIC

            self._proyecto = proyecto(
                f.proyecto,
                f.api_key,
                f.region,
                f.zona_datos,
                f.proyecto_api_version,
                None if f.api_key else token(SCOPE_FOUNDRY_ANTHROPIC),
            )

    async def leer(self) -> list[Any]:
        declaradas = await self._declarada.leer() if self._declarada else []
        leidas = await self._proyecto.leer() if self._proyecto else []
        nombres = {e.destino for e in declaradas}
        return declaradas + [e for e in leidas if e.destino not in nombres]


def _contexto(config: Configuracion, almacen: Any) -> ProveedorGobernanza:
    from railspec.contracts.repositorio import RolContexto

    from .contexto import ContextoConectable, FuenteContexto, ResolutorSecretos

    defecto = []
    if config.pce_url:
        defecto.append(
            FuenteContexto(
                rol=RolContexto.gobernanza, nombre="pce", url=config.pce_url, api_key=config.pce_api_key
            )
        )
    return ContextoConectable(
        almacen, defecto, ResolutorSecretos(config.secretos_dir), cache_s=config.contexto_cache_s
    )


def main() -> None:  # pragma: no cover - arranque real
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    config = Configuracion.desde_entorno()
    _, app = ensamblar(config)
    uvicorn.run(app, host=config.host, port=config.puerto)


if __name__ == "__main__":  # pragma: no cover
    main()
