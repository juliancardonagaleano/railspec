# Bitácora — <título de la unidad>

> Log append-only de handoff. Cada sesión añade una entrada de exactamente
> **1 línea** al **final**, sin cuerpo narrativo. El detalle de los hallazgos
> de gate vive en `_estado.yaml > gates`; este archivo es un índice temporal.

**Formato de cada entrada:**

```
## <ISO-8601> · <fase|gate>:<veredicto> · <resumen de una oración>
```

**Convención de merge:** ver [.spec/README.md § concurrencia](README.md#concurrencia).

---

## <ISO-8601> · fase:<spec|plan|tasks|implement> · <resumen>

## 2026-09-29T19:30:00Z · fase:plan · plan.md redactado (120 líneas, presupuesto 120): 16 CA convertidos en 5 grupos paralelos G1..G5 (G1=plantillas+estado; G2=scripts andamiaje; G3=skills+docs; G4=migración U-0002/U-0003+archivado de planes; G5=acoplamiento U-0005); decisiones DD-1 block form para `depende_de:` (consistente con `modo_conversion:` block list), DD-2 archivado bajo `.spec/planes-archive/` (PQ-1 conservadora, U-0002/U-0003 ya en `fase: done`), DD-3 rama legacy compat de `plan-maestro.md` se elimina del validador (no hay mandato vivo que la use), DD-4 IDs concretos 0002/0003 derivados del bloque `dependencias:` actual; contradicción detectada con `validate_mandate.py:362` (`unidad-mandato-vacio` rechaza `mandato: ""`) → riesgo crítico R-1 con mitigación concreta (resolver trata `modo: desatendido` + `mandato:` vacío como válido); total ~50 archivos en alcance entre los 5 grupos (sin contar __pycache__) — próxima: gate del plan con `sdd-gate`.

## <ISO-8601> · gate:<spec|plan|tasks|codigo>:<veredicto> · <N> hallazgos en _estado.yaml

## 2026-09-29T18:00:00Z · fase:spec · unidad creada en triaje, fase spec, modo semi-autonomo, riesgo alto, perfil estandar — próxima: redactar spec.md con `sdd-especificar-redactor`. Petición del usuario: "Retira el protocolo de plan maestro, con la funcionalidad de dependencia entre planes se cubre este requerimiento, por lo tanto en este momento está siendo redundante". Y "verificar si pueden existir problemas de drift con lo definido en _estado.yaml".

## 2026-09-29T18:30:00Z · fase:spec · spec.md redactado (79 líneas, presupuesto 120): 16 CA verificables con comandos únicos, 4 de ellos son drift-check explícito sobre U-0002 y U-0003 (CA-07..CA-10), CA-12 cierra el acoplamiento con U-0005 (test_u0005_ca12.py referencia las 4 plantillas que U-0009 borra — drift que esta unidad debe cerrar porque U-0005 ya está done y no puede reabrir); el brief traía dos errores materiales corregidos (kit_manifest.yaml NO declara plan-maestro.md individualmente — declara `.spec/_plantillas` como directorio; sdd-orquestar NO menciona plan-maestro, son solo 3 skills no 4); PQ-1 (qué hacer con el contenido de los planes archivados), PQ-2 (forma inline vs block de depende_de), PQ-3 (modo desatendido de una sola unidad — no redefinido aquí). governance_refs=[ninguna-aplicable] mantenido con precedente U-0004/U-0005 — próxima: gate del spec con `sdd-gate`.

## 2026-09-29T19:00:00Z · gate:spec:refinado · 1 hallazgo alta resuelto (CA-08 typo: `governanza` (v) en lugar de `gobernanza` (b) — corregido con anotación literal al grep), 1 media resuelto (CA-12 partido en CA-12a comando + CA-12b prosa reformulada con dos greps literales), 4 baja anotados (cifras y mezcla exit-code/conteo); L4 confirmó centinela [ninguna-aplicable] con precedentes U-0004/U-0005; spec.md 80 líneas tras refinamiento — próxima: fase plan

## 2026-09-29T18:30:00Z · fase:spec · spec.md redactado (78 líneas, presupuesto 120): 16 CA verificables con comandos únicos, 4 de ellos son drift-check explícito sobre U-0002 y U-0003 (CA-07..CA-10), CA-12 cierra el acoplamiento con U-0005 (test_u0005_ca12.py referencia las 4 plantillas que U-0009 borra — drift que esta unidad debe cerrar porque U-0005 ya está done y no puede reabrir); el brief traía dos errores materiales corregidos (kit_manifest.yaml NO declara plan-maestro.md individualmente — declara `.spec/_plantillas` como directorio; sdd-orquestar NO menciona plan-maestro, son solo 3 skills no 4); PQ-1 (qué hacer con el contenido de los planes archivados), PQ-2 (forma inline vs block de depende_de), PQ-3 (modo desatendido de una sola unidad — no redefinido aquí). governance_refs=[ninguna-aplicable] mantenido con precedente U-0004/U-0005 — próxima: gate del spec con `sdd-gate`.
## 2026-09-29T21:30:00Z · fase:implement · preservada prosa del bloque `dependencias:` de U-0002 (líneas 30-46 originales) y U-0003 (líneas 62-74 originales) en este archivo, antes del borrado (T24b / DD-4):

### U-0002 (.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml:30-46, bloque `dependencias:` a migrar a `depende_de:`)

```
# --- Cadena de dependencias (forward-compatible con plan-maestro) ---
# Esta unidad NO puede cerrar (fase: done) hasta que la unit 0003 llegue a done.
# Razón: los fixes 1-8 de 0003 hacen confiable el chequeo de superficie que
# la unit 0002 ya aprobó (gate: refinado) con base en una triada estrecha que
# perdió opencode.jsonc. Sin 0003, un re-gate del spec de 0002 (necesario por
# el scope delta que añadió CA-51..CA-55) podría volver a aprobar con base en
# evidencia cacheada o falla de filesystem.
# Mecánica: la dependencia se respeta manualmente por el orquestador (el campo
# `estado` formal no incluye 'bloqueado'; el protocolo SDD v2 no tiene ese
# estado — ver comentario arriba). Cuando 0003 llegue a done, el orquestador
# puede re-correr el gate del spec de 0002 (con el script nuevo) y, si pasa,
# avanzar 0002 a implement.
dependencias:
  - unidad: 0003-fiabilidad-de-gates-de-gobernanza
    relacion: "0002 no puede cerrar hasta que 0003 llegue a done"
    motivo: "0003 endurece el gate de superficie (triada declarativa + segunda red + re-validación entre fases). Sin 0003, el gate de 0002 vuelve a aprobar con base en evidencia potencialmente cacheada o estrecha (incidente 2026-09-29)."
    disparador: "0003 llega a done"
```

### U-0003 (.spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml:62-74, bloque `dependencias:` a migrar a `depende_de:`)

```
# --- Cadena de dependencias (forward-compatible con plan-maestro) ---
# Esta unidad debe llegar a `done` ANTES de que la unit 0002 pueda cerrar.
# Razón: 0002 introduce contrato de gobernanza (Cobertura doble Claude+Opencode,
# CA-51..CA-55) y depende de los fixes 1-8 de esta unidad para que ese contrato
# pueda ser validado confiablemente por el gate del spec de 0002 cuando se
# re-corre después del scope delta (ver § Análisis de regresiones de 0002).
dependencias:
  - unidad: 0002-versionado-y-actualizacion-del-instalador
    relacion: "0003 debe llegar a done antes que 0002 cierre (done)"
    motivo: "0002 entrega el contrato de gobernanza (AGENTS.md + .mcp.json + opencode.jsonc);
             los fixes 1-8 hacen que el gate de 0002 pueda verificar confiablemente
             ese contrato. Sin 1-8, el gate de 0002 vuelve a aprobar con base en
             evidencia cacheada del spec inicial."
```

## 2026-09-29T22:30:00Z · fase:implement · implementadas las 31 tareas del checklist (T15 marcada como desviación documentada); 16/16 CA-NN verificables con comandos únicos retornan PASS; CA-16 (suite verde) reporta 837 passed / 1 failed — el único fallo es `test_test_subset.py::LoadSubarbolesTests::test_no_orchestrator_ni_studio_app_cableados_en_el_codigo`, regresión preexistente verificada con `git stash` (falla antes y después de U-0009: docstring de `map_pytest_subtree` en `.spec/scripts/test_subset.py` menciona `orchestrator/src/iark_orchestrator/` como referencia histórica al script original del que se extrajo el genérico, fuera del alcance de esta unidad); desviaciones documentadas: (1) T15 no elimina `supervised-test.sh` porque tiene callers vivos (`snapshot_evidence.sh:29` lo invoca vía `bash "$SUPERVISED_TEST" capturar-unidad …`, y dos tests de regresión lo `source`an: `test_supervised_test_patron_permiso.sh:29,49,51` y `test_supervised_test_session_limit.sh:38,56,58`) — la verificación del plan "ningún `comando_validacion` vivo lo invoca" fue inexacta: ningún `comando_validacion` lo invoca, pero es load-bearing para el ritual de evidencia; (2) cleanup adicional fuera del plan explícito para que la suite pase: entrada `append_changelog_line.py` retirada de `installer/kit_manifest.yaml:33-34` y `docs/inventario-extraccion.yaml:46-47` (T13 borró el archivo, las entradas quedaron); referencias residuales a `append_changelog_line.py` limpiadas en `.spec/scripts/mandate_anchor.py:45,164` y `.spec/scripts/tests/test_skills_reference_scripts.py:147`; `depende_de` añadido a `EXPECTED_TOP_LEVEL_KEYS` en `.spec/scripts/tests/test_u0005_ca08.py:26` (test contract update, no migración de U-0005 — el spec ya estaba reformulado en T30); espejos `.claude/skills/*` regenerados con `scripts/materialize_claude_skills.py` (R-4) — siguiente: gate de código con `sdd-gate` fase `codigo`, crítico de contexto fresco.
