# Paquete de aprobación — CLI unificado del kit

> Modo semi-autonomo. Único checkpoint antes de implementar.

## Petición original

> "Verificación y unificación de scripts y modularización de los mismos convirtiéndolos en un cli, esto implica revisar las diferentes carpetas de scripts y unificarlos todos en el cli."

## Qué se va a hacer (2-5 líneas)

Unificar la superficie de ayuda del kit detrás de un solo binario
`installer/cli.py`. Los 4 scripts Python top-level
(`scripts/kit_doctor.py`, `scripts/materialize_claude_{agents,commands,skills}.py`)
se mueven como módulos al paquete `installer/`; `installer/cli.py` extiende
su `argparse` con sub-comandos `doctor`, `materialize`, `install-hook` que
delegan a esos módulos (los 3 materializadores, en proceso — sin
subprocess); los scripts top-level quedan como shims ≤60 líneas que
delegan al módulo nuevo; `installer/installer.py:_run_materializer` ya
no lanza subproceso para los espejos. `scripts/install_pre_push_hook.sh`
queda verbatim. `installer/kit_manifest.yaml:177-186` no cambia.

**Perfil de esfuerzo:** `estandar` (fuente única `.spec/perfiles.yaml`).

## Criterios de aceptación

| Id | Criterio | Tareas que lo cubren |
|---|---|---|
| CA-01 | 4 scripts Python se exponen como módulos bajo `installer/` con `main(argv)` | T1, T3, T4, T5 |
| CA-02 | `installer/cli.py doctor` invoca `installer.doctor.main([])` con mismo exit code | T7 |
| CA-03 | `installer/cli.py materialize <x> --check` mismo exit code que el shim `--check` | T8 |
| CA-04 | `installer/cli.py materialize <x>` regenera `.claude/<x>/` byte-idéntico | T8 |
| CA-05 | `installer/cli.py install-hook pre-push` mismo exit code que el bash en 4 casos | T9 |
| CA-06 | `installer/cli.py install-hook pre-push --uninstall` mismo exit code que el bash | T9 |
| CA-07 | Los 5 archivos originales siguen existiendo; shims ≤60 líneas; bash verbatim | T10, T11, T12, T13 |
| CA-08 | `_run_materializer` invoca materializadores en proceso (1 `subprocess.run` restante) | T15 |
| CA-09 | `--help` lista los sub-comandos nuevos | T6 |
| CA-10 | shim `scripts/kit_doctor.py` importa `main` desde `installer.doctor` | T10 |
| CA-11 | 3 shims `scripts/materialize_claude_*.py` importan `main` desde `installer.materializers.<x>` | T11, T12, T13 |
| CA-12 | `pytest installer -q` y `pytest .spec/scripts/tests -q` exit 0; tests migran de `sys.path.insert` a imports `installer.*` | T16, T17 |
| CA-13 | `python3 scripts/kit_doctor.py [--action <s>]` mantiene exit codes originales | T7, T10 |
| CA-14 | `scripts/install_pre_push_hook.sh` verbatim; CLI lo invoca por `subprocess.run(["bash", ...])` | T9, T14 |
| CA-15 | `kit_manifest.yaml:177-186` mantiene las 5 entradas; módulos nuevos NO aparecen en el manifiesto | T2, T14 |

## Alcance del cambio

- **Archivos a crear (5):** `installer/doctor.py`, `installer/materializers/__init__.py`, `installer/materializers/agents.py`, `installer/materializers/commands.py`, `installer/materializers/skills.py`.
- **Archivos a modificar (6):** `installer/cli.py` (subparsers + dispatch), `installer/installer.py` (`_run_materializer` → invocación en proceso), `scripts/kit_doctor.py` y 3 materializadores (shims ≤60 líneas), `scripts/install_pre_push_hook.sh` (verificación de verbatim, no se toca).
- **Tests a migrar (4 archivos):** `installer/tests/test_cli.py`, `.spec/scripts/tests/test_kit_doctor.py`, `test_materialize_claude_agents.py`, `test_materialize_claude_commands.py` — sustituir `sys.path.insert(0, str(SCRIPTS_DIR))` por imports `installer.doctor` / `installer.materializers.<x>`. `test_materialize_claude_skills.py` también usa ese patrón.
- **Fuera de alcance (declarado):** `scripts/mcp-pce.sh`, scripts de `.spec/scripts/`, `installer/kit_manifest.yaml:177-186`, contenido de `scripts/install_pre_push_hook.sh`, referencias externas en `.claude/skills/kit-doctor/SKILL.md`, `AGENTS.md`, `.spec/scripts/pre-commit-gate.sh:41-43`, `.spec/scripts/guard_generated_paths.py:81-83`, `comando_validacion` de U-0004.
- **Comando de validación:** `python3 -m pytest installer -q && python3 -m pytest .spec/scripts/tests -q`

## Historial de gates

