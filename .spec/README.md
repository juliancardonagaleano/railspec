# Spec-Driven Development (SDD) — Protocolo

> Protocolo SDD del kit. Este archivo es el runbook de arranque y la
> referencia detallada del protocolo mismo — su fuente canónica es este
> repositorio (`../sdd-kit`), que lo edita directamente; un destino que lo
> recibe instalado no debería editarlo a mano (ver § sdd-kit más abajo).

Este es el protocolo de **desarrollo guiado por especificación** para evolucionar **el repositorio que lo instala**, en lugar de "vibe coding". Es independiente de cualquier pipeline de generación de artefactos de producto propio de ese repositorio: aquel genera producto; SDD evoluciona el propio repositorio (su framework, su código, su andamiaje).

**Claude-first (desde v2.0.0).** El kit usa las primitivas de Claude Code —subagentes en paralelo, worktrees— para los paneles de crítica y el fan-out de implementación. Los adaptadores para otros CLIs (Codex, Copilot, OpenCode) están **deprecados**: el estado sigue viviendo en disco, así que otro CLI puede leer una unidad, pero no ejecuta gates ni fan-out.

## Triaje (primero, siempre)

Antes de actuar, clasificar la petición:

| Petición | Acción |
|---|---|
| Cambio **no trivial** al repo (feature, refactor con impacto, cambio de contrato/comportamiento) | Iniciar SDD (skill `sdd-orquestar`) |
| Análisis ("¿cómo funciona / por qué falla X?") | Responder directo. NO crear unidad SDD |
| Explicación de código | Responder directo. NO crear unidad SDD |
| Tarea simple (fix de 1 línea, rename, typo, bump, ajuste de doc) | Ejecutar directo (con validación mínima). NO crear unidad SDD |
| Ambiguo | Preguntar al usuario antes de decidir |

Un cambio es "**menor**" solo por decisión del **gate** o del **humano** al
rechazar la sugerencia de iniciar SDD — el agente propone SDD para todo lo no
trivial y **no preclasifica** un cambio como menor por su cuenta.

Si es un cambio no trivial, el triaje además fija **dos parámetros** de la unidad:

- **Modo** (`interactivo` | `semi-autonomo`) — cuánta intervención humana quiere el usuario. Default: `interactivo`.
- **Tier de riesgo** (`bajo` | `medio` | `alto`) — cuánta validación merece el cambio. Default: `medio`.

Ambos se persisten en `_estado.yaml`. El tier de riesgo se puede cambiar a mitad de unidad (queda en bitácora). El modo es distinto: toda unidad nace en `modo: interactivo`, salvo que el humano mismo fije otro modo en la petición inicial; convertirla después (a `semi-autonomo` o, bajo mandato, a `supervisado`/`desatendido`) solo se admite tras la fase `research` o tras el checkpoint del `spec.md`, y la conversión queda registrada en `_estado.yaml > modo_conversion` (quién, cuándo, tras qué fase) y en bitácora — ver `.spec/SUPERVISADO.md`.

## Los cuatro modos

Escalera ordenada por cuánta intervención humana exige, de más a menos:

| Modo | Posición del humano | Punto de partida | Checkpoints humanos |
|---|---|---|---|
| `interactivo` | en el bucle | Conversación con el usuario | Tras `spec.md` y tras `plan.md` |
| `semi-autonomo` | aprueba un paquete y la unidad corre sola | Una premisa | **Uno solo**: antes de implementar |
| `supervisado` | sobre el bucle: 0 checkpoints, decide al escalar | Mandato aprobado (`.spec/SUPERVISADO.md`) | Ninguno intermedio; decide en cada parada tipificada |
| `desatendido` | fuera del bucle: 1 aprobación upfront de la tanda | Paquete de tanda aprobado | Ninguno intermedio; gates que escalan cierran `auto-deferred` |

