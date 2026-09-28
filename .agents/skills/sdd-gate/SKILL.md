---
name: sdd-gate
description: "Use when: validar un artefacto SDD recién producido (spec.md, plan.md, tasks.md o el diff de implementación) antes de avanzar de fase — corre el gate determinista y el bucle de crítica-refinamiento con panel de agentes. Does NOT redactar el artefacto desde cero (eso es la skill de la fase). Keywords: gate, validación, crítica, refinar, self-improvement, revisar spec, revisar plan."
compatibility: "IArk — Claude-first (subagentes en paralelo)"
license: "Proprietary - IArk"
metadata:
  user-invocable: "true"
---

# Skill: SDD — Gate (motor de validación y refinamiento)

Valida un artefacto de una unidad SDD y lo refina hasta que converge o escala.
Es **una sola skill parametrizada**, no un gate por fase: el flujo es idéntico y
lo que cambia es la rúbrica que se carga.

**NO redacta el artefacto desde cero** (eso es `sdd-especificar` /
`sdd-planificar` / `sdd-tareas` / `sdd-implementar`). **NO aprueba por
agotamiento**: si no converge, escala al humano.

## Parámetros

| Parámetro | Valores | De dónde sale |
|---|---|---|
| `fase` | `spec` \| `plan` \| `tasks` \| `codigo` | La skill que invoca el gate |
| `tier` | `bajo` \| `medio` \| `alto` | `_estado.yaml > riesgo` (default `medio`) |
| `unidad` | ruta `.spec/units/<NNNN-slug>/` | La skill que invoca el gate |

Si se invoca sin `fase` (p. ej. por slash-command), derivarla de
`_estado.yaml > fase` con este mapeo — **no** hay rúbrica para las demás:

| `fase` de la unidad | Gate a correr |
|---|---|
| `spec` / `plan` / `tasks` | el del mismo nombre |
| `implement` | `codigo` |
| `research` / `aprobacion` / `done` | ninguno — parar y decirlo |

El presupuesto por tier —topes duros, no sugerencias— sale de
`python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --gate --tier <tier>`
(fuente `.spec/perfiles.yaml`), que devuelve `criticos`, `iteraciones` y
`adversarial`; si el comando falla, parar y reportarlo (CA-33). Con el
perfil `estandar` reproduce exactamente la tabla documentada en
`.spec/README.md § Gates de validación`.

**Los lentes obligatorios de la rúbrica se ejecutan siempre**, en cualquier
tier: son los que hacen que el veredicto signifique algo (correctitud y
gobernanza, según la fase). El tier no decide si se evalúa la gobernanza —
decide cuánta profundidad extra se añade encima (lentes opcionales) y cuántos
agentes (`criticos`) cubren esos lentes — ver paso 4.

## Flujo

### 1. Cargar contexto y rúbrica

- Leer `_estado.yaml` (para `riesgo`, `modo`, `governance_refs`) y el artefacto
  a evaluar. En `fase: codigo`, leer además el diff (`git diff`), `spec.md` y
  `tasks.md` — el crítico de cumplimiento contrasta los `CA-NN` marcados contra
  las tareas, no solo contra el diff.
- Cargar la rúbrica de la fase: `references/rubrica-<fase>.md`. Sus lentes son
  la lista de críticos a lanzar.
- **Si `_estado.yaml > gates > <fase> > veredicto` ya es `escalado`**: esta
  corrida es una re-corrida de una fase escalada. **MUST NOT** lanzar el panel
  sin que `gates.<fase>.rehabilitado_por: {por, en, motivo}` ya esté
  registrado en `_estado.yaml` (decisión humana previa a esta corrida, más su
  línea en `bitacora.md`). Sin eso, parar y pedirlo — no es un chequeo
  determinista del paso 2, es una precondición de arrancar. Ver § 7 para el
  efecto de `rehabilitado_por` sobre `iteraciones`.

### 2. Capa determinista (siempre, sin agentes)

Barata y primero: si algo de esto falla, no se gasta un solo agente.

