"""Resolución de ``credencial_ref`` (``secret://<secreto>/<clave>``) sin guardar el valor.

Mongo solo guarda la referencia. El valor se lee del Kubernetes Secret
montado como volumen en ``<secretos_dir>/<secreto>/<clave>`` o, si no está
montado, de la variable ``RAILSPEC_SECRETO_<SECRETO>_<CLAVE>`` (mayúsculas,
``-`` y ``.`` como ``_``). Nunca se registra el valor.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_REF = re.compile(r"^secret://([a-z0-9-]+)/([A-Za-z0-9_.-]+)$")


class SecretoNoDisponible(Exception):
    pass


class ResolutorSecretos:
    def __init__(self, directorio: str | Path, entorno: Mapping[str, str] | None = None) -> None:
        self._dir = Path(directorio)
        self._env = os.environ if entorno is None else entorno

    @staticmethod
    def variable(secreto: str, clave: str) -> str:
        return "RAILSPEC_SECRETO_" + re.sub(r"[^A-Z0-9]", "_", f"{secreto}_{clave}".upper())

    def resolver(self, ref: str) -> str:
        m = _REF.match(ref)
        if m is None:
            raise SecretoNoDisponible(f"referencia inválida: {ref}")
        secreto, clave = m.groups()
        archivo = self._dir / secreto / clave
        try:
            if archivo.is_file():
                return archivo.read_text(encoding="utf-8").strip()
        except OSError:
            pass
        valor = self._env.get(self.variable(secreto, clave))
        if valor:
            return valor
        raise SecretoNoDisponible(f"{ref} no está montado ni en {self.variable(secreto, clave)}")
