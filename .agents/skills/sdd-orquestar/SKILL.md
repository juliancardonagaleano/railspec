---
name: sdd-orquestar
description: "Use when: el usuario pide un cambio NO trivial a este repo en lenguaje natural (sin invocar una fase concreta) — conduce el flujo Spec-Driven completo, en modo interactivo o semi-autonomo, con gates entre fases. Does NOT aplicar a análisis, explicaciones ni tareas simples (esas van directas). Keywords: nueva feature, refactor, implementar cambio, plan de trabajo, dejar de vibe coding, modo semi-autonomo."
compatibility: "Claude-first (conduce fases con gates). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Orquestar (coordinador del flujo)

Conduce el flujo Spec-Driven completo a partir de una petición en lenguaje natural, sin que el usuario tenga que invocar cada fase. Se ejecuta **inline** en la conversación (no como subagente aislado) para poder pausar en los checkpoints y recibir la aprobación del usuario.

**NO implementa saltándose fases. NO omite el checkpoint que corresponde al modo. Delega la lógica de cada fase a las skills `sdd-*` y la validación a `sdd-gate`.**

Cada skill de fase delega internamente su contenido pesado (redactar, criticar, implementar, explorar) a un subagente con modelo y effort fijados — ver `.spec/MODELO-AGENTES.md`. Este orquestador no necesita elegir ni verificar ningún modelo: es orquestación barata, corre bien en el modelo que tenga la sesión.

## Triaje (primero, siempre)

Antes de iniciar nada, clasificar la petición (misma tabla de triaje que `.spec/README.md`):

| Petición | Acción |
|---|---|
| Cambio no trivial al repo | Iniciar el flujo (abajo) |
| Análisis / explicación | Responder directo. NO crear unidad SDD. Terminar. |
| Tarea simple (1 línea, rename, typo, bump) | Ejecutar directo. NO crear unidad SDD. Terminar. |
| Ambiguo | Preguntar al usuario antes de decidir |

Un cambio es "**menor**" solo por decisión del **gate** o del **humano** al
rechazar la sugerencia de iniciar SDD — este agente propone SDD para todo lo
no trivial y **no preclasifica** un cambio como menor por su cuenta.

### Fijar modo y tier

Si el triaje da "cambio no trivial", determinar dos parámetros y persistirlos en `_estado.yaml`:

**Modo** — cómo lo pidió el usuario:

| Señal en la petición | Modo |
|---|---|
| "modo semi-autonomo", "sin preguntarme", "hazlo todo y me avisas", una premisa cerrada | `semi-autonomo` |
| Conversación abierta, el usuario está explorando el problema | `interactivo` |
| No hay señal clara | `interactivo` (default conservador) |

**Tier de riesgo** — qué toca el cambio:

| El cambio toca | Tier |
|---|---|
| Contratos públicos, gobernanza, datos persistidos, artefactos que otros repos consumen, seguridad | `alto` |
| Comportamiento interno de un subárbol, superficie de usuario | `medio` |
| Docs, mensajes, tests aislados, cambios reversibles de una sola pieza | `bajo` |

**Perfil de esfuerzo** — cuánto se gasta en producir la corrida (eje
independiente de `riesgo`): `estandar` por default, persistido en
`_estado.yaml > perfil`. Se cambia en cualquier momento con
`/sdd-perfil <nombre>` (skill `sdd-perfil`) — no requiere triaje explícito.

Decir al usuario en la misma línea qué modo, tier y perfil se asumieron. No preguntarlo como ceremonia: es un default anunciado, y él corrige si quiere.

El default es siempre `interactivo`: la unidad solo nace en otro modo cuando el
humano mismo lo fija en la petición inicial (fila `semi-autonomo` de arriba; los
modos bajo mandato — `supervisado`, `desatendido` — no se fijan
aquí, los habilita un mandato aprobado). Convertir el modo de una unidad ya
nacida ocurre después, nunca en este triaje: solo tras la fase `research` o
tras el checkpoint del `spec.md`, y queda registrada como una entrada nueva
en `_estado.yaml > modo_conversion` (`desde`, `hacia`, `en`, `por`, `tras`) más
su línea en `bitacora.md` — ver `.spec/SUPERVISADO.md` § "Modo por decisión
humana y unidades en vuelo".

## Flujo

```
modo interactivo                        modo semi-autonomo
─────────────────                       ──────────────────
sdd-especificar → sdd-gate(spec)        sdd-especificar → sdd-gate(spec)
  ⛔ CHECKPOINT humano                              ↓
sdd-planificar  → sdd-gate(plan)        sdd-planificar  → sdd-gate(plan)
  ⛔ CHECKPOINT humano                              ↓
sdd-tareas      → sdd-gate(tasks)       sdd-tareas      → sdd-gate(tasks)
        ↓                                           ↓
sdd-implementar                         paquete-aprobacion.md
        ↓                                 ⛔ CHECKPOINT humano (único)
sdd-gate(codigo) → cierre               sdd-implementar
                                                    ↓
                                        sdd-gate(codigo) → cierre
```

### Pasos

