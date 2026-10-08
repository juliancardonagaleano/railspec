"""Error de negocio del protocolo: se traduce a ``ErrorTool`` en MCP y HTTP."""

from __future__ import annotations

from railspec.contracts.tools import CodigoError


class ErrorNegocio(Exception):
    """Se traduce a ``ErrorTool`` en MCP y HTTP."""

    def __init__(self, codigo: CodigoError, detalle: str, version_estado: int | None = None) -> None:
        super().__init__(f"{codigo.value}: {detalle}")
        self.codigo = codigo
        self.detalle = detalle
        self.version_estado = version_estado