- Todas las secciones de la plantilla están presentes y ninguna dice "TBD",
  "..." ni quedó con el texto de ejemplo de la plantilla.
- Los chequeos específicos de la fase que declara su rúbrica (sección
  "Capa determinista" de `references/rubrica-<fase>.md`).

Si algún chequeo determinista falla, **no se lanza el panel**: se devuelve el
control a la skill de fase con la lista de faltantes para que los complete. Eso
no consume iteración, pero **sí se cuenta**: a la **tercera** devolución por la
misma causa, el gate emite `escalado` con causa `determinista-irresoluble`. Una
fase que no consigue satisfacer un chequeo en dos intentos no lo va a conseguir
en el décimo, y el lazo fase→gate→fase no puede quedar abierto sin dejar rastro
en disco.

**Excepción — la validación en rojo no es una omisión rellenable.** En
`fase: codigo`, si el `comando_validacion` falla, el gate no devuelve el control
para "completar" nada: emite directamente `escalado` con causa
`validacion-en-rojo`. Es un hecho sobre el código, no una sección que falte.

### 3. Recuperar la gobernanza aplicable (obligatorio)

Los críticos evalúan contra la gobernanza vigente, no contra su criterio. Las
rúbricas no contienen reglas de gobernanza: las recuperan.

- **CONSULTAR** `pce-mcp` → tool `resolve_entity` → query: cada
  id de `_estado.yaml > governance_refs` → **APLICAR**: el mandato de cada
  artefacto es criterio de evaluación del panel.
- **APLICAR** siempre la gobernanza indexada en `AGENTS.md` (baseline de
  toda unidad) aunque no esté en `governance_refs`: su ausencia ahí **no**
  es hallazgo.
- **CONSULTAR** `pce-mcp` → tools `traverse_knowledge` (query = objeto de
  la unidad) y `search_catalog` acotado por `domain`/`query` a ese objeto,
  con `type` en `adr`, `policy` y `principle` → **APLICAR**: detectar
  artefactos **específicos del objeto** que el spec no listó (hallazgo
  "gobernanza omitida"). Los tres tipos, no dos: un ADR omitido pesa más
  que una policy omitida.

**Sin fallback.** Si el MCP no responde o devuelve error (incluido
`budget_exceeded`), el gate NO critica de memoria: emite veredicto `escalado`
con causa `sin-gobernanza` y `governance_consultada: no`. Si solo una parte de
las consultas respondió, `governance_consultada: parcial` y el veredicto no
puede ser `aprobado`.

En **modo supervisado** este *sin fallback* se extiende a **cualquier** punto del
gate: un rechazo del MCP en este paso o en la consulta puntual de un crítico
(paso 4) cierra igual el gate con veredicto `escalado` y causa
`sin-gobernanza`. `governance_consultada: parcial` no existe en ese modo — la
corrida es `si` o `no`.

### 4. Panel de críticos — una sola tanda, sin reintento en bucle

El `criticos` resuelto arriba (§ Parámetros, `resolve --gate --tier`) fija **cuántos
agentes** lanzar, no cuántos lentes evaluar — los lentes activos de la
rúbrica (obligatorios siempre, más los opcionales que permita el tier) se
evalúan todos, siempre (0117-D4/D6):

- **`criticos: 1`** (tier `bajo`/`medio` en `estandar`): **MUST** lanzar un
  solo subagente que evalúa **todos** los lentes activos en una sola pasada
  multi-lente — nunca omitir un lente por compartir agente.
- **`criticos` mayor a 1** (tier `alto` en `estandar`): **MUST** repartir los
  lentes activos entre esos `criticos` agentes, **en una sola tanda
  paralela**, de forma que cada lente activo quede cubierto por exactamente
  uno de ellos — si dos lentes le tocan al mismo agente, ese agente los
  evalúa juntos en la misma pasada, nunca en instancias separadas para el
  mismo reparto.

Para cada agente del panel, correr
`python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role <rol-de-la-tabla-de-abajo>`
y lanzarlo con el `subagent_type`/`model` de esa salida; si el comando falla,
parar y reportarlo (CA-33).

