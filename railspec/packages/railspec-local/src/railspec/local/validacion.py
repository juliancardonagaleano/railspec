"""Ejecución local del comando de validación de una orden.

El comando corre en el worktree de la unidad; el gate de código critica en
remoto sin ejecutar nada. La salida completa se guarda en local
(``.railspec/validacion/<orden>.log``) y al servidor solo viaja lo que el
nivel de código permite: en ``restringido`` nada de la salida (un fallo de
pruebas imprime código fuente), en ``interno`` y ``abierto`` la cola recortada
y con secretos redactados.
"""

from __future__ import annotations

import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from railspec.contracts.comun import NivelCodigo
from railspec.contracts.reporte import SALIDA_MAX_CARACTERES, ResultadoValidacion

from . import secretos

DIR_LOGS = Path(".railspec") / "validacion"


@dataclass
class Ejecucion:
    resultado: ResultadoValidacion
    log: Path
    salida_local: str


def ejecutar(worktree: Path, comando: str, nivel: NivelCodigo, nombre_log: str, timeout_s: int) -> Ejecucion:
    inicio = time.monotonic()
    try:
        proc = subprocess.run(
            comando,
            shell=True,
            cwd=worktree,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            timeout=timeout_s,
            check=False,
        )
        codigo = proc.returncode
        salida = proc.stdout.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as exc:
        codigo = 124
        crudo = exc.stdout or b""
        salida = crudo.decode("utf-8", "replace") + f"\n[railspec] tiempo agotado tras {timeout_s} s\n"
    duracion_ms = int((time.monotonic() - inicio) * 1000)

    log = worktree / DIR_LOGS / f"{nombre_log}.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text(salida, encoding="utf-8")

    if nivel == NivelCodigo.restringido:
        enviada, recortada = "", bool(salida)
    else:
        limpia = secretos.redactar(salida)
        recortada = len(limpia) > SALIDA_MAX_CARACTERES
        enviada = limpia[-SALIDA_MAX_CARACTERES:]
    resultado = ResultadoValidacion(
        comando=comando,
        codigo_salida=codigo,
        duracion_ms=duracion_ms,
        salida=enviada,
        recortada=recortada,
    )
    return Ejecucion(resultado=resultado, log=log, salida_local=salida[-SALIDA_MAX_CARACTERES:])
