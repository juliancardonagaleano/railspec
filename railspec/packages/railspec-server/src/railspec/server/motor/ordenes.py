"""Construcción de órdenes de trabajo (funciones puras).

La orden es el contrato con el arnés: dice qué hacer, con qué contexto, sobre
qué archivos y qué reportar. El motor nunca le deja decidir fase ni gate.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from railspec.contracts.comun import Criterio, Fase, Glob
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.hallazgos import Hallazgo
from railspec.contracts.orden import (
    AlcanceArchivos,
    Artefacto,
    ContextoArmado,
    OrdenImplementar,
    OrdenRedactar,
    OrdenRefinar,
    OrdenValidar,
    Tarea,
)

from .artefactos import PLANTILLAS, GrupoPlan, GrupoTareas
from .nucleo import primario

FASE_DE_ARTEFACTO = {Artefacto.spec: Fase.spec, Artefacto.plan: Fase.plan, Artefacto.tasks: Fase.tasks}


def ruta_artefacto(estado: EstadoUnidad, artefacto: Artefacto) -> str:
    return f".railspec/unidades/{estado.unidad.unidad}/{artefacto.value}.md"


def _base(estado: EstadoUnidad, id_: uuid.UUID, ahora: datetime) -> dict:
    repo = primario(estado)
    base_commit = next(r.base_commit for r in estado.repositorios if r.repositorio == repo)
    return {
        "id": id_,
        "secuencia": estado.secuencia_ordenes + 1,
        "unidad": estado.unidad,
        "repositorio": repo,
        "base_commit": base_commit,
        "emitida_en": ahora,
    }


_GUIA = {
    Artefacto.spec: "Redacta el spec: QUÉ y POR QUÉ, sin solución técnica. Cada criterio CA-NN debe ser verificable.",
    Artefacto.plan: (
        "Redacta el plan técnico a partir del spec aprobado: enfoque, reutilización, grupos con sus "
        "archivos (globs relativos), riesgos y un comando de validación local."
    ),
    Artefacto.tasks: (
        "Descompón el plan aprobado en tareas atómicas por grupo; cada tarea cita los CA-NN que cubre "
        "y todo CA-NN queda cubierto."
    ),
}


def redactar(
    estado: EstadoUnidad,
    artefacto: Artefacto,
    pedido: str,
    contexto: ContextoArmado,
    criterios: list[Criterio],
    id_: uuid.UUID,
    ahora: datetime,
    nota: str | None = None,
) -> OrdenRedactar:
    ruta = ruta_artefacto(estado, artefacto)
    instrucciones = f"{_GUIA[artefacto]}\n\nPedido de la unidad «{estado.titulo}»:\n{pedido}"
    if nota:
        instrucciones += f"\n\nIndicación adicional:\n{nota}"
    return OrdenRedactar(
        **_base(estado, id_, ahora),
        fase=FASE_DE_ARTEFACTO[artefacto],
        instrucciones=instrucciones[:20_000],
        contexto=contexto,
        alcance=AlcanceArchivos(permitidos=[ruta]),
        criterios=criterios,
        artefacto=artefacto,
        ruta_artefacto=ruta,
        plantilla=PLANTILLAS[artefacto],
    )


def refinar(
    estado: EstadoUnidad,
    artefacto: Artefacto,
    sha256_actual: str,
    hallazgos: list[Hallazgo],
    contexto: ContextoArmado,
    criterios: list[Criterio],
    id_: uuid.UUID,
    ahora: datetime,
) -> OrdenRefinar:
    ruta = ruta_artefacto(estado, artefacto)
    return OrdenRefinar(
        **_base(estado, id_, ahora),
        fase=FASE_DE_ARTEFACTO[artefacto],
        instrucciones=(
            f"Corrige {ruta} según los hallazgos del gate. Resuelve todos los de severidad alta y media; "
            "los de severidad baja son opcionales. No cambies lo que ningún hallazgo señala."
        ),
        contexto=contexto.model_copy(update={"hallazgos_previos": hallazgos}),
        alcance=AlcanceArchivos(permitidos=[ruta]),
        criterios=criterios,
        artefacto=artefacto,
        ruta_artefacto=ruta,
        sha256_actual=sha256_actual,
        hallazgos=hallazgos,
    )


def implementar(
    estado: EstadoUnidad,
    grupo_plan: GrupoPlan,
    grupo_tareas: GrupoTareas,
    criterios: list[Criterio],
    comando_validacion: str | None,
    contexto: ContextoArmado,
    id_: uuid.UUID,
    ahora: datetime,
    hallazgos: list[Hallazgo] | None = None,
    permitidos: list[Glob] | None = None,
) -> OrdenImplementar:
    citados = {c for t in grupo_tareas.tareas for c in t.criterios}
    instrucciones = f"Implementa el grupo {grupo_plan.id} ({grupo_plan.nombre}) del plan: sus tareas, nada más."
    if hallazgos:
        instrucciones = (
            f"Corrige la implementación según los hallazgos del gate de código (grupo {grupo_plan.id}). "
            "Resuelve los de severidad alta y media."
        )
        contexto = contexto.model_copy(update={"hallazgos_previos": hallazgos})
    return OrdenImplementar(
        **_base(estado, id_, ahora),
        fase=Fase.implement,
        instrucciones=instrucciones,
        contexto=contexto,
        alcance=AlcanceArchivos(permitidos=permitidos or grupo_plan.archivos),
        criterios=[c for c in criterios if c.id in citados],
        comando_validacion=comando_validacion,
        grupo=grupo_plan.id,
        tareas=[Tarea(id=t.id, descripcion=t.descripcion, criterios=t.criterios) for t in grupo_tareas.tareas],
    )


def validar(
    estado: EstadoUnidad, comando: str, contexto: ContextoArmado, criterios: list[Criterio], id_: uuid.UUID, ahora: datetime
) -> OrdenValidar:
    return OrdenValidar(
        **_base(estado, id_, ahora),
        fase=Fase.implement,
        instrucciones="Corre el comando de validación en el worktree de la unidad y reporta la salida tal cual.",
        contexto=contexto,
        criterios=criterios,
        comando_validacion=comando,
    )
