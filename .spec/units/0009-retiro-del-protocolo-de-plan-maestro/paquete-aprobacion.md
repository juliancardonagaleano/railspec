# Paquete de aprobación — Retiro del protocolo de plan maestro, consolidación en `mandato.md` y migración de U-0002/U-0003

> Artefacto del **modo semi-autonomo**. Único checkpoint duro antes de implementar.

## Petición original

"Retira el protocolo de plan maestro, con la funcionalidad de dependencia entre planes se cubre este requerimiento, por lo tanto en este momento está siendo redundante." + "verificar si pueden existir problemas de drift con lo definido en _estado.yaml".

## Qué se va a hacer (2-5 líneas)

Borrar 3 plantillas redundantes (`plan-maestro.md`, `historial.md`, `paquete-tanda-aprobacion.md`); consolidar `mandato.md` como única plantilla de mandato; declarar el nuevo campo `depende_de:` (block-list) en `_estado.yaml`; migrar U-0002 y U-0003 (los únicos planes activos hoy) reasignando su `dependencias:` informal a `depende_de:` y vaciando `mandato:`; archivar sus planes bajo `.spec/planes-archive/`. Limpiar las ~50 referencias residuales a `plan-maestro` en 28+ archivos de `.spec/scripts/` y sus tests, 4 skills SDD (`sdd-desatendido`, `sdd-preflight`, `sdd-supervisado`, `sdd-orquestar`), 3 docs canónicos (`SUPERVISADO.md`, `PARADAS-SUPERVISADO.md`, `AGENTS.md`), y el test de U-0005 (`test_u0005_ca12.py` que asume la existencia de las 4 plantillas — CA-12a/CA-12b cierran el acoplamiento).

**Perfil de esfuerzo:** `estandar` — se puede cambiar con `/sdd-perfil <nombre>` en este checkpoint.

## Criterios de aceptación

| Id | Criterio | Tareas |
|---|---|---|
| CA-01 | `plan-maestro.md` borrado | T1 |
| CA-02 | `historial.md` borrado | T2 |
| CA-03 | `paquete-tanda-aprobacion.md` borrado | T3 |
| CA-04 | `mandato.md` sin referencias a `plan-maestro` | T4 |
| CA-05 | `_estado.yaml` declara `depende_de: []` | T5 |
| CA-06 | Comentario de `mandato:` ya no menciona plan maestro | T5 |
| CA-07 | U-0002 `mandato: ""` (drift-check) | T25 |
| CA-08 | U-0002 `depende_de:` incluye U-0003 | T25 |
| CA-09 | U-0003 `mandato: ""` (drift-check) | T26 |
| CA-10 | U-0003 `depende_de:` incluye U-0002 | T26 |
| CA-11 | Planes U-0002/U-0003 archivados | T27, T28 |
| CA-12a | `test_u0005_ca12.py` sin refs a las 3 borradas | T29 |
| CA-12b | U-0005 `spec.md`/`tasks.md` reformulan a 1 plantilla | T30, T31 |
| CA-13 | Scripts sin refs a `plan-maestro` | T6..T18 |
| CA-14 | 4 skills sin refs a `plan-maestro` | T19..T22 |
| CA-15 | 3 docs sin refs a `plan-maestro` | T23, T24 |
| CA-16 | `pytest .spec/scripts/tests installer -q` exit 0 | Validación |

## Alcance del cambio

- **Archivos a tocar:** ~50 archivos:
  - Borrar 3 plantillas + 2 scripts huérfanos (`append_changelog_line.py`, `supervised-test.sh`).
  - Modificar 1 plantilla (`mandato.md`), `_estado.yaml`, ~25 archivos en `.spec/scripts/` + sus tests, 4 skills SDD, 3 docs, 2 `_estado.yaml` activos (U-0002, U-0003).
  - Archivar 2 planes (U-0002, U-0003) a `.spec/planes-archive/`.
