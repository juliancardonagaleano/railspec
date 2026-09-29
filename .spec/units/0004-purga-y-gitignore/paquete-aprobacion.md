# Paquete de aprobación — Purga de archivos de ruido y blindaje del .gitignore

> Artefacto del **modo semi-autonomo**. Es lo que se presenta al humano en el único
> checkpoint duro (antes de implementar). Su propósito es que el humano decida
> con evidencia en minutos, sin releer tres documentos: qué se va a hacer, qué
> criticaron los agentes, qué se supuso y qué se va a tocar.
>
> No sustituye a `spec.md` / `plan.md` / `tasks.md`: los resume y enlaza.

## Petición original

"Realizar una limpieza y purga de todos los archivos que hagan ruido dentro del repositorio."

## Qué se va a hacer (2-5 líneas)

Cerrar la grieta de `.gitignore`: añadir los patrones `.gitnexus/` y `.codebase-memory/` al final del bloque "Estado local efímero" — exactamente los caches que `AGENTS.md:164-167` declara como excluidos pero que `.gitignore` no implementaba. Hoy no hay archivos versionados de ruido (`git ls-files` ya devuelve 0 hits), pero la primera vez que un dev corra `gitnexus analyze` o `codebase-memory-mcp` en su copia, ese directorio quedaría versionable. El cambio es estrictamente aditivo: 2 líneas, sin tocar nada más.

**Perfil de esfuerzo:** `ligero` — se puede cambiar en este mismo checkpoint con `/sdd-perfil <nombre>`; rige desde la siguiente invocación de subagente.

## Criterios de aceptación

| Id | Criterio | Tareas que lo cubren |
|---|---|---|
| CA-01 | `git ls-files \| grep -E '__pycache__\|\.pytest_cache\|\.DS_Store\|node_modules\|\.ruff_cache\|\.mypy_cache' \| wc -l` devuelve `0` | T2 |
| CA-02 | `git ls-files \| grep -E '\.gitnexus/\|\.codebase-memory/' \| wc -l` devuelve `0` | T2 |
| CA-03 | `git check-ignore -v` sobre 9 paths ruidosos reporta la regla excluyente (exit 0) | T3 |
| CA-04 | `git status --ignored --short` lista 9 paths con `!!` y NO lista 11 paths de carga legítima | T4 |
| CA-05 | Bucle sobre los 16 patrones termina con exit 0 (cada `grep -F -q -e "$p" .gitignore` encuentra el patrón; si falta uno, se nombra) | T1, T5 |
| CA-06 | `git check-ignore --no-index` sobre cada source/dest del manifiesto termina con exit 1 (validación por comportamiento del guardián, no por superficie textual) | T6 |
| CA-07 | `python3 -m pytest installer -q` exit code 0 | T7 |
| CA-08 | `python3 scripts/kit_doctor.py` exit code 0 | T8 |

## Alcance del cambio

- **Archivos a crear/modificar:** 1 archivo (ver `plan.md`) — `.gitignore` (modificar, añadir 2 líneas al final del bloque "Estado local efímero").
- **Fuera de alcance (declarado):**
  - Borrar los directorios de ruido que ya existen en el árbol de trabajo del desarrollador (`scripts/__pycache__/`, `.pytest_cache/`, etc.). Son locales por definición (`.gitignore` ya los ciega) y se purgan solos con `git clean -fdx` o re-clon.
  - Modificar `installer/kit_manifest.yaml`, `AGENTS.md`, scripts del andamiaje, o cualquier archivo bajo `.spec/scripts/`.
  - Cambiar el allowlist `.vscode/*`, los bloques OS/IDE/Python/Node de `.gitignore`, ni agregar `.spec/.gate-delta/` a nivel raíz.
  - Introducir un `pre-commit`/`pre-push` adicional que valide `.gitignore`.
- **Comando de validación:** `python3 -m pytest installer -q && python3 scripts/kit_doctor.py`

## Historial de gates

Todas las columnas salen de `_estado.yaml > gates > <fase>`.

| Fase | Veredicto | Iteraciones | Hallazgos resueltos | Hallazgos sin resolver | Governance |
|---|---|---|---|---|---|
| spec | refinado | 1 | 2 (media) + 1 (baja anotada) | 1 (baja) | si — centinela [ninguna-aplicable] confirmado |
| plan | aprobado | 1 | 0 | 0 | si — centinela [ninguna-aplicable] confirmado |
| tasks | aprobado | 1 | 0 | 0 | si — centinela [ninguna-aplicable] confirmado |

**Correcciones más relevantes:**

