"""Configuración del servidor desde variables de entorno.

Convención fijada en ``railspec/docs/contratos.md`` § Variables de entorno: los
valores llegan de Kubernetes Secrets. Nada aquí lee archivos ni tiene valores
por defecto secretos. El estado vive en Mongo (``RAILSPEC_MONGO_URI``) o en Postgres
(``RAILSPEC_POSTGRES_URL``, ver ``estado/postgres.py``). Sin ninguno de los dos el servidor sería
un entorno de desarrollo con almacenes en memoria (``modo_memoria``): solo arranca con
``RAILSPEC_PERMITIR_DESARROLLO=1``, igual que los tokens de desarrollo junto a una base
o a la GitHub App (``validar_arranque``).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass, field

from .consola.config import ConfigConsola
from .proveedores.cifrado import Cifrador, ErrorCifrado

log = logging.getLogger("railspec.server")

_VERDADEROS = {"1", "true", "si", "sí", "yes", "on"}


class ErrorConfiguracion(ValueError):
    """La configuración es insegura o incoherente: el servidor no arranca."""


def _bandera(valor: str | None) -> bool:
    return (valor or "").strip().lower() in _VERDADEROS


@dataclass(frozen=True)
class ConfigFoundry:
    endpoint: str
    #: Sin clave, el adaptador usa Entra ID (DefaultAzureCredential).
    api_key: str | None = None
    #: Región del recurso (``eastus2``): la de la auditoría y la de los SKU Standard.
    region: str | None = None
    #: Zona de datos del recurso (``us``, ``eu``): la de los SKU DataZone.
    zona_datos: str | None = None
    #: Endpoint del proyecto de Foundry para leer el catálogo de despliegues por API.
    proyecto: str | None = None
    proyecto_api_version: str = "v1"
    #: Despliegues declarados a mano (``despliegue=modelo[:SKU]`` o JSON); se suman a los del proyecto.
    despliegues: str | None = None


@dataclass(frozen=True)
class ConfigAnthropic:
    api_key: str


@dataclass(frozen=True)
class Configuracion:
    mongo_uri: str | None = None
    mongo_db: str = "railspec"
    #: Estado en Postgres en vez de Mongo (Supabase, Neon, Azure Database for PostgreSQL). Excluyente.
    postgres_url: str | None = None
    #: Esquema de la tabla de documentos. No ``public``: Supabase lo expone por su API REST.
    postgres_esquema: str = "railspec"
    foundry: ConfigFoundry | None = None
    anthropic: ConfigAnthropic | None = None
    #: Clave maestra que cifra las claves de las suscripciones de modelos (``RAILSPEC_CLAVE_MAESTRA``).
    #: Sin ella el servidor arranca (``RAILSPEC_FOUNDRY_*`` sigue valiendo) pero no puede guardar ni
    #: usar suscripciones. Mal formada, el servidor no arranca.
    cifrador: Cifrador | None = field(default=None, repr=False)
    pce_url: str | None = None
    #: Grafo central (railspec-graph). Sin él, el gate de código no ve impacto de grafo.
    falkordb_url: str | None = None
    pce_api_key: str | None = None
    #: Caché de consultas a proveedores de contexto (PCE), en segundos; 0 la desactiva.
    contexto_cache_s: float = 900.0
    #: Vigencia del catálogo de modelos leído por API, en segundos.
    catalogo_ttl_s: float = 3600.0
    #: Vigencia de la caché de nodos de modelo por hash de entradas, en segundos; 0 la desactiva.
    cache_nodos_s: float = 86400.0
    #: Montaje de Kubernetes Secrets para ``credencial_ref`` (``secret://<secreto>/<clave>``).
    secretos_dir: str = "/var/run/secrets/railspec"
    #: Tokens de desarrollo ``token=login:github_id`` separados por coma. Solo
    #: para entornos sin GitHub App; en producción la identidad es GitHub.
    tokens_desarrollo: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: ``RAILSPEC_PERMITIR_DESARROLLO=1``: el servidor puede arrancar sin Mongo (todo en memoria y
    #: cualquier persona autenticada es ``desarrollador`` en cualquier org) y con tokens de
    #: desarrollo junto a Mongo o a la GitHub App. Nunca en producción.
    permitir_desarrollo: bool = False
    #: OIDC de GitHub Actions (actor de servicio de ``graph.index``). Sin audiencia = desactivado.
    #: Debe ser un valor largo y no adivinable (cualquier repositorio puede pedir un token con la
    #: audiencia que quiera); no hay valor por defecto a propósito.
    oidc_audiencia: str | None = None
    oidc_emisor: str = "https://token.actions.githubusercontent.com"
    #: ``owner/repo`` que pueden presentar un token OIDC. Obligatoria con audiencia: vacía, el
    #: servidor no arranca (ver ``VerificadorOidcActions``).
    oidc_repositorios: frozenset[str] = frozenset()
    #: Chat de contexto: carpeta con un clon de solo lectura por repositorio (``<owner>/<repo>``)
    #: para ``code.read``; sin ella el chat responde sin leer código.
    chat_clones: str | None = None
    #: Regiones de Azure de la zona de datos donde el chat puede enviar código (restringido/interno).
    chat_zona_datos: frozenset[str] = frozenset()
    chat_modelo: str = "claude-sonnet-5-5"
    host: str = "0.0.0.0"
    puerto: int = 8080
    #: Consola web (``RAILSPEC_CONSOLA_*`` y GitHub App); ver ``railspec/docs/consola.md``.
    consola: ConfigConsola = field(default_factory=ConfigConsola)

    @property
    def modo_memoria(self) -> bool:
        return self.mongo_uri is None and self.postgres_url is None

    @property
    def base_persistente(self) -> bool:
        return not self.modo_memoria

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> Configuracion:
        env = os.environ if entorno is None else entorno
        foundry = None
        if env.get("RAILSPEC_FOUNDRY_ENDPOINT"):
            foundry = ConfigFoundry(
                endpoint=env["RAILSPEC_FOUNDRY_ENDPOINT"].rstrip("/"),
                api_key=env.get("RAILSPEC_FOUNDRY_API_KEY") or None,
                region=(env.get("RAILSPEC_FOUNDRY_REGION") or "").strip().lower() or None,
                zona_datos=(env.get("RAILSPEC_FOUNDRY_ZONA_DATOS") or "").strip().lower() or None,
                proyecto=(env.get("RAILSPEC_FOUNDRY_PROYECTO") or "").strip().rstrip("/") or None,
                proyecto_api_version=env.get("RAILSPEC_FOUNDRY_PROYECTO_API_VERSION") or "v1",
                despliegues=(env.get("RAILSPEC_FOUNDRY_DESPLIEGUES") or "").strip() or None,
            )
        anthropic = None
        if _bandera(env.get("RAILSPEC_ANTHROPIC_HABILITADO")):
            clave = env.get("RAILSPEC_ANTHROPIC_API_KEY")
            if not clave:
                raise ValueError("RAILSPEC_ANTHROPIC_HABILITADO exige RAILSPEC_ANTHROPIC_API_KEY")
            anthropic = ConfigAnthropic(api_key=clave)
        return cls(
            mongo_uri=env.get("RAILSPEC_MONGO_URI") or None,
            mongo_db=env.get("RAILSPEC_MONGO_DB") or "railspec",
            postgres_url=env.get("RAILSPEC_POSTGRES_URL") or None,
            postgres_esquema=env.get("RAILSPEC_POSTGRES_ESQUEMA") or "railspec",
            foundry=foundry,
            anthropic=anthropic,
            cifrador=_cifrador(env),
            pce_url=env.get("RAILSPEC_PCE_URL") or None,
            falkordb_url=env.get("RAILSPEC_FALKORDB_URL") or None,
            pce_api_key=env.get("RAILSPEC_PCE_API_KEY") or None,
            contexto_cache_s=float(env.get("RAILSPEC_CONTEXTO_CACHE_S") or 900),
            catalogo_ttl_s=float(env.get("RAILSPEC_CATALOGO_TTL_S") or 3600),
            cache_nodos_s=float(env.get("RAILSPEC_CACHE_NODOS_S") or 86400),
            secretos_dir=env.get("RAILSPEC_SECRETOS_DIR") or "/var/run/secrets/railspec",
            tokens_desarrollo=_tokens(env.get("RAILSPEC_TOKENS_DESARROLLO", "")),
            permitir_desarrollo=_bandera(env.get("RAILSPEC_PERMITIR_DESARROLLO")),
            oidc_audiencia=env.get("RAILSPEC_OIDC_AUDIENCIA", "").strip() or None,
            oidc_emisor=env.get("RAILSPEC_OIDC_EMISOR") or "https://token.actions.githubusercontent.com",
            oidc_repositorios=frozenset(
                r.strip() for r in env.get("RAILSPEC_OIDC_REPOSITORIOS", "").split(",") if r.strip()
            ),
            chat_clones=env.get("RAILSPEC_CHAT_CLONES") or None,
            chat_zona_datos=frozenset(
                r.strip() for r in env.get("RAILSPEC_CHAT_ZONA_DATOS", "").split(",") if r.strip()
            ),
            chat_modelo=env.get("RAILSPEC_CHAT_MODELO") or "claude-sonnet-5-5",
            host=env.get("RAILSPEC_HOST", "0.0.0.0"),
            puerto=int(env.get("RAILSPEC_PUERTO", "8080")),
            consola=ConfigConsola.desde_entorno(env),
        )


def validar_arranque(config: Configuracion) -> None:
    """Rechaza (``ErrorConfiguracion``) las combinaciones de desarrollo con datos o identidad reales.

    - Sin ``RAILSPEC_MONGO_URI`` el estado vive en memoria y ``AutorizadorRoles(abierto=True)`` da
      ``desarrollador`` en toda organización a cualquier persona autenticada.
    - ``RAILSPEC_TOKENS_DESARROLLO`` sustituye por completo la identidad de GitHub y habilita
      ``POST /consola/api/auth/desarrollo``; junto a Mongo o a la GitHub App suele ser una clave
      sobrante del Secret (entra por ``envFrom``), no una decisión.

    Ambas se levantan solo con ``RAILSPEC_PERMITIR_DESARROLLO=1``, que además deja un WARNING.
    """

    if config.mongo_uri and config.postgres_url:
        raise ErrorConfiguracion(
            "RAILSPEC_MONGO_URI y RAILSPEC_POSTGRES_URL a la vez: el estado vive en una sola base, quita una"
        )
    if config.permitir_desarrollo:
        motivos = []
        if config.modo_memoria:
            motivos.append("estado en memoria (sin RAILSPEC_MONGO_URI ni RAILSPEC_POSTGRES_URL)")
        if config.tokens_desarrollo:
            motivos.append("tokens de desarrollo activos: no hay inicio de sesión con GitHub")
        log.warning(
            "RAILSPEC_PERMITIR_DESARROLLO=1: modo desarrollo permitido (%s). Solo para máquinas "
            "de desarrollo y pruebas, nunca en producción.",
            "; ".join(motivos) or "sin efectos hoy",
        )
        return
    if config.modo_memoria:
        raise ErrorConfiguracion(
            "sin RAILSPEC_MONGO_URI ni RAILSPEC_POSTGRES_URL el servidor arranca en memoria y trata a toda "
            "persona autenticada como desarrollador de cualquier organización: define RAILSPEC_MONGO_URI "
            "(o RAILSPEC_POSTGRES_URL) o, solo en desarrollo, RAILSPEC_PERMITIR_DESARROLLO=1"
        )
    if config.tokens_desarrollo and (config.base_persistente or config.consola.github_app is not None):
        raise ErrorConfiguracion(
            "RAILSPEC_TOKENS_DESARROLLO sustituye la identidad de GitHub y habilita el acceso por token "
            "de desarrollo, pero hay una base de datos o GitHub App configuradas: quita la variable (¿clave "
            "sobrante en el Secret?) o, solo en desarrollo, define RAILSPEC_PERMITIR_DESARROLLO=1"
        )


def _cifrador(env: Mapping[str, str]) -> Cifrador | None:
    try:
        return Cifrador.desde_entorno(env)
    except ErrorCifrado as exc:
        raise ValueError(exc.detalle) from exc


def _tokens(crudo: str) -> dict[str, tuple[str, int]]:
    tokens: dict[str, tuple[str, int]] = {}
    for par in filter(None, (p.strip() for p in crudo.split(","))):
        token, _, identidad = par.partition("=")
        login, _, github_id = identidad.partition(":")
        if not token or not login or not github_id.isdigit():
            raise ValueError("RAILSPEC_TOKENS_DESARROLLO espera token=login:github_id")
        tokens[token] = (login, int(github_id))
    return tokens
