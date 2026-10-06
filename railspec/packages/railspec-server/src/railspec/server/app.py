"""Ensamblado del servidor desde ``Configuracion`` y punto de entrada ``railspec-server``."""

from __future__ import annotations

import logging
from typing import Any

from railspec.contracts.comun import Proveedor

from .config import Configuracion, validar_arranque
from .estado import (
    CheckpointsMongo,
    almacen_desde_postgres,
    almacen_desde_uri,
    almacen_en_memoria,
    asegurar_esquema,
)
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
    fuente_codigo: Any | None = None,
    cliente_github: Any | None = None,
) -> tuple[Motor, Any]:
    """Construye motor y app ASGI. ``proveedores``, ``gobernanza``, ``motor_grafo`` (un
    ``MotorGrafo`` de railspec-graph), ``verificador_oidc``, ``fuente_codigo`` (clones
    canónicos para ``code.read``) y ``cliente_github`` (HTTP del inicio de sesión de la
    consola) permiten inyectar dobles (pruebas y entorno de
    integración sin credenciales)."""

    from .api.identidad import (
        IdentidadCompuesta,
        IdentidadDesarrollo,
        IdentidadGithub,
        VerificadorOidcActions,
    )
    from .api.registro import AutorizadorRoles, Registro
    from .api.renovacion import RenovadorGithub, router_renovacion
    from .api.superficies import aplicacion
    from .consola import montar_consola
    from .consola.almacen import AlmacenConsola
    from .consola.contexto import ContextoConsola
    from .consola.github import ClienteGithub
    from .consola.sesion import Firmador, IdentidadConConsola

    # Antes de abrir ninguna base: la configuración insegura no arranca (M4, B11).
    validar_arranque(config)
    if config.modo_memoria:
        log.warning(
            "sin RAILSPEC_MONGO_URI ni RAILSPEC_POSTGRES_URL: estado en memoria, solo para desarrollo"
        )
        almacen = almacen_en_memoria()
    elif config.postgres_url:
        almacen = almacen_desde_postgres(config.postgres_url, config.postgres_esquema)
    else:
        almacen = almacen_desde_uri(config.mongo_uri, config.mongo_db)
    # Un retroceso de despliegue sobre un estado más nuevo no arranca (EsquemaIncompatible).
    esquema = asegurar_esquema(almacen.db)
    log.info("esquema del estado: versión %d", esquema.almacenada)
    from .proveedores.suscripciones import ServicioSuscripciones

    datos_consola = AlmacenConsola(almacen.db)
    suscripciones = ServicioSuscripciones(datos_consola, config.cifrador)
    if config.cifrador is None:
        log.warning(
            "sin RAILSPEC_CLAVE_MAESTRA: las suscripciones de modelos no se guardan ni se usan; "
            "las variables RAILSPEC_FOUNDRY_* / RAILSPEC_ANTHROPIC_* siguen valiendo"
        )
    if proveedores is None:
        proveedores = _proveedores(config, almacen, suscripciones)
    elif isinstance(proveedores, Proveedores) and proveedores.suscripciones is None:
        proveedores.suscripciones = suscripciones
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
    # ``validar_arranque`` ya impide tokens de desarrollo sin la bandera; se repite aquí para que ni
    # la identidad ni lo que anuncia /consola/api/auth/config dependan de esa llamada.
    tokens_desarrollo = dict(config.tokens_desarrollo) if config.permitir_desarrollo else {}
    # Los tokens de GitHub solo valen si los emitió la GitHub App de Railspec (``config.consola.github_app``);
    # sin App se rechazan, salvo en modo desarrollo explícito.
    humana = (
        IdentidadDesarrollo(tokens_desarrollo)
        if tokens_desarrollo
        else IdentidadGithub(
            cliente_github, app=config.consola.github_app, permitir_sin_app=config.permitir_desarrollo
        )
    )
    if verificador_oidc is None and config.oidc_audiencia:
        if config.oidc_audiencia.lower() == "railspec":
            raise ValueError(
                "RAILSPEC_OIDC_AUDIENCIA=railspec es adivinable (era el valor de los ejemplos): "
                "usa un valor largo y aleatorio, el mismo que la variable del workflow de reindexado"
            )
        # Lanza ValueError si falta RAILSPEC_OIDC_REPOSITORIOS: el servidor no arranca.
        verificador_oidc = VerificadorOidcActions(
            config.oidc_audiencia, config.oidc_emisor, config.oidc_repositorios
        )
    firmador = Firmador(config.consola.secreto_sesion)
    # Los tokens ``rsc1`` de la consola valen como Bearer en /v1 (tools y chat).
    identidad = IdentidadConConsola(IdentidadCompuesta(humana, verificador_oidc), firmador)
    from .portabilidad import manejadores as manejadores_portabilidad

    extra = manejadores_portabilidad(motor)
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
    app.include_router(router_renovacion(RenovadorGithub(config.consola.github_app, cliente_github)))
    catalogo = getattr(proveedores, "catalogo", None)
    if catalogo is not None and catalogo.registrar is None:
        catalogo.registrar = datos_consola.guardar_estado_catalogo
    consola = ContextoConsola(
        config=config.consola,
        firmador=firmador,
        datos=datos_consola,
        almacen=almacen,
        registro=registro,
        identidad=identidad,
        github=ClienteGithub(config.consola.github_app, cliente_github),
        logins_desarrollo={login: gid for login, gid in tokens_desarrollo.values()},
        tokens_desarrollo=tokens_desarrollo,
        grafo=grafo,
        acceso_grafo=acceso,
        fuente_codigo=fuente_codigo,
        catalogo=catalogo,
        suscripciones=suscripciones,
        abierto=config.modo_memoria,
    )
    montar_consola(app, consola)
    if config.consola.carpeta_spa is not None:
        _redirigir_raiz(app)
    if config.metricas_token:
        from .metricas import montar_metricas

        montar_metricas(
            app, token=config.metricas_token, version_app=app.version, esquema=esquema, sondas=sondas
        )
    return motor, app


