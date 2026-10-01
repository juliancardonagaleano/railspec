"""Adaptador de GitHub Copilot CLI.

Verificado con GitHub Copilot CLI 1.0.90:

- Servidor MCP del proyecto en ``.mcp.json`` con la misma entrada que Claude
  Code (``type: stdio``): si los dos arneses están instalados, la comparten.
  Copilot solo carga los servidores del proyecto en carpetas de confianza (lo
  pregunta al abrir el repositorio la primera vez).
- Skills en ``.github/skills/<nombre>/SKILL.md``; toda skill se invoca como
  ``/<nombre>``, así que ``/railspec`` es la skill de arranque.
- Copilot pide permiso para toda tool MCP y no admite listas de permisos
  versionadas en el repositorio. ``allowed-tools`` en las skills aprueba las
  tools del bucle (``railspec(unit_advance)``) durante la sesión en que se
  invoca la skill; las que registran una decisión humana no se listan, así que
  Copilot pregunta siempre por ellas.
- Las reglas van en ``AGENTS.md``, el mismo bloque que OpenCode y Codex.
"""

from __future__ import annotations

from ..piezas import ArchivoPropio, BloqueReglas, EntradaJson, Pieza

DIR_SKILLS = ".github/skills"
ARCHIVO_REGLAS = "AGENTS.md"


def allowed_tools(servidor: str, tools: tuple[str, ...]) -> list[str]:
    return ["allowed-tools:"] + [f"  - {servidor}({t})" for t in tools]


def piezas(
    comando: str,
    skill_bucle: str,
    reglas: str,
    entrada_mcp: EntradaJson,
    servidor: str,
    automaticas: tuple[str, ...],
) -> list[Pieza]:
    from ..arranque import con_frontmatter, skill_arranque

    permisos = allowed_tools(servidor, automaticas)
    return [
        ArchivoPropio(
            f"{DIR_SKILLS}/railspec/SKILL.md",
            skill_arranque(comando, "`/railspec`", permisos, con_argumentos=True),
        ),
        ArchivoPropio(f"{DIR_SKILLS}/railspec-bucle/SKILL.md", con_frontmatter(skill_bucle, permisos)),
        entrada_mcp,
        BloqueReglas(ARCHIVO_REGLAS, reglas),
    ]
