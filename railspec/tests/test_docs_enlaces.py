"""Los enlaces entre docs de Railspec no se rompen.

Revisa, en ``railspec/README.md`` y ``railspec/docs/*.md``, que cada enlace
relativo apunte a un archivo que existe y, si lleva ancla, a un título real de
ese archivo (con la misma regla de ancla que GitHub). No toca la red.
"""

from __future__ import annotations

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import pytest

RAIZ = Path(__file__).resolve().parents[1]
DOCS = [RAIZ / "README.md", *sorted((RAIZ / "docs").glob("*.md"))]

_ENLACE = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_BLOQUE = re.compile(r"^(```|~~~)")
_TITULO = re.compile(r"^#{1,6}\s+(.*?)\s*#*\s*$")
_EXTERNO = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)


def _lineas_fuera_de_bloques(texto: str):
    """Pares (número de línea, línea) que no están dentro de un bloque de código."""
    en_bloque = False
    for numero, linea in enumerate(texto.splitlines(), start=1):
        if _BLOQUE.match(linea.lstrip()):
            en_bloque = not en_bloque
            continue
        if not en_bloque:
            yield numero, linea


def _ancla(titulo: str) -> str:
    """Ancla de GitHub: minúsculas, sin puntuación, espacios por guiones."""
    titulo = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", titulo)  # enlaces: solo el texto
    titulo = titulo.replace("`", "")
    titulo = unicodedata.normalize("NFC", titulo).lower()
    titulo = re.sub(r"[^\w\- ]", "", titulo, flags=re.UNICODE)
    return titulo.replace(" ", "-")


def _anclas(ruta: Path) -> set[str]:
    vistas: dict[str, int] = {}
    anclas: set[str] = set()
    for _, linea in _lineas_fuera_de_bloques(ruta.read_text(encoding="utf-8")):
        titulo = _TITULO.match(linea)
        if not titulo:
            continue
        base = _ancla(titulo.group(1))
        repeticiones = vistas.get(base, 0)
        vistas[base] = repeticiones + 1
        anclas.add(base if repeticiones == 0 else f"{base}-{repeticiones}")
    return anclas


def _enlaces_relativos(ruta: Path):
    texto = ruta.read_text(encoding="utf-8")
    for numero, linea in _lineas_fuera_de_bloques(texto):
        # los códigos en línea pueden mostrar sintaxis de enlace sin serlo
        linea = re.sub(r"`[^`]*`", "", linea)
        for destino in _ENLACE.findall(linea):
            if _EXTERNO.match(destino):
                continue
            yield numero, destino


def _casos():
    for ruta in DOCS:
        for numero, destino in _enlaces_relativos(ruta):
            yield pytest.param(ruta, numero, destino, id=f"{ruta.relative_to(RAIZ)}:{numero}->{destino}")


def test_hay_docs_y_enlaces_que_revisar():
    assert len(DOCS) > 5
    assert sum(1 for _ in _enlaces_relativos(RAIZ / "README.md")) > 5


@pytest.mark.parametrize(("ruta", "numero", "destino"), list(_casos()))
def test_enlace_relativo_resuelve(ruta: Path, numero: int, destino: str):
    sin_ancla, _, ancla = unquote(destino).partition("#")
    objetivo = ruta if not sin_ancla else (ruta.parent / sin_ancla).resolve()
    assert objetivo.exists(), f"{ruta.relative_to(RAIZ)}:{numero}: no existe {sin_ancla}"
    if ancla and objetivo.suffix == ".md":
        assert ancla in _anclas(objetivo), (
            f"{ruta.relative_to(RAIZ)}:{numero}: {objetivo.name} no tiene el título #{ancla}"
        )


def test_el_calculo_de_anclas_sigue_a_github():
    assert _ancla("Iniciar sesión con GitHub") == "iniciar-sesión-con-github"
    assert _ancla("Zona de datos del chat") == "zona-de-datos-del-chat"
    assert _ancla("`railspec login` y (más) cosas: 1.5") == "railspec-login-y-más-cosas-15"
