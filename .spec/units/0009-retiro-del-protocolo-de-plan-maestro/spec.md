# Spec — Retiro del protocolo de plan maestro, consolidación en `mandato.md` y migración de U-0002/U-0003

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso vive en `plan.md`).

## Problema / Motivación

El protocolo SDD mantiene **dos formas de amparar unidades bajo modo supervisado o desatendido** que hoy son redundantes:

- `.spec/_plantillas/plan-maestro.md` (242 líneas) — plan multi-unidad con `## Unidades miembro`, `## Cadena de dependencias`, `## Registro de revisiones` y un `mandato.md` interno.
- `.spec/_plantillas/mandato.md` (243 líneas) — mandato de **una sola unidad** (D-15, D-16), con el mismo resto de anclas.

Las dos unidades activas que hoy usan plan maestro (U-0002 y U-0003) lo invocan con **planes de una sola unidad** — la diferencia práctica con `mandato.md` es nula. Las 3 secciones propias del plan multi-unidad (`## Unidades miembro`, `## Cadena de dependencias`, `## Registro de revisiones`) **nunca se usan** en planes activos. Resultado: dos plantillas que divergen sin valor, dos sitios a mantener, y una categoría de archivo (`.spec/planes/<id>/plan.md`) que duplica `.spec/units/<id>/mandato.md`.

Adicionalmente, U-0002 y U-0003 declaran dependencias entre unidades en un bloque `dependencias:` informal (no presente en la plantilla `_estado.yaml` canónica), mientras `mandato:` apunta al id de un plan maestro que tras este retiro deja de existir. Si no se hace el **drift-check** explícito sobre esos dos `_estado.yaml`, el campo `mandato:` queda apuntando a un plan borrado (referencia muerta) y la dependencia entre unidades sigue viviendo en un bloque que la herramienta de gates no entiende como formal.

Si no se hace: el protocolo acumula dos plantillas divergentes, dos sitios de prosa normativa sobre el mismo concepto, las dependencias siguen informales, y cualquier unidad nueva que reabra un plan maestro de una sola unidad repite trabajo.

## Resultado esperado

1. Solo existe una plantilla de mandato: `.spec/_plantillas/mandato.md`. Las plantillas `plan-maestro.md`, `historial.md` y `paquete-tanda-aprobacion.md` están borradas.
2. `.spec/_plantillas/_estado.yaml` declara el nuevo campo `depende_de: []` (lista de IDs de unidades, default `[]`); el comentario del campo `mandato:` ya no menciona `plan maestro (.spec/planes/<id>/)`.
3. U-0002 y U-0003 tienen `_estado.yaml` migrado: `mandato:` vacío y `depende_de:` con la lista de unidades de las que dependen; los planes activos están archivados o migrados.
4. Ningún consumidor vivo del protocolo (scripts, skills, docs, tests, manifiesto del kit) menciona `plan-maestro` como artefacto activo.
5. El test `test_u0005_ca12.py` y la prosa asociada en U-0005 (`spec.md:69`, `tasks.md:16,27,37`) ya no referencian las 3 plantillas borradas — la suite verde no se rompe por este retiro.

## Alcance

**Incluye:**

- Borrar `.spec/_plantillas/plan-maestro.md`, `.spec/_plantillas/historial.md`, `.spec/_plantillas/paquete-tanda-aprobacion.md`.
- Actualizar `.spec/_plantillas/mandato.md`: retirar referencias cruzadas a `plan-maestro.md` en cabecera y notas inline; si una sección solo aplica a multi-unidad y no se usa en mandato aislado, retirarla (decisión de redacción en `plan.md`).
- Modificar `.spec/_plantillas/_estado.yaml`: añadir `depende_de: []` (lista, default vacío) y reescribir el comentario del campo `mandato:` para que solo refiera `mandato.md` dentro de la propia unidad.
- Migrar `.spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml`: vaciar `mandato:`; mover el bloque `dependencias:` (líneas 42-46) al nuevo campo `depende_de:`.
- Migrar `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml`: misma operación sobre las líneas 68-74.
- Archivar o migrar `.spec/planes/0002-versionado-y-actualizacion-del-instalador/plan.md` y `.spec/planes/0003-fiabilidad-de-gates-de-gobernanza/plan.md` (decisión en `plan.md`).
- Eliminar las referencias a `plan-maestro` en ~15 archivos de `.spec/scripts/` (`_common.py`, `append_changelog_line.py`, `guard_bash_spec_writes.py`, `guard_written_state_shape.py`, `instance_lock.py`, `mandate_anchor.py`, `preflight.py`, `record_mandate_validation.py`, `sdd_safe_write.py`, `supervised_parallel.py`, `supervised-test.sh`, `validate_mandate.py`, `validate-supervised.sh` y sus tests).
- Eliminar las referencias en las 3 skills SDD: `.agents/skills/sdd-desatendido/SKILL.md`, `.agents/skills/sdd-preflight/SKILL.md`, `.agents/skills/sdd-supervisado/SKILL.md` (los espejos `.claude/skills/*` se regeneran con el materializador; no son carga).
- Eliminar las referencias en `.spec/SUPERVISADO.md` (líneas 22, 113), `.spec/PARADAS-SUPERVISADO.md` (línea 4) y `AGENTS.md` (línea 149).
- Actualizar `test_u0005_ca12.py` (EXCLUDED_PATHS a solo `mandato.md`), `spec.md:69` y `tasks.md:16,27,37` de U-0005 — el acoplamiento con U-0005 ya cerrada es parte explícita del alcance porque de lo contrario la suite queda rota.

