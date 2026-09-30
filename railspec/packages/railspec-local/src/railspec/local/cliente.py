"""Cliente del servidor Railspec: habla solo el contrato.

``Transporte`` es la frontera mínima (llamar una tool por nombre con un
diccionario JSON). ``ClienteServidor`` valida la entrada y la salida contra
el registro de tools del contrato, así que nada que no encaje sale ni entra.
En producción el transporte es MCP Streamable HTTP (``transporte_mcp``); en
pruebas, un servidor doble en memoria.
"""

from __future__ import annotations

from typing import Any, Protocol, TypeVar

from pydantic import BaseModel, ValidationError
from railspec.contracts.tools import TOOLS, ErrorTool, Superficie

from .errores import ErrorServidor, RespuestaInvalida

S = TypeVar("S", bound=BaseModel)


class Transporte(Protocol):
    async def llamar(self, tool: str, argumentos: dict[str, Any]) -> dict[str, Any]:
        """Devuelve la salida estructurada de la tool.

        Lanza ``ErrorServidor`` si el servidor respondió con un ``ErrorTool`` y
        ``SinConexion`` si no hubo respuesta."""
        ...


class ClienteServidor:
    def __init__(self, transporte: Transporte) -> None:
        self.transporte = transporte

    async def llamar(self, tool: str, entrada: BaseModel, salida: type[S]) -> S:
        definicion = TOOLS[tool]
        if Superficie.mcp not in definicion.superficies:
            raise ValueError(f"{tool} no se expone por MCP")
        if not isinstance(entrada, definicion.entrada):
            raise TypeError(f"{tool} espera {definicion.entrada.__name__}")
        if salida is not definicion.salida:
            raise TypeError(f"{tool} devuelve {definicion.salida.__name__}")
        datos = await self.transporte.llamar(tool, entrada.model_dump(mode="json", exclude_none=True))
        try:
            return salida.model_validate(datos)
        except ValidationError as exc:
            raise RespuestaInvalida(
                f"{tool}: la respuesta del servidor no cumple el contrato: {exc}"
            ) from exc


def error_desde(datos: dict[str, Any]) -> ErrorServidor:
    """Convierte un cuerpo ``ErrorTool`` en excepción; si no encaja, respuesta inválida."""

    try:
        return ErrorServidor(ErrorTool.model_validate(datos))
    except ValidationError as exc:
        raise RespuestaInvalida(f"error del servidor fuera de contrato: {datos!r}") from exc
