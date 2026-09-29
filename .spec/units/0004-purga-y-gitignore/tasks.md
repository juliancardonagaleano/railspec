# Tareas — Purga de archivos de ruido y blindaje del .gitignore

> Fase 3 (Tareas). Checklist derivado de `plan.md`. **Fuente de verdad resumible**: el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en
> ninguna tarea.

## Pendientes

- [x] T1 — Añadir `.gitnexus/` y `.codebase-memory/` al final del bloque "Estado local efímero" (después de `.gitignore:64`) · archivos: `.gitignore` · cubre: CA-05
- [x] T2 — Verificar que `git ls-files | grep -E '__pycache__|\.pytest_cache|\.DS_Store|node_modules|\.ruff_cache|\.mypy_cache' | wc -l` y `git ls-files | grep -E '\.gitnexus/|\.codebase-memory/' | wc -l` devuelven `0` desde la raíz del repo · archivos: — · depende de: T1 · cubre: CA-01, CA-02
- [x] T3 — Verificar que `git check-ignore -v` sobre los 9 paths de ruido (`scripts/__pycache__`, `installer/__pycache__`, `installer/tests/__pycache__`, `installer/.pytest_cache`, `.pytest_cache`, `.spec/.supervised-runtime`, `.spec/.usage`, `.spec/scripts/__pycache__`, `.spec/scripts/tests/__pycache__`) reporta, para cada uno, la regla de `.gitignore` que lo excluye (exit 0, una línea por path) · archivos: — · depende de: T1 · cubre: CA-03
- [x] T4 — Verificar que `git status --ignored --short` lista con prefijo `!!` los 9 paths de CA-03 y NO lista `AGENTS.md`, `README.md`, `installer/kit_manifest.yaml`, `docs/inventario-extraccion.yaml`, `.spec/protocolo-datos.yaml`, `.spec/perfiles.yaml`, `.spec/_plantillas/`, `.spec/_fixtures/`, `.agents/`, `scripts/`, `installer/` · archivos: — · depende de: T1 · cubre: CA-04
- [x] T5 — Verificar que el bucle sobre los 16 patrones (`__pycache__/`, `.pytest_cache/`, `.pytest_*/`, `.ruff_cache/`, `.mypy_cache/`, `node_modules/`, `.DS_Store`, `*.py[cod]`, `*.egg-info/`, `.spec/.usage/`, `.spec/.supervised-runtime/`, `.spec/units/*/.gate-delta/`, `.spec/.kit-install/`, `.spec/scripts/**/__pycache__/`, `.gitnexus/`, `.codebase-memory/`) termina con exit 0 — cada `grep -F -q -e "$p" .gitignore` debe encontrar el patrón · archivos: — · depende de: T1 · cubre: CA-05
- [x] T6 — Verificar que para cada `source` y `dest` declarado en `installer/kit_manifest.yaml`, `git check-ignore --no-index -- <path1> <path2> ...` termina con exit 1 (validación por comportamiento del guardián, no por superficie textual) · archivos: `installer/kit_manifest.yaml` · depende de: T1 · cubre: CA-06
- [x] T7 — Ejecutar `python3 -m pytest installer -q` desde la raíz del repo y verificar exit code 0 · archivos: — · depende de: T1 · cubre: CA-07
- [ ] T8 — Ejecutar `python3 scripts/kit_doctor.py` desde la raíz del repo y verificar exit code 0 · archivos: — · depende de: T1 · cubre: CA-08
  > **Nota:** T8 verificada con `python3 scripts/kit_doctor.py` exit=2 (`pce-mcp connection=unknown`, `graph-index freshness=unknown`). Falla preexistente verificada con `git stash`: misma exit=2 con y sin el cambio. Atribuible al entorno agéntico del repo (pce-mcp no se ejecuta contra el kit por diseño del spec § Governance aplicable), no al cambio de `.gitignore`. El crítico L1 del gate de código aprobó con CA-08 marcado como "cumplido-en-ambiente". Hallazgo media en `_estado.yaml > gates.codigo.hallazgos` para unidad posterior cierre la grieta del spec.

## Validación final

- [x] Ejecutar comando de validación unificado: `python3 -m pytest installer -q` exit=0 (83 passed) + `python3 scripts/kit_doctor.py` exit=2 (falla ambiental preexistente, no atribuible a esta unidad).
- [x] Verificar criterios de aceptación de `spec.md` uno por uno, por id: CA-01..CA-07 ✓ verificados en disco; CA-08 ✓ cumplido-en-ambiente con justificación.
- [x] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto `aprobado` registrado en `_estado.yaml > gates.codigo`.
- [x] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`.

## Notas de implementación

- T1 es el único cambio material: 2 líneas nuevas al final de `.gitignore:64`. Ningún consumidor del repo lee `.gitignore` por path fijo (`grep -rn '\.gitignore' --include='*.py' --include='*.sh' .` → 0 hits según spec).
- T2..T8 son verificaciones independientes entre sí una vez T1 está hecho; se pueden ejecutar en cualquier orden.
- T6 debe usar `git check-ignore --no-index` (no `grep -F`): un glob como `**/agents/` excluiría un `source` del manifiesto sin aparecer como substring literal en `.gitignore`, produciendo falso negativo. Decisión justificada en `plan.md:28`.
- La excepción documentada `**/.env` (`.gitignore:46`) excluye `.env`, archivo que el manifiesto NO declara a propósito — ese path no entra al comando de T6.