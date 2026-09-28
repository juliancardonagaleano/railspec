---
name: sdd-supervisado
description: "Use when: conducir un mandato supervisado ya aprobado — un plan maestro por objetivo (`.spec/planes/<id>/`) o una unidad aislada ya en `modo: supervisado` — fase a fase, con gates, decisiones delegadas, paradas tipificadas y cierre, sin checkpoints humanos intermedios. Does NOT aplicar a unidades `interactivo` ni `semi-autonomo` (esas van por `sdd-orquestar`), ni aprobar un mandato, ni redactar el mandato. Keywords: supervisado, mandato, plan maestro, delegaciones, punto de retoma, overnight."
compatibility: "Claude-first (subagentes en paralelo, worktrees)"
license: "Proprietary"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Supervisado (conductor de un mandato)

Conduce un **mandato supervisado aprobado** de punta a punta. Corre **inline** en la
conversación —no forkeada— porque dispara fan-out y persiste estado en el árbol
compartido, y ningún subagente de este mecanismo anida el tool Agent
(`.spec/MODELO-AGENTES.md:33-37`). Es orquestación barata: no fija modelo propio.

No reimplementa ninguna fase. Delega spec/plan/tasks/implementación a
`sdd-especificar`, `sdd-planificar`, `sdd-tareas` y `sdd-implementar`, y cada gate a
`sdd-gate`. Lo que hace y hoy no hace nadie: leer el mandato, correr el validador en
cada frontera, marcar las unidades amparadas, recorrer la cadena, resolver cada
decisión contra `## Delegaciones`, escribir el registro, respetar el tope de
paralelismo, tipificar cada parada con su punto de retoma, y cerrar el mandato.

Los artefactos que conduce —plantillas de mandato, lista de paradas, contrato del
registro, validador, campos de `_estado.yaml`— están documentados en
`.spec/SUPERVISADO.md`. Esta skill los **usa**, no los redefine. Las referencias
`0109a`/`0109b` CA-NN de abajo son los criterios de aceptación de las unidades
que introdujeron este modo en este repo: se conservan por trazabilidad.

## Cuándo usar / NO usar

| Usar | NO usar |
|---|---|
| Conducir un plan maestro o una unidad aislada bajo mandato aprobado | Unidad `interactivo` o `semi-autonomo` → `sdd-orquestar` |
| Retomar un mandato que quedó parado | Redactar o aprobar un mandato → acto humano |
| Cerrar un mandato cuyo criterio de salida se cumplió | Desbloquear una parada → solo el autor de la entrada de mandato vigente |

Ningún mandato permite saltar un gate: **no se puede omitir ni bajar el tier**.
El tier de cada gate es el `riesgo` de la unidad, y los cuatro veredictos (spec,
plan, tasks, codigo) quedan persistidos en `_estado.yaml > gates`.

## 0. Identidad de la sesión

Toda escritura de esta skill —bitácora, parada, punto de retoma, registro de
decisiones— cita la **misma** `sesion`, para que la evidencia de una corrida se
pueda reconstruir después:

- `sesion` = la URL `Claude-Session` que declara el harness de la sesión en curso.
- Si el harness no la declara, `sesion` = `<actor>@<hostname> <ISO-8601>`, con el
  actor en la forma canónica de `0109a` CA-01c (igualdad literal, nunca heurística).

No se inventa un identificador distinto por sección ni se reusa el de otra corrida.

## 1. Lanzamiento

### 1.1 Lock de instancia única

Hay **una sola instancia por mandato**. Consultar, tomar y liberar ese lock es lo que
mecaniza `.spec/scripts/instance_lock.py` (`inspect` / `acquire` / `release`): esa es
la única fuente de cómo se lee y cómo se escribe `## Instancia en curso`.

