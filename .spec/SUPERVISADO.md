# Documentación del modo supervisado

> Referencia **única** de los artefactos del modo supervisado del kit SDD:
> plantillas de mandato, lista de paradas, contrato del registro, validador y
> campos nuevos de `_estado.yaml`, además de la conducta de un mandato en
> marcha (§ Orquestación). Todo campo que se añada al modo se documenta en esta
> misma lista — nunca en una segunda.
>
> **Procedencia:** este artefacto y la orquestación que lo conduce (skill
> `sdd-supervisado`) se introdujeron en las unidades `0109a` (artefactos) y
> `0109b` (orquestación) de este repo. Las referencias `0109a`/`0109b` CA-NN
> que aparecen abajo son los criterios de aceptación de esas unidades: se
> conservan por trazabilidad.
>
> Cualquier duda de fondo sobre el modo se resuelve leyendo la gobernanza
> aplicable vía el MCP, no en este archivo.

## 1. Artefactos entregados

### Plantilla del plan maestro por objetivo

- **Dónde vive:** `.spec/_plantillas/plan-maestro.md`.
- **Qué es:** el molde de un mandato supervisado por **objetivo** (varias
  unidades miembro con una cadena de dependencias viva). Un humano lo
  instancia bajo `.spec/planes/<id>/` (D-17) para abrir un mandato nuevo.
- **Quién lo escribe:** un humano, al redactar un plan nuevo; la orquestación
  de `0109b` lo rellena progresivamente mientras el mandato corre.
- **Contenido:** las 18 anclas literales de `spec.md` CA-01 (`## Objetivo y
  criterio de salida` … `## Revisión posterior`, con las tres subsecciones de
  `## Delegaciones`). En `## Cadena de dependencias`, la cadena se escribe en
  una línea `Vigente:` con los ids separados por `→` (`/` entre ramas
  paralelas) y toda anotación entre paréntesis: es la línea que lee el
  validador para CA-07.

### Plantilla del mandato de unidad aislada

- **Dónde vive:** `.spec/_plantillas/mandato.md`.
- **Qué es:** el molde de un mandato supervisado para **una sola unidad** SDD
  (D-15). Se instancia como `.spec/units/<NNNN-slug>/mandato.md` salvo que
  el `plan.md` de esa unidad fije otro nombre (D-16).
- **Quién lo escribe:** un humano, al marcar una unidad como `modo:
  supervisado`; la skill `sdd-supervisado` la rellena mientras corre.
- **Contenido:** las anclas de CA-01 menos las tres de agrupación (`##
  Unidades miembro`, `## Cadena de dependencias`, `## Registro de
  revisiones`), más `## Unidad amparada` (CA-02).

### Lista única de condiciones de parada

- **Dónde vive:** `.spec/PARADAS-SUPERVISADO.md`.
- **Qué es:** el único archivo bajo `.spec/` que enumera las condiciones de
  parada indelegables del modo supervisado (CA-15, CA-16). Ambas plantillas la
  referencian por ruta en `## Condiciones de parada` — **no la reproducen**.
- **Quién la escribe:** se edita solo por SDD, nunca por el agente en medio de
  un mandato en marcha.
- Cada condición cita su origen, y las formas en uso son exactamente tres:
  (1) un identificador de gobernanza que resuelve vía el MCP
  (`pri-ia-humano-decide`, `pol-gob-no-creacion-directa`, `pol-ia-retrieval-mcp`);
  (2) un archivo de este repo citado `ruta:línea` (condiciones 7-8); (3) una
  decisión de diseño propia del protocolo SDD de este repo, con su unidad de
  origen citada (condiciones 4-6, 9-12, 14).

### Contrato del registro de decisiones

- **Dónde vive:** la sección `## Registro de decisiones` de ambas plantillas
  (no es un archivo aparte).
