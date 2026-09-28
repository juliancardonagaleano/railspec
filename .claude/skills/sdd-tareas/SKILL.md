---
name: sdd-tareas
description: "Use when: descomponer un plan.md aprobado en un tasks.md — checklist atómico con dependencias y cobertura de criterios, que sirve de fuente de verdad resumible. Does NOT implement. Keywords: tareas, checklist, descomposición, todo."
compatibility: "IArk — Claude-first (subagentes en paralelo)"
license: "Proprietary - IArk"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Tareas

Fase 3 del desarrollo Spec-Driven. Descompone un `plan.md` aprobado en `tasks.md`: un checklist atómico que es la **fuente de verdad resumible** del progreso.

**Prerequisito:** `plan.md` existe y pasó su gate. **NO implementa.**

## Cuándo Usar / NO Usar

| Usar | NO Usar |
|------|---------|
| Convertir el plan en pasos accionables | No hay plan → `sdd-planificar` |
| Ordenar tareas con dependencias | Ejecutar tareas → `sdd-implementar` |

## Flujo

### 1. Retomar contexto
- Leer `_estado.yaml`, `plan.md`, `spec.md`. Verificar fase `plan` y que su gate no quedó `escalado`.

### 2. Delegar la redacción

Correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-tareas-redactor`
y lanzar el Agent con el `subagent_type` y el `model` de esa salida; si el
comando falla, parar y reportarlo (CA-33). Invocar ese subagente con: la ruta
de la unidad, `spec.md` y `plan.md`. El redactor descompone el plan en
`tasks.md` con trazabilidad completa a los `CA-NN`, y deja
`_estado.yaml`/`bitacora.md` al día — no repetir aquí ese trabajo. Si reporta
algún `CA-NN` huérfano, no continuar al gate: devolverle el hallazgo para que
lo corrija antes de seguir.

Registrar además en `_estado.yaml > modelo_ejecucion > tareas` qué se invocó
realmente (`subagente`, `modelo` y `perfil` vigente —
`_estado.yaml > perfil`, o `estandar` si no está fijado—, `en: <ahora,
ISO-8601>`) — auditoría, ver `.spec/MODELO-AGENTES.md`.

### 3. Gate
- Invocar `sdd-gate` con `fase: tasks` y el tier de `_estado.yaml > riesgo`.
- Si el veredicto es `escalado`: parar y presentar los hallazgos al humano.

### 4. Paquete de aprobación (solo modo semi-autonomo)

Si `_estado.yaml > modo` es `semi-autonomo`, redactar `paquete-aprobacion.md` desde
la plantilla y **pausar** pidiendo la decisión humana; registrar el resultado en
`_estado.yaml > aprobacion_paquete` y dejar `fase: aprobacion` mientras se
espera.

Esto vale tanto si el flujo lo conduce `sdd-orquestar` como si el usuario va
invocando las fases una a una: el checkpoint no puede depender de por dónde se
entró. Si `sdd-orquestar` ya lo redactó, no duplicarlo.

## Output

`.spec/units/<NNNN-slug>/tasks.md` + `_estado.yaml` (fase=tasks, con veredicto del gate) + entrada en `bitacora.md`.
