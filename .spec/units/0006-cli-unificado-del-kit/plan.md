# Plan técnico — CLI unificado del kit: scripts top-level como sub-comandos del instalador

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de un `spec.md` aprobado (93 líneas, 15 CA verificables).

## Enfoque

El refactor separa dos responsabilidades que hoy conviven en `scripts/`: la
**lógica** (qué hace cada herramienta) pasa a módulos dentro del paquete
`installer/`, mientras que los **puntos de entrada conservados** quedan en
`scripts/` como shims ≤60 líneas que delegan al módulo correspondiente. Esto
permite que `installer/cli.py` extienda su `argparse` con tres subparsers
(`doctor`, `materialize`, `install-hook`) que llaman a la misma función `main`
que ya invocan los scripts originales — preservando exit codes y reporte
byte-a-byte (CA-02..CA-06, CA-13).

En paralelo, `installer/installer.py:_run_materializer` (líneas 216-228) deja
de pagar overhead de subproceso: importa `agents_main`, `commands_main` y
`skills_main` desde los módulos nuevos y los invoca en proceso, propagando el
returncode. El único `subprocess.run` que queda en el archivo es el de
`_install_pre_push_hook` (líneas 231-243), porque el bash no se mueve a
Python — el CLI del kit lo invoca por `subprocess.run(["bash", str(target /
"scripts/install_pre_push_hook.sh")])` con la misma firma que ya usa esa
función (CA-08, CA-14).

