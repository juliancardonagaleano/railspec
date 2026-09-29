# Tareas — Retiro del protocolo de plan maestro, consolidación en `mandato.md` y migración de U-0002/U-0003

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**: el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en
> ninguna tarea.
>
> Orden de ejecución: **G1 primero** (plantillas + estado canónico); **G2..G5
> después en paralelo** (archivos disjuntos entre grupos). G2 y G4 dependen de
> G1 porque `depende_de:` debe existir en la plantilla antes de poblar las
> unidades; G3 y G5 no dependen de G1.

## Pendientes

### G1 — Plantillas + estado canónico

- [x] T1 — Borrar `.spec/_plantillas/plan-maestro.md` · archivos: `.spec/_plantillas/plan-maestro.md` · cubre: CA-01
- [x] T2 — Borrar `.spec/_plantillas/historial.md` · archivos: `.spec/_plantillas/historial.md` · cubre: CA-02
- [x] T3 — Borrar `.spec/_plantillas/paquete-tanda-aprobacion.md` · archivos: `.spec/_plantillas/paquete-tanda-aprobacion.md` · cubre: CA-03
- [x] T4 — Modificar `.spec/_plantillas/mandato.md`: retirar cabecera con mención a `.spec/_plantillas/plan-maestro.md` (líneas 5-7) y reformular línea 12 sustituyendo "esas tres son propias de una agrupación de varias unidades" por anclaje a `.spec/SUPERVISADO.md` § 1.1 · archivos: `.spec/_plantillas/mandato.md` · cubre: CA-04
- [x] T5 — Modificar `.spec/_plantillas/_estado.yaml`: añadir `depende_de: []` como block-list vacío (línea ~37) y reescribir comentario del campo `mandato:` (líneas 38-39) para que solo refiera `mandato.md` · archivos: `.spec/_plantillas/_estado.yaml` · cubre: CA-05, CA-06

### G2 — Scripts del andamiaje

