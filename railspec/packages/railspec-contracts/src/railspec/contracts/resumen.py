"""Resumen del contenido de un índice (desde 1.10): la huella que CI y el servidor calculan igual.

CI lo calcula sobre el índice completo del commit y lo manda en el último lote de ``graph.index``;
el servidor calcula el suyo sobre lo que quedó en el canónico y compara ruta por ruta. Los dos
lados tienen que producir exactamente los mismos bytes, así que el algoritmo vive aquí, en un solo
lugar, y ambos lo importan.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

from .tools import ResumenArchivo, ResumenIndice


def huella_archivo(simbolos: Iterable[tuple[str, str]]) -> str:
    """Huella (16 hex) de los símbolos de un archivo, dados como pares ``(id, sha256)``."""

    lineas = sorted(f"{id_}\t{sha}" for id_, sha in simbolos)
    return hashlib.sha256("\n".join(lineas).encode("utf-8")).hexdigest()[:16]


def resumir(simbolos: Iterable[tuple[str, str, str]]) -> ResumenIndice:
    """Resumen por archivo de símbolos dados como ``(id, ruta, sha256)``, ordenado por ruta."""

    por_ruta: dict[str, list[tuple[str, str]]] = {}
    for id_, ruta, sha in simbolos:
        por_ruta.setdefault(ruta, []).append((id_, sha))
    return ResumenIndice(
        archivos=[
            ResumenArchivo(ruta=ruta, simbolos=len(pares), huella=huella_archivo(pares))
            for ruta, pares in sorted(por_ruta.items())
        ]
    )


def divergencias(esperado: ResumenIndice, encontrado: ResumenIndice) -> list[str]:
    """Rutas (ordenadas) cuyo contenido difiere entre el resumen de CI y el del canónico.

    Una ruta está en la lista si falta en un lado o si cambian su número de símbolos o su huella."""

    a = {r.ruta: (r.simbolos, r.huella) for r in esperado.archivos}
    b = {r.ruta: (r.simbolos, r.huella) for r in encontrado.archivos}
    return sorted(ruta for ruta in a.keys() | b.keys() if a.get(ruta) != b.get(ruta))
