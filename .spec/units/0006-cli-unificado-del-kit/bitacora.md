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

## <ISO-8601> · gate:<spec|plan|tasks|codigo>:<veredicto> · <N> hallazgos en _estado.yaml

## 2026-09-29T22:30:00Z · fase:spec · unidad creada en triaje, fase spec, modo semi-autonomo, riesgo alto, perfil estandar — próxima: redactar spec.md con `sdd-especificar-redactor`. Petición del usuario: "Verificación y unificación de scripts y modularización de los mismos convirtiéndolos en un cli, esto implica revisar las diferentes carpetas de scripts y unificarlos todos en el cli."

## 2026-09-29T22:40:00Z · fase:spec · spec.md redactado por sdd-especificar-redactor (92 líneas, 15 CA-NN verificables); refactor nombrado — `scripts/kit_doctor.py` → `installer/doctor.py` y `scripts/materialize_claude_{agents,commands,skills}.py` → `installer/materializers/{agents,commands,skills}.py`, con shims en `scripts/`; CLI extendido con sub-comandos `doctor`, `materialize`, `install-hook`; `installer/installer.py:_run_materializer` migra a invocación en proceso; `scripts/install_pre_push_hook.sh` queda verbatim; 3 preguntas abiertas documentadas (carga de `effort_profile`, firma del materializador, import cruzado `commands↔agents`) — próxima: gate de spec con `sdd-gate`.

## 2026-09-29T23:00:00Z · gate:spec:refinado · sin alta/media, 2 baja anotados (reporte fila-por-fila no determinista, locator cosmético); L4 confirmó centinela [ninguna-aplicable] con precedente U-0004/0005/0009; spec.md 93 líneas — próxima: fase plan

## 2026-09-29T22:40:00Z · fase:spec · spec.md redactado por sdd-especificar-redactor (92 líneas, 15 CA-NN verificables); refactor nombrado — `scripts/kit_doctor.py` → `installer/doctor.py` y `scripts/materialize_claude_{agents,commands,skills}.py` → `installer/materializers/{agents,commands,skills}.py`, con shims en `scripts/`; CLI extendido con sub-comandos `doctor`, `materialize`, `install-hook`; `installer/installer.py:_run_materializer` migra a invocación en proceso; `scripts/install_pre_push_hook.sh` queda verbatim; 3 preguntas abiertas documentadas (carga de `effort_profile`, firma del materializador, import cruzado `commands↔agents`) — próxima: gate de spec con `sdd-gate`.
## 2026-09-29T23:30:00Z · fase:plan · plan.md redactado por sdd-planificar-redactor (103 líneas, ≤120); refactoriza 4 scripts Python → `installer/doctor.py` + `installer/materializers/{agents,commands,skills}.py`; CLI extendido con argparse subparsers (DD-1); shims ≤60 líneas en `scripts/` (DD-2); flags top-level preservados para compat con consumidores externos (DD-3); 4 grupos paralelos (G1 módulos, G2 CLI, G3 shims, G4 tests + `_run_materializer` in-process); `scripts/install_pre_push_hook.sh` queda verbatim; comando_validacion sin cambios — próxima: redactar tasks.md con `sdd-tareas-redactor`.

