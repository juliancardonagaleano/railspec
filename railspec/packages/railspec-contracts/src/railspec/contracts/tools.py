"""Registro único de tools (R1).

Cada tool se define una vez: nombre, esquemas de entrada y salida, efecto
(lectura o escritura), rol mínimo y superficies donde se expone. El servidor
genera desde aquí el servidor MCP y la API HTTP de la consola; el agente del
chat solo recibe tools de lectura, y ``code.read`` solo existe para él.

El actor nunca viaja en la entrada: el servidor lo deriva del token
autenticado (GitHub OAuth, u OIDC de GitHub Actions) y de la superficie.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import UUID4, AwareDatetime, BaseModel, Field, model_validator

from ._base import VERSION_CONTRATO, Contrato, Mensaje
from .comun import (
    CLASE_CODIGO_INTERNO,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Arnes,
    Commit,
    EstadoFase,
    Fase,
    Modo,
    Perfil,
    Riesgo,
    RutaRelativa,
    Sha256,
    Slug,
    UnidadId,
)
from .estado import Checkpoint, Decision, EstadoUnidad
from .insumo import Insumo
from .orden import OrdenDeTrabajo
from .referencias import RefArchivo, RefNodoGrafo, RefSimbolo
from .reporte import ReporteOrden
from .repositorio import CLAVES_TELEMETRIA, Rol
from .snapshot import Relacion, SimboloId, TipoSimbolo


class Efecto(StrEnum):
    lectura = "lectura"
    escritura = "escritura"


class Superficie(StrEnum):
    mcp = "mcp"  # arneses vía proxy local
    http = "http"  # API de la consola
    chat = "chat"  # agente del chat de contexto (solo lectura)


class CodigoError(StrEnum):
    version_contrato_no_soportada = "version-contrato-no-soportada"
    fuera_de_alcance = "fuera-de-alcance"
    no_encontrado = "no-encontrado"
    conflicto_version = "conflicto-version"
    orden_no_vigente = "orden-no-vigente"
    base_commit_distinto = "base-commit-distinto"
    secuencia_duplicada = "secuencia-duplicada"
    snapshot_invalido = "snapshot-invalido"
    secretos_detectados = "secretos-detectados"
    checkpoint_ya_resuelto = "checkpoint-ya-resuelto"
    perfil_insatisfacible = "perfil-insatisfacible"
    unidad_no_cerrada = "unidad-no-cerrada"
    presupuesto_agotado = "presupuesto-agotado"


class ErrorTool(Mensaje):
    """Error de negocio común a todas las tools (MCP: isError; HTTP: 4xx con este cuerpo)."""

    codigo: CodigoError
    detalle: str = Field(max_length=2000)
    version_estado: int | None = Field(default=None, ge=1)


# --- unit.start -----------------------------------------------------------------


class RepositorioInicio(Contrato):
    repositorio: Slug
    rama: str = Field(min_length=1, max_length=255)
    base_commit: Commit


class UnitStartEntrada(Mensaje):
    alcance: AlcanceWorkspace
    repositorios: list[RepositorioInicio] = Field(
        min_length=1, description="El primero es el primario donde se trabaja."
    )
    titulo: str = Field(min_length=1, max_length=200)
    pedido: str = Field(min_length=1, max_length=20_000)
    plan: Slug | None = None
    perfil: Perfil | None = None
    riesgo_sugerido: Riesgo | None = Field(default=None, description="El triaje del servidor decide.")
    arnes: Arnes | None = None
    insumos: list[UUID4] = Field(default_factory=list, description="Insumos del chat (R6).")
    version_contrato_cliente: str = Field(pattern=r"^[0-9]+\.[0-9]+$")


class UnitStartSalida(Mensaje):
    estado: EstadoUnidad
    version_contrato_negociada: str = Field(pattern=r"^[0-9]+\.[0-9]+$")


# --- unit.advance -----------------------------------------------------------------


class UnitAdvanceEntrada(Mensaje):
    unidad: AlcanceUnidad
    version_vista: int = Field(ge=1, description="Versión de estado que conoce el cliente.")


class AvanceOrden(Contrato):
    tipo: Literal["orden"] = "orden"
    orden: OrdenDeTrabajo


class AvanceCheckpoint(Contrato):
    tipo: Literal["checkpoint"] = "checkpoint"
    checkpoint: Checkpoint


class AvanceEspera(Contrato):
    """El servidor trabaja (gate, redacción delegada); con Tasks, ``tarea`` la sigue."""

    tipo: Literal["en-espera"] = "en-espera"
    motivo: str = Field(min_length=1, max_length=300)
    reintentar_en_s: int = Field(ge=1, le=3600)
    tarea: str | None = Field(default=None, max_length=200, description="Id de Tasks MCP.")


class AvanceCerrada(Contrato):
    tipo: Literal["cerrada"] = "cerrada"


Avance = Annotated[
    Union[AvanceOrden, AvanceCheckpoint, AvanceEspera, AvanceCerrada],
    Field(discriminator="tipo"),
]


class UnitAdvanceSalida(Mensaje):
    version_estado: int = Field(ge=1)
    avance: Avance


# --- unit.report ---------------------------------------------------------------------


class UnitReportSalida(Mensaje):
    aceptado: Literal[True] = True
    version_estado: int = Field(ge=1)
    siguiente: Literal["advance"] = Field(
        default="advance", description="El cliente siempre vuelve a llamar unit.advance."
    )


# --- unit.approve / unit.integrate (R7) --------------------------------------------------


class UnitApproveEntrada(Mensaje):
    unidad: AlcanceUnidad
    checkpoint: UUID4
    decision: Decision
    comentario: str | None = Field(default=None, max_length=4000)

    @model_validator(mode="after")
    def _comentario(self) -> UnitApproveEntrada:
        if self.decision != Decision.aprobado and not self.comentario:
            raise ValueError(f"una decisión {self.decision.value} necesita comentario")
        return self


class UnitIntegrateEntrada(Mensaje):
    unidad: AlcanceUnidad
    especificacion_viva: str = Field(min_length=1, max_length=512)
    pr_url: str | None = Field(default=None, pattern=r"^https://", max_length=512)


class EstadoSalida(Mensaje):
    estado: EstadoUnidad


# --- unit.status / unit.list ------------------------------------------------------------


class UnitStatusEntrada(Mensaje):
    unidad: AlcanceUnidad


class UnitStatusSalida(Mensaje):
    estado: EstadoUnidad
    orden_vigente: OrdenDeTrabajo | None = None


class UnitListEntrada(Mensaje):
    alcance: AlcanceWorkspace
    repositorio: Slug | None = None
    fase: list[Fase] = Field(default_factory=list)
    estado: list[EstadoFase] = Field(default_factory=list)
    integradas: bool | None = None
    cursor: str | None = Field(default=None, max_length=200)
    limite: int = Field(default=50, ge=1, le=200)


class ResumenUnidad(Contrato):
    unidad: UnidadId
    titulo: str
    fase: Fase
    estado: EstadoFase
    modo: Modo
    riesgo: Riesgo
    repositorio_primario: Slug
    dueno_login: str | None = None
    integrada: bool
    actualizado_en: AwareDatetime


class UnitListSalida(Mensaje):
    unidades: list[ResumenUnidad]
    cursor_siguiente: str | None = None


# --- graph.query ----------------------------------------------------------------------


class VerboGrafo(StrEnum):
    resolve = "resolve"
    search = "search"
    traverse = "traverse"
    related = "related"


class ConsultaResolve(Contrato):
    verbo: Literal["resolve"] = "resolve"
    nombre: str = Field(min_length=1, max_length=512)


class ConsultaSearch(Contrato):
    verbo: Literal["search"] = "search"
    texto: str = Field(min_length=1, max_length=1000)
    tipos: list[TipoSimbolo] = Field(default_factory=list)
    semantica: bool = Field(default=False, description="Similitud por vectores además de texto.")


class ConsultaTraverse(Contrato):
    verbo: Literal["traverse"] = "traverse"
    simbolo: SimboloId
    relaciones: list[Relacion] = Field(default_factory=list)
    direccion: Literal["upstream", "downstream"]
    profundidad: int = Field(default=2, ge=1, le=5)


class ConsultaRelated(Contrato):
    verbo: Literal["related"] = "related"
    simbolo: SimboloId


class GraphQueryEntrada(Mensaje):
    alcance: AlcanceWorkspace
    repositorios: list[Slug] = Field(
        default_factory=list, description="Vacío = primario más transversales vinculados."
    )
    unidad: UnidadId | None = Field(
        default=None, description="Incluye la superposición sin commit de esa unidad."
    )
    consulta: Annotated[
        Union[ConsultaResolve, ConsultaSearch, ConsultaTraverse, ConsultaRelated],
        Field(discriminator="verbo"),
    ]
    limite: int = Field(default=25, ge=1, le=200)


class ResultadoGrafo(Contrato):
    ref: Annotated[Union[RefSimbolo, RefNodoGrafo, RefArchivo], Field(discriminator="tipo")]
    puntuacion: float | None = None
    relacion: Relacion | None = None
    distancia: int | None = Field(default=None, ge=0)
    riesgo: Literal["bajo", "medio", "alto", "critico"] | None = None


class GraphQuerySalida(Mensaje):
    resultados: list[ResultadoGrafo]
    commits: dict[Slug, Commit] = Field(description="Commit del grafo consultado por repositorio.")
    truncado: bool = False


# --- code.read (solo chat) -----------------------------------------------------------


class CodeReadEntrada(Mensaje):
    alcance: AlcanceRepositorio
    commit: Commit | None = Field(default=None, description="None = commit canónico vigente.")
    ruta: RutaRelativa
    linea_inicio: int | None = Field(default=None, ge=1)
    linea_fin: int | None = Field(default=None, ge=1)
    simbolo: SimboloId | None = None


class FragmentoLeido(Contrato):
    ruta: RutaRelativa
    linea_inicio: int = Field(ge=1)
    linea_fin: int = Field(ge=1)
    sha256: Sha256
    texto: str = Field(
        max_length=40_000,
        json_schema_extra=CLASE_CODIGO_INTERNO,
        description="Leído al vuelo del clon canónico; nunca se persiste.",
    )


class CodeReadSalida(Mensaje):
    commit: Commit
    fragmentos: list[FragmentoLeido]


# --- insumo.get ----------------------------------------------------------------------


class InsumoGetEntrada(Mensaje):
    alcance: AlcanceWorkspace
    id: UUID4


class InsumoGetSalida(Mensaje):
    insumo: Insumo


# --- telemetry.query (R5) -------------------------------------------------------------


ClaveTelemetria = Literal[
    "workspace", "repositorio", "unidad", "nodo", "fase", "tier", "proveedor", "modelo", "veredicto"
]


class TelemetryQueryEntrada(Mensaje):
    org: Slug
    workspace: Slug | None = None
    desde: AwareDatetime
    hasta: AwareDatetime
    agrupar_por: list[ClaveTelemetria] = Field(default_factory=list)
    filtros: dict[ClaveTelemetria, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _rango(self) -> TelemetryQueryEntrada:
        if self.hasta <= self.desde:
            raise ValueError("hasta debe ser posterior a desde")
        return self


class FilaTelemetria(Contrato):
    claves: dict[ClaveTelemetria, str | None]
    llamadas: int = Field(ge=0)
    tokens_entrada: int = Field(ge=0)
    tokens_salida: int = Field(ge=0)
    tokens_cache_lectura: int = Field(ge=0)
    costo_usd: float = Field(ge=0)
    duracion_ms: int = Field(ge=0)


class TelemetryQuerySalida(Mensaje):
    filas: list[FilaTelemetria]


# --- Registro -------------------------------------------------------------------------


class ToolDef(BaseModel):
    nombre: str = Field(pattern=r"^[a-z]+\.[a-z_]+$")
    descripcion: str
    efecto: Efecto
    rol_minimo: Rol
    superficies: frozenset[Superficie]
    entrada: type[BaseModel]
    salida: type[BaseModel]

    @model_validator(mode="after")
    def _reglas(self) -> ToolDef:
        if Superficie.chat in self.superficies and self.efecto != Efecto.lectura:
            raise ValueError(f"{self.nombre}: el chat solo ve tools de lectura")
        if self.nombre == "code.read" and self.superficies != {Superficie.chat}:
            raise ValueError("code.read solo se expone al agente del chat")
        return self

    def campos_codigo_interno(self) -> list[str]:
        """Rutas JSON (con [] para listas) de la salida con clase codigo_interno."""

        return sorted(_rutas_marcadas(self.salida.model_json_schema(), ""))

    def manifiesto(self) -> dict[str, Any]:
        return {
            "name": self.nombre,
            "description": self.descripcion,
            "efecto": self.efecto.value,
            "rol_minimo": self.rol_minimo.value,
            "superficies": sorted(s.value for s in self.superficies),
            "inputSchema": self.entrada.model_json_schema(),
            "outputSchema": self.salida.model_json_schema(),
            "codigo_interno": self.campos_codigo_interno(),
        }


def _rutas_marcadas(esquema: dict[str, Any], prefijo: str) -> set[str]:
    defs = esquema.get("$defs", {})
    encontradas: set[str] = set()

    def visitar(nodo: Any, ruta: str, pila: tuple[str, ...]) -> None:
        if not isinstance(nodo, dict):
            return
        if nodo.get("x-railspec-clase") == "codigo_interno":
            encontradas.add(ruta)
        ref = nodo.get("$ref")
        if ref:
            nombre = ref.rsplit("/", 1)[-1]
            if nombre not in pila:
                visitar(defs.get(nombre, {}), ruta, pila + (nombre,))
        for clave, sub in nodo.get("properties", {}).items():
            visitar(sub, f"{ruta}.{clave}" if ruta else clave, pila)
        if "items" in nodo:
            visitar(nodo["items"], f"{ruta}[]", pila)
        for combinador in ("anyOf", "oneOf", "allOf"):
            for sub in nodo.get(combinador, []):
                visitar(sub, ruta, pila)

    visitar(esquema, prefijo, ())
    return encontradas


_M, _H, _C = Superficie.mcp, Superficie.http, Superficie.chat
_L, _E = Efecto.lectura, Efecto.escritura

TOOLS: dict[str, ToolDef] = {
    t.nombre: t
    for t in (
        ToolDef(
            nombre="unit.start",
            descripcion="Arranca una unidad SDD en un workspace; nace en modo interactivo.",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M, _H}),
            entrada=UnitStartEntrada,
            salida=UnitStartSalida,
        ),
        ToolDef(
            nombre="unit.advance",
            descripcion="Devuelve la orden vigente, un checkpoint, una espera o el cierre.",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M}),
            entrada=UnitAdvanceEntrada,
            salida=UnitAdvanceSalida,
        ),
        ToolDef(
            nombre="unit.report",
            descripcion="Cierra la orden vigente con snapshot, validación o artefacto.",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M}),
            entrada=ReporteOrden,
            salida=UnitReportSalida,
        ),
        ToolDef(
            nombre="unit.approve",
            descripcion="Resuelve un checkpoint humano; gana la primera resolución por cualquier canal.",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M, _H}),
            entrada=UnitApproveEntrada,
            salida=EstadoSalida,
        ),
        ToolDef(
            nombre="unit.integrate",
            descripcion="Registra que el resultado de una unidad cerrada se integró (spec viva, PR).",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M, _H}),
            entrada=UnitIntegrateEntrada,
            salida=EstadoSalida,
        ),
        ToolDef(
            nombre="unit.status",
            descripcion="Estado de una unidad y su orden vigente.",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H, _C}),
            entrada=UnitStatusEntrada,
            salida=UnitStatusSalida,
        ),
        ToolDef(
            nombre="unit.list",
            descripcion="Lista unidades de un workspace con filtros.",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H, _C}),
            entrada=UnitListEntrada,
            salida=UnitListSalida,
        ),
        ToolDef(
            nombre="graph.query",
            descripcion="Consulta el grafo de código (resolve, search, traverse, related).",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H, _C}),
            entrada=GraphQueryEntrada,
            salida=GraphQuerySalida,
        ),
        ToolDef(
            nombre="code.read",
            descripcion="Lee fragmentos del clon canónico al vuelo, sin persistirlos. Solo chat.",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_C}),
            entrada=CodeReadEntrada,
            salida=CodeReadSalida,
        ),
        ToolDef(
            nombre="insumo.get",
            descripcion="Devuelve un insumo exportado del chat (railspec insumo pull).",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H, _C}),
            entrada=InsumoGetEntrada,
            salida=InsumoGetSalida,
        ),
        ToolDef(
            nombre="telemetry.query",
            descripcion="Agrega la telemetría por nodo por las claves pedidas.",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_H, _C}),
            entrada=TelemetryQueryEntrada,
            salida=TelemetryQuerySalida,
        ),
    )
}

assert set(ClaveTelemetria.__args__) == set(CLAVES_TELEMETRIA)  # type: ignore[attr-defined]


def tools_para(superficie: Superficie) -> list[ToolDef]:
    return [t for t in TOOLS.values() if superficie in t.superficies]


def manifiesto_tools() -> dict[str, Any]:
    return {
        "version_contrato": VERSION_CONTRATO,
        "tools": [TOOLS[n].manifiesto() for n in sorted(TOOLS)],
        "error": ErrorTool.model_json_schema(),
    }
