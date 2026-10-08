"""Archivos de configuración del repo (``contexto.yaml`` y ``.railspecignore``): leer, validar, proponer.

La consola no escribe en el repositorio: ``proponer`` abre un PR con la GitHub App como instalación y
nunca hace commit en la rama principal. Si la App no está configurada, no está instalada o no tiene
permisos de escritura, responde en ``modo: "manual"`` con el diff para aplicarlo a mano. Hace falta
``workspace-admin``: los patrones de ``.railspecignore`` nombran rutas que el repositorio prefiere no
exponer. Ver ``railspec/docs/consola.md``.
"""

from __future__ import annotations

import asyncio
import hashlib
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import Field
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.repositorio import EventoAuditoria, Rol

from ..api.grafo import repositorio_de_url
from .api import Entrada
from .archivos import ARCHIVOS, MAX_BYTES, ArchivoEditable, diff_unificado
from .contexto import ContextoConsola
from .repo_github import ArchivoCambiado, ArchivoRemoto, ErrorRepo, ModoManual

router = APIRouter()

SIN_CREDENCIALES = "el servidor no tiene las credenciales de la GitHub App como instalación"


class Validar(Entrada):
    contenido: str = Field(max_length=MAX_BYTES * 2)


class Proponer(Entrada):
    contenido: str = Field(max_length=MAX_BYTES * 2)
    #: Hash del archivo que la persona abrió (``sha`` de la lectura); ``None`` si no existía.
    sha_base: str | None = Field(default=None, max_length=64)
    motivo: str | None = Field(default=None, max_length=500)


def _normalizar(texto: str) -> str:
    """Finales de línea ``\\n`` y un salto final: lo que deja un editor y evita un diff por el último byte."""

    texto = texto.replace("\r\n", "\n").replace("\r", "\n")
    return texto if texto.endswith("\n") or not texto else texto + "\n"


async def _preparar(request: Request, org: str, ws: str, repo: str, archivo: str):
    ctx: ContextoConsola = request.app.state.consola
    sesion = await ctx.sesion(request)
    ctx.permisos(sesion).exigir(org, ws, Rol.workspace_admin)
    spec = ARCHIVOS.get(archivo)
    if spec is None:
        raise HTTPException(404, f"archivo desconocido: {archivo} (se edita {', '.join(ARCHIVOS)})")
    vinculo = ctx.datos.vinculo(AlcanceRepositorio(org=org, workspace=ws, repositorio=repo))
    if vinculo is None:
        raise HTTPException(404, f"{repo} no está vinculado a {org}/{ws}")
    nombre = repositorio_de_url(vinculo.url)
    if nombre is None:
        raise HTTPException(422, "la URL del vínculo no es un repositorio de GitHub")
    return ctx, sesion, spec, vinculo, nombre


async def _leer(ctx: ContextoConsola, nombre: str, spec: ArchivoEditable, rama: str):
    """``(archivo remoto | None, motivo manual | None)``: sin lectura, la edición queda en modo manual."""

    if ctx.repo_github is None:
        return None, SIN_CREDENCIALES
    try:
        return await asyncio.to_thread(ctx.repo_github.leer, nombre, spec.ruta, rama), None
    except ModoManual as exc:
        return None, str(exc)
    except ErrorRepo as exc:
        raise HTTPException(502, str(exc)) from exc


def _motivo_sin_pr(remoto: ArchivoRemoto | None, motivo: str | None) -> str | None:
    if motivo is not None:
        return motivo
    if remoto is not None and not remoto.puede_proponer:
        return "la instalación de la App no tiene contenido:escritura y pull requests:escritura"
    return None


@router.get("/orgs/{org}/workspaces/{ws}/repositorios/{repo}/archivos/{archivo}")
async def leer_archivo(org: str, ws: str, repo: str, archivo: str, request: Request) -> dict[str, Any]:
    ctx, _, spec, vinculo, nombre = await _preparar(request, org, ws, repo, archivo)
    remoto, motivo = await _leer(ctx, nombre, spec, vinculo.rama_por_defecto)
    motivo = _motivo_sin_pr(remoto, motivo)
    return {
        "archivo": spec.id,
        "ruta": spec.ruta,
        "repositorio": nombre,
        "rama": vinculo.rama_por_defecto,
        "existe": remoto.existe if remoto is not None else None,
        "contenido": remoto.contenido if remoto is not None else "",
        "sha": remoto.sha if remoto is not None else None,
        "plantilla": spec.plantilla,
        "modo": "manual" if motivo else "pr",
        "motivo_manual": motivo,
    }