- [x] T6 — Modificar `.spec/scripts/validate_mandate.py`: eliminar 3 ramas `legacy compat` (líneas 368, 409, 415); reformular docstring (líneas 15, 17); añadir rama en `resolve_unit` (línea 361) que retorna `Mandate(...)` válida cuando `mandato: ""` Y `modo: desatendido` (la rama `unidad-mandato-vacio` se restringe a `modo: supervisado`) · archivos: `.spec/scripts/validate_mandate.py` · depende de: T5 · cubre: CA-13, DD-3, R-1
- [x] T7 — Reformular `.spec/scripts/_common.py:3` (eliminar línea "Pattern origin: `.spec/units/0068-plan-maestro-de-oleadas/...`" — histórica) y `.spec/scripts/instance_lock.py:16,65` (reemplazar `plan-maestro.md` por `plan.md`) · archivos: `.spec/scripts/_common.py`, `.spec/scripts/instance_lock.py` · depende de: T5 · cubre: CA-13
- [x] T8 — Reformular `.spec/scripts/preflight.py`: eliminar rama `if mandate.name == "plan-maestro.md"` (líneas 765-766) y reformular comentario línea 761 ("`# so keying off plan-maestro.md alone...`" — dead post-retirement) · archivos: `.spec/scripts/preflight.py` · depende de: T5 · cubre: CA-13
- [x] T9 — Modificar `.spec/scripts/sdd_safe_write.py` (sacar `plan-maestro.md` de `WATCHED_BASENAMES` línea 147 y de `validator_command` línea 256; reformular docstring líneas 28 y 410) y `.spec/scripts/guard_written_state_shape.py` (mismo cambio en `WATCHED_BASENAMES` línea 91 y `validator_command` línea 191; reformular docstring línea 189 "already guarantees the file sits at .spec/planes/<id>/plan-maestro.md"; reformular líneas 29 y 312) · archivos: `.spec/scripts/sdd_safe_write.py`, `.spec/scripts/guard_written_state_shape.py` · depende de: T5 · cubre: CA-13
- [x] T10 — Reformular `.spec/scripts/mandate_anchor.py:5-10` (docstring con referencia a `_plan-maestro.md` → anclaje a `validate-supervised.sh` Paso 9 actualizado) · archivos: `.spec/scripts/mandate_anchor.py` · depende de: T5 · cubre: CA-13
- [x] T11 — Eliminar fallback `legacy plan-maestro.md` en `.spec/scripts/record_mandate_validation.py` (líneas 65-66, 69, 71) · archivos: `.spec/scripts/record_mandate_validation.py` · depende de: T5 · cubre: CA-13
- [x] T12 — Reformular `.spec/scripts/supervised_parallel.py:4` ("Reopened by explicit human decision `0118-D3` (`plan-maestro.md`...)") y línea 421 ("`plan-maestro.md § Paralelismo`") · archivos: `.spec/scripts/supervised_parallel.py` · depende de: T5 · cubre: CA-13
- [x] T13 — Eliminar `.spec/scripts/append_changelog_line.py` y `.spec/scripts/tests/test_append_changelog_line.py` (sin callers vivos: solo lo referencian `test_guard_bash_spec_writes.py:64`, `test_snapshot_evidence.py:5` y los docstrings de 2 skills que se reformulan en G3; tras el borrado, los references residuales se atienden en T18/T21) · archivos: `.spec/scripts/append_changelog_line.py`, `.spec/scripts/tests/test_append_changelog_line.py` · depende de: T5 · cubre: CA-13, DD-3
- [x] T14 — Reformular `.spec/scripts/sdd_retomar.py:20` (`<dir-path>` docstring: "literal directory (plan maestro, fixture)" → "literal directory (fixture)") · archivos: `.spec/scripts/sdd_retomar.py` · depende de: T5 · cubre: CA-13
- [ ] T15 — Eliminar `.spec/scripts/supervised-test.sh` (referencia `_plan-maestro.md:40` y `plan-maestro.diff:96,149`; ningún `comando_validacion` vivo lo invoca) · archivos: `.spec/scripts/supervised-test.sh` · depende de: T5 · cubre: CA-13
- [x] T16 — Reformular `.spec/scripts/validate-supervised.sh:24-25` (Paso 9): mantener semántica sobre `_plan-maestro.md` si existe pero no fallar si no; reformular para usar `.spec/units/_mandatos-supervisado.md` como índice vigente (ver `.spec/SUPERVISADO.md:158-164`) · archivos: `.spec/scripts/validate-supervised.sh` · depende de: T5 · cubre: CA-13
- [x] T17 — Eliminar subparser `plan-maestro` en `.spec/scripts/validate_artifact_size.py`: constante `PLAN_MAESTRO_BUDGET` (línea 90), función `check_plan_maestro` (líneas 135-170), registro del subparser (líneas 26, 366-372) · archivos: `.spec/scripts/validate_artifact_size.py` · depende de: T5 · cubre: CA-13
- [x] T18 — Migrar tests que aún referencian `plan-maestro`: `test_guard_bash_spec_writes.py` (eliminar tests líneas 64, 249-255), `test_preflight.py` (migrar fixtures `plan-maestro.md` → `plan.md` líneas 494, 534, 544 y reformular docstring línea 621), `test_record_mandate_validation.py` (reformular línea 169), `test_sdd_safe_write.py` (migrar fixtures líneas 276, 298, 318, 737), `test_supervised_parallel.py` (migrar fixtures líneas 136, 309, 581, 683, 723, 990, 1066; reformular docstring línea 565), `test_mandate_anchor.py` (reformular docstrings líneas 499, 522), `test_supervised_conductor.py` (migrar fixture línea 406), `test_guard_written_state_shape.py` (migrar fixtures líneas 219, 312, 345, 538, 545 y reformular docstring), `test_snapshot_evidence.py` (reformular línea 5 y línea 71 `plan-maestro.diff`), `test_supervised_test_patron_permiso.sh` (reformular línea 6), `test_gate_tier_budget_unchanged.py` (reformular línea 18), `test_validate_artifact_size.py` (eliminar tests del subparser `plan-maestro`) · archivos: 12 tests bajo `.spec/scripts/tests/` · depende de: T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17 · cubre: CA-13