El shim patrón es uniforme: `from installer.<paquete>.<modulo> import main;
if __name__ == "__main__": sys.exit(main())` (los materializadores pasan
`sys.argv[1:]` para preservar `--repo-root`/`--check`/`--dry-run`). Cada shim
queda por debajo de 60 líneas (CA-07, CA-10, CA-11). `scripts/install_pre_push_hook.sh`
queda verbatim (CA-14).

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `installer/doctor.py` | crear | Mover `main`, `parse_args`, `Row`, `check_*`, `run_diagnostics`, `remediate_*`, `compute_exit_code`, `print_report` desde `scripts/kit_doctor.py`. Conservar firma `main(argv=None, run=None, confirm=None)`. |
| `installer/materializers/__init__.py` | crear | Paquete vacío (marca a `installer.materializers` como importable). |
| `installer/materializers/agents.py` | crear | Mover desde `scripts/materialize_claude_agents.py`. Conservar `sys.path.insert(0, SPEC_SCRIPTS_DIR)` + `import effort_profile as ep` (pregunta abierta #1, opción conservadora). Reescribir `MANIFEST_HEADER` y `_header_comment` para citar la nueva ruta `installer/materializers/agents.py`. |
| `installer/materializers/commands.py` | crear | Mover desde `scripts/materialize_claude_commands.py`. Reescribir import cruzado a `from installer.materializers.agents import MaterializeError, split_frontmatter` (pregunta abierta #3). |
| `installer/materializers/skills.py` | crear | Mover desde `scripts/materialize_claude_skills.py`. Mismo patrón byte-a-byte + SHA-256, sin imports cruzados. |
| `installer/cli.py` | modificar | Extender `build_parser` (línea 46) con subparsers `doctor`, `materialize`, `install-hook`; enrutar en `main` (línea 83). Mantener compat con flags existentes (DD-1). |
| `installer/installer.py` | modificar | `_run_materializer` (líneas 216-228) importa y llama en proceso. `_run_materializer` se elimina o se reduce a no-op si se invoca directo desde el loop de espejos. Constante `MIRROR_MATERIALIZERS` (líneas 75-79) se sustituye por tupla de callables o por dispatch por nombre. |
| `scripts/kit_doctor.py` | modificar | Sustituir por shim ≤60 líneas que importa `main` desde `installer.doctor` y lo invoca con `sys.argv[1:]`. |
| `scripts/materialize_claude_agents.py` | modificar | Sustituir por shim ≤60 líneas que importa `main` desde `installer.materializers.agents` y lo invoca con `sys.argv[1:]`. |
| `scripts/materialize_claude_commands.py` | modificar | Sustituir por shim ≤60 líneas análogo. |
| `scripts/materialize_claude_skills.py` | modificar | Sustituir por shim ≤60 líneas análogo. |
| `installer/tests/test_*.py`, `.spec/scripts/tests/test_kit_doctor.py`, `test_materialize_claude_*.py` | modificar | Migrar `sys.path.insert(0, str(SCRIPTS_DIR))` (3 archivos) a `from installer.doctor import …` / `from installer.materializers.<x> import …`. |

Total: 6 archivos a crear, 6 a modificar.

## Reutilización (no reinventar)

- `installer/cli.py:46` `build_parser()` — se extiende in-place; misma firma, mismo `RawDescriptionHelpFormatter`, mismo orden de flags top-level (`--target`, `--install`, `--force`, `--version`).
- `installer/cli.py:83` `main(argv)` — se extiende con un `elif s == "doctor"` etc.; mismo patrón de imprimir errores a `stderr` y devolver exit code.
- `installer/installer.py:216` `_run_materializer()` — se reemplaza por una función local `_run_in_process(target, which)` que importa `agents_main`/`commands_main`/`skills_main` y los invoca con `["--repo-root", str(target)]`.
- `installer/installer.py:231` `_install_pre_push_hook()` — se reutiliza tal cual; el CLI solo cambia la firma del proceso (`subprocess.run(["bash", str(target / "scripts/install_pre_push_hook.sh"), ...args])`) que ya usa esa función.
- `installer/installer.py:75` `MIRROR_MATERIALIZERS` — se sustituye por una tupla `(("agents", agents_main), ("commands", commands_main), ("skills", skills_main))`; el loop de espejos del `run_install` (líneas 471-478) itera sobre la tupla nueva y propaga returncode igual que antes.
- `manifest.py:REPO_ROOT`, `get_kit_version`, `ManifestError` — el sub-comando `doctor` no necesita nada de aquí; `--version` sigue importando lo mismo.
- Helpers internos de los scripts: `Row`, `MaterializeError`, `split_frontmatter`, `compute_exit_code`, etc. se mueven verbatim, sin renombrar.

## Decisiones de diseño

- **DD-1 — Forma del CLI: subparsers vs. flags de nivel superior.** Se eligen **argparse subparsers** (`parser.add_subparsers(dest="command")`), no flags nuevos a nivel raíz, porque los tres sub-comandos tienen sub-argumentos disjuntos (`materialize <target>` con `--check`/`--dry-run`; `install-hook pre-push` con `--uninstall`) y comparten poco con los flags actuales (`--target` no aplica a `doctor`, `--install` no aplica a `materialize`). Subparsers es además el patrón estándar de `argparse` y la verificación de CA-09 (`grep -E "doctor|materialize|install-hook"` sobre `--help`) lo presupone. Los flags de nivel superior se conservan para no romper consumidores que llaman `installer/cli.py --install`/`--version`/`--target` directamente (DD-3).

- **DD-2 — Shims ≤60 líneas en `scripts/` vs. eliminación de los scripts.** Se conservan los **4 scripts Python como shims** y el bash verbatim, en vez de eliminarlos, porque `installer/kit_manifest.yaml:177-186` sigue declarándolos como carga al destino, y porque `.claude/skills/kit-doctor/SKILL.md`, `AGENTS.md`, `.spec/scripts/pre-commit-gate.sh:41-43`, `.spec/scripts/guard_generated_paths.py:81-83` y el `comando_validacion` de U-0004 citan las rutas `scripts/kit_doctor.py` y `scripts/materialize_claude_*.py`. Eliminarlos rompería esos contratos externos sin ganancia real (el bytecode ya se importa desde `installer.*`; el shim solo paga una línea de `sys.exit(main())`). El bash queda verbatim por la misma razón: `_install_pre_push_hook` y ahora `installer/cli.py install-hook` lo invocan por subproceso.

- **DD-3 — Compatibilidad con consumidores externos.** El CLI conserva `--version`, `--target`, `--install`, `--force` y la semántica de "sin flags = verificar" exactamente como antes (CA-09 verificable con la ayuda actual de `git log -p installer/cli.py`); los sub-comandos son **añadidos**, no sustituciones. El shim `scripts/kit_doctor.py` conserva la signatura `main(argv=None, run=None, confirm=None)` (los dos últimos, opcionales, son los inyecciones que usan los tests). Los materializadores preservan `--repo-root`/`--check`/`--dry-run` en el mismo orden; el refactor no reordena flags. Esto cubre la pregunta abierta #2 ("firma del materializador preservada").

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | Módulos nuevos: mover lógica a `installer/doctor.py` y `installer/materializers/{agents,commands,skills}.py` | `installer/doctor.py`, `installer/materializers/__init__.py`, `installer/materializers/agents.py`, `installer/materializers/commands.py`, `installer/materializers/skills.py` | — | estandar |
| G2 | Extensión del CLI: `build_parser` con subparsers + dispatch en `main` | `installer/cli.py` | G1 | estandar |
| G3 | Shims en `scripts/` (4 Python ≤60 líneas) + bash verbatim | `scripts/kit_doctor.py`, `scripts/materialize_claude_agents.py`, `scripts/materialize_claude_commands.py`, `scripts/materialize_claude_skills.py`, `scripts/install_pre_push_hook.sh` | G1 | estandar |
| G4 | Migración de `_run_materializer` en `installer.py` + migración de tests con `sys.path.insert` | `installer/installer.py`, `installer/tests/test_*.py`, `.spec/scripts/tests/test_kit_doctor.py`, `test_materialize_claude_agents.py`, `test_materialize_claude_commands.py` | G1, G2, G3 | estandar |

G1 no tiene dependencias internas; G2, G3 y G4 parten de G1 (necesitan los
módulos para enrutar / delegar / importar). G2 y G3 son paralelos entre sí:
tocan archivos disjuntos (`installer/cli.py` vs. archivos bajo `scripts/`).
G4 depende de los tres: la migración del `_run_materializer` solo tiene
sentido con los módulos ya en sitio y los tests solo pasan una vez los shims
existan.

## Riesgos y mitigaciones

- **Riesgo:** el `sys.path.insert(0, SPEC_SCRIPTS_DIR)` dentro de `installer/materializers/agents.py:46-47` quede oculto dentro del paquete y rompa tests que importan `effort_profile` desde otra raíz. **Mitigación:** la pregunta abierta #1 fija que se conserva el patrón actual sin cambio; verificación adicional en `test_materialize_claude_agents.py` migrado: añadir `assert str(SPEC_SCRIPTS_DIR.resolve()) in sys.path` para que el contrato sea explícito.

- **Riesgo:** los 4 shims en `scripts/` superen 60 líneas por accidente (boilerplate de docstring + `argparse` residual). **Mitigación:** el patrón es uniforme y minimal — header docstring de 3 líneas + `from installer.X import main` + `if __name__ == "__main__": sys.exit(main(sys.argv[1:]))` para los materializadores, `main()` (sin args) para `kit_doctor`. CA-07 verifica con `wc -l`; el sub-agent que implemente G3 debe atenerse al patrón sin re-importar `argparse` en el shim.

- **Riesgo:** cambiar `MIRROR_MATERIALIZERS` de tupla de strings a tupla de callables rompa el `_run_materializer` actual y deje `--install` con un `AttributeError` en runtime. **Mitigación:** el refactor se hace atómicamente en `installer/installer.py:471-478`; el loop pasa de `for script_rel in MIRROR_MATERIALIZERS: code = _run_materializer(script_rel, git_root)` a `for which, fn in MIRROR_MATERIALIZERS: code = fn(["--repo-root", str(git_root)])`. Verificación: ejecutar `python3 installer/cli.py --install --force` en un clon de prueba antes de mergear.

- **Riesgo:** la migración de los tests rompa suites que usan fixtures con `sys.path.insert` por otros motivos (no solo los 3 nombrados en spec.md). **Mitigación:** `git grep -n "sys.path.insert.*SCRIPTS_DIR"` antes de migrar; cualquier hit fuera de los 3 archivos listados en CA-12 se trata como hallazgo nuevo y se documenta en `_estado.yaml > gates > implement > hallazgos` antes de tocar.

## Comando de validación

```
python3 -m pytest installer -q && python3 -m pytest .spec/scripts/tests -q
```

Las suites `installer/tests/` y `.spec/scripts/tests/` cubren los 4 módulos
nuevos (vía los tests migrados de `sys.path.insert`) y los CLI flags de
`installer/cli.py --help`. El `comando_validacion` declarado en
`_estado.yaml:16` no cambia: el refactor no introduce regresiones, solo
reorganiza entry points.