"""Servidor MCP local por stdio: lo que el arnés ve de Railspec.

Los nombres usan guion bajo (``unit_start``) y no el punto del registro
(``unit.start``) porque varios arneses solo admiten ``[A-Za-z0-9_-]`` en
nombres de tool. El actor nunca viaja: el servidor lo deriva del token.

Checkpoints humanos: ``unit_checkpoint`` pregunta al humano con un formulario
(*elicitation*; desde la revisión 2026-07-28 viaja como ``InputRequiredResult``)
y resuelve el checkpoint con su respuesta, sin que el modelo del arnés la vea
ni la invente. Si el arnés no declara la capacidad, las reglas de conducta
le obligan a preguntar al humano y llamar ``unit_approve`` con su decisión.
"""

import functools
import logging
from collections.abc import Awaitable, Callable
from typing import Annotated, Any, Literal
from uuid import UUID

from mcp.server.mcpserver import AcceptedElicitation, Elicit, ElicitationResult, MCPServer, Resolve
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field, ValidationError
from railspec.contracts.comun import Fase, Modo, Perfil, Riesgo
from railspec.contracts.estado import Decision
from railspec.contracts.reporte import ResultadoOrden, UsoModeloArnes
from railspec.contracts.tools import nombre_mcp

from . import __version__
from .errores import ErrorRailspec
from .proxy import ProxyLocal

log = logging.getLogger(__name__)

INSTRUCCIONES = """\
Railspec pone rieles de Spec-Driven Development a este arnés. El servidor decide fase, gate y
presupuesto; tú ejecutas la orden de trabajo vigente y nada más.
Bucle: unit_start (una vez) → unit_advance → ejecutar la orden en el worktree de la unidad →
unit_report → unit_advance… hasta que unit_advance devuelva "cerrada".
No decidas fases ni te saltes gates; un checkpoint lo resuelve siempre un humano."""


class RespuestaCheckpoint(BaseModel):
    decision: Literal["aprobado", "cambios-solicitados", "rechazado"] = Field(
        description="aprobado, cambios-solicitados o rechazado"
    )
    comentario: str = Field(default="", description="Obligatorio si no apruebas.")


