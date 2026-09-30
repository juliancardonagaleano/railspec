"""Chat de contexto de la consola: respuesta estructurada, gate de salida y conversación.

El modelo del chat no escribe texto libre hacia el usuario: devuelve una
``RespuestaChat`` que el servidor valida y pasa por el gate de salida
determinístico antes de renderizar (R9, R10). El esquema ya excluye bloques
de código, HTML y URL; las huellas, la forma de código, los secretos, el
alcance y el presupuesto de fuga los decide el gate en código.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import UUID4, AwareDatetime, Field, field_validator, model_validator

from ._base import Contrato, Mensaje
from .comun import Actor, AlcanceWorkspace, NivelCodigo, Proveedor, Sha256, verificar_texto_plano
from .referencias import Referencia

# --- Respuesta estructurada (R10) ----------------------------------------------


class Afirmacion(Contrato):
    texto: str = Field(min_length=1, max_length=1200)
    referencias: list[Referencia] = Field(default_factory=list, max_length=20)

    @field_validator("texto")
    @classmethod
    def _plano(cls, v: str) -> str:
        return verificar_texto_plano(v, "afirmacion.texto")


class RespuestaChat(Contrato):
    """Lo único que el modelo del chat puede devolver."""

    afirmaciones: list[Afirmacion] = Field(min_length=1, max_length=30)
    preguntas_abiertas: list[str] = Field(default_factory=list, max_length=10)

    @field_validator("preguntas_abiertas")
    @classmethod
    def _plano(cls, v: list[str]) -> list[str]:
        for p in v:
            if not p or len(p) > 500:
                raise ValueError("pregunta_abierta vacía o de más de 500 caracteres")
            verificar_texto_plano(p, "preguntas_abiertas")
        return v


# --- Veredicto del gate de salida (R10) ------------------------------------------


class ReglaGate(StrEnum):
    esquema = "esquema"
    huella_contexto = "huella-contexto"
    normalizacion = "normalizacion"
    forma_codigo = "forma-codigo"
    secretos = "secretos"
    alcance = "alcance"
    presupuesto_fuga = "presupuesto-fuga"


class ResultadoRegla(StrEnum):
    pasa = "pasa"
    recorta = "recorta"  # solo alcance: elimina referencias no visibles
    bloquea = "bloquea"


class EvaluacionRegla(Contrato):
    regla: ReglaGate
    resultado: ResultadoRegla
    huellas_coincidentes: list[Sha256] = Field(
        default_factory=list, description="Hashes de las huellas que coincidieron; nunca texto."
    )
    referencias_eliminadas: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _coherente(self) -> EvaluacionRegla:
        if self.resultado == ResultadoRegla.recorta and self.regla != ReglaGate.alcance:
            raise ValueError("solo la regla de alcance recorta; las demás pasan o bloquean")
        return self


class VeredictoGateSalida(Contrato):
    version_gate: str = Field(min_length=1, max_length=40)
    permitido: bool
    reglas: list[EvaluacionRegla] = Field(min_length=len(ReglaGate))
    evaluado_en: AwareDatetime

    @model_validator(mode="after")
    def _coherente(self) -> VeredictoGateSalida:
        vistas = [r.regla for r in self.reglas]
        if sorted(vistas) != sorted(ReglaGate):
            raise ValueError("el veredicto evalúa cada regla del gate exactamente una vez")
        bloqueada = any(r.resultado == ResultadoRegla.bloquea for r in self.reglas)
        if self.permitido == bloqueada:
            raise ValueError("permitido debe ser falso si y solo si alguna regla bloquea")
        return self

    @property
    def reglas_fallidas(self) -> list[ReglaGate]:
        return [r.regla for r in self.reglas if r.resultado == ResultadoRegla.bloquea]


# --- Conversación y mensajes (R9) ------------------------------------------------


class ConsumoFuga(Contrato):
    """Caracteres de identificadores y literales citados, acumulados."""

    caracteres: int = Field(ge=0)
    tope: int = Field(ge=1)


class LlamadaTool(Contrato):
    tool: str = Field(pattern=r"^[a-z]+\.[a-z_]+$")
    entrada_sha256: Sha256
    duracion_ms: int = Field(ge=0)
    fragmentos_leidos: int = Field(
        default=0, ge=0, description="Solo code.read; se guardan huellas, no texto."
    )


class RolMensaje(StrEnum):
    usuario = "usuario"
    asistente = "asistente"


class MensajeChat(Mensaje):
    id: UUID4
    conversacion: UUID4
    alcance: AlcanceWorkspace
    rol: RolMensaje
    autor: Actor
    creado_en: AwareDatetime
    pregunta: str | None = Field(default=None, max_length=8000)
    respuesta: RespuestaChat | None = None
    aviso_bloqueo: list[ReglaGate] | None = Field(
        default=None, description="Si el gate bloqueó: reglas que fallaron (sustituye a respuesta)."
    )
    veredicto_gate: VeredictoGateSalida | None = None
    llamadas_tool: list[LlamadaTool] = Field(default_factory=list)
    proveedor: Proveedor | None = None
    modelo: str | None = Field(default=None, max_length=120)
    conservar_en_insumo: bool = False

    @model_validator(mode="after")
    def _coherente(self) -> MensajeChat:
        if self.rol == RolMensaje.usuario:
            if self.pregunta is None or self.respuesta is not None or self.veredicto_gate is not None:
                raise ValueError("un mensaje de usuario lleva pregunta y nada más")
            return self
        if self.veredicto_gate is None:
            raise ValueError("toda respuesta del asistente registra el veredicto del gate")
        if self.veredicto_gate.permitido:
            if self.respuesta is None or self.aviso_bloqueo is not None:
                raise ValueError("respuesta permitida: lleva respuesta y no aviso")
        else:
            if self.respuesta is not None:
                raise ValueError("respuesta bloqueada: nunca se guarda la respuesta del modelo")
            if self.aviso_bloqueo != self.veredicto_gate.reglas_fallidas:
                raise ValueError("aviso_bloqueo debe listar las reglas que fallaron")
            if self.conservar_en_insumo:
                raise ValueError("una respuesta bloqueada no puede conservarse en un insumo")
        return self


class Conversacion(Mensaje):
    id: UUID4
    alcance: AlcanceWorkspace
    repositorios: list[str] = Field(min_length=1)
    autor: Actor
    nivel_efectivo: NivelCodigo = Field(description="El más restrictivo de los repositorios consultados.")
    creada_en: AwareDatetime
    expira_en: AwareDatetime = Field(description="TTL de la conversación en Mongo.")
    consumo_fuga: ConsumoFuga
    consumo_fuga_usuario: ConsumoFuga = Field(description="Acumulado del autor en la ventana vigente.")
    bloqueos: int = Field(default=0, ge=0)
    limitada: bool = Field(default=False, description="Bloqueos repetidos limitan la conversación.")

    @model_validator(mode="after")
    def _coherente(self) -> Conversacion:
        if self.expira_en <= self.creada_en:
            raise ValueError("expira_en debe ser posterior a creada_en")
        return self
