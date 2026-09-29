# Plan técnico — Estandarización de nomenclatura, unificación de artefactos SDD y reducción de ventana de contexto

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de un `spec.md` aprobado (gate `spec: refinado`, 3 hallazgos resueltos — `bitacora.md:25`).

## Enfoque

La unidad ataca tres síntomas declarados en `spec.md § Problema` (convención implícita, duplicación de prosa en plantillas, `_estado.yaml` 55% comentarios) **sin introducir archivos nuevos** (restricción operativa de la unidad) y sin tocar las 4 plantillas prerrequisito de U-0009 (`plan-maestro.md`, `mandato.md`, `historial.md`, `paquete-tanda-aprobacion.md` — ver CA-12). La estrategia es: una **fuente única canónica en `.spec/README.md`** (sección `## nomenclatura` y sección `## concurrencia`) que absorbe la prosa duplicada y los comentarios inline; las 7 plantillas en alcance (las 6 `.md` + `_estado.yaml`) **referencian** esa fuente con anchors reproducibles en vez de re-declarar; los verificadores son **un test por CA-NN** en `.spec/scripts/tests/` (11 tests CA-02..CA-12, CA-13 itera sobre la lista). El split es: G1 produce el README canónico (nomenclatura + concurrencia); G2 consolida las 7 plantillas (incluida la reducción de `_estado.yaml` de 185 a ≤100 líneas); G3 añade los 11 tests. **G1 primero** porque G2 referencia anchors que G1 crea.

