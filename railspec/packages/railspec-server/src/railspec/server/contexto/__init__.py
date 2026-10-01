"""Herramientas de contexto conectables por rol (gobernanza, documentación, memoria).

El servidor es cliente MCP de cada proveedor de contexto. PCE es la primera
implementación: cualquier servidor que hable sus verbos (``search_catalog``)
se conecta igual. Ver ``railspec/docs/proveedores.md``.
"""

from .conectable import ContextoConectable, FuenteContexto, fuente_de_config
from .destinos import DestinoNoPermitido, PoliticaDestinos
from .pce import ClienteContexto, ClientePce
from .secretos import ReferenciaInvalida, ResolutorSecretos

__all__ = [
    "ClienteContexto",
    "ClientePce",
    "ContextoConectable",
    "DestinoNoPermitido",
    "FuenteContexto",
    "PoliticaDestinos",
    "ReferenciaInvalida",
    "ResolutorSecretos",
    "fuente_de_config",
]
