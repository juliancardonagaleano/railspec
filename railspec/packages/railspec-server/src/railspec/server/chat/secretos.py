"""Regla ``secretos``: los mismos patrones que aplica el proxy antes de subir un snapshot.

``railspec-server`` y ``railspec-local`` nunca se importan entre sí, así que la
lista se copia de ``railspec/local/secretos.py``; una prueba compara ambas
cuando los dos paquetes están instalados, para que no diverjan.
"""

from __future__ import annotations

import re

PATRONES: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
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
        ("cadena-conexion", r"\b[a-z][a-z0-9+.-]*://[^\s:/@'\"]+:[^\s@'\"]{3,}@[^\s'\"]+"),
        ("jwt", r"\beyJ[A-Za-z0-9_-]{10,}\.eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"),
        (
            "asignacion-secreto",
            r"(?i)\b(?:password|passwd|pwd|secret|api[_-]?key|access[_-]?token|client[_-]?secret)"
            r"\s*[:=]\s*['\"][^'\"\s$<{]{8,}['\"]",
        ),
    )
)


def detectar(texto: str) -> list[str]:
    """Tipos de secreto encontrados (nunca el valor)."""

    return [nombre for nombre, patron in PATRONES if patron.search(texto)]
