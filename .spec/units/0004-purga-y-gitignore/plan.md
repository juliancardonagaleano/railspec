# Plan técnico — Purga de archivos de ruido y blindaje del .gitignore

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de un `spec.md` aprobado.

## Enfoque

La unidad cierra una grieta puntual: `.gitignore` ya excluye las fuentes de ruido tradicionales (Python líneas 25-46, Node líneas 49-52, OS/IDE líneas 1-13) pero omite dos caches que `AGENTS.md:164-167` declara como excluidos — `.gitnexus/` y `.codebase-memory/`. El cambio es estrictamente aditivo: dos líneas nuevas al final del bloque "Estado local efímero" (`.gitignore:54-64`), sin reordenar, fusionar ni reescribir reglas existentes. Ningún consumidor del repo lee `.gitignore` por path fijo ni asume su forma actual (`grep -rn '\.gitignore' --include='*.py' --include='*.sh' .` → 0 hits), así que el cambio es invisible para el código.

El plan no introduce scripts, hooks, ni materializadores nuevos. La auditoría ya está codificada como CA-01..CA-08 en `spec.md` y se ejecuta con comandos `git` reproducibles — no necesita un runner propio. La elección de `git check-ignore --no-index` para CA-06 (en vez del `grep -F` del draft inicial, refutado en gate) evita el falso negativo que un glob como `**/agents/` produciría sobre un `source` del manifiesto: el guardián se valida por comportamiento, no por superficie textual.

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `.gitignore` | modificar | Añadir `.gitnexus/` y `.codebase-memory/` al final del bloque "Estado local efímero" (después de la línea 64). Sin tocar los bloques OS/IDE (1-13), Python (25-46) ni Node (49-52). |

## Reutilización (no reinventar)

- `.gitignore:25-46` — bloque Python ya cubre `__pycache__/`, `.pytest_cache/`, `.mypy_cache/`, `.ruff_cache/`; los 2 patrones nuevos no son caches de Python, no encajan aquí.
- `.gitignore:49-52` — bloque Node ya cubre `node_modules/`; `.gitnexus/` y `.codebase-memory/` no son caches de Node, no encajan aquí.
- `.gitignore:59-64` — bloque "Estado local efímero" ya agrupa 6 patrones del andamiaje local (`.spec/.usage/`, `.spec/.supervised-runtime/`, `.spec/units/*/.gate-delta/`, `.spec/.pilot-verde`, `.spec/.kit-install/`, `.spec/scripts/**/__pycache__/`); los 2 caches MCP son de la misma naturaleza (artefactos que un servidor o el andamiaje escribe en tiempo de ejecución) y encajan aquí.
- `installer/kit_manifest.yaml:24-205` — los 41+ pares `source`/`dest` que CA-06 enumera se leen directamente del manifiesto en tiempo de verificación; no se copian a una lista propia.

## Decisiones de diseño

- **Decisión:** añadir `.gitnexus/` y `.codebase-memory/` al final del bloque "Estado local efímero" (`.gitignore:54-64`), no en los bloques Python ni Node. **Razón:** son caches de servidores MCP (`/opt/homebrew/bin/gitnexus mcp` y `codebase-memory-mcp`), declarados en `AGENTS.md:164-167` como caches excluidos. No son caches de Python ni de Node, por lo que su ubicación debe respetar la categoría "artefactos que el andamiaje/servidor escribe en tiempo de ejecución", que es la del bloque "Estado local efímero" (comentario en `.gitignore:54-58`).
- **Decisión:** no tocar el allowlist `.vscode/*` (`.gitignore:9-13`) ni los bloques OS/IDE/Python/Node. **Razón:** el alcance del spec no los menciona; las preguntas abiertas (`spec.md:117-119`) las dejan explícitamente fuera como suposiciones conservadoras. Forzar aquí un cambio de política sobre `.vscode/` (o agregar `.spec/.gate-delta/` a nivel raíz) amplificaría la superficie de la unidad sin valor.
- **Decisión:** usar `git check-ignore --no-index` para CA-06, no `grep -F` sobre el contenido de `.gitignore`. **Razón:** CA-06 valida por comportamiento del guardián, no por superficie textual; un glob como `**/agents/` excluiría un `source` del manifiesto sin aparecer como substring literal en `.gitignore`, produciendo un falso negativo silencioso. El gate del spec ya documentó esta refutación (`bitacora.md:19`).

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | grupo único — cambios acoplados | `.gitignore` | — | estandar |

## Riesgos y mitigaciones

- **Riesgo:** un commit accidental suba `.gitnexus/` o `.codebase-memory/` entre el cierre de spec y la implementación. **Mitigación:** el cambio es de 2 líneas a un archivo ya versionado; entre la redacción del plan y la implementación no hay clone nuevo ni sesión interactiva de `gitnexus analyze` que pueda materializar esos directorios. Si llegasen a aparecer, `git ls-files` los detectaría antes del push (CA-01, CA-02). El `git log --all --diff-filter=A -- '.gitnexus/*' '.codebase-memory/*'` hecho en la fase de spec confirma que esos directorios nunca se intentaron commitear en la historia del repo.
- **Riesgo:** las 2 líneas nuevas rompan un allowlist o el orden de negaciones de `.gitignore`. **Mitigación:** los allowlist activos son los de `.vscode/*` (`.gitignore:9-13`) y **no contienen** `.gitnexus` ni `.codebase-memory`; las líneas nuevas se añaden al final del archivo, fuera de cualquier bloque `!`.
- **Riesgo:** CA-06 (manifesto completo) falle por un `source`/`dest` que un glob del `.gitignore` excluya sin que el spec lo haya previsto. **Mitigación:** el barrido `grep -rn '\.gitignore' --include='*.py' --include='*.sh' .` hecho en la fase de spec no encontró ningún consumidor del archivo en código del repo; los globs presentes (`__pycache__/`, `node_modules/`, `**/.env`, `.vscode/*` con allowlist, `.idea/`, `*.egg-info/`, `dist/`, `build/`) no intersectan ninguno de los 41+ paths del manifiesto, todos ya validados con `git ls-files` (CA-01, CA-02).
- **Riesgo:** CA-05 enumere un patrón ausente tras la implementación. **Mitigación:** los 16 patrones del bucle se descomponen en 14 ya presentes en `.gitignore` actual + 2 que añade G1 — la aritmética 14+2=16 cierra.

## Comando de validación

Comando principal (literal de `_estado.yaml > comando_validacion`):

```
python3 -m pytest installer -q && python3 scripts/kit_doctor.py
```

Sub-verificación de humo para CA-05 (bucle sobre los 16 patrones; el `|| echo "FALTA: $p"` nombra el ausente si lo hay):

```bash
for p in __pycache__/ .pytest_cache/ .pytest_*/ .ruff_cache/ .mypy_cache/ \
         node_modules/ .DS_Store '*.py[cod]' '*.egg-info/' \
         .spec/.usage/ .spec/.supervised-runtime/ \
         .spec/units/*/.gate-delta/ .spec/.kit-install/ \
         .spec/scripts/**/__pycache__/ .gitnexus/ .codebase-memory/; do
  grep -F -q -e "$p" .gitignore || { echo "FALTA: $p"; exit 1; }
done
```

Los CA-01, CA-02, CA-03, CA-04, CA-06 se ejecutan manualmente con `git ls-files`, `git check-ignore -v` y `git status --ignored --short` (comandos literales del spec, sección Criterios de aceptación).