**MUST NOT** reintentar en bucle a la espera de un crítico que no responde:
si un crítico no devuelve veredicto, el veredicto del gate es `escalado
sin-gobernanza`.

Cada crítico recibe: el artefacto completo (más `tasks.md` en `fase: codigo`,
por el paso 1), la gobernanza recuperada en el paso 3 — es la fuente; el
crítico solo vuelve a `pce-mcp` por un detalle puntual que el paso 3 no le dio,
no para repetir la búsqueda —, y **el texto completo del/los lente(s) que le
tocaron** copiado de `references/rubrica-<fase>.md` (no solo el nombre).

En **modo supervisado**, un rechazo del MCP en esta consulta puntual del crítico
cierra el gate igual que en el paso 3: veredicto `escalado` con causa
`sin-gobernanza`. No existe `governance_consultada: parcial` en ese modo — la
corrida es `si` o `no`.

Qué rol lanzar por lente — esta tabla solo asigna el rol; cada lanzamiento
resuelve su `subagent_type`/`model` con `resolve --role <rol>` (arriba). Ver
`.spec/MODELO-AGENTES.md` para el porqué:

| Fase | Lentes | Subagente |
|---|---|---|
| `spec` | L2, L3, L4 (todos los activos) | `sdd-critico-profundo` |
| `plan` | L3 | `sdd-critico-profundo` |
| `tasks` | L1 | `sdd-critico-estructural` |
| `codigo` | L2 — Correctitud | `sdd-critico-profundo` |
| `codigo` | L1 — Cumplimiento de `CA-NN`, L3 — Encaje | `sdd-critico-cumplimiento` |

**Si un `subagent_type` de la tabla no resuelve** (falta `.claude/agents/`,
checkout sin materializar): no bloquear el gate por eso — es un mecanismo
temporal, no una dependencia dura del protocolo. Lanzar `general-purpose` con
la misma lente y las mismas instrucciones que le tocaban a ese rol, anotarlo
en bitácora, y en el paso 8 persistir `subagente: general-purpose` en vez de
inventar que corrió el rol previsto. Detalle completo en
`.spec/MODELO-AGENTES.md` § "Si un subagente nombrado no está disponible".

Cada crítico devuelve una lista de hallazgos con esta forma:

```
severidad: alta | media | baja
lente: <nombre del lente>
donde: <sección o línea del artefacto>
problema: <qué está mal, en una frase>
correccion: <qué habría que cambiar>
```

Reglas del panel:

- Un crítico que no encuentra nada devuelve lista vacía. **No se inventan
  hallazgos para justificar la corrida.**
- Severidad `alta` = el artefacto es inservible o incumple gobernanza.
  `media` = degrada calidad o deja ambigüedad real. `baja` = mejora opcional.

### 5. Verificación adversarial (según `adversarial` resuelto en § Parámetros)

Solo corre si `adversarial: true` en el presupuesto resuelto en § Parámetros
(`estandar`: tier `alto`; `profundo` lo habilita también en `medio` — no
asumir "solo tier alto"). Antes de refinar, correr
`python3 .spec/scripts/effort_profile.py resolve --unit <ruta-unidad> --role sdd-refutador`
y lanzar ese `subagent_type`/`model`; si el comando falla, parar y reportarlo
(CA-33). Cada hallazgo de severidad `alta` pasa por ese subagente cuya
instrucción es **refutarlo**: "intenta demostrar que este hallazgo es falso o
irrelevante; ante la duda, refuta". Los hallazgos refutados se descartan y se
anotan en bitácora. Esto evita que el refinamiento persiga críticas
plausibles pero incorrectas.

### 6. Refinar

- **MUST** declarar `causa_raíz: <file:line>` para todo hallazgo `alta` antes de
  aplicar su corrección. Sin esa declaración la corrección **no se considera
  resuelta** y el gate **no puede** ser `aprobado`.
- Aplicar al artefacto las correcciones de los hallazgos `alta` y `media`
  supervivientes. Los `baja` se anotan en bitácora y no bloquean.
