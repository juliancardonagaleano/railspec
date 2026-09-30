"""Adaptadores por arnés: registran el proxy y el stack mínimo de tres piezas.

Cada arnés recibe lo mismo, en su formato:

1. Comando de arranque ``/railspec`` (llama ``unit_start``).
2. Skill ``railspec-bucle`` (``unit_advance`` → ejecutar → ``unit_report``).
3. Reglas de conducta, en un bloque delimitado del archivo de instrucciones
   del arnés (``CLAUDE.md`` o ``AGENTS.md``) que se reemplaza sin tocar el resto.

Además, el proxy queda registrado como servidor MCP por stdio en la
configuración del arnés, fusionando solo la entrada ``railspec``.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path
from typing import Any

from railspec.contracts.comun import Arnes

from ..errores import ErrorRailspec

NOMBRE_SERVIDOR = "railspec"
INICIO_BLOQUE = "<!-- railspec:inicio (generado por `railspec instalar`; no editar dentro) -->"
FIN_BLOQUE = "<!-- railspec:fin -->"


def plantilla(nombre: str) -> str:
    return files(__package__).joinpath("plantillas", nombre).read_text(encoding="utf-8")


@dataclass(frozen=True)
class Adaptador:
    arnes: Arnes
    archivo_mcp: str
    ruta_comando: str
    ruta_skill: str
    archivo_reglas: str

    def entrada_mcp(self) -> dict[str, Any]:
        if self.arnes == Arnes.opencode:
            return {"type": "local", "command": ["railspec", "mcp"], "enabled": True}
        return {"type": "stdio", "command": "railspec", "args": ["mcp"]}

    def clave_mcp(self) -> str:
        return "mcp" if self.arnes == Arnes.opencode else "mcpServers"

    def archivos(self) -> dict[str, str]:
        return {self.ruta_comando: plantilla("comando.md"), self.ruta_skill: plantilla("skill-bucle.md")}


ADAPTADORES: dict[Arnes, Adaptador] = {
    Arnes.claude_code: Adaptador(
        arnes=Arnes.claude_code,
        archivo_mcp=".mcp.json",
        ruta_comando=".claude/commands/railspec.md",
        ruta_skill=".claude/skills/railspec-bucle/SKILL.md",
        archivo_reglas="CLAUDE.md",
    ),
    Arnes.opencode: Adaptador(
        arnes=Arnes.opencode,
        archivo_mcp="opencode.jsonc",
        ruta_comando=".opencode/command/railspec.md",
        ruta_skill=".opencode/skill/railspec-bucle/SKILL.md",
        archivo_reglas="AGENTS.md",
    ),
}


# --- JSON con comentarios -------------------------------------------------------------

_TOKEN_JSONC = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_COMA_FINAL = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)


def leer_jsonc(texto: str) -> Any:
    """JSON tolerante a comentarios y comas finales (``opencode.jsonc``)."""

    sin_comentarios = _TOKEN_JSONC.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", texto)
    limpio = _COMA_FINAL.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(0), sin_comentarios)
    return json.loads(limpio) if limpio.strip() else {}


def _fusionar_mcp(ruta: Path, clave: str, entrada: dict[str, Any]) -> bool:
    datos: dict[str, Any] = {}
    if ruta.is_file():
        try:
            datos = leer_jsonc(ruta.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ErrorRailspec(f"{ruta} no es JSON válido; no se toca: {exc}") from exc
        if not isinstance(datos, dict):
            raise ErrorRailspec(f"{ruta} no es un objeto JSON; no se toca.")
    if ruta.name == "opencode.jsonc":
        datos.setdefault("$schema", "https://opencode.ai/config.json")
    servidores = datos.setdefault(clave, {})
    if servidores.get(NOMBRE_SERVIDOR) == entrada:
        return False
    servidores[NOMBRE_SERVIDOR] = entrada
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return True


# --- bloque de reglas -------------------------------------------------------------------


def bloque_reglas() -> str:
    return f"{INICIO_BLOQUE}\n{plantilla('reglas.md').rstrip()}\n{FIN_BLOQUE}\n"


def _fusionar_bloque(ruta: Path, bloque: str) -> bool:
    actual = ruta.read_text(encoding="utf-8") if ruta.is_file() else ""
    patron = re.compile(re.escape(INICIO_BLOQUE) + r".*?" + re.escape(FIN_BLOQUE) + r"\n?", re.DOTALL)
    if patron.search(actual):
        nuevo = patron.sub(lambda _: bloque, actual, count=1)
    else:
        separador = "" if not actual else ("\n" if actual.endswith("\n") else "\n\n")
        nuevo = actual + separador + bloque
    if nuevo == actual:
        return False
    ruta.write_text(nuevo, encoding="utf-8")
    return True


# --- instalar y verificar -------------------------------------------------------------------


def instalar(raiz: Path, arnes: Arnes) -> list[str]:
    """Escribe o actualiza lo del adaptador; devuelve las rutas que cambiaron."""

    if arnes not in ADAPTADORES:
        raise ErrorRailspec(
            f"No hay adaptador para {arnes.value} todavía (primera ola: claude-code, opencode)."
        )
    adaptador = ADAPTADORES[arnes]
    cambiados: list[str] = []
    for relativa, contenido in adaptador.archivos().items():
        ruta = raiz / relativa
        if not ruta.is_file() or ruta.read_text(encoding="utf-8") != contenido:
            ruta.parent.mkdir(parents=True, exist_ok=True)
            ruta.write_text(contenido, encoding="utf-8")
            cambiados.append(relativa)
    if _fusionar_mcp(raiz / adaptador.archivo_mcp, adaptador.clave_mcp(), adaptador.entrada_mcp()):
        cambiados.append(adaptador.archivo_mcp)
    if _fusionar_bloque(raiz / adaptador.archivo_reglas, bloque_reglas()):
        cambiados.append(adaptador.archivo_reglas)
    return cambiados


def verificar(raiz: Path, arnes: Arnes) -> list[str]:
    """Deriva: rutas del adaptador que faltan o no coinciden con esta versión del proxy."""

    adaptador = ADAPTADORES[arnes]
    deriva = [
        r
        for r, contenido in adaptador.archivos().items()
        if not (raiz / r).is_file() or (raiz / r).read_text(encoding="utf-8") != contenido
    ]
    ruta_mcp = raiz / adaptador.archivo_mcp
    try:
        datos = leer_jsonc(ruta_mcp.read_text(encoding="utf-8")) if ruta_mcp.is_file() else {}
    except json.JSONDecodeError:
        datos = {}
    if datos.get(adaptador.clave_mcp(), {}).get(NOMBRE_SERVIDOR) != adaptador.entrada_mcp():
        deriva.append(adaptador.archivo_mcp)
    ruta_reglas = raiz / adaptador.archivo_reglas
    if not ruta_reglas.is_file() or bloque_reglas() not in ruta_reglas.read_text(encoding="utf-8"):
        deriva.append(adaptador.archivo_reglas)
    return deriva
