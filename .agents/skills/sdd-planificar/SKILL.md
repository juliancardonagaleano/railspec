---
name: sdd-planificar
description: "Use when: traducir un spec.md aprobado en un plan técnico (plan.md) — enfoque, archivos a tocar, reutilización, grupos paralelizables, riesgos, comando de validación. Does NOT write code ni descompone en checklist (eso es sdd-tareas). Keywords: plan técnico, diseño, cómo implementar."
compatibility: "IArk — Claude-first (subagentes en paralelo)"
license: "Proprietary - IArk"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Planificar

Fase 2 del desarrollo Spec-Driven. Convierte un `spec.md` aprobado en un `plan.md`: el **CÓMO** técnico.

**Prerequisito:** `spec.md` existe, pasó su gate y (en modo interactivo) está aprobado. **NO implementa. NO crea el checklist (eso es `sdd-tareas`).**

## Cuándo Usar / NO Usar

| Usar | NO Usar |
|------|---------|
| Diseñar el enfoque técnico de una unidad | Aún no hay spec → `sdd-especificar` |
| Listar archivos a tocar y riesgos | Descomponer en tareas → `sdd-tareas` |

## Flujo

### 1. Retomar contexto
- Leer `_estado.yaml`, `spec.md` y `bitacora.md` de la unidad.
- Verificar que la fase actual sea `spec` y que su gate no haya quedado `escalado`. Si no, detener y reportar.

### 2. Explorar en paralelo (antes de diseñar)

Correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-explorador`
y lanzar el Agent con el `subagent_type` y el `model` de esa salida; si el
comando falla, parar y reportarlo (CA-33). Lanzar **en una sola tanda
paralela** un subagente `sdd-explorador` por cada vía, todos con ese mismo
`subagent_type`/`model`; cada uno devuelve hallazgos con rutas, no opiniones:

| Vía | Busca | Devuelve |
|---|---|---|
| Reutilización | Funciones, utilidades, patrones y artefactos existentes que cubren parte del cambio | `ruta:linea` + qué resuelve |
| Gobernanza | Mandatos que restringen el enfoque, vía MCP | ids + mandato aplicable |
| Riesgos | Consumidores del artefacto a tocar, estado persistido, compatibilidad hacia atrás | qué se rompe y dónde |

El explorador de gobernanza usa el patrón obligatorio:
- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada id de `_estado.yaml > governance_refs` → **APLICAR**: traducir cada mandato a una restricción concreta del enfoque.
- **Sin fallback**: MCP caído ⇒ parar y decirlo.

### 3. Delegar la redacción

Correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-planificar-redactor`
y lanzar el Agent con el `subagent_type` y el `model` de esa salida; si el
comando falla, parar y reportarlo (CA-33). Invocar ese subagente con: la
ruta de la unidad, `spec.md`, `_estado.yaml`, y los hallazgos de los tres
exploradores del paso 2. El redactor escribe `plan.md` (incluida la marca de
`Complejidad` por grupo cuando aplique) y deja `_estado.yaml >
comando_validacion` y `bitacora.md` al día — no repetir aquí ese trabajo.

Registrar además en `_estado.yaml > modelo_ejecucion > planificar` qué se
invocó realmente (`subagente`, `modelo` y `perfil` vigente —
`_estado.yaml > perfil`, o `estandar` si no está fijado—, `en: <ahora,
ISO-8601>`) — auditoría, ver `.spec/MODELO-AGENTES.md`.

### 4. Gate
- Invocar `sdd-gate` con `fase: plan` y el tier de `_estado.yaml > riesgo`.
- Si el veredicto es `escalado`: parar y presentar los hallazgos al humano.

### 5. Checkpoint humano (solo modo interactivo)
- Presentar `plan.md` ya refinado y pedir aprobación antes de `sdd-tareas`.
- Mostrar "perfil vigente: `<perfil>`" (`_estado.yaml > perfil`, `estandar` si
  no está fijado). Aceptar `/sdd-perfil <nombre>` en este mismo checkpoint
  (CA-28/29); el perfil nuevo rige desde la siguiente invocación de subagente.
- Si el humano pide cambios: volver al paso 3 con su feedback como insumo adicional para el redactor.
- En modo semi-autonomo no se pausa aquí.

## Output

`.spec/units/<NNNN-slug>/plan.md` + `_estado.yaml` (fase=plan, con veredicto del gate) + entrada en `bitacora.md`.
