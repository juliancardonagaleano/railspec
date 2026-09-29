# Spec — CLI unificado del kit: scripts top-level como sub-comandos del instalador

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

El kit tiene hoy **dos interfaces paralelas** para el operador: `installer/cli.py` (`--version`, `--target`, `--install`, `--force`, verificar implícito; 103 líneas, contrato público estable por U-0002) y 5 ejecutables top-level bajo `scripts/` (`kit_doctor.py`, `materialize_claude_{agents,commands,skills}.py`, `install_pre_push_hook.sh`), cada uno con su propio `argparse`, `--help` y códigos de salida.

Esa duplicación acarrea tres problemas concretos, todos verificables en el repo:

1. **Superficie de ayuda fragmentada.** El operador que diagnostica el entorno tiene que recordar `python3 scripts/kit_doctor.py`; el que materializa espejos, `python3 scripts/materialize_claude_<target>.py`; el que instala el hook, `bash scripts/install_pre_push_hook.sh`. No hay un solo `installer/cli.py --help` que los liste.
2. **El instalador los lanza por subproceso.** `installer/installer.py:_run_materializer` (líneas 216-228) corre los 3 materializadores con `subprocess.run([sys.executable, "-B", script, "--repo-root", target])`; `_install_pre_push_hook` (líneas 231-243) lanza el bash con `subprocess.run(["bash", hook_installer])`. Cada `--install` paga arranque de intérprete o shell, parseo de `argparse` y re-importación de `installer/manifest.py` desde cero.
3. **Las pruebas existentes dependen de un truco de `sys.path`.** `test_kit_doctor.py:26-29`, `test_materialize_claude_agents.py:25-29`, `test_materialize_claude_commands.py:30-32` hacen `sys.path.insert(0, str(SCRIPTS_DIR))` para importar los scripts como módulos. Eso ata su layout a `scripts/` y bloquea una reorganización limpia.

Si no se resuelve: cada `--install` seguirá pagando overhead de subproceso, la ayuda seguirá fragmentada en 5 `--help` distintos, y el `kit-doctor` SKILL.md seguirá documentando una ruta que el kit podría dejar de cargar al destino.

## Resultado esperado

El operador del kit tiene un solo binario, `installer/cli.py`, cuya ayuda lista todas las operaciones del kit: las de siempre (`--version`, `--target`, `--install`, `--force`) y tres familias nuevas de sub-comandos:

- `doctor [--action <sujeto>]` — diagnóstico y remediación del entorno agéntico (sub-comando de `scripts/kit_doctor.py`).
- `materialize <agents|commands|skills> [--check] [--dry-run]` — regenerar los espejos `.claude/{agents,commands,skills}/` (sub-comandos de los 3 materializadores).
- `install-hook pre-push [--uninstall]` — instalar o retirar el gancho de pre-push del worktree (sub-comando de `scripts/install_pre_push_hook.sh`).

Los 4 scripts Python y el script bash siguen existiendo en `scripts/` (el manifiesto los sigue declarando en `installer/kit_manifest.yaml:177-186`); los 4 Python son shims que delegan a sus módulos homónimos bajo `installer/`, y el bash queda verbatim. `installer/installer.py` ya no lanza subproceso para los espejos — los invoca en proceso.

## Alcance

**Incluye:**

- Exponer los 4 scripts Python como módulos bajo el paquete `installer/`:
  - `scripts/kit_doctor.py` → `installer/doctor.py` (función `main(argv)` con la misma signatura).
  - `scripts/materialize_claude_agents.py` → `installer/materializers/agents.py`.
  - `scripts/materialize_claude_commands.py` → `installer/materializers/commands.py`.
  - `scripts/materialize_claude_skills.py` → `installer/materializers/skills.py`.
- Añadir 3 sub-comandos al CLI existente: `doctor`, `materialize`, `install-hook`. Cada uno delega al módulo `installer.*` correspondiente (los Python) o a `bash scripts/install_pre_push_hook.sh` (el bash).
- Reemplazar los 4 scripts Python en `scripts/` por shims de menos de 60 líneas que importan `main` del módulo nuevo y lo invocan con `sys.argv[1:]`.
- Migrar `installer/installer.py:_run_materializer` para invocar los 3 materializadores en proceso (sin `subprocess.run`); el bash del hook sigue invocándose por subproceso (mismo patrón que ya usa `_install_pre_push_hook`).

**No incluye (fuera de alcance):**