- Si un hallazgo `alta` **no puede** resolverse sin una decisión humana
  (conflicto de gobernanza, ambigüedad del negocio, cambio de alcance): no se
  inventa la respuesta. Se marca como hallazgo sin resolver y el gate escalará.

### 7. Convergencia

Una **iteración** = una tanda de panel (paso 4) más su refinamiento (paso 6).
`gates.<fase>.iteraciones` es el contador **acumulado por fase entre
invocaciones** del gate — no un contador de sesión: toda re-corrida del gate
sobre la misma fase suma sobre ese mismo contador, sea cual sea el veredicto
que dejó la corrida anterior. El presupuesto que resuelve
`effort_profile.py resolve --gate` (§ Parámetros) es **techo duro** sobre ese
acumulado: no hay excepción de ningún tipo — ni por criterio del panel ni por
autorización humana — que extienda ese tope. La única vía para seguir
trabajando una fase que llegó al tope
es dejarla `escalado` y resolver fuera del gate — `rehabilitado_por` (abajo)
autoriza **intentar de nuevo desde cero de criterio**, nunca sumar cupo.

Tras cada iteración, decidir con esta regla, en este orden:

1. **Un hallazgo `alta`/`media` reaparece** — mismo lente **y** misma
   ubicación o misma causa raíz que un hallazgo de la iteración anterior que
   el refinamiento (paso 6) ya había dado por corregido — → veredicto
   `escalado` con causa `sin-convergencia`, sin gastar más iteraciones: el
   refinamiento ya lo intentó una vez sobre ese punto y no cerró; seguir
   iterando ahí no es mecánico, es una señal para el humano.
2. **La iteración no reduce el número de hallazgos `alta`/`media` abiertos**
   que encontró el panel (paso 4) respecto de los que encontró en la
   iteración anterior — igual o más, nunca menos — → veredicto `escalado`
   con causa `sin-convergencia`, aunque los hallazgos concretos sean
   distintos y aunque el refinamiento de esta iteración los haya resuelto
   todos: el conteo sin bajar es evidencia de que el artefacto no converge,
   no de que el panel encontró cosas nuevas legítimas.
3. **Quedan hallazgos `alta` o `media` sin resolver** (y no aplican 1 ni 2)
   → veredicto `escalado`, sin gastar más iteraciones. La `causa` que se
   persiste en el paso 8 depende de por qué quedaron sin resolver:
   `hallazgos-sin-resolver` si no se pudieron corregir dentro del alcance;
   `decision-humana` si lo que falta es una decisión que el gate no puede
   tomar por su cuenta.
4. **Todos los hallazgos `alta` y `media` quedaron resueltos** (y no
   aplican 1 ni 2):
   - Si **queda presupuesto** de iteraciones en el tier → correr una tanda más
     de panel sobre el artefacto ya refinado. Si esa tanda no encuentra nada
     nuevo `alta`/`media`, veredicto `refinado`. Si encuentra, volver a 6 —y
     re-evaluar 1-2 contra esta iteración en la siguiente vuelta.
   - Si **no queda presupuesto** → veredicto `refinado`. Un hallazgo corregido
     es un hallazgo cerrado: agotar el tope **después** de resolver todo no
     convierte el resultado en `escalado`.
5. **La primera tanda no encontró ningún hallazgo `alta`/`media`** → veredicto
   `aprobado`.

En tier `bajo` (1 iteración) esto significa: una tanda de panel; si no hay nada,
`aprobado`; si hubo hallazgos y se corrigieron todos, `refinado` sin re-crítica;
si alguno no se pudo corregir, `escalado`.

Un gate **nunca** devuelve `aprobado` ni `refinado` con hallazgos `alta` o
`media` abiertos, y **nunca** devuelve `escalado` por el solo hecho de haber
usado todo su presupuesto — `sin-convergencia` (1-2 arriba) es distinto de
eso: escala por el **patrón** entre iteraciones (reaparición o conteo
estancado), no porque se acabaron las iteraciones.

