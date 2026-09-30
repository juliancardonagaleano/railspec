"""Perfiles de esfuerzo por defecto y resolución de rol → modelo.

La consola edita ``PerfilConfig`` por organización o workspace; si no hay
ninguno guardado, rigen estos. Los modelos son despliegues de Claude en
Foundry (primario) y el mismo id en Anthropic directo; ``effort`` se pasa en
``output_config``. El ``riesgo`` de la unidad fija el tope del gate (críticos,
iteraciones, verificación adversarial) dentro del perfil.
"""

from __future__ import annotations

from datetime import UTC, datetime

from railspec.contracts.comun import (
    Actor,
    AlcanceWorkspace,
    Canal,
    Effort,
    Perfil,
    Proveedor,
    Riesgo,
    TipoActor,
)
from railspec.contracts.repositorio import Auditoria, PerfilConfig, RequisitoRol, TopeGate

#: Roles de modelo del motor. Los críticos siguen la tabla del kit
#: (estructural para tasks, cumplimiento para CA-NN y encaje, profundo para lo demás).
ROLES = ("redactor", "critico-estructural", "critico-profundo", "critico-cumplimiento", "refutador")

ACTOR_SERVIDOR = Actor(tipo=TipoActor.agente, canal=Canal.servidor, agente="railspec-server")

_OPUS = "claude-opus-5-5"
_SONNET = "claude-sonnet-5-5"


def _req(modelo: str, effort: Effort) -> RequisitoRol:
    return RequisitoRol(
        modelo={Proveedor.foundry: modelo, Proveedor.anthropic: modelo},
        effort=effort,
        structured_outputs=True,
    )


_ROLES_POR_PERFIL: dict[Perfil, dict[str, RequisitoRol]] = {
    Perfil.ligero: {
        "redactor": _req(_SONNET, Effort.medium),
        "critico-estructural": _req(_SONNET, Effort.low),
        "critico-profundo": _req(_SONNET, Effort.medium),
        "critico-cumplimiento": _req(_SONNET, Effort.medium),
        "refutador": _req(_SONNET, Effort.medium),
    },
    Perfil.estandar: {
        "redactor": _req(_OPUS, Effort.medium),
        "critico-estructural": _req(_SONNET, Effort.medium),
        "critico-profundo": _req(_OPUS, Effort.high),
        "critico-cumplimiento": _req(_SONNET, Effort.high),
        "refutador": _req(_OPUS, Effort.high),
    },
    Perfil.profundo: {
        "redactor": _req(_OPUS, Effort.high),
        "critico-estructural": _req(_OPUS, Effort.medium),
        "critico-profundo": _req(_OPUS, Effort.xhigh),
        "critico-cumplimiento": _req(_OPUS, Effort.high),
        "refutador": _req(_OPUS, Effort.xhigh),
    },
}

_GATE_POR_PERFIL: dict[Perfil, dict[Riesgo, TopeGate]] = {
    Perfil.ligero: {
        Riesgo.bajo: TopeGate(criticos=1, iteraciones=1, adversarial=False),
        Riesgo.medio: TopeGate(criticos=1, iteraciones=2, adversarial=False),
        Riesgo.alto: TopeGate(criticos=2, iteraciones=2, adversarial=True),
    },
    Perfil.estandar: {
        Riesgo.bajo: TopeGate(criticos=1, iteraciones=2, adversarial=False),
        Riesgo.medio: TopeGate(criticos=2, iteraciones=2, adversarial=False),
        Riesgo.alto: TopeGate(criticos=3, iteraciones=3, adversarial=True),
    },
    Perfil.profundo: {
        Riesgo.bajo: TopeGate(criticos=2, iteraciones=2, adversarial=False),
        Riesgo.medio: TopeGate(criticos=3, iteraciones=3, adversarial=True),
        Riesgo.alto: TopeGate(criticos=3, iteraciones=4, adversarial=True),
    },
}

_EXPLORADORES = {Riesgo.bajo: 0, Riesgo.medio: 1, Riesgo.alto: 2}


def perfil_por_defecto(alcance: AlcanceWorkspace, nombre: Perfil) -> PerfilConfig:
    origen = datetime(2026, 9, 30, tzinfo=UTC)
    return PerfilConfig(
        version=1,
        auditoria=Auditoria(
            creado_por=ACTOR_SERVIDOR,
            creado_en=origen,
            actualizado_por=ACTOR_SERVIDOR,
            actualizado_en=origen,
        ),
        org=alcance.org,
        workspace=None,
        nombre=nombre,
        roles=dict(_ROLES_POR_PERFIL[nombre]),
        gate=dict(_GATE_POR_PERFIL[nombre]),
        exploradores=dict(_EXPLORADORES),
    )


def requisito(perfil: PerfilConfig, rol: str) -> RequisitoRol:
    """El rol del perfil guardado; si la consola no lo definió, el del perfil por defecto."""

    return perfil.roles.get(rol) or _ROLES_POR_PERFIL[perfil.nombre][rol]


def tope_gate(perfil: PerfilConfig, riesgo: Riesgo) -> TopeGate:
    return perfil.gate.get(riesgo) or _GATE_POR_PERFIL[perfil.nombre][riesgo]
