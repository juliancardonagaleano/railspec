"""Avisos por Teams y correo, e informe semanal, de una organización (solo ``org-admin``).

El URL del webhook de Teams es un secreto: se escribe, se cifra y no vuelve a salir (solo su host).
Ver ``railspec/docs/consola.md`` § Avisos e informes y ``railspec/docs/motor.md`` § Avisos.
"""

from __future__ import annotations

import asyncio
from typing import Any, Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import Field, SecretStr
from railspec.contracts.comun import Slug
from railspec.contracts.repositorio import EventoAuditoria

from ..avisos.modelo import MAX_DESTINATARIOS, EventosAviso, InformeConfig
from ..avisos.servicio import EntradaAvisos, ServicioAvisos
from .api import Entrada
from .contexto import ContextoConsola
from .rutas_config import _escritura

router = APIRouter()


def _servicio(ctx: ContextoConsola) -> ServicioAvisos:
    if ctx.avisos is None:
        raise HTTPException(409, "este servidor no tiene avisos")
    return ctx.avisos


@router.get("/orgs/{org}/avisos")
async def avisos(org: str, request: Request) -> dict[str, Any]:
    ctx, _ = await _escritura(request, org, None)
    return await asyncio.to_thread(_servicio(ctx).vista, org)


class AvisosEntrada(Entrada):
    activo: bool
    #: ``None`` = conservar el guardado. Nunca se devuelve, ni se registra.
    teams_url: SecretStr | None = Field(default=None, max_length=2048, repr=False)
    quitar_teams: bool = False
    destinatarios: list[str] = Field(default_factory=list, max_length=MAX_DESTINATARIOS)
    eventos: EventosAviso = Field(default_factory=EventosAviso)
    informe: InformeConfig = Field(default_factory=InformeConfig)
    version: int | None = None


@router.put("/orgs/{org}/avisos")
async def guardar_avisos(org: Slug, entrada: AvisosEntrada, request: Request) -> dict[str, Any]:
    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previa = ctx.datos.avisos_config(org)
    url = entrada.teams_url.get_secret_value() if entrada.teams_url else None
    c = await asyncio.to_thread(
        servicio.guardar,
        org,
        EntradaAvisos(
            activo=entrada.activo,
            teams_url=url,
            quitar_teams=entrada.quitar_teams,
            destinatarios=entrada.destinatarios,
            eventos=entrada.eventos,
            informe=entrada.informe,
        ),
        entrada.version,
        ctx.auditoria_entidad(actor, previa.auditoria if previa else None),
    )
    ctx.auditar(
        actor,
        org,
        None,
        EventoAuditoria.cambio_configuracion,
        entidad="avisos",
        accion="crear" if previa is None else "editar",
        activo=c.activo,
        teams_host=c.teams_host,
        destinatarios=len(c.destinatarios),
        informe=c.informe.activo,
        webhook_escrito=url is not None,
        webhook_quitado=entrada.quitar_teams,
    )
    return servicio.vista_config(c)


class PruebaEntrada(Entrada):
    que: Literal["aviso", "informe"] = "aviso"


@router.post("/orgs/{org}/avisos/prueba")
async def prueba(org: str, entrada: PruebaEntrada, request: Request) -> dict[str, Any]:
    ctx, _ = await _escritura(request, org, None)
    resultados = await asyncio.to_thread(_servicio(ctx).probar, org, entrada.que)
    return {
        "resultados": [
            {"canal": r.canal, "resultado": "enviado" if r.ok else "error", "codigo": r.codigo}
            for r in resultados
        ]
    }


@router.get("/orgs/{org}/informe-semanal")
async def informe_semanal(org: str, request: Request) -> dict[str, Any]:
    ctx, _ = await _escritura(request, org, None)
    return await asyncio.to_thread(_servicio(ctx).informe, org)
