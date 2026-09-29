# Modelo de agentes

> Registro normativo del kit: cómo el protocolo SDD asigna modelo y effort
> por rol, cómo decide `subagent_type`, y qué hace cuando un subagente
> nombrado no está disponible en el runtime del destino.
>
> Fuente única de modelo/effort: `.spec/perfiles.yaml`. Esta página es
> **prosa explicativa** derivada de ese YAML — el parser vive en
> `.spec/scripts/effort_profile.py` (CLI + importable). Cualquier divergencia
> entre esta tabla y el YAML debe corregirse en el YAML; esta página se
> regenera por reempaquetamiento del kit, no se edita a mano.
>
> El kit produce `.mcp.json`, `opencode.jsonc`, `AGENTS.md` y
> `scripts/mcp-pce.sh` como carga con fusión con marcador; un destino
> configurado por el kit hereda esta página como prosa de gobernanza
> entregada al lector del destino, **no** como runtime de gobernanza
> aplicable al kit mismo (`pce-mcp` no se ejecuta contra el repo del kit —
> ver `.spec/SUPERVISADO.md` § Por qué es una skill inline, sin modelo
> propio, y `.spec/README.md` § Governance).

## Tabla de roles

Para cada rol, `modelo` y `effort` salen de `.spec/perfiles.yaml >
perfiles.<perfil>.roles.<rol>`. La columna `subagent_type` la resuelve
`effort_profile.py resolve --unit <ruta> --role <rol>` (puede ser el
nombre del rol mismo o una variante generada `<rol>-<effort>` cuando el
perfil cambia `effort` solo para `complejo`).

### Perfil `estandar` (default de `.spec/perfiles.yaml`)

Cada rol declara `modelo: <modelo>` y `effort: <effort>` (la columna
`subagent_type` la resuelve `effort_profile.py resolve --unit <ruta>
--role <rol>`):

- `sdd-critico-cumplimiento` — `modelo: sonnet`, `effort: low`.
- `sdd-critico-estructural` — `modelo: haiku`, `effort: base`.
- `sdd-critico-profundo` — `modelo: sonnet`, `effort: high`.
- `sdd-especificar-redactor` — `modelo: sonnet`, `effort: high`.
- `sdd-explorador` — `modelo: sonnet`, `effort: low`.
- `sdd-implementador` — `modelo: sonnet`, `effort: medium`.
- `sdd-planificar-redactor` — `modelo: sonnet`, `effort: medium`.
- `sdd-refutador` — `modelo: sonnet`, `effort: low`.
- `sdd-tareas-redactor` — `modelo: haiku`, `effort: base`.

Presupuesto del gate (`perfiles.estandar.gate`): `bajo` y `medio` =
`{criticos: 1, iteraciones: 1, adversarial: false}`; `alto` =
`{criticos: 2, iteraciones: 2, adversarial: true}`. Exploradores por tier:
`{bajo: 1, medio: 1, alto: 2}`. Implementador para grupos `Complejidad:
complejo`: `modelo: sonnet`, `effort: high`.

### Perfil `ligero`

Cada rol declara `modelo: <modelo>` y `effort: <effort>`:

- `sdd-critico-cumplimiento` — `modelo: haiku`, `effort: base`.
- `sdd-critico-profundo` — `modelo: sonnet`, `effort: low`.
- `sdd-especificar-redactor` — `modelo: sonnet`, `effort: medium`.
- `sdd-explorador` — `modelo: haiku`, `effort: base`.
- `sdd-implementador` — `modelo: sonnet`, `effort: low`.
- `sdd-planificar-redactor` — `modelo: haiku`, `effort: base`.
- `sdd-refutador` — `modelo: haiku`, `effort: base`.

Presupuesto del gate (`perfiles.ligero.gate`): solo `alto` =
`{criticos: 1, adversarial: false}`; los tiers `bajo` y `medio` caen al
default de `estandar` por ausencia de override. Exploradores solo para
`alto: 1`. Implementador para grupos `Complejidad: complejo`: `modelo:
sonnet`, `effort: medium`.

### Perfil `profundo`

Cada rol declara `modelo: <modelo>` y `effort: <effort>`:

- `sdd-especificar-redactor` — `modelo: sonnet`, `effort: xhigh`.
- `sdd-critico-profundo` — `modelo: sonnet`, `effort: max`.
- `sdd-implementador` — `modelo: sonnet`, `effort: xhigh`.
- `sdd-refutador` — `modelo: sonnet`, `effort: max`.

Presupuesto del gate (`perfiles.profundo.gate`): `medio` =
`{adversarial: true}`; el resto cae al default de `estandar`. Implementador
para grupos `Complejidad: complejo`: `modelo: sonnet`, `effort: max`.

## Modo desatendido

> Modo definido en `.spec/SUPERVISADO.md`: tanda de unidades bajo un plan
> maestro aprobado, ejecutadas en una sola corrida con **un paquete de
> aprobación upfront**, sin checkpoints intermedios. Cada unidad corre su
> flujo SDD completo (spec → plan → tasks → gate de cada fase →
> implementar → gate de código → cierre) hasta que un gate escala; las que
> escalan quedan `auto-deferred` (diferidas) y las del mismo plan siguen.

Bajo este modo, **el paralelismo es por grupos de una unidad**, no por
unidades del plan: el fan-out de `sdd-implementar` opera sobre los
`Grupos de tareas paralelizables` de `plan.md` (típicamente G1..G3 con
archivos disjuntos entre grupos), no entre unidades. Cada unidad sigue
siendo una unidad: tiene su `_estado.yaml`, su `bitacora.md` y sus gates.

La diferencia operativa con `semi-autonomo` es de **decisión**, no de
**mecánica**:

| | `semi-autonomo` | `desatendido` |
|---|---|---|
| Checkpoints humanos | 1 paquete de aprobación | 1 paquete de aprobación upfront |
| Gates que escalan | Pausa para humano | `auto-deferred`, sigue con la siguiente unidad del plan |
| Bitácora por unidad | igual | igual; la unidad diferida conserva el estado |

El plan maestro que cobija las unidades en `modo: desatendido` debe
listarlas en su `## Unidades miembro` y su paquete debe estar aprobado
antes de que `sdd-desatendido` arranque la tanda
(`.spec/SUPERVISADO.md` § Plan maestro). Sin plan maestro aprobado, una
unidad `modo: desatendido` no es válida — el validador
`validate_mode_conversion.py --unit <ruta>` lo rechaza.

## Auditoría de modelo/effort

Cada unidad SDD persiste en `_estado.yaml > modelo_ejecucion` qué rol,
modelo y effort corrió cada fase — `especificar`, `planificar`, `tareas`,
los 4 gates (`gate_spec`, `gate_plan`, `gate_tasks`, `gate_codigo`) y la
implementación (`implementar` con una entrada por grupo paralelo).

El campo es **memoria operacional del kit**, no metadato cosmético:
quien invoca al subagente lo registra al terminar — no se infiere
después, porque nadie más sabe con certeza qué se invocó de verdad.
Tres casos atípicos que vale la pena documentar:

- **Subagente degradado.** Si `effort_profile.py resolve --role <rol>`
  devuelve un `subagent_type` que el runtime del destino no reconoce,
  el orquestador lanza `general-purpose` con la misma lente y las
  mismas instrucciones que le tocaban a ese rol (precedente
  unit 0003, fix 4, CA-08) y persiste `subagente: general-purpose` en
  vez de inventar que corrió el rol previsto. Detalle operacional en
  `.agents/skills/sdd-gate/references/subagent-fallback.md`.
- **Inline por edición de plantilla.** Editar una plantilla
  existente (`spec.md`, `plan.md`, `tasks.md`) suele ser más eficiente
  que delegar a un redactor que reescribiría el documento entero. El
  orquestador anota `subagente: inline` y `nota:` con el porqué
  (precedente U-0002 — `sdd-planificar-redactor` y `sdd-tareas-redactor`
  no registrados como `subagent_type` en opencode, se degradaron a
  `general` con el mismo prompt del rol).
- **Orquestador-inline en gates sin tool Agent.** Si la sesión no
  expone `Agent`/`Task`, los críticos del gate se evalúan inline por
  el orquestador; se persiste `subagente: orchestrator-inline,
  modelo: sonnet, perfil: ligero` con la misma lente que habría
  corrido el subagente nombrado.

