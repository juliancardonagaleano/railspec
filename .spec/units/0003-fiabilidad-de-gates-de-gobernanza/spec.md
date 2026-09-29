# Spec — Fiabilidad del flujo de validación de superficie de gobernanza en los gates

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

El gate de spec de la unidad `0002-versionado-y-actualizacion-del-instalador` aprobó
`governance_refs: [ninguna-aplicable]` cuando el repo **ya tenía** un archivo
(`opencode.jsonc`) que declaraba `pce-mcp` como server MCP de Opencode. Esa aprobación
se propagó a los gates de plan y tasks sin re-verificar, porque la "triada" de
`sdd-gate § 3` es estrecha (busca `AGENTS.md` y `.mcp.json` por nombre literal) y
porque `0117-D6` quita el panel (y por tanto el lente de gobernanza) de plan y tasks.

Análisis del incidente (`bitacora.md` de la unidad 0002, entrada del 2026-09-29T05:30:00Z)
identifica **siete bugs en skills/agents y tres en el orquestador**, agrupados en
cuatro categorías:

1. **Triada estrecha** — `sdd-gate/SKILL.md:126` busca `AGENTS.md` y `.mcp.json` por
   nombre. Configuraciones con otros nombres son invisibles. El "conjunto vacío
   verificado" del skill es verificado solo respecto a esos tres nombres, no
   exhaustivamente.
2. **Reuso de evidencia entre fases** — `governance_consultada: si` se persiste en
   `_estado.yaml > gates.spec` y los gates de plan y tasks lo heredan sin
   re-validar. Un cambio de filesystem entre spec y plan es invisible para los
   gates siguientes.
3. **Aislamiento de fase** — `0117-D6` quita el panel de plan y tasks; el lente L4
   (gobernanza) solo corre en spec. Combinado con (2), significa que el chequeo de
   gobernanza ocurre exactamente una vez por unidad.
4. **`subagent_type` no registrado + fallback silencioso** — `effort_profile.py`
   devuelve `sdd-explorador` (etc.) como `subagent_type`, pero esos nombres no
   están registrados en opencode. El skill tiene regla de fallback para críticos
   (`.agents/skills/sdd-gate/SKILL.md:189-195`) pero no para exploradores/redactores.
   Cuando el orquestador cae a `general`, las instrucciones del rol no se
   inyectan.

Si no se hace: cada unidad futura hereda el mismo riesgo. La próxima vez que un
archivo no anticipado declare `pce-mcp` (o cualquier otro artefacto de gobernanza),
el gate lo aprobará silenciosamente. El antidrift del protocolo se rompe por el
mismo protocolo que lo define.

## Resultado esperado

El gate de cada fase del SDD (spec, plan, tasks, código) valida de forma
**confiable** la superficie de gobernanza del kit, contra las tres fuentes posibles:

- El **spec** de la unidad (lista declarativa de archivos raíz que el kit gobierna).
- El **filesystem** actual del repo (verificación de presencia y ausencia).
- El **catálogo de gobernanza** vía MCP `pce-mcp` (cuando esté conectado).

Tres propiedades concretas:

1. **Triada declarativa.** El spec de cada unidad declara un
   `configuration_contract:` (lista de rutas raíz que el kit gobierna, p.ej.
   `.claude/settings.json`, `.mcp.json`, `opencode.jsonc`, `AGENTS.md`). El gate
   compara esa lista contra el filesystem: cada entrada declarada debe existir;
   ningún archivo raíz con declarador de MCP/gob puede existir fuera de la lista
   sin escalar. La triada literal por nombre de archivo deja de ser la fuente de
   verdad: es red de seguridad, no contrato.
2. **Re-validación entre fases.** Cada gate (no solo spec) ejecuta un chequeo de
   superficie contra el filesystem actual y contra un hash del filesystem al
   momento de cerrar la fase anterior. Si el hash cambió, el gate re-ejecuta la
   triada completa antes de aprobar. La "evidencia cacheada" deja de ser
   mecanismo de aprobación: es referencia, no sustituto.
3. **Fallback explícito para `subagent_type`.** El skill tiene una regla análoga
   a la de críticos (líneas 189-195) para exploradores y redactores: si el rol
   resuelto por `effort_profile.py` no existe en el entorno, el orquestador (a)
   anota en `bitacora.md` qué rol se degradó, (b) inyecta las instrucciones del
   rol desde el archivo `.agents/agents/<rol>.md` en el prompt, y (c) persiste
   `subagente: <general|general-purpose>` en `modelo_ejecucion.<fase>`. La
   degradación deja de ser silenciosa.

Adicionalmente, dos propiedades de soporte:

