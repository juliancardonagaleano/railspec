"""Registro único de tools (R1).

Cada tool se define una vez: nombre, esquemas de entrada y salida, efecto
(lectura o escritura), rol mínimo y superficies donde se expone. El servidor
genera desde aquí el servidor MCP y la API HTTP de la consola; el agente del
chat solo recibe tools de lectura, y ``code.read`` solo existe para él.

El actor nunca viaja en la entrada: el servidor lo deriva del token
autenticado (GitHub OAuth, u OIDC de GitHub Actions) y de la superficie.

Nombres (desde 1.3): el nombre canónico lleva punto (``unit.start``) y es el
que usan la API HTTP, el registro y la documentación. Toda superficie MCP
(servidor y proxy) expone el alias ``nombre_mcp`` (``unit_start``), porque
varios arneses rechazan el punto; ``resolver_tool`` acepta las dos formas.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated, Any, Literal, Union

from pydantic import UUID4, AwareDatetime, BaseModel, Field, model_validator

from ._base import VERSION_CONTRATO, Contrato, Mensaje
from .comun import (
    CLASE_CODIGO_INTERNO,
    MODOS_CON_MANDATO,
    AlcanceRepositorio,
    AlcanceUnidad,
    AlcanceWorkspace,
    Arnes,
    Commit,
    CriterioId,
    EstadoFase,
    Fase,
    Modo,
    Perfil,
    Riesgo,
    RutaRelativa,
    Sha256,
    Slug,
    TipoActor,
    UnidadId,
    ids_unicos,
)
from .estado import Checkpoint, Decision, EstadoUnidad
from .eventos import CommitEmpujado, Direccion, EventoSync, OrdenReportada, SnapshotSubido
from .insumo import Insumo
from .orden import OrdenDeTrabajo
from .portabilidad import PaqueteUnidad
from .referencias import RefArchivo, RefCriterio, RefNodoGrafo, RefSimbolo
from .reporte import ReporteOrden
from .repositorio import CLAVES_TELEMETRIA, Rol
from .snapshot import (
    EMBEDDING_DIMENSIONES,
    DeltaIndice,
    Relacion,
    SimboloId,
    TipoSimbolo,
    validar_vector_b64,
)


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
    conversion_no_permitida = "conversion-no-permitida"
    unidad_no_cerrada = "unidad-no-cerrada"
    presupuesto_agotado = "presupuesto-agotado"
    secuencia_con_hueco = "secuencia-con-hueco"


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
    modo: Modo | None = Field(
        default=None,
        description=(
            "Desde 1.2: modo que el humano fija en la petición inicial. El servidor lo registra "
            "como primera conversión desde interactivo, con tras=None."
        ),
    )
    arnes: Arnes | None = None
    insumos: list[UUID4] = Field(default_factory=list, description="Insumos del chat (R6).")
    version_contrato_cliente: str = Field(pattern=r"^[0-9]+\.[0-9]+$")

    @model_validator(mode="after")
    def _mandato(self) -> UnitStartEntrada:
        if self.modo in MODOS_CON_MANDATO and self.plan is None:
            raise ValueError(f"modo {self.modo.value} exige un mandato (plan)")
        return self


class UnitStartSalida(Mensaje):
    estado: EstadoUnidad
    version_contrato_negociada: str = Field(pattern=r"^[0-9]+\.[0-9]+$")


# --- unit.import y unit.export (desde 1.4) ----------------------------------------------


class UnitImportEntrada(Mensaje):
    """Crea una unidad desde un paquete ``railspec.unidad/v1`` (ver ``portabilidad``).

    Solo humanos: la unidad nace con ese humano como dueño y, si estaba
    cerrada, él rehabilita el gate de código. Un agente recibe
    ``fuera-de-alcance``, igual que en ``unit.start``.
    """

    alcance: AlcanceWorkspace
    repositorios: list[RepositorioInicio] = Field(
        min_length=1, description="El primero es el primario donde se trabaja."
    )
    arnes: Arnes | None = None
    version_contrato_cliente: str = Field(pattern=r"^[0-9]+\.[0-9]+$")
    paquete: PaqueteUnidad


class UnitImportSalida(Mensaje):
    estado: EstadoUnidad
    ya_existia: bool = Field(description="True si el mismo origen ya se había importado.")
    version_contrato_negociada: str = Field(pattern=r"^[0-9]+\.[0-9]+$")


class UnitExportEntrada(Mensaje):
    unidad: AlcanceUnidad


class UnitExportSalida(Mensaje):
    paquete: PaqueteUnidad

    @model_validator(mode="after")
    def _origen(self) -> UnitExportSalida:
        if self.paquete.origen.tipo != "railspec":
            raise ValueError("un paquete exportado tiene origen railspec")
        return self


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
    commit_integrado: Commit | None = Field(
        default=None,
        description=(
            "Desde 1.4: sha del commit resultante en la rama destino. El servidor conserva la "
            "superposición de la unidad en el grafo hasta que el índice canónico alcance ese "
            "commit; sin él, la descarta al integrar."
        ),
    )


class UnitSetModeEntrada(Mensaje):
    """Desde 1.2: convierte el modo de una unidad; solo un humano.

    El servidor solo la admite tras la fase research o tras el checkpoint
    del spec, y la registra en ``modo_conversion``; en otro momento responde
    ``conversion-no-permitida``.
    """

    unidad: AlcanceUnidad
    modo: Modo
    motivo: str = Field(min_length=1, max_length=2000)
    version_vista: int = Field(ge=1)

    @model_validator(mode="after")
    def _mandato(self) -> UnitSetModeEntrada:
        if self.modo in MODOS_CON_MANDATO and self.unidad.plan is None:
            raise ValueError(f"modo {self.modo.value} exige un mandato (unidad.plan)")
        return self


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


# --- sync.pull y sync.push (desde 1.3) ------------------------------------------------

MAX_EVENTOS_SYNC = 500


class SyncPullEntrada(Mensaje):
    """Pide los eventos remoto→local de una unidad posteriores a ``desde``."""

    unidad: AlcanceUnidad
    desde: int = Field(ge=0, description="Última secuencia remoto→local ya aplicada.")
    limite: int = Field(default=100, ge=1, le=MAX_EVENTOS_SYNC)


class SyncPullSalida(Mensaje):
    eventos: list[EventoSync] = Field(max_length=MAX_EVENTOS_SYNC)
    ultima_secuencia: int = Field(ge=0, description="Última secuencia remoto→local emitida.")
    hay_mas: bool

    @model_validator(mode="after")
    def _reglas(self) -> SyncPullSalida:
        previa = None
        for evento in self.eventos:
            if evento.direccion != Direccion.remoto_a_local:
                raise ValueError("sync.pull solo devuelve eventos remoto→local")
            if previa is not None and evento.secuencia <= previa:
                raise ValueError("sync.pull devuelve secuencias crecientes")
            previa = evento.secuencia
        if previa is not None and previa > self.ultima_secuencia:
            raise ValueError("ultima_secuencia no puede ser menor que la del último evento")
        if self.hay_mas and not self.eventos:
            raise ValueError("hay_mas exige al menos un evento")
        return self


CargaLocal = Annotated[
    Union[SnapshotSubido, OrdenReportada, CommitEmpujado],
    Field(discriminator="tipo"),
]


class EventoSubida(Contrato):
    """Evento local→remoto tal como lo sube el proxy.

    Sin actor ni dirección: el servidor completa el ``EventoSync`` con el
    actor del token y la dirección local→remoto.
    """

    id: UUID4 = Field(description="Clave de idempotencia.")
    secuencia: int = Field(ge=1, description="Monótona por unidad en la dirección local→remoto.")
    emitido_en: AwareDatetime
    causado_por: UUID4 | None = None
    carga: CargaLocal


class SyncPushEntrada(Mensaje):
    """Sube la cola local→remoto de una unidad, en orden.

    Idempotente por ``id``: un evento ya confirmado se descarta sin error.
    ``snapshot.subido`` y ``orden.reportada`` solo avisan; los datos viajan en
    ``unit.report``. Un hueco respecto a la última secuencia confirmada se
    rechaza con ``secuencia-con-hueco``.
    """

    unidad: AlcanceUnidad
    eventos: list[EventoSubida] = Field(min_length=1, max_length=MAX_EVENTOS_SYNC)

    @model_validator(mode="after")
    def _reglas(self) -> SyncPushEntrada:
        ids_unicos([str(e.id) for e in self.eventos], "eventos")
        for previo, siguiente in zip(self.eventos, self.eventos[1:], strict=False):
            if siguiente.secuencia <= previo.secuencia:
                raise ValueError("sync.push exige secuencias crecientes")
        return self


class SyncPushSalida(Mensaje):
    confirmada_hasta: int = Field(ge=0, description="Última secuencia local→remoto confirmada.")
    duplicados: int = Field(default=0, ge=0, description="Eventos ya confirmados que se descartaron.")


# --- graph.query ----------------------------------------------------------------------


class VerboGrafo(StrEnum):
    resolve = "resolve"
    search = "search"
    traverse = "traverse"
    related = "related"
    impact = "impact"
    trace = "trace"


class ConsultaResolve(Contrato):
    verbo: Literal["resolve"] = "resolve"
    nombre: str = Field(min_length=1, max_length=512)


class ConsultaSearch(Contrato):
    verbo: Literal["search"] = "search"
    texto: str = Field(min_length=1, max_length=1000)
    tipos: list[TipoSimbolo] = Field(default_factory=list)
    semantica: bool = Field(default=False, description="Similitud por vectores además de texto.")
    vector_b64: str | None = Field(
        default=None,
        pattern=r"^[A-Za-z0-9+/]+={0,2}$",
        description=(
            "Desde 1.1: embedding del texto calculado por el cliente con el modelo local "
            "(int8, 768, base64). Sin él, el servidor codifica la consulta si tiene "
            "codificador o cae a búsqueda por texto."
        ),
    )
    modelo_embedding: Literal["nomic-embed-code"] | None = None

    @model_validator(mode="after")
    def _vector(self) -> ConsultaSearch:
        if self.vector_b64 is None:
            if self.modelo_embedding is not None:
                raise ValueError("modelo_embedding solo acompaña a vector_b64")
            return self
        if not self.semantica:
            raise ValueError("vector_b64 exige semantica=true")
        if self.modelo_embedding is None:
            raise ValueError("vector_b64 exige modelo_embedding")
        validar_vector_b64(self.vector_b64, EMBEDDING_DIMENSIONES)
        return self


class ConsultaTraverse(Contrato):
    verbo: Literal["traverse"] = "traverse"
    simbolo: SimboloId
    relaciones: list[Relacion] = Field(default_factory=list)
    direccion: Literal["upstream", "downstream"]
    profundidad: int = Field(default=2, ge=1, le=5)


class ConsultaRelated(Contrato):
    verbo: Literal["related"] = "related"
    simbolo: SimboloId


class ConsultaImpact(Contrato):
    """Desde 1.4: impacto de la superposición de una unidad (exige ``unidad``).

    Devuelve los símbolos que la superposición toca (``distancia`` 0) y los
    afectados aguas arriba (``distancia`` >= 1, con ``relacion``), todos con
    ``riesgo``.
    """

    verbo: Literal["impact"] = "impact"
    profundidad: int = Field(default=3, ge=1, le=5)


class ConsultaTrace(Contrato):
    """Desde 1.4: trazabilidad entre criterios ``CA-NN`` y símbolos.

    Con ``criterio`` (exige ``unidad``) devuelve ``RefSimbolo`` de los símbolos
    enlazados a ese criterio; con ``simbolo`` devuelve ``RefCriterio`` de las
    unidades y criterios que lo tocaron. Exactamente uno de los dos.
    """

    verbo: Literal["trace"] = "trace"
    criterio: CriterioId | None = None
    simbolo: SimboloId | None = None

    @model_validator(mode="after")
    def _uno(self) -> ConsultaTrace:
        if (self.criterio is None) == (self.simbolo is None):
            raise ValueError("trace exige exactamente uno de criterio o simbolo")
        return self


class GraphQueryEntrada(Mensaje):
    alcance: AlcanceWorkspace
    repositorios: list[Slug] = Field(
        default_factory=list, description="Vacío = primario más transversales vinculados."
    )
    unidad: UnidadId | None = Field(
        default=None, description="Incluye la superposición sin commit de esa unidad."
    )
    consulta: Annotated[
        Union[
            ConsultaResolve,
            ConsultaSearch,
            ConsultaTraverse,
            ConsultaRelated,
            ConsultaImpact,
            ConsultaTrace,
        ],
        Field(discriminator="verbo"),
    ]
    limite: int = Field(default=25, ge=1, le=200)

    @model_validator(mode="after")
    def _unidad(self) -> GraphQueryEntrada:
        if self.unidad is None:
            if isinstance(self.consulta, ConsultaImpact):
                raise ValueError("impact exige unidad")
            if isinstance(self.consulta, ConsultaTrace) and self.consulta.criterio is not None:
                raise ValueError("trace por criterio exige unidad")
        return self


class ResultadoGrafo(Contrato):
    ref: Annotated[Union[RefSimbolo, RefNodoGrafo, RefArchivo, RefCriterio], Field(discriminator="tipo")]
    puntuacion: float | None = None
    relacion: Relacion | None = None
    distancia: int | None = Field(default=None, ge=0)
    riesgo: Literal["bajo", "medio", "alto", "critico"] | None = None


class GraphQuerySalida(Mensaje):
    resultados: list[ResultadoGrafo]
    commits: dict[Slug, Commit] = Field(description="Commit del grafo consultado por repositorio.")
    truncado: bool = False


# --- graph.index (solo CI) -----------------------------------------------------------


class GraphIndexEntrada(Mensaje):
    """Desde 1.1: un job de CI sube el índice del canónico tras cada push.

    El índice y los embeddings se calculan en el runner con el mismo motor
    que el proxy local; el servidor nunca indexa ni calcula embeddings de
    código. Un índice grande viaja en lotes; el canónico solo avanza cuando
    llegan todos los lotes del commit.
    """

    alcance: AlcanceRepositorio
    rama: str = Field(min_length=1, max_length=255)
    commit: Commit
    commit_anterior: Commit | None = Field(
        default=None, description="Base del delta incremental; None = índice completo."
    )
    lote: int = Field(ge=1)
    lotes: int = Field(ge=1, le=10_000)
    delta: DeltaIndice

    @model_validator(mode="after")
    def _lotes(self) -> GraphIndexEntrada:
        if self.lote > self.lotes:
            raise ValueError("lote mayor que lotes")
        if self.commit_anterior == self.commit:
            raise ValueError("commit_anterior igual a commit")
        return self


class GraphIndexSalida(Mensaje):
    commit: Commit
    lotes_recibidos: int = Field(ge=0)
    aplicado: bool = Field(description="True cuando el canónico ya avanzó a este commit.")


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


def nombre_mcp(nombre: str) -> str:
    """Alias MCP determinista de un nombre canónico: el punto pasa a guion bajo."""

    return nombre.replace(".", "_")


class ToolDef(BaseModel):
    nombre: str = Field(pattern=r"^[a-z]+\.[a-z_]+$")
    descripcion: str
    efecto: Efecto
    rol_minimo: Rol
    superficies: frozenset[Superficie]
    #: Quién puede llamar la tool. Por defecto, personas y agentes: el actor de servicio (OIDC de
    #: GitHub Actions, de cualquier repositorio del mundo) solo entra donde la tool lo declara, y
    #: eso hoy es únicamente ``graph.index`` (ver ``_reglas``).
    tipos_actor: frozenset[TipoActor] = frozenset({TipoActor.humano, TipoActor.agente})
    entrada: type[BaseModel]
    salida: type[BaseModel]

    @model_validator(mode="after")
    def _reglas(self) -> ToolDef:
        if Superficie.chat in self.superficies and self.efecto != Efecto.lectura:
            raise ValueError(f"{self.nombre}: el chat solo ve tools de lectura")
        if self.nombre == "code.read" and self.superficies != {Superficie.chat}:
            raise ValueError("code.read solo se expone al agente del chat")
        if self.nombre == "graph.index" and (
            self.tipos_actor != {TipoActor.servicio} or self.superficies != {Superficie.http}
        ):
            raise ValueError("graph.index es solo HTTP y solo para identidades de servicio")
        if TipoActor.servicio in self.tipos_actor and self.nombre != "graph.index":
            raise ValueError(f"{self.nombre}: solo graph.index admite identidades de servicio")
        return self

    @property
    def nombre_mcp(self) -> str:
        """Alias que exponen las superficies MCP (desde 1.3)."""

        return nombre_mcp(self.nombre)

    def campos_codigo_interno(self) -> list[str]:
        """Rutas JSON (con [] para listas) de la salida con clase codigo_interno."""

        return sorted(_rutas_marcadas(self.salida.model_json_schema(), ""))

    def manifiesto(self) -> dict[str, Any]:
        return {
            "name": self.nombre,
            "mcp_name": self.nombre_mcp,
            "description": self.descripcion,
            "efecto": self.efecto.value,
            "rol_minimo": self.rol_minimo.value,
            "superficies": sorted(s.value for s in self.superficies),
            "tipos_actor": sorted(t.value for t in self.tipos_actor),
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
            nombre="unit.set_mode",
            descripcion="Convierte el modo de una unidad tras research o el checkpoint del spec.",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M, _H}),
            tipos_actor=frozenset({TipoActor.humano}),
            entrada=UnitSetModeEntrada,
            salida=EstadoSalida,
        ),
        ToolDef(
            nombre="unit.import",
            descripcion="Crea una unidad desde un paquete railspec.unidad/v1 (kit SDD u otro Railspec).",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M, _H}),
            tipos_actor=frozenset({TipoActor.humano}),
            entrada=UnitImportEntrada,
            salida=UnitImportSalida,
        ),
        ToolDef(
            nombre="unit.export",
            descripcion="Devuelve el paquete railspec.unidad/v1 de una unidad.",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H}),
            entrada=UnitExportEntrada,
            salida=UnitExportSalida,
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
            nombre="sync.pull",
            descripcion="Trae los eventos remoto→local de una unidad desde una secuencia.",
            efecto=_L,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M}),
            tipos_actor=frozenset({TipoActor.humano, TipoActor.agente}),
            entrada=SyncPullEntrada,
            salida=SyncPullSalida,
        ),
        ToolDef(
            nombre="sync.push",
            descripcion="Sube en orden los eventos local→remoto pendientes de una unidad (commit.empujado…).",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_M}),
            tipos_actor=frozenset({TipoActor.humano, TipoActor.agente}),
            entrada=SyncPushEntrada,
            salida=SyncPushSalida,
        ),
        ToolDef(
            nombre="graph.query",
            descripcion="Consulta el grafo de código (resolve, search, traverse, related, impact, trace).",
            efecto=_L,
            rol_minimo=Rol.lector,
            superficies=frozenset({_M, _H, _C}),
            entrada=GraphQueryEntrada,
            salida=GraphQuerySalida,
        ),
        ToolDef(
            nombre="graph.index",
            descripcion="Sube el índice del canónico calculado en CI (solo OIDC de GitHub Actions).",
            efecto=_E,
            rol_minimo=Rol.desarrollador,
            superficies=frozenset({_H}),
            tipos_actor=frozenset({TipoActor.servicio}),
            entrada=GraphIndexEntrada,
            salida=GraphIndexSalida,
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


_POR_NOMBRE_MCP: dict[str, ToolDef] = {t.nombre_mcp: t for t in TOOLS.values()}
assert len(_POR_NOMBRE_MCP) == len(TOOLS), "dos tools comparten alias MCP"


def resolver_tool(nombre: str) -> ToolDef | None:
    """Busca una tool por su nombre canónico o por su alias MCP."""

    return TOOLS.get(nombre) or _POR_NOMBRE_MCP.get(nombre)


def tools_para(superficie: Superficie) -> list[ToolDef]:
    return [t for t in TOOLS.values() if superficie in t.superficies]


def manifiesto_tools() -> dict[str, Any]:
    return {
        "version_contrato": VERSION_CONTRATO,
        "tools": [TOOLS[n].manifiesto() for n in sorted(TOOLS)],
        "error": ErrorTool.model_json_schema(),
    }