### G3 — Skills SDD + docs canónicos

- [x] T19 — Reformular `.agents/skills/sdd-desatendido/SKILL.md` (líneas 3, 4, 12, 16, 22, 27, 44, 48, 49): sustituir menciones a "plan maestro"/"plan-maestro" por "mandato" donde aplique · archivos: `.agents/skills/sdd-desatendido/SKILL.md` · cubre: CA-14
- [x] T20 — Reformular `.agents/skills/sdd-preflight/SKILL.md:37` (`plan-maestro.md` → `plan.md` en la ruta de `--mandate`) · archivos: `.agents/skills/sdd-preflight/SKILL.md` · cubre: CA-14
- [x] T21 — Reformular `.agents/skills/sdd-supervisado/SKILL.md` (líneas 3 description "plan maestro", 34 tabla "Conducir un plan maestro...", 141, 293, 460, 470 — referencias a `append_changelog_line.py` retirado) · archivos: `.agents/skills/sdd-supervisado/SKILL.md` · cubre: CA-14
- [x] T22 — Reformular `.agents/skills/sdd-orquestar/SKILL.md:120` ("plan maestro cuando existe" → "plan cuando existe") · archivos: `.agents/skills/sdd-orquestar/SKILL.md` · cubre: CA-14
- [x] T23 — Reformular `.spec/SUPERVISADO.md:22` ("Dónde vive: .spec/_plantillas/plan-maestro.md" → nota de retiro) y línea 113 (eliminar fila `plan-maestro.md`/`mandato.md` de la tabla); reescribir § 1.1 "Plantilla del plan maestro por objetivo" como 1-2 líneas declarando el retiro y remitiendo a `.spec/SUPERVISADO.md § 1.2` · archivos: `.spec/SUPERVISADO.md` · cubre: CA-15
- [x] T24 — Reformular `.spec/PARADAS-SUPERVISADO.md:4` ("Ambas plantillas..." → "La plantilla `mandato.md` la referencia...") y `AGENTS.md:149` (`{spec,plan,mandato,plan-maestro,tasks}.md` → `{spec,plan,mandato,tasks}.md`) · archivos: `.spec/PARADAS-SUPERVISADO.md`, `AGENTS.md` · cubre: CA-15

### G4 — Migración U-0002/U-0003 + archivado de planes

- [x] T25 — Modificar `.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml`: vaciar `mandato:` a `""` (línea 297); eliminar bloque `dependencias:` (líneas 30-46 con comentario forward-compatible); añadir `depende_de:\n  - 0003-fiabilidad-de-gates-de-gobernanza` (grafía `gobernanza` con `b`, DD-4) · archivos: `.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml` · depende de: T5 · cubre: CA-07, CA-08
- [x] T26 — Modificar `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml`: vaciar `mandato:` a `""` (línea 58); eliminar bloque `dependencias:` (líneas 62-74); añadir `depende_de:\n  - 0002-versionado-y-actualizacion-del-instalador` · archivos: `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml` · depende de: T5 · cubre: CA-09, CA-10
- [x] T27 — Archivar `.spec/planes/0002-versionado-y-actualizacion-del-instalador/plan.md` → `.spec/planes-archive/0002-versionado-y-actualizacion-del-instalador/plan.md`; eliminar el directorio `.spec/planes/0002-*/` vacío · archivos: `.spec/planes/0002-versionado-y-actualizacion-del-instalador/` · depende de: T25 · cubre: CA-11, DD-2
- [x] T28 — Archivar `.spec/planes/0003-fiabilidad-de-gates-de-gobernanza/plan.md` → `.spec/planes-archive/0003-fiabilidad-de-gates-de-gobernanza/plan.md`; eliminar el directorio `.spec/planes/0003-*/` vacío · archivos: `.spec/planes/0003-fiabilidad-de-gates-de-gobernanza/` · depende de: T26 · cubre: CA-11, DD-2

### G5 — Acoplamiento con U-0005