4. **Segunda red por contenido.** El chequeo de superficie hace, además de la
   lista declarativa y la presencia por nombre, una búsqueda por contenido:
   `grep -rln "pce-mcp\|mcpServers\|\\bservers\\b" .` excluyendo `.git/` y `.spec/`.
   Si encuentra un archivo que declare `pce-mcp` o `mcpServers` sin estar en la
   lista declarativa del spec, escala como `superficie-no-declarada`.
5. **Vigencia del centinela.** El campo `governance_refs` en `_estado.yaml`
   incluye `verificado_en: <ISO-8601>` y `fs_hash: <sha256>`. El gate falla si
   `verificado_en` tiene más de 24 horas (en modo interactivo) o si `fs_hash`
   cambió desde la última verificación. El centinela `[ninguna-aplicable]` no se
   "congela" por error.

Las dos últimas son redes de seguridad para los tres fallos principales: si
(1) se equivoca al listar, (4) lo detecta; si (2) reusa evidencia obsoleta, (5)
lo detecta por hash.

## Alcance

**Incluye** (los 8 fixes priorizados, en orden de dependencia):

1. **Triada declarativa.** Modificar `sdd-gate/SKILL.md` para que el chequeo de
   superficie lea un `configuration_contract:` del spec de la unidad cuando
   exista, o caiga a la triada literal como red de seguridad. El contrato se
   valida: cada ruta declarada debe existir en el filesystem; ningún archivo
   raíz con declarador MCP/gob puede existir fuera de la lista sin escalar.

2. **Re-validación entre fases.** Añadir a la capa determinista de plan y tasks
   (`deterministic-layer.md`) un chequeo: comparar el hash del filesystem actual
   contra el hash persistido al cerrar la fase anterior. Si difiere → escalar
   con causa `superficie-cambiada` y forzar re-ejecución de la triada. Añadir al
   skill `sdd-gate/SKILL.md` una regla explícita de persistencia de hash.

3. **Segunda red por contenido.** Modificar el chequeo de superficie para que,
   tras pasar la lista declarativa y la presencia por nombre, ejecute un
   `grep -rln "pce-mcp\|mcpServers" .` excluyendo `.git/` y `.spec/`. Si
   encuentra un archivo con declarador MCP/gob fuera de la lista, escala como
   `superficie-no-declarada`.

4. **Fallback explícito para `subagent_type`.** Añadir al skill
   `sdd-gate/SKILL.md` (o a un nuevo archivo `references/subagent-fallback.md`)
   una regla análoga a la de críticos (líneas 189-195) para exploradores y
   redactores. La regla exige: (a) anotar en `bitacora.md` qué rol se degradó,
   (b) inyectar las instrucciones del rol desde el archivo `.agents/agents/<rol>.md`
   en el prompt, (c) persistir `subagente: general|general-purpose` en
   `modelo_ejecucion.<fase>`. El skill `sdd-orquestar/SKILL.md` también debe
   referenciar esta regla cuando dispatcha exploradores/redactores.

5. **L4 en plan y tasks.** Modificar la tabla "Qué rol lanzar por lente" en
   `sdd-gate/SKILL.md:181-188` para que plan y tasks ejecuten el lente L4
   reducido (re-validar triada + lista declarativa + segunda red, no
   re-listar aplicables). La rúbrica de cada fase (`rubrica-plan.md`,
   `rubrica-tasks.md`) necesita actualizarse para reflejar que L4 está activo.

6. **Vigencia del centinela.** Modificar el schema de `governance_refs` en
   `_estado.yaml` (no formalizado en script; propagación por convención via
   `sdd_retomar.py` y `effort_profile.py`). Añadir `verificado_en: <ISO-8601>` y
   `fs_hash: <sha256>` por gate. El gate falla si `verificado_en` tiene más
   de 24 horas (interactivo) o si `fs_hash` cambió. La triada puede convivir
   con la lista declarativa: el hash es de filesystem, no de la lista.

7. **Auditor de superficie como script separado.** Crear
   `.spec/scripts/check_governance_surface.py` que encapsula: lista declarativa
   (leer spec), segunda red por contenido, comparador de filesystem, hash de
   filesystem. La sección del skill que hoy hace el chequeo se reemplaza por
   una llamada a este script. Esto aísla la lógica y la hace testeable.

