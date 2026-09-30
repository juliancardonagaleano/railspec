"""Capa determinista: plantillas y validadores de spec, plan y tasks.

Un artefacto inválido nunca llega a un modelo: si la forma no cumple, el gate
devuelve hallazgos del lente ``estructura`` sin gastar tokens y la orden de
refinar vuelve al arnés. De cada artefacto válido se extrae lo que el motor
necesita para las fases siguientes: criterios ``CA-NN`` (spec), grupos con su
alcance y el comando de validación (plan), tareas por grupo (tasks).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from pydantic import BaseModel, Field
from railspec.contracts.comun import Criterio, GateFase, Severidad
from railspec.contracts.hallazgos import Cita, Hallazgo
from railspec.contracts.orden import Artefacto, Tarea

MAX_CARACTERES = {Artefacto.spec: 40_000, Artefacto.plan: 60_000, Artefacto.tasks: 40_000}

PLANTILLAS: dict[Artefacto, str] = {
    Artefacto.spec: """# <título de la unidad>

## Problema
Qué pasa hoy y por qué importa. Sin solución técnica.

## Alcance
Qué entra.

## Fuera de alcance
Qué no entra, explícitamente.

## Criterios de aceptación
- CA-01: <comportamiento observable y verificable>
- CA-02: <...>
""",
    Artefacto.plan: """# Plan: <título de la unidad>

## Enfoque
Cómo se resuelve y por qué así; qué se reutiliza del repositorio.

## Grupos
### G1 — <nombre>
Archivos: `ruta/o/glob`, `otra/ruta`
Qué cambia en este grupo.

### G2 — <nombre>
Archivos: `...`

## Riesgos
- <riesgo y mitigación>

## Validación
Comando: `<comando que valida la unidad en local>`
""",
    Artefacto.tasks: """# Tareas: <título de la unidad>

## G1 — <nombre del grupo del plan>
- [ ] T-01: <tarea atómica y verificable> (CA-01)
- [ ] T-02: <...> (CA-01, CA-02)

