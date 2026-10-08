"""Informe de la organización en un periodo: unidades cerradas, gates escalados y gasto por tier.

Reutiliza lo que ya está: los estados de las unidades (``listar_estados``), la telemetría de los nodos
(``consultar_telemetria``, la misma de las estadísticas de la consola) y el registro de escalados que
llevan los avisos (``avisos_eventos``). No lee auditoría, artefactos ni código: solo cuenta y suma.

- **Unidad cerrada** = ``fase done`` y ``estado completado``; el instante es ``actualizado_en`` del
  estado, que es el del cierre mientras nadie la toque después (integrarla sí lo mueve).
- **Gate escalado** = un escalado que los avisos registraron en el periodo, por causa. Los anteriores a
  la puesta en marcha de los avisos no figuran.
- **Gasto** = telemetría del periodo agrupada por workspace y tier (``tier`` nulo: llamadas sin tier,
  p. ej. las del chat).
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from typing import Any

from railspec.contracts.comun import AlcanceWorkspace, EstadoFase, Fase
from railspec.contracts.tools import TelemetryQueryEntrada, UnitListEntrada

#: Páginas de unidades que se recorren por workspace (200 por página): el tope evita un barrido sin fin.
MAX_PAGINAS = 100


def _cerradas(almacen: Any, org: str, workspace: str, desde: datetime, hasta: datetime) -> int:
    n, cursor = 0, None
    for _ in range(MAX_PAGINAS):
        estados, cursor = almacen.listar_estados(
            UnitListEntrada(
                alcance=AlcanceWorkspace(org=org, workspace=workspace),
                fase=[Fase.done],
                estado=[EstadoFase.completado],
                cursor=cursor,
                limite=200,
            )
        )
        n += sum(1 for e in estados if desde <= e.actualizado_en < hasta)
        if cursor is None:
            break
    return n


def construir_informe(almacen: Any, datos: Any, org: str, desde: datetime, hasta: datetime) -> dict[str, Any]:
    """El informe como diccionario JSON (lo que muestra la consola y de lo que se arma el mensaje)."""

    workspaces = [w.alcance.workspace for w in datos.workspaces(org)]
    filas = almacen.consultar_telemetria(
        TelemetryQueryEntrada(org=org, desde=desde, hasta=hasta, agrupar_por=["workspace", "tier"])
    ).filas
    costo_ws: dict[str, float] = defaultdict(float)
    por_tier: dict[str | None, list[float]] = defaultdict(lambda: [0.0, 0])
    for f in filas:
        ws = f.claves.get("workspace")
        if ws is not None:
            costo_ws[ws] += f.costo_usd
        t = por_tier[f.claves.get("tier")]
        t[0] += f.costo_usd
        t[1] += f.llamadas
    escalados_ws: dict[str, int] = defaultdict(int)
    por_causa: dict[str, int] = defaultdict(int)
    for e in datos.eventos_aviso(org, desde, hasta):
        if e.get("tipo") == "gate-escalado" or e.get("tipo") == "presupuesto-agotado":
            escalados_ws[e.get("workspace") or ""] += 1
            por_causa[e.get("causa") or "?"] += 1
    por_workspace = []
    for ws in sorted(set(workspaces) | set(costo_ws) | set(escalados_ws) - {""}):
        por_workspace.append(
            {
                "workspace": ws,
                "unidades_cerradas": _cerradas(almacen, org, ws, desde, hasta),
                "gates_escalados": escalados_ws.get(ws, 0),
                "costo_usd": round(costo_ws.get(ws, 0.0), 4),
            }
        )
    orden = {"alto": 0, "medio": 1, "bajo": 2, None: 3}
    return {
        "org": org,
        "desde": desde.isoformat(),
        "hasta": hasta.isoformat(),
        "totales": {
            "unidades_cerradas": sum(w["unidades_cerradas"] for w in por_workspace),
            "gates_escalados": sum(escalados_ws.values()),
            "costo_usd": round(sum(w["costo_usd"] for w in por_workspace), 4),
            "llamadas": int(sum(v[1] for v in por_tier.values())),
        },
        "por_tier": [
            {"tier": t, "costo_usd": round(v[0], 4), "llamadas": int(v[1])}
            for t, v in sorted(por_tier.items(), key=lambda kv: orden.get(kv[0], 9))
        ],
        "por_causa": [{"causa": c, "gates_escalados": n} for c, n in sorted(por_causa.items())],
        "por_workspace": por_workspace,
    }