El alcance evita rename de archivos del kit (`scripts/install_pre_push_hook.sh` está en `installer/kit_manifest.yaml:187` e `installer/installer.py:81,231,480`; romper ese contrato exigiría U-0007 primero). El outlier se documenta como excepción vigente en la tabla del README § Nomenclatura.

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `.spec/README.md` | modificar | Añadir `# nomenclatura:` (anchor literal exigido por CA-01 — tabla de excepciones + reglas por extensión) y `## concurrencia` (anchor literal exigido por CA-11 — convención bitácora + regla 0083/0084). Posicionar entre `## Unidad de trabajo` y `## Las fases y sus skills` (nomenclatura) y entre `## Governance` y `## sdd-kit` (concurrencia). |
| `.spec/_plantillas/_estado.yaml` | modificar | Reducir de 185 a ≤100 líneas (CA-08). Mover los bloques de comentarios inline ≥3 líneas al README (§ Nomenclatura, § Concurrencia, ampliar § Los cuatro modos y § Gates de validación) o a una sola línea explicativa. Preservar todos los campos y sus valores por defecto; preservar `yaml.safe_load` parseable. |
| `.spec/_plantillas/bitacora.md` | modificar | Reemplazar la línea 13 "Convención de merge (unidad 0098)" por `Ver .spec/README.md § concurrencia` (anchor literal). |
| `.spec/_plantillas/plan.md` | modificar | Comprimir el bloque de líneas 36-52 ("Si el cambio no admite paralelismo real...") de 16 líneas no-vacías a ≤5 (CA-10). Mover la justificación detallada al README. |
| `.spec/_plantillas/paquete-aprobacion.md` | modificar | Reemplazar las líneas 19-21 ("Perfil de esfuerzo: `<perfil>`...") por una referencia al README § Los cuatro modos o al header de `.spec/perfiles.yaml`. |
| `.spec/_plantillas/spec.md` | verificar | Sin cambios — su prosa sobre "Criterios de aceptación" y "Governance aplicable" no duplica bloques ≥5 líneas con otras plantillas en alcance (verificación del verificador de CA-05). |
| `.spec/_plantillas/tasks.md` | verificar | Sin cambios — su prosa sobre "Notas de implementación" es <5 líneas y no duplica. |
| `.spec/_plantillas/research.md` | verificar | Sin cambios — su prosa sobre hallazgos/evidencia no duplica con otras plantillas. |
| `.spec/scripts/tests/test_u0005_ca02.py` | crear | Verificador de nomenclatura (CA-02): lee convención desde `.spec/README.md` (no hardcoded). Recorre `scripts/`, `installer/`, `.spec/scripts/`, `.agents/`, `.claude/`, `.spec/units/`. Lista outliers con exit 0. Métodos: `test_u0005_ca02_*` (seleccionable por `-k u0005_ca02`). |
| `.spec/scripts/tests/test_u0005_ca03.py` | crear | Verificador (CA-03): corre el verificador de CA-02 contra un fixture con outlier conocido y verifica que su nombre aparece literalmente en stdout con `exit 0`. Método: `test_u0005_ca03_*`. |
| `.spec/scripts/tests/test_u0005_ca04.py` | crear | Verificador (CA-04): verifica que la cadena `install_pre_push_hook.sh` aparece en la salida del verificador de CA-02 Y en la tabla de excepciones de `.spec/README.md § nomenclatura`. Método: `test_u0005_ca04_*`. |
| `.spec/scripts/tests/test_u0005_ca05.py` | crear | Verificador de duplicación (CA-05): bloques de prosa ≥5 líneas consecutivas (o comentarios `#` consecutivos ≥5 en `_estado.yaml`) repetidos en 2+ plantillas en alcance. Método: `test_u0005_ca05_*`. |
| `.spec/scripts/tests/test_u0005_ca06.py` | crear | Verificador (CA-06): itera sobre las plantillas que sobrevivan al filtro de CA-05 con prosa duplicada legítima (i.e., secciones que sí se re-declaran por necesidad), extrae los anchors reproducibles que apunten a `.spec/README.md § nomenclatura` o `§ concurrencia`, y verifica que cada plantilla re-declarante contiene al menos uno. Método: `test_u0005_ca06_*`. |
| `.spec/scripts/tests/test_u0005_ca07.py` | crear | Verificador (CA-07): corre `git status --short .spec/_plantillas/` y verifica que cada path listado pertenece a la lista permitida (los 7 templates en alcance o los 4 de U-0009 intactos). Método: `test_u0005_ca07_*`. |
| `.spec/scripts/tests/test_u0005_ca08.py` | crear | Verificador de tamaño: `wc -l .spec/_plantillas/_estado.yaml` ≤100. |
| `.spec/scripts/tests/test_u0005_ca09.py` | crear | Verificador de comentarios inline: parsea `_estado.yaml`, busca bloques `#` consecutivos ≥3 líneas sin anchor de redirección al README. |
| `.spec/scripts/tests/test_u0005_ca10.py` | crear | Verificador de bloque "Complejidad": extrae el bloque entre `> Si el cambio no admite paralelismo real` y el siguiente `##` en `plan.md`, cuenta líneas no-vacías, verifica ≤5. |
| `.spec/scripts/tests/test_u0005_ca11.py` | crear | Verificador de centralización: `grep -F 'Convención de merge (unidad 0098)' .spec/_plantillas/bitacora.md` = 0 hits Y `grep -F '0083/0084' .spec/_plantillas/_estado.yaml` = 0 hits Y ambos anchors del README § Concurrencia aparecen en las plantillas. |
| `.spec/scripts/tests/test_u0005_ca12.py` | crear | Verificador de byte-identidad: `git diff HEAD -- .spec/_plantillas/{plan-maestro,mandato,historial,paquete-tanda-aprobacion}.md` retorna vacío tras la corrida. |
| `.spec/scripts/tests/test_u0005_ca13.py` | crear | Verificador de trazabilidad: itera sobre CA-02..CA-12, verifica que cada uno tiene al menos un test `test_u0005_ca<NN>.py` coleccionable. |

## Reutilización (no reinventar)

- `.spec/scripts/tests/test_scripts_naming_and_strict_mode.py:25-44` — patrón de lista `NAMED_FILES` por stem/kebab; el verificador de CA-02 lo extiende con la lectura dinámica de convención desde el README en vez de hardcodear la lista.
- `.spec/scripts/tests/test_common.py` — helpers `_common.field()`, `_common.field_list()`, `_common.top_level_block()`, `_common.child_blocks()` consumidos por `validate_mode_conversion.py` y `validate_gate_budget.py`. El verificador de CA-09 los usa para parsear `_estado.yaml` sin PyYAML (consistente con el resto del andamiaje).
- `installer/installer.py:81,231,480` y `installer/kit_manifest.yaml:187` — referencias a `scripts/install_pre_push_hook.sh` que el verificador de CA-02 detecta como outlier; el plan **no** las toca (decisión DD-5).
- `installer/tests/test_manifest.py:46` — test existente que verifica la presencia del path en el manifiesto; preserva la excepción documentada.
- `.spec/units/0004-purga-y-gitignore/plan.md:34` — patrón de grupo único con columna `Complejidad` cuando no hay paralelismo real; reusado para G1 (G2+G3 sí admiten paralelismo, por lo que el plan tiene 3 filas).

