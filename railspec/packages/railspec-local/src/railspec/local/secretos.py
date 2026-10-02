"""Exclusiones y detección de secretos antes de que nada salga del clon.

Dos barreras, en este orden:

1. Exclusión: archivos que por su ruta nunca se indexan ni viajan (``.env``,
   claves, dependencias, build) más lo que liste ``.railspecignore``. Un
   archivo excluido no aparece en el snapshot, ni siquiera su hash.
2. Detección: el contenido de cada archivo tocado que sí viajaría se revisa
   con patrones de secretos conocidos. Un solo hallazgo impide el snapshot
   (el contrato exige ``escaneo_secretos.hallazgos == 0``). Los hallazgos se
   informan por ruta, línea y tipo, nunca con el valor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from . import __version__
from .rutas import coincide, leer_patrones

HERRAMIENTA = "railspec-secretos"
VERSION = __version__
ARCHIVO_IGNORE = ".railspecignore"

EXCLUSIONES_POR_DEFECTO: tuple[str, ...] = (
    ".env",
    ".env.*",
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "*.jks",
    "*.keystore",
    "id_rsa*",
    "id_ed25519*",
    "*.tfstate",
    "*.tfstate.*",
    ".npmrc",
    ".pypirc",
    ".netrc",
    ".git-credentials",
    "secrets.*",
    "node_modules/",
    ".venv/",
    "venv/",
    "__pycache__/",
    "dist/",
    "build/",
    "target/",
    ".railspec/",
    ".codebase-memory/",
)

# Los cuantificadores que aceptan una corrida larga llevan cota (esquema, usuario y clave de
# ``cadena-conexion``; cabecera de ``jwt``): sin ella ``re`` reintenta el mismo tramo desde cada
# frontera de palabra y el costo es cuadrático. El servidor copia estos patrones (``chat.secretos``).
_PATRONES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (nombre, re.compile(expr))
    for nombre, expr in (
        ("clave-privada", r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY"),
        ("aws-access-key", r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b"),
        ("github-token", r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
        ("slack-token", r"\bxox[abposr]-[A-Za-z0-9-]{10,}\b"),
        ("google-api-key", r"\bAIza[0-9A-Za-z_-]{35}\b"),
        ("stripe-key", r"\b(?:sk|rk)_live_[0-9A-Za-z]{20,}\b"),
        ("anthropic-key", r"\bsk-ant-[A-Za-z0-9_-]{20,}\b"),
        ("openai-key", r"\bsk-(?:proj-)?[A-Za-z0-9]{32,}\b"),
        ("azure-storage", r"AccountKey=[A-Za-z0-9+/=]{40,}"),
        ("azure-sas", r"[?&]sig=[A-Za-z0-9%+/=]{30,}"),
        ("cadena-conexion", r"\b[a-z][a-z0-9+.-]{0,31}://[^\s:/@'\"]{1,128}:[^\s@'\"]{3,256}@[^\s'\"]+"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{10,256}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        (
            "asignacion-secreto",
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*['\"][^'\"\s$<{]{8,}['\"]",
        ),
    )
)


@dataclass(frozen=True)
class Hallazgo:
    ruta: str
    linea: int
    tipo: str

    def __str__(self) -> str:
        return f"{self.ruta}:{self.linea} ({self.tipo})"


def exclusiones(raiz: Path) -> list[str]:
    patrones = list(EXCLUSIONES_POR_DEFECTO)
    archivo = raiz / ARCHIVO_IGNORE
    if archivo.is_file():
        patrones += leer_patrones(archivo.read_text(encoding="utf-8"))
    return patrones


def excluido(ruta: str, patrones: list[str]) -> bool:
    return coincide(ruta, patrones)


def escanear(ruta: str, datos: bytes) -> list[Hallazgo]:
    if b"\0" in datos[:8192]:
        return []  # binario: no se indexa ni viaja como texto
    texto = datos.decode("utf-8", "replace")
    hallazgos: list[Hallazgo] = []
    for n, linea in enumerate(texto.splitlines(), start=1):
        for nombre, patron in _PATRONES:
            if patron.search(linea):
                hallazgos.append(Hallazgo(ruta, n, nombre))
                break
    return hallazgos


def redactar(texto: str) -> str:
    """Sustituye secretos en texto libre (salida de validación) por un marcador."""

    for nombre, patron in _PATRONES:
        texto = patron.sub(f"[secreto:{nombre}]", texto)
    return texto
