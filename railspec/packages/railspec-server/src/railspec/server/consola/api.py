"""API HTTP de la consola (``/consola/api``): sesión, identidad y tools.

La sub-aplicación reúne las rutas de administración, configuración,
estadísticas y exploración (``rutas_*``). El chat no cuelga de aquí: va por
``/v1/chat`` con el token ``rsc1`` (``railspec.server.chat.http``). Contrato
completo en ``railspec/docs/consola.md``.
"""

from __future__ import annotations

import logging
import secrets
from datetime import timedelta
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, RedirectResponse, Response
from pydantic import BaseModel, ConfigDict, Field, ValidationError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.tools import Superficie

from ..api.identidad import TokenInvalido
from ..proveedores.suscripciones import ErrorSuscripcion
from .contexto import CABECERA_CSRF, AutorizadorConsola, ContextoConsola
from .github import ErrorGithub
from .sesion import COOKIE, COOKIE_ESTADO
from .vistas import TOOLS_CONSOLA, rechazo_tool, salida_tool, tool_de_consola

log = logging.getLogger("railspec.consola")

RUTA_SPA = "/consola"
RUTA_API = "/consola/api"


class Entrada(BaseModel):
    model_config = ConfigDict(extra="forbid")


class LoginDesarrollo(Entrada):
    token: str = Field(min_length=1, max_length=500)


def _errores(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"ruta": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()][:50]


def _volver(valor: str | None) -> str:
    """Solo rutas internas de la SPA: evita redirecciones abiertas."""

    if not valor or not valor.startswith("/") or valor.startswith("//") or "\\" in valor:
        return "/"
    return valor


