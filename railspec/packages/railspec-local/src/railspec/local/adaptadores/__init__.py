"""Adaptadores por arnés: registran el proxy y el stack mínimo de tres piezas.

Cada arnés recibe lo mismo, en su formato:

1. Comando de arranque ``/railspec`` (llama ``unit_start``); en Codex y Copilot,
   que no tienen comandos del proyecto, es la skill ``railspec``.
2. Skill ``railspec-bucle`` (``unit_advance`` → ejecutar → ``unit_report``).
3. Reglas de conducta, en un bloque delimitado del archivo de instrucciones
   del arnés (``CLAUDE.md`` o ``AGENTS.md``) que se reemplaza sin tocar el resto.

Segunda ola: Codex (``adaptadores/codex``) y GitHub Copilot CLI
(``adaptadores/copilot``). Varios arneses comparten piezas idénticas (el bloque
de ``AGENTS.md``; la entrada de ``.mcp.json`` en Claude Code y Copilot):
desinstalar uno no quita lo que otro instalado sigue usando.

Además, el proxy queda registrado como servidor MCP por stdio en la
configuración del arnés, y los permisos del arnés se ajustan para que el bucle
corra sin preguntas pero todo lo que decide un humano (aprobar un checkpoint,
cambiar de modo, integrar) pase siempre por su confirmación.

Donde el arnés tiene hooks previos a cada tool, las reglas de conducta se
aplican además con ``railspec hook <arnés>`` (ver ``guardia.py``): un hook
``PreToolUse`` en Claude Code y un plugin en OpenCode.

``instalar``, ``verificar`` y ``desinstalar`` son idempotentes y solo tocan lo
de Railspec: la entrada ``railspec`` de cada JSON, los permisos que añadió y el
bloque entre marcadores.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from importlib.resources import files
from pathlib import Path

from railspec.contracts.comun import Arnes

from .. import guardia
from ..errores import ErrorRailspec
from .piezas import (
    FIN_BLOQUE,
    INICIO_BLOQUE,
    ArchivoLegado,
    ArchivoPropio,
    BloqueReglas,
    ElementosLista,
    EntradaHook,
    EntradaJson,
    Pieza,
    leer_jsonc,
)

__all__ = [
    "ADAPTADORES",
    "COMANDO_PROXY",
    "FIN_BLOQUE",
    "INICIO_BLOQUE",
    "NOMBRE_SERVIDOR",
    "TOOLS_AUTOMATICAS",
    "TOOLS_HUMANAS",
    "Adaptador",
    "desinstalar",
    "instalados",
    "instalar",
    "leer_jsonc",
    "plantilla",
    "verificar",
]

NOMBRE_SERVIDOR = "railspec"
COMANDO_PROXY = ["railspec", "mcp"]

#: Tools del proxy que el bucle llama sin preguntar al humano.
TOOLS_AUTOMATICAS = (
    "unit_start",
    "unit_advance",
    "unit_report",
    "unit_checkpoint",
    "unit_status",
    "unit_list",
    "graph_query",
    "insumo_pull",
    "railspec_sync",
)
#: Tools que registran una decisión humana: el arnés pide confirmación siempre.
TOOLS_HUMANAS = guardia.TOOLS_HUMANAS

#: Tools de Claude Code que la guardia revisa: las que escriben archivos y las humanas.
HOOK_CLAUDE_CODE = {
    "matcher": "|".join(
        ["Write", "Edit", "MultiEdit", "NotebookEdit"] + [f"mcp__railspec__{t}" for t in TOOLS_HUMANAS]
    ),
    "hooks": [{"type": "command", "command": "railspec hook claude-code", "timeout": 30}],
}


def plantilla(nombre: str) -> str:
    """Texto de una plantilla del paquete (``plantillas/<nombre>``)."""

    return files(__package__).joinpath("plantillas", nombre).read_text(encoding="utf-8")


def _comando(arnes: Arnes) -> str:
    texto = plantilla("comando.md")
    if arnes == Arnes.opencode:
        # OpenCode no conoce `argument-hint` en el frontmatter de un comando.
        texto = "".join(
            linea for linea in texto.splitlines(keepends=True) if not linea.startswith("argument-hint:")
        )
    return texto


@dataclass(frozen=True)
class Adaptador:
    arnes: Arnes
    ruta_comando: str
    ruta_skill: str
    archivo_reglas: str
    #: Arneses de la segunda ola: construyen sus piezas en su propio módulo.
    fabrica: Callable[[Path, Path | None], list[Pieza]] | None = None

    def archivo_mcp(self, raiz: Path) -> str:
        if self.arnes != Arnes.opencode:
            return ".mcp.json"
        # OpenCode lee `opencode.json` u `opencode.jsonc`: se fusiona en el que ya exista.
        for nombre in ("opencode.jsonc", "opencode.json"):
            if (raiz / nombre).is_file():
                return nombre
        return "opencode.jsonc"

    def piezas(self, raiz: Path, dir_worktrees: Path | None) -> list[Pieza]:
        if self.fabrica is not None:
            return self.fabrica(raiz, dir_worktrees)
        comunes: list[Pieza] = [
            ArchivoPropio(self.ruta_comando, _comando(self.arnes)),
            ArchivoPropio(self.ruta_skill, plantilla("skill-bucle.md")),
        ]
        reglas = BloqueReglas(self.archivo_reglas, plantilla("reglas.md"))
        if self.arnes == Arnes.opencode:
            return comunes + self._piezas_opencode(raiz) + [reglas]
        return comunes + self._piezas_claude_code(dir_worktrees) + [reglas]

    def _piezas_claude_code(self, dir_worktrees: Path | None) -> list[Pieza]:
        compartido = ".claude/settings.json"
        local = ".claude/settings.local.json"
        piezas: list[Pieza] = [
            _entrada_mcp_json(),
            ElementosLista(
                compartido, ("permissions", "allow"), tuple(f"mcp__railspec__{t}" for t in TOOLS_AUTOMATICAS)
            ),
            # `ask` gana a `allow`: aunque alguien permita `mcp__railspec` entero, estas preguntan.
            ElementosLista(
                compartido, ("permissions", "ask"), tuple(f"mcp__railspec__{t}" for t in TOOLS_HUMANAS)
            ),
            # Reglas de conducta aplicadas: escrituras fuera de la orden y decisiones humanas.
            EntradaHook(compartido, ("hooks", "PreToolUse"), HOOK_CLAUDE_CODE, "railspec hook "),
            # Por máquina: confiar en el `.mcp.json` del proyecto y abrir la carpeta de worktrees.
            ElementosLista(local, ("enabledMcpjsonServers",), (NOMBRE_SERVIDOR,)),
        ]
        if dir_worktrees is not None:
            piezas.append(
                ElementosLista(local, ("permissions", "additionalDirectories"), (str(dir_worktrees),))
            )
        return piezas

    def _piezas_opencode(self, raiz: Path) -> list[Pieza]:
        archivo = self.archivo_mcp(raiz)
        esquema = (("$schema", "https://opencode.ai/config.json"),)
        piezas: list[Pieza] = [
            EntradaJson(
                archivo,
                ("mcp", NOMBRE_SERVIDOR),
                {"type": "local", "command": COMANDO_PROXY, "enabled": True},
                vacio=esquema,
            ),
        ]
        # OpenCode nombra las tools MCP `<servidor>_<tool>` y admite permisos por nombre de tool.
        piezas += [
            EntradaJson(
                archivo,
                ("permission", f"{NOMBRE_SERVIDOR}_{t}"),
                "ask",
                siempre_quitar=False,
                vacio=esquema,
                escalar_a="*",
            )
            for t in TOOLS_HUMANAS
        ]
        # Reglas de conducta aplicadas por plugin (`tool.execute.before`) y permiso de los worktrees.
        piezas.append(ArchivoPropio(".opencode/plugins/railspec.js", plantilla("plugin-opencode.js")))
        # Rutas de versiones anteriores del adaptador (carpetas en singular).
        piezas += [
            ArchivoLegado(".opencode/command/railspec.md"),
            ArchivoLegado(".opencode/skill/railspec-bucle/SKILL.md"),
        ]
        return piezas


def _entrada_mcp_json() -> EntradaJson:
    """``.mcp.json`` del proyecto: el mismo para Claude Code y Copilot."""

    return EntradaJson(
        ".mcp.json",
        ("mcpServers", NOMBRE_SERVIDOR),
        {"type": "stdio", "command": COMANDO_PROXY[0], "args": COMANDO_PROXY[1:]},
    )


def _piezas_codex(raiz: Path, dir_worktrees: Path | None) -> list[Pieza]:
    from . import codex

    return codex.piezas(
        plantilla("comando.md"),
        plantilla("skill-bucle.md"),
        plantilla("reglas.md"),
        NOMBRE_SERVIDOR,
        COMANDO_PROXY,
        TOOLS_AUTOMATICAS,
        TOOLS_HUMANAS,
    )


def _piezas_copilot(raiz: Path, dir_worktrees: Path | None) -> list[Pieza]:
    from . import copilot

    return copilot.piezas(
        plantilla("comando.md"),
        plantilla("skill-bucle.md"),
        plantilla("reglas.md"),
        _entrada_mcp_json(),
        NOMBRE_SERVIDOR,
        TOOLS_AUTOMATICAS,
    )


ADAPTADORES: dict[Arnes, Adaptador] = {
    Arnes.claude_code: Adaptador(
        arnes=Arnes.claude_code,
        ruta_comando=".claude/commands/railspec.md",
        ruta_skill=".claude/skills/railspec-bucle/SKILL.md",
        archivo_reglas="CLAUDE.md",
    ),
    Arnes.opencode: Adaptador(
        arnes=Arnes.opencode,
        ruta_comando=".opencode/commands/railspec.md",
        ruta_skill=".opencode/skills/railspec-bucle/SKILL.md",
        archivo_reglas="AGENTS.md",
    ),
    Arnes.codex: Adaptador(
        arnes=Arnes.codex,
        ruta_comando=".agents/skills/railspec/SKILL.md",
        ruta_skill=".agents/skills/railspec-bucle/SKILL.md",
        archivo_reglas="AGENTS.md",
        fabrica=_piezas_codex,
    ),
    Arnes.copilot: Adaptador(
        arnes=Arnes.copilot,
        ruta_comando=".github/skills/railspec/SKILL.md",
        ruta_skill=".github/skills/railspec-bucle/SKILL.md",
        archivo_reglas="AGENTS.md",
        fabrica=_piezas_copilot,
    ),
}


def _adaptador(arnes: Arnes) -> Adaptador:
    if arnes not in ADAPTADORES:
        raise ErrorRailspec(
            f"No hay adaptador para {arnes.value}; disponibles: {', '.join(a.value for a in ADAPTADORES)}."
        )
    return ADAPTADORES[arnes]


def _unicos(rutas: list[str]) -> list[str]:
    return list(dict.fromkeys(rutas))


def instalar(raiz: Path, arnes: Arnes, dir_worktrees: Path | None = None) -> list[str]:
    """Escribe o actualiza lo del adaptador; devuelve las rutas que cambiaron.

    ``dir_worktrees`` es la carpeta hermana donde viven los worktrees de las
    unidades; en Claude Code se abre al arnés en su configuración local.
    """

    piezas = _adaptador(arnes).piezas(raiz, dir_worktrees)
    return _unicos([p.archivo for p in piezas if p.instalar(raiz)])


def verificar(raiz: Path, arnes: Arnes, dir_worktrees: Path | None = None) -> list[str]:
    """Deriva: rutas del adaptador que faltan o no coinciden con esta versión del proxy."""

    piezas = _adaptador(arnes).piezas(raiz, dir_worktrees)
    return _unicos([p.archivo for p in piezas if not p.instalada(raiz)])


def desinstalar(
    raiz: Path, arnes: Arnes, dir_worktrees: Path | None = None, quedan: list[Arnes] | None = None
) -> list[str]:
    """Quita lo que el adaptador instaló y nada más; devuelve las rutas que cambiaron.

    Las piezas idénticas a las de un arnés que se queda (``quedan``; por
    defecto, los demás instalados) se conservan.
    """

    if quedan is None:
        quedan = [a for a in instalados(raiz) if a != arnes]
    ajenas = [p for a in quedan if a != arnes for p in _adaptador(a).piezas(raiz, dir_worktrees)]
    piezas = _adaptador(arnes).piezas(raiz, dir_worktrees)
    return _unicos([p.archivo for p in piezas if p not in ajenas and p.desinstalar(raiz)])


def _propias(arnes: Arnes, raiz: Path) -> list[Pieza]:
    """Piezas que solo pone este arnés: las compartidas no dicen cuál está instalado."""

    ajenas = [p for a, adaptador in ADAPTADORES.items() if a != arnes for p in adaptador.piezas(raiz, None)]
    return [
        p
        for p in ADAPTADORES[arnes].piezas(raiz, None)
        if not isinstance(p, ArchivoLegado) and p not in ajenas
    ]


def instalados(raiz: Path) -> list[Arnes]:
    """Arneses con alguna pieza propia de Railspec presente en el repositorio."""

    return [arnes for arnes in ADAPTADORES if any(p.presente(raiz) for p in _propias(arnes, raiz))]
