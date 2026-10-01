"""Vistas de solo lectura que arma la consola a partir del estado del motor.

Funciones puras sobre documentos ya leídos (sin acceso a datos): resumen de
órdenes para la línea de tiempo, trazabilidad CA-NN y estadísticas del
workspace. Ninguna devuelve texto de código: de una orden solo salen sus
metadatos (nunca instrucciones, plantilla ni contexto) y de un snapshot solo
rutas y símbolos (nombre, tipo, ruta), nunca diff ni fragmentos.
"""

from __future__ import annotations

from collections import Counter
from typing import Any

from railspec.contracts.comun import EstadoFase, Fase, GateFase, Severidad, Veredicto
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.snapshot import Snapshot


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
