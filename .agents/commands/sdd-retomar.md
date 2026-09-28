---
description: "SDD — retomar una unidad existente (de otra sesión)"
argument-hint: "[id/slug de la unidad, opcional]"
---

Ejecuta la skill canónica `sdd-retomar` (`.agents/skills/sdd-retomar/SKILL.md`) siguiendo el protocolo de Desarrollo Spec-Driven definido en `AGENTS.md`. Reconstruye el estado desde los artefactos en disco antes de continuar, incluidos `modo`, `riesgo` y el veredicto de los gates ya corridos.

Unidad: $ARGUMENTS
