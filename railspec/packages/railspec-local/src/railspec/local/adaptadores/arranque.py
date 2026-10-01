"""Comando de arranque como skill, para arneses sin comandos de barra propios del proyecto.

Codex y Copilot descubren skills (``<carpeta>/<nombre>/SKILL.md``) pero no los
comandos de ``.claude/commands`` ni ``.opencode/commands``. La skill
``railspec`` lleva el mismo texto que ``plantillas/comando.md`` (la fuente
única), con cabecera de skill y la petición tomada del mensaje del humano en
vez de ``$ARGUMENTS``.
"""

from __future__ import annotations

import json


def _partes(texto: str) -> tuple[list[str], str]:
    """Líneas del frontmatter y cuerpo de un Markdown con cabecera ``---``."""

    if not texto.startswith("---\n"):
        return [], texto
    cabecera, cuerpo = texto[4:].split("\n---\n", 1)
    return cabecera.splitlines(), cuerpo


def con_frontmatter(texto: str, extra: list[str]) -> str:
    """Añade líneas al frontmatter de una skill (``allowed-tools``, ``argument-hint``)."""

    cabecera, cuerpo = _partes(texto)
    return "---\n" + "\n".join(cabecera + extra) + "\n---\n" + cuerpo


def skill_arranque(
    comando: str, invocacion: str, extra: list[str] | None = None, con_argumentos: bool = False
) -> str:
    """``comando``: el texto de ``plantillas/comando.md``; ``invocacion``: cómo la llama el humano.

    ``con_argumentos`` conserva ``argument-hint`` (Copilot lo muestra al escribir ``/railspec``).
    """

    cabecera, cuerpo = _partes(comando)
    descripcion = (
        f"Use when: el humano invoca {invocacion} para arrancar o retomar una unidad de Railspec "
        "(llama unit_start o unit_status y sigue con railspec-bucle). Does NOT ejecutar órdenes: eso es "
        "railspec-bucle. Keywords: railspec, arrancar unidad, retomar unidad, unit_start."
    )
    lineas = ["name: railspec", f"description: {json.dumps(descripcion, ensure_ascii=False)}"]
    if con_argumentos:
        lineas += [linea for linea in cabecera if linea.startswith("argument-hint:")]
    cuerpo = cuerpo.lstrip("\n").replace(
        "$ARGUMENTS", f"el texto que el humano escribió junto a {invocacion}"
    )
    return "---\n" + "\n".join(lineas + (extra or [])) + "\n---\n\n" + cuerpo