**No incluye (fuera de alcance):**

- Cambios al motor de validación `validate_mandate.py` que no sean consecuencia directa de borrar las plantillas referenciadas.
- Retiro o redefinición del modo `desatendido` — sigue operando con `mandato.md` para una sola unidad (semántica `auto-deferred` se conserva); este spec no toca la maquinaria de modos.
- Reescritura del cliente CLI del instalador (`installer/cli.py`) ni del antidrift de pre-push — solo se elimina la entrada de `.spec/_plantillas/{plan-maestro,historial,paquete-tanda-aprobacion}.md` que ya no existe, sin tocar el manifest.
- Migración de U-0001, U-0004, U-0005 (sus `mandato:` están vacíos y `fase: done`; no hay drift que migrar en sus `_estado.yaml` salvo los comentarios inline históricos que mencionan `_plan-maestro.md` — no se tocan).
- Reescritura de U-0002/U-0003/U-0004 `_estado.yaml` para limpiar los comentarios inline que mencionan `_plan-maestro.md § Changelog` y `_plan-maestro.md § Modelo de ejecución multi-agente` — son históricos, no son drift activo, se difieren a una unidad de limpieza posterior.
- Reescritura de U-0002 `spec.md:599` y `plan.md:95` — referencias históricas a `.spec/_plantillas/plan-maestro.md:56,117` en una unidad cerrada; no son drift activo.

## Criterios de aceptación

Cada CA es **verificable sin interpretar** — comando único reproducible. Los CA-07..CA-10 son el **drift-check explícito** sobre los `_estado.yaml` activos de U-0002 y U-0003 que `mandato:` apuntaba a planes hoy vigentes.

