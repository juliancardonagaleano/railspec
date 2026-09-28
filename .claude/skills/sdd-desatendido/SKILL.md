---
name: sdd-desatendido
description: "Use when: conducir las unidades de un plan maestro bajo `modo: desatendido` — 1 aprobación del mandato (sección `## Aprobación` del plan), sin checkpoints intermedios, con gates que escalan como `auto-deferred` (la unidad queda diferida, las demás del plan siguen) y las paradas tipificadas de `.spec/PARADAS-SUPERVISADO.md`. Does NOT aplicar a unidades `interactivo` ni `semi-autonomo` (esas van por `sdd-orquestar`), ni a `supervisado` (`sdd-supervisado`, que congela el mandato entero ante un gate escalado en vez de diferir solo la unidad), ni aprobar el mandato. Keywords: desatendido, plan maestro, mandato, aprobación upfront, auto-deferred, limpieza masiva, reseeding, catch-up."
compatibility: "IArk — Claude-first (unidades de un plan maestro, 1 aprobación upfront)"
license: "Proprietary - IArk"
metadata:
  user-invocable: "true"
---

# sdd-desatendido — Skill

> Orquesta las unidades de un plan maestro bajo `modo: desatendido`, con 1 aprobación upfront del mandato y sin checkpoints intermedios.

## Cuándo usar

- Cuando las unidades están amparadas por el mismo plan maestro (`.spec/planes/<id>/plan.md`, `## Unidades miembro`) y deben correr su ciclo SDD completo sin checkpoints intermedios, bajo una sola aprobación del mandato (`## Aprobación`, `## Estado: aprobado`).
- Patrones: limpieza masiva, reseeding, catch-up, partición de unidades grandes.

## NO usar

- Cuando el impacto de cada unidad requiere revisión humana individual → usar `modo: semi-autonomo` con 1 paquete por unidad.
- Cuando las unidades no comparten plan maestro → usar `modo: interactivo` con gates humanos.
- Cuando un gate escalado debe congelar el mandato entero, no solo diferir la unidad → usar `modo: supervisado`.

## Flujo

1. **Validar el mandato**: `python3 .spec/scripts/validate_mandate.py --plan <id-del-plan-maestro>` en `PASS` — la unidad declara `mandato: <id-del-plan-maestro>` en su `_estado.yaml`, está listada en `## Unidades miembro` del plan, y el plan está `## Estado: aprobado` con `## Aprobación` al día (hash vigente). Sin esto, abortar con parada tipificada.
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
- `unidad-amparada-fallida` (auto-deferred): si una unidad tiene un gate que escala, marcar `escalado: auto-deferred` y continuar con las demás unidades del plan maestro.

## Output

- El plan maestro con `## Aprobación` vigente (hash al día) y `## Estado: aprobado`.
- N unidades con `_estado.yaml > mandato: <id-del-plan-maestro>, modo: desatendido, fase: done`.
- 1 reporte final.