- **Qué es:** el contrato de campos que toda decisión tomada bajo el mandato
  debe declarar: identificador (`<id-unidad>-D<secuencia>`, CA-01b), tipo
  (`autonoma` | `heredada`), unidad afectada, qué se decidió, alternativas
  consideradas, la pre-decisión o criterio aplicado (citado por identificador
  dentro del mandato — `heredada:<id-unidad>` para el tipo `heredada`), cómo
  revertirla, y estado de revisión (`pendiente` | `aceptada` | `revertida`)
  con fecha y quién (CA-11, CA-12).
- **Quién lo escribe:** la skill `sdd-supervisado` serializa las entradas;
  esta unidad solo fija el contrato que `validate_mandate.py` verifica.

### Validador `validate_mandate.py`

- **Dónde vive:** `.spec/scripts/validate_mandate.py` (usa `.spec/scripts/_common.py`).
- **Qué es:** el comando de validación local, sin agentes, de un mandato
  supervisado (CA-21). Es el `comando_validacion` de esta unidad.
- **Invocación:**
  - `python3 .spec/scripts/validate_mandate.py --plan <id|ruta>` — valida un
    plan por objetivo más los `_estado.yaml` de sus unidades miembro.
  - `python3 .spec/scripts/validate_mandate.py --unidad <ruta>` — valida el
    directorio de una unidad aislada (su `_estado.yaml` más su archivo de
    mandato).
  - `python3 .spec/scripts/validate_mandate.py --codigos` — imprime la lista
    completa de códigos que el validador puede reportar. **Esta
    documentación no transcribe esa lista**: es la fuente única (CA-25).
  - `python3 .spec/scripts/validate_mandate.py --unidad <ruta> --hash` — imprime el
    sha256 que un humano copia en su entrada de `## Aprobación`. `--hash` es un
    flag sin argumento: acompaña a `--unidad <ruta>` o a `--plan <id|ruta>`, y
    exactamente uno de los dos debe estar presente.
  - `python3 .spec/scripts/validate_mandate.py --plan <id|ruta> --resumen` — imprime
    los campos que `sdd-retomar` transcribe para una unidad supervisado
    (CA-29): `forma`, `mandato`, `estado-mandato`, `ultima-aprobacion` |
    `sin aprobar`, `punto-de-retoma` | `sin retoma`, `ultima-bitacora`, y la
    línea literal `retoma sin punto verificado` cuando corresponde.
- Salida por unión (sin corto-circuito entre códigos de contenido); exit
  distinto de cero si hay algún error; las advertencias no afectan al exit.

## 2. Campos nuevos de `_estado.yaml`

### `_estado.yaml`

| Campo | Qué es | Quién lo escribe | Valores |
|---|---|---|---|
| `modo: supervisado` | Tercer valor de `modo`, junto a `interactivo` y `semi-autonomo` (y `desatendido`). Solo válido acompañado del campo `mandato`. Un mandato **habilita** este modo, no lo asigna: la unidad nace en `modo: interactivo` y se convierte mediante `modo_conversion` (ver más abajo). | Un humano, mediante la conversión registrada; la skill `sdd-supervisado` transcribe el cambio, no lo decide. | `supervisado` |
| `modo_conversion` | Lista cronológica de conversiones de modo ya decididas por un humano: cada entrada trae `desde`, `hacia`, `en`, `por`, `tras`. Ver `.spec/_plantillas/_estado.yaml` para la forma completa. | Un humano, al convertir el modo de la unidad; ninguna skill la escribe por su cuenta. | lista, vacía en una unidad que sigue en `interactivo` |
| `mandato` | Referencia **única** (un solo valor escalar) al mandato que ampara la unidad: el identificador de un plan (`.spec/planes/<id>/`) o la ruta relativa al archivo de mandato de la propia unidad. | Un humano al declarar la unidad supervisado; la skill `sdd-supervisado` (CA-11) la sustituye al iniciar un plan aprobado — nadie más la reescribe. | id de plan `^[a-z0-9]+(-[a-z0-9]+)*$`, o ruta que termina en `.md` / contiene `/` |
| `validaciones_mandato` | Historial de corridas del validador sobre esta unidad: lista de `{fecha, fase, exit, codigos}`. | la skill `sdd-supervisado` (CA-08) — no se escribe a mano. | lista, vacía por defecto |