@router.post("/orgs/{org}/workspaces/{ws}/repositorios/{repo}/archivos/{archivo}/validar")
async def validar_archivo(
    org: str, ws: str, repo: str, archivo: str, entrada: Validar, request: Request
) -> dict[str, Any]:
    _, _, spec, _, _ = await _preparar(request, org, ws, repo, archivo)
    return spec.validar(_normalizar(entrada.contenido), org, None).como_dict()


@router.post("/orgs/{org}/workspaces/{ws}/repositorios/{repo}/archivos/{archivo}/proponer")
async def proponer_archivo(
    org: str, ws: str, repo: str, archivo: str, entrada: Proponer, request: Request
) -> JSONResponse:
    ctx, sesion, spec, vinculo, nombre = await _preparar(request, org, ws, repo, archivo)
    contenido = _normalizar(entrada.contenido)
    validacion = spec.validar(contenido, org, None)
    if not validacion.ok:
        errores = [
            {"ruta": f"línea {h.linea}" if h.linea else spec.ruta, "mensaje": h.mensaje}
            for h in validacion.errores
        ]
        return JSONResponse(
            {"detalle": f"{spec.ruta} no es válido", "errores": errores[:50]}, status_code=422
        )

    remoto, motivo = await _leer(ctx, nombre, spec, vinculo.rama_por_defecto)
    if remoto is not None:
        if remoto.sha != entrada.sha_base:
            raise HTTPException(
                409, f"{spec.ruta} cambió en {vinculo.rama_por_defecto} desde que lo abriste; recárgalo"
            )
        if remoto.contenido == contenido:
            raise HTTPException(422, "no hay cambios respecto de la rama")
    antes = remoto.contenido if remoto is not None and remoto.existe else None
    diff = diff_unificado(antes, contenido, spec.ruta) if remoto is not None else None
    motivo = _motivo_sin_pr(remoto, motivo)

    actor = sesion.actor()
    detalle = {
        "archivo": spec.ruta,
        # Solo la huella: el contenido no entra en la auditoría (puede nombrar rutas que el repo no expone).
        "sha256": hashlib.sha256(contenido.encode()).hexdigest()[:16],
        "motivo": (entrada.motivo or "").strip() or None,
    }
    pr = None
    if motivo is None:
        assert ctx.repo_github is not None
        cuerpo = (
            f"Propuesto desde la consola de Railspec por {sesion.login} ({org}/{ws}).\n\n"
            f"Archivo: `{spec.ruta}`. Motivo: {detalle['motivo'] or 'sin motivo'}.\n\n"
            "La consola no fusiona: revisa el cambio y fusiónalo como cualquier otro."
        )
        try:
            pr = await asyncio.to_thread(
                ctx.repo_github.proponer,
                nombre,
                spec.ruta,
                vinculo.rama_por_defecto,
                contenido,
                entrada.sha_base,
                titulo=f"Railspec: actualizar {spec.ruta}",
                cuerpo=cuerpo,
                mensaje_commit=f"Railspec: actualizar {spec.ruta} desde la consola",
            )
        except ArchivoCambiado as exc:
            raise HTTPException(409, str(exc)) from exc
        except ModoManual as exc:
            motivo = str(exc)
        except ErrorRepo as exc:
            raise HTTPException(502, str(exc)) from exc

    if pr is not None:  # el modo manual no cambia nada: solo se audita lo que llegó al repositorio
        ctx.auditar(
            actor,
            org,
            ws,
            EventoAuditoria.cambio_configuracion,
            repositorio=repo,
            entidad="archivo-repositorio",
            accion="proponer",
            pr=pr.url,
            **detalle,
        )
    if pr is not None:
        return JSONResponse(
            {"modo": "pr", "pr_url": pr.url, "numero": pr.numero, "rama": pr.rama, "diff": diff}
        )
    return JSONResponse(
        {
            "modo": "manual",
            "motivo_manual": motivo,
            "diff": diff,
            "contenido": contenido,
            "ruta": spec.ruta,
        }
    )