8. **Reglas de cross-check del orquestador.** Añadir a `sdd-orquestar/SKILL.md`
   una sección "Cross-check de superficie" que diga: antes de confiar en el
   resultado de un explorador de gobernanza, el orquestador debe ejecutar al
   menos un sanity probe (`ls -la`, `grep`, `find`) sobre el filesystem actual.
   Si el explorador dice "no surface" pero el filesystem tiene archivos que
   podrían declararla, el orquestador NO persiste el resultado del explorador
   hasta haber hecho su propio probe. Esto convierte la falla del 2026-09-29
   (Orch-Bug 1-3 de mi análisis) en regla explícita del orquestador.

**No incluye:**

- Cambios al motor de validación adversarial (`sdd-refutador`); el fix 5 añade
  L4 a plan y tasks pero no cambia la verificación adversarial.
- Reescritura del schema de `_estado.yaml` (los campos `verificado_en` y
  `fs_hash` se añaden como comentarios documentados, no como campos formales;
  la formalización queda para una unit posterior si se vuelve necesario).
- **Re-correr el gate del spec de la unit 0002 como cierre del scope delta
  de 0002.** Eso lo hace el operador (o una unit posterior: una vez 0003
  llegue a done, el orquestador re-corre el gate de spec de 0002 contra el
  script nuevo y, si aprueba, 0002 puede cerrar). Esta unit **no cierra
  0002**; solo arregla el motor para que la re-corrida posterior sea
  confiable. El CA-21 (smoke test de este script contra una unidad de
  control) **sí** corre dentro de esta unit y se cuenta como cierre de
  su propio alcance, no como cierre del scope delta de 0002.
- Migración retroactiva de unidades ya cerradas (`0001`, etc.); la regla
  aplica a units nuevas y a units reabiertas.
- Re-correr los gates de 0002 después del scope delta (eso lo hace el
  operador o una unit posterior; esta unit solo arregla el motor).

### Decisiones pendientes para `plan.md`, con su criterio

| Decisión | Criterio que la elegida debe satisfacer |
|---|---|
| Forma del `configuration_contract:` en spec.md | Una sola lista declarada por unidad; ningún archivo se enumera dos veces; legible por un test sin parsear prosa libre |
| Algoritmo de hash de filesystem | Determinista (mismo hash para mismo filesystem); barato (no recorre binarios); rápido (subsegundo para un repo típico) |
| Umbral de vigencia del centinela (24h en interactivo) | Configurable por perfil; el default debe ser tal que una sesión de trabajo no expire el centinela, pero una sesión al día siguiente sí |
| Cuándo el fallback de subagent_type aplica | Cualquier rol `sdd-*` que `effort_profile.py` resuelva pero opencode no reconozca; no solo exploradores y redactores, también críticos si fuera necesario |
| Política de error cuando el script `check_governance_surface.py` falla | `escalado` con causa `auditor-fallo` (no se avanza el gate); nunca silencio |

## Configuration contract

El kit (este repo) gobierna las siguientes rutas raíz de configuración. El
script `check_governance_surface.py` las usa como lista declarativa
(CA-01..CA-03). El kit **no** gobierna otras rutas — si el script
encuentra archivos que declaren MCP o gobernanza fuera de esta lista, los
marca como `superficie-no-declarada`.

```
configuration_contract: [".mcp.json", "opencode.jsonc", "AGENTS.md", "scripts/mcp-pce.sh"]
```

Tres de las cuatro entradas son **producidas** por el kit y entregadas al
destino como carga con fusión con marcador (`.mcp.json`, `opencode.jsonc`,
`AGENTS.md` — ver unit 0002 CA-51, CA-52, CA-53). La cuarta
(`scripts/mcp-pce.sh`) es el launcher que esos configs invocan (CA-54).
Esta lista es la que el auditor usa como fuente de verdad para detectar
superficie no declarada.

## Criterios de aceptación

Cada criterio se decide sí/no mirando solo el repo. **Convención de
verificación:** cada CA tiene al menos un test (en `.spec/scripts/tests/`,
`installer/tests/`, o un test ad-hoc del script nuevo) cuyo nombre contiene
`u0003_ca<NN>`. La rúbrica de `fase: spec` exige trazabilidad por CA.

**Triada declarativa (Fix 1):**

- [ ] CA-01 — Un test fija que, dado un spec de unit con
      `configuration_contract: [.mcp.json, opencode.jsonc, AGENTS.md]`, el
      script `check_governance_surface.py` lo lee y lo compara contra el
      filesystem: cada ruta existe.
- [ ] CA-02 — Un test fija que el script rechaza (exit != 0, mensaje claro)
      cuando una ruta del `configuration_contract:` no existe en el filesystem.
- [ ] CA-03 — Un test fija que el script rechaza cuando existe en la raíz un
      archivo que declara `mcpServers` o `mcp` (en JSON/JSONC) sin estar en el
      `configuration_contract:`. Equivalente: la segunda red detecta la
      superficie no declarada.