**Re-correr una fase `escalado` exige decisión humana registrada antes**
(ver también § 1). El humano registra
`gates.<fase>.rehabilitado_por: {por, en, motivo}` en `_estado.yaml` y una
línea en `bitacora.md` antes de que el gate vuelva a correr sobre esa fase.
Esa re-corrida **no reinicia** `iteraciones` — sigue sumando sobre el mismo
contador acumulado (arriba) y sigue sujeta al mismo techo duro.

### 8. Persistir el veredicto

En `_estado.yaml > gates > <fase>`:

```yaml
gates:
  <fase>:
    veredicto: aprobado | refinado | escalado
    tier: bajo | medio | alto  # valor de `_estado.yaml > riesgo`; se persiste
                                # siempre, en cualquier modo
    causa: <solo si escalado>   # sin-gobernanza | decision-humana |
                                # hallazgos-sin-resolver | validacion-en-rojo |
                                # determinista-irresoluble | sin-convergencia
    iteraciones: <n>            # acumulado por fase entre invocaciones (§ 7);
                                # una re-corrida SUMA sobre este mismo valor,
                                # nunca lo reinicia
    rehabilitado_por:           # SOLO si esta corrida re-abre una fase cuyo
      por: <quién>              # último veredicto persistido era `escalado`
      en: <ISO-8601>            # (§ 7); ausente en cualquier otro caso
      motivo: <por qué se re-habilita>
    hallazgos:                  # SOLO los que quedaron sin resolver
      - severidad: alta
        lente: <nombre>
        donde: <sección o línea>
        problema: <una frase>
        correccion: <qué habría que cambiar>
    hallazgos_resueltos: <n>    # cuántos se corrigieron en este gate
    governance_consultada: si | parcial | no
    criticos:                   # qué se lanzó realmente por cada lente (paso 4)
      - {lente: <nombre>, subagente: <sdd-critico-*>, modelo: <el resuelto>, perfil: <vigente>}
    refutador:                  # solo si corrió el paso 5 (adversarial: true); si no, []
      - {subagente: sdd-refutador, modelo: <el resuelto>, perfil: <vigente>, hallazgos_evaluados: <n>}
```

Los hallazgos se guardan **con su forma completa**, no como texto suelto: quien
retome la unidad tiene que poder ver qué lente encontró qué y dónde, sin releer
la bitácora. `hallazgos_resueltos` es un contador — el detalle de lo corregido
va en la bitácora. `criticos`/`refutador` son auditoría de modelo/perfil —
ver `.spec/MODELO-AGENTES.md` — y no hay que preguntarle a cada crítico qué
modelo es: el `subagente` y el `modelo` salen de la resolución real del paso 4
(o son `general-purpose` sin modelo fijo si se usó la degradación de ese
paso), y `perfil` es el vigente de `_estado.yaml > perfil` (o `estandar` si no
está fijado) al momento de lanzarlo.

Y anexar a `bitacora.md` el detalle narrativo: qué lente encontró qué, qué se
corrigió, qué se descartó por refutación, qué quedó abierto.

### 9. Devolver control

- `aprobado` / `refinado` → la skill de fase continúa el flujo.
- `escalado` → **parar**. Presentar al humano los hallazgos abiertos y la causa.
  En modo semi-autonomo esto rompe la autonomía a propósito: el modo semi-autonomo
  automatiza el trabajo, no la decisión. En **modo supervisado** no hay humano en
  la sesión: en vez de presentar al humano, devuelve el control a
  `sdd-supervisado`, que congela el mandato — desbloquea solo el autor de la
  entrada de mandato vigente.

## Contrato con las skills de fase

| Invoca | Con fase | Qué hace con el veredicto |
|---|---|---|
| `sdd-especificar` | `spec` | `escalado` → no pasa a plan |
| `sdd-planificar` | `plan` | `escalado` → no pasa a tareas |
| `sdd-tareas` | `tasks` | `escalado` → no pasa a implementar |
| `sdd-implementar` | `codigo` | `escalado` → no marca `fase: done` |

## Output

Artefacto refinado en disco + `_estado.yaml > gates > <fase>` con el contrato de
veredicto + entrada en `bitacora.md`.
