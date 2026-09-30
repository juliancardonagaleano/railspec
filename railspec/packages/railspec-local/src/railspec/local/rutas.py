"""Coincidencia de rutas con patrones de sintaxis gitignore (subconjunto).

Se usa para ``alcance.permitidos``/``prohibidos`` de la orden y para las
exclusiones de ``.railspecignore``. Soporta ``*``, ``?``, ``**`` y la barra
final de directorio; un patrón sin barra coincide en cualquier nivel.
"""

from __future__ import annotations

import re
from functools import lru_cache


@lru_cache(maxsize=512)
def _regex(patron: str) -> re.Pattern[str]:
    p = patron.strip()
    directorio = p.endswith("/")
    p = p.rstrip("/")
    anclado = "/" in p
    p = p.lstrip("/")
    partes: list[str] = []
    i = 0
    while i < len(p):
        if p.startswith("**/", i):
            partes.append("(?:.*/)?")
            i += 3
        elif p.startswith("**", i):
            partes.append(".*")
            i += 2
        elif p[i] == "*":
            partes.append("[^/]*")
            i += 1
        elif p[i] == "?":
            partes.append("[^/]")
            i += 1
        else:
            partes.append(re.escape(p[i]))
            i += 1
    cuerpo = "".join(partes)
    prefijo = "^" if anclado else "^(?:.*/)?"
    # Un patrón que nombra un directorio cubre todo lo que cuelga de él.
    sufijo = "/.*$" if directorio else "(?:/.*)?$"
    return re.compile(prefijo + cuerpo + sufijo)


def coincide(ruta: str, patrones: list[str] | tuple[str, ...]) -> bool:
    return any(_regex(p).match(ruta) for p in patrones if p.strip() and not p.lstrip().startswith("#"))


def leer_patrones(texto: str) -> list[str]:
    return [linea.strip() for linea in texto.splitlines() if linea.strip() and not linea.startswith("#")]