### Plantillas de mandato (`plan-maestro.md` / `mandato.md`)

| Campo/sección | Qué es | Quién lo escribe | Valores |
|---|---|---|---|
| `lanza:` | Campo **opcional e informativo** de cada entrada de `## Mandato`: el actor previsto para lanzar la orquestación. No restringe quién puede lanzarla en la práctica (D-19). | Quien firma la entrada de mandato. | forma canónica de actor (CA-01c), o ausente |
| `## Instancia en curso` | Mientras una orquestación corre bajo este mandato: identificador de sesión y fecha de arranque. Se vacía al parar o al cerrar. | la skill `sdd-supervisado` (CA-13). | vacía por defecto |
| `## Revisión posterior` | Por cada decisión revisada: identificador de la decisión, quién revisó (forma canónica de CA-01c), fecha, y resultado (`aceptada` \| `revertida`, con la tarea de reversión si fue revertida). | Un humano, al revisar el registro de decisiones. | una entrada por decisión revisada |
| `## Paradas` | Sección repetible: una entrada `### <fecha ISO-8601>` por condición de parada (CA-04 — como máximo un encabezado `##` de esta sección por documento). | La conducción de fase de la skill `sdd-supervisado`, mientras sostiene el lock de instancia (`instance_lock.py`) — el humano aporta la decisión (qué desbloquea, qué ocurrió) en el checkpoint; la conducción solo transcribe (`pri-ia-humano-decide`). Ver § 3 "Escritor único y colisión concurrente". | una entrada por parada |
| `## Punto de retoma` | Sección repetible: una entrada `### <fecha ISO-8601>` por retoma del mandato (CA-04 — mismo límite de un encabezado `##`). | Mismo escritor que `## Paradas`. Ver § 3 "Escritor único y colisión concurrente". | una entrada por retoma |

`0109b` documenta en esta misma lista todo campo nuevo que añada — no crea un
segundo inventario de campos.

### Modo por decisión humana y unidades en vuelo

Toda unidad nace en `modo: interactivo`, salvo que el humano mismo fije otro
modo en la petición inicial. Convertirla a `semi-autonomo`, `supervisado` o
`desatendido` se admite tras la fase `research` o tras el checkpoint del
`spec.md` — la ventana esperada, sin fricción adicional — o, más adelante
(`plan`, `tasks`, `implement`), solo cuando el propio humano registra esa
decisión de forma explícita en la misma conversación en la que la pide (no
una inferencia del conductor): es la excepción, no la regla, y existe para no
imponerle un modo sin pausas a una unidad ya profunda sin que quede clarísimo
que fue una decisión humana. La conversión queda registrada en
`_estado.yaml > modo_conversion` (una entrada por conversión, con `desde`,
`hacia`, `en`, `por`, `tras`) y en `bitacora.md`. Un mandato o plan maestro
**habilita** el modo `supervisado`/`desatendido`: no lo asigna
por sí solo.

**Unidades en vuelo.** Una unidad con `modo: supervisado`, `mandato` poblado y
sin `modo_conversion`, creada antes de **2026-09-24**, conserva su modo hasta
`fase: done` sin regularización retroactiva. Toda unidad creada después de
esa fecha, aun bajo el mismo mandato, nace en `interactivo` y se convierte por
el mecanismo anterior.

## 3. Reglas

### Fuente única del registro de decisiones (CA-13)

`## Registro de decisiones` es la **única** descripción de alternativas
consideradas y de cómo revertir cada decisión. `bitacora.md` y `plan.md` de
las unidades citan cada decisión por su identificador (`<id-unidad>-D<n>`)
— nunca reproducen su contenido.

### Línea de changelog (CA-18)

Al parar o cerrar un mandato, la entrada de ese mandato en el índice de mandatos
(`.spec/units/_mandatos-supervisado.md`, registro operativo no normativo) recibe
**una sola línea** fechada que referencia el mandato y su punto de retoma, sin
duplicar el detalle — el detalle vive en `## Paradas` / `## Punto de retoma` del
propio mandato. La entrada la crea la skill `sdd-supervisado` al marcar la unidad
(`SKILL.md` § 1.4); la línea, su § 12.