`interactivo`/`semi-autonomo` se fijan en el triaje de cualquier unidad;
`supervisado`/`desatendido` los **habilita** un mandato o un paquete de tanda
ya aprobado — nunca el triaje inicial (ver `.spec/SUPERVISADO.md` y
`.spec/MODELO-AGENTES.md` § Modo desatendido).

> **`interactivo` (modo SDD) no es lo mismo que `interactive` (modo de
> ejecución del orquestador).** El primero es cómo este protocolo produce un
> cambio al repo (checkpoints humanos tras `spec.md`/`plan.md`); el segundo es
> el modo de ejecución del proceso de arquitectura de `orchestrator/`
> (`interactive` | `automated`, ver `AGENTS.md` § Proceso de arquitectura).
> Comparten la palabra, no el concepto: uno es del protocolo de desarrollo de
> este repo, el otro es del dominio de producto que ese mismo repo construye.

En **modo interactivo** el humano co-diseña; cada artefacto llega al checkpoint ya criticado y refinado por su gate.

En **modo semi-autonomo** las fases de documentos corren seguidas y el humano interviene una vez, con el **paquete de aprobación** (`.spec/_plantillas/paquete-aprobacion.md`): resumen del cambio, criterios con sus tareas, historial de gates, suposiciones tomadas y riesgos aceptados. Tras el OK, la implementación corre hasta el cierre con verificación agéntica.

**El modo semi-autonomo automatiza el trabajo, no la decisión.** Se detiene igual si un gate escala, y los cambios destructivos siguen requiriendo confirmación aunque ya exista aprobación del paquete.

Los modos `supervisado` y `desatendido` — sin checkpoints intermedios, con
decisiones delegadas y paradas tipificadas — están documentados en
`.spec/SUPERVISADO.md` y `.spec/PARADAS-SUPERVISADO.md`; se conducen con las
skills `sdd-supervisado` y `sdd-desatendido` respectivamente.

## Unidad de trabajo

Cada cambio no trivial vive en `.spec/units/<NNNN-slug>/`:

| Archivo | Rol | Fase |
|---|---|---|
| `research.md` | Hechos verificados con su evidencia (opcional, solo si hubo fase 0) | Investigar |
| `spec.md` | QUÉ y POR QUÉ + criterios `CA-NN` + governance | Especificar |
| `plan.md` | CÓMO técnico: archivos, decisiones, grupos paralelizables, riesgos, validación | Planificar |
| `tasks.md` | Checklist con estado y cobertura `CA-NN` (fuente de verdad resumible) | Tareas / Implementar |
| `paquete-aprobacion.md` | Lo que se presenta en el checkpoint único (solo modo semi-autonomo) | Aprobación |
| `_estado.yaml` | Estado machine-readable: fase, modo, riesgo, gates, governance | todas |
| `bitacora.md` | Log de handoff append-only entre sesiones | todas |

Plantillas en `.spec/_plantillas/`.

> El kit **no decide** si `.spec/units/` se versiona: cada repo consumidor lo fija en su propio `.gitignore`. Donde no se versionan, las unidades son estado de trabajo local y la retoma funciona entre sesiones de la **misma máquina**, pero no viaja por git entre clones.

## Las fases y sus skills

| Fase | Skill | Slash-command | Produce |
|---|---|---|---|
| 1. Especificar | `sdd-especificar` | `/sdd-especificar` | `spec.md` + `_estado.yaml` |
| 2. Planificar | `sdd-planificar` | `/sdd-planificar` | `plan.md` |
| 3. Tasks | `sdd-tareas` | `/sdd-tareas` | `tasks.md` |
| 4. Implementar | `sdd-implementar` | `/sdd-implementar` | código + `tasks.md` actualizado |
| Gate (todas) | `sdd-gate` | `/sdd-gate` | artefacto refinado + veredicto |
| Orquestar | `sdd-orquestar` | `/sdd` | conduce el flujo según el modo |
| Retomar | `sdd-retomar` | `/sdd-retomar` | reporte de dónde continuar |