| Fase | Veredicto | Iteraciones | Hallazgos resueltos | Hallazgos sin resolver | Governance |
|---|---|---|---|---|---|
| spec | refinado | 1 | 2 | 0 (2 baja anotados) | si (centinela [ninguna-aplicable] mantenido con precedente U-0004/0005/0009) |
| plan | aprobado | 1 | 0 | 0 | si (centinela [ninguna-aplicable]) |
| tasks | aprobado | 1 | 0 | 0 | si (centinela [ninguna-aplicable]) |

**Correcciones más relevantes:** ninguna en plan ni tasks. La fase spec dejó
2 hallazgos de severidad baja (reporte fila-por-fila no determinista entre
corridas en CA-02; locator cosmético en CA-08), anotados pero no
bloqueantes — el exit code es el discriminador vertebral y los SHA-256 por
archivo son el segundo.

## Suposiciones tomadas (requieren confirmación)

| # | Pregunta | Suposición tomada | Impacto si es errada |
|---|---|---|---|
| 1 | ¿Sustituir `sys.path.insert(0, SPEC_SCRIPTS_DIR)` + `import effort_profile as ep` en `installer/materializers/agents.py` por import relativo? | Conservar el patrón actual sin cambio (pregunta abierta #1 del spec). | Si se quiere romper la dependencia de `sys.path`, hay que registrar `installer/materializers/` como paquete con `__init__.py` que reexporte `effort_profile` — trabajo extra. La suposición conservadora es lo que el plan ejecuta. |
| 2 | ¿La firma del materializador cambia al invocarse en proceso? | Firma y orden de `--check`/`--dry-run`/`--repo-root` se preservan; no se añaden flags implícitos. | Si se quería reordenar flags, queda para una unidad posterior. CA-02..CA-06, CA-13 presuponen exit codes idénticos. |
| 3 | ¿Cómo reescribir el import cruzado `materialize_claude_commands` ↔ `materialize_claude_agents`? | Reescribir a `from installer.materializers.agents import MaterializeError, split_frontmatter` (pregunta abierta #3 del spec). | Si se conservaba `from materialize_claude_agents import ...` (ruta `scripts/`), CA-01 fallaría — `scripts/` queda como shim y no expone los símbolos necesarios. |

## Riesgos aceptados

- **R1:** `sys.path.insert(0, SPEC_SCRIPTS_DIR)` oculto dentro de `installer/materializers/agents.py:46-47` puede romper tests que importan `effort_profile` desde otra raíz. **Mitigación:** añadir `assert str(SPEC_SCRIPTS_DIR.resolve()) in sys.path` en el test migrado de `test_materialize_claude_agents.py` para que el contrato sea explícito.
- **R2:** los 4 shims en `scripts/` superan 60 líneas por boilerplate accidental. **Mitigación:** patrón uniforme y minimal en T10-T13 (header docstring de 3 líneas + `from installer.X import main` + `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))` para los materializadores, `main()` para `kit_doctor`); CA-07 verifica con `wc -l`.
- **R3:** cambio de `MIRROR_MATERIALIZERS` de tupla de strings a tupla de callables rompe `_run_materializer`. **Mitigación:** refactor atómico en `installer/installer.py:471-478`; loop pasa de `for script_rel in MIRROR_MATERIALIZERS: code = _run_materializer(script_rel, git_root)` a `for which, fn in MIRROR_MATERIALIZERS: code = fn(["--repo-root", str(git_root)])`. Verificación: `python3 installer/cli.py --install --force` en un clon de prueba antes de mergear (T15).
- **R4:** la migración de los tests rompe suites que usan fixtures con `sys.path.insert` por otros motivos. **Mitigación:** `git grep -n "sys.path.insert.*SCRIPTS_DIR"` antes de migrar; cualquier hit fuera de los 4 archivos listados en T16 se trata como hallazgo nuevo y se documenta en `_estado.yaml > gates > implement > hallazgos` antes de tocar.

## Gobernanza aplicada

`governance_refs: [ninguna-aplicable]` — el kit no tiene superficie de
gobernanza propia. `pce-mcp` aparece solo como carga que se instala en un
destino (cargado por `.mcp.json` / `opencode.jsonc`); no se ejecuta contra
este repo. El centinela se mantiene con precedente U-0001, U-0003, U-0004,
U-0009. El lente L4 corrió con la triada literal como red de seguridad:
`AGENTS.md` y `.mcp.json` están en el repo pero como **carga al destino**
(ver `installer/kit_manifest.yaml:159-164`), no como superficie de
gobernanza del kit — la prosa `AGENTS.md` se sobreescribe en cada
`--install` y `.mcp.json`/`opencode.jsonc` se fusionan con marcador
(`_sdd_kit: true`). Ninguno declara MCP/gob del kit sobre sí mismo.

## Decisión

- [x] **Aprobar** — implementar según `tasks.md`. Decisión tomada por el
      agente en modo desatendido, conforme a la instrucción del operador
      ("**NO pauses** para confirmación. Marca el paquete como aprobado ...
      y avanza `fase: implement`").