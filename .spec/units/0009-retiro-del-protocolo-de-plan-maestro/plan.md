# Plan técnico — Retiro del protocolo de plan maestro, consolidación en `mandato.md` y migración de U-0002/U-0003

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de un `spec.md` aprobado (16 CA verificables, gate `spec: refinado`, tier alto, 1 hallazgo alta resuelto — typo `governanza` — y 1 media resuelto — CA-12 partido en a/b).

## Enfoque

Borrar las 3 plantillas redundantes (`plan-maestro.md`, `historial.md`, `paquete-tanda-aprobacion.md`), consolidar `mandato.md` como única plantilla de mandato, declarar el nuevo campo `depende_de:` en `_estado.yaml` (block-list de IDs de unidad), migrar las 2 unidades activas que usaban `plan-maestro.md` (U-0002 y U-0003 — ambas `fase: done`) reasignando su `dependencias:` informal a `depende_de:` y vaciando `mandato:`, archivar sus planes bajo `.spec/planes-archive/`, y limpiar las ~50 referencias residuales a `plan-maestro` en scripts, tests, skills SDD y docs canónicos (sin tocar el instalador ni el materializador de `.claude/`). La operación es **mayormente de borrado y reemplazo de literales**, con un único punto de decisión no-trivial en `validate_mandate.py` (R-1, contradicción CA-07 ↔ `unidad-mandato-vacio` resuelta en DD-3).

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `.spec/_plantillas/plan-maestro.md` | eliminar | CA-01 |
| `.spec/_plantillas/historial.md` | eliminar | CA-02 |
| `.spec/_plantillas/paquete-tanda-aprobacion.md` | eliminar | CA-03 |
| `.spec/_plantillas/mandato.md` | modificar | quitar cabecera que menciona `.spec/_plantillas/plan-maestro.md` (líneas 5-7); reformular línea 12 sustituyendo "esas tres son propias de una agrupación de varias unidades" por anclaje a `.spec/SUPERVISADO.md` § 1.1; CA-04 |
| `.spec/_plantillas/_estado.yaml` | modificar | añadir `depende_de: []` (block-list vacío, línea ~37); reescribir comentario de `mandato:` (línea 38-39) para que solo refiera `mandato.md`; CA-05, CA-06 |
| `.spec/scripts/validate_mandate.py` | modificar | eliminar 3 ramas `legacy compat` (líneas 368, 409, 415); reformular docstring (líneas 15, 17); añadir rama en `resolve_unit` (línea 361): si `mandato: ""` Y `modo: desatendido`, retornar `Mandate(...)` válida en lugar de `unidad-mandato-vacio`; DD-3, R-1 |
| `.spec/scripts/_common.py` | modificar | eliminar línea 3 "Pattern origin: `.spec/units/0068-plan-maestro-de-oleadas/...`" — referencia histórica, no funcional; CA-13 |
| `.spec/scripts/instance_lock.py` | modificar | reemplazar menciones a `plan-maestro.md` por `plan.md` (líneas 16, 65) — solo docs, sin cambio funcional; CA-13 |
| `.spec/scripts/preflight.py` | modificar | eliminar rama `if mandate.name == "plan-maestro.md"` (líneas 765-766); reformular comentario línea 761 ("`# so keying off plan-maestro.md alone...`") — dead post-retirement; CA-13 |
| `.spec/scripts/sdd_safe_write.py` | modificar | sacar `plan-maestro.md` de `WATCHED_BASENAMES` (línea 147) y de `validator_command` (línea 256); reformular docstring (líneas 28, 410) y comentario de CA-13 (línea 8); CA-13 |
| `.spec/scripts/guard_written_state_shape.py` | modificar | mismo cambio en `WATCHED_BASENAMES` (línea 91) y `validator_command` (línea 191); reformular docstring línea 189 ("`already guarantees the file sits at .spec/planes/<id>/plan-maestro.md`"); reformular líneas 29 y 312 de tests; CA-13 |
| `.spec/scripts/mandate_anchor.py` | modificar | reformular líneas 5-10 (docstring referencia a `_plan-maestro.md`) — sustituir por anclaje a `validate-supervised.sh` Paso 9 actualizado; CA-13 |
| `.spec/scripts/record_mandate_validation.py` | modificar | eliminar fallback `legacy plan-maestro.md` (líneas 65-66, 69, 71); CA-13 |
| `.spec/scripts/supervised_parallel.py` | modificar | reformular línea 4 ("Reopened by explicit human decision `0118-D3` (`plan-maestro.md`...)") y línea 421 ("`plan-maestro.md § Paralelismo`"); CA-13 |
| `.spec/scripts/append_changelog_line.py` | eliminar archivo | sin callers vivos: `append_changelog_line.py` es referenciado solo por tests (`test_append_changelog_line.py:35`, `test_guard_bash_spec_writes.py:64`, `test_snapshot_evidence.py:5`) y por docstrings de 2 skills; al eliminarse el archivo, los tests se borran también (ver tabla abajo); DD-3, CA-13 |
| `.spec/scripts/sdd_retomar.py` | modificar | reformular línea 20 (`<dir-path>` docstring: "literal directory (plan maestro, fixture)") → "literal directory (fixture)"; CA-13 |
| `.spec/scripts/supervised-test.sh` | eliminar archivo | el shell script referencia `_plan-maestro.md` (línea 40) y `plan-maestro.diff` (líneas 96, 149); ningún `comando_validacion` vivo lo invoca (verificado); CA-13 |
| `.spec/scripts/validate-supervised.sh` | modificar | reformular línea 24-25 (Paso 9 mantiene su semántica sobre `_plan-maestro.md` si el archivo existe, pero el script deja de fallar si no — el archivo es histórico, no normativo); reformular Paso 9 para usar `.spec/units/_mandatos-supervisado.md` que es el índice vigente (ver `.spec/SUPERVISADO.md:158-164`); CA-13 |
| `.spec/scripts/validate_artifact_size.py` | modificar | eliminar subparser `plan-maestro` (líneas 26, 366-372) y función `check_plan_maestro` (líneas 135-170); eliminar constante `PLAN_MAESTRO_BUDGET` (línea 90); CA-13 |
| `.spec/scripts/tests/test_append_changelog_line.py` | eliminar archivo | implícito al borrar `append_changelog_line.py` |
| `.spec/scripts/tests/test_guard_bash_spec_writes.py` | modificar | eliminar tests que referencian `_plan-maestro.md` (líneas 64, 249-255) |
| `.spec/scripts/tests/test_preflight.py` | modificar | migrar fixtures `plan-maestro.md` → `plan.md` (líneas 494, 534, 544) y actualizar docstring línea 621 |
| `.spec/scripts/tests/test_record_mandate_validation.py` | modificar | reformular líneas 169 (comentario legacy) — sin fixture a migrar |
| `.spec/scripts/tests/test_sdd_safe_write.py` | modificar | migrar fixtures `plan-maestro.md` → `plan.md` (líneas 276, 298, 318, 737) |
| `.spec/scripts/tests/test_supervised_parallel.py` | modificar | migrar fixtures `plan-maestro.md` → `plan.md` (líneas 136, 309, 581, 683, 723, 990, 1066); reformular docstring línea 565 (`build_fixture_repo`: "real `.spec/planes/<plan_id>/plan-maestro.md`") |
| `.spec/scripts/tests/test_mandate_anchor.py` | modificar | reformular docstrings líneas 499 y 522 (referencias `_plan-maestro.md`) |
| `.spec/scripts/tests/test_supervised_conductor.py` | modificar | migrar fixture línea 406 `plan-maestro.md` → `plan.md` |
| `.spec/scripts/tests/test_guard_written_state_shape.py` | modificar | migrar fixtures (líneas 219, 312, 345, 538, 545) y actualizar docstring |
| `.spec/scripts/tests/test_snapshot_evidence.py` | modificar | reformular línea 5 (referencia `_plan-maestro.md`) y línea 71 (`plan-maestro.diff`) — sin fixture |
| `.spec/scripts/tests/test_supervised_test_patron_permiso.sh` | modificar | reformular línea 6 (referencia `plan-ejemplo/plan-maestro.md`) |
| `.spec/scripts/tests/test_gate_tier_budget_unchanged.py` | modificar | reformular línea 18 (referencia `plan-ejemplo/plan-maestro.md`) |
| `.spec/scripts/tests/test_validate_artifact_size.py` | modificar | eliminar tests del subparser `plan-maestro` |
| `.spec/scripts/tests/test_u0005_ca12.py` | modificar | reducir `EXCLUDED_PATHS` a solo `[".spec/_plantillas/mandato.md"]` (líneas 20-25 y 53-58); CA-12a |
| `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/spec.md` | modificar | reformular CA-12 (línea 69) — la lista de "4 plantillas que esta unidad no toca" pasa a ser 1 (`mandato.md`); reformular nota línea 73; CA-12b |
| `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/tasks.md` | modificar | reformular T8 (línea 16), T19 (línea 27) y validación final línea 37 — la lista de 4 plantillas baja a 1; CA-12b |
| `.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml` | modificar | vaciar `mandato:` a `""` (línea 297); eliminar bloque `dependencias:` (líneas 30-46 con comentario forward-compatible); añadir `depende_de:\n  - 0003-fiabilidad-de-gates-de-gobernanza` (DD-4, CA-08) |
| `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml` | modificar | vaciar `mandato:` a `""` (línea 58); eliminar bloque `dependencias:` (líneas 62-74); añadir `depende_de:\n  - 0002-versionado-y-actualizacion-del-instalador` (DD-4, CA-10) |
| `.spec/planes/0002-versionado-y-actualizacion-del-instalador/` | archivar | mover `plan.md` a `.spec/planes-archive/0002-versionado-y-actualizacion-del-instalador/plan.md`; eliminar directorio `.spec/planes/0002-*/`; CA-11, DD-2 |
| `.spec/planes/0003-fiabilidad-de-gates-de-gobernanza/` | archivar | análogo al anterior; CA-11, DD-2 |
| `.agents/skills/sdd-desatendido/SKILL.md` | modificar | reformular líneas 3 (description/keywords), 4 (compatibility), 12, 16, 22, 27, 44, 48, 49 (todas las menciones a "plan maestro"/"plan-maestro") — sustituir por "mandato" donde aplica; CA-14 |
| `.agents/skills/sdd-preflight/SKILL.md` | modificar | reformular línea 37 (`plan-maestro.md` → `plan.md` en la ruta de `--mandate`); CA-14 |
| `.agents/skills/sdd-supervisado/SKILL.md` | modificar | reformular líneas 3 (description "plan maestro"), 34 (tabla "Conducir un plan maestro..."), 141, 293, 460, 470 (referencias a `append_changelog_line.py` retirado); CA-14 |
| `.agents/skills/sdd-orquestar/SKILL.md` | modificar | reformular línea 120 ("plan maestro cuando existe" → "plan cuando existe"); CA-14 (contradecía spec § Resultado esperado línea 23) |
| `.spec/SUPERVISADO.md` | modificar | reformular línea 22 (`Dónde vive: .spec/_plantillas/plan-maestro.md` → "Retirado por U-0009; ver `.spec/SUPERVISADO.md § 1.2`") y línea 113 (eliminar la fila `plan-maestro.md` / `mandato.md` que lista la plantilla); reescribir § 1.1 "Plantilla del plan maestro por objetivo" como 1-2 líneas declarando el retiro; CA-15 |
| `.spec/PARADAS-SUPERVISADO.md` | modificar | reformular línea 4 (`Ambas plantillas...` → "La plantilla `mandato.md` la referencia..."); CA-15 |
| `AGENTS.md` | modificar | reformular línea 149 (`{spec,plan,mandato,plan-maestro,tasks}.md` → `{spec,plan,mandato,tasks}.md`); CA-15 |
| `.spec/scripts/append_changelog_line.py` | eliminar archivo | sin callers vivos: el script es referenciado solo por tests (`test_append_changelog_line.py:35`, `test_guard_bash_spec_writes.py:64`, `test_snapshot_evidence.py:5`) y por docstrings de 2 skills (`sdd-supervisado/SKILL.md:293, 460, 470`); al eliminarse el archivo, los tests se borran también (ver tabla abajo); DD-3, CA-13 |
| `.spec/scripts/tests/test_append_changelog_line.py` | eliminar archivo | implícito al borrar `append_changelog_line.py` |

