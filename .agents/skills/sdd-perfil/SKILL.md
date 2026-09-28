---
name: sdd-perfil
description: "Use when: fijar o consultar el perfil de esfuerzo (`ligero`|`estandar`|`profundo`) de la unidad SDD activa, o fijar/mostrar/limpiar el archivo de perfiles local activo por clon (`set-file`/`show`/`clear-file`) — cuánto se gasta en modelo/effort por rol, presupuesto del gate y exploradores, sin tocar `riesgo`. Does NOT elegir el modo ni el tier de riesgo (eso lo fija el triaje de `sdd-orquestar`), ni invocar subagentes. Keywords: perfil, esfuerzo, /sdd-perfil, ligero, estandar, profundo, modelo, effort, archivo activo, set-file, clear-file."
compatibility: "Claude-first (lee/escribe `_estado.yaml`). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
model: haiku
metadata:
  user-invocable: "true"
---

# Skill: SDD — Perfil de esfuerzo

Orquestador **delgado**: invoca `.spec/scripts/effort_profile.py` con lo que
recibe y transcribe su salida. No invoca subagentes, no decide valores por su
cuenta. `.spec/perfiles.yaml` es la fuente canónica y la única compartida —
lo único que otro clon o colaborador ve. Un archivo local de perfiles (eje 2,
más abajo) es una preferencia de este clon: no viaja con el repositorio ni
cambia lo versionado.

Esta skill cubre **dos ejes independientes**. Fijar uno no afecta al otro
(CA-23): `/sdd-perfil <perfil>` no crea, borra ni modifica el puntero del
archivo activo; `set-file`/`clear-file` no tocan `_estado.yaml > perfil` de
ninguna unidad.

## Eje 1 — Perfil de esfuerzo de la unidad

`/sdd-perfil <nombre> [<ruta-unidad>]`

- `<nombre>`: uno de `ligero`, `estandar`, `profundo`.
- `<ruta-unidad>` (opcional): ruta, id o slug de la unidad. Si se omite, se
  usa la unidad activa que el propio script descubre (mismo mecanismo de
  `sdd_retomar.py`).

### Flujo

1. Correr `python3 .spec/scripts/effort_profile.py set <nombre> [--unit <ruta-unidad>]`.
2. Si el comando sale con código ≠ 0, transcribir su mensaje de error tal
   cual y **parar** — no reintentar con un valor distinto ni asumir un
   default:
   - **CA-08** — sin unidad activa y sin `<ruta-unidad>`: el script falla
     ("no existe la unidad" / unidad ambigua) y **no escribe** ningún
     archivo.
   - **CA-09** — `<nombre>` no declarado en el perfil resuelto (versionado
     más el archivo activo, si hay uno): el script falla y **no escribe**
     ningún archivo.
3. Si el comando sale con código 0, correr
   `python3 .spec/scripts/effort_profile.py show --unit <ruta-unidad>` y
   mostrar el perfil resultante (roles, presupuesto del gate, exploradores)
   en prosa breve — no pegar el JSON crudo completo.

### Alcance y efecto

El perfil nuevo **rige desde la siguiente invocación de subagente** (CA-29):
esta skill solo escribe `perfil: <nombre>` en el `_estado.yaml` de la unidad;
no relanza ni reinterpreta ningún paso ya corrido de la fase actual. Es
aceptado en los checkpoints humanos de `sdd-especificar` (paso 4),
`sdd-planificar` (paso 5) y en el paquete de aprobación — en cualquiera de
esos puntos, invocar esta skill y seguir con el flujo normal de la fase.

## Eje 2 — Archivo de perfiles activo (por clon)

Un archivo local `.spec/perfiles.<nombre>.yaml`, fuera del control de
fuente, puede pisar o extender el versionado sin tocarlo. El alcance es
**por clon, no por sesión**: dos sesiones abiertas sobre el mismo clon
comparten la misma selección, porque ambas leen el mismo puntero en disco
(`.spec/.perfiles-activo`).

### Fijar — `set-file <nombre>`

`python3 .spec/scripts/effort_profile.py set-file <nombre>`

Antes de escribir el puntero corre la resolución completa (estructura,
fusión, completitud, guarda de variantes) como dry-run:

- `<nombre>` sin `.spec/perfiles.<nombre>.yaml` en disco: sale ≠ 0 y **no**
  escribe el puntero.
- Éxito (código 0): escribe el puntero y reporta, ahí mismo, qué perfiles
  del overlay pisan uno del versionado y cuáles son nuevos. Un nombre de
  perfil mal escrito dentro del archivo overlay **no** es error de
  validación — declarar perfiles nuevos es capacidad contratada del
  archivo local —, así que el typo se ve revisando esa lista: aparece como
  perfil "nuevo" inesperado, no como fallo.

### Mostrar — `show`

`python3 .spec/scripts/effort_profile.py show [--unit <ruta-unidad>]`

Reporta en una sola salida el archivo activo (o que no hay ninguno) y el
perfil vigente de la unidad, incluyendo los mismos pisados/nuevos que
`set-file` cuando hay un archivo activo.

### Limpiar — `clear-file`

`python3 .spec/scripts/effort_profile.py clear-file`

Borra `.spec/.perfiles-activo`. Sale 0 también si se repite sin puntero
presente — no hace falta comprobar antes si existe.

## Lo que este eje NO toca

`.claude/agents/`, `scripts/materialize_claude_agents.py`,
`scripts/materialize_claude_skills.py` y `validate_protocol_drift.py` leen
siempre `.spec/perfiles.yaml` **versionado**. El archivo activo del clon
nunca alimenta ninguna superficie que el repositorio distribuya.
