"""Topes del presupuesto: por unidad, por fase y mensual del workspace.

Los tres vienen de ``PresupuestoConfig`` (la edita la consola) y se comprueban
con lo ya consumido; al alcanzar uno, el gate escala con ``presupuesto-agotado``
y dice cuál (``por_unidad``, ``por_fase <fase>`` o ``mensual_usd <mes>``).

- ``por_unidad`` se copia a la unidad al arrancarla y se compara con su
  ``consumo``, que vive en el estado.
- ``por_fase`` y ``mensual_usd`` se leen vigentes en cada comprobación, así que
  un cambio en la consola rige desde la siguiente llamada. No hay otro registro
  por fase ni por mes que la telemetría: se suman sus filas (la de la unidad y
  la fase, la del workspace y el mes calendario UTC). El mes cuenta toda la
  telemetría del workspace, también la del chat.
- Una llamada que respondió la caché de nodos no gasta: su fila vale cero.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from railspec.contracts.comun import AlcanceWorkspace, Fase, GateFase, Presupuesto
from railspec.contracts.estado import Consumo, EstadoUnidad
from railspec.contracts.tools import TelemetryQueryEntrada

from ..proveedores.base import Uso

SIN_GASTO = Uso()


def exceso(consumo: Consumo, tope: Presupuesto) -> str | None:
    """El primer tope de ``tope`` que ``consumo`` alcanza, como ``tokens 1200/1000``."""

    if tope.tokens_max is not None and consumo.tokens >= tope.tokens_max:
        return f"tokens {consumo.tokens}/{tope.tokens_max}"
    if tope.costo_usd_max is not None and consumo.costo_usd >= tope.costo_usd_max:
        return f"costo {consumo.costo_usd:.2f}/{tope.costo_usd_max:.2f} USD"
    if tope.segundos_max is not None and consumo.segundos >= tope.segundos_max:
        return f"segundos {consumo.segundos}/{tope.segundos_max}"
    return None


def con_uso(consumo: Consumo, uso: Uso) -> Consumo:
    """``consumo`` más lo que costó ``uso``, con la misma aritmética que el estado de la unidad."""

    return Consumo(
        tokens=consumo.tokens + uso.tokens,
        segundos=consumo.segundos + uso.duracion_ms // 1000,
        costo_usd=round(consumo.costo_usd + uso.costo_usd, 6),
    )


def _de_telemetria(
    almacen: Any, ws: AlcanceWorkspace, desde: datetime, ahora: datetime, filtros: dict[str, str]
) -> Consumo:
    filas = almacen.consultar_telemetria(
        TelemetryQueryEntrada(
            org=ws.org,
            workspace=ws.workspace,
            desde=desde,
            # Un segundo de margen sobre el reloj de la réplica; ``max`` por si otra réplica
            # (con otro reloj) creó la unidad un instante después de ``ahora``.
            hasta=max(ahora, desde) + timedelta(seconds=1),
            filtros=filtros,
        )
    ).filas
    return Consumo(
        tokens=sum(f.tokens_entrada + f.tokens_salida for f in filas),
        segundos=sum(f.duracion_ms for f in filas) // 1000,
        costo_usd=round(sum(f.costo_usd for f in filas), 6),
    )


def agotado(
    almacen: Any,
    ahora: datetime,
    estado: EstadoUnidad,
    gate: GateFase,
    fase: Fase,
    gastado: Uso = SIN_GASTO,
) -> str | None:
    """Qué tope se agotó antes de otra llamada del gate ``gate`` (fase ``fase``), o ``None``.

    ``gastado`` es lo que ya costaron las llamadas del mismo panel, que aún no
    están en el estado ni en la telemetría. Se comprueban de menor a mayor costo
    de consulta: el de la unidad (en memoria), el de la fase y el del mes.
    """

    unidad = exceso(con_uso(estado.consumo, gastado), estado.presupuesto)
    if unidad:
        return f"por_unidad: {unidad}"
    ws = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
    config = almacen.presupuesto(ws)
    if config is None:
        return None
    tope_fase = config.por_fase.get(fase)
    if tope_fase is not None:
        consumo = con_uso(
            _de_telemetria(
                almacen,
                ws,
                estado.creado_en,
                ahora,
                {"unidad": estado.unidad.unidad, "fase": gate.value},
            ),
            gastado,
        )
        motivo = exceso(consumo, tope_fase)
        if motivo:
            return f"por_fase {fase.value}: {motivo}"
    if config.mensual_usd is not None:
        ahora_utc = ahora.astimezone(UTC)
        inicio = datetime(ahora_utc.year, ahora_utc.month, 1, tzinfo=UTC)
        gasto = round(_de_telemetria(almacen, ws, inicio, ahora, {}).costo_usd + gastado.costo_usd, 6)
        if gasto >= config.mensual_usd:
            return f"mensual_usd {inicio:%Y-%m}: costo {gasto:.2f}/{config.mensual_usd:.2f} USD"
    return None
