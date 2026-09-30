"""Piezas que un adaptador escribe en el repositorio, y cómo se instalan, verifican y quitan.

Cada pieza toca solo lo suyo: un archivo propio de Railspec, una clave o unos
elementos de lista dentro de un JSON del arnés, o un bloque delimitado de un
Markdown de instrucciones. Lo demás de cada archivo se conserva siempre.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ..errores import ErrorRailspec

INICIO_BLOQUE = "<!-- railspec:inicio (generado por `railspec instalar`; no editar dentro) -->"
FIN_BLOQUE = "<!-- railspec:fin -->"

# --- JSON con comentarios -------------------------------------------------------------

_TOKEN_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_COMA_FINAL = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)


def leer_jsonc(texto: str) -> Any:
    """JSON tolerante a comentarios y comas finales (``opencode.jsonc``)."""

    sin_comentarios = _TOKEN_JSONC.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", texto)
    limpio = _COMA_FINAL.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(0), sin_comentarios)
    return json.loads(limpio) if limpio.strip() else {}


def _leer_objeto(ruta: Path) -> dict[str, Any]:
    if not ruta.is_file():
        return {}
    try:
        datos = leer_jsonc(ruta.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ErrorRailspec(f"{ruta} no es JSON válido; no se toca: {exc}") from exc
    if not isinstance(datos, dict):
        raise ErrorRailspec(f"{ruta} no es un objeto JSON; no se toca.")
    return datos


def _leer_objeto_o_vacio(ruta: Path) -> dict[str, Any]:
    try:
        return _leer_objeto(ruta)
    except ErrorRailspec:
        return {}


def _escribir_objeto(raiz: Path, ruta: Path, datos: dict[str, Any], vacio: dict[str, Any]) -> None:
    """Escribe el JSON; si solo queda lo que el adaptador pondría en un archivo nuevo, lo borra."""

    if datos == vacio or not datos:
        ruta.unlink(missing_ok=True)
        _quitar_vacios(ruta, raiz)
        return
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


def _obtener(datos: dict[str, Any], claves: tuple[str, ...]) -> Any:
    actual: Any = datos
    for clave in claves:
        if not isinstance(actual, dict) or clave not in actual:
            return None
        actual = actual[clave]
    return actual


def _contenedor(datos: dict[str, Any], claves: tuple[str, ...], ruta: Path, escalar_a: str | None) -> dict:
    actual = datos
    for clave in claves:
        valor = actual.setdefault(clave, {})
        if isinstance(valor, str) and escalar_a is not None:
            # OpenCode admite `"permission": "ask"` como abreviatura de `{"*": "ask"}`.
            valor = actual[clave] = {escalar_a: valor}
        if not isinstance(valor, dict):
            raise ErrorRailspec(f"{ruta}: `{'.'.join(claves)}` no es un objeto; no se toca.")
        actual = valor
    return actual


def _podar(datos: dict[str, Any], claves: tuple[str, ...]) -> None:
    """Quita los contenedores que quedaron vacíos a lo largo de ``claves``."""

    for n in range(len(claves), 0, -1):
        padre = _obtener(datos, claves[: n - 1]) if n > 1 else datos
        if isinstance(padre, dict) and padre.get(claves[n - 1]) in ({}, []):
            del padre[claves[n - 1]]


def _quitar_vacios(ruta: Path, raiz: Path) -> None:
    """Borra las carpetas que quedaron vacías entre ``ruta`` y la raíz del repositorio."""

    carpeta = ruta.parent
    while carpeta != raiz and raiz in carpeta.parents:
        if not carpeta.is_dir() or any(carpeta.iterdir()):
            return
        carpeta.rmdir()
        carpeta = carpeta.parent


# --- piezas ---------------------------------------------------------------------------


class Pieza(Protocol):
    archivo: str

    def instalar(self, raiz: Path) -> bool: ...
    def instalada(self, raiz: Path) -> bool: ...
    def presente(self, raiz: Path) -> bool: ...
    def desinstalar(self, raiz: Path) -> bool: ...


@dataclass(frozen=True)
class ArchivoPropio:
    """Archivo que es entero de Railspec (comando, skill)."""

    archivo: str
    contenido: str

    def instalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        if ruta.is_file() and ruta.read_text(encoding="utf-8") == self.contenido:
            return False
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(self.contenido, encoding="utf-8")
        return True

    def instalada(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        return ruta.is_file() and ruta.read_text(encoding="utf-8") == self.contenido

    def presente(self, raiz: Path) -> bool:
        return (raiz / self.archivo).exists()

    def desinstalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        if not ruta.exists():
            return False
        ruta.unlink()
        _quitar_vacios(ruta, raiz)
        return True


@dataclass(frozen=True)
class ArchivoLegado(ArchivoPropio):
    """Ruta que escribió una versión anterior del adaptador: instalar la quita."""

    contenido: str = ""

    def instalar(self, raiz: Path) -> bool:
        return self.desinstalar(raiz)

    def instalada(self, raiz: Path) -> bool:
        return not self.presente(raiz)


@dataclass(frozen=True)
class EntradaJson:
    """Una clave de un objeto JSON del arnés (``mcpServers.railspec``).

    ``siempre_quitar``: la clave es de Railspec (el nombre ``railspec``) y la
    desinstalación la quita aunque alguien la haya editado; si no, solo se quita
    si conserva el valor que puso el adaptador (una regla de permisos ajena se respeta).
    """

    archivo: str
    claves: tuple[str, ...]
    valor: Any
    siempre_quitar: bool = True
    vacio: tuple[tuple[str, Any], ...] = ()
    escalar_a: str | None = None

    def instalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        datos = _leer_objeto(ruta)
        if _obtener(datos, self.claves) == self.valor:
            return False
        for clave, valor in self.vacio:
            datos.setdefault(clave, valor)
        _contenedor(datos, self.claves[:-1], ruta, self.escalar_a)[self.claves[-1]] = self.valor
        _escribir_objeto(raiz, ruta, datos, {})
        return True

    def instalada(self, raiz: Path) -> bool:
        return _obtener(_leer_objeto_o_vacio(raiz / self.archivo), self.claves) == self.valor

    def presente(self, raiz: Path) -> bool:
        return _obtener(_leer_objeto_o_vacio(raiz / self.archivo), self.claves) is not None

    def desinstalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        if not ruta.is_file():
            return False
        datos = _leer_objeto(ruta)
        padre = _obtener(datos, self.claves[:-1]) if len(self.claves) > 1 else datos
        if not isinstance(padre, dict) or self.claves[-1] not in padre:
            return False
        if not self.siempre_quitar and padre[self.claves[-1]] != self.valor:
            return False
        del padre[self.claves[-1]]
        _podar(datos, self.claves[:-1])
        _escribir_objeto(raiz, ruta, datos, dict(self.vacio))
        return True


@dataclass(frozen=True)
class ElementosLista:
    """Elementos de una lista JSON (``permissions.allow``) que Railspec añade sin duplicar."""

    archivo: str
    claves: tuple[str, ...]
    valores: tuple[str, ...]

    def _lista(self, datos: dict[str, Any], ruta: Path) -> list:
        contenedor = _contenedor(datos, self.claves[:-1], ruta, None)
        lista = contenedor.setdefault(self.claves[-1], [])
        if not isinstance(lista, list):
            raise ErrorRailspec(f"{ruta}: `{'.'.join(self.claves)}` no es una lista; no se toca.")
        return lista

    def instalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        datos = _leer_objeto(ruta)
        lista = self._lista(datos, ruta)
        faltan = [v for v in self.valores if v not in lista]
        if not faltan:
            return False
        lista.extend(faltan)
        _escribir_objeto(raiz, ruta, datos, {})
        return True

    def instalada(self, raiz: Path) -> bool:
        lista = _obtener(_leer_objeto_o_vacio(raiz / self.archivo), self.claves)
        return isinstance(lista, list) and all(v in lista for v in self.valores)

    def presente(self, raiz: Path) -> bool:
        lista = _obtener(_leer_objeto_o_vacio(raiz / self.archivo), self.claves)
        return isinstance(lista, list) and any(v in lista for v in self.valores)

    def desinstalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        if not ruta.is_file():
            return False
        datos = _leer_objeto(ruta)
        lista = _obtener(datos, self.claves)
        if not isinstance(lista, list) or not any(v in lista for v in self.valores):
            return False
        lista[:] = [v for v in lista if v not in self.valores]
        _podar(datos, self.claves)
        _escribir_objeto(raiz, ruta, datos, {})
        return True


_PATRON_BLOQUE = re.compile(
    r"\n?" + re.escape(INICIO_BLOQUE) + r".*?" + re.escape(FIN_BLOQUE) + r"\n?", re.DOTALL
)


@dataclass(frozen=True)
class BloqueReglas:
    """Bloque delimitado dentro del archivo de instrucciones del arnés (``CLAUDE.md``, ``AGENTS.md``)."""

    archivo: str
    reglas: str

    @property
    def bloque(self) -> str:
        return f"{INICIO_BLOQUE}\n{self.reglas.rstrip()}\n{FIN_BLOQUE}\n"

    def instalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        actual = ruta.read_text(encoding="utf-8") if ruta.is_file() else ""
        patron = re.compile(re.escape(INICIO_BLOQUE) + r".*?" + re.escape(FIN_BLOQUE) + r"\n?", re.DOTALL)
        if patron.search(actual):
            nuevo = patron.sub(lambda _: self.bloque, actual, count=1)
        else:
            separador = "" if not actual else ("\n" if actual.endswith("\n") else "\n\n")
            nuevo = actual + separador + self.bloque
        if nuevo == actual:
            return False
        ruta.write_text(nuevo, encoding="utf-8")
        return True

    def instalada(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        return ruta.is_file() and self.bloque in ruta.read_text(encoding="utf-8")

    def presente(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        return ruta.is_file() and INICIO_BLOQUE in ruta.read_text(encoding="utf-8")

    def desinstalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        if not self.presente(raiz):
            return False
        actual = ruta.read_text(encoding="utf-8")
        nuevo = _PATRON_BLOQUE.sub("\n", actual, count=1)
        nuevo = nuevo.rstrip("\n") + "\n" if nuevo.strip() else ""
        # El separador que puso `instalar` delante del bloque también sale.
        if actual.startswith(INICIO_BLOQUE):
            nuevo = nuevo.lstrip("\n")
        if not nuevo:
            ruta.unlink()
        else:
            ruta.write_text(nuevo, encoding="utf-8")
        return True
