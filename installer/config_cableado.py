"""Cableado con marcador del contrato de configuración del destino.

Tres funciones — una por archivo JSON del contrato de configuración que el
kit gobierna en el destino (CA-35, CA-45..CA-47, CA-51, CA-52):

  * ``mezclar_claude_settings(git_root, kit_root)`` — ``.claude/settings.json``
    (claves anidadas ``hooks.PreToolUse`` / ``hooks.PrePush`` con
    discriminador interno ``_sdd_kit: true`` por hook entry).
  * ``mezclar_mcp_json(git_root, kit_root)`` — ``.mcp.json`` (clave raíz
    ``mcpServers``, discriminador por server).
  * ``mezclar_opencode_jsonc(git_root, kit_root)`` — ``opencode.jsonc``
    (clave raíz ``mcp``, mismo discriminador, sintaxis JSONC tolerante a
    ``//`` comments y trailing commas al leer).

Las tres operan por marcador interno ``_sdd_kit: true`` dentro de cada entry
declarada por el kit. Esa forma sobrevive a que el destino reordene o
reformatee el archivo (el discriminador es por valor de campo, no por
posición ni por número de línea). Las tres son idempotentes byte-a-byte
entre dos ``--install`` consecutivos (CA-46/CA-51/CA-52).

Si el archivo destino no existe, se crea. Si el JSON es inválido, aborta
sin pisar (exit code 2 que el instalador propaga).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

DISCRIMINATOR = "_sdd_kit"
TRUE = True

SETTINGS_REL = Path(".claude") / "settings.json"
MCP_JSON_REL = Path(".mcp.json")
OPENCODE_JSONC_REL = Path("opencode.jsonc")

KIT_SETTINGS_REL = Path(".claude") / "settings.json"
KIT_MCP_JSON_REL = Path(".mcp.json")
KIT_OPENCODE_JSONC_REL = Path("opencode.jsonc")


class CableadoError(Exception):
    """El archivo destino existe pero su JSON es inválido o no se puede
    leer/escribir. El instalador lo traduce a código 2 (error operativo)."""


def _read_json(path: Path) -> dict | None:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CableadoError(f"no se pudo leer {path}: {exc}") from exc
    try:
        return json.loads(text)
    except json.JSONDecodeError as exc:
        raise CableadoError(f"{path} tiene JSON inválido: {exc}") from exc


def _strip_jsonc(text: str) -> str:
    out_lines: list[str] = []
    for raw in text.splitlines():
        stripped = raw.lstrip()
        if stripped.startswith("//"):
            continue
        if "//" in raw:
            hash_idx = raw.find("//")
            quote_count = 0
            i = 0
            while i < hash_idx:
                ch = raw[i]
                if ch == '"' and (i == 0 or raw[i - 1] != "\\"):
                    quote_count ^= 1
                i += 1
            if quote_count == 0:
                raw = raw[:hash_idx].rstrip()
        out_lines.append(raw)
    cleaned = "\n".join(out_lines)
    cleaned = re.sub(r",(\s*[}\]])", r"\1", cleaned)
    return cleaned


def _read_jsonc(path: Path) -> dict | None:
    if not path.is_file():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CableadoError(f"no se pudo leer {path}: {exc}") from exc
    try:
        return json.loads(_strip_jsonc(text))
    except json.JSONDecodeError as exc:
        raise CableadoError(f"{path} tiene JSON/JSONC inválido: {exc}") from exc


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, indent=2, ensure_ascii=False)
    if path.is_file():
        existing = path.read_text(encoding="utf-8")
        if existing.endswith("\n") and not text.endswith("\n"):
            text = text + "\n"
    path.write_text(text, encoding="utf-8")


def _replace_hooks_event(settings: dict, event: str, kit_hooks: list) -> None:
    hooks = settings.setdefault("hooks", {})
    if not isinstance(hooks, dict):
        return
    entries = hooks.get(event)
    if not isinstance(entries, list):
        return
    kept: list = []
    for entry in entries:
        if not isinstance(entry, dict):
            kept.append(entry)
            continue
        if entry.get(DISCRIMINATOR) is TRUE:
            continue
        kept.append(entry)
    for kit_entry in kit_hooks:
        if isinstance(kit_entry, dict):
            kit_entry.setdefault(DISCRIMINATOR, TRUE)
        kept.append(kit_entry)
    hooks[event] = kept


def _read_settings_kit_block(kit_root: Path) -> dict:
    path = kit_root / KIT_SETTINGS_REL
    return _read_json(path) or {}


def mezclar_claude_settings(git_root: Path, kit_root: Path) -> None:
    """Fusiona con marcador ``.claude/settings.json`` del destino.

    Política:
      - Mantiene ``permissions``, ``env`` y cualquier hook ajeno intactos.
      - Conserva el orden de los hooks: los del kit aparecen al final de
        cada evento ``PreToolUse``/``PrePush`` tras los hooks ajenos.
      - Discriminador: campo interno ``_sdd_kit: true`` en cada entry del
        kit. Las entries con el discriminador se eliminan antes de re-
        agregar (idempotencia byte-a-byte entre dos ``--install``
        consecutivos, CA-13).
      - No-op si el kit no trae ``.claude/settings.json`` y el destino no
        tiene uno: no se crea archivo vacío (cumple CA-13 sobre el
        contenido del worktree).
    """
    dest_path = git_root / SETTINGS_REL
    settings = _read_json(dest_path)
    if settings is None:
        raise CableadoError(f"{dest_path} no se pudo cargar")
    kit_settings = _read_settings_kit_block(kit_root)
    if not isinstance(kit_settings, dict):
        kit_settings = {}

    has_kit_event = False
    for event in ("PreToolUse", "PrePush"):
        kit_hooks_obj = kit_settings.get("hooks") if isinstance(kit_settings, dict) else None
        kit_event = kit_hooks_obj.get(event) if isinstance(kit_hooks_obj, dict) else None
        if not isinstance(kit_event, list) or not kit_event:
            continue
        has_kit_event = True
        _replace_hooks_event(settings, event, list(kit_event))

    if not dest_path.is_file() and not has_kit_event:
        return
    _write_json(dest_path, settings)


def mezclar_mcp_json(git_root: Path, kit_root: Path) -> None:
    """Fusiona con marcador ``.mcp.json`` del destino bajo ``mcpServers``.

    Cada server entry declarada por el kit lleva ``_sdd_kit: true``. Las
    ajenas al kit (sin el discriminador) se preservan sin cambios. Si el
    archivo no existe, se crea.
    """
    dest_path = git_root / MCP_JSON_REL
    data = _read_json(dest_path)
    if data is None:
        raise CableadoError(f"{dest_path} no se pudo cargar")
    if not isinstance(data, dict):
        data = {}

    kit_path = kit_root / KIT_MCP_JSON_REL
    kit_data = _read_json(kit_path) or {}
    if not isinstance(kit_data, dict):
        kit_data = {}

    kit_servers = kit_data.get("mcpServers")
    if not isinstance(kit_servers, dict):
        kit_servers = {}

    dest_servers = data.get("mcpServers")
    if not isinstance(dest_servers, dict):
        dest_servers = {}

    for name, body in list(dest_servers.items()):
        if isinstance(body, dict) and body.get(DISCRIMINATOR) is TRUE:
            del dest_servers[name]

    merged: dict = {}
    for name, body in dest_servers.items():
        merged[name] = body
    for name, body in kit_servers.items():
        if isinstance(body, dict):
            body = dict(body)
            body[DISCRIMINATOR] = TRUE
        merged[name] = body

    data["mcpServers"] = merged
    _write_json(dest_path, data)


def mezclar_opencode_jsonc(git_root: Path, kit_root: Path) -> None:
    """Fusiona con marcador ``opencode.jsonc`` del destino bajo ``mcp``.

    Acepta JSONC (``//`` comments y trailing commas) al leer; emite JSON
    estricto al escribir. Mismo discriminador ``_sdd_kit: true`` por server
    entry que ``mezclar_mcp_json``.
    """
    dest_path = git_root / OPENCODE_JSONC_REL
    data = _read_jsonc(dest_path)
    if data is None:
        raise CableadoError(f"{dest_path} no se pudo cargar")
    if not isinstance(data, dict):
        data = {}

    kit_path = kit_root / KIT_OPENCODE_JSONC_REL
    kit_data = _read_jsonc(kit_path) or {}
    if not isinstance(kit_data, dict):
        kit_data = {}

    kit_servers = kit_data.get("mcp")
    if not isinstance(kit_servers, dict):
        kit_servers = {}

    dest_servers = data.get("mcp")
    if not isinstance(dest_servers, dict):
        dest_servers = {}

    for name, body in list(dest_servers.items()):
        if isinstance(body, dict) and body.get(DISCRIMINATOR) is TRUE:
            del dest_servers[name]

    merged: dict = {}
    for name, body in dest_servers.items():
        merged[name] = body
    for name, body in kit_servers.items():
        if isinstance(body, dict):
            body = dict(body)
            body[DISCRIMINATOR] = TRUE
        merged[name] = body

    data["mcp"] = merged
    _write_json(dest_path, data)


def cablear(git_root: Path, kit_root: Path) -> None:
    """Invoca las tres fusiones en orden (Claude settings → mcp.json →
    opencode.jsonc). Llamado por el instalador al cierre exitoso del run.
    """
    mezclar_claude_settings(git_root, kit_root)
    mezclar_mcp_json(git_root, kit_root)
    mezclar_opencode_jsonc(git_root, kit_root)