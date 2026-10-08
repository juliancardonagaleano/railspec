"""Texto de los avisos y del informe. Solo identificadores, causas y números: nunca texto de código."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from .canales import Mensaje
from .modelo import EventoAviso, TipoAviso

CAUSAS = {
    "hallazgos-sin-resolver": "quedaron hallazgos graves sin resolver",
    "sin-gobernanza": "no se pudo consultar la gobernanza",
    "presupuesto-agotado": "se agotó el presupuesto",
    "error-proveedor": "falló el proveedor de modelos",
    "sin-convergencia": "el refinamiento no converge",
    "importado": "unidad importada: la rehabilita una persona",
}

_TIER = {"alto": "alto", "medio": "medio", "bajo": "bajo", None: "sin tier"}
MAX_FILAS_WORKSPACE = 15


def _enlace(base: str | None, *partes: str) -> str | None:
    return None if not base else "/".join([base.rstrip("/"), "consola", *partes])


def asunto(evento: EventoAviso) -> str:
    unidad = f" {evento.unidad}" if evento.unidad else ""
    if evento.tipo == TipoAviso.presupuesto_agotado:
        return f"[Railspec] Presupuesto agotado ({evento.org}){unidad}"
    return f"[Railspec] Gate escalado ({evento.org}){unidad}"


def mensaje_evento(e: EventoAviso, base: str | None) -> Mensaje:
    hechos: list[tuple[str, str]] = [("Organización", e.org)]
    if e.workspace:
        hechos.append(("Workspace", e.workspace))
    if e.unidad:
        hechos.append(("Unidad", e.unidad))
    if e.fase:
        hechos.append(("Gate", e.fase))
    if e.tipo == TipoAviso.presupuesto_agotado:
        titulo = "Presupuesto agotado: el gate no puede seguir"
        if e.tope:
            hechos.append(("Tope", e.tope))
        if e.consumo:
            hechos.append(("Consumo", e.consumo))
        lineas = ("Sube el tope en Configuración o retoma la unidad con un presupuesto mayor.",)
    else:
        titulo = "Un gate escaló y espera a una persona"
        hechos.append(("Causa", CAUSAS.get(e.causa or "", e.causa or "sin causa")))
        lineas = ("Abre la unidad para ver los hallazgos y decidir.",)
    hechos.append(("Cuándo (UTC)", f"{e.en:%Y-%m-%d %H:%M}"))
    enlace = _enlace(base, e.org, e.workspace, "unidades", e.unidad) if e.workspace and e.unidad else None
    return Mensaje(titulo, tuple(hechos), lineas, enlace)


def asunto_informe(informe: dict[str, Any]) -> str:
    periodo = f"{_dia(informe['desde'])} al {_dia(informe['hasta'])}"
    return f"[Railspec] Informe semanal de {informe['org']} ({periodo})"


def _dia(iso: str) -> str:
    return f"{datetime.fromisoformat(iso):%Y-%m-%d}"


def mensaje_informe(informe: dict[str, Any], base: str | None) -> Mensaje:
    t = informe["totales"]
    hechos = (
        ("Organización", informe["org"]),
        ("Periodo (UTC)", f"{_dia(informe['desde'])} al {_dia(informe['hasta'])}"),
        ("Unidades cerradas", str(t["unidades_cerradas"])),
        ("Gates escalados", str(t["gates_escalados"])),
        ("Gasto", f"{t['costo_usd']:.2f} USD en {t['llamadas']} llamadas"),
    )
    lineas: list[str] = []
    if informe["por_tier"]:
        lineas.append(
            "Gasto por tier: "
            + "; ".join(
                f"{_TIER.get(f['tier'], f['tier'])} {f['costo_usd']:.2f} USD" for f in informe["por_tier"]
            )
        )
    if informe["por_causa"]:
        lineas.append(
            "Escalados por causa: "
            + "; ".join(
                f"{CAUSAS.get(f['causa'], f['causa'])} ({f['gates_escalados']})" for f in informe["por_causa"]
            )
        )
    filas = informe["por_workspace"]
    for w in filas[:MAX_FILAS_WORKSPACE]:
        lineas.append(
            f"{w['workspace']}: {w['unidades_cerradas']} cerradas, {w['gates_escalados']} escalados, "
            f"{w['costo_usd']:.2f} USD"
        )
    if len(filas) > MAX_FILAS_WORKSPACE:
        lineas.append(f"… y {len(filas) - MAX_FILAS_WORKSPACE} workspaces más")
    return Mensaje(
        f"Informe semanal de {informe['org']}", hechos, tuple(lineas), _enlace(base, informe["org"])
    )


def mensaje_prueba(org: str, base: str | None) -> Mensaje:
    return Mensaje(
        "Aviso de prueba de Railspec",
        (("Organización", org),),
        ("Si lees esto, el canal funciona. No requiere ninguna acción.",),
        _enlace(base, org, "configuracion"),
    )
