---
description: "SDD — conducir el flujo Spec-Driven completo desde una petición (modo interactivo o semi-autonomo)"
argument-hint: "[descripción del cambio | id/slug para retomar] [--modo interactivo|semi-autonomo] [--riesgo bajo|medio|alto]"
---

Ejecuta la skill canónica `sdd-orquestar` (`.agents/skills/sdd-orquestar/SKILL.md`) siguiendo el protocolo de Desarrollo Spec-Driven de `AGENTS.md`: triar la petición, y si es un cambio no trivial, conducir las fases con su gate de validación.

Modo y tier de riesgo: tomarlos de los flags si vienen en `$ARGUMENTS`; si no, inferirlos en el triaje (defaults: `interactivo`, `medio`) y anunciar cuáles se asumieron.

- `interactivo` — el humano en el bucle: checkpoints tras spec y tras plan.
- `semi-autonomo` — spec, plan y tareas sin intervención; un único checkpoint humano antes de implementar, con el paquete de aprobación.

Los modos `supervisado` y `desatendido` no se fijan aquí: los habilita un mandato aprobado (`sdd-supervisado`, `sdd-desatendido`).

En ambos modos, un gate con veredicto `escalado` detiene el flujo y lo decide el humano.

Petición: $ARGUMENTS
