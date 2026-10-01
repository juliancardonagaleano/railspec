"""Configuración del servidor desde variables de entorno.

Convención fijada en ``railspec/docs/contratos.md`` § Variables de entorno: los
valores llegan de Kubernetes Secrets. Nada aquí lee archivos ni tiene valores
por defecto secretos; sin Mongo configurado el servidor arranca con almacenes
en memoria (solo desarrollo y pruebas) y lo dice en ``modo_memoria``.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field

_VERDADEROS = {"1", "true", "si", "sí", "yes", "on"}


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
    foundry: ConfigFoundry | None = None
    anthropic: ConfigAnthropic | None = None
    pce_url: str | None = None
    #: Grafo central (railspec-graph). Sin él, el gate de código no ve impacto de grafo.
    falkordb_url: str | None = None
    pce_api_key: str | None = None
    #: Caché de consultas a proveedores de contexto (PCE), en segundos; 0 la desactiva.
    contexto_cache_s: float = 900.0
    #: Vigencia del catálogo de modelos leído por API, en segundos.
    catalogo_ttl_s: float = 3600.0
    #: Montaje de Kubernetes Secrets para ``credencial_ref`` (``secret://<secreto>/<clave>``).
    secretos_dir: str = "/var/run/secrets/railspec"
    #: Tokens de desarrollo ``token=login:github_id`` separados por coma. Solo
    #: para entornos sin GitHub App; en producción la identidad es GitHub.
    tokens_desarrollo: dict[str, tuple[str, int]] = field(default_factory=dict)
    #: OIDC de GitHub Actions (actor de servicio de ``graph.index``). Audiencia vacía = desactivado.
    oidc_audiencia: str | None = "railspec"
    oidc_emisor: str = "https://token.actions.githubusercontent.com"
    #: ``owner/repo`` que pueden presentar un token OIDC; vacío = cualquiera con vínculo.
    oidc_repositorios: frozenset[str] = frozenset()
    host: str = "0.0.0.0"
    puerto: int = 8080

    @property
    def modo_memoria(self) -> bool:
        return self.mongo_uri is None

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
            foundry=foundry,
            anthropic=anthropic,
            pce_url=env.get("RAILSPEC_PCE_URL") or None,
            falkordb_url=env.get("RAILSPEC_FALKORDB_URL") or None,
            pce_api_key=env.get("RAILSPEC_PCE_API_KEY") or None,
            contexto_cache_s=float(env.get("RAILSPEC_CONTEXTO_CACHE_S") or 900),
            catalogo_ttl_s=float(env.get("RAILSPEC_CATALOGO_TTL_S") or 3600),
            secretos_dir=env.get("RAILSPEC_SECRETOS_DIR") or "/var/run/secrets/railspec",
            tokens_desarrollo=_tokens(env.get("RAILSPEC_TOKENS_DESARROLLO", "")),
            oidc_audiencia=env.get("RAILSPEC_OIDC_AUDIENCIA", "railspec").strip() or None,
            oidc_emisor=env.get("RAILSPEC_OIDC_EMISOR") or "https://token.actions.githubusercontent.com",
            oidc_repositorios=frozenset(
                r.strip() for r in env.get("RAILSPEC_OIDC_REPOSITORIOS", "").split(",") if r.strip()
            ),
            host=env.get("RAILSPEC_HOST", "0.0.0.0"),
            puerto=int(env.get("RAILSPEC_PUERTO", "8080")),
        )


def _tokens(crudo: str) -> dict[str, tuple[str, int]]:
    tokens: dict[str, tuple[str, int]] = {}
    for par in filter(None, (p.strip() for p in crudo.split(","))):
        token, _, identidad = par.partition("=")
        login, _, github_id = identidad.partition(":")
        if not token or not login or not github_id.isdigit():
            raise ValueError("RAILSPEC_TOKENS_DESARROLLO espera token=login:github_id")
        tokens[token] = (login, int(github_id))
    return tokens