Lo que el script no decide y decide el conductor: con el lock tomado por otra sesión,
esta instancia **no avanza** —no toca ningún archivo, ni `_estado.yaml`, ni el
mandato— y reporta la sesión que lo ocupa y desde cuándo, con los valores literales
que el propio lock declara.

La sección se **vacía** al parar y al cerrar (§ 6, § 10). No expira sola: un lock
huérfano lo libera **solo el autor de la entrada de mandato vigente**, como cualquier
parada, dejando entrada de bitácora del mandato con actor, fecha y motivo.

### 1.2 Quién puede lanzar

La puede lanzar **cualquier humano con acceso al repo** (D-19). El lanzador se
registra en la forma canónica de `0109a` CA-01c. El campo `lanza:` de la entrada de
mandato vigente es **informativo**: si el lanzador real difiere de `lanza:`, se anota
en la bitácora del mandato y se sigue — no se rechaza el lanzamiento.

Quién **desbloquea** una parada y quién **revisa** una decisión no cambia por esto:
esos dos actos son del autor de la entrada de mandato vigente (§ 6, § 10).

### 1.3 Validador de aprobación

Antes de conducir **cualquier** fase se valida la aprobación del mandato y se deja
constancia de esa corrida. El ritual completo —correr el validador de `0109a` y
anexar su entrada a `validaciones_mandato`— lo ejecuta
`.spec/scripts/record_mandate_validation.py` (`--unit <DIR> --phase <FASE>`; con
`--mandate <ID|RUTA>` valida el mandato de un plan por objetivo, y sin esa bandera la
forma «unidad aislada»). Esta skill no vuelve a escribir esa invocación a mano.

Semántica exacta de invocación de `preflight.py --mandate` y `validate_mandate.py
--plan`: `AGENTS.md` § "Contratos de invocación exactos (scripts del modo
supervisado y `RunSnapshot`)" es la fuente única — no se repite aquí. (El `--mandate` de
`record_mandate_validation.py`, arriba, es un flag distinto de un tercer script,
con su propia semántica ya descrita en el párrafo anterior — no lo cubre esa
tabla.)

Lo que el script entrega es un resultado; qué significa cada resultado lo decide el
conductor:

- Exit `0` del ritual y `exit: 0` dentro de la entrada anexada ⇒ seguir.
- Exit ≠ 0 del ritual, o un `exit` ≠ 0 del validador dentro de la entrada ⇒ parada
  tipificada (§ 9), con los `codigos` literales que reportó.
- Un mandato en `borrador` da `aprobacion-ausente`: **no se conduce ninguna fase de
  implementación de una unidad amparada sin aprobación registrada en el mandato**.
  Se para en el lanzamiento, antes de invocar `sdd-implementar`, y se reporta ese
  código sin interpretarlo.

El sha256 que un humano copia en su entrada de `## Aprobación`, y el resumen del
mandato que `sdd-retomar` transcribe, los rinde el mismo validador por sus modos de
consulta; **esta skill nunca firma una aprobación**.

El ancla de aditividad del lanzamiento la escribe `.spec/scripts/mandate_anchor.py`:
la primera vez que corre sobre un mandato dado crea la sección `## Ancla de
aditividad` que el `comando_validacion` de una unidad posterior resuelve como
`VALIDAR_BASE` de `validate-supervised.sh`; una segunda invocación sobre el mismo
mandato (otro lanzamiento tras una retoma) no la toca (éxito idempotente). Nunca se
invoca antes de que la validación de aprobación pase: un mandato rechazado en este
paso —vencido, sin paquete de aprobación— no llega a escribir el ancla.

### 1.4 Marcado de las unidades

