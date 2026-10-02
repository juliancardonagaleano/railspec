"""Convivencia del kit con Railspec en los archivos que los dos tocan.

``railspec instalar`` escribe en cuatro archivos que el kit también gobierna:

  * ``.mcp.json``, ``opencode.jsonc`` y ``.claude/settings.json``: el kit fusiona
    sus entradas por el marcador ``_sdd_kit: true`` (``config_cableado``) y Railspec
    añade las suyas (``railspec``, ``permissions``, el hook ``railspec hook``) sin
    marcador. Lo ajeno al kit es de quien lo puso.
  * ``AGENTS.md``: carga ordinaria del kit (se sobrescribe), y Railspec añade al final
    un bloque entre ``<!-- railspec:inicio … -->`` y ``<!-- railspec:fin -->``.

Además, los espejos ``.claude/commands`` y ``.claude/skills`` del kit no copian ni podan
lo que se llama ``railspec*`` (``materializers/ajenos.py``).

Este módulo concentra lo único que el kit sabe de Railspec, para que el instalador
no pise ese bloque al reinstalar y para que ``verificar`` mida solo lo que el kit
puso: el digest de línea base de estos cuatro archivos es el de su parte del kit
(``contenido_del_kit``), no el del archivo entero. Un archivo sin nada ajeno tiene
el mismo contenido de kit que antes; los registros de instalación anteriores, con el
digest del archivo entero, se siguen aceptando (``coincide``).
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

DISCRIMINATOR = "_sdd_kit"

#: Archivos que ``config_cableado`` fusiona con el destino en vez de copiarlos byte a byte.
MEZCLADOS = frozenset({".mcp.json", "opencode.jsonc", ".claude/settings.json"})

#: Prosa de gobernanza del kit: se sobrescribe, salvo el bloque que Railspec escribió dentro.
ARCHIVO_DE_PROSA = "AGENTS.md"

#: Claves de cada archivo fusionado donde el kit pone entradas marcadas.
_CLAVES_MEZCLADAS: dict[str, tuple[tuple[str, ...], ...]] = {
    ".mcp.json": (("mcpServers",),),
    "opencode.jsonc": (("mcp",), ("agent",)),
    ".claude/settings.json": (("hooks", "PreToolUse"), ("hooks", "PrePush")),
}

_BLOQUE_RAILSPEC = re.compile(rb"<!-- railspec:inicio[^>]*-->.*?<!-- railspec:fin -->\n?", re.DOTALL)
_COMENTARIOS = re.compile(r'"(?:\\.|[^"\\])*"|//[^\n]*|/\*.*?\*/', re.DOTALL)
_COMA_FINAL = re.compile(r'"(?:\\.|[^"\\])*"|,(\s*[}\]])', re.DOTALL)


def _leer_jsonc(data: bytes) -> Any:
    texto = data.decode("utf-8")
    texto = _COMENTARIOS.sub(lambda m: m.group(0) if m.group(0).startswith('"') else "", texto)
    texto = _COMA_FINAL.sub(lambda m: m.group(1) if m.group(1) is not None else m.group(0), texto)
    return json.loads(texto) if texto.strip() else {}


def _marcada(entrada: Any) -> bool:
    return isinstance(entrada, dict) and entrada.get(DISCRIMINATOR) is True


def _parte_del_kit(dest_rel: str, data: bytes) -> bytes | None:
    """Las entradas marcadas del JSON, en forma canónica; ``None`` si no se puede leer."""

    try:
        documento = _leer_jsonc(data)
    except (ValueError, UnicodeDecodeError):
        return None
    if not isinstance(documento, dict):
        return None
    parte: dict[str, Any] = {}
    for claves in _CLAVES_MEZCLADAS[dest_rel]:
        actual: Any = documento
        for clave in claves:
            actual = actual.get(clave) if isinstance(actual, dict) else None
        if isinstance(actual, dict):
            entradas: Any = {k: v for k, v in actual.items() if _marcada(v)}
        elif isinstance(actual, list):
            entradas = [e for e in actual if _marcada(e)]
        else:
            continue
        if entradas:
            parte[".".join(claves)] = entradas
    return json.dumps(parte, sort_keys=True, ensure_ascii=False).encode("utf-8")


def bloque_railspec(data: bytes) -> bytes | None:
    """El bloque de reglas de Railspec de un ``AGENTS.md``, con su salto de línea final."""

    encontrado = _BLOQUE_RAILSPEC.search(data)
    return encontrado.group(0) if encontrado else None


def sin_bloque_railspec(data: bytes) -> bytes:
    """``data`` sin el bloque de Railspec ni la línea en blanco que lo separaba del resto."""

    encontrado = _BLOQUE_RAILSPEC.search(data)
    if encontrado is None:
        return data
    antes, despues = data[: encontrado.start()], data[encontrado.end() :]
    if antes.endswith(b"\n\n"):
        antes = antes[:-1]
    return antes + despues


def conservar_bloque_railspec(nuevo: bytes, actual: bytes | None) -> bytes:
    """``nuevo`` con el bloque de Railspec que ``actual`` ya tenía, puesto como lo pone ``instalar``."""

    bloque = bloque_railspec(actual) if actual else None
    if bloque is None:
        return nuevo
    separador = b"\n" if nuevo.endswith(b"\n") else b"\n\n"
    return (nuevo + separador if nuevo else nuevo) + bloque


def contenido_del_kit(dest_rel: str, data: bytes) -> bytes:
    """Lo que de ``data`` es del kit: el archivo entero salvo en los cuatro compartidos."""

    if dest_rel in MEZCLADOS:
        parte = _parte_del_kit(dest_rel, data)
        return data if parte is None else parte
    if dest_rel == ARCHIVO_DE_PROSA:
        return sin_bloque_railspec(data)
    return data


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def digest_de_instalacion(dest_rel: str, data: bytes) -> str:
    """Digest de línea base de un archivo del destino (el de su parte del kit)."""

    return sha256_bytes(contenido_del_kit(dest_rel, data))


def coincide(dest_rel: str, data: bytes, digest: str) -> bool:
    """``data`` es lo que registra ``digest``: por contenido del kit o, en registros anteriores, entero."""

    return digest_de_instalacion(dest_rel, data) == digest or sha256_bytes(data) == digest
