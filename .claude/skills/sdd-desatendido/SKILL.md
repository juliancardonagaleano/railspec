---
name: sdd-desatendido
description: "Use when: conducir las unidades amparadas por un mismo mandato bajo `modo: desatendido` — 1 aprobación del mandato, sin checkpoints intermedios, con gates que escalan como `auto-deferred` (la unidad queda diferida, las demás del mandato siguen) y las paradas tipificadas de `.spec/PARADAS-SUPERVISADO.md`. Does NOT aplicar a unidades `interactivo` ni `semi-autonomo` (esas van por `sdd-orquestar`), ni a `supervisado` (`sdd-supervisado`, que congela el mandato entero ante un gate escalado en vez de diferir solo la unidad), ni aprobar el mandato. Keywords: desatendido, mandato, aprobación upfront, auto-deferred, limpieza masiva, reseeding, catch-up."
compatibility: "Claude-first (unidades amparadas por un mismo mandato, 1 aprobación upfront)"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# sdd-desatendido — Skill

> Orquesta las unidades amparadas por un mismo mandato bajo `modo: desatendido`, con 1 aprobación upfront del mandato y sin checkpoints intermedios.

## Cuándo usar

- Cuando las unidades comparten el mismo mandato (`.spec/units/<NNNN-slug>/mandato.md`, `## Unidad amparada`) y deben correr su ciclo SDD completo sin checkpoints intermedios, bajo una sola aprobación del mandato (`## Aprobación`, `## Estado: aprobado`).
- Patrones: limpieza masiva, reseeding, catch-up, partición de unidades grandes.

## NO usar

- Cuando el impacto de cada unidad requiere revisión humana individual → usar `modo: semi-autonomo` con 1 paquete por unidad.
- Cuando las unidades no comparten mandato → usar `modo: interactivo` con gates humanos.
- Cuando un gate escalado debe congelar el mandato entero, no solo diferir la unidad → usar `modo: supervisado`.

## Flujo

1. **Validar el mandato**: `python3 .spec/scripts/validate_mandate.py --unidad <dir-unidad>` en `PASS` — la unidad tiene `mandato: ""` y su propio archivo `mandato.md` (o `plan.md`) en `## Estado: aprobado` con `## Aprobación` al día (hash vigente). Sin esto, abortar con parada tipificada.
2. **Para cada unidad amparada**:
   a. **spec**: redactar `spec.md` (o recuperarlo si la unidad ya tiene uno aprobado).
   b. **gate de spec**: tier declarado en `_estado.yaml > riesgo`; presupuesto de iteraciones resuelto con `python3 .spec/scripts/effort_profile.py resolve --unit <ruta> --gate --tier <riesgo>` — techo duro, sin iteraciones excepcionales (`sdd-gate/SKILL.md` § Convergencia del gate). Veredicto `escalado` → `escalado: auto-deferred` (cerrar el gate así, continuar con la siguiente unidad).
   c. **plan**: redactar `plan.md`.
   d. **gate de plan**: tier declarado; capa determinista (0117-D6) si es fase plan/tasks, panel si es implement.
   e. **tasks**: redactar `tasks.md` con trazabilidad CA → tarea.
   f. **gate de tasks**: capa determinista.
   g. **implement**: ejecutar tareas, marcar checkboxes.
   h. **gate de código**: tier declarado; críticos con contexto fresco.
   i. **cerrar**: `_estado.yaml > fase: done, estado: completado`; entrada en `bitacora.md`.
3. **Reporte final**: lista de unidades cerradas, gates escalados (auto-deferred), tiempo total, decisiones tomadas.

## Paradas tipificadas (heredadas + nuevas)

- Las 14 condiciones de `.spec/PARADAS-SUPERVISADO.md`.
- `plan-incompleto` (abort): si una unidad falla de modo que afecta a las demás unidades amparadas por el mismo plan, abortar el resto.
- `unidad-amparada-fallida` (auto-deferred): si una unidad tiene un gate que escala, marcar `escalado: auto-deferred` y continuar con las demás unidades del mandato.

## Output

- El mandato (archivo dentro de la propia unidad o plan archivado bajo `.spec/planes-archive/`) con `## Aprobación` vigente (hash al día) y `## Estado: aprobado`.
- N unidades con `_estado.yaml > mandato: "", modo: desatendido, fase: done`.
- 1 reporte final.
