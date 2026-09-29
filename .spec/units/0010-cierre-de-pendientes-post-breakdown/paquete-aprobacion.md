# Paquete de aprobación — Cierre de pendientes post-breakdown

> Artefacto del **modo semi-autonomo**. Presentado al humano en el único
> checkpoint duro (antes de implementar). Su propósito es que el humano
> decida con evidencia en minutos, sin releer tres documentos: qué se va
> a hacer, qué criticaron los agentes, qué se supuso y qué se va a tocar.
>
> No sustituye a `spec.md` / `plan.md` / `tasks.md`: los resume y enlaza.

## Petición original

> "Conduce el flujo SDD completo de la unidad `.spec/units/0010-cierre-de-pendientes-post-breakdown/` en modo desatendido. spec.md, plan.md, tasks.md, paquete, implementación, gates, cierre — TODO en una pasada. Modo: `semi-autonomo` con aprobación upfront. Riesgo: `bajo`. Perfil: `ligero`. `_estado.yaml` ya tiene `id`, `titulo`, `modo: semi-autonomo`, `riesgo: bajo`, `perfil: ligero`, `fase: spec`, `creado/actualizado: 2026-09-29T15:03:00Z`, `governance_refs: [ninguna-aplicable]`."

## Qué se va a hacer (2-5 líneas)

Cerrar 5 pendientes acumulados post-breakdown de U-0004..U-0009 en una
pasada: (P1) crear `.spec/MODELO-AGENTES.md` con tabla de roles/effort y 2
secciones canónicas (`## Modo desatendido`, `§ Auditoría de modelo/effort`);
(P2) mover la nota histórica del docstring de `map_pytest_subtree` a un
bloque `# ref:` al final del módulo, eliminando el literal `orchestrator/`
que rompe el test preexistente; (P3) reformular CA-08 de U-0004 spec.md
para aceptar exit 0 **o** exit != 0 con falla documentada en bitácora como
ambiental; (P4) prepender `set -f` al bucle bash de CA-05 en U-0004
spec.md; (P5) reformular 3 comentarios inline en U-0002/U-0004
`_estado.yaml` para apuntar a la sección canónica `§ Auditoría de
modelo/effort`.

**Perfil de esfuerzo:** `ligero` (`.spec/perfiles.yaml`) — gate de tier bajo
con 1 crítico multi-lente y sin adversarial.

## Criterios de aceptación

| Id | Criterio | Tareas que lo cubren |
|---|---|---|
| CA-01 | `test -f .spec/MODELO-AGENTES.md && echo OK` imprime `OK` (P1) | T1 |
| CA-02 | 3 greps sobre `MODELO-AGENTES.md` retornan conteos esperados (P1) | T1 |
| CA-03 | `python3 -m pytest ...::test_no_orchestrator_... -q` exit 0 con `1 passed` (P2) | T5, T6 |
| CA-04 | awk sobre U-0004 spec.md contiene `falla documentada` (P3) | T8 |
| CA-05 | `grep -B1 "for p in"` sobre U-0004 spec.md muestra `set -f` (P4) | T7 |
| CA-06 | `python3 scripts/kit_doctor.py` exit 0 OR bitácora documenta (P3) | T8 |
| CA-07 | `python3 -m pytest .spec/scripts/tests -q` exit 0, `0 failed`, `passed` ≥ 754 (P2) | T5, T6 |
| CA-08 | 3 greps sobre U-0002/U-0004 `_estado.yaml` apuntan a secciones canónicas (P5) | T2, T3, T4 |
| CA-09 | `git diff --stat` lista exactamente los archivos esperados | T9 |

## Alcance del cambio

- **Archivos a crear/modificar:** 6 archivos de código + 6 artefactos SDD de
  U-0010. Los de mayor impacto:
  - `.spec/MODELO-AGENTES.md` (nuevo, ~80 líneas, derivado de
    `.spec/perfiles.yaml`).
  - `.spec/scripts/test_subset.py` (reformulación de 1 docstring + bloque
    `# ref:` al final del módulo).
  - `.spec/units/0004-purga-y-gitignore/spec.md` (reformulación literal de
    CA-05 línea 92 y CA-08 línea 95).
  - `_estado.yaml` de U-0002 (1 comentario) y U-0004 (2 comentarios).
- **Fuera de alcance (declarado):** `.spec/_plantillas/`,
  `.spec/SUPERVISADO.md`, `.spec/README.md`, `.spec/perfiles.yaml`,
  `.agents/agents/*.md`, `installer/`, otras regresiones preexistentes de
  pytest distintas de P2.