**Marcado.** Un mandato aprobado **habilita** el modo `supervisado`; no lo
asigna por sí solo. Escribir `_estado.yaml > modo: supervisado` exige una
entrada `modo_conversion` (`desde`, `hacia`, `en`, `por`, `tras`) que un
humano registra tras la fase `research` o tras el checkpoint del `spec.md`
de la unidad (`.spec/SUPERVISADO.md` § "Modo por decisión humana y unidades en
vuelo") — esta skill transcribe esa decisión, no la toma. Al iniciar un
mandato aprobado, por cada unidad amparada:

- `_estado.yaml > mandato`: **sustituye** la referencia anterior por la del mandato
  vigente — una sola referencia por unidad (`0109a` CA-04). Si la unidad ya traía
  otra, la sustitución deja la anterior **citada en la bitácora** de la unidad.
- `modelo_ejecucion` y `gates` como en cualquier unidad.
- Una entrada nueva en `bitacora.md` de la unidad que cita el mandato, su entrada de
  aprobación y la `sesion` de § 0.
- Una entrada nueva para la unidad —o, si `mandato` es el id de un plan, una viñeta `- unidad:
  <id>` bajo la entrada de ese plan— en el índice de mandatos `.spec/units/_mandatos-supervisado.md`.
  Esta skill crea, en este mismo paso: el índice si no existe (con la cabecera canónica de
  `.spec/_plantillas/mandato.md` § "Formato de entrada en las secciones-registro"); la entrada
  del plan bajo `## Planes por objetivo` si es la primera unidad de ese plan (`### <id-plan>` con
  `- mandato: <ruta de su plan-maestro.md>`, reemplazando la nota de sección vacía — ese
  reemplazo no cuenta como editar una entrada existente); y la entrada de la unidad, sin línea de
  changelog todavía (las líneas las escribe § 11 al parar o cerrar).

## 2. Flujo por unidad, dirigido por estado

El siguiente paso lo deciden `fase` y `gates` de `_estado.yaml` —la misma tabla que
`sdd-retomar`—, nunca por dónde se entró. Así una retoma continúa donde quedó y una
unidad sembrada a mitad de camino se conduce desde ahí.

| Estado leído | Siguiente |
|---|---|
| `fase: research` | Paso 0 de `sdd-orquestar` ("Investigar"): lanzar los exploradores, consolidar `research.md` con `modelo_ejecucion.research`, luego `sdd-especificar` |
| `fase: spec`, sin `gates.spec` | `sdd-gate` (`fase: spec`) |
| `fase: spec`, con `gates.spec` | `sdd-planificar` |
| `fase: plan`, sin `gates.plan` | `sdd-gate` (`fase: plan`) |
| `fase: plan`, con `gates.plan` | `sdd-tareas` |
| `fase: tasks`, sin `gates.tasks` | `sdd-gate` (`fase: tasks`) |
| `fase: tasks`, con `gates.tasks` | `sdd-implementar` |
| `fase: implement` | `sdd-implementar` (continúa las tareas `[ ]`) |
| `fase: done` | nada: la unidad está cerrada |
| `gates.<fase>` con veredicto `escalado` | parada (§ 6), sin avanzar |

El recorrido completo es investigación (paso 0) → spec → gate → plan → gate →
tasks → gate → implementación → gate de código, **sin checkpoints** humanos
intermedios: el checkpoint del modo supervisado es la aprobación del mandato, ya
verificada en § 1.3. No hay `paquete-aprobacion.md` en este modo (el modo
`semi-autonomo` sigue exigiéndolo, intacto).

## 3. Cadena de un plan

Para un mandato de plan, la cadena vigente es la línea `Vigente:` de
`## Cadena de dependencias` — fuente única, no se transcribe a ningún otro archivo.

- Recorrer esa cadena **en orden**.
- Conducir solo las unidades **miembro** (`## Unidades miembro`) cuyas dependencias
  declaradas estén en `fase: done`.
- Una unidad marcada `(no amparada)` en la cadena **solo se espera**: no se conduce,
  no se marca, no se le tocan artefactos.
- Si la cadena cambió después de la última aprobación, el validador lo dice con
  `aprobacion-desactualizada` — código de la lista humana (§ 9): no se auto-corrige.

Para un mandato de unidad aislada, "la cadena" es esa única unidad
(`## Unidad amparada`).

## 4. Decisiones bajo delegación

Ante **cada** decisión que aparezca mientras se conduce, consultar `## Delegaciones`
del mandato, que tiene tres categorías mutuamente excluyentes (D-13):

| Categoría | Conducta |
|---|---|
| `### Pre-decididas` (`PD-n`) | El implementador la aplica directamente; el conductor la **asienta** en `## Registro de decisiones` al integrar el grupo |
| `### Con criterio` (`CR-n`) | El conductor **decide** aplicando el criterio citado, asienta la entrada con alternativas consideradas, criterio citado por id y cómo revertirla, y **relanza el grupo** con la decisión |
| `### Reservadas` (`RS-n`) | **Parada** (§ 6): lo reservado no se decide aplicando ningún criterio, aunque una entrada de `### Con criterio` parezca cubrirla |
| No cubierta por ninguna | **Parada** (§ 6): lo ambiguo no se infiere (`pri-ia-cero-inferencia-implicita`) |

Canal desde los grupos: un implementador que topa con una decisión con criterio,
reservada o no cubierta reporta `bloqueo: decision-requerida <qué, alternativas>` y
**detiene su tarea**; no la fuerza ni inventa una salida para poder seguir.

**Un solo escritor.** Solo el conductor escribe `## Registro de decisiones`, y lo hace
serializado: una entrada a la vez, con identificador `<id-unidad>-D<secuencia>` donde
la secuencia es el último `n` de esa unidad más uno — sin huecos ni duplicados
(`0109a` CA-01b; el validador los rechaza con `decision-id-duplicado`). El registro es
la **única** descripción de alternativas y de reversión: bitácoras y `plan.md` citan
cada decisión por su identificador, nunca reproducen su contenido
(`pri-gob-fuente-verdad-unica`).

## 5. Paralelismo

Leer `## Paralelismo` del mandato: `carriles`, `tope-worktrees`, `dueno-stack-vivo`,
`presupuesto-mcp` y `conducta-presupuesto-agotado`.

- **El tope manda.** No se lanzan más grupos simultáneos que `tope-worktrees`, aunque
  la suma de los grupos de las unidades en vuelo lo permitiera. Lo que no cabe se
  **serializa** y queda anotado en la bitácora de la unidad, con el tope citado.
- **Stack vivo: solo su `dueno-stack-vivo`** — `.spec/PARADAS-SUPERVISADO.md` condición 10.
- **Corpus de la PCE: no reconstruir en vuelo** — `.spec/PARADAS-SUPERVISADO.md` condición 12.
- Agotado el `presupuesto-mcp`, la conducta es la que declara el propio mandato y la
  lista única de paradas: parar (§ 9 y `.spec/PARADAS-SUPERVISADO.md`).

## 6. Paradas y punto de retoma

Son parada: cualquier condición de `.spec/PARADAS-SUPERVISADO.md` (lista única, citada
por ruta — no se reproduce aquí, `pol-ia-no-embeber-conocimiento`), un gate con
veredicto `escalado`, un exit ≠ 0 del validador (§ 9), el fin del mandato (§ 7) y una
instrucción humana directa.

Cada condición de `.spec/PARADAS-SUPERVISADO.md` cae en una de dos **clases de ámbito**
que el conductor distingue antes de decidir qué se congela. El preámbulo de ese
archivo las declara explícitamente; aquí se aplica la consecuencia operativa:

- **Unit-level (regla por defecto).** La condición afecta a **una sola unidad
  amparada** y, por la cadena de `## Cadena de dependencias` del mandato, a sus
  **dependientes directos** (unidades cuyo id aparece aguas abajo del id
  afectado en la línea `Vigente:`). Las unidades independientes y las
  posteriores (no-dependientes) **siguen**. El mandato, salvo que se eleve a
  `parado` por una causa mandato-nivel, **no** se eleva a `parado` — su
  `## Estado` permanece en `aprobado`. La **decisión** sobre la parada sigue
  siendo humana (`pri-ia-humano-decide`); lo que se distribuye aquí es la
  **propagación** del efecto.
- **Mandato-nivel (caso particular).** La condición exige **parar el mandato
  entero** — afecta a la infraestructura compartida (stack vivo, knowledge-router,
  PCE, base viva) o es una instrucción humana directa, un gate `escalado` de
  carrera completa, o el fin del mandato. Aquí sí se eleva `## Estado` a
  `parado`. La lista canónica de las condiciones mandato-nivel está en el
  preámbulo de `.spec/PARADAS-SUPERVISADO.md`.

Cuando una parada dispara **varias** condiciones a la vez y al menos una es
mandato-nivel, **el ámbito dominante es mandato-nivel** — se eleva el `## Estado`
del mandato y se sigue el flujo de la segunda columna. Cuando **todas** las
condiciones disparadas son unit-level, se mantiene el ámbito unit.

Ante cualquiera de estas paradas:

1. **Freeze unit-level por defecto.** Los grupos en vuelo de la unidad afectada
   y de sus dependientes directos terminan su **tarea atómica actual** y paran;
   ninguno inicia otra tarea. Los grupos de unidades independientes y
   posteriores (no-dependientes) continúan como si nada, salvo que la unidad
   que paran **sea** su dependiente directo, en cuyo caso siguen el freeze de
   su dependiente. La regla de prioridad es la cadena `Vigente:` del mandato
   (§ 3) — fuente única, nunca inferida de referencias cruzadas
   (`pri-gob-fuente-verdad-unica`). Si la condición disparada es
   mandato-nivel, el freeze se eleva a **toda la orquestación del mandato**
   (comportamiento previo) y se sigue el flujo de la columna mandato-nivel.
2. Escribir la entrada en `## Paradas` del mandato: `unidad:` con el id de la
   unidad afectada (o `todas` si la condición es mandato-nivel); `disparador:`
   que nombra la condición o el gate; `causa:` observada; `invalida-el-objetivo:`
   sí | no; y la `sesion` de § 0. La entrada distingue las dos clases por
   el `disparador:` y por el `unidad:` — el término "parada" se conserva único
   (decisión de la unidad `0139`, pregunta 5 del humano-decisor).
3. Poner `## Estado` del mandato en `parado` **solo si** la condición es
   mandato-nivel. En unit-level, `## Estado` permanece en `aprobado`; el
   detalle del freeze vive en `## Punto de retoma.unidades-en-curso` (ver
   sub-bloque "Visibilidad agregada" abajo).
4. Escribir una entrada nueva en `## Punto de retoma` con **todos** los campos
   de `0109a` CA-17: `rama`, `commits` (hashes), `validacion` (comandos corridos
   y su **resultado literal**), `unidades-en-curso` (unidad y fase por
   carril/grupo) y `pasos` numerados.
   Dónde está de verdad la copia de trabajo —rama, `HEAD` y lo no commiteado
   bajo los árboles que esta corrida toca— lo rinde
   `.spec/scripts/report_git_sync.py`, que **solo lee**: el conductor escribe
   con ese reporte los campos `rama`, `commits` y `validacion`, y decide qué
   hacer con lo que salga divergente.
   Para paradas unit-level, el campo
   `unidades-en-curso` declara **cada** unidad amparada con su estado por
   unidad (`en-verde` | `esperando-A-parada` | `parada` | `no-iniciada`) — ver
   sub-bloque "Visibilidad agregada".
5. Liberar el lock con `.spec/scripts/instance_lock.py release`, que es lo que
   vacía `## Instancia en curso`.
6. Anexar la línea de changelog (§ 11) con
   `.spec/scripts/append_changelog_line.py`.

### 6.A Visibilidad agregada (por defecto)

Por defecto, el reporte de estado del mandato que el conductor rinde al humano
combina **dos vistas**:

- **Vista del mandato completo:** `## Estado` (`aprobado` / `parado`) y, si
  está `parado`, el motivo agregado leyendo la entrada de `## Paradas` que
  abrió la parada.
- **Vista por-unidad:** cada unidad amparada con su `fase`, su `estado` y, si
  está congelada por una parada unit-level, su estado operativo —
  `en-verde`, `esperando-A-parada` (cuando es dependiente directo de la
  unidad que paró), `parada` (cuando es la unidad que disparó), o
  `no-iniciada`. El estado `esperando-A-parada` se conserva mientras la
  unidad no reanuda; al reanudar la unidad A, sus dependientes B pasan a
  `esperando-A-reanudada` hasta cerrar su re-fase, y luego `en-verde`.

El campo `unidades-en-curso` de `## Punto de retoma` es el vehículo de esa
visibilidad por-unidad — no se introduce un nuevo campo. `## Estado` del
mandato **no se amplía**: el vocabulario cerrado CA-01d
(`borrador` | `aprobado` | `parado` | `cerrado`) se mantiene; la visibilidad
agregada se rinde con esos mismos estados por unidad.

### 6.B Notificación diferenciada por severidad

Por defecto:

- Paradas **alta** o **crítica** notifican al humano **en el momento** —
  entrada de bitácora del mandato + mensaje al actor de la entrada de
  `## Mandato` vigente (forma canónica de actor, CA-01c).
- Paradas **media** o **baja** se **acumulan** al resumen del mandato
  escrito en `## Punto de retoma.unidades-en-curso` y se notifican al
  humano al cierre del turno o de la fase, no en el momento.

La severidad la asigna el crítico del gate (`sdd-gate`/`references/
rubrica-spec.md`/`rubrica-codigo.md`) o la causa de la condición
disparada; en unit-level, se mantiene la severidad original de la
condición — no se atenúa por ser unit-level.

### 6.C Reanudación determinista (re-fase, no reinicio)

Al desbloquear la unidad A, sus dependientes directos B reanudan desde
**donde quedaron** en su cadena — no reinician su tarea. La cadena
vigente (`## Cadena de dependencias`) sigue siendo la fuente del
orden. Si B avanzó durante la parada bajo un supuesto sobre A que
**cambió** mientras estaba congelada, B revalida su gate con el
estado nuevo de A antes de continuar; ese re-gate no es parada
nueva, es parte de la reanudación. El estado congelado de B incluye
su checkpoint — la reanudación es determinista con ese checkpoint
como entrada.

**Desbloqueo.** Lo registra **solo el autor de la entrada de mandato vigente**, en
`## Paradas` (`desbloqueo-quien`/`-fecha`/`-que`). Una decisión de desbloqueo firmada
por otro actor **no desbloquea**: el validador la rechaza con `desbloqueo-no-autor` y
el mandato sigue `parado`. El conductor no compara por heurística: igualdad literal
contra el `autor` de la entrada de `## Mandato` vigente.

## 7. Vencimiento

El `fin` ISO-8601 de la entrada de mandato vigente se compara **al lanzar** y **antes
de iniciar cada unidad de trabajo**. Superado ese `fin`, el vencimiento completa, por
cada carril o grupo en vuelo, exactamente **una** unidad de trabajo —y ninguna otra
se inicia:

| Unidad de trabajo en curso | Conducta al vencer |
|---|---|
| Artefacto de fase (`spec.md`, `plan.md`, `tasks.md` o el gate que lo evalúa) | Se termina y se registra |
| Una tarea de `tasks.md` | Se termina |
| Integración de un worktree | Se completa **solo si** el grupo terminó todas sus tareas y su validación pasó; en cualquier otro caso se descarta limpia (`git worktree remove --force`, sin merge) y se anota |
| Cierre de una unidad | Se completa |

Después: parada (§ 6), con el punto de retoma describiendo **por carril** qué se
completó y qué se descartó. Lo que estaba "en curso" al lanzar es lo que declara la
última entrada de `## Punto de retoma`, no una inferencia.

**Reanudación.** Una entrada **nueva** de `## Mandato` firmada por cualquier humano
con acceso al repo (D-19), en la forma canónica de actor, basta para reanudar sin
reaprobar — salvo que la cadena (plan) o las delegaciones hayan cambiado desde la
aprobación vigente: ahí el validador emite `aprobacion-desactualizada` y hace falta
una reaprobación humana.

## 8. Retoma

Al retomar un mandato parado, antes de lanzar ninguna fase:

1. Delegar a `sdd-retomar` la reconstrucción de contexto de la unidad. El resumen del
   mandato que ahí se transcribe —forma, estado, última aprobación y punto de retoma,
   sin volver a parsear el archivo— lo rinde `.spec/scripts/supervised_conductor.py`
   (función `resumen_stdout`), que es quien invoca al validador en su modo de consulta.
2. Correr el validador (§ 9) con `fase: retoma`, y contrastar dónde está la copia de
   trabajo contra el punto de retoma con `.spec/scripts/report_git_sync.py` (solo lee).
3. Leer la entrada más reciente de `## Punto de retoma`:
   - Si el validador emite `retoma-incompleta` (entrada sin algún campo, o anterior a
     la parada abierta): **no se continúa ninguna fase**; se reporta el código.
   - Si registra una **validación en rojo** en su campo `validacion`, hay que
     distinguir de qué rojo se trata — toda parada por un exit ≠ 0 del validador (§ 9)
     deja por construcción un rojo ahí, así que leer «rojo ⇒ no se continúa» en línea
     recta haría irretomable toda parada:
     - **el rojo que es la parada misma** —el exit ≠ 0 de `validate_mandate.py` que la
       disparó, y los códigos que lo siguen reportando mientras el mandato sigue
       `parado` (p. ej. `mandato-estado-inconsistente` (iii), que solo se apaga al
       cerrar)— **no** bloquea la retoma por sí solo: es exactamente lo que resuelve el
       desbloqueo del autor (§ 6). Con **toda** parada de `## Paradas` desbloqueada
       (`desbloqueo-quien`/`-fecha`/`-que` escritos), se continúa, y ese rojo residual
       se disuelve en el cierre de § 10;
     - **mientras quede una parada abierta**, o si la entrada registra un rojo
       **independiente** de la parada —el `comando_validacion` de la unidad, tests,
       build, un gate `escalado`—, **no se continúa ninguna fase**: nada lo resolvió.
       Es una parada con decisión humana, no un estado del que se salga corrigiendo
       sobre la marcha.
4. Solo con la retoma verificada —punto de retoma completo (sin `retoma-incompleta`),
   toda parada desbloqueada y ningún rojo independiente pendiente— se retoma el flujo
   de § 2 desde la fase que declaren `fase` y `gates`. El rojo que la entrada de retoma
   **registra** es historia de la parada; lo que decide es qué lo causó y si el acto
   que lo resuelve —el desbloqueo del autor, o la corrección de lo que fallaba— está
   escrito, no el color guardado.

## 9. Validador en el bucle

Correr `validate_mandate.py` **antes de cada fase** de una unidad amparada, **en cada
retoma** y **antes de cerrar**. Cada corrida —sin excepción, incluidos los reintentos—
deja una entrada en `_estado.yaml > validaciones_mandato` de la unidad:

```yaml
validaciones_mandato:
  - {fecha: "<ISO-8601>", fase: <lanzamiento|retoma|spec|plan|tasks|implement|cierre>, exit: <int>, codigos: [<códigos literales>]}
```

Exit ≠ 0 es **parada tipificada**: entrada en `## Paradas` con los códigos, `## Estado`
en `parado`, punto de retoma (§ 6), y se desbloquea como cualquier otra parada.

- **Lista humana, sin ninguna acción del conductor** (ni corrección ni reintento):
  `aprobacion-desactualizada`, `retoma-incompleta`, `cierre-con-pendientes`.
  Se para y se reporta, literal.
- **Cualquier otro código** admite una auto-corrección **en el propio mandato** y una
  re-corrida, con **tope de 2 intentos** (2 intentos por fase, cada uno registrado en
  `validaciones_mandato` con la misma `fase`). Agotado el tope: parada tipificada.
- La auto-corrección nunca toca el veredicto de un gate, una aprobación, una firma ni
  un artefacto de gobernanza (`pol-gob-no-creacion-directa`).

## 10. Cierre

**Disparador.** Para un plan: todas las unidades miembro en `fase: done`. Para una
unidad aislada: el cierre de su propia unidad (`fase: done`, que ya hace
`sdd-implementar`).

Al dispararse, en este orden:

1. Evaluar el criterio de salida declarado en `## Objetivo y criterio de salida` y
   **registrarlo con su evidencia**. Si **no** se cumple: no se cierra — parada (§ 6),
   punto de retoma y decisión humana.
2. Correr el validador (§ 9) con `fase: cierre`. Un exit ≠ 0 impide el cierre;
   `cierre-con-pendientes` (una decisión `autonoma` sigue en `pendiente`) es de la
   lista humana: el conductor **nunca** marca una decisión como revisada para
   destrabarse.
3. Poner `## Estado` en `cerrado`.
4. Anexar la línea de changelog (§ 11).
5. Liberar el lock con `.spec/scripts/instance_lock.py release`, que es lo que vacía
   `## Instancia en curso`.

**Cierre de turno.** Si la línea que § 11 acaba de anexar —o una anterior, todavía sin
resolver— trae `medicion-pendiente: unidad=<id> indicadores=<lista> bitacora=<ruta>`,
esta es la sesión que la resuelve: medir esos indicadores con `usage_report.py
indicators` sobre la ventana de esta sesión, anotarlos en la bitácora que la línea
señala con fecha y `session_id`, y marcar `medicion-hecha: <fecha>` en esa misma línea
del changelog (edición puntual con la herramienta de edición del harness:
`.spec/scripts/append_changelog_line.py` solo anexa, no reescribe líneas existentes).

El conductor **nunca** escribe `## Revisión posterior`: esa sección la escribe **solo el autor** de la entrada de mandato vigente.
El paso de `revision: pendiente` a `aceptada`/`revertida` de una decisión `autonoma` es acto de ese humano — **nunca** de esta skill, que no tiene paso alguno para hacerlo (`0109a` CA-01g, `## Revisión posterior`).

## 11. Changelog

Al parar o cerrar un mandato, anexar a la entrada de ese mandato en el índice de mandatos
`.spec/units/_mandatos-supervisado.md` **una única línea** fechada que referencie el mandato y su
punto de retoma, sin duplicar el detalle —que vive en `## Paradas` / `## Punto de retoma` del
propio mandato—. Esa línea la escribe `.spec/scripts/append_changelog_line.py`.
Idempotente: una segunda invocación con la misma línea no la duplica y sigue saliendo
`0`. Junto con crear la entrada al marcar (§ 1.4), es el **único** cambio que esta
skill hace al índice: ninguna línea ni entrada existente se edita ni se borra.

## Output

Mandato conducido hasta su cierre o hasta una parada tipificada, con: unidades
amparadas marcadas y con sus artefactos y gates persistidos; `## Registro de
decisiones` al día; `validaciones_mandato` con una entrada por corrida del validador;
`## Paradas` y `## Punto de retoma` completos si paró; `## Instancia en curso` vacía;
y una sola línea nueva en el changelog global.