El comando `effort_profile.py show [--unit <ruta>]` imprime el perfil
activo y el resoluble por rol; `effort_profile.py set <perfil> [--unit
<ruta>]` cambia el `perfil:` de la unidad; `set-file`/`clear-file`
operan el puntero `.spec/.perfiles-activo` (eje independiente del
`perfil:` por unidad — `spec.md § Comportamiento contratado`).

## Subagentes y roles

El patrón operativo es **declarar antes de invocar**: para cada
`Agent`/`Task` que el orquestador lanza, primero resuelve su
`subagent_type` y `model` con

```
python3 .spec/scripts/effort_profile.py resolve \
  --unit <ruta-unidad> \
  --role <rol>
```

y luego pasa ambos al tool. Si el comando falla, **parar y reportarlo**
(CA-33 de `sdd-orquestar`) — nunca asumir un valor. Esto evita que un
cambio en `.spec/perfiles.yaml` quede silenciosamente sin aplicar: el
orquestador lee el YAML en cada invocación, no cachea.

La razón por la que `.spec/MODELO-AGENTES.md` existe como archivo y no
como entrada más del YAML es que las **prosa explicativas** de las
secciones canónicas (`## Modo desatendido`, `## Auditoría de
modelo/effort`, `## Subagentes y roles`) son referencias que otros
documentos del kit y de los destinos citan — 8 lugares en este repo
(`.spec/_plantillas/_estado.yaml`, `.spec/README.md`,
`.spec/SUPERVISADO.md`, `.spec/units/0002/0004/0005/_estado.yaml`,
`pre-commit-gate.sh`, `effort_profile.py`). Mantener la prosa en YAML
sería inflar el parser sin motivo; el YAML sigue siendo la fuente de
datos y este archivo es la fuente de prosa.

## Si un subagente nombrado no está disponible

Tres escenarios legítimos — los tres con el mismo fallback operacional
pero distinta causa raíz:

1. **`opencode` no tiene registrado el `subagent_type`** (p.ej. un clon
   nuevo donde `.claude/agents/` no se ha materializado vía
   `scripts/materialize_claude_agents.py`). El orquestador lanza
   `general-purpose` con la misma lente y las mismas instrucciones que
   le tocaban a ese rol; anota en `bitacora.md` qué rol se degradó;
   persiste `subagente: general-purpose` en `modelo_ejecucion.<fase>`
   (ver `.agents/skills/sdd-gate/SKILL.md` § "Fallback de `subagent_type`
   no registrados").
2. **El runtime del destino no expone `Agent`/`Task`** (p.ej. una sesión
   sin subagentes). El orquestador evalúa la lente inline y persiste
   `subagente: orchestrator-inline`. Es el caso documentado por
   unit 0010 (esta misma unidad) bajo modo `semi-autonomo` con perfil
   `ligero`.
3. **El `subagent_type` resuelve pero el `model` no** (p.ej. el runtime
   conoce el rol pero no el modelo). El orquestador aborta la corrida
   y reporta el error — no degrada el modelo silenciosamente, porque
   el modelo es parte del contrato del rol (no es intercambiable por
   costo).

En los tres casos, el veredicto del gate **sigue siendo válido** — el
fallback es operacional, no de cobertura: el crítico sigue aplicando
los mismos lentes, solo cambia quién lo ejecuta. La trazabilidad vive
en `_estado.yaml > modelo_ejecucion.<fase>.subagente` y `bitacora.md`.

## Referencias

- `.spec/perfiles.yaml` — fuente única de modelo/effort.
- `.spec/scripts/effort_profile.py` — CLI + parser.
- `.spec/SUPERVISADO.md` § "Por qué es una skill inline, sin modelo
  propio" — justificación del patrón.
- `.spec/README.md` § "Ejecución multi-agente" y § "Gates de validación"
  — prosa canónica sobre roles y presupuestos.
- `.spec/_plantillas/_estado.yaml` y `.spec/scripts/validate_mode_conversion.py`
  — campos validados del estado de cada unidad.
