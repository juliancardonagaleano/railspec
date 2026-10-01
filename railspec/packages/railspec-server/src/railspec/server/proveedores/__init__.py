"""Proveedores de modelo: Foundry (primario) y Anthropic tras bandera, detrás de una interfaz."""

from .base import ErrorProveedor, PeticionModelo, ProveedorModelo, RespuestaModelo, Uso
from .catalogo import Catalogo, EntradaCatalogo
from .seleccion import Eleccion, PerfilInsatisfacible, Proveedores

__all__ = [
    "Catalogo",
    "Eleccion",
    "EntradaCatalogo",
    "ErrorProveedor",
    "PeticionModelo",
    "PerfilInsatisfacible",
    "ProveedorModelo",
    "Proveedores",
    "RespuestaModelo",
    "Uso",
]
