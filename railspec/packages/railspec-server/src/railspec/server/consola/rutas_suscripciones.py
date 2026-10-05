"""Suscripciones de modelos (contrato 1.6): registro, descubrimiento de modelos y elección de los disponibles.

Una suscripción es la conexión de una organización a Foundry o a Anthropic. Solo la crea, edita o borra un
``org-admin`` (o quien administra la plataforma, la misma regla que el resto de la configuración de la
organización); cualquier persona con rol en la organización puede leerlas, porque quien edita un perfil
tiene que ver qué suscripciones y modelos puede elegir. **Ninguna respuesta lleva la clave**: solo
``clave_configurada``. Ver ``railspec/docs/proveedores.md`` § Suscripciones.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import Field, SecretStr
from railspec.contracts.comun import Effort, Proveedor, Slug
from railspec.contracts.repositorio import (
    AutenticacionSuscripcion,
    EventoAuditoria,
    SuscripcionModelo,
)

from ..proveedores.cifrado import VARIABLE
from ..proveedores.suscripciones import (
    EntradaSuscripcion,
    ErrorSuscripcion,
    ServicioSuscripciones,
    hosting_de,
)
from .api import Entrada
from .contexto import ContextoConsola
from .rutas_config import _escritura, _lectura

router = APIRouter()


def _servicio(ctx: ContextoConsola) -> ServicioSuscripciones:
    if ctx.suscripciones is None:
        raise HTTPException(409, "este servidor no tiene suscripciones de modelos")
    return ctx.suscripciones


def _vista(ctx: ContextoConsola, s: SuscripcionModelo) -> dict[str, Any]:
    """La suscripción para la consola: sin clave, con lo derivado de cada modelo y sus perfiles."""

    d = s.model_dump(mode="json")
    hosting = hosting_de(s)
    for m, original in zip(d["modelos"], s.modelos, strict=True):
        m["clave"] = original.clave
        m["hosting"] = hosting
        # Que sirva a restringido/interno además depende de la zona del workspace: aquí solo si puede servir.
        m["restringible"] = hosting == "azure" and original.region not in (None, "global")
    d["perfiles"] = [
        {"nombre": p.nombre.value, "workspace": p.workspace}
        for p in ctx.datos.perfiles_con_suscripcion(s.org, s.id)
    ]
    return d


def _auditar(
    ctx: ContextoConsola, actor: Any, org: str, accion: str, s_id: str, **detalle: str | int | bool
) -> None:
    ctx.auditar(
        actor,
        org,
        None,
        EventoAuditoria.cambio_configuracion,
        entidad="suscripcion",
        accion=accion,
        id=s_id,
        **detalle,
    )


# --- lectura ------------------------------------------------------------------------------------


@router.get("/orgs/{org}/suscripciones")
async def suscripciones(org: str, request: Request) -> dict[str, Any]:
    ctx = await _lectura(request, org, None)
    servicio = _servicio(ctx)
    return {
        "cifrado": {"disponible": servicio.cifrado_disponible, "variable": VARIABLE},
        "suscripciones": [_vista(ctx, s) for s in servicio.listar(org)],
    }


@router.get("/orgs/{org}/suscripciones/{id_}")
async def suscripcion(org: str, id_: str, request: Request) -> dict[str, Any]:
    ctx = await _lectura(request, org, None)
    return _vista(ctx, _servicio(ctx).obtener(org, id_))


# --- escritura ----------------------------------------------------------------------------------


class SuscripcionEntrada(Entrada):
    proveedor: Proveedor
    nombre: str = Field(min_length=1, max_length=120)
    autenticacion: AutenticacionSuscripcion = AutenticacionSuscripcion.api_key
    endpoint: str | None = Field(default=None, max_length=512)
    proyecto: str | None = Field(default=None, max_length=512)
    region: str | None = Field(default=None, max_length=40)
    zona_datos: str | None = Field(default=None, max_length=40)
    habilitada: bool = True
    #: ``None`` o vacía = conservar la que hay. Nunca se devuelve, ni se registra.
    clave: SecretStr | None = Field(default=None, max_length=2000, repr=False)
    version: int | None = None


@router.put("/orgs/{org}/suscripciones/{id_}")
async def guardar_suscripcion(
    org: str, id_: Slug, entrada: SuscripcionEntrada, request: Request
) -> dict[str, Any]:
    """Crea (sin ``version``) o edita una suscripción. Cambiar el endpoint exige la clave otra vez."""

    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previo = ctx.datos.suscripcion(org, id_)
    clave = entrada.clave.get_secret_value() if entrada.clave else None
    s = servicio.guardar(
        org,
        id_,
        entrada.proveedor,
        EntradaSuscripcion(
            nombre=entrada.nombre,
            autenticacion=entrada.autenticacion,
            endpoint=entrada.endpoint,
            proyecto=entrada.proyecto,
            region=entrada.region,
            zona_datos=entrada.zona_datos,
            habilitada=entrada.habilitada,
            clave=clave,
        ),
        entrada.version,
        ctx.auditoria_entidad(actor, previo.auditoria if previo else None),
    )
    _auditar(
        ctx,
        actor,
        org,
        "crear" if previo is None else "editar",
        id_,
        proveedor=s.proveedor.value,
        autenticacion=s.autenticacion.value,
        endpoint=s.endpoint,
        region=s.region,
        zona_datos=s.zona_datos,
        habilitada=s.habilitada,
        clave_escrita=clave is not None,
    )
    return _vista(ctx, s)


@router.delete("/orgs/{org}/suscripciones/{id_}")
async def borrar_suscripcion(org: str, id_: str, request: Request) -> Response:
    ctx, actor = await _escritura(request, org, None)
    s = _servicio(ctx).borrar(org, id_)
    _auditar(ctx, actor, org, "borrar", id_, proveedor=s.proveedor.value)
    return Response(status_code=204)


# --- descubrimiento y elección de modelos -------------------------------------------------------


@router.post("/orgs/{org}/suscripciones/{id_}/descubrir")
async def descubrir(org: str, id_: str, request: Request) -> Any:
    """Lee los modelos del proveedor: 200 con la suscripción actualizada, 502 con el error si falla.

    En los dos casos queda registrado el resultado (``ultima_lectura``) y la elección anterior se conserva.
    """

    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previo = servicio.obtener(org, id_)
    r = await servicio.descubrir(org, id_, actor.login, ctx.auditoria_entidad(actor, previo.auditoria))
    lectura = r.lectura
    _auditar(
        ctx,
        actor,
        org,
        "descubrir",
        id_,
        resultado=lectura.resultado,
        modelos=lectura.modelos,
        error=lectura.error_codigo,
    )
    cuerpo = {
        "resultado": lectura.resultado,
        "modelos": lectura.modelos,
        "suscripcion": _vista(ctx, r.suscripcion),
    }
    if lectura.resultado == "error":
        cuerpo["detalle"] = lectura.error_detalle
        cuerpo["codigo"] = lectura.error_codigo
        return JSONResponse(cuerpo, status_code=502)
    return cuerpo


class SeleccionEntrada(Entrada):
    #: Despliegue (Foundry) o id del modelo (Anthropic) de cada modelo que queda disponible; el resto, no.
    seleccionados: list[str] = Field(max_length=500)
    version: int


@router.put("/orgs/{org}/suscripciones/{id_}/modelos")
async def seleccionar_modelos(
    org: str, id_: str, entrada: SeleccionEntrada, request: Request
) -> dict[str, Any]:
    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previo = servicio.obtener(org, id_)
    s = servicio.seleccionar(
        org, id_, entrada.version, entrada.seleccionados, ctx.auditoria_entidad(actor, previo.auditoria)
    )
    _auditar(ctx, actor, org, "seleccionar-modelos", id_, modelos=sum(m.seleccionado for m in s.modelos))
    return _vista(ctx, s)


class DeclaracionEntrada(Entrada):
    modelo: str = Field(min_length=1, max_length=120)
    despliegue: str = Field(min_length=1, max_length=120)
    sku: str = Field(min_length=1, max_length=60)
    efforts: list[Effort] | None = None
    structured_outputs: bool | None = None
    contexto: int | None = Field(default=None, ge=1)
    version: int


@router.post("/orgs/{org}/suscripciones/{id_}/modelos")
async def declarar_modelo(
    org: str, id_: str, entrada: DeclaracionEntrada, request: Request
) -> dict[str, Any]:
    """Declara a mano un despliegue de Foundry (o corrige uno descubierto) y lo deja disponible."""

    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previo = servicio.obtener(org, id_)
    s = servicio.declarar(
        org,
        id_,
        entrada.version,
        modelo=entrada.modelo,
        despliegue=entrada.despliegue,
        sku=entrada.sku,
        efforts=entrada.efforts,
        structured_outputs=entrada.structured_outputs,
        contexto=entrada.contexto,
        auditoria=ctx.auditoria_entidad(actor, previo.auditoria),
    )
    _auditar(ctx, actor, org, "declarar-modelo", id_, despliegue=entrada.despliegue, sku=entrada.sku)
    return _vista(ctx, s)


@router.delete("/orgs/{org}/suscripciones/{id_}/modelos/{clave:path}")
async def retirar_modelo(org: str, id_: str, clave: str, version: int, request: Request) -> dict[str, Any]:
    ctx, actor = await _escritura(request, org, None)
    servicio = _servicio(ctx)
    previo = servicio.obtener(org, id_)
    s = servicio.retirar(org, id_, version, clave, ctx.auditoria_entidad(actor, previo.auditoria))
    _auditar(ctx, actor, org, "retirar-modelo", id_, despliegue=clave)
    return _vista(ctx, s)


__all__ = ["ErrorSuscripcion", "router"]
