"""Herramientas de contexto conectables por rol (gobernanza, documentación, memoria).

El servidor es cliente MCP de cada proveedor de contexto. PCE es la primera
implementación: cualquier servidor que hable sus verbos (``search_catalog``)
se conecta igual. Ver ``railspec/docs/proveedores.md``.
"""

from .conectable import ContextoConectable, FuenteContexto, fuente_de_config
from .pce import ClienteContexto, ClientePce
from .secretos import ResolutorSecretos

__all__ = [
    "ClienteContexto",
    "ClientePce",
    "ContextoConectable",
    "FuenteContexto",
    "ResolutorSecretos",
    "fuente_de_config",
]