- [ ] **CA-01** — Plantilla `plan-maestro.md` borrada: `test ! -f .spec/_plantillas/plan-maestro.md`.
- [ ] **CA-02** — Plantilla `historial.md` borrada: `test ! -f .spec/_plantillas/historial.md`.
- [ ] **CA-03** — Plantilla `paquete-tanda-aprobacion.md` borrada: `test ! -f .spec/_plantillas/paquete-tanda-aprobacion.md`.
- [ ] **CA-04** — `mandato.md` consolidado (sin referencias a `plan-maestro`): `grep -c 'plan-maestro' .spec/_plantillas/mandato.md` retorna 0.
- [ ] **CA-05** — `_estado.yaml` declara `depende_de: []`: `grep -n '^depende_de:' .spec/_plantillas/_estado.yaml` retorna exactamente 1 línea y el valor es `[]`.
- [ ] **CA-06** — Comentario del campo `mandato:` ya no menciona plan maestro: `awk '/^mandato:/{flag=1; c=0} flag {print; c++; if (c>=4) exit}' .spec/_plantillas/_estado.yaml | grep -cE 'plan maestro|plan-maestro'` retorna 0.
- [ ] **CA-07 (drift-check U-0002 — `mandato:` vacío)** — `awk '/^mandato:/{print; exit}' .spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml` retorna `mandato: ""` (literalmente vacío entre comillas).
- [ ] **CA-08 (drift-check U-0002 — `depende_de:` con U-0003)** — `awk '/^depende_de:/{flag=1; next} flag && /^[^ ]/ && !/depende_de/ {flag=0} flag {print}' .spec/units/0002-versionado-y-actualizacion-del-instalador/_estado.yaml | grep -q '0003-fiabilidad-de-gates-de-gob*ernanza'` retorna exit 0 (nota: grafía `gobernanza`, con `b` y no `v`).
- [ ] **CA-09 (drift-check U-0003 — `mandato:` vacío)** — `awk '/^mandato:/{print; exit}' .spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml` retorna `mandato: ""`.
- [ ] **CA-10 (drift-check U-0003 — `depende_de:` con U-0002)** — `awk '/^depende_de:/{flag=1; next} flag && /^[^ ]/ && !/depende_de/ {flag=0} flag {print}' .spec/units/0003-fiabilidad-de-gates-de-gobernanza/_estado.yaml | grep -q '0002-versionado-y-actualizacion-del-instalador'` retorna exit 0.
- [ ] **CA-11** — Planes activos U-0002 y U-0003 archivados o migrados: `test ! -f .spec/planes/0002-versionado-y-actualizacion-del-instalador/plan.md && test ! -f .spec/planes/0003-fiabilidad-de-gates-de-gobernanza/plan.md`.
- [ ] **CA-12a (acoplamiento U-0005 — test)** — `test_u0005_ca12.py` no referencia las 3 plantillas borradas: `grep -cE 'plan-maestro|historial|paquete-tanda' .spec/scripts/tests/test_u0005_ca12.py` retorna 0.
- [ ] **CA-12b (acoplamiento U-0005 — prosa reformulada)** — La lista bajo chequeo de byte-identidad en `U-0005/spec.md:69` y `tasks.md:16,27,37` reformula a `mandato.md` como única excepción vigente: `grep -F 'mandato.md' .spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/spec.md | grep -cE 'plan-maestro|historial|paquete-tanda'` retorna 0; `grep -F 'mandato.md' .spec/units/0005-estandarizacion-de-nomenclatura-y-artefactos-sdd/tasks.md | grep -cE 'plan-maestro|historial|paquete-tanda'` retorna 0.
- [ ] **CA-13** — Scripts del protocolo sin referencias a `plan-maestro` (excluyendo `__pycache__/`): `grep -rl 'plan-maestro' .spec/scripts/ --exclude-dir=__pycache__` retorna vacío.
- [ ] **CA-14** — 3 skills SDD sin menciones a `plan-maestro`: para `s` en `sdd-desatendido sdd-preflight sdd-supervisado`, `grep -q 'plan-maestro' ".agents/skills/$s/SKILL.md" && exit 1`; `exit 0` final.
- [ ] **CA-15** — 3 docs canónicos sin menciones a `plan-maestro`: `grep -c 'plan-maestro' .spec/SUPERVISADO.md .spec/PARADAS-SUPERVISADO.md AGENTS.md` retorna 0 en cada archivo.
- [ ] **CA-16** — Suite de tests verde tras el retiro: `python3 -m pytest .spec/scripts/tests installer -q` retorna exit 0.

## Governance aplicable

Este repo **no tiene superficie de gobernanza propia** — `pce-mcp` no se ejecuta contra el kit (precedente U-0004 y U-0005 ya cerrado en `fase: done`, ambos con `governance_refs: [ninguna-aplicable]` confirmado por el crítico L4 con evidencia). El `_estado.yaml > governance_refs: [ninguna-aplicable]` se mantiene.

## Preguntas abiertas

- **PQ-1** — ¿Qué se hace con el contenido de `.spec/planes/0002-*/plan.md` y `0003-*/plan.md`? Tres opciones: (a) archivar bajo un directorio `.spec/planes-archive/`; (b) migrar el contenido relevante a un `mandato.md` dentro de la propia unidad; (c) descartar (U-0002 y U-0003 ya están cerradas en `fase: done`). La opción más conservadora es (a).
- **PQ-2** — ¿El campo `depende_de:` se representa como inline (`depende_de: ["0003-..."]`) o block (`depende_de:\n  - 0003-...`)? La forma concreta se fija en `plan.md` — el spec solo exige que la lista contenga los IDs correctos (CA-08/CA-10).
- **PQ-3** — ¿`modo: desatendido` para una sola unidad queda como alias de `modo: supervisado` (con `mandato.md` propio), o se conserva como modo distinto con semántica `auto-deferred`? Esta unidad **no redefine modos**; el spec lo deja intacto. Si el operador decide fusionarlos, queda como unidad posterior.