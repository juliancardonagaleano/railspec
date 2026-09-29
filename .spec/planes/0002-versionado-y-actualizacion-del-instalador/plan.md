# Plan maestro — Cierre de la unit 0002 con re-fence de governance

- id: 0002-versionado-y-actualizacion-del-instalador

> Plan maestro de una sola unidad (modo desatendido). Ampara a
> `.spec/units/0002-versionado-y-actualizacion-del-instalador/` para correr su
> ciclo SDD **de implement** bajo `modo: desatendido`: 1 aprobación upfront
> del mandato (esta sección `## Aprobación`), sin checkpoints intermedios,
> con gates que escalan marcados como `escalado: auto-deferred`.
>
> **Por qué este plan existe ahora**: el operador aprobó "Procede de forma
> desatendida" tras verificar que (a) la unit 0003 está cerrada en `done`
> (la dependencia sobre los 8 fixes de governance-surface está satisfecha);
> (b) el re-gate del spec de 0002 con `check_governance_surface.py` retorna
> verdict `ok` y exit 0 (la lista declarativa del spec cubre los 4 archivos
> que el kit produce como carga). La unit 0002 puede ahora ejecutar su
> ciclo de implement sin intervención: 41 tareas en `tasks.md` con cobertura
> CA-01..CA-55, 4 grupos paralelos G1..G4, comando de validación verde.

## Objetivo y criterio de salida

Llevar la unit `0002-versionado-y-actualizacion-del-instalador` de `fase:
tasks` (gates spec/plan/tasks aprobados) a `fase: done` bajo `modo:
desatendido`, ejecutando las 41 tareas T1..T41 descritas en `tasks.md`
con sus 4 grupos paralelos G1..G4. Criterio de salida verificable, sin
interpretar:

1. La unit 0002 reporta `_estado.yaml > fase: done, estado: completado`,
   con su `comando_validacion` (`python3 -m pytest .spec/scripts/tests
   installer -q`) en verde.
2. `python3 .spec/scripts/validate_mandate.py --plan
   0002-versionado-y-actualizacion-del-instalador` pasa (exit 0).
3. Los 55 CA del spec tienen tests `u0002_ca<NN>_*` ejecutándose y pasando;
   ningún CA queda huérfano.

## Unidades miembro

- 0002-versionado-y-actualizacion-del-instalador

(No hay otras unidades. La unit 0003 que motivó la apertura de este
plan ya está cerrada y no es miembro: este plan maestro es **de una
sola unidad** para el cierre de 0002.)

## Cadena de dependencias

Vigente: 0002-versionado-y-actualizacion-del-instalador

(Plan de una sola unidad; no hay cadena de amparadas. La unit 0003 ya
cerró su propia cadena y libera la dependencia que tenía sobre 0002.)

## Registro de revisiones

### 2026-09-29T08:40:00Z
- que-cambio: instancia inicial del plan maestro
- motivo: el operador aprobó "Procede de forma desatendida" para el
  cierre de 0002, una vez satisfecha la dependencia sobre 0003 y con
  el re-gate del spec de 0002 verde.

## Mandato

### 2026-09-29T08:40:00Z
- autor: Julian
- lanza: orquestador (inline o `sdd-implementar` con perfil estandar)
- inicio: 2026-09-29T08:40:00Z
- fin: 2026-09-30T08:40:00Z

(24 horas de ventana — tope conservador para una unit de tamaño
grande. Si el ciclo no cierra en ese plazo, el plan entra en `## Paradas`
con causa `mandato-fin`. El validador exige `fin` obligatorio.)

## Delegaciones

### Pre-decididas

| Id | Decisión | Pre-decisión | Impacto |
|---|---|---|---|
| PD-1 | Stack de orquestación | Solo el orquestador inline; sin subagentes externos para redactar. Las tareas ya están en `tasks.md`; la implementación lee y ejecuta sin subagentes. | Reduce variabilidad y costo de tokens. |
| PD-2 | Triage de gates escalados | Gates que escalan en implement se marcan `escalado: auto-deferred` y la unit queda diferida; el plan puede cerrar con esa nota sin reabrir. | El plan es de una sola unit: si el gate de código escala, el plan cierra con la unit 0002 sin `done`. |