def _redirigir_raiz(app: Any) -> None:
    """``/`` no es ninguna superficie: sin esto responde Not Found a quien abre el dominio."""

    from fastapi.responses import RedirectResponse

    from .consola.api import RUTA_SPA

    @app.get("/", include_in_schema=False)
    async def _raiz() -> RedirectResponse:
        # Temporal (no 308): el destino de la raíz puede cambiar.
        return RedirectResponse(RUTA_SPA + "/", status_code=307)


def _sondas(config: Configuracion, almacen: Any, motor_grafo: Any | None) -> dict[str, Any]:
    """Comprobaciones ligeras de ``/healthz``: una réplica sin sus bases no debe recibir tráfico."""

    sondas: dict[str, Any] = {}
    if config.postgres_url:
        sondas["postgres"] = lambda: almacen.db.command("ping")
    elif not config.modo_memoria:
        sondas["mongo"] = lambda: almacen.db.command("ping")
    if motor_grafo is not None and hasattr(motor_grafo, "ping"):
        sondas["falkordb"] = motor_grafo.ping
    return sondas


def _proveedores(config: Configuracion, almacen: Any, suscripciones: Any | None = None) -> Proveedores:
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
        log.warning(
            "sin proveedores de modelo por variables de entorno: los perfiles necesitan una suscripción "
            "registrada en la consola (unit.start rechaza el resto: perfil-insatisfacible)"
        )
    catalogo = Catalogo(fuentes, almacen, ttl_s=config.catalogo_ttl_s) if fuentes else None
    from .proveedores.cache import CacheNodos

    cache = CacheNodos(almacen, config.cache_nodos_s) if config.cache_nodos_s > 0 else None
    return Proveedores(
        disponibles,
        catalogo,
        zona_recurso=f.zona_datos if f else None,
        cache=cache,
        suscripciones=suscripciones,
    )


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
