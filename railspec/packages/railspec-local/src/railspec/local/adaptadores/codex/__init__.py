"""Adaptador de Codex (CLI de OpenAI).

Verificado con codex-cli 0.159.3:

- Servidor MCP del proyecto en ``.codex/config.toml`` (``[mcp_servers.railspec]``).
  Codex solo lee la configuración del proyecto si el proyecto es de confianza
  (lo pregunta al abrirlo la primera vez y lo guarda en ``~/.codex/config.toml``).
- Permisos por tool con ``[mcp_servers.railspec.tools.<tool>] approval_mode``:
  ``approve`` no pregunta, ``prompt`` pregunta siempre; ``default_tools_approval_mode``
  cubre las tools que el proxy añada en el futuro.
- Codex no tiene comandos de barra del proyecto: el arranque es la skill
  ``railspec`` (el humano escribe ``$railspec <petición>``) y el bucle, la skill
  ``railspec-bucle``, ambas en ``.agents/skills/`` (la carpeta común de skills).
- Las reglas van en ``AGENTS.md``, el mismo bloque que OpenCode.
- Reglas aplicadas con un hook ``PreToolUse`` en ``.codex/hooks.json`` (``railspec hook
  codex``; mismo formato de archivo que los hooks de Claude Code). Codex no ejecuta un hook de
  proyecto hasta que el humano lo revisa y confía (``/hooks`` en el TUI; la confianza queda en
  ``~/.codex/config.toml``, por máquina y ligada al contenido del hook, así que cada cambio del
  hook pide revisarlo de nuevo). Solo ``deny`` funciona: un ``ask`` cuenta como error y la tool
  corre, así que la confirmación de las tools humanas sigue siendo ``approval_mode`` (ver
  ``guardia.hook_codex``). Un hook que no llega a ejecutarse (``railspec`` fuera del PATH, tiempo
  agotado) deja pasar la tool: no falla cerrado.

El bloque TOML va entre comentarios marcadores al final del archivo y solo
contiene tablas, así que no cambia el significado de lo que el humano tenga
antes; si ya define ``mcp_servers.railspec`` por su cuenta, no se toca.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ...errores import ErrorRailspec
from ..piezas import ArchivoPropio, BloqueReglas, EntradaHook, Pieza

INICIO_TOML = "# railspec:inicio (generado por `railspec instalar`; no editar dentro)"
FIN_TOML = "# railspec:fin"
ARCHIVO_CONFIG = ".codex/config.toml"
DIR_SKILLS = ".agents/skills"
ARCHIVO_REGLAS = "AGENTS.md"
ARCHIVO_HOOKS = ".codex/hooks.json"

_PATRON = re.compile(r"\n?" + re.escape(INICIO_TOML) + r".*?" + re.escape(FIN_TOML) + r"\n?", re.DOTALL)


def config_mcp(
    nombre: str, comando: list[str], automaticas: tuple[str, ...], humanas: tuple[str, ...]
) -> str:
    def tabla(tool: str, modo: str) -> str:
        return f'\n[mcp_servers.{nombre}.tools.{tool}]\napproval_mode = "{modo}"\n'

    lineas = [
        f"[mcp_servers.{nombre}]",
        f'command = "{comando[0]}"',
        "args = [" + ", ".join(f'"{a}"' for a in comando[1:]) + "]",
        # Lo que no esté listado (tools nuevas del proxy) pregunta.
        'default_tools_approval_mode = "prompt"',
    ]
    texto = "\n".join(lineas) + "\n"
    texto += "".join(tabla(t, "approve") for t in automaticas)
    # Decisiones humanas: Codex pide confirmación siempre, aunque alguien cambie el modo por defecto.
    texto += "".join(tabla(t, "prompt") for t in humanas)
    return texto


def _validar(ruta: Path, texto: str) -> dict:
    try:
        return tomllib.loads(texto)
    except tomllib.TOMLDecodeError as exc:
        raise ErrorRailspec(f"{ruta} no es TOML válido; no se toca: {exc}") from exc


@dataclass(frozen=True)
class BloqueToml:
    """Tablas de Railspec entre marcadores dentro de un TOML del arnés."""

    archivo: str
    contenido: str
    servidor: str

    @property
    def bloque(self) -> str:
        return f"{INICIO_TOML}\n{self.contenido.rstrip()}\n{FIN_TOML}\n"

    def _leer(self, raiz: Path) -> str:
        ruta = raiz / self.archivo
        return ruta.read_text(encoding="utf-8") if ruta.is_file() else ""

    def instalar(self, raiz: Path) -> bool:
        ruta = raiz / self.archivo
        actual = self._leer(raiz)
        sin_bloque = _PATRON.sub("\n", actual, count=1) if INICIO_TOML in actual else actual
        if self.servidor in _validar(ruta, sin_bloque).get("mcp_servers", {}):
            raise ErrorRailspec(
                f"{ruta} ya define mcp_servers.{self.servidor} fuera del bloque de Railspec; "
                "quítalo o deja que lo gestione `railspec instalar`."
            )
        if INICIO_TOML in actual:
            nuevo = _PATRON.sub(lambda m: ("\n" if m.group(0).startswith("\n") else "") + self.bloque, actual)
        else:
            separador = "" if not actual else ("\n" if actual.endswith("\n") else "\n\n")
            nuevo = actual + separador + self.bloque
        if nuevo == actual:
            return False
        _validar(ruta, nuevo)
        ruta.parent.mkdir(parents=True, exist_ok=True)
        ruta.write_text(nuevo, encoding="utf-8")
        return True

    def instalada(self, raiz: Path) -> bool:
        return self.bloque in self._leer(raiz)

    def presente(self, raiz: Path) -> bool:
        return INICIO_TOML in self._leer(raiz)

    def desinstalar(self, raiz: Path) -> bool:
        if not self.presente(raiz):
            return False
        ruta = raiz / self.archivo
        actual = self._leer(raiz)
        nuevo = _PATRON.sub("\n", actual, count=1)
        nuevo = nuevo.rstrip("\n") + "\n" if nuevo.strip() else ""
        if actual.startswith(INICIO_TOML):
            nuevo = nuevo.lstrip("\n")
        if not nuevo:
            ruta.unlink()
            carpeta = ruta.parent
            if carpeta != raiz and not any(carpeta.iterdir()):
                carpeta.rmdir()
        else:
            ruta.write_text(nuevo, encoding="utf-8")
        return True


def piezas(
    comando: str,
    skill_bucle: str,
    reglas: str,
    servidor: str,
    comando_proxy: list[str],
    automaticas: tuple[str, ...],
    humanas: tuple[str, ...],
    hook: dict,
) -> list[Pieza]:
    from ..arranque import skill_arranque

    return [
        ArchivoPropio(f"{DIR_SKILLS}/railspec/SKILL.md", skill_arranque(comando, "`$railspec`")),
        ArchivoPropio(f"{DIR_SKILLS}/railspec-bucle/SKILL.md", skill_bucle),
        BloqueToml(ARCHIVO_CONFIG, config_mcp(servidor, comando_proxy, automaticas, humanas), servidor),
        EntradaHook(ARCHIVO_HOOKS, ("hooks", "PreToolUse"), hook, "railspec hook "),
        BloqueReglas(ARCHIVO_REGLAS, reglas),
    ]
