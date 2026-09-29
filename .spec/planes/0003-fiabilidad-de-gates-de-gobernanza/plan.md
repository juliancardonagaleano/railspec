# Plan maestro — Fiabilidad de gates de gobernanza

- id: 0003-fiabilidad-de-gates-de-gobernanza

> Plan maestro de una sola unidad. Ampara a
> `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/` para correr su ciclo SDD
> completo bajo `modo: desatendido`: 1 aprobación upfront del mandato (esta
> sección `## Aprobación`), sin checkpoints intermedios, con gates que escalan
> marcados como `escalado: auto-deferred`. Cubre los 8 fixes priorizados en el
> análisis del incidente del 2026-09-29 (triada del `sdd-gate § 3` perdió
> `opencode.jsonc`; 7 bugs en skills/agents + 3 en orquestador).
>
> **Por qué desatendido y no supervisado o interactivo**: el operador aprobó
> continuar sin checkpoints (`Continúa de modo desatendido`); no se requiere
> intervención por unidad; las decisiones que lo requieran están en
> `### Reservadas` (no se tomaron en este plan). El mandato es de una sola
> unidad, así que la diferencia práctica con `supervisado` es nula — pero el
> protocolo pide `desatendido` con plan maestro (ver `_estado.yaml` de la
> unit: "`hacia: desatendido` exige `mandato` poblado con el id de un plan
> maestro que liste esta unidad en su `## Unidades miembro`").
>
> **Criterio de salida**: la unit 0003 llega a `fase: done, estado:
> completado`; `python3 -spec/scripts/validate_mandate.py --plan
> 0003-fiabilidad-de-gates-de-gobernanza` pasa; re-corrida del gate del
> spec de la unit 0002 con el script nuevo (`check_governance_surface.py`)
> aprueba con la lista declarativa del spec post-delta de 0002 (los 5 CA
> nuevos CA-51..CA-55).

## Objetivo y criterio de salida

Llevar la unit `0003-fiabilidad-de-gates-de-gobernanza` de `fase: spec`
(actual) a `fase: done` bajo `modo: desatendido`, aplicando los 8 fixes
priorizados que cierran el incidente del 2026-09-29 donde el gate de spec de
la unit 0002 aprobó `governance_refs: [ninguna-aplicable]` a pesar de que
`opencode.jsonc` ya declaraba `pce-mcp`. Criterio de salida verificable,
sin interpretar:

1. La unit 0003 reporta `_estado.yaml > fase: done, estado: completado`,
   con su `comando_validacion` (`python3 -m pytest .spec/scripts/tests
   installer -q`) en verde.
2. `python3 .spec/scripts/validate_mandate.py --plan
   0003-fiabilidad-de-gates-de-gobernanza` pasa (exit 0, ningún código
   reportado).
3. El script nuevo `.spec/scripts/check_governance_surface.py` existe,
   tiene tests, y la re-corrida del gate del spec de la unit 0002 con ese
   script (lista declarativa + segunda red) aprueba los 5 CA nuevos
   CA-51..CA-55 sin escala.

## Unidades miembro

- 0003-fiabilidad-de-gates-de-gobernanza

(No hay otras unidades miembro. La unit 0002 ya está cerrada en su ciclo
`tasks` post-delta; el efecto de 0003 sobre 0002 es liberar la dependencia
declarada en el `_estado.yaml > dependencias` de 0002 — ver bitácora de 0002
del 2026-09-29T05:45:00Z. La unit 0002 no es **amparada** por este plan; es
una unidad externa que se re-correrá tras el cierre de 0003, sin pertenecer a
este plan maestro.)

## Cadena de dependencias

Vigente: 0003-fiabilidad-de-gates-de-gobernanza

(Plan de una sola unidad; no hay cadena de amparadas. La unidad externa 0002
se libera como efecto colateral del cierre de 0003, pero eso ocurre fuera
del ámbito de este plan y no se declara aquí.)

## Registro de revisiones

(Plan recién instanciado — sin revisiones aún. Toda edición futura a
`## Cadena de dependencias` o a la lista de `## Unidades miembro` queda
registrada aquí.)

### 2026-09-29T05:50:00Z
- que-cambio: instancia inicial del plan maestro
- motivo: la unit 0003 se abrió en `modo: interactivo` por el operador
  (`Abre una unidad nueva para aplicar todo de 1 a 8`); el operador pidió
  luego `Continúa de modo desatendido`, lo que requiere este plan maestro
  con la unit 0003 listada en `## Unidades miembro` y `## Estado: aprobado`.

## Mandato

Lista **append-only**. La entrada vigente (la única por ahora) delimita la
ventana de la orquestación desatendida.

### 2026-09-29T05:50:00Z
- autor: Julian
- lanza: orquestador (inline o `sdd-implementar` con perfil estandar)
- inicio: 2026-09-29T05:50:00Z
- fin: 2026-09-30T05:50:00Z

(24 horas de ventana — tope conservador para una unit de tamaño medio. Si
el ciclo no cierra en ese plazo, el plan entra en `## Paradas` con causa
`mandato-fin`. El validador exige `fin` obligatorio.)

## Delegaciones

### Pre-decididas

| Id | Decisión | Pre-decisión | Impacto |
|---|---|---|---|
| PD-1 | Stack de orquestación | Solo el orquestador inline; sin subagentes externos para redactar (el spec ya está escrito; plan/tasks los escribe el orquestador con la misma prosa usada en 0002) | Reduce variabilidad y costo de tokens. |
| PD-2 | Triage de gates escalados | Gates que escalan se marcan `escalado: auto-deferred` y se continúa con la siguiente fase, sin parar el mandato entero (no hay "siguiente unidad" porque el plan ampara solo una) | El plan puede cerrar sin reabrir si el gate del spec falla: la unidad 0003 queda en `escalado: auto-deferred` y la unit 0002 sigue bloqueada. |

### Con criterio

| Id | Decisión | Criterio a aplicar | Impacto |
|---|---|---|---|
| CR-1 | Estilo de redacción del plan | Reutilizar el patrón del plan 0002 (4 grupos, archivos disjuntos, decisiones de diseño con razón explícita, riesgos con mitigación) | Mantiene consistencia con el resto del repo. |
| CR-2 | Estilo de redacción de tasks | Misma convención `u0003_ca<NN>`; trazabilidad uno-a-uno CA ↔ tarea; granularidad atómica | El gate de tasks verifica cobertura por conteo. |
| CR-3 | Cuándo llamar al script nuevo | El gate del spec lo invoca desde el paso 3 ("recuperar gobernanza aplicable"); el gate de plan/tasks invoca el chequeo de hash de filesystem (Fix 2) | El script encapsula la lógica para que sea testeable y reutilizable. |

### Reservadas

| Id | Decisión | Por qué se reserva | Impacto de no decidirla |
|---|---|---|---|
| RS-1 | Renombrar `tests/test_settings_cableado.py` (plan 0002) a `test_config_cableado.py` | El plan 0002 ya renombró el módulo (`settings_cableado.py` → `config_cableado.py`); renombrar el test es trivial pero toca archivos de 0002 (no de 0003); una re-corrida del gate de tests de 0002 detectará el desfase | Si no se renombra, los tests de 0002 fallan al correr (el módulo ya no existe); reabrir 0002 no es opción mientras dependa de 0003. |
| RS-2 | Orden de aplicación de los 8 fixes | Los fixes tienen dependencias (Fix 7 — auditor como script — es prerrequisito para que el skill use el script; Fix 6 — vigencia del centinela — necesita campos en `_estado.yaml` que no existen formalmente). Pero los criterios de aceptación son por fix y se pueden implementar en cualquier orden que respete las dependencias internas. | Si se aplica en orden distinto al propuesto, el spec sigue siendo válido; el plan debe documentar el orden elegido. |

## Condiciones de parada

Referencia — **no transcripción** (`pri-gob-fuente-verdad-unica`,
`pol-ia-no-embeber-conocimiento`): `.spec/PARADAS-SUPERVISADO.md`. El validador
falla con `paradas-sin-referencia` si esta sección no contiene esa referencia.

Ver también: `.spec/PARADAS-SUPERVISADO.md`.

## Paralelismo

- carriles: sdd (solo este mandato toca el protocolo SDD mismo — skill
  `sdd-gate`, `sdd-orquestar`, script `.spec/scripts/check_governance_surface.py`,
  `effort_profile.py`)
- tope-worktrees: 1 (un solo stack de orquestación; no hay paralelismo real
  porque el plan ampara una sola unidad)
- dueno-stack-vivo: orquestador inline (la sesión que conduce)
- presupuesto-mcp: 0 (no se invoca `pce-mcp` durante este mandato — la
  superficie de gobernanza del kit sigue siendo la que la unit 0002 reformuló:
  `pce-mcp` no se ejecuta contra el kit mismo; los fixes operan sobre el
  filesystem local)
- conducta-presupuesto-agotado: parar — condición "MCP de gobernanza ausente
  o sin presupuesto" de `.spec/PARADAS-SUPERVISADO.md` (D-10); en ningún
  caso continuar un gate con gobernanza de memoria

El validador falla con `paralelismo-sin-declarar` si falta cualquiera de
estos campos.

## Registro de decisiones

### 0003-D1
- tipo: autonoma
- unidad: 0003-fiabilidad-de-gates-de-gobernanza
- que-se-decidio: el modo de la unit es `desatendido` (no `interactivo` ni
  `semi-autonomo`), y este plan maestro se crea para ampararla porque la
  conversión `→ desatendido` exige mandato con id de plan maestro (ver
  `_estado.yaml > modo_conversion` y comentario asociado).
- alternativas: (a) correr el ciclo sin conversión formal, saltando los
  chequeos del validador — rechazado porque el protocolo exige plan maestro;
  (b) correr en `supervisado` con `mandato.md` dentro de la unit — posible
  pero la diferencia con `desatendido` es nula para una sola unidad y el
  operador pidió explícitamente `desatendido`.
- criterio: PD-1
- reversion: cambiar `modo_conversion` a `desde: desatendido / hacia:
  interactivo` y volver al modo interactivo. Requiere nueva aprobación del
  operador.
- revision: aceptada
- revision-fecha: 2026-09-29T05:50:00Z
- revision-quien: Julian

### 0003-D2
- tipo: autonoma
- unidad: 0003-fiabilidad-de-gates-de-gobernanza
- que-se-decidio: el orden de aplicación de los fixes en `tasks.md` es
  **Fix 7 primero** (crear el script `.spec/scripts/check_governance_surface.py`
  con sus tests), luego Fix 1+2+3+5 (modificar `sdd-gate/SKILL.md` para usar
  el script y añadir chequeos de hash y segunda red), luego Fix 4
  (fallback de `subagent_type`), luego Fix 6 (vigencia del centinela),
  finalmente Fix 8 (cross-check del orquestador en `sdd-orquestar/SKILL.md`).
- alternativas: orden secuencial 1, 2, 3, ..., 8 — rechazado porque Fix 1-5
  referencian al script que crea Fix 7; si se hace Fix 1-5 primero, hay que
  hardcodear la lógica en el skill y luego migrarla al script.
- criterio: PD-2
- reversion: reordenar tareas en `tasks.md` antes de implementar; o mover
  el script a un fix posterior y dejar la lógica en el skill como
  intermediario.
- revision: aceptada
- revision-fecha: 2026-09-29T05:50:00Z
- revision-quien: Julian

### 0003-D3
- tipo: autonoma
- unidad: 0003-fiabilidad-de-gates-de-gobernanza
- que-se-decidio: el script `check_governance_surface.py` se crea con
  argumentos CLI (`--unit <ruta-unidad>`, `--spec <ruta-spec>`,
  `--filesystem-root <ruta>`) y retorna código != 0 con mensaje claro en
  stderr ante cualquier falla (lista declarativa inconsistente, superficie
  no declarada, hash de filesystem divergente). El skill `sdd-gate` lo
  invoca vía `subprocess.run` y traduce la salida a veredicto del gate.
- alternativas: importarlo como módulo Python y llamar funciones — rechazado
  porque acopla el skill al módulo (testing requiere mock; deploy requiere
  PYTHONPATH); CLI mantiene el skill como orquestador y al script como
  herramienta.
- criterio: CR-3
- reversion: refactorizar el script para exponer funciones puras además del
  CLI; los tests existentes siguen pasando.
- revision: aceptada
- revision-fecha: 2026-09-29T05:50:00Z
- revision-quien: Julian

## Paradas

(Sin paradas abiertas. Este plan puede entrar en `## Paradas` si un gate
escala; el orquestador registra la parada con su formato canónico.)

## Punto de retoma

(Sin punto de retoma: el plan aún no ha corrido. La primera entrada se
añade cuando el primer gate escala o cuando termina la ventana.)

## Estado

- estado: aprobado

## Instancia en curso

(vacío — la orquestación aún no ha arrancado)

## Aprobación

### 2026-09-29T05:50:00Z
- quien: Julian
- hash: e465aabe74293f95d9d6c074b839de01176da7c6e95bb76bb009c843dc421602

(Hash calculado con `python3 .spec/scripts/validate_mandate.py --hash --plan
0003-fiabilidad-de-gates-de-gobernanza` después de redactar el cuerpo del
plan. Toda edición posterior al plan invalida este hash — el validador
reportará `aprobacion-desactualizada` hasta que se actualice el hash con
`validate_mandate.py --hash` y se agregue una entrada de `## Revisión
posterior` con la fecha y quien, sin re-aprobar el contenido.)

## Revisión posterior

(Sin revisiones posteriores aún.)

## Ancla de aditividad

(vacío — se completa con `mandate_anchor.py write` al primer lanzamiento,
unidad `0114`, `CA-29`/`CA-30`: una sola línea `commit: <40 hex>`, el commit
de `HEAD` en ese momento.)