## G2 — <...>
- [ ] T-03: <...> (CA-02)
""",
}

SECCIONES_OBLIGATORIAS = {
    Artefacto.spec: ("Problema", "Alcance", "Fuera de alcance", "Criterios de aceptación"),
    Artefacto.plan: ("Enfoque", "Grupos", "Riesgos", "Validación"),
    Artefacto.tasks: (),
}

_SECCION = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_CRITERIO = re.compile(r"^\s*[-*]\s+\**(CA-\d{2,3})\**\s*[:.—-]\s*(.+?)\s*$", re.MULTILINE)
_GRUPO_PLAN = re.compile(r"^###\s+(G\d{1,2})\s*[—:-]\s*(.+?)\s*$", re.MULTILINE)
_GRUPO_TASKS = re.compile(r"^##\s+(G\d{1,2})\s*[—:-]\s*(.+?)\s*$", re.MULTILINE)
_ARCHIVOS = re.compile(r"^\s*Archivos\s*:\s*(.+?)\s*$", re.MULTILINE | re.IGNORECASE)
_COMANDO = re.compile(r"^\s*Comando\s*:\s*`([^`]+)`\s*$", re.MULTILINE | re.IGNORECASE)
_TAREA = re.compile(
    r"^\s*[-*]\s+\[[ xX]\]\s+(T-\d{2,3})\s*[:.—-]\s*(.+?)"
    r"(?:\s*\(((?:CA-\d{2,3})(?:\s*,\s*CA-\d{2,3})*)\))?\s*$",
    re.MULTILINE,
)
_GLOB = re.compile(r"`([^`]+)`")


class GrupoPlan(BaseModel):
    id: str
    nombre: str
    archivos: list[str] = Field(default_factory=list)


class GrupoTareas(BaseModel):
    id: str
    nombre: str
    tareas: list[Tarea] = Field(default_factory=list)


class Extraido(BaseModel):
    """Lo que el motor lleva de una fase a la siguiente."""

    criterios: list[Criterio] = Field(default_factory=list)
    grupos_plan: list[GrupoPlan] = Field(default_factory=list)
    comando_validacion: str | None = None
    grupos_tareas: list[GrupoTareas] = Field(default_factory=list)


@dataclass
class Validacion:
    extraido: Extraido
    problemas: list[tuple[str, str]] = field(default_factory=list)  # (sección, problema)

    @property
    def valida(self) -> bool:
        return not self.problemas


def _secciones(texto: str) -> set[str]:
    return {m.group(1).strip().lower() for m in _SECCION.finditer(texto)}


def _bloques(texto: str, patron: re.Pattern[str]) -> list[tuple[re.Match[str], str]]:
    marcas = list(patron.finditer(texto))
    bloques = []
    for i, m in enumerate(marcas):
        fin = marcas[i + 1].start() if i + 1 < len(marcas) else len(texto)
        siguiente = _SECCION.search(texto, m.end())
        if patron is _GRUPO_PLAN and siguiente and siguiente.start() < fin:
            fin = siguiente.start()
        bloques.append((m, texto[m.end() : fin]))
    return bloques


def validar(artefacto: Artefacto, texto: str, previo: Extraido | None = None) -> Validacion:
    """Valida la forma y extrae datos; ``previo`` trae lo extraído en fases anteriores."""

    previo = previo or Extraido()
    v = Validacion(extraido=previo.model_copy(deep=True))
    if len(texto) > MAX_CARACTERES[artefacto]:
        v.problemas.append(("(documento)", f"supera {MAX_CARACTERES[artefacto]} caracteres"))
    presentes = _secciones(texto)
    for s in SECCIONES_OBLIGATORIAS[artefacto]:
        if s.lower() not in presentes:
            v.problemas.append((s, f"falta la sección '## {s}'"))

    if artefacto == Artefacto.spec:
        _validar_spec(texto, v)
    elif artefacto == Artefacto.plan:
        _validar_plan(texto, v)
    else:
        _validar_tasks(texto, v)
    return v


def _validar_spec(texto: str, v: Validacion) -> None:
    vistos: dict[str, str] = {}
    for m in _CRITERIO.finditer(texto):
        cid, cuerpo = m.group(1), m.group(2)
        if cid in vistos:
            v.problemas.append(("Criterios de aceptación", f"{cid} está repetido"))
            continue
        vistos[cid] = cuerpo[:2000]
    if not vistos:
        v.problemas.append(("Criterios de aceptación", "no hay criterios con forma '- CA-NN: texto'"))
    v.extraido.criterios = [Criterio(id=c, texto=t) for c, t in vistos.items()]


def _validar_plan(texto: str, v: Validacion) -> None:
    grupos: list[GrupoPlan] = []
    for m, cuerpo in _bloques(texto, _GRUPO_PLAN):
        gid = m.group(1)
        if any(g.id == gid for g in grupos):
            v.problemas.append(("Grupos", f"{gid} está repetido"))
            continue
        archivos_m = _ARCHIVOS.search(cuerpo)
        archivos = _GLOB.findall(archivos_m.group(1)) if archivos_m else []
        malos = [a for a in archivos if a.startswith("/") or ".." in a.split("/")]
        if not archivos:
            v.problemas.append((f"Grupos/{gid}", "falta la línea 'Archivos: `ruta`' con al menos una ruta"))
        for a in malos:
            v.problemas.append((f"Grupos/{gid}", f"ruta no relativa al repositorio: {a}"))
        grupos.append(
            GrupoPlan(id=gid, nombre=m.group(2)[:200], archivos=[a for a in archivos if a not in malos])
        )
    if not grupos:
        v.problemas.append(("Grupos", "no hay grupos con forma '### G1 — nombre'"))
    comando = _COMANDO.search(texto)
    if not comando:
        v.problemas.append(("Validación", "falta la línea 'Comando: `...`'"))
    v.extraido.grupos_plan = grupos
    v.extraido.comando_validacion = comando.group(1).strip()[:2000] if comando else None


def _validar_tasks(texto: str, v: Validacion) -> None:
    criterios = {c.id for c in v.extraido.criterios}
    grupos_plan = {g.id for g in v.extraido.grupos_plan}
    grupos: list[GrupoTareas] = []
    vistas: set[str] = set()
    cubiertos: set[str] = set()
    for m, cuerpo in _bloques(texto, _GRUPO_TASKS):
        gid = m.group(1)
        if grupos_plan and gid not in grupos_plan:
            v.problemas.append((gid, f"{gid} no existe en el plan"))
        tareas: list[Tarea] = []
        for t in _TAREA.finditer(cuerpo):
            tid, desc, cas = t.group(1), t.group(2), t.group(3)
            if tid in vistas:
                v.problemas.append((gid, f"{tid} está repetida"))
                continue
            vistas.add(tid)
            citados = [c.strip() for c in cas.split(",")] if cas else []
            desconocidos = [c for c in citados if c not in criterios]
            if not citados:
                v.problemas.append((gid, f"{tid} no cita ningún CA-NN"))
            for c in desconocidos:
                v.problemas.append((gid, f"{tid} cita {c}, que no está en el spec"))
            validos = [c for c in citados if c in criterios]
            cubiertos.update(validos)
            tareas.append(Tarea(id=tid, descripcion=desc[:2000], criterios=validos))
        if not tareas:
            v.problemas.append((gid, "grupo sin tareas '- [ ] T-NN: ...'"))
        grupos.append(GrupoTareas(id=gid, nombre=m.group(2)[:200], tareas=tareas))
    if not grupos:
        v.problemas.append(("(documento)", "no hay grupos con forma '## G1 — nombre'"))
    for c in sorted(criterios - cubiertos):
        v.problemas.append(("cobertura", f"{c} no lo cubre ninguna tarea"))
    for g in sorted(grupos_plan - {g.id for g in grupos}):
        v.problemas.append(("cobertura", f"el grupo {g} del plan no tiene tareas"))
    v.extraido.grupos_tareas = grupos


def hallazgos_estructura(fase: GateFase, v: Validacion, primer_id: int = 1) -> list[Hallazgo]:
    return [
        Hallazgo(
            id=f"H-{primer_id + i}",
            gate=fase,
            lente="estructura",
            severidad=Severidad.alta,
            titulo=problema[:200],
            cita=Cita(seccion=seccion[:200]),
            evidencia=f"Validador determinista: {problema}"[:4000],
            propuesta="Ajustar el artefacto a la plantilla de la orden.",
        )
        for i, (seccion, problema) in enumerate(v.problemas)
    ]
