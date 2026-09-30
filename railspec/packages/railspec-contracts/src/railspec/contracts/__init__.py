"""Contratos de Railspec (versión 1).

Fuente de verdad de los mensajes entre servidor, proxy local, arneses y
consola. Ver ``railspec/docs/contratos.md``.
"""

from ._base import VERSION_CONTRATO, VERSION_MAYOR
from .chat import Conversacion, MensajeChat, RespuestaChat, VeredictoGateSalida
from .comun import Actor, AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace, NivelCodigo
from .estado import Checkpoint, EstadoLocal, EstadoUnidad, ResultadoGate
from .eventos import EventoSync
from .hallazgos import Hallazgo
from .insumo import Insumo, InsumoResuelto
from .orden import OrdenDeTrabajo, OrdenImplementar, OrdenRedactar, OrdenRefinar, OrdenValidar
from .reporte import ReporteOrden
from .snapshot import Snapshot
from .tools import TOOLS, Superficie, ToolDef, nombre_mcp, resolver_tool, tools_para

__version__ = "0.1.0"

__all__ = [
    "TOOLS",
    "VERSION_CONTRATO",
    "VERSION_MAYOR",
    "Actor",
    "AlcanceRepositorio",
    "AlcanceUnidad",
    "AlcanceWorkspace",
    "Checkpoint",
    "Conversacion",
    "EstadoLocal",
    "EstadoUnidad",
    "EventoSync",
    "Hallazgo",
    "Insumo",
    "InsumoResuelto",
    "MensajeChat",
    "NivelCodigo",
    "OrdenDeTrabajo",
    "OrdenImplementar",
    "OrdenRedactar",
    "OrdenRefinar",
    "OrdenValidar",
    "ReporteOrden",
    "RespuestaChat",
    "ResultadoGate",
    "Snapshot",
    "Superficie",
    "ToolDef",
    "VeredictoGateSalida",
    "nombre_mcp",
    "resolver_tool",
    "tools_para",
]
