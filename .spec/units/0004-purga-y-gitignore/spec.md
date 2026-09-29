# Spec — Purga de archivos de ruido y blindaje del .gitignore

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

El árbol de trabajo del repo exhibe directorios de ruido generados por
herramientas (`__pycache__/`, `.pytest_cache/`, etc.) y por servidores
MCP (`AGENTS.md:164-167` declara `.gitnexus/` y `.codebase-memory/`
como caches excluidos por `.gitignore`). A la fecha, **ninguno de esos
directorios está versionado** — `git ls-files | grep -E '__pycache__|\.pytest_cache|\.DS_Store|node_modules|\.ruff_cache|\.mypy_cache'`
devuelve 0 hits — pero `.gitignore` tiene una grieta visible: los
caches de los MCP de inteligencia de código están documentados como
excluidos en la prosa normativa y `.gitignore` no los implementa. La
prosa y el guardián se contradicen; la primera vez que un desarrollador
corra `gitnexus analyze` o un servidor `codebase-memory-mcp` en su
copia, ese directorio quedará versionable.

Si esto no se resuelve: (a) un commit accidental puede subir
`.gitnexus/` o `.codebase-memory/`, contaminando `git clone` de otros;
(b) la cobertura de los caches del andamiaje queda solo inferida, no
auditada con un comando reproducible; y (c) el contrato entre
`installer/kit_manifest.yaml` (lo que el kit carga) y `.gitignore`
(lo que se excluye) no se verifica mecánicamente, así que un futuro
"simplificar .gitignore" puede borrar por accidente una regla
necesaria sin que nada lo detecte.

## Resultado esperado

Tras cerrar esta unidad, el repositorio cumple simultáneamente tres
propiedades verificables sin interpretar:

1. **Ningún archivo de ruido está versionado** — `git ls-files` por
   los patrones canónicos (`__pycache__`, `.pytest_cache`,
   `node_modules`, `.ruff_cache`, `.mypy_cache`, `.DS_Store`,
   `.gitnexus`, `.codebase-memory`) devuelve cero hits.
2. **Todo directorio de ruido conocido está ciego por `.gitignore`** —
   verificado por `git status --ignored` y `git check-ignore` por
   path. La grieta actual (`.gitnexus/` y `.codebase-memory/`,
   citados en `AGENTS.md:164-167` pero ausentes de `.gitignore`)
   queda cerrada; el conjunto final de reglas se documenta como lista
   canónica en este spec (CA-05).
3. **La carga legítima del kit no queda excluida por accidente** —
   cada `source` y `dest` de `installer/kit_manifest.yaml` sigue
   legible bajo `git ls-files`. Excepción documentada: `**/.env`
   (`.gitignore:46`) excluye `.env`, archivo que el manifiesto NO
   declara a propósito.

La suite del instalador (`python3 -m pytest installer -q`) sigue verde
y `python3 scripts/kit_doctor.py` no detecta regresiones.

## Alcance

**Incluye:**
- Auditar el estado actual de `.gitignore` con comandos reproducibles
  (qué patrones literales están, qué directorios de ruido caen bajo
  ellos, qué entradas del manifiesto podrían estar excluidas por error).
- Cerrar los huecos encontrados — añadir a `.gitignore` los patrones
  `.gitnexus/` y `.codebase-memory/` que `AGENTS.md:164-167` ya
  declara como excluidos pero `.gitignore` no implementa.
- Confirmar que la suite del instalador pasa sin cambios — esta
  unidad no toca `installer/`, solo `.gitignore`.

**No incluye (fuera de alcance):**
- Borrar los directorios de ruido que ya existen en el árbol de
  trabajo del desarrollador (`scripts/__pycache__/`, `.pytest_cache/`,
  etc.). Son locales por definición (`.gitignore` ya los ciega) y se
  purgan solos con `git clean -fdx` o re-clon — un script que los
  borre amplifica la superficie del kit sin valor.
- Modificar `installer/kit_manifest.yaml` para declarar esos
  directorios como "carga": no lo son — son ruido.
- Modificar `AGENTS.md` (la cita sobre `.gitnexus/` y
  `.codebase-memory/` ya es correcta; lo que faltaba era que
  `.gitignore` la implementara).
- Cambiar `installer/`, scripts del andamiaje, o cualquier archivo bajo
  `.spec/scripts/` — esta unidad es estrictamente sobre el guardián de
  qué queda versionado.
- Introducir un `pre-commit`/`pre-push` adicional que valide
  `.gitignore` — la auditoría reproducible ya es el contrato; otro
  hook lo duplicaría.

## Criterios de aceptación

Cada `CA-NN` se verifica con un comando o `ruta:línea` reproducible;
el plan y `tasks.md` los referencian por id; el gate de tareas falla
si algún criterio queda sin tarea que lo cubra.