## Reutilización (no reinventar)

- `.spec/scripts/_common.py:field_list` y `field` (líneas 69-77) — parsers existentes de `mandato:` y campos top-level; se reutilizan para `depende_de:` sin añadir parser.
- `.spec/scripts/sdd_safe_write_canonical.normalize_canonical` — la canonización YAML ya cubre block-lists; `depende_de:` se canonicaliza sin código nuevo.
- Materializadores `.claude/skills/*` — los espejos se regeneran desde `.agents/skills/*` por `scripts/materialize_claude_skills.py`; CA-14 no exige acción en `.claude/skills/` (los espejos son derivados).
- El bloque `dependencias:` actual de U-0002/U-0003 (líneas 42-46 y 68-74 de sus `_estado.yaml`) — fuente única para derivar los IDs de `depende_de:` (DD-4).

## Decisiones de diseño

- **DD-1 — Forma de `depende_de:`** — **block-list YAML** (`depende_de:\n  - 0003-fiabilidad-de-gates-de-gobernanza`). **Razón:** consistente con el resto del `_estado.yaml` (`modo_conversion:`, `validaciones_mandato:`, `gates.<fase>.criticos:` ya son block-lists); el parser existente `_common.top_level_block`/`block_list_of_dicts` (líneas 125-200) maneja block-lists pero NO flow-style inline; el inline `["…"]` exigiría un parser nuevo para 1 solo callsite.
- **DD-2 — Archivado de planes** — **(a) `.spec/planes-archive/`**. **Razón:** U-0002 y U-0003 están `fase: done`; el contenido de sus `plan.md` es histórico y se cita en `bitacora.md` (cronología inmutable). Migrar a `mandato.md` dentro de cada unidad (opción b) duplica contenido sin valor; descartar (c) pierde trazabilidad del cierre. La conservadora `(a)` preserva evidencia sin tocar las unidades cerradas.
- **DD-3 — Legacy compat del validador** — **eliminar la rama `plan-maestro.md` y aceptar `mandato: ""` en `modo: desatendido`**. **Razón:** ningún mandato vivo usa `plan-maestro.md` como nombre (verificado: `plan.md` es el canónico desde U-0002/0003); la rama `legacy compat` es código muerto. Sobre `mandato: ""`: las unidades `modo: desatendido` post-retiro quedan sin mandato por diseño (PQ-3 deja la redefinición de modos a una unidad posterior), por lo que `validate_mandate.py:362` debe aceptar este caso como válido cuando `modo == "desatendido"` (la rama `unidad-mandato-vacio` se restringe a `modo: supervisado`).
- **DD-4 — IDs concretos de `depende_de:`** — **U-0002 → `0003-fiabilidad-de-gates-de-gobernanza`; U-0003 → `0002-versionado-y-actualizacion-del-instalador`**. **Razón:** derivación directa del bloque `dependencias:` actual (U-0002:42-46 cita U-0003; U-0003:68-74 cita U-0002); preserva la prosa completa de cada bloque en `bitacora.md` antes del borrado para no perder el contexto narrativo.

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | Plantillas + estado canónico | `.spec/_plantillas/{plan-maestro,historial,paquete-tanda-aprobacion}.md` (borrar), `.spec/_plantillas/mandato.md`, `.spec/_plantillas/_estado.yaml` | — | estandar |
| G2 | Scripts del andamiaje | `.spec/scripts/{validate_mandate,_common,instance_lock,preflight,sdd_safe_write,guard_written_state_shape,mandate_anchor,record_mandate_validation,supervised_parallel,append_changelog_line,sdd_retomar,supervised-test,validate-supervised,validate_artifact_size}.{py,sh}` + sus 12 tests en `.spec/scripts/tests/` | — | estandar |
| G3 | Skills SDD + docs canónicos | `.agents/skills/{sdd-desatendido,sdd-preflight,sdd-supervisado}/SKILL.md`, `.spec/SUPERVISADO.md`, `.spec/PARADAS-SUPERVISADO.md`, `AGENTS.md` | — | estandar |
| G4 | Migración U-0002/U-0003 + archivado de planes | `.spec/units/0002-*/_estado.yaml`, `.spec/units/0003-*/_estado.yaml`, `.spec/planes/0002-*/`, `.spec/planes/0003-*/` | G1 (el campo `depende_de:` debe existir en la plantilla antes de poblar las unidades) | estandar |
| G5 | Acoplamiento con U-0005 | `.spec/units/0005-*/spec.md`, `.spec/units/0005-*/tasks.md`, `.spec/scripts/tests/test_u0005_ca12.py` | — | estandar |

