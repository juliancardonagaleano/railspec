"""Vistas de solo lectura que arma la consola a partir del estado del motor.

Funciones puras sobre documentos ya leídos (sin acceso a datos): resumen de
órdenes para la línea de tiempo, trazabilidad CA-NN, estadísticas del
workspace y la forma con que el estado y las tools salen hacia el navegador.
Ninguna devuelve texto de código: de una orden solo salen sus metadatos
(nunca instrucciones, plantilla, contexto ni comando de validación), de un
hallazgo no salen evidencia ni propuesta (texto libre de los críticos) y de un
snapshot solo rutas y símbolos (nombre, tipo, ruta), nunca diff ni fragmentos.

Todo es lista blanca: un campo nuevo del contrato no sale por la consola hasta
que se lo agregue aquí. ``/v1`` y MCP no pasan por este módulo (el arnés sí
necesita la orden completa).
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable
from typing import Any

from railspec.contracts.comun import EstadoFase, Fase, GateFase, Severidad, Veredicto
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.snapshot import Snapshot
from railspec.contracts.tools import CodigoError, ErrorTool, ToolDef, resolver_tool


def resumen_reporte(reporte: dict[str, Any] | None, snapshot: Snapshot | None) -> dict[str, Any] | None:
    if reporte is None:
        return None
    return {
        "resultado": reporte.get("resultado"),
        "reportado_en": reporte.get("reportado_en"),
        "tareas_completadas": list(reporte.get("tareas_completadas") or []),
        "archivos": [{"ruta": a.ruta, "estado": a.estado.value} for a in snapshot.archivos]
        if snapshot
        else [],
    }


def resumen_orden(
    orden: dict[str, Any], reporte: dict[str, Any] | None = None, snapshot: Snapshot | None = None
) -> dict[str, Any]:
    alcance = orden.get("alcance") or {}
    resumen = {
        "id": orden["id"],
        "secuencia": orden["secuencia"],
        "tipo": orden["tipo"],
        "fase": orden["fase"],
        "repositorio": orden["repositorio"],
        "base_commit": orden["base_commit"],
        "emitida_en": orden["emitida_en"],
        "criterios": [{"id": c["id"], "texto": c["texto"]} for c in orden.get("criterios") or []],
        "alcance": {"permitidos": alcance.get("permitidos", []), "prohibidos": alcance.get("prohibidos", [])},
        "reporte": resumen_reporte(reporte, snapshot),
    }
    if "artefacto" in orden:
        resumen["artefacto"] = orden["artefacto"]
    if orden["tipo"] == "implementar":
        resumen["grupo"] = orden.get("grupo")
        resumen["tareas"] = [
            {"id": t["id"], "descripcion": t["descripcion"], "criterios": list(t.get("criterios") or [])}
            for t in orden.get("tareas") or []
        ]
    if orden["tipo"] == "refinar":
        resumen["hallazgos"] = [_hallazgo(h) for h in orden.get("hallazgos") or []]
    return resumen


def _hallazgo(h: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": h["id"],
        "gate": h["gate"],
        "severidad": h["severidad"],
        "titulo": h["titulo"],
        "criterio": h.get("criterio"),
        "refutado": bool(h.get("refutado")),
    }


# --- estado y tools hacia el navegador ----------------------------------------------------------------

#: Campos de ``EstadoUnidad`` que salen por la consola. Queda fuera ``comando_validacion``
#: (instrucción para el arnés, igual que en la orden) y todo campo que el contrato agregue después.
CAMPOS_ESTADO = (
    "version_contrato",
    "unidad",
    "version",
    "titulo",
    "pedido",
    "dueno",
    "carril",
    "arnes",
    "repositorios",
    "fase",
    "estado",
    "modo",
    "riesgo",
    "perfil",
    "modo_conversion",
    "governance_refs",
    "insumos",
    "gates",
    "modelo_ejecucion",
    "orden_vigente",
    "secuencia_ordenes",
    "checkpoint_pendiente",
    "resoluciones",
    "integracion",
    "depende_de",
    "presupuesto",
    "consumo",
    "creado_en",
    "actualizado_en",
    "actualizado_por",
)

#: Campos de ``ResultadoGate`` que salen por la consola; los hallazgos pasan por ``hallazgo_estado``.
CAMPOS_GATE = (
    "veredicto",
    "causa",
    "iteraciones",
    "gobernanza_consultada",
    "criticos",
    "refutador",
    "rehabilitado",
    "cerrado_en",
)

_CAMPOS_CITA = ("ruta", "linea_inicio", "linea_fin", "seccion")


def hallazgo_estado(h: dict[str, Any]) -> dict[str, Any]:
    """Hallazgo para la ficha del gate: ``_hallazgo`` más lente y cita (ruta, líneas, sección).

    Sin ``evidencia`` ni ``propuesta``: son texto libre de los críticos (hasta 4000 caracteres)
    y pueden citar código.
    """

    cita = h.get("cita") or {}
    return {
        **_hallazgo(h),
        "lente": h.get("lente"),
        "cita": {k: cita.get(k) for k in _CAMPOS_CITA if k in cita},
    }


def resultado_gate(r: dict[str, Any]) -> dict[str, Any]:
    salida = {k: r[k] for k in CAMPOS_GATE if k in r}
    salida["hallazgos"] = [hallazgo_estado(h) for h in r.get("hallazgos") or []]
    return salida


def vista_estado(estado: EstadoUnidad | dict[str, Any]) -> dict[str, Any]:
    """``EstadoUnidad`` (o su JSON) como lo recibe el navegador, con lista blanca."""

    doc = estado.model_dump(mode="json") if isinstance(estado, EstadoUnidad) else estado
    salida = {k: doc[k] for k in CAMPOS_ESTADO if k in doc}
    salida["gates"] = {g: resultado_gate(r) for g, r in (doc.get("gates") or {}).items()}
    return salida


def _envoltura(cuerpo: dict[str, Any], *campos: str) -> dict[str, Any]:
    return {k: cuerpo[k] for k in ("version_contrato", *campos) if k in cuerpo}


def _solo_estado(cuerpo: dict[str, Any]) -> dict[str, Any]:
    return {**_envoltura(cuerpo), "estado": vista_estado(cuerpo["estado"])}


def _inicio(cuerpo: dict[str, Any]) -> dict[str, Any]:
    return {**_envoltura(cuerpo, "version_contrato_negociada"), "estado": vista_estado(cuerpo["estado"])}


def _estado_de_unidad(cuerpo: dict[str, Any]) -> dict[str, Any]:
    """``unit.status``: la orden vigente sale como en el detalle de la unidad (solo metadatos)."""

    orden = cuerpo.get("orden_vigente")
    return {
        **_envoltura(cuerpo),
        "estado": vista_estado(cuerpo["estado"]),
        "orden_vigente": resumen_orden(orden) if orden else None,
    }


def _tal_cual(cuerpo: dict[str, Any]) -> dict[str, Any]:
    return cuerpo


#: Tools que la consola expone y cómo filtra su salida. Es la lista blanca de ``/consola/api/tools``:
#: una tool que no figura aquí no se anuncia ni se invoca por la consola (``unit.export`` devuelve spec,
#: plan y tasks; ``unit.import``, ``insumo.get`` y las del arnés tampoco las usa la SPA). Las que no
#: llevan código (listados, telemetría, grafo con nombres y rutas) salen tal cual, declarado a propósito.
_SALIDAS: dict[str, Callable[[dict[str, Any]], dict[str, Any]]] = {
    "unit.list": _tal_cual,
    "unit.status": _estado_de_unidad,
    "unit.start": _inicio,
    "unit.approve": _solo_estado,
    "unit.integrate": _solo_estado,
    "unit.set_mode": _solo_estado,
    "telemetry.query": _tal_cual,
    "graph.query": _tal_cual,
}

TOOLS_CONSOLA = frozenset(_SALIDAS)


def tool_de_consola(nombre: str) -> ToolDef | None:
    """La tool de ``nombre`` (canónico o alias MCP) si la consola la expone; si no, None."""

    tool = resolver_tool(nombre)
    return tool if tool is not None and tool.nombre in TOOLS_CONSOLA else None


def rechazo_tool(nombre: str) -> tuple[dict[str, Any], int]:
    """Cuerpo y estado HTTP para una tool que la consola no expone (mismos códigos que el registro)."""

    if resolver_tool(nombre) is None:
        codigo, estado, detalle = CodigoError.no_encontrado, 404, f"tool {nombre} no disponible"
    else:
        codigo, estado, detalle = CodigoError.fuera_de_alcance, 403, f"{nombre} no se expone por la consola"
    return ErrorTool(codigo=codigo, detalle=detalle[:2000]).model_dump(mode="json"), estado


def salida_tool(nombre: str, cuerpo: dict[str, Any]) -> dict[str, Any]:
    """Salida de una tool de la consola tal como sale al navegador.

    ``nombre`` es el canónico. Falla cerrado: una tool sin filtro declarado lanza ``KeyError``.
    """

    return _SALIDAS[nombre](cuerpo)


def trazabilidad(
    estado: EstadoUnidad,
    ordenes: list[dict[str, Any]],
    reportes: dict[str, dict[str, Any]],
    snapshots: dict[str, Snapshot],
) -> dict[str, Any]:
    """CA-NN → tareas → archivos → símbolos → hallazgos.

    Los criterios salen de las órdenes (la última redacción de cada uno gana);
    las tareas, de las órdenes de implementar; archivos y símbolos, del
    snapshot que reportó cada orden, así que la granularidad es el grupo de
    tareas de esa orden. Los hallazgos vienen de los gates del estado y de las
    órdenes de refinar.
    """

    textos: dict[str, str] = {}
    tareas_por_criterio: dict[str, list[dict[str, Any]]] = {}
    ordenes_por_criterio: dict[str, list[str]] = {}
    sin_criterio: list[dict[str, Any]] = []
    hallazgos: dict[str, dict[str, Any]] = {}
    for orden in ordenes:
        for c in orden.get("criterios") or []:
            textos[c["id"]] = c["texto"]
        for h in orden.get("hallazgos") or []:
            hallazgos[h["id"]] = _hallazgo(h)
        if orden["tipo"] != "implementar":
            continue
        reporte = reportes.get(str(orden["id"])) or {}
        completadas = set(reporte.get("tareas_completadas") or [])
        for t in orden.get("tareas") or []:
            tarea = {
                "id": t["id"],
                "descripcion": t["descripcion"],
                "grupo": orden.get("grupo"),
                "completada": t["id"] in completadas,
                "orden": str(orden["id"]),
            }
            if not t.get("criterios"):
                sin_criterio.append(tarea)
            for c in t.get("criterios") or []:
                tareas_por_criterio.setdefault(c, []).append(tarea)
                ordenes_por_criterio.setdefault(c, [])
                if str(orden["id"]) not in ordenes_por_criterio[c]:
                    ordenes_por_criterio[c].append(str(orden["id"]))
    for gate in estado.gates.values():
        for h in gate.hallazgos:
            hallazgos[h.id] = _hallazgo(h.model_dump(mode="json"))

    criterios = []
    for cid in sorted(set(textos) | set(tareas_por_criterio)):
        archivos: dict[tuple[str, str], dict[str, Any]] = {}
        simbolos: dict[tuple[str, str], dict[str, Any]] = {}
        for orden_id in ordenes_por_criterio.get(cid, []):
            snap_id = (reportes.get(orden_id) or {}).get("snapshot_id")
            snap = snapshots.get(str(snap_id)) if snap_id else None
            if snap is None:
                continue
            for a in snap.archivos:
                archivos[(snap.repositorio, a.ruta)] = {
                    "repositorio": snap.repositorio,
                    "ruta": a.ruta,
                    "estado": a.estado.value,
                }
            for s in snap.delta_indice.simbolos_upsert if snap.delta_indice else []:
                simbolos[(snap.repositorio, s.id)] = {
                    "repositorio": snap.repositorio,
                    "simbolo": s.id,
                    "nombre": s.nombre,
                    "tipo": s.tipo.value,
                    "ruta": s.ruta,
                }
        criterios.append(
            {
                "id": cid,
                "texto": textos.get(cid),
                "tareas": tareas_por_criterio.get(cid, []),
                "archivos": [archivos[k] for k in sorted(archivos)],
                "simbolos": [simbolos[k] for k in sorted(simbolos)],
                "hallazgos": sorted(
                    (h for h in hallazgos.values() if h["criterio"] == cid), key=lambda h: h["id"]
                ),
            }
        )
    return {"criterios": criterios, "sin_criterio": {"tareas": sin_criterio}}


def resumen_workspace(
    unidades: list[dict[str, Any]],
    gasto_mes_usd: float,
    presupuesto_mensual_usd: float | None,
    desde: str,
    grafo: list[dict[str, Any]],
) -> dict[str, Any]:
    por_fase = Counter(u.get("fase") for u in unidades)
    por_estado = Counter(u.get("estado") for u in unidades)
    gates: dict[str, dict[str, Any]] = {}
    for g in GateFase:
        resultados = [u["gates"][g.value] for u in unidades if (u.get("gates") or {}).get(g.value)]
        veredictos = Counter(r["veredicto"] for r in resultados)
        severidades = Counter(h["severidad"] for r in resultados for h in r.get("hallazgos") or [])
        gates[g.value] = {
            "total": len(resultados),
            **{v.value: veredictos.get(v.value, 0) for v in Veredicto},
            "iteraciones_media": round(sum(r.get("iteraciones", 0) for r in resultados) / len(resultados), 2)
            if resultados
            else 0.0,
            "hallazgos": {s.value: severidades.get(s.value, 0) for s in Severidad},
            "rehabilitados": sum(1 for r in resultados if r.get("rehabilitado")),
        }
    return {
        "unidades": {
            "total": len(unidades),
            "por_fase": {f.value: por_fase.get(f.value, 0) for f in Fase},
            "por_estado": {e.value: por_estado.get(e.value, 0) for e in EstadoFase},
            "integradas": sum(1 for u in unidades if u.get("integracion")),
            "checkpoints_pendientes": sum(1 for u in unidades if u.get("checkpoint_pendiente")),
        },
        "gates": gates,
        "gasto": {
            "mes_usd": round(gasto_mes_usd, 6),
            "presupuesto_mensual_usd": presupuesto_mensual_usd,
            "desde": desde,
        },
        "grafo": grafo,
    }
