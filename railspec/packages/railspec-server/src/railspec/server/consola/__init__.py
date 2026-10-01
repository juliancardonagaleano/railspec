"""Consola web de Railspec (fase 7): API en ``/consola/api`` y SPA en ``/consola/``.

``montar_consola`` añade ambas a la app ASGI del servidor. La SPA
(``railspec-console``) es estática: si ``RAILSPEC_CONSOLA_DIR`` apunta a su
``dist``, el servidor la sirve con vuelta a ``index.html`` para las rutas del
cliente; si no, solo se monta la API.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import ConfigConsola, ConfigGithubApp, validar_secreto
from .revocados import RevocadosMongo
from .seguridad import LimitadorVentana, SeguridadConsola


def montar_consola(app: Any, ctx: Any) -> None:
    """``ctx``: un ``ContextoConsola`` (import perezoso: FastAPI es del extra ``motor``)."""

    from fastapi.responses import FileResponse, RedirectResponse

    from .api import RUTA_API, RUTA_SPA, crear_api

    # Falla al arrancar, no al primer inicio de sesión: con https o GitHub App una clave
    # efímera haría que la réplica B rechace la cookie de la A.
    if ctx.config.exige_secreto and ctx.firmador.efimero:
        validar_secreto(None, exigido=True)
    # Cerrar sesión debe valer en todas las réplicas: los revocados viven en la base.
    if ctx.firmador.revocados is None:
        ctx.firmador.revocados = RevocadosMongo(ctx.datos.db)
    app.mount(RUTA_API, crear_api(ctx))
    limite = ctx.config.limite_auth_minuto
    app.state.limitador_auth = LimitadorVentana(limite, 60.0) if limite > 0 else None
    app.add_middleware(
        SeguridadConsola,
        prefijo_spa=RUTA_SPA,
        prefijo_api=RUTA_API,
        hsts=ctx.config.cookie_segura,
        limitador=app.state.limitador_auth,
    )
    if ctx.config.carpeta_spa is None:
        return
    raiz = Path(ctx.config.carpeta_spa).resolve()
    indice = raiz / "index.html"
    if not indice.is_file():
        raise ValueError(f"RAILSPEC_CONSOLA_DIR={raiz} no contiene index.html (¿falta npm run build?)")

    @app.get(RUTA_SPA, include_in_schema=False)
    async def _raiz_spa():
        return RedirectResponse(RUTA_SPA + "/", status_code=308)

    @app.get(RUTA_SPA + "/{ruta:path}", include_in_schema=False)
    async def _spa(ruta: str):
        archivo = _archivo_de_la_spa(raiz, ruta)
        if archivo is not None:
            cache = "public, max-age=31536000, immutable" if ruta.startswith("assets/") else "no-cache"
            return FileResponse(archivo, headers={"Cache-Control": cache})
        return FileResponse(indice, headers={"Cache-Control": "no-cache"})


def _archivo_de_la_spa(raiz: Path, ruta: str) -> Path | None:
    """El archivo estático de ``ruta`` dentro de ``raiz``; None si no existe o sale de la carpeta.

    Una ruta con byte nulo (``/consola/%00``) o ilegible para el sistema de archivos no es un
    archivo: cae en ``index.html`` como cualquier ruta del cliente, no en un 500.
    """

    if not ruta:
        return None
    try:
        archivo = (raiz / ruta).resolve()
        if archivo.is_file() and archivo.is_relative_to(raiz):
            return archivo
    except (ValueError, OSError):
        pass
    return None


__all__ = ["ConfigConsola", "ConfigGithubApp", "montar_consola"]
