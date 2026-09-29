# Tareas — CLI unificado del kit: scripts top-level como sub-comandos del instalador

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**: el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en ninguna tarea.

## Pendientes

### Grupo G1 — Módulos nuevos en `installer/`

- [ ] T1 — Crear `installer/doctor.py` moviendo `main`, `parse_args`, `Row`, `check_*`, `run_diagnostics`, `remediate_*`, `compute_exit_code`, `print_report` desde `scripts/kit_doctor.py`. Conservar firma `main(argv=None, run=None, confirm=None)`. Reescribir docstring de módulo y referencias a `scripts/kit_doctor.py` que aparezcan en mensajes (`COMMAND_CITATIONS` queda igual). · archivos: `installer/doctor.py` · cubre: CA-01

- [ ] T2 — Crear `installer/materializers/__init__.py` (paquete vacío) y verificar que los módulos bajo él NO aparecen en `installer/kit_manifest.yaml` (mismo patrón que `installer/installer.py`). · archivos: `installer/materializers/__init__.py` · cubre: CA-15

- [ ] T3 — Crear `installer/materializers/agents.py` moviendo desde `scripts/materialize_claude_agents.py`. Conservar `sys.path.insert(0, SPEC_SCRIPTS_DIR)` + `import effort_profile as ep` (pregunta abierta #1). Reescribir `MANIFEST_HEADER` y `_header_comment` para citar `installer/materializers/agents.py`. Conservar firma `main(argv: list[str] | None = None) -> int` y flags `--check`/`--dry-run`/`--repo-root`. · archivos: `installer/materializers/agents.py` · depende de: T2 · cubre: CA-01

- [ ] T4 — Crear `installer/materializers/commands.py` moviendo desde `scripts/materialize_claude_commands.py`. Reescribir import cruzado (línea 46 del script original) a `from installer.materializers.agents import MaterializeError, split_frontmatter` (pregunta abierta #3). Conservar firma y flags. · archivos: `installer/materializers/commands.py` · depende de: T2, T3 · cubre: CA-01

- [ ] T5 — Crear `installer/materializers/skills.py` moviendo desde `scripts/materialize_claude_skills.py`. Sin imports cruzados. Conservar firma y flags. · archivos: `installer/materializers/skills.py` · depende de: T2 · cubre: CA-01

### Grupo G2 — Extensión del CLI

- [ ] T6 — Extender `installer/cli.py:build_parser` con `parser.add_subparsers(dest="command", required=False)` y tres subparsers: `doctor`, `materialize` (con choices `agents|commands|skills`), `install-hook` (con choices `pre-push`). Mantener flags top-level `--version`, `--target`, `--install`, `--force` intactos (DD-1, DD-3). Verificar CA-09 con `python3 installer/cli.py --help`. · archivos: `installer/cli.py` · depende de: T1, T3, T4, T5 · cubre: CA-09

- [ ] T7 — Añadir rama en `installer/cli.py:main` para `args.command == "doctor"` que llama `installer.doctor.main(args.doctor_args)` y devuelve su returncode. El shim `scripts/kit_doctor.py` delega al mismo `main`, así que el contrato es simétrico (CA-13). · archivos: `installer/cli.py` · depende de: T1, T6 · cubre: CA-02, CA-13

- [ ] T8 — Añadir rama en `installer/cli.py:main` para `args.command == "materialize"` que dispatch por `args.materialize_target` a `agents_main`/`commands_main`/`skills_main` con `args.materialize_args`. Conservar orden de flags (pregunta abierta #2). · archivos: `installer/cli.py` · depende de: T3, T4, T5, T6 · cubre: CA-03, CA-04

- [ ] T9 — Añadir rama en `installer/cli.py:main` para `args.command == "install-hook"` que invoca `subprocess.run(["bash", str(target / "scripts/install_pre_push_hook.sh"), *args.hook_args])` con la misma firma que `_install_pre_push_hook` (línea 233). Soportar `--uninstall`. · archivos: `installer/cli.py` · depende de: T6 · cubre: CA-05, CA-06, CA-14

### Grupo G3 — Shims en `scripts/`

- [ ] T10 — Sustituir `scripts/kit_doctor.py` por shim ≤60 líneas: `from installer.doctor import main; if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`. Conservar shebang, docstring de una línea y exit code. · archivos: `scripts/kit_doctor.py` · depende de: T1 · cubre: CA-07, CA-10, CA-13

- [ ] T11 — Sustituir `scripts/materialize_claude_agents.py` por shim ≤60 líneas: `from installer.materializers.agents import main; if __name__ == "__main__": sys.exit(main(sys.argv[1:]))`. · archivos: `scripts/materialize_claude_agents.py` · depende de: T3 · cubre: CA-07, CA-11

- [ ] T12 — Sustituir `scripts/materialize_claude_commands.py` por shim ≤60 líneas análogo. · archivos: `scripts/materialize_claude_commands.py` · depende de: T4 · cubre: CA-07, CA-11

- [ ] T13 — Sustituir `scripts/materialize_claude_skills.py` por shim ≤60 líneas análogo. · archivos: `scripts/materialize_claude_skills.py` · depende de: T5 · cubre: CA-07, CA-11

- [ ] T14 — Verificar que `scripts/install_pre_push_hook.sh` queda verbatim (sin cambios de bytes). No tocar. Verificación: `git diff scripts/install_pre_push_hook.sh` retorna vacío (CA-14). Verificar también que `installer/kit_manifest.yaml:177-186` no cambia. · archivos: `scripts/install_pre_push_hook.sh`, `installer/kit_manifest.yaml` · cubre: CA-14, CA-15

### Grupo G4 — Tests + migración de `_run_materializer`

- [ ] T15 — Migrar `installer/installer.py:_run_materializer` (líneas 216-228) para importar `agents_main`, `commands_main`, `skills_main` desde `installer.materializers.*` y llamarlos en proceso con `["--repo-root", str(git_root)]`. Sustituir `MIRROR_MATERIALIZERS` (líneas 75-79) por tupla de `(which, callable)`. Tras el cambio, `grep -n "subprocess.run" installer/installer.py` retorna exactamente 1 match (CA-08). · archivos: `installer/installer.py` · depende de: T3, T4, T5 · cubre: CA-08

- [ ] T16 — Migrar `sys.path.insert(0, str(SCRIPTS_DIR))` en `installer/tests/test_*.py` y `.spec/scripts/tests/test_kit_doctor.py`, `test_materialize_claude_agents.py`, `test_materialize_claude_commands.py` a importar `installer.doctor` / `installer.materializers.<x>`. Verificación: `git grep -n "sys.path.insert.*SCRIPTS_DIR"` retorna 0 hits (riesgo documentado en plan). · archivos: `installer/tests/test_kit_doctor.py`, `.spec/scripts/tests/test_kit_doctor.py`, `test_materialize_claude_agents.py`, `test_materialize_claude_commands.py` · depende de: T1, T3, T4, T5 · cubre: CA-12

- [ ] T17 — Correr `python3 -m pytest installer -q && python3 -m pytest .spec/scripts/tests -q` y verificar exit code 0 (cero regresiones; CA-12). Cualquier fallo se documenta en `_estado.yaml > gates > implement > hallazgos` antes de continuar. · archivos: — · depende de: T6..T16 · cubre: CA-12

## Validación final

- [ ] Ejecutar comando de validación (ver `plan.md`)
- [ ] Verificar criterios de aceptación de `spec.md` (uno por uno, por id): CA-01..CA-15
- [ ] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto registrado
- [ ] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`

## Notas de implementación

Detalles que surjan al implementar (decisiones puntuales, desvíos del plan).