**Re-validación entre fases (Fix 2):**

- [ ] CA-04 — Un test fija que el chequeo determinista de plan (y tasks)
      compara `fs_hash` actual contra el hash persistido al cerrar spec. Si
      difieren, el gate escala con causa `superficie-cambiada` y `veredicto:
      escalado` (no `aprobado`).
- [ ] CA-05 — Un test fija que, si `fs_hash` no cambió, el chequeo pasa sin
      invocar la triada completa (atajo). El comportamiento es opt-in: el
      operador puede pedir re-verificación explícita.

**Segunda red por contenido (Fix 3):**

- [ ] CA-06 — Un test fija que `grep -rln "pce-mcp\|mcpServers" .` ejecutando
      desde la raíz, excluyendo `.git/` y `.spec/`, devuelve vacío para un
      filesystem limpio (sin configs MCP no declarados).
- [ ] CA-07 — Un test fija que, dado un filesystem con un `opencode.jsonc` que
      declara `pce-mcp` (como en el incidente del 2026-09-29) y sin
      `configuration_contract:` que lo cubra, el script `check_governance_surface.py`
      escala con causa `superficie-no-declarada`.

**Fallback de subagent_type (Fix 4):**

- [ ] CA-08 — Un test estructural (`u0003_ca08_*`) fija que
      `references/subagent-fallback.md` (o `sdd-gate/SKILL.md`) declara una
      regla de fallback con la misma forma estructural que la regla de críticos
      en `sdd-gate/SKILL.md:189-195`: tres bloques distinguibles (qué rol se
      degradó, qué se inyecta, qué se persiste), cita los nombres de los roles
      `sdd-*` que la cubren, y el patrón se detecta por grep estructural
      (no por inspección libre de prosa).
- [ ] CA-09 — Un test fija que, dado un fallo de registro de
      `subagent_type`, `sdd_retomar.py` o el orquestador anota en bitácora
      qué rol se degradó y persiste `modelo_ejecucion.<fase>.subagente: general|general-purpose`.

**L4 en plan y tasks (Fix 5):**

- [ ] CA-10 — Un test fija que el gate de plan ejecuta el chequeo de
      superficie (no solo la lista declarativa; incluye segunda red + comparador
      de hash). Mismo para tasks.
- [ ] CA-11 — Un test fija que la rúbrica de plan (`rubrica-plan.md`) y la de
      tasks (`rubrica-tasks.md`) mencionan el lente L4 como activo.

**Vigencia del centinela (Fix 6):**

- [ ] CA-12 — Un test fija que `sdd_retomar.py` o el chequeo de superficie
      rechaza (exit != 0) si `verificado_en` tiene más de 24 horas.
- [ ] CA-13 — Un test fija que rechaza si `fs_hash` cambió desde la última
      verificación documentada.

**Auditor de superficie como script (Fix 7):**

- [ ] CA-14 — `python3 .spec/scripts/check_governance_surface.py --help`
      muestra el contrato del script (qué hace, qué falla, qué retorna).
- [ ] CA-15 — Un test fija que el script tiene una API estable y testeable:
      la lógica está en funciones puras (sin I/O fuera de su argumento), y
      se puede ejecutar contra un filesystem simulado (con `tmp_path`).
- [ ] CA-16a — Smoke test: ejecutar el gate del spec de una unidad de
      control (la unit 0001 cerrada, sin surface declarada) antes y después
      del reemplazo; comparar veredictos — deben ser idénticos
      (regression-safe).
- [ ] CA-16b — Test estructural: el paso 3 de `sdd-gate/SKILL.md` llama a
      `.spec/scripts/check_governance_surface.py` en lugar de la lógica inline
      (verificable por grep estructural sobre el skill).
      lógica vive en un lugar testeable.

**Cross-check del orquestador (Fix 8):**

- [ ] CA-17 — `sdd-orquestar/SKILL.md` contiene una sección "Cross-check de
      superficie" que dice, en prosa clara: antes de persistir el resultado
      de un explorador de gobernanza, el orquestador ejecuta al menos un
      sanity probe sobre el filesystem actual.
- [ ] CA-18a — Test estructural: la sección "Cross-check de superficie"
      existe en `sdd-orquestar/SKILL.md` y enumera al menos tres acciones
      (verificar existencia de archivos esperados, ejecutar `ls`/`grep`/
      `find` sobre el filesystem, comparar contra el resultado del
      explorador antes de persistir). Verificable por grep estructural.
