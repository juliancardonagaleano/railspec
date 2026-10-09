"""``GET /v1/repositorios/resolver?url=<remoto de git>``: a qué vínculo pertenece un clon.

``railspec instalar`` sin parámetros lee el ``origin`` del clon y pregunta aquí por el vínculo (organización,
workspace y slug), así que quien clona un repositorio ya vinculado no tiene que conocer esos tres valores.

- Exige el token de la persona (el mismo del arnés). Un actor de servicio (OIDC de Actions) no tiene rol por
  organización y recibe 403.
- Solo devuelve vínculos de workspaces donde la persona tiene algún rol (asignado a ella o a un equipo suyo
  de GitHub, como en el resto del servidor): no sirve para averiguar qué repositorios están vinculados en
  organizaciones ajenas. Sin coincidencias, 200 con la lista vacía.
- La URL se compara por ``owner/repo`` sin distinguir mayúsculas (GitHub no las distingue); solo se admite
  ``https://github.com/<owner>/<repo>`` (``.git`` o ``/`` finales opcionales), como en el vínculo.

Respuesta 200: ``{"coincidencias": [{"org", "workspace", "repositorio", "nivel_codigo",
"rama_por_defecto"}]}`` (puede haber varias: el mismo repositorio vinculado a más de un workspace).
Errores: ``{"codigo", "detalle"}`` con 401/503 de identidad, 403 ``solo-personas`` y 422 ``entrada-invalida``.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from railspec.contracts.comun import Actor, TipoActor

from .grafo import repositorio_de_url
from .identidad import ActorConEquipos, TokenInvalido
from .roles import rol_efectivo
from .superficies import _actor, estado_de_identidad

TOPE_URL = 512


def _clave(url: str) -> str | None:
    repositorio = repositorio_de_url(url.strip())
    return repositorio.lower() if repositorio else None


def coincidencias(datos: Any, actor: Actor, url: str) -> list[dict[str, str]] | None:
    """Los vínculos de ``url`` que la persona puede ver; ``None`` si la URL no es de GitHub."""

    clave = _clave(url)
    if clave is None:
        return None
    github_id = actor.github_id
    equipos = actor.equipos if isinstance(actor, ActorConEquipos) else frozenset()
    asignaciones = datos.asignaciones_de_sujeto(github_id, equipos)
    salida = []
    for v in datos.vinculos_de_orgs({a.org for a in asignaciones}):
        a = v.alcance
        if _clave(v.url) != clave:
            continue
        if rol_efectivo([x for x in asignaciones if x.org == a.org], a.workspace) is None:
            continue
        salida.append(
            {
                "org": a.org,
                "workspace": a.workspace,
                "repositorio": a.repositorio,
                "nivel_codigo": v.nivel_codigo.value,
                "rama_por_defecto": v.rama_por_defecto,
            }
        )
    return salida


def router_resolucion(identidad: Any, datos: Any) -> APIRouter:
    router = APIRouter(prefix="/v1/repositorios")

    def error(estado: int, codigo: str, detalle: str) -> JSONResponse:
        return JSONResponse({"codigo": codigo, "detalle": detalle}, status_code=estado)

    @router.get("/resolver")
    async def resolver(request: Request) -> JSONResponse:
        try:
            actor = await _actor(identidad, request.headers.get("authorization"), "arnes")
        except TokenInvalido as exc:
            return JSONResponse({"detalle": str(exc)}, status_code=estado_de_identidad(exc))
        if actor.tipo != TipoActor.humano or actor.github_id is None:
            return error(403, "solo-personas", "solo una persona autenticada puede resolver su repositorio")
        url = request.query_params.get("url", "")
        if not url or len(url) > TOPE_URL:
            return error(422, "entrada-invalida", "falta url (texto de hasta 512 caracteres)")
        lista = coincidencias(datos, actor, url)
        if lista is None:
            return error(422, "entrada-invalida", "url debe ser https://github.com/<owner>/<repo>")
        return JSONResponse({"coincidencias": lista}, headers={"Cache-Control": "no-store"})

    return router
