---
description: "SDD — correr el gate de validación y refinamiento sobre un artefacto de la unidad"
argument-hint: "[id/slug de la unidad] [spec|plan|tasks|codigo]"
---

Ejecuta la skill canónica `sdd-gate` (`.agents/skills/sdd-gate/SKILL.md`): capa determinista, recuperación de gobernanza vía MCP, panel de críticos en paralelo y bucle de refinamiento, hasta converger o escalar.

El tier (número de críticos e iteraciones) sale de `_estado.yaml > riesgo`. El veredicto se persiste en `_estado.yaml > gates > <fase>` y el detalle en `bitacora.md`.

Si no se indica la fase, usar la fase actual de `_estado.yaml`.

Unidad y fase: $ARGUMENTS
