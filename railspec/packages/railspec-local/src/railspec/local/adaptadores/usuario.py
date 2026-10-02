"""Adaptadores en el alcance de usuario: la configuración del arnés de la persona, no la del repositorio.

``railspec instalar --alcance usuario`` deja el stack de tres piezas (comando ``/railspec``, skill
``railspec-bucle`` y reglas de conducta), el servidor MCP, los permisos y la guardia en la carpeta de
configuración que cada arnés lee para cualquier repositorio, de modo que no hace falta tocar cada clon:

====================== ==================================================================
Claude Code            ``~/.claude/`` (comando, skill, ``settings.json``, ``CLAUDE.md``) y el
                       servidor MCP en ``~/.claude.json`` (``mcpServers``, alcance *user*)
OpenCode               ``$XDG_CONFIG_HOME/opencode`` (``~/.config/opencode``): ``commands/``,
                       ``skills/``, ``plugins/``, ``AGENTS.md`` y ``opencode.json``
====================== ==================================================================

Lo de cada repositorio no cambia: ``.railspec/config.json`` (org, workspace, repositorio y nivel de
código) sigue siendo del repositorio y lo escribe ``railspec instalar`` sin ``--arnes``.

Codex y GitHub Copilot CLI quedan fuera: no se verificó dónde cargan en la configuración del usuario los
hooks, las skills y el servidor MCP, y una ruta supuesta dejaría la guardia sin aplicar sin avisar.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from railspec.contracts.comun import Arnes

from ..errores import ErrorRailspec
from . import (
    COMANDO_PROXY,
    NOMBRE_SERVIDOR,
    _comando,
    _unicos,
    piezas_ajustes_claude_code,
    piezas_config_opencode,
    plantilla,
)
from .piezas import ArchivoPropio, BloqueReglas, ElementosLista, EntradaJson, Pieza

ARNESES = (Arnes.claude_code, Arnes.opencode)

#: Entrada del servidor en ``~/.claude.json``: la forma exacta que escribe ``claude mcp add --scope user``.
ENTRADA_MCP_CLAUDE_CODE = {"type": "stdio", "command": COMANDO_PROXY[0], "args": COMANDO_PROXY[1:], "env": {}}


class AlcanceNoSoportado(ErrorRailspec):
    def __init__(self, arnes: Arnes) -> None:
        super().__init__(
            f"{arnes.value} no admite todavía el alcance de usuario: no se verificó dónde carga en "
            "~/ los hooks, las skills y el servidor MCP, y una ruta supuesta dejaría la guardia sin "
            f"aplicar sin avisar. Instálalo por repositorio (`railspec instalar --arnes {arnes.value}`). "
            f"Con alcance de usuario: {', '.join(a.value for a in ARNESES)}."
        )


def comprobar(arnes: Arnes) -> Arnes:
    if arnes not in ARNESES:
        raise AlcanceNoSoportado(arnes)
    return arnes


def carpeta_personal(home: Path | None) -> Path:
    if home is not None:
        return home
    try:
        return Path.home()
    except RuntimeError as exc:  # sin HOME ni entrada en passwd (contenedor con uid arbitrario)
        raise ErrorRailspec("No se pudo determinar tu carpeta personal: fija HOME.") from exc


def base(arnes: Arnes, home: Path | None = None, entorno: Mapping[str, str] | None = None) -> Path:
    """Carpeta contra la que se resuelven las rutas de las piezas de ``arnes``."""

    comprobar(arnes)
    carpeta = carpeta_personal(home)
    if arnes == Arnes.claude_code:
        return carpeta
    env = os.environ if entorno is None else entorno
    xdg = Path(env.get("XDG_CONFIG_HOME") or "")
    # La especificación XDG manda ignorar una ruta relativa.
    return (xdg if xdg.is_absolute() else carpeta / ".config") / "opencode"


def _archivo_opencode(raiz: Path) -> str:
    for nombre in ("opencode.json", "opencode.jsonc"):
        if (raiz / nombre).is_file():
            return nombre
    return "opencode.json"


def piezas(arnes: Arnes, raiz: Path, dir_worktrees: Path | None = None) -> list[Pieza]:
    """Piezas del alcance de usuario; ``raiz`` es la ``base`` de ``arnes``."""

    comprobar(arnes)
    reglas = plantilla("reglas-usuario.md")
    if arnes == Arnes.opencode:
        return [
            ArchivoPropio("commands/railspec.md", _comando(arnes)),
            ArchivoPropio("skills/railspec-bucle/SKILL.md", plantilla("skill-bucle.md")),
            *piezas_config_opencode(_archivo_opencode(raiz)),
            ArchivoPropio("plugins/railspec.js", plantilla("plugin-opencode.js")),
            BloqueReglas("AGENTS.md", reglas),
        ]
    ajustes = ".claude/settings.json"
    piezas_claude: list[Pieza] = [
        ArchivoPropio(".claude/commands/railspec.md", _comando(arnes)),
        ArchivoPropio(".claude/skills/railspec-bucle/SKILL.md", plantilla("skill-bucle.md")),
        EntradaJson(".claude.json", ("mcpServers", NOMBRE_SERVIDOR), ENTRADA_MCP_CLAUDE_CODE),
        *piezas_ajustes_claude_code(ajustes),
        BloqueReglas(".claude/CLAUDE.md", reglas),
    ]
    if dir_worktrees is not None:
        piezas_claude.append(
            ElementosLista(ajustes, ("permissions", "additionalDirectories"), (str(dir_worktrees),))
        )
    return piezas_claude


def instalar(
    arnes: Arnes,
    dir_worktrees: Path | None = None,
    home: Path | None = None,
    entorno: Mapping[str, str] | None = None,
) -> list[str]:
    """Escribe o actualiza lo del adaptador; devuelve las rutas absolutas que cambiaron."""

    raiz = base(arnes, home, entorno)
    return _absolutas(raiz, [p.archivo for p in piezas(arnes, raiz, dir_worktrees) if p.instalar(raiz)])


def verificar(
    arnes: Arnes,
    dir_worktrees: Path | None = None,
    home: Path | None = None,
    entorno: Mapping[str, str] | None = None,
) -> list[str]:
    """Deriva: rutas absolutas del adaptador que faltan o no coinciden con esta versión del proxy."""

    raiz = base(arnes, home, entorno)
    return _absolutas(raiz, [p.archivo for p in piezas(arnes, raiz, dir_worktrees) if not p.instalada(raiz)])


def desinstalar(
    arnes: Arnes,
    dir_worktrees: Path | None = None,
    home: Path | None = None,
    entorno: Mapping[str, str] | None = None,
) -> list[str]:
    """Quita lo que el adaptador instaló y nada más; devuelve las rutas absolutas que cambiaron."""

    raiz = base(arnes, home, entorno)
    return _absolutas(raiz, [p.archivo for p in piezas(arnes, raiz, dir_worktrees) if p.desinstalar(raiz)])


def instalados(home: Path | None = None, entorno: Mapping[str, str] | None = None) -> list[Arnes]:
    """Arneses con alguna pieza de Railspec presente en la configuración del usuario."""

    resultado = []
    for arnes in ARNESES:
        raiz = base(arnes, home, entorno)
        if any(p.presente(raiz) for p in piezas(arnes, raiz)):
            resultado.append(arnes)
    return resultado


def _absolutas(raiz: Path, rutas: list[str]) -> list[str]:
    return _unicos([str(raiz / r) for r in rutas])