- [ ] CA-18b — Test funcional: con un `tmp_path` que contiene
      `opencode.jsonc` declarando `pce-mcp` y un explorador simulado que
      devuelve "no surface", la regla del orquestador hace que el veredicto
      del gate sea `escalado` (no `aprobado`). Reproduce el incidente del
      2026-09-29 con filesystem real.

**Cierre:**

- [ ] CA-19 — El spec de esta unit cubre los 8 fixes (cada uno tiene al menos
      un CA en esta lista o un CA derivado directo). `python3 -m pytest
      .spec/scripts/tests installer -q --collect-only -k u0003` lista tests
      para cada CA.
- [ ] CA-20 — `python3 -m pytest .spec/scripts/tests installer -q` (el
      `comando_validacion`) termina con código 0. Ningún test preexistente
      se rompe por estos cambios.
- [ ] CA-21 — **Smoke test del script nuevo contra una unidad de control.**
      El script `.spec/scripts/check_governance_surface.py` se ejecuta
      contra el spec y el filesystem de la unit 0001 (cerrada, sin surface
      declarada) y contra una copia sintética con `opencode.jsonc`
      declarando `pce-mcp` (replica el incidente del 2026-09-29): en el
      primer caso retorna 0; en el segundo retorna != 0 con causa
      `superficie-no-declarada`. Esto cierra el alcance de esta unit
      respecto al motor: **no** cierra ni re-corre el gate del spec de la
      unit 0002 (eso queda para una unit posterior tras el cierre de 0003).

## Governance aplicable

Misma situación que la unidad 0002 post-delta: el kit **produce** la superficie
de gobernanza (`AGENTS.md`, `.mcp.json`, `opencode.jsonc`) pero `pce-mcp` no
se ejecuta contra el kit mismo. El centinela `governance_refs: [ninguna-aplicable]`
se mantiene con justificación reformulada.

Esta unidad **endurece** el chequeo de esa misma superficie; no la aplica al
kit. La salvedad del § Governance de 0002 sigue activa para esta unit: cuando
un destino con `pce-mcp` instale este kit, la prosa de gobernanza entregada
debe contrastarse con `pri-gob-precedencia-superficies` (punto ya registrado
en 0002).

## Preguntas abiertas

Solo el humano puede resolverlas; en modo interactivo quedan listadas para el
checkpoint. Con recomendación del redactor entre paréntesis, no como decisión.

- **P-1 — Forma del `configuration_contract:`**. ¿Lista plana de strings
  (`configuration_contract: [".mcp.json", "opencode.jsonc", "AGENTS.md"]`),
  objeto con tipo (`configuration_contract: {carga: [...], gobernanza: [...]}`),
  o tabla en prosa? (Recomendación: lista plana en `spec.md § Alcance` con
  comentario que diga "Estas rutas son gobernanza/configuración entregadas al
  destino. Su ausencia en el filesystem del kit es CA-02; su presencia fuera
  de la lista es CA-03.")

- **P-2 — Algoritmo de hash de filesystem**. ¿`find … | sort | xargs sha256sum`
  (barato, sensible a orden de listado), `git ls-files | sha256sum` (rápido,
  pero solo cubre archivos tracked), o `tar cf - . | sha256sum` (más caro pero
  exhaustivo)? (Recomendación: `find … -type f -not -path './.git/*' -not -path
  './.spec/*' -not -path './.gitnexus/*' -print0 | sort -z | xargs -0 sha256sum
  | sha256sum` — excluye directorios generados, es estable, y produce un único
  hash para comparar.)

- **P-3 — Umbral de 24 horas**. ¿Por sesión de trabajo, por día calendario,
  por cambio de git ref? (Recomendación: por sesión de trabajo — se
  re-verifica al inicio de cada gate, no por tiempo transcurrido. La métrica
  de tiempo es fallback si no hay `fs_hash` previo.)

- **P-4 — Qué hacer cuando un test preexistente referencia la triada literal
  por nombre.** Si hay tests que asumen que "no AGENTS.md + no .mcp.json =
  sin surface", la nueva lista declarativa los rompe. (Recomendación: tests que
  asumen la triada literal se actualizan para que asuman la lista declarativa
  cuando el spec la declare; los tests del incidente del 2026-09-29 sirven
  como referencia del patrón a migrar.)

- **P-5 — Cuándo arranca la cadena de dependencia con 0002.** ¿Apenas 0003
  llegue a `done`, o después de un gate explícito de re-verificación de 0002?
  (Recomendación: cuando 0003 llegue a `done`, el gate de implementación de
  0002 se re-corre automáticamente con la lista declarativa y la segunda red;
  si encuentra superficie no declarada en el spec post-delta, escala; si pasa,
  0002 puede cerrar.)
