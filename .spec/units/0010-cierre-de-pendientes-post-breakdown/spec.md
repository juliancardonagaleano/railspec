# Spec — Cierre de pendientes post-breakdown

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describas el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

Cinco pendientes se acumularon en `_estado.yaml > gates.codigo.hallazgos` de las
unidades U-0004..U-0009, cerrados por sus gates pero nunca remediados:

- **P1** — `.spec/MODELO-AGENTES.md` citado desde 8 lugares
  (`_estado.yaml:46,82,105` de U-0002/U-0004/U-0005, `pre-commit-gate.sh:46,58`,
  `effort_profile.py:5`, `.spec/SUPERVISADO.md:276,281`,
  `.spec/README.md:49,202`) pero **no existe** en el filesystem. Cada unidad
  que lo tocó lo dejó "fuera de alcance por restricción de no crear archivos".
- **P2** — `test_test_subset.py::test_no_orchestrator_...` falla
  preexistentemente: `inspect.getsource(test_subset)` contiene el literal
  `orchestrator/` en el docstring de `map_pytest_subtree:136`. 754 tests
  pasan, 1 falla documentada.
- **P3** — `0004-purga-y-gitignore/spec.md:95` (CA-08) exige
  `python3 scripts/kit_doctor.py` exit 0; la misma unidad declara
  `spec.md:97-114` que `pce-mcp` no se ejecuta contra el kit. Tensión heredada
  que U-0004 cerró como "cumplido-en-ambiente" sin resolver.
- **P4** — Bucle bash de `0004-purga-y-gitignore/spec.md:92` (CA-05) sin
  `set -f`. En zsh, los 16 patrones contienen `*` (`.pytest_*/`,
  `.spec/scripts/**/__pycache__/`) y el shell los expande antes de `grep`.
  U-0004 ejecutó OK con `set -f` ad-hoc pero el spec no lo exige.
- **P5** — 3 comentarios inline en `_estado.yaml` de U-0002 (≈115) y U-0004
  (49 y 105) citan `MODELO-AGENTES.md` con wording histórico. Tras P1 las
  citas quedan válidas; conviene apuntarlas a la sección canónica
  `§ Auditoría de modelo/effort`.

**Si no se hace:** las 8 referencias siguen colgando, `kit_doctor` queda
documentalmente en rojo, el bucle de CA-05 rompe en cualquier clon nuevo con
zsh, y la suite pytest mantiene 1 falla heredada.

## Resultado esperado

- `.spec/MODELO-AGENTES.md` existe con tabla de roles/effort derivada de
  `.spec/perfiles.yaml` y secciones `## Modo desatendido` y `## Auditoría de
  modelo/effort` que las 8 referencias ya hechas resuelven sin colgarse.
- `python3 -m pytest .spec/scripts/tests -q` exit 0 con `0 failed` (P2 cerrado).
- U-0004 `spec.md CA-08` reformulado: exit 0 **o** exit != 0 con falla
  documentada en bitácora de la unidad como ambiental
  (pce-mcp/graph-index no responden en este clon).
- U-0004 `spec.md CA-05` reformulado: bucle arranca con `set -f` antes del
  `for` y nota sobre globbing de zsh aparece en la prosa del CA.
- 3 comentarios inline en U-0002/U-0004 `_estado.yaml` reformulados para
  apuntar a la sección canónica `§ Auditoría de modelo/effort` o mantenerse
  como referencia válida si ya la nombran.

## Alcance

**Incluye:** crear `.spec/MODELO-AGENTES.md`; mover la nota histórica del
docstring de `map_pytest_subtree` a un bloque `# ref:` al final de
`test_subset.py`; editar `.spec/units/0004-purga-y-gitignore/spec.md`
(líneas ≈92 y ≈95); editar 3 comentarios inline en `_estado.yaml` de
U-0002 (1) y U-0004 (2).

**No incluye (fuera de alcance):** tocar `.spec/_plantillas/`,
`.spec/SUPERVISADO.md`, `.spec/README.md`, `.spec/perfiles.yaml` (las citas a
`MODELO-AGENTES.md` en esos archivos son referencias válidas tras P1;
redirigirlas sería scope creep); tocar `.agents/agents/*.md` (prohibido);
revertir U-0004/spec.md a su forma pre-U-0004 (el spec está aprobado por el
gate de U-0004; solo se reformulan CA-05 y CA-08); tocar regresiones
preexistentes distintas de `test_no_orchestrator_...`; reconectar `pce-mcp`
(la tensión CA-08 vs § Governance se cierra vía reformulación del CA).

## Criterios de aceptación

Cada `CA-NN` se verifica con un comando o `ruta:línea` reproducible; el plan y
`tasks.md` los referencian por id; el gate de tareas falla si algún criterio
queda sin tarea que lo cubra.