- Tocar `scripts/mcp-pce.sh` — launcher invocado por `.mcp.json` del destino (U-0007); renombrarlo o moverlo rompería ese contrato.
- Tocar los scripts de `.spec/scripts/` — andamiaje de runtime del SDD (gate, validate, supervise); no son del CLI del kit.
- Cambiar `installer/kit_manifest.yaml:177-186` — las 5 entradas de `scripts/` siguen declarando carga hacia el destino. Los nuevos módulos bajo `installer/` no se declaran en el manifiesto (código interno del kit, no carga, mismo patrón que `installer/installer.py`).
- Cambiar `installer/installer.py:81` (`PRE_PUSH_HOOK_INSTALLER`) ni `scripts/install_pre_push_hook.sh` — el bash queda verbatim y el CLI lo invoca por `subprocess.run(["bash", ...])` igual que ya hace `_install_pre_push_hook`.
- Migrar los tests que hacen `sys.path.insert(0, str(SCRIPTS_DIR))` para importar los scripts — housekeeping de la fase de implementación, cubierto por CA-12.
- Cambiar `.claude/skills/kit-doctor/SKILL.md`, `AGENTS.md`, `.spec/scripts/pre-commit-gate.sh:41-43`, `.spec/scripts/guard_generated_paths.py:81-83` ni `comando_validacion` de U-0004 — los shims preservan las rutas que esos archivos mencionan.

## Criterios de aceptación

- [ ] CA-01 — Los 4 scripts Python a unificar exponen su entry point como módulo bajo `installer/`: `scripts/kit_doctor.py` → `installer/doctor.py`; `scripts/materialize_claude_{agents,commands,skills}.py` → `installer/materializers/{agents,commands,skills}.py`. Cada módulo expone `main(argv)` con la misma signatura que el script original (verificable con `grep -n "^def main" installer/doctor.py installer/materializers/{agents,commands,skills}.py` retornando 4 matches).

- [ ] CA-02 — `python3 installer/cli.py doctor` invoca `installer.doctor.main([])` y termina con el mismo exit code que `python3 scripts/kit_doctor.py` en el mismo entorno (verificable ejecutando ambos y comparando; el reporte fila-por-fila y los exit codes 0/1/2/3/4 deben coincidir exactamente).

- [ ] CA-03 — `python3 installer/cli.py materialize <agents|commands|skills> --check` termina con el mismo exit code que `python3 scripts/materialize_claude_<target>.py --check` sobre el mismo árbol (verificable ejecutando ambos contra el repo y comparando).

- [ ] CA-04 — `python3 installer/cli.py materialize <agents|commands|skills>` (sin flag) regenera el espejo `.claude/<target>/` con bytes idénticos a `python3 scripts/materialize_claude_<target>.py --repo-root .` (verificable con `diff -r .claude/<target>/ <snapshot-antes>` o SHA-256 antes/después; el `materialized_at` del manifiesto puede diferir por la ventana de idempotencia de 60 s, pero los SHA-256 por archivo deben coincidir).

- [ ] CA-05 — `python3 installer/cli.py install-hook pre-push` y `bash scripts/install_pre_push_hook.sh` terminan con el mismo exit code sobre un worktree de prueba en los 4 casos (ausente, ajeno, marcado-mío, re-instalar; el exit 0/1/2 debe coincidir en cada caso).

- [ ] CA-06 — `python3 installer/cli.py install-hook pre-push --uninstall` y `bash scripts/install_pre_push_hook.sh --uninstall` terminan con el mismo exit code bajo las mismas 4 condiciones (incluyendo el caso "no hay hook instalado → exit 0").

- [ ] CA-07 — Los 5 archivos originales siguen existiendo en disco en sus rutas declaradas en `installer/kit_manifest.yaml:177-186`. Los 4 scripts Python son shims de menos de 60 líneas que delegan al módulo `installer.*`; `scripts/install_pre_push_hook.sh` queda verbatim byte-a-byte (verificable con `git diff scripts/install_pre_push_hook.sh` retornando vacío y `wc -l scripts/kit_doctor.py scripts/materialize_claude_*.py` retornando ≤ 60 por archivo para los 4 shims).

- [ ] CA-08 — `installer/installer.py:MIRROR_MATERIALIZERS` ya no lanza subproceso para los 3 materializadores: importa `agents_main`, `commands_main`, `skills_main` desde `installer.materializers.*` y los invoca en proceso, pasando `--repo-root <target>` y propagando su returncode. Verificable con `grep -n "subprocess\.run" installer/installer.py` después del cambio retornando exactamente 1 match (el de `_install_pre_push_hook`, que sigue siendo subproceso).

- [ ] CA-09 — `python3 installer/cli.py --help` lista, además de `--version`, `--target`, `--install`, `--force`, los sub-comandos `doctor`, `materialize <agents|commands|skills>`, `install-hook <pre-push>` con descripción y flags. Verificable con `python3 installer/cli.py --help 2>&1 | grep -E "doctor|materialize|install-hook"` retornando ≥ 3 líneas.