### Renovación del mandato (CA-08)

El mandato es una lista **append-only** de entradas fechadas. Renovarlo
significa **agregar una entrada nueva**, nunca editar la anterior. La firma
la puede poner **cualquier humano con acceso al repo** (D-19), identificado
en la forma canónica de actor (CA-01c). Tras una renovación, el autor de esa
entrada nueva pasa a ser "el autor de la entrada vigente" a efectos de
desbloqueo de paradas (CA-10) y de revisión de decisiones (CA-01g) — hasta
la siguiente renovación.

### Conducta ante presupuesto agotado del MCP (CA-20)

Por defecto: **parar**. Un MCP de gobernanza ausente o sin presupuesto se
trata igual que ausente (D-10); en ningún caso se continúa un gate ni se
resuelve una decisión con gobernanza de memoria o con una copia local. Esta
es una de las condiciones de `.spec/PARADAS-SUPERVISADO.md`; el mandato declara
esta misma conducta en su sección `## Paralelismo` (campo de presupuesto
esperado y conducta al agotarse, CA-19).

### Escritor único y colisión concurrente de `## Paradas` / `## Punto de retoma` (CA-05/CA-06)

Escritor único: la conducción de fase de la skill `sdd-supervisado`, mientras
sostiene el lock de instancia (`instance_lock.py` — ver `## Instancia en
curso` arriba). El contenido de la decisión (qué desbloquea una parada, qué
ocurrió) lo aporta el humano en el checkpoint; la conducción solo transcribe
(`pri-ia-humano-decide`) — nunca decide por su cuenta. Ninguna edición humana
directa debe ocurrir sobre estas dos secciones mientras el lock está tomado.

Colisión concurrente (CA-06): se resuelve con el lock de instancia existente
(`instance_lock.py acquire`/`release`) — la única primitiva de exclusión "una
sola conducción a la vez" que ya existe en este repo; no se introduce un
mecanismo nuevo. Su TOCTOU conocido (`acquire`/`release` sin `flock` ni
verificación de mtime) queda **fuera de alcance**: es un riesgo de
concurrencia real pero distinto — afecta a `## Instancia en curso`, no a
estas dos secciones — y se registra como hallazgo relacionado, no como algo
resuelto aquí.

### Ancla única de sección (CA-04/CA-07)

Cada sección repetible de un documento de mandato (mínimo `## Paradas` y
`## Punto de retoma`, y por extensión todo el vocabulario cerrado de
`PLAN_ANCHORS`/`UNIT_ANCHORS`) tiene como máximo un encabezado `##` por
documento; la repetición se expresa únicamente en sub-anchors (`### <fecha
ISO-8601>`), nunca duplicando el `##`. `validate_mandate.py`
(`check_sections()`) cuenta las ocurrencias de cada ancla sobre el texto
crudo del documento y falla con el código `seccion-duplicada` si alguna
aparece más de una vez — antes de que `sections()`
(`.spec/scripts/_common.py:148`) tenga oportunidad de colapsar un duplicado
en silencio (última ocurrencia gana). `sections()` no cambia su contrato de
lectura: sus consumidores (`worktree_registry.latest_stop_for_unit`,
`resume_point`/`check_resume`, `usage/indicators.py` K5) siguen recibiendo un
cuerpo único por ancla. Precedente distinto: `mandate_anchor.py` repara en
silencio su única ancla regenerable (`## Ancla de aditividad`); estas
secciones no son regenerables determinísticamente, así que el validador
falla explícito en vez de reparar.

## 4. Ampliación futura

la skill `sdd-supervisado` documenta en este mismo archivo (no en uno
nuevo) todo campo o artefacto que añada mientras hace **cumplir** lo que aquí
solo se **declara** — la distinción entre "expresable" (esta unidad) y
"cumplido" (`0109b`) queda en `spec.md` § Resultado esperado.

## Orquestación (0109b)