0. **Investigar (opcional)** — solo si la premisa tiene incógnitas que el spec
   tendría que suponer (comportamiento actual desconocido, integración sin
   documentar, dudas sobre qué existe ya en el repo). Antes de lanzar nada,
   correr `python3 .spec/scripts/effort_profile.py resolve --unit <ruta> --explorers --tier <riesgo>`
   para el número de exploradores y
   `python3 .spec/scripts/effort_profile.py resolve --unit <ruta> --role sdd-explorador`
   para su `subagent_type`/`model`; si cualquiera de los dos comandos falla,
   parar y reportarlo (CA-33) en vez de asumir un valor. Lanzar ese número de
   subagentes `sdd-explorador` **en paralelo**, cada uno por una vía distinta
   (por archivos, por consumidores, por gobernanza vía MCP, por historia de
   git), pasando el `subagent_type`/`model` resueltos, y consolidar en
   `research.md` desde la plantilla: solo hechos con su evidencia. El
   `spec.md` luego **cita** esos hechos en vez de suponerlos. Si el cambio es
   conocido, omitir este paso — no es ceremonia obligatoria.

   La consolidación de `research.md` corre siempre **inline**, en el modelo
   de la sesión — no se delega a un subagente. Al cerrar este paso (se haya
   corrido o se haya omitido), escribir `_estado.yaml > modelo_ejecucion.research`
   con sus dos sub-claves: `exploradores` (`subagente`, `modelo`, `effort`,
   `perfil`, `instancias`, `en`) — vacío/omitido si el paso se omitió — y
   `consolidacion` (`subagente: inline`, `modelo: <modelo de sesión>`,
   `perfil`, `en`), siempre presente.

#### Atajo: fast-track (tier `bajo` + `modo: interactivo`)

Cuando `_estado.yaml > riesgo` es `bajo` **y** `modo` es `interactivo`, el flujo
se reduce al subconjunto mínimo (decisión directa de Julian, `0123-D1`,
2026-09-21, aplicada bajo CR-2):

- se omite el paso 0 (investigación) — el spec cita el código o el `research.md`
  del plan maestro cuando existe;
- se redacta un único artefacto `spec.md` (≤ 120 líneas, presupuesto del
  validador `validate_artifact_size.py spec`) que cubre QUÉ, POR QUÉ, criterios
  `CA-NN` y una sección corta "Enfoque" con archivos a tocar y comando de
  validación;
- el gate corre solo en su **capa determinista** más un crítico Haiku `low`
  multi-lente (un solo agente, una sola iteración — equivalente a la
  consolidación que `0117-D4` ya aplica a tier `bajo` con panel completo);
- el modo interactivo mantiene su único checkpoint humano: tras el gate del
  spec+enfoque, antes de implementar.

El fast-track **no** salta la consulta de gobernanza: el crítico lee los
`governance_refs` declarados en `_estado.yaml` igual que en el flujo estándar.
Si el gate escala, vuelve al flujo completo (pasos 1-5 desde el spec ya
escrito). El criterio de "escalado" del fast-track es el mismo que el flujo
estándar — ningún gate aprueba por agotamiento.

**Modo micro** (formalizado por `0126-modo-micro-para-unidades-chicas`,
2026-09-22): variante aún más liviana del fast-track, gatillada cuando
`spec.md ≤ 50` líneas, `git diff --stat ≤ 60` líneas netas, y el cambio no
toca contrato/comportamiento. La verificación adversarial del gate de código
es `git diff --stat` puro — sin crítico, sin panel. Aplica sobre tier `bajo`
sin reemplazar el fast-track anterior. Ver `.spec/README.md ## Gates de
validación` para los criterios de aplicabilidad.

1. **Especificar** — invocar `sdd-especificar`, luego `sdd-gate` con `fase: spec`.
   - Modo interactivo: **pausar**, mostrar el `spec.md` ya refinado y pedir OK explícito.
   - Modo semi-autonomo: continuar sin pausar.
2. **Planificar** — invocar `sdd-planificar`, luego `sdd-gate` con `fase: plan`.
   - Modo interactivo: **pausar** y pedir OK explícito.
   - Modo semi-autonomo: continuar.
3. **Tareas** — invocar `sdd-tareas`, luego `sdd-gate` con `fase: tasks`.
4. **Checkpoint del modo semi-autonomo** — solo en `modo: semi-autonomo`: redactar
   `paquete-aprobacion.md` desde la plantilla (resumen, criterios con sus
   tareas, historial de gates leído de `_estado.yaml > gates`, suposiciones
   tomadas, riesgos) y **pausar** hasta aprobación explícita.
5. **Implementar** — invocar `sdd-implementar`: fan-out por grupos, validación,
   `sdd-gate` con `fase: codigo` y cierre.

### Qué hacer con el veredicto de cada gate

| Veredicto | Acción |
|---|---|
| `aprobado` / `refinado` | Continuar el flujo |
| `escalado` | **Parar en cualquiera de los dos modos.** Presentar al usuario los hallazgos abiertos y la causa; esperar su decisión |

Un gate `escalado` en modo semi-autonomo no es un fallo del modo: es el diseño. El modo semi-autonomo automatiza el trabajo, no la decisión.

## Persistencia (obligatoria entre pasos)

Cada skill de fase actualiza `_estado.yaml` y anexa a `bitacora.md` 1 línea (`## <ISO> · <fase> · <resumen>`); cada gate escribe su veredicto en `_estado.yaml > gates`. Verificar que ocurrió antes de avanzar: si el estado no se persistió, la unidad no se puede retomar.

## Retoma

Si el usuario pide continuar una unidad existente, delegar primero a `sdd-retomar` (corre forkeada, ver `.spec/MODELO-AGENTES.md`) para reconstruir contexto, y continuar el flujo desde la fase que indique `_estado.yaml`, respetando el `modo` registrado. Si `sdd-retomar` reporta varias unidades candidatas sin poder elegir, preguntar al humano cuál antes de seguir — ella no puede preguntarlo directamente.

## Output

Unidad SDD conducida hasta donde el usuario aprobó (o hasta `fase: done`), con artefactos, veredictos de gate y estado persistidos en `.spec/units/<NNNN-slug>/`.