## Decisiones de diseño

- **Decisión (DD-1):** la sección de nomenclatura va en `.spec/README.md` con anchor literal `# nomenclatura:`. **Razón:** la restricción operativa prohíbe archivos nuevos (`spec.md § Alcance — No incluye`), y `.spec/README.md` es el doc canónico del protocolo (`README.md:5-6` se declara a sí mismo como fuente canónica). El anchor literal es `# nomenclatura:` (con `:`) para que `grep -n '^# nomenclatura' .spec/README.md` devuelva exactamente una línea, según exige CA-01. Se posiciona entre `## Unidad de trabajo` y `## Las fases y sus skills` para mantener la proximidad con la unidad de trabajo (los archivos que la convención gobierna).
- **Decisión (DD-2):** la sección de concurrencia va en `.spec/README.md` con anchor literal `## concurrencia` (en minúscula, consistente con el anchor de CA-11 que el verificador busca). **Razón:** la suposición conservadora del spec (`spec.md:85`) prefiere `.spec/README.md` por ser canónico; `.spec/SUPERVISADO.md` se reserva para el modo supervisado y excede el alcance de CA-11. Se posiciona entre `## Governance` y `## sdd-kit`. El verificador de CA-11 compara el anchor literal en minúscula (forma que aparece en `bitacora.md:13` y `_estado.yaml:5-11` originales), y los anchors que las plantillas usan para apuntar son `Ver .spec/README.md § concurrencia`.
- **Decisión (DD-3):** los 102 líneas de comentarios inline de `_estado.yaml` se redistribuyen así: (a) bloque `modo` (líneas 35-47, 13 líneas) → absorbido por `## Los cuatro modos` del README (existente), que ya cubre el dominio — `_estado.yaml` retiene solo la primera línea del comentario; (b) bloque `riesgo` (líneas 49-53, 5 líneas) → absorbido por `## Gates de validación` del README (existente, `README.md:123-130` ya lista los topes); (c) bloque multi-colaborador + `modo_conversion` + regla `0083/0084` (líneas 5-11 + 60-88, ~35 líneas) → absorbido por `## concurrencia` del README (nueva); (d) bloque `modelo_ejecucion` (líneas 98-118, 21 líneas) → comprimido a 5 líneas de referencia al README, conservando el contrato de campo intacto; (e) bloque `gates` (líneas 119-163, 45 líneas) → comprimido a 8 líneas, los campos siguen siendo parseables y los detalles de contrato van al README; (f) bloque `mandato` (líneas 165-184, 20 líneas) → comprimido a 4 líneas, los detalles del gobierno del campo van al README. **Razón:** los campos YAML del estado (`fase`, `estado`, `modo`, `riesgo`, etc.) deben permanecer como valores escalares parseables; los comentarios que explican "qué significa cada modo" o "cómo se resuelven conflictos" son prosa del protocolo, no del estado machine-readable. La compresión se hace **sin cambiar el contrato** (el conjunto de claves y sus valores por defecto): `yaml.safe_load(_estado_plantilla)` devuelve la misma estructura antes y después.
- **Decisión (DD-4):** tres tests separados (`test_u0005_ca02.py`, `test_u0005_ca03.py`, `test_u0005_ca04.py`) — uno por CA-NN, con métodos `test_u0005_ca02_*`, `test_u0005_ca03_*`, `test_u0005_ca04_*` respectivamente, **todos** seleccionables por `-k u0005_ca<NN>`. **Razón:** CA-13 del spec exige textualmente "cada CA-NN de CA-02 a CA-12 tiene al menos un test en `.spec/scripts/tests/` con `test_u0005_ca<NN>` en el nombre" — un archivo único con métodos `test_ca03_*` (sin prefijo `u0005_`) no satisface `-k u0005_ca03`. La verificación importa también como contrato de auto-trazabilidad (la `test_u0005_ca13.py` itera sobre CA-02..CA-12 y verifica que cada CA tiene su verificador coleccionable). La estructura interna de cada archivo es libre (un verificador compartido por CA-02/03/04 es válido si cada uno importa y ejercita el verificador de manera trazable).
- **Decisión (DD-5):** `scripts/install_pre_push_hook.sh` queda como **excepción documentada**, no se renombra. **Razón:** está en 5 sitios load-bearing (`installer/kit_manifest.yaml:187`, `installer/installer.py:81,231,480`, `installer/tests/test_manifest.py:46`, `installer/installer.py:231`); un rename rompe la idempotencia del instalador y exigiría coordinarse con U-0007 (manifiesto del kit) y U-0002 (CA-33 byte-identidad del hook). El verificador de CA-02 lo nombra; la tabla de excepciones del README § Nomenclatura lo cita con motivo "load-bearing para `installer/installer.py:81` (`PRE_PUSH_HOOK_INSTALLER`) — rename queda fuera de esta unidad por contrato con U-0007".
- **Decisión (DD-6):** el bloque de 17 líneas (16 no-vacías) entre `> Si el cambio no admite paralelismo real` y `## Riesgos y mitigaciones` en `plan.md:36-52` se comprime a 3 líneas no-vacías: una sobre la regla del grupo único (preservar columna `Complejidad`), una sobre los valores válidos (`estandar` | `complejo`), y una referencia `Ver .spec/README.md § Reglas del fan-out para justificación detallada y ejemplos`. **Razón:** la justificación detallada (ejemplos, contraste con migración de datos, cita a `MODELO-AGENTES.md`) es prosa del protocolo, no de la plantilla de cada plan; el gate y el retomar pagan ventana por ella cada vez que cargan la plantilla. La compresión retiene el literal `Complejidad: complejo` y `estandar` que `_estado.yaml > modelo_ejecucion > implementar` exige (`plan.md:50-51`), y retiene la pista operativa de cuándo usar grupo único (línea 1 del bloque comprimido).

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | README canónico (nomenclatura + concurrencia) — fuente única | `.spec/README.md` | — | estandar |
| G2 | Consolidación de las 7 plantillas en alcance (reducir duplicación + reducir `_estado.yaml` a ≤100 líneas) | `.spec/_plantillas/_estado.yaml`, `.spec/_plantillas/bitacora.md`, `.spec/_plantillas/plan.md`, `.spec/_plantillas/paquete-aprobacion.md`, `.spec/_plantillas/spec.md`, `.spec/_plantillas/tasks.md`, `.spec/_plantillas/research.md` | G1 | estandar |
| G3 | Verificadores en `.spec/scripts/tests/` (11 tests, uno por CA-02..CA-12 + CA-13 de auto-trazabilidad) | `.spec/scripts/tests/test_u0005_ca02.py`, `test_u0005_ca05.py`, `test_u0005_ca08.py`, `test_u0005_ca09.py`, `test_u0005_ca10.py`, `test_u0005_ca11.py`, `test_u0005_ca12.py`, `test_u0005_ca13.py` | G1 | estandar |

