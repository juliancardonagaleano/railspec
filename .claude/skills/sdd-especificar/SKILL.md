---
name: sdd-especificar
description: "Use when: arrancar una unidad de trabajo Spec-Driven para evolucionar este repo — capturar QUÉ y POR QUÉ en spec.md antes de tocar código. Does NOT design the technical approach (eso es sdd-planificar). Keywords: especificación, spec, nueva tarea, plan de trabajo, dejar de vibe coding."
compatibility: "Claude-first (subagentes en paralelo)"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Especificar

Fase 1 del desarrollo Spec-Driven. Captura el **QUÉ** y el **POR QUÉ** de un cambio al framework en un `spec.md`, antes de diseñar el cómo o tocar código. Independiente de cualquier pipeline de producto propio del destino.

**NO diseña el enfoque técnico (eso es `sdd-planificar`). NO implementa.**

## Cuándo Usar / NO Usar

| Usar | NO Usar |
|------|---------|
| Iniciar un cambio no trivial al repo | Diseñar archivos/enfoque → `sdd-planificar` |
| Formalizar una idea antes de codear | Fix trivial de 1 línea (no requiere ceremonia) |
| Registrar criterios de aceptación | Generar artefactos de producto final → pipeline propio del destino |

## Flujo

### 1. Asignar identidad
- Revisar `.spec/units/` y elegir el siguiente `NNNN` secuencial. Crear `.spec/units/<NNNN-slug>/` con slug en kebab-case.
- Copiar las plantillas desde `.spec/_plantillas/` (`spec.md`, `_estado.yaml`, `bitacora.md`).
- Registrar el `modo` y el `riesgo` que fijó el triaje (defaults: `interactivo`, `medio`).

### 2. Delegar la redacción

Correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-especificar-redactor`
y lanzar el Agent con el `subagent_type` y el `model` de esa salida; si el
comando falla, parar y reportarlo (CA-33). Invocar ese subagente con: la ruta
de la unidad, el `modo` y `riesgo` ya fijados, y la petición original del
usuario (o, si es una revisión pedida en el checkpoint del paso 4, el
feedback del humano como insumo adicional). El redactor consulta gobernanza
contra `pce-mcp` sin fallback, escribe `spec.md`, y deja
`_estado.yaml > governance_refs` y `bitacora.md` al día — no repetir aquí ese
trabajo. Si reporta que el MCP no respondió, parar y decirlo: no continuar con
gobernanza inventada.

Registrar además en `_estado.yaml > modelo_ejecucion > especificar` qué se
invocó realmente (`subagente`, `modelo` y `perfil` vigente —
`_estado.yaml > perfil`, o `estandar` si no está fijado—, `en: <ahora,
ISO-8601>`) — auditoría, ver `.spec/MODELO-AGENTES.md`.

### 3. Gate
- Invocar `sdd-gate` con `fase: spec` y el tier de `_estado.yaml > riesgo`.
- Si el veredicto es `escalado`: parar y presentar los hallazgos al humano.

### 4. Checkpoint humano (solo modo interactivo)
- Presentar el `spec.md` **ya refinado por el gate** y pedir aprobación antes de `sdd-planificar`.
- Mostrar "perfil vigente: `<perfil>`" (`_estado.yaml > perfil`, `estandar` si
  no está fijado). Aceptar `/sdd-perfil <nombre>` en este mismo checkpoint
  (CA-28/29); el perfil nuevo rige desde la siguiente invocación de subagente.
- Si el humano pide cambios: volver al paso 2 con su feedback como insumo adicional para el redactor.
- En modo semi-autonomo no se pausa aquí: el flujo continúa y el humano decidirá con el paquete de aprobación.

## Output

`.spec/units/<NNNN-slug>/spec.md` + `_estado.yaml` (fase=spec, con veredicto del gate) + entrada en `bitacora.md`.