def _errores(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    """Convierte errores del proxy en resultados de error legibles para el arnés."""

    @functools.wraps(fn)
    async def envuelta(*args: Any, **kwargs: Any) -> Any:
        try:
            return await fn(*args, **kwargs)
        except ErrorRailspec as exc:
            raise ToolError(str(exc)) from exc
        except ValidationError as exc:
            raise ToolError(f"Entrada fuera de contrato: {exc}") from exc

    return envuelta


def crear_servidor(fabrica_proxy: Callable[[], ProxyLocal]) -> MCPServer:
    """``fabrica_proxy`` se llama perezosamente para que el servidor arranque aunque la
    configuración falte y el error llegue como resultado de la tool, no como caída."""

    servidor = MCPServer(
        name="railspec",
        title="Railspec",
        version=__version__,
        instructions=INSTRUCCIONES,
    )
    cache: dict[str, ProxyLocal] = {}

    def proxy() -> ProxyLocal:
        if "p" not in cache:
            cache["p"] = fabrica_proxy()
        return cache["p"]

    escritura = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=False)
    lectura = ToolAnnotations(readOnlyHint=True)

    @_errores
    async def unit_start(
        titulo: str,
        pedido: str,
        insumos: list[UUID] | None = None,
        perfil: Perfil | None = None,
        riesgo_sugerido: Riesgo | None = None,
        plan: str | None = None,
        modo: Modo | None = None,
    ) -> dict[str, Any]:
        """Arranca una unidad SDD: el servidor la registra y el proxy crea su worktree y rama.
        `insumos` son ids de insumos exportados desde el chat de la consola. `modo` solo si el humano
        lo pidió explícitamente (supervisado y desatendido exigen `plan`); si no, nace interactivo."""
        return await proxy().iniciar(titulo, pedido, insumos, perfil, riesgo_sugerido, plan, modo)

    @_errores
    async def unit_advance(unidad: str | None = None) -> dict[str, Any]:
        """Pide al servidor lo siguiente: una orden de trabajo, un checkpoint humano, una espera o el
        cierre. Primero envía lo que haya en cola sin conexión. Si la orden parte de otro commit base,
        rebasa el worktree (solo limpio); un conflicto o cambios sin commit se informan y no se toca nada."""
        respuesta = await proxy().avanzar(unidad)
        if respuesta.get("tipo") == "checkpoint":
            respuesta["como_resolver"] = (
                "Llama unit_checkpoint: pregunta al humano con un formulario y registra su decisión. Si tu "
                "arnés no admite formularios, pregúntale tú y llama unit_approve con su decisión exacta. "
                "Nunca decidas por él; también puede resolverlo desde la consola web."
            )
        return respuesta

    @_errores
    async def unit_report(
        unidad: str | None = None,
        resultado: ResultadoOrden = ResultadoOrden.completado,
        tareas_completadas: list[str] | None = None,
        motivo: str | None = None,
        modelo: str | None = None,
    ) -> dict[str, Any]:
        """Cierra la orden en curso. El proxy construye el snapshot según la política de código,
        corre la validación y lee el artefacto; tú solo dices el resultado. `motivo` es obligatorio
        si el resultado es fallido o bloqueado; `modelo` es el modelo que usaste (telemetría)."""
        uso = UsoModeloArnes(modelo=modelo) if modelo else None
        return await proxy().reportar(unidad, resultado, tareas_completadas, motivo, uso)

    def _pedir_decision(unidad: str | None = None) -> Elicit[RespuestaCheckpoint]:
        try:
            checkpoint = proxy().checkpoint_pendiente(unidad)
        except ErrorRailspec as exc:
            raise ToolError(str(exc)) from exc
        return Elicit(
            f"Railspec · {checkpoint.tipo.value} ({checkpoint.fase.value}): {checkpoint.pregunta}",
            RespuestaCheckpoint,
        )

    @_errores
    async def unit_checkpoint(
        respuesta: Annotated[ElicitationResult[RespuestaCheckpoint], Resolve(_pedir_decision)],
        unidad: str | None = None,
    ) -> dict[str, Any]:
        """Pregunta al humano, con un formulario del arnés, la decisión del checkpoint pendiente y la
        registra. El modelo no decide: la respuesta viene del humano."""
        if not isinstance(respuesta, AcceptedElicitation):
            return {"tipo": "checkpoint", "como_resolver": "El humano no respondió; sigue pendiente."}
        checkpoint = proxy().checkpoint_pendiente(unidad)
        datos = respuesta.data
        resultado = await proxy().aprobar(
            unidad, checkpoint.id, Decision(datos.decision), datos.comentario or None
        )
        return {**resultado, "tipo": "checkpoint-resuelto", "decision": datos.decision}

    @_errores
    async def unit_approve(
        checkpoint: UUID, decision: Decision, comentario: str | None = None, unidad: str | None = None
    ) -> dict[str, Any]:
        """Resuelve un checkpoint con la decisión que dio el humano (nunca la tuya). Gana la primera
        resolución por cualquier canal."""
        return await proxy().aprobar(unidad, checkpoint, decision, comentario)

    @_errores
    async def unit_set_mode(modo: Modo, motivo: str, unidad: str | None = None) -> dict[str, Any]:
        """Cambia el modo de la unidad. Solo cuando el humano lo pide explícitamente, con su motivo;
        el servidor lo admite tras research o tras el checkpoint del spec."""
        return await proxy().cambiar_modo(unidad, modo, motivo)

    @_errores
    async def unit_integrate(
        especificacion_viva: str,
        pr_url: str | None = None,
        unidad: str | None = None,
        commit_integrado: str | None = None,
    ) -> dict[str, Any]:
        """Registra que el resultado de una unidad cerrada se integró (spec viva y PR).
        `commit_integrado` es el sha del commit resultante en la rama destino; si falta, se usa la
        punta de la rama por defecto del remoto."""
        return await proxy().integrar(unidad, especificacion_viva, pr_url, commit_integrado)

    @_errores
    async def unit_status(unidad: str | None = None) -> dict[str, Any]:
        """Estado remoto de la unidad y resumen local (worktree, cola pendiente)."""
        return await proxy().estado(unidad)

    @_errores
    async def unit_list(
        fase: list[Fase] | None = None,
        integradas: bool | None = None,
        limite: int = 50,
        cursor: str | None = None,
    ) -> dict[str, Any]:
        """Unidades del workspace de este repositorio."""
        filtros: dict[str, Any] = {"repositorio": proxy().config.repo.repositorio, "limite": limite}
        if fase:
            filtros["fase"] = fase
        if integradas is not None:
            filtros["integradas"] = integradas
        if cursor:
            filtros["cursor"] = cursor
        return await proxy().listar(**filtros)

    @_errores
    async def graph_query(
        consulta: dict[str, Any],
        repositorios: list[str] | None = None,
        unidad: str | None = None,
        limite: int = 25,
    ) -> dict[str, Any]:
        """Consulta el grafo de código del workspace. `consulta` lleva `verbo`
        (resolve, search, traverse, related, impact, trace) y sus campos. Devuelve referencias,
        nunca código: resuélvelas leyendo el clon. Lee siempre `avisos`: dicen si una búsqueda
        semántica no es por similitud, si el repositorio no tiene índice canónico (un resultado
        vacío no prueba que el símbolo no exista) o si el índice está desactualizado o va por
        detrás de tu rama base. `frescura` da el commit y el instante del último índice."""
        return await proxy().consultar_grafo(consulta, repositorios, unidad, limite)

    @_errores
    async def code_search(
        texto: str,
        limite: int = 20,
        tipos: list[str] | None = None,
        ruta: str | None = None,
        unidad: str | None = None,
        modo: str = "auto",
    ) -> dict[str, Any]:
        """Busca texto en el código del clon (nombre, ruta y cuerpo de cada símbolo) con BM25,
        sin red: complementa a `graph_query`, que responde por nombre. Devuelve símbolos
        con su ruta, líneas y un fragmento con los términos marcados entre «»; para el código
        completo, lee el archivo. `tipos` filtra por tipo de símbolo y `ruta` por prefijo de ruta.
        `modo`: `auto` (palabras y, si el equipo tiene el modelo local de embeddings y los vectores
        calculados, también similitud), `texto`, `semantico` o `hibrido`; la respuesta dice el `modo`
        usado. Lee `avisos`: dicen si no hay índice (llama `code_index`), si va por detrás de tu rama
        o si faltan vectores."""
        return await proxy().buscar_codigo(texto, limite, tipos, ruta, unidad, modo)

    @_errores
    async def code_index(unidad: str | None = None) -> dict[str, Any]:
        """Construye el índice de texto de `code_search` con el indexador local (una vez por clon o
        unidad; después se mantiene con cada `unit_report`). Todo queda en .railspec/ y no viaja.
        No calcula vectores (tardan minutos): si el equipo tiene el modelo de embeddings, el aviso
        de la respuesta dice cuántos faltan y la persona los calcula con `railspec indice --vectores`."""
        return await proxy().indexar_codigo(unidad)

    @_errores
    async def insumo_pull(insumo: UUID, unidad: str | None = None) -> dict[str, Any]:
        """Trae un insumo del chat de la consola y lo escribe como Markdown en .railspec/insumos/
        del worktree de la unidad (o de la raíz si no hay unidad)."""
        return await proxy().traer_insumo(insumo, unidad)

    @_errores
    async def railspec_sync(unidad: str | None = None) -> dict[str, Any]:
        """Sincroniza con el servidor: envía la cola sin conexión (reportes y avisos, commits
        empujados incluidos) y trae los eventos remoto→local."""
        return await proxy().sincronizar(unidad)

    # Las tools que envuelven una del contrato se publican con su alias MCP (contrato 1.3);
    # las que solo existen en el proxy llevan nombre propio.
    for fn, nombre, anotaciones in (
        (unit_start, nombre_mcp("unit.start"), escritura),
        (unit_advance, nombre_mcp("unit.advance"), escritura),
        (unit_report, nombre_mcp("unit.report"), escritura),
        (unit_checkpoint, "unit_checkpoint", escritura),
        (unit_approve, nombre_mcp("unit.approve"), escritura),
        (unit_set_mode, nombre_mcp("unit.set_mode"), escritura),
        (unit_integrate, nombre_mcp("unit.integrate"), escritura),
        (unit_status, nombre_mcp("unit.status"), lectura),
        (unit_list, nombre_mcp("unit.list"), lectura),
        (graph_query, nombre_mcp("graph.query"), lectura),
        (code_search, "code_search", lectura),
        (code_index, "code_index", escritura),
        (insumo_pull, "insumo_pull", escritura),
        (railspec_sync, "railspec_sync", escritura),
    ):
        servidor.add_tool(fn, name=nombre, annotations=anotaciones, structured_output=False)
    return servidor


def servir(fabrica_proxy: Callable[[], ProxyLocal]) -> None:
    crear_servidor(fabrica_proxy).run("stdio")