> G2..G5 admiten paralelismo real entre sí (archivos disjuntos); G1 condiciona G4 pero no G2/G3/G5.

## Riesgos y mitigaciones

- **R-1 (crítico) — `validate_mandate.py:362` rechaza `mandato: ""` con `unidad-mandato-vacio`**, pero CA-07/CA-09 requieren exactamente `mandato: ""`. **Mitigación:** DD-3 — modificar `resolve_unit` para tratar `mandato:` vacío como válido cuando `modo: desatendido` (gating por modo, no por valor). Verificación: `python3 -m pytest .spec/scripts/tests installer -q` cubre el caso tras el cambio; el test unitario de la rama está implícito en CA-16.
- **R-2 — Tests preexistentes rompen al retirar ramas legacy** (`test_preflight.py`, `test_sdd_safe_write.py`, `test_supervised_parallel.py`, `test_guard_written_state_shape.py` con fixtures `plan-maestro.md`). **Mitigación:** cada test que crea el archivo `plan-maestro.md` se migra a `plan.md`; los tests que comparan strings literal se reformulan a la nueva constante. Verificación: la suite completa (CA-16) debe pasar antes de cerrar.
- **R-3 — `sdd-orquestar` puede tener lógica condicional sobre `plan-maestro` que grep no detecta** (strings parametrizados, ramas por basename). **Mitigación:** inspección manual de `.agents/skills/sdd-orquestar/SKILL.md` (grep inicial retornó vacío, pero se relee la skill completa en fase implement); si se encuentra, se trata como issue de G3.
- **R-4 — Materializadores `.claude/` regeneran espejos desde `.agents/`**; cualquier mención residual en `.claude/skills/sdd-supervisado/SKILL.md:141` (espejo de G3) aparecería de nuevo tras un re-materialize. **Mitigación:** CA-14 se verifica sobre `.agents/skills/sdd-supervisado/SKILL.md` (fuente canónica); los espejos `.claude/` se regeneran al final de la implementación por `scripts/materialize_claude_skills.py` y se validan con `grep -c 'plan-maestro' .claude/skills/sdd-supervisado/SKILL.md` → 0 como check extra pre-cerrar.

## Comando de validación

```
python3 -m pytest .spec/scripts/tests installer -q -k u0009 ; \
python3 -m pytest .spec/scripts/tests installer -q ; \
grep -rl 'plan-maestro' .spec/scripts/ .agents/skills/ .spec/_plantillas/ .spec/SUPERVISADO.md .spec/PARADAS-SUPERVISADO.md AGENTS.md --exclude-dir=__pycache__ | wc -l ; \
test ! -f .spec/_plantillas/plan-maestro.md && test ! -f .spec/_plantillas/historial.md && test ! -f .spec/_plantillas/paquete-tanda-aprobacion.md && test ! -f .spec/planes/0002-versionado-y-actualizacion-del-instalador/plan.md && test ! -f .spec/planes/0003-fiabilidad-de-gates-de-gobernanza/plan.md
```

Resultado esperado: `pytest -k u0009` → `2 passed` (placeholder hasta que `sdd-tareas` cree los tests específicos); `pytest` global → `0 failed`; `wc -l` → `0`; `test ...` → exit 0. El `git status --short .spec/_plantillas/` debe mostrar ≤ 1 línea (solo `mandato.md` consolidado).