- [ ] CA-01 — `git ls-files | grep -E '__pycache__|\.pytest_cache|\.DS_Store|node_modules|\.ruff_cache|\.mypy_cache' | wc -l` desde la raíz del repo devuelve `0`.
- [ ] CA-02 — `git ls-files | grep -E '\.gitnexus/|\.codebase-memory/' | wc -l` desde la raíz del repo devuelve `0`.
- [ ] CA-03 — `git check-ignore -v scripts/__pycache__ installer/__pycache__ installer/tests/__pycache__ installer/.pytest_cache .pytest_cache .spec/.supervised-runtime .spec/.usage .spec/scripts/__pycache__ .spec/scripts/tests/__pycache__` reporta, para cada uno de los 9 paths, la regla de `.gitignore` que lo excluye (exit 0 y una línea por path).
- [ ] CA-04 — `git status --ignored --short` lista, con prefijo `!!`, los 9 paths de CA-03, y **no** lista `AGENTS.md`, `README.md`, `installer/kit_manifest.yaml`, `docs/inventario-extraccion.yaml`, `.spec/protocolo-datos.yaml`, `.spec/perfiles.yaml`, `.spec/_plantillas/`, `.spec/_fixtures/`, `.agents/`, `scripts/`, `installer/`.
- [ ] CA-05 — Cada uno de los 16 patrones listados aparece en `.gitignore` (bucle `set -f; for p in __pycache__/ .pytest_cache/ .pytest_*/ .ruff_cache/ .mypy_cache/ node_modules/ .DS_Store '*.py[cod]' '*.egg-info/' .spec/.usage/ .spec/.supervised-runtime/ .spec/units/*/.gate-delta/ .spec/.kit-install/ .spec/scripts/**/__pycache__/ .gitnexus/ .codebase-memory/; do grep -F -q -e "$p" .gitignore || { echo "FALTA: $p"; exit 1; }; done` termina con exit 0 — la salida del comando nombra el patrón faltante si lo hay; el `set -f` previo desactiva globbing de zsh para que los literales `*` no se expandan antes de `grep`).
- [ ] CA-06 — Para cada `source` y `dest` declarado en `installer/kit_manifest.yaml`, `git check-ignore --no-index -- <path1> <path2> ...` termina con exit 1 (ningún path del manifiesto está excluido por `.gitignore`, **verificación por comportamiento del guardián, no por superficie textual**). El método basado en `grep -F` no aplica: un glob como `**/agents/` excluiría una entrada del manifiesto sin aparecer como substring en `.gitignore`, produciendo un falso negativo silencioso. Excepción documentada: `**/.env` (`.gitignore:46`) excluye `.env`, archivo que el manifiesto NO incluye a propósito — ese path no figura en el manifiesto, así que no entra al comando.
- [ ] CA-07 — `python3 -m pytest installer -q` desde la raíz del repo termina con exit code 0 (contrato del manifiesto, baseline de instalación, aislamiento, verificadores).
- [ ] CA-08 — `python3 scripts/kit_doctor.py` desde la raíz del repo termina con exit code 0 **o** exit != 0 con al menos una línea en `bitacora.md` de la unidad que documente la falla como ambiental — **falla documentada** por la unidad, registrada como ruido del entorno local (precedente U-0004 § Governance aplicable: `pce-mcp` y/o `graph-index` no responden en este clon); sin nuevos faltantes de carga ni de configuración del entorno agéntico.

## Governance aplicable

`pce-mcp` **no se ejecuta** contra el repo del kit — el kit es
productor del contrato de gobernanza que un destino recibe (`AGENTS.md`,
`.mcp.json`, `opencode.jsonc`, `scripts/mcp-pce.sh`), no su consumidor.
La superficie de gobernanza que el kit produce vive en cada destino,
no en este repo. Por tanto, **no hay artefacto de gobernanza recuperable
contra el cual evaluar este spec** — condición distinta de "un MCP
caído", que el centinela `governance_refs: [ninguna-aplicable]` registra
de forma explícita. El precedente en `0001-…/spec.md:78-97` y
`0003-…/spec.md:321` cubre este mismo caso. Esta unidad, además, **no
toma decisiones de fuente única ni invierte la precedencia de
superficies** — solo añade líneas a `.gitignore` ya citadas en la
prosa normativa local (`AGENTS.md:164-167`).

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Sin superficie de gobernanza propia del kit (`pce-mcp` no se ejecuta contra este repo); ver justificación arriba |

## Preguntas abiertas

- **¿Hace falta también `.spec/.gate-delta/` a nivel raíz (no per-unidad)?** Hoy solo `.spec/units/*/.gate-delta/` está excluido. **Suposición conservadora**: la unidad actual no lo agrega; si el plan detecta escritura global del andamiaje bajo `.spec/.gate-delta/`, lo eleva a criterio nuevo.
- **¿`.vscode/` debe excluirse entero en lugar del whitelist actual?** El allowlist de 4 extensiones (`.gitignore:10-13`) es defensivo y razonable. **Suposición conservadora**: se mantiene el whitelist actual; un eventual cambio de política cae en una unidad separada.