### Con criterio

| Id | Decisión | Criterio a aplicar | Impacto |
|---|---|---|---|
| CR-1 | Orden de ejecución de los 4 grupos | G1 (datos/CLI) y G4 (cableado) primero porque G2 (install/verify) los necesita; G3 (antidrift) en paralelo con G2 una vez G1 esté listo. | G1 establece la base (manifest, mcp-pce.sh); G4 crea `config_cableado.py` que G2 invoca; G2 implementa install/verify; G3 implementa check_install_drift.py. |
| CR-2 | Renombre de archivos preexistentes | `installer/settings_cableado.py` → `installer/config_cableado.py`; `installer/tests/test_settings_cableado.py` → `installer/tests/test_config_cableado.py`. Hacer el renombre en commit separado antes de empezar T8. | Evita confusiones con grep que asume el nombre viejo; tests preexistentes que asumen el nombre viejo fallarán hasta que se actualicen (parte de G4). |
| CR-3 | Política ante test preexistente fallando | `test_test_subset.py::test_no_orchestrator_ni_studio_app_cableados_en_el_codigo` ya fallaba antes de unit 0003 (verificado con `git stash`). CA-20 excluye explícitamente este fallo preexistente. Si el implement de 0002 causa nuevos fallos en tests preexistentes (no este), revertir el cambio y re-planificar. | Mantiene la garantía de "ningún test preexistente se rompe" de CA-31/CA-20. |

### Reservadas

| Id | Decisión | Por qué se reserva | Impacto de no decidirla |
|---|---|---|---|
| RS-1 | Cómo implementar el cierre del cliente `scripts/mcp-pce.py` | La unit 0002 entrega `mcp-pce.sh` como carga pero NO entrega `mcp-pce.py` (es provisto por la PCE). Si una implementación futura de 0002 agrega un `mcp-pce.py` mínimo (para que `mcp-pce.sh` no falle al primer run con `python3 mcp-pce.py` no encontrado), el alcance se amplía. | Sin esta implementación, el destino necesita que la PCE provea `mcp-pce.py` antes de usar el loader. El scope delta del 2026-09-29 NO la agregó; queda como RS-1. |
| RS-2 | Orden de implementación entre T7 (`.spec/README.md` contrato nuevo) y T5/T6 (cli.py docstring) | Las dos actualizan prosa sobre el contrato `--force` / exit code 3. Coordinar el orden de aplicación en disco para que el diff sea coherente. | Si T7 se aplica antes que T6, el README miente brevemente; si T6 antes que T7, el docstring miente. Decisión menor, no bloquea. |

## Condiciones de parada

Referencia — **no transcripción** (`pri-gob-fuente-verdad-unica`,
`pol-ia-no-embeber-conocimiento`): `.spec/PARADAS-SUPERVISADO.md`. El
validador falla con `paradas-sin-referencia` si esta sección no contiene
esa referencia.

Ver también: `.spec/PARADAS-SUPERVISADO.md`.

## Paralelismo

- carriles: installer (la unit 0002 produce cambios en `installer/` —
  no toca `sdd/` directamente).
- tope-worktrees: 1 (un solo stack de orquestación; no hay paralelismo
  real porque el plan ampara una sola unidad).
- dueno-stack-vivo: orquestador inline (la sesión que conduce).
- presupuesto-mcp: 0 (no se invoca `pce-mcp` durante este mandato — la
  superficie de gobernanza del kit es la reformulada por 0002 y validada
  por `check_governance_surface.py` en local; el motor nuevo del gate
  corre sin MCP).
- conducta-presupuesto-agotado: parar — condición "MCP de gobernanza
  ausente o sin presupuesto" de `.spec/PARADAS-SUPERVISADO.md` (D-10);
  en ningún caso continuar un gate con gobernanza de memoria.

El validador falla con `paralelismo-sin-declarar` si falta cualquiera
de estos campos.

## Registro de decisiones