```
modo interactivo                        modo semi-autonomo
─────────────────                       ──────────────────
spec.md   → gate(spec)                  spec.md   → gate(spec)
          ← ⛔ aprobación humana                   ↓
plan.md   → gate(plan)                  plan.md   → gate(plan)
          ← ⛔ aprobación humana                   ↓
tasks.md  → gate(tasks)                 tasks.md  → gate(tasks)
          ↓                                       ↓
implementar                             paquete-aprobacion.md
          ↓                                       ← ⛔ aprobación humana (única)
gate(codigo) → cierre                   implementar
                                                  ↓
                                        gate(codigo) → cierre
```

## Gates de validación

Cada fase termina en un gate (skill `sdd-gate`) con dos capas:

1. **Determinista** — sin agentes: plantilla completa, criterios con id, cobertura `CA-NN`, `governance_refs` poblados, comando de validación presente. Si falla, se devuelve a la fase sin gastar agentes.
2. **Agéntica (Self-Improvement Loop)** — generador → panel de críticos con lentes distintos → refinador → re-crítica, hasta converger o agotar iteraciones.

El tier de riesgo dimensiona el panel (**topes duros**):

| Tier | Críticos por iteración | Máx. iteraciones | Verificación adversarial |
|---|---|---|---|
| `bajo` | 1 | 1 | no |
| `medio` | 1 | 1 | no |
| `alto` | 2 | 2 | sí |

Estos valores son los del perfil `estandar` de `.spec/perfiles.yaml`, su
fuente canónica y la única compartida; los perfiles `ligero`/`profundo` los
modulan por unidad (`_estado.yaml > perfil`, comando `/sdd-perfil`). Un
archivo de perfiles local (`.spec/perfiles.<nombre>.yaml`, seleccionado por
puntero — ver skill `sdd-perfil`) es preferencia de clon: no viaja con el
repositorio ni cambia lo versionado.

Todo gate persiste su veredicto en `_estado.yaml > gates > <fase>`:

```yaml
gates:
  spec:
    veredicto: aprobado | refinado | escalado
    iteraciones: 2
    hallazgos: []            # los que quedaron sin resolver
    governance_consultada: si | parcial | no
```

Reglas que no se negocian:

- Un gate **nunca aprueba por agotamiento**: si quedan hallazgos `alta`/`media` sin resolver al terminar, el veredicto es `escalado` y decide un humano. A la inversa, gastar todo el presupuesto **después** de resolver todos los hallazgos no es escalar: es `refinado`.
- Un gate **nunca critica de memoria**: si el MCP de gobernanza no responde, el veredicto es `escalado` con causa `sin-gobernanza`.
- El gate de `codigo` corre **sin intervención humana** antes de `fase: done`, con críticos de contexto fresco distintos de quien implementó.

## Ejecución multi-agente

| Fase | Fan-out |
|---|---|
| Planificar | Exploradores en paralelo: reutilización en el repo, gobernanza aplicable, riesgos |
| Gates | Panel de críticos en paralelo, un lente distinto cada uno |
| Implementar | Grupos de tareas independientes en paralelo (worktrees), máx. 4 grupos |
| Verificación | Agente de contexto fresco, distinto del implementador |

El principio detrás: **quien genera no se certifica a sí mismo**, y la diversidad de lentes encuentra más que la repetición del mismo.

## Fase 0 — Investigar (opcional)

Cuando la premisa tiene incógnitas que el spec no puede resolver suponiendo, se antepone un barrido multi-agente que produce `research.md` en la unidad (plantilla en `.spec/_plantillas/research.md`). El spec luego **cita hechos** en vez de suponerlos.

La conduce `sdd-orquestar` como paso 0 de su flujo: exploradores en paralelo por vías distintas (archivos, consumidores, gobernanza vía MCP, historia de git), consolidados en un documento donde **cada afirmación lleva su evidencia**. Es opcional: si el cambio es conocido, se omite — no es ceremonia obligatoria. No tiene gate propio; su calidad se juzga en el gate del spec, que verá si los criterios se apoyan en hechos o en suposiciones.

