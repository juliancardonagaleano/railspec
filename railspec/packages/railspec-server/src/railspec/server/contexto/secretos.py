"""Resolución de ``credencial_ref`` (``secret://<secreto>/<clave>``) sin guardar el valor.

Mongo solo guarda la referencia. El valor se lee del Kubernetes Secret
montado como volumen en ``<secretos_dir>/<secreto>/<clave>`` o, si no está
montado, de la variable ``RAILSPEC_SECRETO_<SECRETO>_<CLAVE>`` (mayúsculas,
``-`` y ``.`` como ``_``). Nunca se registra el valor.

**Namespace por organización.** El pool de secretos del servidor es uno solo:
sin más, la organización B podría referenciar el secreto de A. Por eso el
nombre del secreto lleva la organización como prefijo, ``<org>--<nombre>``
(``secret://acme--pce/api-key`` es de la organización ``acme``), y el
resolutor recibe la organización de la consulta y rechaza toda referencia que
no sea de su namespace; la consola aplica la misma regla al guardar. El
``<nombre>`` no puede contener ``--`` y la clave empieza por alfanumérico: así
ninguna referencia pertenece a dos organizaciones (la de ``acme--b`` no es
nunca de ``acme``) y las variables de entorno de organizaciones distintas no
chocan.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from pathlib import Path

_REF = re.compile(r"secret://([a-z0-9-]+)/([A-Za-z0-9][A-Za-z0-9_.-]*)")
_NOMBRE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*")


class SecretoNoDisponible(Exception):
    pass


class ReferenciaInvalida(SecretoNoDisponible):
    """La referencia está mal formada o no es del namespace de la organización."""


def validar_referencia(ref: str, org: str) -> tuple[str, str]:
    """Devuelve ``(secreto, clave)`` si ``ref`` es una referencia bien formada del namespace de ``org``."""

    m = _REF.fullmatch(ref)
    if m is None:
        raise ReferenciaInvalida(f"referencia inválida: {ref!r} (se espera secret://{org}--<nombre>/<clave>)")
    secreto, clave = m.groups()
    prefijo = f"{org}--"
    if not secreto.startswith(prefijo) or _NOMBRE.fullmatch(secreto[len(prefijo) :]) is None:
        raise ReferenciaInvalida(
            f"referencia fuera del namespace de {org}: {ref} (el secreto se llama {org}--<nombre>)"
        )
    return secreto, clave


class ResolutorSecretos:
    def __init__(self, directorio: str | Path, entorno: Mapping[str, str] | None = None) -> None:
        self._dir = Path(directorio)
        self._env = os.environ if entorno is None else entorno

    @staticmethod
    def variable(secreto: str, clave: str) -> str:
        return "RAILSPEC_SECRETO_" + re.sub(r"[^A-Z0-9]", "_", f"{secreto}_{clave}".upper())

    def resolver(self, ref: str, org: str) -> str:
        """Valor de ``ref`` para la organización ``org``; rechaza las referencias de otro namespace."""

        secreto, clave = validar_referencia(ref, org)
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