### 0002-D1
- tipo: autonoma
- unidad: 0002-versionado-y-actualizacion-del-instalador
- que-se-decidio: el modo de la unit pasa a `desatendido` para el
  cierre (implement + code gate), manteniendo el resto del flujo
  aprobado por los gates previos (spec, plan, tasks).
- alternativas: (a) correr implement en `interactivo` y aceptar los
  checkpoints del modo — rechazado porque el operador aprobó
  explícitamente "Procede de forma desatendida"; (b) correr en
  `supervisado` con `mandato.md` unit-level — innecesario porque el
  plan maestro es trivial (una sola unit) y `desatendido` es el
  equivalente exacto.
- criterio: PD-1
- reversion: cambiar `modo_conversion` a `desde: desatendido / hacia:
  interactivo` y volver al modo interactivo. Requiere nueva aprobación
  del operador.
- revision: aceptada
- revision-fecha: 2026-09-29T08:40:00Z
- revision-quien: Julian

### 0002-D2
- tipo: autonoma
- unidad: 0002-versionado-y-actualizacion-del-instalador
- que-se-decidio: el orden de implementación de los 4 grupos sigue
  G1 → (G4 en paralelo con resto de G1) → G2 → G3. La razón:
  G1 sienta la base (manifest con configuration_contract, mcp-pce.sh);
  G4 crea `config_cableado.py` que G2 importa perezosamente; G3
  implementa check_install_drift.py que solo necesita G1.
- alternativas: (a) orden lineal 1, 2, 3, 4 — rechazado porque G4 y G2
  podrían correr en paralelo una vez G1 esté listo; (b) G2 primero
  que G4 — rechazado porque G2 importa `installer.config_cableado` que
  no existe hasta G4.
- criterio: CR-1
- reversion: reordenar tareas; el archivo tasks.md debe reordenarse
  para que la numeración refleje el orden real de aplicación.
- revision: aceptada
- revision-fecha: 2026-09-29T08:40:00Z
- revision-quien: Julian

### 0002-D3
- tipo: heredada
- unidad: 0002-versionado-y-actualizacion-del-instalador
- que-se-decidio: el cierre de 0002 se ejecuta con el motor de gates
  endurecido por unit 0003 (check_governance_surface.py, segunda red
  por contenido, comparador de hash, fallback de subagent_type, L4
  en plan/tasks, cross-check del orquestador). Esto **no** requiere
  re-trabajo del spec, plan o tasks de 0002 — la reforma del motor
  hace el cierre de 0002 confiable sin tocar su contenido.
- alternativas: ninguna (decisión heredada del plan maestro de 0003; alternativas ya evaluadas allí).
- criterio: heredada:0003-fiabilidad-de-gates-de-governanza (heredada
  de la decisión D2 del plan maestro de 0003).
- reversion: ninguna (la reforma del motor ya está aplicada; revertir
  implicaría cerrar 0003 sin usar sus fixes, lo cual es la conducta
  pre-2026-09-29 que llevó al incidente).
- revision: aceptada
- revision-fecha: 2026-09-29T08:40:00Z
- revision-quien: Julian

## Paradas

(Sin paradas abiertas. Este plan puede entrar en `## Paradas` si un
gate escala; el orquestador registra la parada con su formato canónico.)

## Punto de retoma

(Sin punto de retoma: el plan aún no ha corrido. La primera entrada se
añade cuando el primer gate escala o cuando termina la ventana.)

## Estado

- estado: aprobado

## Instancia en curso

(vacío — la orquestación aún no ha arrancado)

## Aprobación

### 2026-09-29T08:40:00Z
- quien: Julian
- hash: a4ebc115484721935d34d21ff8f18fadc7d4b0857f207179da61fb558263858f

(Hash calculado con `python3 .spec/scripts/validate_mandate.py --hash
--plan 0002-versionado-y-actualizacion-del-instalador` después de
redactar el cuerpo del plan. Toda edición posterior al plan invalida
este hash — el validador reportará `aprobacion-desactualizada` hasta
que se actualice.)

## Revisión posterior

(Sin revisiones posteriores aún.)

## Ancla de aditividad

(vacío — se completa con `mandate_anchor.py write` al primer lanzamiento.)