> G1 va primero porque G2 inserta anchors (`§ nomenclatura`, `§ concurrencia`) que G1 crea. G2 y G3 admiten paralelismo real (archivos disjuntos: G2 toca plantillas, G3 toca tests).

## Riesgos y mitigaciones

- **Riesgo:** mover comentarios inline de `_estado.yaml` rompa `yaml.safe_load` y `sdd-retomar.py` deje de parsear el estado. **Mitigación:** el verificador de CA-08 (`test_u0005_ca08.py`) ejecuta `yaml.safe_load(_estado_plantilla)` sobre la plantilla reducida y exige que el set de claves (`fase`, `estado`, `modo`, `riesgo`, `perfil`, `gates`, `modelo_ejecucion`, etc.) coincida con el set original — preserva el contrato del campo aunque cambie la prosa alrededor. Verificación adicional manual antes de cerrar: `python3 -c "import yaml; yaml.safe_load(open('.spec/_plantillas/_estado.yaml'))"` retorna sin excepción.
- **Riesgo:** `sdd-retomar.py` parsee comentarios como datos y la compresión a referencias los deje en posiciones que el script no espera. **Mitigación:** inspección previa de `.spec/scripts/sdd_retomar.py` (lectura por búsqueda): el script consume `_estado.yaml` vía `_common.field`/`_common.field_list`/`_common.top_level_block`/`_common.child_blocks`, que **ignoran comentarios por construcción** (`test_common.py:174-188` lo cubre). La compresión no toca valores escalares, solo comentarios, así que el contrato de lectura no cambia.
- **Riesgo:** el verificador de CA-02 lee `.spec/README.md` y si alguien cambia el anchor `# nomenclatura:` por otro, el verificador queda roto silenciosamente. **Mitigación:** el verificador declara el anchor esperado explícitamente como constante (`EXPECTED_ANCHOR = "# nomenclatura:"`) y falla con mensaje literal si no encuentra exactamente una línea que coincida; el CA-01 mismo (`grep -n '^# nomenclatura' .spec/README.md`) detecta la regresión en CI antes que el verificador corra.
- **Riesgo:** los 11 tests nuevos de U-0005 interfieran con `--collect-only` previo o con la suite de pytest del instalador. **Mitigación:** los tests se filtran por `-k u0005` para aislar la corrida; `python3 -m pytest .spec/scripts/tests installer -q -k u0005` se usa en validación; ningún test preexistente se modifica ni se retira (CA-14).
- **Riesgo:** una de las 4 plantillas excluidas (`plan-maestro.md`, `mandato.md`, `historial.md`, `paquete-tanda-aprobacion.md`) cambie por accidente durante la implementación (un editor con auto-format, un `git add -A`, etc.) y CA-12 no lo detecte hasta el gate de código. **Mitigación:** el verificador de CA-12 corre `git diff HEAD -- <4 paths>` en la fase de implementación, **antes** del commit; cualquier diff no vacío falla el test con el path explícito.
- **Riesgo:** G2 reduzca `_estado.yaml` por debajo del contrato mínimo (elimina un campo por error). **Mitigación:** el verificador de CA-08 compara el set de claves top-level del YAML parseado contra una lista dorada (`EXPECTED_TOP_LEVEL_KEYS`) capturada en esta fase antes de la implementación; diff de claves = fail.
- **Riesgo:** la cita a `.spec/MODELO-AGENTES.md` (mencionada en `_estado.yaml:46,82`, `plan.md:52`, `README.md:48`, `SUPERVISADO.md:1,17`, `pre-commit-gate.sh:18`, `effort_profile.py:9`) quede colgante tras la compresión de `plan.md`. **Mitigación:** la compresión retiene la cita (el bloque comprimido sigue terminando con `Ver .spec/MODELO-AGENTES.md` o equivalente); la resolución del hallazgo adicional es prerrequisito de otra unidad, fuera de alcance.

