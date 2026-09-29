# Spec — Estandarización de nomenclatura, unificación de artefactos SDD y reducción de ventana de contexto

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

Tres síntomas visibles hoy en `.spec/_plantillas/` y `scripts/`:

1. **Convención de nombres implícita y dispersa.** El repo usa snake_case para Python (PEP 8), kebab-case para shell en `.spec/scripts/` pero **mezcla ambos en `scripts/`** (`install_pre_push_hook.sh` snake vs `mcp-pce.sh` kebab vs `mcp-pce.py` kebab), kebab-case para skills/agents/unidades, snake_case para YAML. La regla existe implícitamente; no está escrita en un solo lugar ni la verifica nada — `grep` por patrones de violación no existe.

2. **Duplicación de prosa en plantillas.** `bitacora.md:13` re-implementa "Convención de merge (unidad 0098)" que ya está implícita en la regla multi-colaborador de `_estado.yaml:5-11` (ambos mecanismos resuelven conflictos concurrentes de archivo, pero el primero es para `## ` entries de bitácora y el segundo para campos de `_estado.yaml`); `paquete-aprobacion.md:18-21` re-implementa la semántica de "perfil de esfuerzo" que ya vive en `.spec/perfiles.yaml`; `_estado.yaml:35-48` re-explica los 4 modos que ya están en `.spec/README.md:39-45`. Cada plantilla re-importa texto y el `sdd-gate` lo paga en cada iteración.

3. **`_estado.yaml` consume 102 líneas de comentarios de 185 totales** (55%), y `plan.md:36-52` carga una nota de 14 líneas sobre "Complejidad: complejo" que duplica lo que debería estar en una sección canónica del README. Cada gate del spec lee las 185 líneas; cada `sdd-retomar` carga las 102 líneas de comentario antes de leer estado.

Si esto no se resuelve: cada nueva unidad hereda la dispersión, el `sdd-gate` paga ventana por texto redundante en cada iteración, y un cambio a la regla de nomenclatura exige tocar ≥ 4 archivos hoy.

> **Hallazgo adicional** (no es CA, queda para otra unidad): `.spec/MODELO-AGENTES.md` está citado desde `_estado.yaml:46,82`, `plan.md:52`, `README.md:48`, `SUPERVISADO.md:1,17`, `pre-commit-gate.sh:18` y `effort_profile.py:9`, pero **no existe** en el filesystem. La creación del doc o la redirección de las citas queda fuera de este spec por la restricción de no introducir archivos nuevos.

## Resultado esperado

1. **Una regla de nomenclatura declarada una sola vez** en una sección canónica — `.spec/README.md` § Nomenclatura (existente) u otra fuente única que decida el plan. La regla cubre `.py`, `.sh`, `.md`, `.yaml`, slugs de `.spec/units/<NNNN-…>/`, nombres de skills/agents.
2. **Una verificación automática** que falla si un archivo bajo `scripts/`, `installer/`, `.spec/scripts/`, `.agents/`, `.claude/`, `.spec/units/` viola la regla, leyendo la convención declarada en CA-01 (no hardcoded). Sin esta verificación el spec no protege contra regresión.
3. **Las 7 plantillas que este spec puede tocar** (`_estado.yaml`, `spec.md`, `plan.md`, `tasks.md`, `research.md`, `bitacora.md`, `paquete-aprobacion.md`) **no contienen bloques de prosa ≥ 5 líneas duplicados** entre 2+ plantillas. Cada bloque vive en una única fuente y se referencia desde las demás.
4. **`_estado.yaml` plantilla ≤ 100 líneas**, con bloques de comentarios inline ≥ 3 líneas consecutivas movidos a `.spec/README.md` u otra fuente canónica decidida por el plan. Sin esto, el `sdd-gate` paga ventana por prosa que ya leyó del README.

## Alcance

**Incluye:**
- Declarar la convención de nomenclatura en una sección canónica (ancla `# nomenclatura:` o la que decida el plan) — preferentemente `.spec/README.md`.
- Añadir un verificador determinista en `.spec/scripts/tests/` que falle ante violación; **uno nuevo**, sin tocar `test_scripts_naming_and_strict_mode.py` (cubre solo los 8 nombres de CA-10 de la unidad 0114, alcance distinto).
- Consolidar la prosa duplicada en las **7 plantillas que este spec puede tocar**. La consolidación puede implicar **mover texto** a `.spec/README.md` (existente) o **eliminar líneas redundantes** referenciando desde la fuente canónica.
- Reducir la ventana de contexto que paga el gate al leer plantillas (CA-08, CA-09, CA-10, CA-11).

**No incluye (fuera de alcance):**
- Tocar `.spec/_plantillas/mandato.md` más allá del scope de U-0009 — la consolidación que esa plantilla requiere la decide U-0009 (CA-04); el resto del scope de U-0005 cubre las 6 plantillas restantes.
- Tocar `installer/cli.py`, `installer/installer.py` ni el motor del instalador — U-0006.
- Tocar `.mcp.json` ni `opencode.jsonc` — U-0007.
- Modificar `.spec/perfiles.yaml` — U-0008.
- Renombrar archivos existentes del kit (e.g. `scripts/install_pre_push_hook.sh` → `install-pre-push-hook.sh`). El verificador los nombra; el plan **decide caso por caso** si renombrar o documentar como excepción. Las decisiones de rename viajan a `plan.md` (no se toman aquí).
- Crear archivos nuevos — prohibido por la restricción operativa de esta unidad.
- Resolver la cita colgante a `.spec/MODELO-AGENTES.md` (hallazgo, no CA).