def crear_api(ctx: ContextoConsola) -> FastAPI:
    from . import rutas_admin, rutas_config, rutas_exploracion, rutas_suscripciones

    api = FastAPI(title="Railspec consola", docs_url=None, redoc_url=None, openapi_url=None)
    api.state.consola = ctx

    @api.exception_handler(HTTPException)
    async def _http(request: Request, exc: HTTPException) -> JSONResponse:
        return JSONResponse({"detalle": exc.detail}, status_code=exc.status_code)

    @api.exception_handler(ValidationError)
    async def _validacion(request: Request, exc: ValidationError) -> JSONResponse:
        return JSONResponse(
            {"detalle": "entrada fuera de contrato", "errores": _errores(exc)}, status_code=422
        )

    @api.exception_handler(ErrorSuscripcion)
    async def _suscripcion(request: Request, exc: ErrorSuscripcion) -> JSONResponse:
        return JSONResponse({"detalle": exc.detalle, "codigo": exc.codigo}, status_code=exc.estado)

    @api.exception_handler(ConflictoVersion)
    async def _conflicto(request: Request, exc: ConflictoVersion) -> JSONResponse:
        detalle = "otra persona cambió esto antes" if exc.esperada else "ya existe"
        return JSONResponse({"detalle": detalle, "version_actual": exc.actual}, status_code=409)

    from fastapi.exceptions import RequestValidationError

    @api.exception_handler(RequestValidationError)
    async def _peticion(request: Request, exc: RequestValidationError) -> JSONResponse:
        errores = [{"ruta": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()]
        return JSONResponse(
            {"detalle": "entrada fuera de contrato", "errores": errores[:50]}, status_code=422
        )

    # --- autenticación -----------------------------------------------------------------

    def _poner_sesion(respuesta: Response, login: str, github_id: int, equipos: frozenset[int]) -> None:
        vida = timedelta(hours=ctx.config.horas_sesion)
        token = ctx.firmador.emitir(login, github_id, equipos, vida, "sesion")
        respuesta.set_cookie(
            ctx.config.nombre_cookie(COOKIE),
            token,
            max_age=int(vida.total_seconds()),
            httponly=True,
            secure=ctx.config.cookie_segura,
            samesite="lax",
            path=RUTA_SPA,
        )

    def _borrar_cookie(respuesta: Response, base: str, path: str) -> None:
        # Un __Secure- solo se puede borrar con una respuesta que también lleve Secure.
        respuesta.delete_cookie(
            ctx.config.nombre_cookie(base),
            path=path,
            secure=ctx.config.cookie_segura,
            httponly=True,
            samesite="lax",
        )

    def _redireccion_oauth() -> str:
        return f"{ctx.config.url_publica}{RUTA_API}/auth/github/callback"

    @api.get("/auth/config")
    async def auth_config() -> dict[str, bool]:
        return {"github": ctx.config.github_app is not None, "desarrollo": bool(ctx.tokens_desarrollo)}

    @api.get("/auth/github/inicio")
    async def github_inicio(volver: str | None = None) -> Response:
        if ctx.config.github_app is None:
            raise HTTPException(404, "la GitHub App no está configurada")
        nonce = secrets.token_urlsafe(16)
        estado = ctx.firmador.firmar(
            "oauth",
            {
                "n": nonce,
                "v": _volver(volver),
                "exp": int(ctx.firmador.ahora().timestamp()) + 600,
            },
        )
        r = RedirectResponse(ctx.github.url_autorizar(_redireccion_oauth(), estado), status_code=302)
        r.set_cookie(
            ctx.config.nombre_cookie(COOKIE_ESTADO),
            nonce,
            max_age=600,
            httponly=True,
            secure=ctx.config.cookie_segura,
            samesite="lax",
            path=f"{RUTA_API}/auth",
        )
        return r

    @api.get("/auth/github/callback")
    async def github_callback(request: Request, code: str = "", state: str = "") -> Response:
        import asyncio

        try:
            estado = ctx.firmador.abrir(state, "oauth")
        except TokenInvalido as exc:
            raise HTTPException(400, f"estado de OAuth inválido: {exc}") from exc
        if estado.get("n") != request.cookies.get(ctx.config.nombre_cookie(COOKIE_ESTADO)):
            raise HTTPException(400, "el estado de OAuth no corresponde a este navegador")
        if not code:
            raise HTTPException(400, "GitHub no devolvió código")
        try:
            usuario = await asyncio.to_thread(ctx.github.usuario_desde_codigo, code, _redireccion_oauth())
        except ErrorGithub as exc:
            raise HTTPException(401, str(exc)) from exc
        destino = RUTA_SPA + _volver(estado.get("v"))
        r = RedirectResponse(destino, status_code=302)
        _poner_sesion(r, usuario.login, usuario.github_id, usuario.equipos)
        _borrar_cookie(r, COOKIE_ESTADO, f"{RUTA_API}/auth")
        log.info("sesión de consola para %s", usuario.login)
        return r

    @api.post("/auth/desarrollo")
    async def login_desarrollo(entrada: LoginDesarrollo) -> Response:
        identidad = ctx.tokens_desarrollo.get(entrada.token)
        if identidad is None:
            raise HTTPException(401, "token de desarrollo desconocido")
        r = Response(status_code=204)
        _poner_sesion(r, identidad[0], identidad[1], frozenset())
        return r

    @api.post("/auth/salir")
    async def salir(request: Request) -> Response:
        # Con la misma cabecera anti-CSRF que el resto de escrituras: un sitio ajeno no puede
        # cerrar la sesión de nadie.
        if request.headers.get(CABECERA_CSRF) != "1":
            raise HTTPException(403, f"falta la cabecera {CABECERA_CSRF}: 1")
        cookie = request.cookies.get(ctx.config.nombre_cookie(COOKIE))
        if cookie:
            try:
                # Revocación real: la cookie (copiada o no) y los tokens api de esta sesión dejan
                # de valer en todas las réplicas. Una cookie ya vencida o ajena solo se borra.
                ctx.firmador.revocar(ctx.firmador.sesion(cookie, "sesion"))
            except TokenInvalido:
                pass
        r = Response(status_code=204)
        _borrar_cookie(r, COOKIE, RUTA_SPA)
        return r

    @api.post("/auth/token")
    async def token_api(request: Request) -> dict[str, str]:
        # Solo con la cookie de sesión del navegador (y su cabecera anti-CSRF): un token api, uno
        # de GitHub o de desarrollo no sirven para acuñar otro. Así una fuga del token api no da
        # acceso indefinido, y el nuevo nunca vive más que la sesión que lo pide.
        sesion = await ctx.sesion(request, solo_cookie=True)
        vida = min(timedelta(minutes=ctx.config.minutos_token), sesion.expira - ctx.firmador.ahora())
        token = ctx.firmador.emitir(sesion.login, sesion.github_id, sesion.equipos, vida, "api", sesion.sid)
        return {"token": token, "expira_en": (ctx.firmador.ahora() + vida).isoformat()}

    # --- quién soy -------------------------------------------------------------------------

    @api.get("/yo")
    async def yo(request: Request) -> dict[str, Any]:
        sesion = await ctx.sesion(request)
        permisos = ctx.permisos(sesion)
        asignaciones = ctx.datos.asignaciones_de_sujeto(sesion.github_id, sesion.equipos)
        orgs = {a.org for a in asignaciones}
        entidades = {o.id: o for o in ctx.datos.organizaciones(None if permisos.plataforma else orgs)}
        salida = []
        for org in sorted(orgs | set(entidades)):
            rol_org = permisos.rol(org, None)
            nombres = {w.alcance.workspace: w.nombre for w in ctx.datos.workspaces(org)}
            visibles = {a.workspace for a in asignaciones if a.org == org and a.workspace}
            if rol_org is not None and rol_org.value == "org-admin":
                visibles |= set(nombres)
            workspaces = []
            for ws in sorted(visibles):
                rol = permisos.rol(org, ws)
                if rol is not None:
                    workspaces.append({"workspace": ws, "nombre": nombres.get(ws, ws), "rol": rol.value})
            o = entidades.get(org)
            salida.append(
                {
                    "id": org,
                    "nombre": o.nombre if o else org,
                    "rol": rol_org.value if rol_org and rol_org.value == "org-admin" else None,
                    "workspaces": workspaces,
                }
            )
        return {
            "login": sesion.login,
            "github_id": sesion.github_id,
            "plataforma_admin": permisos.plataforma,
            "organizaciones": salida,
        }

    # --- tools (registro único, superficie HTTP, canal consola) ------------------------------

    @api.get("/tools")
    async def tools(request: Request) -> dict[str, Any]:
        await ctx.sesion(request)
        # Solo las tools de la lista blanca de la consola (vistas.TOOLS_CONSOLA), no todo lo HTTP.
        habilitadas = [t for t in ctx.registro.tools(Superficie.http) if t.nombre in TOOLS_CONSOLA]
        return {"tools": [t.manifiesto() for t in habilitadas]}

    @api.post("/tools/{nombre}")
    async def invocar(nombre: str, request: Request) -> JSONResponse:
        sesion = await ctx.sesion(request)
        tool = tool_de_consola(nombre)
        if tool is None:
            cuerpo, estado = rechazo_tool(nombre)
            return JSONResponse(cuerpo, status_code=estado)
        try:
            argumentos = await request.json()
        except ValueError:
            return JSONResponse({"detalle": "cuerpo JSON inválido"}, status_code=422)
        registro = ctx.registro.con_autorizador(AutorizadorConsola(ctx.permisos(sesion)))
        r = await registro.invocar(tool.nombre, argumentos, sesion.actor(), Superficie.http)
        # Hacia el navegador la salida va filtrada: nunca la orden completa ni texto de código.
        cuerpo = salida_tool(tool.nombre, r.cuerpo) if r.ok else r.cuerpo
        return JSONResponse(cuerpo, status_code=r.estado_http)

    for modulo in (rutas_admin, rutas_config, rutas_exploracion, rutas_suscripciones):
        api.include_router(modulo.router)
    return api
