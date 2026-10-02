"""Lectura de los workflows de ``.github/workflows`` para las pruebas que vigilan cómo instalan una
herramienta (``test_workflows_indexador.py`` y ``test_workflows_ruff.py``).

``requisitos`` devuelve cada palabra con la que un workflow pide instalar un paquete, tal como la verá
pip; decidir si está bien fijada es de cada prueba.
"""

from __future__ import annotations

import re
import shlex
from collections.abc import Iterator, Mapping
from pathlib import Path

import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
SEPARADORES = {"&&", "||", ";", "|"}
#: Claves que solo rotulan (nombre de un paso, descripción de una entrada): mencionan el paquete sin
#: instalarlo.
ROTULOS = {"name", "description"}


def textos(nodo, clave: str | None = None) -> Iterator[tuple[str | None, str]]:
    """Cada cadena del YAML con la clave que la contiene, salvo los rótulos."""
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            if k not in ROTULOS:
                yield from textos(v, k)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from textos(v, clave)
    elif isinstance(nodo, str):
        yield clave, nodo


def ordenes(texto: str, sustituciones: Mapping[str, str] | None = None) -> Iterator[list[str]]:
    """Las órdenes de un guion de shell, ya partidas en palabras: sin comentarios, con las
    continuaciones (``\\``) unidas y cada tramo de un ``&&``, ``||``, ``;`` o ``|`` aparte.
    ``sustituciones`` cambia antes de partir (``$(...)`` por una marca sin espacios, por ejemplo)."""
    for origen, marca in (sustituciones or {}).items():
        texto = texto.replace(origen, marca)
    for linea in texto.replace("\\\n", " ").splitlines():
        linea = re.sub(r"(^|\s)#.*$", "", linea).strip()
        if not linea:
            continue
        try:
            palabras = shlex.split(linea)
        except ValueError:  # comillas sin cerrar: mejor una partición burda que callar
            palabras = linea.split()
        orden: list[str] = []
        for palabra in [*palabras, ";"]:
            if palabra in SEPARADORES:
                if orden:
                    yield orden
                orden = []
            else:
                orden.append(palabra)


def requisitos(
    texto_yaml: str, paquete: re.Pattern[str], sustituciones: Mapping[str, str] | None = None
) -> list[str]:
    """Cada palabra con la que el workflow pide instalar ``paquete``, tal como la verá pip.

    En un ``run`` solo cuentan las líneas con ``install`` (``codebase-memory-mcp --version`` o
    ``ruff check`` lo ejecutan, no lo instalan). En cualquier otra clave (la lista ``instalar`` de la
    matriz, un ``with``) toda mención es un requisito: el workflow la pasará luego a pip."""

    encontrados = []
    for clave, texto in textos(yaml.safe_load(texto_yaml)):
        for palabras in ordenes(texto, sustituciones):
            if clave == "run" and "install" not in palabras:
                continue
            encontrados += [p for p in palabras if paquete.search(p)]
    return encontrados