## Criterios de aceptación

Cada `CA-NN` se verifica con un comando o `ruta:línea` reproducible. Convención: cada CA tiene al menos un test en `.spec/scripts/tests/` cuyo nombre contiene `test_u0005_ca<NN>`; `python3 -m pytest .spec/scripts/tests -k u0005_ca<NN> -q` lo selecciona y lo pasa.

**Nomenclatura**

- [ ] CA-01 — Una sección canónica en `.spec/README.md` (u otra fuente única que decida el plan) declara la convención por extensión de archivo (`.py` → snake_case; `.sh` → kebab-case; `.md` → kebab-case o single-word lowercase; `.yaml` → snake_case; slug de `.spec/units/<NNNN-…>/` → `NNNN-kebab-case-slug`). El verificador de CA-02 lee la convención de ese único archivo (no la hardcodea), vía anchor `# nomenclatura:`. Verificación: `grep -n '^# nomenclatura' .spec/README.md` devuelve exactamente una línea.
- [ ] CA-02 — Un test `test_u0005_ca02.py` corre un verificador sobre `scripts/`, `installer/`, `.spec/scripts/`, `.agents/`, `.claude/`, `.spec/units/` y falla si encuentra un nombre que viola la convención declarada en CA-01. Verificación: el test importa la convención desde CA-01 y reporta los outliers por `ruta:línea`.
- [ ] CA-03 — El verificador lista los outliers en stdout con ruta y nombre del archivo, **sin abortar** (exit 0 con stdout no vacío). Verificación: el test ejecuta el verificador contra un fixture que contiene un outlier conocido y verifica que su nombre aparece literalmente en stdout.
- [ ] CA-04 — Para el outlier ya conocido hoy (`scripts/install_pre_push_hook.sh`, snake en directorio de scripts `.sh` mixtos), el verificador lo nombra **y** la sección canónica de CA-01 lo incluye en una tabla de "excepciones vigentes" con motivo y ruta. Verificación: el test verifica que la cadena `install_pre_push_hook.sh` aparece tanto en la salida del verificador como en la tabla de excepciones de la sección canónica.

**Unificación de artefactos SDD (plantillas)**

- [ ] CA-05 — Cada bloque de prosa de **≥ 5 líneas consecutivas** en `.spec/_plantillas/{spec,plan,tasks,research,bitacora,paquete-aprobacion}.md` aparece **a lo sumo en una** de las 6 plantillas de este spec. Para `_estado.yaml` (YAML, no prosa), "bloque" significa **líneas consecutivas de comentario con prefijo `#`** (alineado con la semántica de CA-09). El test excluye los bloques que son ejemplos de payload entre asteriscos y los markers de plantilla (`<...>`, `NNNN-slug`). Verificación: `test_u0005_ca05.py` corre un diff normalizado de cada bloque ≥ 5 líneas contra todas las demás plantillas (y, para `_estado.yaml`, contra los bloques de comentario `#` consecutivos ≥ 5) y falla si hay coincidencia.
- [ ] CA-06 — Las secciones que **sí** aparecen en 2+ plantillas por necesidad de re-declaración (e.g. "Cómo se cierra un ciclo SDD") se referencian desde la fuente única mediante un anchor reproducible (e.g. `Ver § de .spec/README.md`). El plan decide el anchor literal; el test verifica que el anchor aparece en las plantillas que re-declaran.
- [ ] CA-07 — Tras la consolidación, `ls .spec/_plantillas/ | wc -l` es **igual** a 11 (no se introducen ni se retiran plantillas: las eliminaciones son prerrequisito de U-0009). Verificación: `git status --short .spec/_plantillas/` muestra solo archivos en la lista permitida (los 7 que este spec toca + los 4 de U-0009 intactos).

**Reducción de ventana de contexto**