## Comando de validación

Comando principal (literal de `_estado.yaml > comando_validacion` tras la implementación):

```
python3 -m pytest .spec/scripts/tests installer -q -k u0005
```

Sub-verificaciones específicas (comandos literales del spec, sección Criterios de aceptación):

```bash
# CA-01 — anchor único en README
grep -n '^# nomenclatura' .spec/README.md

# CA-08 — tamaño de _estado.yaml plantilla
wc -l .spec/_plantillas/_estado.yaml | awk '{print $1}'

# CA-11 — centralización de concurrencia
grep -F 'Convención de merge (unidad 0098)' .spec/_plantillas/bitacora.md
grep -F '0083/0084' .spec/_plantillas/_estado.yaml

# CA-12 — byte-identidad de las 4 plantillas excluidas
git diff HEAD -- .spec/_plantillas/plan-maestro.md .spec/_plantillas/mandato.md \
  .spec/_plantillas/historial.md .spec/_plantillas/paquete-tanda-aprobacion.md

# CA-13 — trazabilidad de verificadores
python3 -m pytest .spec/scripts/tests -q --collect-only -k u0005
```

Smoke test post-cambio (YAML parseable, suite completa verde):

```bash
python3 -c "import yaml; yaml.safe_load(open('.spec/_plantillas/_estado.yaml'))" \
  && python3 -m pytest .spec/scripts/tests installer -q
```