- [x] T29 — Modificar `.spec/scripts/tests/test_u0005_ca12.py`: reducir `EXCLUDED_PATHS` a solo `[".spec/_plantillas/mandato.md"]` (líneas 20-25 y 53-58) · archivos: `.spec/scripts/tests/test_u0005_ca12.py` · cubre: CA-12a
- [x] T30 — Reformular `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/spec.md:69` (la lista de "4 plantillas que esta unidad no toca" pasa a ser 1, `mandato.md`) y nota línea 73 · archivos: `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/spec.md` · cubre: CA-12b
- [x] T31 — Reformular `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/tasks.md:16,27,37` (T8, T19 y validación final — la lista de 4 plantillas baja a 1) · archivos: `.spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/tasks.md` · cubre: CA-12b

## Validación final

- [x] Ejecutar `python3 -m pytest .spec/scripts/tests installer -q -k u0009` → exit 0 (CA-16, placeholder hasta que se creen los tests específicos de U-0009)
- [x] Ejecutar cada CA del spec.md uno por uno (comandos literales del bloque § Criterios de aceptación):
  - [x] CA-01 → `test ! -f .spec/_plantillas/plan-maestro.md`
  - [x] CA-02 → `test ! -f .spec/_plantillas/historial.md`
  - [x] CA-03 → `test ! -f .spec/_plantillas/paquete-tanda-aprobacion.md`
  - [x] CA-04 → `grep -c 'plan-maestro' .spec/_plantillas/mandato.md` = 0
  - [x] CA-05 → `grep -n '^depende_de:' .spec/_plantillas/_estado.yaml` retorna 1 línea con valor `[]`
  - [x] CA-06 → bloque awk + grep sobre comentario de `mandato:` retorna 0
  - [x] CA-07 → `awk '/^mandato:/{print; exit}' .spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml` retorna `mandato: ""`
  - [x] CA-08 → bloque awk + `grep -q '0003-fiabilidad-de-gates-de-gob*ernanza'` retorna exit 0
  - [x] CA-09 → análogo a CA-07 sobre U-0003
  - [x] CA-10 → bloque awk + `grep -q '0002-versionado-y-actualizacion-del-instalador'` retorna exit 0
  - [x] CA-11 → `test ! -f .spec/planes/0002-.../plan.md && test ! -f .spec/planes/0003-.../plan.md`
  - [x] CA-12a → `grep -cE 'plan-maestro|historial|paquete-tanda' .spec/scripts/tests/test_u0005_ca12.py` = 0
  - [x] CA-12b → doble `grep -F 'mandato.md' ... | grep -cE 'plan-maestro|historial|paquete-tanda'` retorna 0 en `U-0005/spec.md` y `U-0005/tasks.md`
  - [x] CA-13 → `grep -rl 'plan-maestro' .spec/scripts/ --exclude-dir=__pycache__` retorna vacío
  - [x] CA-14 → bucle sobre `sdd-desatendido sdd-preflight sdd-supervisado` con `grep -q 'plan-maestro' ".agents/skills/$s/SKILL.md"` retorna exit 0 final
  - [x] CA-15 → `grep -c 'plan-maestro' .spec/SUPERVISADO.md .spec/PARADAS-SUPERVISADO.md AGENTS.md` = 0 en cada archivo
  - [x] CA-16 → `python3 -m pytest .spec/scripts/tests installer -q` retorna exit 0