- [ ] **CA-01** — `test -f .spec/MODELO-AGENTES.md && echo OK` imprime `OK` (P1).
- [ ] **CA-02** — `grep -c "modelo:" .spec/MODELO-AGENTES.md` ≥ 8; `grep -c "## Modo desatendido" .spec/MODELO-AGENTES.md` ≥ 1; `grep -c "## Auditoría de modelo/effort" .spec/MODELO-AGENTES.md` ≥ 1 (P1).
- [ ] **CA-03** — `python3 -m pytest .spec/scripts/tests/test_test_subset.py::LoadSubarbolesTests::test_no_orchestrator_ni_studio_app_cableados_en_el_codigo -q` exit 0 con `1 passed` (P2).
- [ ] **CA-04** — `awk '/^- \[ \] CA-08/{flag=1} flag && /^- \[ \] CA-/{if(!/CA-08/)exit} flag' .spec/units/0004-purga-y-gitignore/spec.md` contiene `falla documentada` o `documentada en bitácora` (P3).
- [ ] **CA-05** — `grep -B1 "for p in" .spec/units/0004-purga-y-gitignore/spec.md` muestra `set -f` en la línea inmediatamente anterior al `for` (P4).
- [ ] **CA-06** — `python3 scripts/kit_doctor.py` exit 0 **o** exit != 0 con una línea en `bitacora.md` de U-0010 que documente la falla como ambiental (P3 — reformulación CA-08 vivida en U-0004).
- [ ] **CA-07** — `python3 -m pytest .spec/scripts/tests -q` exit 0, `0 failed` y `passed` ≥ 754 (sin regresión P2; CA-03 ⊂ CA-07).
- [ ] **CA-08** — `grep -n "MODELO-AGENTES" .spec/units/0002-.../_estado.yaml .spec/units/0004-.../_estado.yaml` muestra ≥ 3 ocurrencias apuntando a secciones canónicas (`§ Auditoría de modelo/effort` o `§ Modo desatendido`) (P5).
- [ ] **CA-09** — Los archivos modificados/creados en esta corrida son
  exactamente: `.spec/MODELO-AGENTES.md` (nuevo),
  `.spec/scripts/test_subset.py`,
  `.spec/units/0004-purga-y-gitignore/spec.md`,
  `.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml`,
  `.spec/units/0004-purga-y-gitignore/_estado.yaml`, más artefactos SDD de
  U-0010 (`spec.md`, `plan.md`, `tasks.md`, `paquete-aprobacion.md`,
  `bitacora.md`, `_estado.yaml`). Verificable con
  `git log --diff-filter=AM --name-only --since="<ISO-8601 inicio>" | sort -u`
  o equivalente. La corrida no toca `installer/`, `scripts/kit_*.py`,
  `scripts/materialize_*.py`, `.claude/`, `.agents/` (los cambios
  pre-existentes en esos directorios en el worktree son de sesiones
  previas y quedan fuera del scope de U-0010 — ver
  `bitacora.md` § Notas de implementación).

configuration_contract: [".mcp.json", "opencode.jsonc", "AGENTS.md", "scripts/mcp-pce.sh"]

## Governance aplicable

`pce-mcp` **no se ejecuta** contra el repo del kit — el kit es productor del
contrato de gobernanza que un destino recibe (`AGENTS.md`, `.mcp.json`,
`opencode.jsonc`, `scripts/mcp-pce.sh`), no su consumidor. La superficie que
el kit produce vive en cada destino, no en este repo. Por tanto **no hay
artefacto de gobernanza recuperable contra el cual evaluar este spec**;
condición distinta de "un MCP caído", que el centinela
`governance_refs: [ninguna-aplicable]` registra explícitamente. Precedente
en `0001-…/spec.md`, `0004-…/spec.md:97-114`, `0005-…/spec.md § Governance`,
`0009-…/spec.md § Governance`. Esta unidad además **no toma decisiones de
fuente única ni invierte la precedencia de superficies** — solo crea un
documento referenciado por 8 lugares preexistentes y reformula 2 CAs y 3
comentarios inline heredados. El bloque `configuration_contract:` arriba
enumera los 4 archivos que el kit produce como carga (precedente
U-0002/U-0003); ningún archivo raíz declara MCP/gob fuera de esa lista.

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Sin superficie de gobernanza propia del kit; ver justificación arriba |

## Preguntas abiertas

> En **modo semi-autonomo** las preguntas abiertas no bloquean: se responden
> con la opción más conservadora y se listan en el paquete de aprobación.

- **Q1 — ¿`set -f` en CA-05 invalida la verificación "loop ejecutable exit 0"?**
  *Suposición:* No — `set -f` es ortogonal al contrato del bucle; el bloque
  sigue extraíble como `bash -c 'set -f; for p in ...; do grep -F -q -e "$p"
  .gitignore || exit 1; done'`. `plan.md` § Riesgos (R1) traza la
  mitigación.
- **Q2 — ¿Reformular el comentario de U-0004 línea 49 ("§ Modo desatendido")
  rompe el cross-ref al README canónico?** *Suposición:* No — la sección
  existe en `MODELO-AGENTES.md` post-P1; la cita queda válida.
- **Q3 — ¿Hay regresiones preexistentes distintas de P2 que pytest revele?**
  *Suposición:* No — la salida actual muestra `1 failed, 754 passed, 4
  skipped` y el único `F` es el de P2. Cualquier otro `F` queda fuera de
  alcance (restricciones duras).