- **Fuera de alcance:**
  - Reescritura del CLI del instalador, materializador, pre-push hook.
  - Retiro o redefinición del modo `desatendido` (sigue operando con `mandato.md` para una sola unidad; PQ-3 lo difiere).
  - Migración de U-0001/U-0004/U-0005 (sus `mandato:` ya están vacíos; no hay drift que migrar).
  - Limpieza de comentarios inline históricos en `_estado.yaml` de unidades cerradas que mencionan `_plan-maestro.md § Changelog`.

## Historial de gates

| Fase | Veredicto | Hallazgos resueltos |
|---|---|---|
| spec | refinado | 1 alta (typo `gobernanza`) + 1 media (CA-12 partido) |
| plan | refinado | 3 alta + 5 media + 3 baja (líneas faltantes en tabla de archivos) |
| tasks | aprobado | 1 media (DD-4 sin tarea explícita → T24b añadida) |

**Correcciones más relevantes:**
1. **CA-08 typo** (`governanza` con `v` vs `gobernanza` con `b`): la grafía correcta está en el bloque `dependencias:` actual de U-0002; el redactor la había escrito mal. Detectado por el crítico L1.
2. **CA-12 partido en CA-12a/CA-12b**: el original mezclaba un comando reproducible con una cláusula narrativa. CA-12a verifica el test, CA-12b verifica la prosa reformulada de U-0005.
3. **Plan refinado con 6 archivos adicionales** (líneas faltantes a tocar en `.spec/scripts/`, 4 skills SDD en lugar de 3): el crítico L4 detectó que el plan no cubría todas las menciones a `plan-maestro` y `plan maestro` (con espacio), incluyendo docstrings de tests y SKILL.md.
4. **T24b añadida**: preservar la prosa del bloque `dependencias:` actual de U-0002/U-0003 en bitacora.md antes del borrado (DD-4).

## Suposiciones tomadas

| # | Pregunta | Suposición | Impacto si es errada |
|---|---|---|---|
| 1 | ¿Qué hacer con el contenido de los planes archivados? | Archivar bajo `.spec/planes-archive/` (DD-2) | Mínimo — si querés migrar a `mandato.md` dentro de la unidad o descartar, requiere tareas adicionales |
| 2 | Forma de `depende_de:` | block-list YAML (`depende_de:\n  - 0003-...`), no inline (DD-1) | Si preferís inline `["..."]`, hay que añadir un parser nuevo |
| 3 | Eliminar rama legacy de `validate_mandate.py` o solo documentar | Eliminar y aceptar `mandato: ""` en `modo: desatendido` (DD-3) | Si querés conservar legacy, hay que añadir tasks para reescribir los branches |
| 4 | IDs concretos de `depende_de:` para U-0002 y U-0003 | U-0002→U-0003; U-0003→U-0002 (DD-4, derivado del bloque `dependencias:` actual) | Si la dependencia es distinta, hay que reescribir las tareas T25/T26 |

## Riesgos aceptados

- **`validate_mandate.py` rechaza `mandato: ""`** (R-1) → DD-3 lo cierra: aceptar `mandato: ""` cuando `modo: desatendido`.
- **Tests preexistentes rompen al retirar ramas legacy** (R-2) → cada fixture `plan-maestro.md` se migra a `plan.md`; verificación por CA-16.
- **`sdd-orquestar` tiene lógica condicional no detectable por grep simple** (R-3) → inspección manual en G3; se reforma línea 120.
- **Materializadores `.claude/` regeneran espejos desde `.agents/`** (R-4) → CA-14 verifica sobre `.agents/` (fuente canónica); espejos se regeneran al final.

## Gobernanza aplicada

`governance_refs`: `[ninguna-aplicable]` — confirmado en gates de spec/plan/tasks con precedentes U-0004 y U-0005.

## Decisión

- [ ] **Aprobar** — implementar según `tasks.md`
- [ ] **Aprobar con cambios** — indicar cuáles
- [ ] **Rechazar** — la unidad queda en `estado: bloqueado`