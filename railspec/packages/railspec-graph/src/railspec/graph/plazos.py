"""Plazos configurables del grafo, leídos de variables de entorno."""

from __future__ import annotations

import math
import os
from datetime import timedelta

#: Variable que fija tras cuántos días se retira una superposición retenida que ningún índice cubrió
#: (``0`` = nunca caduca). La leen ``IndexadorCanonico`` (para borrarlas) y ``AlmacenGrafo`` (para avisar
#: de las que van por la mitad); una variable inválida impide arrancar.
VAR_RETENIDAS_DIAS = "RAILSPEC_GRAFO_RETENIDAS_DIAS"
RETENIDAS_DIAS = 30.0


def plazo(valor: float | None, variable: str, defecto: float, unidad: str) -> timedelta | None:
    """``valor`` explícito, si no la variable de entorno, si no el defecto; ``0`` = sin plazo."""

    if valor is None:
        crudo = (os.environ.get(variable) or "").strip()
        try:
            valor = float(crudo) if crudo else defecto
        except ValueError:
            raise ValueError(f"{variable} debe ser un número de {unidad}, no {crudo!r}") from None
    if not math.isfinite(valor) or valor < 0:
        raise ValueError(f"{variable} debe ser un número de {unidad} mayor o igual que 0, no {valor!r}")
    if valor == 0:
        return None
    try:
        return timedelta(**{unidad: valor})
    except OverflowError:
        raise ValueError(f"{variable} es demasiado grande: {valor!r} {unidad}") from None
