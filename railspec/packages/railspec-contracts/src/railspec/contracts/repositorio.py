"""Modelo de datos del repositorio central (Mongo, grafo y vectores).

Jerarquía: organización → workspace → vínculo de repositorio → unidad. Todo
documento persistido lleva su espacio de nombres; el aislamiento por
workspace se impone en un único módulo de acceso a datos (``almacen``) con
el workspace obligatorio en cada consulta.

Las entidades de configuración editables desde la consola (R4) llevan
``version`` para bloqueo optimista y ``auditoria`` con el actor (R2).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import UUID4, AwareDatetime, Field, model_validator

from ._base import Contrato, Mensaje
from .comun import (
    Actor,
    AlcanceRepositorio,
    AlcanceWorkspace,
    Effort,
    Fase,
    GateFase,
    Modo,
    NivelCodigo,
    Perfil,
    Presupuesto,
    Proveedor,
    Riesgo,
    RolRepositorio,
    Sha256,
    Slug,
    TipoActor,
    UnidadId,
    Veredicto,
    ids_unicos,
)
from .portabilidad import OrigenPaquete


class Auditoria(Contrato):
    creado_por: Actor
    creado_en: AwareDatetime
    actualizado_por: Actor
    actualizado_en: AwareDatetime

    @model_validator(mode="after")
    def _orden(self) -> Auditoria:
        if self.actualizado_en < self.creado_en:
            raise ValueError("actualizado_en anterior a creado_en")
        return self


class EntidadConfiguracion(Mensaje):
    version: int = Field(ge=1, description="Bloqueo optimista; +1 en cada escritura.")
    auditoria: Auditoria


# --- Organización, workspace, roles (R3) ------------------------------------------


class Organizacion(EntidadConfiguracion):
    id: Slug
    nombre: str = Field(min_length=1, max_length=200)
    github_org: str | None = Field(default=None, max_length=39)
    region_datos: str = Field(min_length=1, max_length=40, description="Residencia (p. ej. eastus2).")


class Workspace(EntidadConfiguracion):
    alcance: AlcanceWorkspace
    nombre: str = Field(min_length=1, max_length=200)
    zona_datos_azure: str | None = Field(default=None, max_length=40)
    perfil_por_defecto: Perfil = Perfil.estandar


class Rol(StrEnum):
    org_admin = "org-admin"
    workspace_admin = "workspace-admin"
    desarrollador = "desarrollador"
    lector = "lector"


class SujetoUsuario(Contrato):
    tipo: Literal["usuario"] = "usuario"
    github_id: int = Field(ge=1)


class SujetoEquipo(Contrato):
    tipo: Literal["equipo"] = "equipo"
    github_org: str = Field(min_length=1, max_length=39)
    equipo: str = Field(min_length=1, max_length=100, description="Slug del equipo de GitHub.")
    equipo_id: int = Field(ge=1)


class AsignacionRol(EntidadConfiguracion):
    id: UUID4
    org: Slug
    workspace: Slug | None = Field(default=None, description="None = rol a nivel organización.")
    rol: Rol
    sujeto: SujetoUsuario | SujetoEquipo = Field(discriminator="tipo")

    @model_validator(mode="after")
    def _nivel(self) -> AsignacionRol:
        if (self.rol == Rol.org_admin) != (self.workspace is None):
            raise ValueError("org-admin va a nivel organización; los demás roles, a un workspace")
        return self


# --- Vínculo de repositorio y política (R4) ------------------------------------------


class HostingChat(StrEnum):
    """Dato heredado: desde la decisión del 2026-10-06 ya no restringe proveedores.

    Usar Anthropic, modelos abiertos o proveedores compatibles es decisión consciente del usuario; el
    campo se conserva por compatibilidad y para la auditoría, sin efecto sobre la elección de modelo.
    """

    azure_zona_datos = "azure-zona-datos"
    cualquiera = "cualquiera"


class PoliticaChat(Contrato):
    """``chat_contexto_codigo``: cómo usa el chat el código como contexto interno.

    ``hosting`` ya no restringe proveedores: el material que viaja lo gobiernan el nivel de código, la
    huella N y los presupuestos de fuga.
    """

    permitido: bool = True
    hosting: HostingChat
    modelos_permitidos: list[str] = Field(
        default_factory=list,
        description=(
            "Lista explícita del usuario; vacío = todos los del catálogo. "
            "``hosting`` ya no filtra el catálogo (decisión del 2026-10-06)."
        ),
    )
    fragmentos_en_respuesta: bool = Field(
        default=False,
        description=(
            "Fragmentos cortos en la respuesta, bajo el presupuesto de fuga. "
            "Ya no se prohíbe en ningún nivel (decisión del 2026-10-06); por defecto solo en abierto."
        ),
    )
    huella_tokens_n: int = Field(
        ge=4, le=64, description="Secuencia compartida máxima antes de bloquear (regla huella)."
    )
    presupuesto_fuga_conversacion: int = Field(ge=1)
    presupuesto_fuga_usuario_dia: int = Field(ge=1)


def politica_chat_por_defecto(nivel: NivelCodigo) -> PoliticaChat:
    """Valores del diseño de la consola del 2026-09-30; editables por vínculo."""

    if nivel == NivelCodigo.abierto:
        return PoliticaChat(
            hosting=HostingChat.cualquiera,
            fragmentos_en_respuesta=True,
            huella_tokens_n=24,
            presupuesto_fuga_conversacion=4000,
            presupuesto_fuga_usuario_dia=20000,
        )
    return PoliticaChat(
        hosting=HostingChat.azure_zona_datos,
        huella_tokens_n=12 if nivel == NivelCodigo.restringido else 16,
        presupuesto_fuga_conversacion=1500,
        presupuesto_fuga_usuario_dia=6000,
    )


class VinculoRepositorio(EntidadConfiguracion):
    alcance: AlcanceRepositorio
    url: str = Field(pattern=r"^https://", max_length=512)
    rol: RolRepositorio
    rama_por_defecto: str = Field(default="main", min_length=1, max_length=255)
    nivel_codigo: NivelCodigo = NivelCodigo.restringido
    chat_contexto_codigo: PoliticaChat
    retencion_snapshots_dias: int = Field(default=30, ge=1)
    exclusiones: list[str] = Field(
        default_factory=list, description="Patrones además de .railspecignore y los de secretos."
    )


# --- Perfiles, catálogo, presupuestos, proveedores de contexto (R4) --------------------


class Capacidades(Contrato):
    efforts: list[Effort] = Field(default_factory=list)
    thinking: bool = False
    structured_outputs: bool = False
    contexto_max_tokens: int = Field(ge=1)


class ModeloCatalogo(Mensaje):
    """Leído por API del proveedor; no se edita a mano."""

    org: Slug
    proveedor: Proveedor
    modelo: str = Field(min_length=1, max_length=120)
    despliegue: str | None = Field(default=None, max_length=120, description="Nombre en Foundry.")
    hosting: Literal["azure", "anthropic", "externo"] = Field(
        description="Desde 1.8: ``externo`` = endpoint compatible fuera de Azure (informativo; no restringe)."
    )
    region: str | None = Field(default=None, max_length=40)
    capacidades: Capacidades
    leido_en: AwareDatetime


class AutenticacionSuscripcion(StrEnum):
    api_key = "api-key"
    #: Entra ID de la identidad del servidor (solo Foundry); la suscripción no guarda ninguna clave.
    identidad_servidor = "identidad-servidor"


class ProtocoloCompatible(StrEnum):
    """Desde 1.8: API que habla un modelo de un proveedor ``compatible``."""

    anthropic_messages = "anthropic-messages"
    openai_chat = "openai-chat"


class PrecioModelo(Contrato):
    """Desde 1.8: tarifa del modelo en USD por millón de tokens, para los topes de costo."""

    entrada: float = Field(ge=0)
    salida: float = Field(ge=0)
    cache_lectura: float = Field(default=0.0, ge=0)


class ModeloSuscripcion(Contrato):
    """Un modelo (o despliegue de Foundry) de una suscripción, elegible o no como modelo disponible.

    ``origen`` ``descubierto`` sale de la API del proveedor; ``declarado`` lo escribe un administrador
    (un despliegue que la API no lista o unas capacidades que la API no da) y gana sobre el descubierto
    de la misma clave. ``region`` se deriva de la suscripción y del SKU (Foundry): ``global``,
    ``zona-<zona>`` o la región del recurso; ``None`` si no se puede saber. La región se audita y no
    restringe qué repositorios sirve el modelo. ``ausente``: estaba elegido y la última lectura ya no lo trae.
    """

    modelo: str = Field(min_length=1, max_length=120)
    despliegue: str | None = Field(default=None, max_length=120, description="Nombre en Foundry.")
    sku: str | None = Field(default=None, max_length=60)
    region: str | None = Field(default=None, max_length=40)
    capacidades: Capacidades
    origen: Literal["descubierto", "declarado"]
    seleccionado: bool = False
    ausente: bool = False
    visto_en: AwareDatetime | None = None
    protocolo: ProtocoloCompatible | None = Field(
        default=None,
        description="Desde 1.8: API que habla el modelo; obligatorio en suscripciones ``compatible``.",
    )
    precio_usd_mtok: PrecioModelo | None = Field(
        default=None,
        description="Desde 1.8: sin tarifa, el modelo cuenta 0 USD y ``costo_usd_max`` no lo frena.",
    )

    @property
    def clave(self) -> str:
        return self.despliegue or self.modelo


class LecturaSuscripcion(Contrato):
    """Resultado del último descubrimiento de modelos de una suscripción; el error ya viene saneado."""

    en: AwareDatetime
    por: str | None = Field(default=None, max_length=100)
    resultado: Literal["ok", "error"]
    modelos: int = Field(ge=0)
    error_codigo: str | None = Field(default=None, max_length=40)
    error_detalle: str | None = Field(default=None, max_length=500)


class SuscripcionModelo(EntidadConfiguracion):
    """Conexión de una organización a un proveedor de modelos (desde 1.6).

    La clave **no** forma parte de este contrato: se guarda cifrada aparte y ninguna API la devuelve;
    aquí solo consta si hay una (``clave_configurada``). Foundry exige ``endpoint``; Anthropic usa
    el endpoint fijo del proveedor y solo ``api-key``. ``region`` y ``zona_datos`` son los del recurso de
    Foundry: con ellas y el SKU de cada despliegue se deriva la región de cada modelo.
    """

    org: Slug
    id: Slug
    nombre: str = Field(min_length=1, max_length=120)
    proveedor: Proveedor
    endpoint: str | None = Field(
        default=None,
        pattern=r"^https://",
        max_length=512,
        description="Foundry: recurso. ``compatible`` (1.8): base de la API de chat completions de OpenAI.",
    )
    endpoint_mensajes: str | None = Field(
        default=None,
        pattern=r"^https://",
        max_length=512,
        description="Desde 1.8, solo ``compatible``: base de la API de mensajes de Anthropic.",
    )
    servicio: str | None = Field(
        default=None,
        max_length=40,
        description="Desde 1.8, solo ``compatible``: servicio conocido (``opencode-zen``, ``minimax``...).",
    )
    proyecto: str | None = Field(
        default=None,
        max_length=512,
        description="Proyecto de Foundry (nombre o URL) para descubrir despliegues.",
    )
    region: str | None = Field(default=None, max_length=40)
    zona_datos: str | None = Field(default=None, max_length=40)
    autenticacion: AutenticacionSuscripcion = AutenticacionSuscripcion.api_key
    clave_configurada: bool = False
    clave_actualizada_en: AwareDatetime | None = None
    habilitada: bool = True
    modelos: list[ModeloSuscripcion] = Field(default_factory=list)
    ultima_lectura: LecturaSuscripcion | None = None

    @model_validator(mode="after")
    def _coherente(self) -> SuscripcionModelo:
        if self.proveedor == Proveedor.compatible:
            self._coherente_compatible()
        else:
            if self.endpoint_mensajes is not None or self.servicio is not None:
                raise ValueError("endpoint_mensajes y servicio son solo de los proveedores compatibles")
            if any(m.protocolo is not None for m in self.modelos):
                raise ValueError("el protocolo de un modelo es solo de los proveedores compatibles")
            if self.proveedor == Proveedor.foundry:
                if self.endpoint is None:
                    raise ValueError("una suscripción de Foundry exige endpoint")
            else:
                if self.endpoint is not None or self.proyecto or self.region or self.zona_datos:
                    raise ValueError(
                        "Anthropic usa el endpoint del proveedor: sin endpoint, proyecto, región ni zona"
                    )
                if self.autenticacion != AutenticacionSuscripcion.api_key:
                    raise ValueError("Anthropic solo se autentica con api-key")
        if self.autenticacion == AutenticacionSuscripcion.identidad_servidor and self.clave_configurada:
            raise ValueError("la identidad del servidor no lleva clave")
        ids_unicos([m.clave for m in self.modelos], "modelos de la suscripción")
        return self

    def _coherente_compatible(self) -> None:
        if self.endpoint is None and self.endpoint_mensajes is None:
            raise ValueError("una suscripción compatible exige endpoint o endpoint_mensajes")
        if self.proyecto or self.region or self.zona_datos:
            raise ValueError("una suscripción compatible no lleva proyecto, región ni zona de datos")
        if self.autenticacion != AutenticacionSuscripcion.api_key:
            raise ValueError("una suscripción compatible solo se autentica con api-key")
        for m in self.modelos:
            if m.protocolo is None:
                raise ValueError(f"{m.clave}: un modelo compatible exige su protocolo")
            falta = (
                self.endpoint if m.protocolo == ProtocoloCompatible.openai_chat else self.endpoint_mensajes
            )
            if falta is None:
                raise ValueError(f"{m.clave}: la suscripción no tiene endpoint para {m.protocolo.value}")


class RequisitoRol(Contrato):
    modelo: dict[Proveedor, str] = Field(min_length=1, description="Modelo por proveedor.")
    effort: Effort | None = None
    structured_outputs: bool = False
    contexto_min_tokens: int | None = Field(default=None, ge=1)


class TopeGate(Contrato):
    criticos: int = Field(ge=1)
    iteraciones: int = Field(ge=1)
    adversarial: bool


class PerfilConfig(EntidadConfiguracion):
    org: Slug
    workspace: Slug | None = None
    nombre: Perfil
    suscripcion: Slug | None = Field(
        default=None,
        description=(
            "Desde 1.6: suscripción de la organización de la que salen los modelos del perfil; los modelos "
            "de ``roles`` tienen que estar entre los que esa suscripción tiene elegidos. Sin ella rigen "
            "las variables RAILSPEC_FOUNDRY_* / RAILSPEC_ANTHROPIC_* del servidor."
        ),
    )
    roles: dict[str, RequisitoRol] = Field(min_length=1)
    gate: dict[Riesgo, TopeGate]
    exploradores: dict[Riesgo, int]


class PresupuestoConfig(EntidadConfiguracion):
    org: Slug
    workspace: Slug | None = None
    por_unidad: Presupuesto
    por_fase: dict[Fase, Presupuesto] = Field(default_factory=dict)
    por_tier: dict[Riesgo, Presupuesto] = Field(
        default_factory=dict,
        description=(
            "Desde 1.7: tope de cada unidad según su riesgo (el tier). Rige como ``por_unidad``: por "
            "cada tope, el efectivo es el menor de los dos. Sin entrada para un riesgo, solo rige "
            "``por_unidad``."
        ),
    )
    mensual_usd: float | None = Field(default=None, gt=0)


class RolContexto(StrEnum):
    gobernanza = "gobernanza"
    grafo_de_codigo = "grafo-de-codigo"
    memoria = "memoria"
    documentacion = "documentacion"


class ProveedorContexto(EntidadConfiguracion):
    org: Slug
    workspace: Slug | None = None
    rol: RolContexto
    nombre: str = Field(min_length=1, max_length=80)
    url: str = Field(pattern=r"^https://", max_length=512)
    credencial_ref: str | None = Field(
        default=None,
        pattern=r"^secret://[a-z0-9-]+/[A-Za-z0-9_.-]+$",
        description="Referencia a Kubernetes Secret (secret://<secreto>/<clave>); nunca el valor.",
    )
    politica_fallo: Literal["estricta", "blanda"]
    fases: list[Fase] = Field(default_factory=list)
    presupuesto_tokens: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _gobernanza_estricta(self) -> ProveedorContexto:
        if self.rol == RolContexto.gobernanza and self.politica_fallo != "estricta":
            raise ValueError("el rol gobernanza siempre tiene política de fallo estricta")
        return self


# --- Telemetría (R5) y auditoría -------------------------------------------------------


class TelemetriaNodo(Mensaje):
    id: UUID4
    org: Slug
    workspace: Slug
    repositorio: Slug | None = None
    unidad: UnidadId | None = None
    conversacion: UUID4 | None = None
    nodo: str = Field(min_length=1, max_length=120)
    fase: Fase | GateFase | Literal["chat"]
    tier: Riesgo | None = None
    proveedor: Proveedor | None = None
    modelo: str | None = Field(default=None, max_length=120)
    tokens_entrada: int = Field(default=0, ge=0)
    tokens_salida: int = Field(default=0, ge=0)
    tokens_cache_lectura: int = Field(default=0, ge=0)
    tokens_cache_escritura: int = Field(default=0, ge=0)
    costo_usd: float = Field(default=0.0, ge=0)
    duracion_ms: int = Field(ge=0)
    veredicto: Veredicto | None = None
    en: AwareDatetime

    @model_validator(mode="after")
    def _origen(self) -> TelemetriaNodo:
        if (self.unidad is None) == (self.conversacion is None):
            raise ValueError("la telemetría es de una unidad o de una conversación, no de ambas")
        return self


#: Claves por las que ``telemetry.query`` puede agrupar.
CLAVES_TELEMETRIA = (
    "workspace",
    "repositorio",
    "unidad",
    "nodo",
    "fase",
    "tier",
    "proveedor",
    "modelo",
    "veredicto",
)


class EventoAuditoria(StrEnum):
    llamada_modelo = "llamada-modelo"
    lectura_codigo = "lectura-codigo"
    resolucion_checkpoint = "resolucion-checkpoint"
    rehabilitacion_gate = "rehabilitacion-gate"
    integracion = "integracion"
    cambio_nivel = "cambio-nivel"
    cambio_configuracion = "cambio-configuracion"
    desvinculo_repositorio = "desvinculo-repositorio"
    bloqueo_gate_salida = "bloqueo-gate-salida"
    importacion = "importacion"  # desde 1.4: unit.import
    cambio_modo = "cambio-modo"  # desde 1.7: unit.set_mode
    cambio_mandato = (
        "cambio-mandato"  # desde 1.11: propuesta, aprobación, parada o revocación (detalle.accion)
    )
    decision_delegada = "decision-delegada"  # desde 1.11: una decisión tomada bajo el mandato
    revision_decision = "revision-decision"  # desde 1.11: un humano acepta o revierte una decisión


class RegistroAuditoria(Mensaje):
    id: UUID4
    alcance: AlcanceWorkspace
    evento: EventoAuditoria
    actor: Actor
    en: AwareDatetime
    repositorio: Slug | None = None
    unidad: UnidadId | None = None
    conversacion: UUID4 | None = None
    nivel_codigo: NivelCodigo | None = None
    proveedor: Proveedor | None = None
    modelo: str | None = Field(default=None, max_length=120)
    region: str | None = Field(default=None, max_length=40)
    sha256_enviado: Sha256 | None = Field(default=None, description="Hash de lo enviado; nunca el texto.")
    detalle: dict[str, str | int | bool] = Field(default_factory=dict)
    origen_importacion: OrigenPaquete | None = Field(
        default=None, description="Desde 1.4: evento importacion."
    )
    artefactos_importados: int | None = Field(
        default=None, ge=0, le=3, description="Desde 1.4: artefactos aprobados por importación."
    )
    gate: GateFase | None = Field(
        default=None, description="Desde 1.7: gate que rehabilitó el evento rehabilitacion-gate."
    )
    modo_anterior: Modo | None = Field(default=None, description="Desde 1.7: evento cambio-modo.")
    modo_nuevo: Modo | None = Field(default=None, description="Desde 1.7: evento cambio-modo.")

    @model_validator(mode="after")
    def _llamada(self) -> RegistroAuditoria:
        importacion = self.evento == EventoAuditoria.importacion
        if importacion:
            faltan = [
                c
                for c in ("unidad", "origen_importacion", "artefactos_importados")
                if getattr(self, c) is None
            ]
            if faltan:
                raise ValueError(f"importacion necesita {', '.join(faltan)}")
            if self.actor.tipo != TipoActor.humano:
                raise ValueError("importacion exige un actor humano")
        elif self.origen_importacion is not None or self.artefactos_importados is not None:
            raise ValueError("origen_importacion y artefactos_importados solo van con importacion")
        rehabilitacion = self.evento == EventoAuditoria.rehabilitacion_gate
        cambio_modo = self.evento == EventoAuditoria.cambio_modo
        if rehabilitacion or cambio_modo:
            exigidos = ("unidad", "gate") if rehabilitacion else ("unidad", "modo_anterior", "modo_nuevo")
            faltan = [c for c in exigidos if getattr(self, c) is None]
            if faltan:
                raise ValueError(f"{self.evento.value} necesita {', '.join(faltan)}")
            if self.actor.tipo != TipoActor.humano:
                raise ValueError(f"{self.evento.value} exige un actor humano")
        if not rehabilitacion and self.gate is not None:
            raise ValueError("gate solo va con rehabilitacion-gate")
        if not cambio_modo and (self.modo_anterior is not None or self.modo_nuevo is not None):
            raise ValueError("modo_anterior y modo_nuevo solo van con cambio-modo")
        if cambio_modo and self.modo_anterior == self.modo_nuevo:
            raise ValueError("un cambio de modo cambia el modo")
        if self.evento in (EventoAuditoria.llamada_modelo, EventoAuditoria.lectura_codigo):
            faltan = [
                c for c in ("repositorio", "nivel_codigo", "sha256_enviado") if getattr(self, c) is None
            ]
            if self.evento == EventoAuditoria.llamada_modelo:
                faltan += [c for c in ("proveedor", "modelo", "region") if getattr(self, c) is None]
            if faltan:
                raise ValueError(f"{self.evento.value} necesita {', '.join(faltan)}")
        return self


# --- Colecciones y grafos ----------------------------------------------------------------


class Coleccion(Contrato):
    nombre: str
    modelo: str
    clave_aislamiento: tuple[str, ...]
    ttl_campo: str | None = None


COLECCIONES: tuple[Coleccion, ...] = (
    Coleccion(nombre="organizaciones", modelo="Organizacion", clave_aislamiento=("id",)),
    Coleccion(
        nombre="workspaces", modelo="Workspace", clave_aislamiento=("alcance.org", "alcance.workspace")
    ),
    Coleccion(nombre="roles", modelo="AsignacionRol", clave_aislamiento=("org",)),
    Coleccion(
        nombre="vinculos",
        modelo="VinculoRepositorio",
        clave_aislamiento=("alcance.org", "alcance.workspace"),
    ),
    Coleccion(nombre="catalogo", modelo="ModeloCatalogo", clave_aislamiento=("org",)),
    Coleccion(nombre="suscripciones", modelo="SuscripcionModelo", clave_aislamiento=("org",)),
    Coleccion(nombre="perfiles", modelo="PerfilConfig", clave_aislamiento=("org",)),
    Coleccion(nombre="presupuestos", modelo="PresupuestoConfig", clave_aislamiento=("org",)),
    Coleccion(nombre="proveedores_contexto", modelo="ProveedorContexto", clave_aislamiento=("org",)),
    Coleccion(nombre="unidades", modelo="EstadoUnidad", clave_aislamiento=("unidad.org", "unidad.workspace")),
    Coleccion(
        nombre="ordenes", modelo="OrdenDeTrabajo", clave_aislamiento=("unidad.org", "unidad.workspace")
    ),
    Coleccion(
        nombre="snapshots",
        modelo="Snapshot",
        clave_aislamiento=("unidad.org", "unidad.workspace"),
        ttl_campo="creado_en",
    ),
    Coleccion(nombre="eventos", modelo="EventoSync", clave_aislamiento=("unidad.org", "unidad.workspace")),
    Coleccion(
        nombre="conversaciones",
        modelo="Conversacion",
        clave_aislamiento=("alcance.org", "alcance.workspace"),
        ttl_campo="expira_en",
    ),
    Coleccion(
        nombre="mensajes_chat",
        modelo="MensajeChat",
        clave_aislamiento=("alcance.org", "alcance.workspace"),
    ),
    Coleccion(nombre="insumos", modelo="Insumo", clave_aislamiento=("alcance.org", "alcance.workspace")),
    Coleccion(nombre="telemetria", modelo="TelemetriaNodo", clave_aislamiento=("org", "workspace")),
    Coleccion(
        nombre="auditoria", modelo="RegistroAuditoria", clave_aislamiento=("alcance.org", "alcance.workspace")
    ),
)


def nombre_grafo(alcance: AlcanceRepositorio) -> str:
    """Un grafo físico de FalkorDB por (workspace, repositorio)."""

    return f"railspec:{alcance.org}:{alcance.workspace}:{alcance.repositorio}"


ids_unicos([c.nombre for c in COLECCIONES], "colecciones")
