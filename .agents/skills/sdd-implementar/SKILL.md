---
name: sdd-implementar
description: "Use when: ejecutar las tareas pendientes de un tasks.md — escribir el código (con fan-out por grupos si el plan lo permite), marcar checkboxes, verificar contra los criterios y cerrar la unidad. Keywords: implementar, ejecutar tareas, codear, materializar el plan."
compatibility: "Claude-first (fan-out por grupos). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Implementar

Fase 4 del desarrollo Spec-Driven. Ejecuta las tareas pendientes de `tasks.md`, manteniendo el checklist como estado vivo, y cierra la unidad tras validar y pasar el gate de código.

**Prerequisito:** `tasks.md` existe y pasó su gate. En **modo semi-autonomo**, además: el humano aprobó el `paquete-aprobacion.md`. Sin esa aprobación esta skill no arranca — es el único checkpoint duro de ese modo. En **modo supervisado** no existe paquete: el checkpoint duro es la aprobación del mandato, verificada corriendo `.spec/scripts/record_mandate_validation.py --unit <ruta> --phase implement` (corre `validate_mandate.py` y anexa la entrada a `validaciones_mandato`; el exit del validador queda dentro de esa entrada) — exit ≠ 0 dentro de la entrada ⇒ no implementar y reportar los códigos.

Respeta el `plan.md` aprobado; si hay que desviarse, documentarlo en "Notas de implementación" de `tasks.md`.

## Cuándo Usar / NO Usar

| Usar | NO Usar |
|------|---------|
| Ejecutar tareas pendientes de una unidad | Aún no hay tasks → `sdd-tareas` |
| Cerrar una unidad tras validar | Replanificar de cero → `sdd-planificar` |

## Flujo

### 1. Retomar contexto
- Leer `_estado.yaml`, `tasks.md`, `plan.md`, `bitacora.md`. Identificar las tareas `[ ]` pendientes y sus dependencias.
- Si `tasks.md` declara `> origen: determinista` (o `determinista+forzado-humano`), correr `python3 .spec/scripts/derive_tasks.py verify --tasks <ruta>/tasks.md` **antes** de tocar ninguna tarea (CA-09): si el `tasks_sha256` no coincide, el script ya reescribió la línea a `determinista+forzado-humano` — no se pierde en silencio que hubo edición manual tras la derivación. No bloquea la implementación; solo registra. Si `origen: redactado`, no aplica.
- En modo semi-autonomo: verificar `_estado.yaml > aprobacion_paquete > decision`.
  - `aprobado` → continuar.
  - `aprobado-con-cambios` → aplicar primero lo que pidió el humano en la fase que corresponda.
  - vacío o `rechazado` → **no implementar**. Si el paquete ni siquiera existe, redactarlo desde la plantilla, dejar `fase: aprobacion` y pedir la decisión. Es el único checkpoint duro de este modo: nunca se salta por haber entrado directo a esta skill.
- En modo supervisado: correr `.spec/scripts/record_mandate_validation.py --unit <ruta> --phase implement` — `exit: 0` dentro de la entrada anexada → continuar; `exit` ≠ 0 → **no implementar**, reportar los códigos de salida. Es el checkpoint duro de este modo: no hay `paquete-aprobacion.md` que redactar ni decisión que pedir aquí — la marca del mandato es del conductor, no de esta skill.

### 2. Ejecutar