## Protocolo de retoma

Para continuar una unidad existente, antes de actuar:

1. Leer `_estado.yaml` → fase, estado, modo, riesgo, gates, governance.
2. Leer `tasks.md` → qué está hecho y qué falta.
3. Leer `bitacora.md` → contexto del último handoff.

Unidades **v1** (sin `modo`/`riesgo`/`gates`) se retoman aplicando los defaults: `modo: interactivo`, `riesgo: medio`, `gates: {}`.

Al cerrar cada sesión (obligatorio): actualizar `_estado.yaml` (`fase`, `estado`, `actualizado`) y **anexar** una entrada a `bitacora.md` (no editar entradas previas). La resumibilidad depende de esto.

## Governance

En las fases especificar/planificar, consultar el MCP `pce-mcp` por principios/ADRs/políticas **específicos del objeto de la unidad** y registrarlos en `governance_refs` de `_estado.yaml`. Lo indexado en `AGENTS.md` es baseline obligatorio de toda unidad y no se relista; el dominio `ia` está rechazado por mandato de ese archivo.

La cadena de precedencia es la canónica del repo (`pri-gob-precedencia-superficies`): este protocolo la **cita**, no la redefine. Lo único que añade es dónde encaja una unidad SDD en ella: el `spec.md` de una unidad nunca prevalece sobre gobernanza aceptada — si el spec choca con un artefacto aplicable, se corrige el spec o se tramita el cambio de gobernanza por su propio ciclo.

Los gates vuelven a consultar el MCP en cada corrida: evalúan contra la gobernanza recuperada, nunca contra conocimiento embebido. **Sin MCP no hay gobernanza**: se para y se dice, no se degrada a criterio propio.

## sdd-kit

El protocolo y el andamiaje SDD que este archivo documenta (`.spec/scripts/`,
`.spec/_plantillas/`, skills `sdd-*`, `.spec/README.md`/`SUPERVISADO.md`/
`PARADAS-SUPERVISADO.md`, entre otros) tiene una única fuente canónica: el
repositorio del kit (`sdd-kit`), que lo edita directamente. Cualquier otro
repositorio lo **recibe instalado** desde ahí y no lo edita a mano — su
propio `.spec/units/`, `.spec/planes/` y `.spec/cambios-menores/` (su rastro
de unidades y planes propio) no viajan al kit ni el kit los toca.

Para instalar o verificar el kit sobre un destino: clonar el repositorio del
kit como hermano del destino (p. ej. `../sdd-kit` si el destino es el
directorio de trabajo actual) y correr `installer/cli.py --target
<ruta-destino>` desde ahí. No tiene destino implícito: siempre hay que
nombrar la ruta del destino explícitamente, porque el kit vive siempre fuera
del repositorio que instala.

| Operación | Cómo se activa |
|---|---|
| verificar | Por defecto, sin flags de escritura |
| instalar | `--install` (con `--force` para autorizar sobrescribir conflictos) |

Códigos de salida:

| Código | Significado |
|---|---|
| 0 | Correcto (verificar: sin divergencias; instalar: sin conflictos o con `--force`) |
| 1 | Verificar con al menos una divergencia |
| 2 | Error operativo. Antes de escribir la carga: destino intacto. Si falla un materializador de espejo o el gancho de pre-push tras escribirla: destino parcialmente escrito, indicado en el mensaje de error |
| 3 | Instalar con al menos un conflicto sin `--force` |

Verificar un destino contra la carga vigente del kit:

```
python3 <ruta-al-kit>/installer/cli.py --target <ruta-destino>
```

Instalar (o reinstalar con `--force`) sobre ese destino:

```
python3 <ruta-al-kit>/installer/cli.py --target <ruta-destino> --install [--force]
```
