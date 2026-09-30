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
    NivelCodigo,
    Perfil,
    Presupuesto,
    Proveedor,
    Riesgo,
    RolRepositorio,
    Sha256,
    Slug,
    UnidadId,
    Veredicto,
    ids_unicos,
)


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
    azure_zona_datos = "azure-zona-datos"
    cualquiera = "cualquiera"


class PoliticaChat(Contrato):
    """``chat_contexto_codigo``: cómo usa el chat el código como contexto interno."""

    permitido: bool = True
    hosting: HostingChat
    modelos_permitidos: list[str] = Field(
        default_factory=list, description="Vacío = todos los del catálogo que cumplan hosting."
    )
    fragmentos_en_respuesta: bool = Field(
        default=False, description="Solo abierto: fragmentos cortos bajo el presupuesto de fuga."
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

    @model_validator(mode="after")
    def _politica(self) -> VinculoRepositorio:
        chat = self.chat_contexto_codigo
        if self.nivel_codigo != NivelCodigo.abierto:
            if chat.hosting != HostingChat.azure_zona_datos:
                raise ValueError("restringido/interno: el chat solo usa modelos en la zona de datos de Azure")
            if chat.fragmentos_en_respuesta:
                raise ValueError("restringido/interno: nunca salen fragmentos en la respuesta")
        return self


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
    hosting: Literal["azure", "anthropic"]
    region: str | None = Field(default=None, max_length=40)
    capacidades: Capacidades
    leido_en: AwareDatetime


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
    roles: dict[str, RequisitoRol] = Field(min_length=1)
    gate: dict[Riesgo, TopeGate]
    exploradores: dict[Riesgo, int]


class PresupuestoConfig(EntidadConfiguracion):
    org: Slug
    workspace: Slug | None = None
    por_unidad: Presupuesto
    por_fase: dict[Fase, Presupuesto] = Field(default_factory=dict)
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

    @model_validator(mode="after")
    def _llamada(self) -> RegistroAuditoria:
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