Para cada grupo, correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-implementador`
(o, si el plan marcó ese grupo `Complejidad: complejo`, con `--complex`
agregado) y lanzar el Agent con el `subagent_type` y el `model` de esa
salida; si el comando falla, parar y reportarlo (CA-33). Lanzar siempre
subagentes así resueltos — nunca implementar inline, para que el trabajo
corra en el modelo y el rol asignados, con o sin fan-out.

**Reuso de agentes** (0114, adición de alcance del 2026-09-20 sobre insight
de `/insights`): antes de lanzar un agente nuevo para un grupo que ya tiene
uno vivo (por ejemplo, al retomar tras una interrupción), consultar
`ListAgents` y retomarlo con `SendMessage` en vez de generar un duplicado —
un subagente duplicado sobre el mismo ítem de trabajo cuesta un `TaskStop` y
retrabajo, sin ningún beneficio.

**Si el plan declaró grupos paralelizables** (y son más de uno): un agente
implementador por grupo, **en paralelo**, cada uno con `isolation: worktree`
para que no compitan por el árbol de trabajo. Máximo 4 grupos.

**Si el plan declaró grupo único** (cambios acoplados): un solo agente
implementador, sin `isolation: worktree` — no hay con quién competir por el
árbol.

**Antes de invocar el fan-out con `isolation: worktree`**, resolver en el
árbol del coordinador el commit sobre el que se está trabajando (`git
rev-parse HEAD`) y pasárselo explícitamente a cada agente como "commit
esperado" — el worktree hijo **no lo hereda por sí solo**. Motivo: `isolation:
worktree` ancla el worktree hijo siempre al repositorio principal (su ruta de
filesystem y su rama activa, normalmente `master`), sin importar el CWD ni el
`HEAD` de quien invoca, incluso si el coordinador corre dentro de otro
worktree con estado que aún no llegó a `master` (por ejemplo, artefactos de
una unidad recién creada sin commitear). Ese estado sigue existiendo en la
base de objetos compartida del repo — todos los worktrees comparten `.git` —
pero el agente del fan-out no lo ve a menos que se le diga dónde buscarlo.

En ambos casos, cada agente recibe: su grupo de tareas, `plan.md`, los
`CA-NN` que cubre y — cuando corre con `isolation: worktree` — el commit
esperado resuelto arriba. El `subagent_type`/`model` de cada grupo ya salió
resuelto arriba: para un grupo `Complejidad: complejo`, `resolve --role
sdd-implementador --complex` devuelve la variante `sdd-implementador-xhigh`
(su `effort: xhigh` vive fijo en el frontmatter de esa variante, no en un
parámetro de invocación); el resto de grupos usa el `sdd-implementador` base
con el modelo/effort que declare el perfil vigente.

**Verificación obligatoria de cada agente del fan-out con `isolation:
worktree`**, como primer paso antes de tocar ninguna tarea: comparar el
estado de la unidad en su propio worktree contra el commit esperado recibido
del coordinador (por ejemplo, `git show <commit-esperado>:<ruta> | diff -
<ruta>` sobre `tasks.md`, `plan.md` y los demás artefactos de la unidad). Si
falta o difiere, recuperarlo con `git checkout <commit-esperado> --
<rutas-de-la-unidad>` antes de proceder — **nunca** reconstruir el artefacto a
ojo. Si el commit esperado ni siquiera es alcanzable en ese worktree (`git
cat-file -e <commit-esperado>` falla, objeto no encontrado), el agente
**reporta bloqueo** y no improvisa.

Tras recoger el resultado de todos los grupos:
- Registrar en `_estado.yaml > modelo_ejecucion > implementar` una entrada por
  grupo con lo realmente invocado (`grupo`, `subagente` — `sdd-implementador`
  o `sdd-implementador-xhigh` —, `modelo`, `perfil` vigente —
  `_estado.yaml > perfil`, o `estandar` si no está fijado—, `complejidad`).
  Auditoría propia de este repo, ver `.spec/MODELO-AGENTES.md`.
- Si algún agente marcó una tarea bloqueada: `_estado.yaml > estado: bloqueado`, documentar en bitácora y detener.
- Los cambios destructivos que un agente haya reportado sin ejecutar (borrar archivos, migrar datos, reescribir historia) se confirman con el humano **aunque el paquete esté aprobado**: la aprobación cubre el plan, no cada acción irreversible. En **modo supervisado** no hay humano en la sesión: salvo una operación pre-decidida y nombrada en el mandato (`## Delegaciones`), todo cambio destructivo es parada tipificada (`.spec/PARADAS-SUPERVISADO.md`), nunca una confirmación inline.

### 3. Validar
- Ejecutar el `comando_validacion` de `_estado.yaml` / `plan.md` (build/typecheck/tests/validate del repo).
- Si falla: corregir y repetir. No se avanza al gate con la validación en rojo.

### 4. Gate de código — incluye la verificación de contexto fresco
- Invocar `sdd-gate` con `fase: codigo` y el tier de `_estado.yaml > riesgo`. Corre **sin intervención humana**: el humano ya decidió en su checkpoint.
- Los críticos del gate son agentes de **contexto fresco, distintos de quien implementó**: no vieron la conversación que produjo el código, solo `spec.md`, `tasks.md` y el diff. Su lente obligatorio L1 recorre los `CA-NN` uno por uno buscando la evidencia concreta (archivo y línea) de cada uno.
- Por eso la verificación no se hace antes, aparte, por el propio implementador: quien escribió el código es el peor juez de si cumple el criterio, porque sabe lo que *quiso* hacer. El crítico solo ve lo que quedó.
- Veredicto `escalado` ⇒ **la unidad no se cierra**: `estado: bloqueado`, hallazgos en `_estado.yaml`, detalle en bitácora, y se reporta al humano. En **modo supervisado** no hay humano en la sesión: en vez de reportar, devuelve el control a `sdd-supervisado`, que congela el mandato.

### 5. Cerrar
- Marcar el bloque "Validación final" de `tasks.md`.
- `_estado.yaml`: `fase: done`, `estado: completado`, `actualizado`.
- Anexar entrada de cierre a `bitacora.md`: qué se implementó, qué encontró el gate de código, qué quedó fuera.

## Output

Código implementado + `tasks.md` con checkboxes al día + `_estado.yaml` (fase=done si cerró, con el veredicto del gate de código) + entrada en `bitacora.md`.
