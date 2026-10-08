"""Configuración y eventos de los avisos (Teams y correo) y del informe semanal.

Es estado del servidor, no contrato: no viaja por ``/v1`` ni por MCP, así que no sube la versión del
contrato. Se guarda por organización (``avisos_config``); el URL del webhook de Teams se guarda
cifrado aparte (``avisos_secretos``) y aquí solo queda su host.

Nada de lo que describe un aviso lleva texto de código: solo identificadores (organización, workspace,
unidad), la fase y la causa del escalado, los topes del presupuesto como números y totales del informe.
"""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Annotated

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator
from railspec.contracts.comun import Slug
from railspec.contracts.repositorio import Auditoria

MAX_DESTINATARIOS = 20
_CORREO = re.compile(
    r"^[A-Za-z0-9._%+'-]{1,64}@[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?\.[A-Za-z]{2,24}$"
)


class TipoAviso(StrEnum):
    gate_escalado = "gate-escalado"
    presupuesto_agotado = "presupuesto-agotado"
    informe_semanal = "informe-semanal"
    prueba = "prueba"


class Canal(StrEnum):
    teams = "teams"
    correo = "correo"


def correo_valido(texto: str) -> str:
    """El correo normalizado (minúsculas, sin espacios) o ``ValueError``."""

    t = texto.strip().lower()
    if not _CORREO.fullmatch(t) or ".." in t:
        raise ValueError(f"{texto!r} no es una dirección de correo")
    return t


class _Modelo(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EventosAviso(_Modelo):
    gate_escalado: bool = True
    presupuesto_agotado: bool = True

    def admite(self, tipo: TipoAviso) -> bool:
        if tipo == TipoAviso.gate_escalado:
            return self.gate_escalado
        if tipo == TipoAviso.presupuesto_agotado:
            return self.presupuesto_agotado
        return True


class InformeConfig(_Modelo):
    activo: bool = False
    #: 0 = lunes … 6 = domingo (UTC).
    dia_semana: Annotated[int, Field(ge=0, le=6)] = 0
    hora_utc: Annotated[int, Field(ge=0, le=23)] = 8


class ConfigAvisos(_Modelo):
    org: Slug
    version: int = Field(ge=1)
    #: Interruptor general: apagado, ni los avisos ni el informe salen (la configuración se conserva).
    activo: bool = False
    #: Host del webhook de Teams (el URL completo es secreto y vive cifrado aparte); ``None`` = sin Teams.
    teams_host: str | None = Field(default=None, max_length=253)
    destinatarios: list[str] = Field(default_factory=list, max_length=MAX_DESTINATARIOS)
    eventos: EventosAviso = Field(default_factory=EventosAviso)
    informe: InformeConfig = Field(default_factory=InformeConfig)
    auditoria: Auditoria

    @field_validator("destinatarios")
    @classmethod
    def _correos(cls, v: list[str]) -> list[str]:
        vistos = [correo_valido(x) for x in v]
        if len(set(vistos)) != len(vistos):
            raise ValueError("hay destinatarios repetidos")
        return vistos


class EventoAviso(_Modelo):
    """Lo que ocurrió, reducido a lo que se puede decir sin enseñar código."""

    tipo: TipoAviso
    org: Slug
    workspace: str | None = None
    unidad: str | None = None
    fase: str | None = None
    #: ``CausaEscalado`` del gate (``presupuesto-agotado``, ``sin-convergencia``…).
    causa: str | None = None
    #: Solo para ``presupuesto-agotado``: ``por_unidad``, ``por_fase plan`` o ``mensual_usd 2026-10``.
    tope: str | None = None
    #: Solo para ``presupuesto-agotado``: el consumo frente al tope, en números (``tokens 1200/1000``).
    consumo: str | None = None
    en: AwareDatetime
