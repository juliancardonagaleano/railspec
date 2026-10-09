#!/usr/bin/env python3
"""Consulta `GET /metrics` de un servidor desplegado y dice si Prometheus lo podría leer (`up == 1`).

    RAILSPEC_METRICAS_TOKEN=... python3 verificar_metricas.py https://railspec.onrender.com

Sin dependencias fuera de la biblioteca estándar. Sale con 0 si el endpoint responde 200 con las
familias de métricas esperadas y todas las sondas en 1; con 1 si algo falla, diciendo qué.
La clave se lee del entorno (nunca como argumento: quedaría en el historial de la shell): una clave
creada en la consola (Plataforma → Operación) o el `RAILSPEC_METRICAS_TOKEN` del servicio.
"""

from __future__ import annotations

import os
import re
import sys
import urllib.error
import urllib.request

#: Familias que el servidor publica siempre (ver `server/metricas.py`).
FAMILIAS = (
    "railspec_info",
    "railspec_estado_esquema",
    "railspec_sonda_ok",
    "railspec_proceso_inicio_segundos",
)
_MUESTRA = re.compile(r"^(?P<nombre>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<etiquetas>.*)\})?\s+(?P<valor>\S+)$")


def problemas(cuerpo: str) -> list[str]:
    """Lo que está mal en un cuerpo de `/metrics`; lista vacía si Prometheus lo podría usar."""

    muestras: list[tuple[str, str, float]] = []
    for linea in cuerpo.splitlines():
        if not linea or linea.startswith("#"):
            continue
        m = _MUESTRA.match(linea)
        if m is None:
            return [f"línea que no es formato de Prometheus: {linea[:80]!r}"]
        try:
            muestras.append((m["nombre"], m["etiquetas"] or "", float(m["valor"])))
        except ValueError:
            return [f"valor que no es un número: {linea[:80]!r}"]
    faltan = [f for f in FAMILIAS if not any(n == f for n, _, _ in muestras)]
    fallos = [f"falta la familia {f}" for f in faltan]
    fallos += [
        f"sonda caída: {etiquetas}"
        for nombre, etiquetas, valor in muestras
        if nombre == "railspec_sonda_ok" and valor != 1
    ]
    codigo = {e: v for n, e, v in muestras if n == "railspec_estado_esquema"}
    cod = next((v for e, v in codigo.items() if 'origen="codigo"' in e), None)
    alm = next((v for e, v in codigo.items() if 'origen="almacenado"' in e), None)
    if cod is not None and alm is not None and cod != alm:
        fallos.append(f"esquema desalineado: el código es {cod:g} y el estado guardado {alm:g}")
    return fallos


def consultar(base: str, token: str, tiempo: float = 10.0) -> tuple[int, str]:
    peticion = urllib.request.Request(
        base.rstrip("/") + "/metrics", headers={"Authorization": f"Bearer {token}"}
    )
    try:
        with urllib.request.urlopen(peticion, timeout=tiempo) as r:  # noqa: S310 (URL del operador)
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, ""


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    token = os.environ.get("RAILSPEC_METRICAS_TOKEN", "")
    if len(args) != 1 or not token:
        print("uso: RAILSPEC_METRICAS_TOKEN=... verificar_metricas.py <url-base>", file=sys.stderr)
        return 2
    try:
        estado, cuerpo = consultar(args[0], token)
    except OSError as e:
        print(f"up == 0: no se pudo conectar ({e})")
        return 1
    if estado == 401:
        print("up == 0: 401, la clave no es una activa de la consola ni RAILSPEC_METRICAS_TOKEN del servicio")
        return 1
    if estado == 404:
        print(
            "up == 0: 404, el servicio no tiene RAILSPEC_METRICAS_TOKEN ni claves activas en la consola "
            "(o no es este servidor)"
        )
        return 1
    if estado != 200:
        print(f"up == 0: respondió {estado}")
        return 1
    fallos = problemas(cuerpo)
    if fallos:
        print("up == 1, pero el contenido tiene problemas:")
        for f in fallos:
            print(f"  - {f}")
        return 1
    print("up == 1: /metrics responde con las familias esperadas y las sondas en 1")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