1. **CA-05:** el draft original empaquetaba 16 sub-aserciones en un solo `grep -F -e '<p1>' -e '<p2>' ...` con respuesta binaria — si fallaba, no se sabía cuál patrón faltaba sin relanzar 16 greps individuales. **Corrección:** bucle `for p in ...; do grep -F -q -e "$p" .gitignore || { echo "FALTA: $p"; exit 1; }; done` que **nombra el patrón ausente** en la salida.
2. **CA-06:** el draft usaba `grep -F '<path>' .gitignore`, que solo detecta reglas escritas con la ruta literal. Un glob como `**/agents/` excluiría un `source` del manifiesto sin aparecer como substring en `.gitignore`, produciendo **falso negativo silencioso** (el spec mismo cita `**/.env` como excepción documentada, lo que confirma que la prosa conoce la existencia de globs). **Corrección:** sustituir por `git check-ignore --no-index -- <path>` — verifica el *comportamiento* del guardián, no la *superficie textual* del archivo.
3. **CA-04** (severidad baja, no bloquea): aserción compuesta (positiva + negativa en un solo criterio). Anotada como mejora opcional para una unidad posterior.

## Suposiciones tomadas (requieren confirmación)

En modo semi-autonomo las preguntas abiertas se resuelven con la opción más conservadora. Cada una se lista aquí para que el humano la confirme o corrija.

| # | Pregunta | Suposición tomada | Impacto si es errada |
|---|---|---|---|
| 1 | ¿Hace falta también `.spec/.gate-delta/` a nivel raíz (no per-unidad)? | **No**, se mantiene el `.spec/units/*/.gate-delta/` actual. Si el plan detecta escritura global del andamiaje bajo `.spec/.gate-delta/`, lo eleva a criterio nuevo. | Mínimo: si aparece un `.spec/.gate-delta/` global, podría quedar versionable hasta que se cierre en una unidad posterior. |
| 2 | ¿`.vscode/` debe excluirse entero en lugar del whitelist actual? | **No**, se mantiene el whitelist actual (`.gitignore:9-13`). | Mínimo: el whitelist actual es defensivo y razonable; un cambio de política cae en una unidad separada si se decide. |

## Riesgos aceptados

- **Commit accidental entre la aprobación y la implementación.** Mitigación: el cambio es de 2 líneas a archivo ya versionado; sin sesión interactiva de `gitnexus analyze` que materialice los directorios. CA-01/CA-02 los detectaría pre-push. `git log --all --diff-filter=A -- '.gitnexus/*' '.codebase-memory/*'` confirma que esos directorios **nunca** se intentaron commitear en la historia del repo.
- **Las 2 líneas rompan un allowlist.** Mitigación: los allowlist activos son `.vscode/*` (`.gitignore:9-13`) y **no contienen** `.gitnexus` ni `.codebase-memory`; las líneas nuevas van al final del archivo, fuera de cualquier bloque `!`.
- **CA-06 falle por un glob imprevisto del `.gitignore` que excluya un source/dest del manifiesto.** Mitigación: el barrido `grep -rn '\.gitignore' --include='*.py' --include='*.sh' .` no encontró ningún consumidor del archivo en código; los globs presentes (`__pycache__/`, `node_modules/`, `**/.env`, `.vscode/*` con allowlist, `.idea/`, `*.egg-info/`, `dist/`, `build/`) no intersectan ninguno de los 41+ paths del manifiesto, todos ya validados con `git ls-files` (CA-01, CA-02).

## Gobernanza aplicada

`governance_refs`: `[ninguna-aplicable]` — consultada vía MCP el `2026-09-29T05:55:00Z` (fase spec) y ratificada en `2026-09-29T11:22:00Z` (fase plan) y `2026-09-29T11:25:37Z` (fase tasks).

**Justificación:** el kit no tiene superficie de gobernanza propia que consultar. `pce-mcp` aparece solo como carga que el kit instala en un destino, no como cliente conectado al repo del kit sobre sí mismo. La prosa de `AGENTS.md` raíz se autodefine como "prosa de gobernanza que el kit lleva como carga al destino"; `.mcp.json` raíz apunta a `scripts/mcp-pce.sh` (launcher del destino); `scripts/mcp-pce.sh` reitera que la fuente de `.env` es el destino.

Los tres lentes L4 (spec), L4 (plan) y L3 (tasks) confirmaron el centinela con evidencia del repo en cada corrida del gate. **No** se invocó `pce-mcp` en ningún momento (no hay con quién, y el centinela exime de la consulta). Sin gobernanza de memoria: el centinela está verificado por evidencia directa del filesystem.

Precedentes: `.spec/units/0001-versionado-y-actualizacion-del-kit/spec.md:78-97` y `.spec/units/0003-fiabilidad-de-gates-de-gobernanza/spec.md:321` cubren este mismo caso.

## Decisión

- [ ] **Aprobar** — implementar según `tasks.md`
- [ ] **Aprobar con cambios** — indicar cuáles (vuelve a la fase que corresponda)
- [ ] **Rechazar** — la unidad queda en `estado: bloqueado` con el motivo en bitácora