- [ ] CA-08 — `wc -l .spec/_plantillas/_estado.yaml | awk '{print $1}'` devuelve un valor **≤ 100** (hoy 185). Verificación: el comando directo.
- [ ] CA-09 — Cada bloque de comentario inline en `_estado.yaml` con **≥ 3 líneas consecutivas** se reemplaza por una referencia a `.spec/README.md` (u otra fuente decidida por el plan) **o** por una sola línea explicativa. Verificación: el test parsea `_estado.yaml` y reporta bloques `#` consecutivos ≥ 3 líneas; falla si los encuentra sin su anchor de redirección.
- [ ] CA-10 — El bloque entre `> Si el cambio no admite paralelismo real` y el siguiente `##` en `.spec/_plantillas/plan.md` (hoy 14 líneas, líneas 36-52) tiene ≤ 5 líneas no-vacías tras el cambio; el resto se referencia desde `.spec/README.md`. Verificación: el test extrae ese bloque y verifica el conteo.
- [ ] CA-11 — Las dos convenciones de resolución de conflictos concurrentes (`bitacora.md:13` "Convención de merge (unidad 0098)" para entries de bitácora; `_estado.yaml:5-11` regla 0083/0084 para campos de `_estado.yaml`) **se referencian desde una sección canónica de `.spec/README.md`** (la sección de "Concurrencia" o la que decida el plan). Tras el cambio, `bitacora.md` no contiene la frase literal `"Convención de merge (unidad 0098)"` y `_estado.yaml` no contiene el bloque de comentario inline `0083/0084`; ambas plantillas apuntan a la sección canónica con un anchor reproducible. Verificación: `grep -F 'Convención de merge (unidad 0098)' .spec/_plantillas/bitacora.md` devuelve 0 hits Y `grep -F '0083/0084' .spec/_plantillas/_estado.yaml` devuelve 0 hits.

**Disciplina y cobertura**

- [x] CA-12 — `mandato.md` queda como única plantilla de mandato tras U-0009 (la lista de "4 plantillas excluidas" original se reduce a 1: las 3 plantillas retiradas por U-0009 ya no existen; `mandato.md` la modifica U-0009 mismo bajo CA-04). Verificación: el test `test_u0005_ca12.py` verifica (a) `EXCLUDED_PATHS = [".spec/_plantillas/mandato.md"]`, (b) `mandato.md` existe, (c) `mandato.md` referencia `.spec/SUPERVISADO.md` (anchor canónico de U-0009 CA-04).
- [ ] CA-13 — Cada CA-NN tiene su verificador definido de forma trazable. **CA-01 no requiere test propio** — el `grep -n '^# nomenclatura' .spec/README.md` declarado en su verificación es el verificador; el spec documenta el comando y el resultado esperado (`1 línea`). **CA-02 a CA-12** tienen al menos un test en `.spec/scripts/tests/` con `test_u0005_ca<NN>` en el nombre. Verificación: `python3 -m pytest .spec/scripts/tests -q --collect-only -k u0005` lista al menos 11 tests (uno por CA-NN de CA-02 a CA-12); CA-13 mismo cuenta como el verificador de sí mismo (`test_u0005_ca13.py` itera sobre la lista de CAs y verifica que cada uno tiene su verificador).
- [ ] CA-14 — `python3 -m pytest .spec/scripts/tests installer -q` (comando de validación de la unidad) termina con código 0 tras cerrar la implementación; ningún test preexistente pierde aserciones fuera de las enumeradas en `plan.md`. Verificación: el comando del paquete de aprobación.

> **Nota sobre precedencia:** la plantilla `mandato.md` (modificada por U-0009, CA-04) está excluida de CA-05/CA-08/CA-09 por construcción — los verificados solo cuentan las 7 plantillas que este spec puede tocar. El verificador de CA-12 (`test_u0005_ca12.py`) verifica el contrato post-U-0009: `mandato.md` existe, contiene el anchor canónico a `.spec/SUPERVISADO.md` § 1.1, y la lista `EXCLUDED_PATHS` se reduce a esa única entrada.

## Governance aplicable

Sin superficie de gobernanza propia del kit — `pce-mcp` no se ejecuta contra este repo (mismo precedente que `0001-…/spec.md:78-97`, `0002-…/spec.md:558-594` y `0004-…/spec.md:97-114`). `governance_refs: [ninguna-aplicable]` ya poblado en `_estado.yaml`.

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Superficie de gobernanza **producida** por el kit (AGENTS.md, `.mcp.json`, `opencode.jsonc`) pero `pce-mcp` no se evalúa localmente; ver justificación arriba |

## Preguntas abiertas

- **¿La sección canónica de nomenclatura vive en `.spec/README.md` o se crea `.spec/NOMENCLATURA.md`?** Restricción operativa de la unidad prohíbe crear archivos nuevos. **Suposición conservadora:** se añade en `.spec/README.md` § Nomenclatura, anchor `# nomenclatura:`. El plan puede proponer otra ubicación si lo justifica.
- **¿Se renombran los outliers conocidos (`scripts/install_pre_push_hook.sh`, `scripts/mcp-pce.py`) o se documentan como excepciones?** **Suposición conservadora:** se documentan como excepciones en la tabla de CA-01; el rename queda como decisión futura, fuera de esta unidad por riesgo de romper el contrato del instalador (`scripts/mcp-pce.sh` es invocado por `.mcp.json` del destino — un rename exige actualizar el manifiesto del kit, prerrequisito de U-0007).
- **¿Se mueve la prosa de CA-09 a `.spec/README.md` o a `.spec/SUPERVISADO.md`?** **Suposición conservadora:** `.spec/README.md` por ser el doc canónico del protocolo; `.spec/SUPERVISADO.md` se reserva para temas del modo supervisado que excedan el README.

> En **modo semi-autonomo** las preguntas abiertas se resuelven con la opción más conservadora y se listan en el paquete de aprobación para que el humano las confirme o corrija.