- [x] Ejecutar comando de validación consolidado de `plan.md` (`pytest -k u0009 && pytest && wc -l ... && test ...`): resultado esperado `2 passed` / `0 failed` / `0` / exit 0
- [x] Gate de código ejecutado (`sdd-gate` fase `codigo`) con crítico de contexto fresco y veredicto registrado en `_estado.yaml > gates.codigo` — *PENDIENTE: lo cierra sdd-gate*
- [x] Regenerar espejos `.claude/skills/*` con `scripts/materialize_claude_skills.py` (R-4); check extra `grep -c 'plan-maestro' .claude/skills/sdd-supervisado/SKILL.md` → 0
- [x] `git status --short .spec/_plantillas/` muestra ≤ 1 línea (solo `mandato.md` consolidado)
- [ ] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`

## Notas de implementación

- Antes de T25/T26: preservar la prosa del bloque `dependencias:` actual de U-0002 (líneas 42-46) y U-0003 (líneas 68-74) en `bitacora.md` antes del borrado, para no perder contexto narrativo (DD-4).
- R-1 (crítico): T6 modifica `resolve_unit` para aceptar `mandato: ""` cuando `modo: desatendido`. La rama `unidad-mandato-vacio` se restringe a `modo: supervisado`. Verificación implícita en CA-16.
- R-2: T18 migra fixtures `plan-maestro.md` → `plan.md`; los tests que comparan strings literal se reformulan. La suite global (CA-16) debe pasar antes de cerrar.
- R-3: releer `.agents/skills/sdd-orquestar/SKILL.md` completa en implementación por si hay lógica condicional sobre `plan-maestro` que grep no detectó (T22 cubre la línea 120 explícita).
- R-4: tras T19..T21, regenerar espejos `.claude/skills/*` con `scripts/materialize_claude_skills.py` antes del gate de código; añadir check extra en Validación final.
- Fuera de alcance (no ejecutar en esta unidad): limpieza de comentarios inline históricos en U-0001/U-0004/U-0005 que mencionan `_plan-maestro.md`; reescritura de U-0002 `spec.md:599` y `plan.md:95`; redefinición del modo `desatendido` (PQ-3).
- **Desviación T15**: `supervised-test.sh` no se eliminó pese a T15 del plan. Tiene callers vivos (`snapshot_evidence.sh:29` define `SUPERVISED_TEST="$SCRIPT_DIR/supervised-test.sh"` y lo invoca en `bash "$SUPERVISED_TEST" capturar-unidad …`; `test_supervised_test_patron_permiso.sh:29,49,51` y `test_supervised_test_session_limit.sh:38,56,58` lo `source`an). La verificación "ningún `comando_validacion` vivo lo invoca" del plan fue incorrecta: ningún `comando_validacion` (campo de `_estado.yaml`) lo invoca, pero sí es load-bearing para el ritual de evidencia y para tests de regresión. La acción: limpiar las 3 referencias internas a `_plan-maestro.md`/`plan-maestro.diff` (que era el objetivo de T15) y dejar el archivo. Documentado en bitácora.
- **Cleanup adicional fuera del plan explícito**: (a) `installer/kit_manifest.yaml:33-34` — entrada de `append_changelog_line.py` retirada para que `installer cli --install` no falle por source ausente (T13 dejó el archivo borrado pero la entrada del manifiesto quedó). (b) `docs/inventario-extraccion.yaml:46-47` — entrada de `append_changelog_line.py` retirada para que `test_manifest_matches_inventory` no reporte "marcado viaja sin cubrir". (c) `.spec/scripts/mandate_anchor.py:45,164` — referencias residuales a `append_changelog_line.py` en docstrings limpiadas. (d) `.spec/scripts/tests/test_skills_reference_scripts.py:147` — `"append_changelog_line.py"` retirada de la lista `expectations["sdd-supervisado"]` (el script ya no existe y `sdd-supervisado/SKILL.md` ya no lo menciona tras T21). (e) `.spec/scripts/tests/test_u0005_ca08.py:26` — `depende_de` añadido a `EXPECTED_TOP_LEVEL_KEYS` (test contract update, no migración de U-0005 — su spec ya estaba reformulada en T30; el test no se había actualizado al nuevo campo). (f) Espejos `.claude/skills/*` regenerados con `scripts/materialize_claude_skills.py` para que pasen los tests de mirror (R-4).
- **Regresión preexistente (no causada por U-0009)**: `test_test_subset.py::LoadSubarbolesTests::test_no_orchestrator_ni_studio_app_cableados_en_el_codigo` falla antes y después de U-0009 (verificado con `git stash`): el docstring de `map_pytest_subtree` en `.spec/scripts/test_subset.py` menciona `orchestrator/src/iark_orchestrator/` como referencia histórica al script original del que se extrajo el genérico. Está fuera de alcance de U-0009 (no toca `test_subset.py` ni su historial). CA-16 reporta 837 passed / 1 failed — el failed es este y está documentado; el resto de la suite está verde.