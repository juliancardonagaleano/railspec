"""Tareas periódicas del servidor, ligadas a la vida de la aplicación (``lifespan``).

Cada tarea es una función síncrona (corre en un hilo) que se repite cada ``intervalo_s``. Un fallo se
registra y la tarea sigue: nunca derriba el servidor ni a las demás. Con varias réplicas cada una
corre su copia, así que lo que se programe aquí tiene que ser idempotente.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class TareaFondo:
    nombre: str
    intervalo_s: float
    funcion: Callable[[], Any]


async def _repetir(tarea: TareaFondo) -> None:
    while True:
        try:
            await asyncio.to_thread(tarea.funcion)
        except Exception:
            log.exception(
                "la tarea periódica %s falló; se reintenta en %.0f s", tarea.nombre, tarea.intervalo_s
            )
        await asyncio.sleep(tarea.intervalo_s)


def iniciar(tareas: Sequence[TareaFondo]) -> list[asyncio.Task[None]]:
    """Arranca cada tarea (la primera corrida es inmediata) y devuelve sus ``asyncio.Task``."""

    return [asyncio.create_task(_repetir(t), name=f"railspec-fondo-{t.nombre}") for t in tareas]


async def detener(corriendo: Sequence[asyncio.Task[None]]) -> None:
    for t in corriendo:
        t.cancel()
    await asyncio.gather(*corriendo, return_exceptions=True)
