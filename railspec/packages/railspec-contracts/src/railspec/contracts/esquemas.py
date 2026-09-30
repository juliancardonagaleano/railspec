"""Exportación de los JSON Schema versionados.

``python -m railspec.contracts.esquemas <dir>`` escribe un archivo por
mensaje de primer nivel más ``tools.json`` con el registro. La prueba de
deriva compara esta salida con ``railspec/schemas/v1`` en el repositorio.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from pydantic import TypeAdapter

from ._base import VERSION_MAYOR
from .chat import Conversacion, MensajeChat, RespuestaChat, VeredictoGateSalida
from .estado import EstadoLocal, EstadoUnidad
from .eventos import EventoSync
from .insumo import Insumo
from .orden import OrdenDeTrabajo
from .reporte import ReporteOrden
from .repositorio import (
    AsignacionRol,
    ModeloCatalogo,
    Organizacion,
    PerfilConfig,
    PresupuestoConfig,
    ProveedorContexto,
    RegistroAuditoria,
    TelemetriaNodo,
    VinculoRepositorio,
    Workspace,
)
from .snapshot import Snapshot
from .tools import manifiesto_tools

BASE_ID = f"https://railspec.dev/schemas/v{VERSION_MAYOR}"

MENSAJES: dict[str, Any] = {
    "orden-de-trabajo": OrdenDeTrabajo,
    "reporte-orden": ReporteOrden,
    "snapshot": Snapshot,
    "evento-sync": EventoSync,
    "estado-unidad": EstadoUnidad,
    "estado-local": EstadoLocal,
    "insumo": Insumo,
    "respuesta-chat": RespuestaChat,
    "veredicto-gate-salida": VeredictoGateSalida,
    "conversacion": Conversacion,
    "mensaje-chat": MensajeChat,
    "organizacion": Organizacion,
    "workspace": Workspace,
    "asignacion-rol": AsignacionRol,
    "vinculo-repositorio": VinculoRepositorio,
    "modelo-catalogo": ModeloCatalogo,
    "perfil": PerfilConfig,
    "presupuesto": PresupuestoConfig,
    "proveedor-contexto": ProveedorContexto,
    "telemetria-nodo": TelemetriaNodo,
    "registro-auditoria": RegistroAuditoria,
}


def esquema(nombre: str) -> dict[str, Any]:
    cuerpo = TypeAdapter(MENSAJES[nombre]).json_schema()
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"{BASE_ID}/{nombre}.schema.json",
        **cuerpo,
    }


def generar() -> dict[str, str]:
    """Nombre de archivo → contenido, con serialización estable."""

    salida = {
        f"{n}.schema.json": json.dumps(esquema(n), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
        for n in MENSAJES
    }
    salida["tools.json"] = json.dumps(manifiesto_tools(), indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    return salida


def escribir(destino: Path) -> list[Path]:
    destino.mkdir(parents=True, exist_ok=True)
    escritos = []
    for nombre, contenido in generar().items():
        ruta = destino / nombre
        ruta.write_text(contenido, encoding="utf-8")
        escritos.append(ruta)
    return escritos


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("uso: python -m railspec.contracts.esquemas <directorio>", file=sys.stderr)
        return 2
    for ruta in escribir(Path(args[0])):
        print(ruta)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
