"""Registro único de tools (R1): una definición, dos superficies (MCP y HTTP).

Cada llamada pasa por el mismo camino, venga del proxy local por MCP o de la
consola por HTTP: la tool existe y se expone en esa superficie, la entrada
valida contra su contrato, el actor sale del token (nunca de la entrada), su
rol en el workspace alcanza el ``rol_minimo`` y la salida valida contra el
contrato antes de salir. Los errores de negocio viajan como ``ErrorTool``.

Las tools que implementan otros hilos (``graph.query``, ``insumo.get``,
``code.read``, ``graph.index``) se enchufan como manejadores; mientras no
haya uno, la tool no se anuncia.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import Actor, AlcanceUnidad, AlcanceWorkspace, TipoActor
from railspec.contracts.repositorio import Rol
from railspec.contracts.tools import TOOLS, CodigoError, ErrorTool, Superficie, ToolDef, resolver_tool

from ..motor.motor import ErrorNegocio, Motor

log = logging.getLogger("railspec.api")

Manejador = Callable[[Any, Actor], Awaitable[BaseModel]]

_JERARQUIA = [Rol.lector, Rol.desarrollador, Rol.workspace_admin, Rol.org_admin]


class ErrorEntrada(Exception):
    """La entrada no cumple el contrato de la tool (MCP: isError; HTTP: 422)."""


class Autorizador(Protocol):
    def rol(self, actor: Actor, org: str, workspace: str | None) -> Rol | None: ...


class AutorizadorRoles:
    """Rol efectivo desde ``AsignacionRol``: el mayor entre el de la organización y el del workspace.

    ``abierto=True`` (solo desarrollo, sin Mongo) da ``desarrollador`` a toda
    persona autenticada sin asignaciones.
    """

    def __init__(self, almacen: Any, *, abierto: bool = False) -> None:
        self._almacen = almacen
        self._abierto = abierto

    def rol(self, actor: Actor, org: str, workspace: str | None) -> Rol | None:
        if actor.tipo == TipoActor.servicio:
            # Sin rol por organización: cualquier workflow de GitHub Actions del mundo puede
            # obtener un OIDC válido. Solo las tools que lo declaran (graph.index) lo admiten,
            # y ``Registro.invocar`` resuelve ese caso sin pasar por aquí.
            return None
        github_id = actor.github_id if actor.tipo == TipoActor.humano else actor.en_nombre_de
        if github_id is None:
            return None
        roles = [
            a.rol
            for a in self._almacen.asignaciones(org, github_id)
            if a.workspace is None or a.workspace == workspace
        ]
        if not roles:
            return Rol.desarrollador if self._abierto else None
        return max(roles, key=_JERARQUIA.index)


def _ambito(entrada: BaseModel) -> tuple[str, str | None]:
    for campo in ("unidad", "alcance"):
        valor = getattr(entrada, campo, None)
        if isinstance(valor, (AlcanceUnidad, AlcanceWorkspace)):
            return valor.org, valor.workspace
    org = getattr(entrada, "org", None)
    if isinstance(org, str):
        return org, getattr(entrada, "workspace", None)
    raise ErrorEntrada("la entrada no declara organización ni workspace")


@dataclass
class Resultado:
    ok: bool
    cuerpo: dict[str, Any]
    estado_http: int = 200


_HTTP = {
    CodigoError.version_contrato_no_soportada: 400,
    CodigoError.fuera_de_alcance: 403,
    CodigoError.no_encontrado: 404,
    CodigoError.conflicto_version: 409,
    CodigoError.orden_no_vigente: 409,
    CodigoError.base_commit_distinto: 409,
    CodigoError.secuencia_duplicada: 409,
    CodigoError.secuencia_con_hueco: 409,
    CodigoError.conversion_no_permitida: 409,
    CodigoError.checkpoint_ya_resuelto: 409,
    CodigoError.unidad_no_cerrada: 409,
    CodigoError.snapshot_invalido: 422,
    CodigoError.secretos_detectados: 422,
    CodigoError.perfil_insatisfacible: 422,
    CodigoError.presupuesto_agotado: 429,
}


def _error(codigo: CodigoError, detalle: str, version: int | None = None) -> Resultado:
    cuerpo = ErrorTool(codigo=codigo, detalle=detalle[:2000], version_estado=version).model_dump(mode="json")
    return Resultado(False, cuerpo, _HTTP.get(codigo, 400))


class Registro:
    def __init__(self, manejadores: dict[str, Manejador], autorizador: Autorizador) -> None:
        desconocidas = set(manejadores) - set(TOOLS)
        if desconocidas:
            raise ValueError(f"manejadores para tools que el contrato no define: {sorted(desconocidas)}")
        self._manejadores = manejadores
        self._autorizador = autorizador

    @classmethod
    def del_motor(
        cls, motor: Motor, autorizador: Autorizador, extra: dict[str, Manejador] | None = None
    ) -> Registro:
        manejadores: dict[str, Manejador] = {
            "unit.start": motor.start,
            "unit.advance": motor.advance,
            "unit.report": motor.report,
            "unit.approve": motor.approve,
            "unit.set_mode": motor.set_mode,
            "sync.pull": motor.sync_pull,
            "sync.push": motor.sync_push,
            "unit.integrate": motor.integrate,
            "unit.status": motor.status,
            "unit.list": motor.list,
            "telemetry.query": motor.telemetry,
        }
        manejadores.update(extra or {})
        return cls(manejadores, autorizador)

    def con_autorizador(self, autorizador: Autorizador) -> Registro:
        """Mismas tools con otro autorizador (la consola resuelve también roles de equipos)."""

        return Registro(self._manejadores, autorizador)

    def tools(self, superficie: Superficie) -> list[ToolDef]:
        return [t for n, t in sorted(TOOLS.items()) if superficie in t.superficies and n in self._manejadores]

    async def invocar(self, nombre: str, argumentos: Any, actor: Actor, superficie: Superficie) -> Resultado:
        # Acepta el nombre canónico (``unit.start``) y el alias MCP (``unit_start``).
        tool = resolver_tool(nombre)
        if tool is None or tool.nombre not in self._manejadores:
            return _error(CodigoError.no_encontrado, f"tool {nombre} no disponible")
        nombre = tool.nombre
        if superficie not in tool.superficies:
            return _error(CodigoError.fuera_de_alcance, f"{nombre} no se expone por {superficie.value}")
        tipos = getattr(tool, "tipos_actor", None)
        if tipos and actor.tipo not in tipos:
            return _error(CodigoError.fuera_de_alcance, f"{nombre} no admite actores {actor.tipo.value}")
        try:
            entrada = tool.entrada.model_validate(argumentos)
        except ValidationError as exc:
            return Resultado(False, {"detalle": "entrada fuera de contrato", "errores": _errores(exc)}, 422)
        try:
            org, workspace = _ambito(entrada)
        except ErrorEntrada as exc:
            return Resultado(False, {"detalle": str(exc)}, 422)
        if actor.tipo == TipoActor.servicio:
            # Identidad OIDC de CI: sin roles por organización. Solo una tool que declare
            # explícitamente ``tipos_actor == {servicio}`` (graph.index) la admite, con su rol
            # mínimo; su manejador comprueba el vínculo del repositorio y su rama por defecto.
            rol = tool.rol_minimo if tool.tipos_actor == {TipoActor.servicio} else None
        else:
            rol = self._autorizador.rol(actor, org, workspace)
        if rol is None or _JERARQUIA.index(rol) < _JERARQUIA.index(tool.rol_minimo):
            return _error(
                CodigoError.fuera_de_alcance,
                f"{nombre} exige rol {tool.rol_minimo.value} en {org}/{workspace}",
            )
        try:
            salida = await self._manejadores[nombre](entrada, actor)
        except ErrorNegocio as exc:
            return _error(exc.codigo, exc.detalle, exc.version_estado)
        except ConflictoVersion as exc:
            return _error(CodigoError.conflicto_version, str(exc), exc.actual or None)
        if not isinstance(salida, tool.salida):
            salida = tool.salida.model_validate(salida)
        return Resultado(True, salida.model_dump(mode="json"))


def _errores(exc: ValidationError) -> list[dict[str, Any]]:
    return [{"ruta": ".".join(str(p) for p in e["loc"]), "mensaje": e["msg"]} for e in exc.errors()][:50]