- [ ] CA-10 — El shim `scripts/kit_doctor.py` contiene `from installer.doctor import main` (o `import installer.doctor`) y un wrapper `if __name__ == "__main__": sys.exit(main())`. Verificable con `grep -n "from installer\.doctor\|import installer\.doctor" scripts/kit_doctor.py` retornando ≥ 1 match y `grep -n "sys\.exit(main())" scripts/kit_doctor.py` retornando ≥ 1 match.

- [ ] CA-11 — Los 3 shims `scripts/materialize_claude_{agents,commands,skills}.py` siguen el mismo patrón: cada uno importa `main` desde `installer.materializers.<x>` y lo invoca con `sys.argv[1:]`. Verificable con `grep -n "from installer\.materializers" scripts/materialize_claude_*.py` retornando exactamente 1 match por archivo (3 totales).

- [ ] CA-12 — Las suites `python3 -m pytest installer -q` y `python3 -m pytest .spec/scripts/tests -q` terminan con exit code 0 después del refactor (cero regresiones; los tests que importan scripts vía `sys.path.insert(0, str(SCRIPTS_DIR))` se migran a importar los módulos `installer.*` como parte de la fase de implementación — sin esta migración, CA-12 falla).

- [ ] CA-13 — La invocación original `python3 scripts/kit_doctor.py [--action <sujeto>]` sigue funcionando con los mismos exit codes sobre el mismo entorno (el shim delega sin modificar la semántica de `main`). Verificable comparando el exit code de `python3 scripts/kit_doctor.py --action gitnexus` antes y después del cambio — ambos deben ser idénticos.

- [ ] CA-14 — `scripts/install_pre_push_hook.sh` queda verbatim (sin cambios de bytes). El CLI `installer/cli.py install-hook pre-push` lo invoca por `subprocess.run(["bash", str(target / "scripts/install_pre_push_hook.sh"), ...])` con la misma firma que ya usa `installer/installer.py:_install_pre_push_hook()`. Verificable con `git diff scripts/install_pre_push_hook.sh` retornando vacío.

- [ ] CA-15 — `installer/kit_manifest.yaml:177-186` mantiene las 5 entradas de carga para `scripts/kit_doctor.py`, `scripts/materialize_claude_*.py`, `scripts/install_pre_push_hook.sh` (sin cambios de bytes en esa región). Los nuevos módulos `installer/doctor.py` y `installer/materializers/{agents,commands,skills}.py` no aparecen en el manifiesto (código interno del kit, no carga al destino; mismo patrón que `installer/installer.py`, `installer/verifier.py`, `installer/manifest.py`). Verificable con `grep -E "^  - source: installer/(doctor|materializers)" installer/kit_manifest.yaml` retornando sin matches.

## Governance aplicable

Sin superficie de gobernanza propia del kit; `pce-mcp` no se ejecuta contra este repo (precedente U-0001:78-97, U-0003:321, U-0004:25-31, U-0009). El spec no introduce ninguna decisión que pueda caer bajo un artefacto recuperable.

| Tipo | Id | Cómo aplica / restringe |
|---|---|---|
| — | ninguna-aplicable | Sin superficie de gobernanza propia; ver precedentes U-0001..U-0009 |

## Preguntas abiertas

- **Carga de `effort_profile` desde el materializador de agentes.** `scripts/materialize_claude_agents.py:46-47` hace `sys.path.insert(0, str(SPEC_SCRIPTS_DIR))` e `import effort_profile as ep`. Tras la mudanza a `installer/materializers/agents.py`, ese patrón queda dentro del paquete — ¿se conserva el `sys.path.insert` o se sustituye por un import relativo? **Suposición conservadora:** se conserva el patrón actual (mismo `sys.path.insert`, mismo `import effort_profile as ep`) — opción de cambio mínimo, respeta el contrato de "mismo perfil que hoy", y un cambio a import relativo obligaría a registrar `installer/materializers/` como paquete con su propio `__init__.py`.
- **`_run_materializer` → invocación en proceso, firma del materializador.** El refactor cambia `subprocess.run([sys.executable, "-B", script, "--repo-root", target])` por `agents_main(["--repo-root", str(target)])`. **Suposición conservadora:** la firma y el orden de argumentos del materializador se preservan — no se añaden flags implícitos; cualquier reordenamiento de `--check`/`--dry-run`/`--repo-root` queda para una unidad posterior.
- **Import cruzado `materialize_claude_commands` ↔ `materialize_claude_agents`.** `scripts/materialize_claude_commands.py:46` importa `MaterializeError, split_frontmatter` desde `materialize_claude_agents`. **Suposición conservadora:** se reescribe a `from installer.materializers.agents import MaterializeError, split_frontmatter` como parte del refactor (no se conserva la importación entre scripts de `scripts/`).