Conduce un mandato supervisado ya **aprobado** (`0109a`) sin más checkpoints
humanos que las paradas que ella misma reconoce. La mecánica completa vive en
`.agents/skills/sdd-supervisado/SKILL.md` (`0109b`); esta sección documenta
solo dónde queda cada pieza, sin repetir su contenido.

### Lanzamiento

- **Skill:** `.agents/skills/sdd-supervisado/SKILL.md` —
  invocable como `/sdd-supervisado <id-plan|ruta-unidad>`.
- **Quién puede lanzarla:** cualquier humano con acceso al repo (D-19,
  § Decisiones heredadas del piloto `0109b`); el campo `lanza:` de la entrada de
  mandato es informativo, no restrictivo — si el lanzador real difiere, se
  anota, no se rechaza.
- **Identificador de sesión:** la URL `Claude-Session` que declara el
  harness o, si no está disponible, `<actor>@<hostname> <ISO-8601>`. El
  mismo identificador viaja a toda entrada de bitácora, parada y retoma que
  la skill escribe durante esa corrida.
- **Instancia única y su lock:** al arrancar escribe sesión, lanzador y
  fecha en `## Instancia en curso` del mandato (`0109a` CA-01f); una segunda
  invocación que la encuentra ocupada para sin tocar nada y lo reporta. Se
  libera —vacía— al parar o al cerrar; fuera de ese flujo solo el autor de la
  entrada de mandato vigente puede liberarla, con el mismo criterio de
  desbloqueo que cualquier parada (CA-05).

### En el bucle

- **`validaciones_mandato` en uso:** la orquestación corre
  `validate_mandate.py` antes de cada fase de una unidad amparada, en cada
  retoma y antes de cerrar, y deja una entrada `{fecha, fase, exit, codigos}`
  por corrida en el campo que declara `0109a` (CA-04, CA-25). Los códigos de
  la lista humana sin acción (`.spec/PARADAS-SUPERVISADO.md`,
  `aprobacion-desactualizada`, `retoma-incompleta`,
  `cierre-con-pendientes`) nunca se
  auto-corrigen; cualquier otro código admite una auto-corrección y
  re-corrida con tope de 2 intentos, cada uno registrado.
- **`tier` en `gates`:** cada veredicto que `sdd-gate` registra en
  `_estado.yaml > gates.<fase>` lleva, además del veredicto de siempre, un
  campo `tier` con el `riesgo` de la unidad — aditivo, sin cambiar rúbricas,
  tiers ni conteo de iteraciones de `sdd-gate` para `interactivo`/`semi-autonomo`.

### Por qué es una skill inline, sin modelo propio

`sdd-supervisado` corre en la misma conversación que dispara el fan-out y
persiste en el árbol compartido, con el molde de `sdd-orquestar`: un
subagente no anida el tool `Agent` (`.spec/MODELO-AGENTES.md`), así que la
orquestación no puede vivir en uno. Delega cada fase y cada gate a las
skills existentes (`sdd-especificar`, `sdd-planificar`, `sdd-tareas`,
`sdd-implementar`, `sdd-gate`), que ya delegan su trabajo pesado a los
subagentes de siempre — no añade ninguno nuevo. Por eso no tiene una entrada
propia en `.spec/MODELO-AGENTES.md`, que es el registro de asignación de
modelo/effort por rol.

### Escenarios probados

Los once escenarios de prueba (`E00`..`E10`) — uno por cada condición
conductual que el piloto `0109b` exige demostrar sobre una unidad de prueba —
se corrieron en el piloto (`0109b`) y no están versionados en este repo. Se
ejecutaron en orden, uno a la vez, sobre una única unidad de prueba compartida
(`escenario-de-prueba-dedicado`), cada uno con su seed, su inducción y sus aserciones.

### Cómo inducir un MCP ausente

Para los escenarios que exigen simular la caída del MCP de gobernanza
(`pce-mcp`) sin mutar el `.env` real ni el entorno que
comparte el orquestador, se lanza `claude` con `--strict-mcp-config` y un
`--mcp-config` que declare `pce-mcp` sobre un comando que siempre falla: así
toda tool del servidor devuelve error sin tocar nada fuera de la sesión.