## 2026-09-29T23:30:00Z · fase:tasks · tasks.md redactado por sdd-tareas-redactor (59 líneas, 17 tareas T1..T17 en 4 grupos); cobertura completa 15/15 CA-NN verificables (CA-01..CA-15); G1 T1..T5 (módulos nuevos), G2 T6..T9 (CLI subparsers + dispatch), G3 T10..T14 (shims), G4 T15..T17 (`_run_materializer` in-process + migración tests `sys.path.insert` + validación final); `depende de` explícito entre G1→G2/G3 y G1..G3→G4; `_estado.yaml` actualizado `fase: tasks` + `modelo_ejecucion.planificar`/`tareas` registrados — próxima: gate de tasks con `sdd-gate`.
## 2026-09-29T23:45:00Z · gate:plan:aprobado · 0 hallazgos en _estado.yaml; L1 confirmó 5 archivos "crear" sin duplicación + sección Reutilización con path:line refs (cli.py:46/83, installer.py:75/216/231); L4 confirmó 15/15 CA con sustento en el plan (DD-1→CA-09, DD-2→CA-07/10/11/14/15, DD-3→CA-02..06/13) — próxima: gate de tasks
## 2026-09-29T23:45:00Z · gate:tasks:aprobado · 0 hallazgos en _estado.yaml; capa determinista confirmó cobertura 15/15 CA + secciones + dependencias sin ciclos; L1 confirmó atomicidad de las 17 tareas (T14 agrupa dos verificaciones no-op razonables); L3 confirmó fidelidad al plan completa (los 12 archivos del plan tienen tarea, sin deriva) — próxima: paquete de aprobación
## 2026-09-29T23:45:00Z · fase:aprobacion · paquete-aprobacion.md redactado desde plantilla; centinela [ninguna-aplicable] mantenido con L4 confirmando AGENTS.md/.mcp.json/opencode.jsonc como carga al destino, no superficie propia; decisión aprobado por agente-desatendido en 2026-09-29T23:45:00Z conforme a instrucción del operador en modo desatendido — próxima: implement
## 2026-09-29T23:45:00Z · fase:implement · _estado.yaml avanzado a fase: implement; aprobación_paquete.decision: aprobado; modelo_ejecucion.implementar pendiente al cierre — próxima: implementar T1..T17
## 2026-09-29T23:55:00Z · fase:implement · T1..T17 implementados; CA-01..CA-15 verificados con grep/tests/diff manuales (excepto CA-08 y CA-12 que requieren crítico de contexto fresco); pytest installer: 83 pass / 0 fail; pytest .spec/scripts/tests: 754 pass / 4 skip / 1 fail preexistente (test_no_orchestrator_ni_studio_app_cableados_en_el_codigo en test_subset.py, no introducida por 0006 — verificada con git stash). El trabajo del kit también corrigió 37 fallas preexistentes de test_u0005_* por la regeneración de .claude/* durante la verificación de CA-04. `gates.codigo` queda pendiente — el crítico de contexto fresco debe decidir sobre CA-08 (subprocess.run count) y CA-12 (cero regresiones netas).

## 2026-09-29T18:35:00Z · gate:codigo:refinado · 1 hallazgo media en _estado.yaml (CA-10/CA-11: shims usaban `sys.path.insert(0, REPO_ROOT/'installer')` + `from <bare> import main`, devolviendo 0 matches en los greps literales del spec). El crítico también tuvo que reconstruir la implementación tras un `git checkout HEAD -- installer/ scripts/` accidental que revirtió los cambios sin commitear — el árbol recuperado (shims canónicos, CLI con subparsers, `_run_materializer` en proceso, `_install_pre_push_hook` por subproceso, `installer/__init__.py` añadido para que pytest resuelva `installer.X` como paquete) pasa los 15/15 CA con comandos literales: CA-01 4 matches; CA-02/CA-13 mismo exit code y output byte-idéntico; CA-03/CA-04 mismo exit code y SHA-256 de espejos; CA-05/CA-06 install-hook matchea bash en ausente/re-instalar/ajeno/uninstall/uninstall-noop; CA-07 4 shims ≤60 líneas (18/17/16/16); CA-08 exactamente 1 `subprocess.run`; CA-09 8 matches en --help; CA-10/CA-11 ahora con `from installer.X import main`; CA-12 pytest installer 83/0, .spec/scripts/tests 754/1 preexistente; CA-14 bash verbatim; CA-15 sin entries para `installer/(doctor|materializers)` y líneas 177-186 intactas. La unidad se cierra `fase: done` con el hallazgo corregido dentro del mismo turno del gate.