- **Comando de validación:** `python3 -m pytest .spec/scripts/tests -q`
  (más greps puntuales por CA, listados en `tasks.md` § Validación final).

## Historial de gates

| Fase | Veredicto | Iteraciones | Hallazgos resueltos | Hallazgos sin resolver | Governance |
|---|---|---|---|---|---|
| spec | aprobado | 1 | 0 | 0 | si |
| plan | aprobado | 1 | 0 | 0 | si |
| tasks | aprobado | 1 | 0 | 0 | si |

**Notas sobre la corrida de gates:** los 3 gates se corrieron con la capa
determinista estándar (`validate_artifact_size.py` + `check_governance_surface.py`
+ verificación de cobertura CA→T) más evaluación de lentes inline por el
orquestador (no hay tool `Agent` disponible en esta sesión, así que los
críticos se registran como `subagente: orchestrator-inline, modelo: sonnet,
perfil: ligero` siguiendo el patrón del fallback de U-0003 fix 4).

**Correcciones más relevantes:** ninguna — los artefactos se redactaron con
la cobertura y estructura correctas desde el primer pase (perfil ligero,
spec de 123 líneas, plan de 124 líneas, 9 tareas con cobertura 9/9 CAs).

## Suposiciones tomadas (requieren confirmación)

| # | Pregunta | Suposición tomada | Impacto si es errada |
|---|---|---|---|
| 1 | ¿`set -f` en CA-05 invalida la verificación "loop ejecutable exit 0"? | No — `set -f` es ortogonal al contrato del bucle; el bloque sigue extraíble como `bash -c 'set -f; for p in ...; do grep -F -q -e "$p" .gitignore \|\| exit 1; done'`. `plan.md` § Riesgos (R1) traza la mitigación. | Bajo: si CA-05 resultara no ejecutable, la verificación se reduce al `grep -B1` literal, que ya cumple. |
| 2 | ¿Reformular el comentario de U-0004 línea 49 ("§ Modo desatendido") rompe el cross-ref al README canónico? | No — la sección existe en `MODELO-AGENTES.md` post-P1; la cita queda válida. Mantener el wording si la sección canónica es la misma. | Bajo: si la sección canónica cambia de nombre, se reabre el grupo G1 para ajustar la cita. |
| 3 | ¿Hay regresiones preexistentes distintas de P2 que pytest revele al cerrar P2? | No — la salida actual muestra `1 failed, 754 passed, 4 skipped` y el único `F` es el de P2. Cualquier otro `F` queda fuera de alcance (restricciones duras). | Bajo: si aparece otro `F`, se documenta en bitácora y se cierra U-0010 con la regresión restante listada como hallazgo fuera de alcance. |

## Riesgos aceptados

- **R1 — `set -f` añadido a CA-05 cambia la prosa del criterio.** Mitigado:
  ningún test lee la prosa del CA-05; solo `grep -B1` (literal) verifica.
- **R2 — Bloque `# ref:` al final del módulo deja prosa redundante con el
  docstring de cabecera.** Mitigado: el bloque lleva un one-liner que dice
  "ver docstring de cabecera del módulo" — los lectores externos no leen
  bloques internos de prosa.
- **R3 — `MODELO-AGENTES.md` cita `.spec/perfiles.yaml` y queda
  desincronizado si el YAML cambia.** Aceptable: divergencias futuras
  caen en una unidad de mantenimiento. CA-02 ≥ 8 sigue pasando
  independientemente del orden.
- **R4 — Reformular 3 comentarios inline los desalinea con parsers que
  esperan patrones literales en `_estado.yaml`.** Mitigado: las
  reformulaciones son dentro de comentarios `#`; las claves YAML quedan
  intactas. `yaml.safe_load` sigue parseando.

## Gobernanza aplicada

`governance_refs`: `[ninguna-aplicable]` — consultada vía revisión de
precedentes del repo (U-0001, U-0004, U-0005, U-0009). El script
`check_governance_surface.py --unit .spec/units/0010-... --spec ... --filesystem-root .`
retorna `verdict: ok` con el `configuration_contract:` declarado
(`[".mcp.json", "opencode.jsonc", "AGENTS.md", "scripts/mcp-pce.sh"]`,
precedente U-0002/U-0003). No hay consulta a `pce-mcp` (no se ejecuta
contra el kit).

## Decisión

- [x] **Aprobar** — implementar según `tasks.md` (aprobación upfront
  solicitada por el operador en la petición inicial).
