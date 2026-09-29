# Tareas — Fiabilidad de gates de gobernanza

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**: el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en
> ninguna tarea.

## Pendientes

### G1 — Auditor como script (sin deps, complejo)

- [x] T1 — Crear `.spec/scripts/check_governance_surface.py` con CLI (`argparse`):
      argumentos `--unit <ruta-unidad>`, `--spec <ruta-spec>`, `--filesystem-root <ruta>`;
      `--help` muestra el contrato del script (qué hace, qué falla, qué retorna).
      Estructura: funciones puras (`compute_fs_hash`, `read_configuration_contract`,
      `find_declared_governance`, `check`) + `main()` que las orquesta y emite
      reporte por stdout/stderr. Cubre CA-14. · archivos: `.spec/scripts/check_governance_surface.py` · cubre: CA-14
- [x] T2 — Implementar `compute_fs_hash(filesystem_root) -> str` en el script: usa
      `find` para listar archivos del filesystem excluyendo `.git/`, `.spec/`,
      `.gitnexus/`, ordena estable, calcula `sha256sum` y devuelve el hash agregado.
      Puro (recibe `Path`, no usa globals). Reutiliza `pathlib.Path` y `subprocess`
      sin side effects externos. Cubre CA-04 (input), CA-13. · archivos: `.spec/scripts/check_governance_surface.py` · cubre: CA-04, CA-13
- [x] T3 — Implementar `read_configuration_contract(spec_path) -> list[str]`:
      lee el spec como texto, busca una sección dedicada (e.g., bloque
      `configuration_contract:` o lista explícita de rutas gobernadas), devuelve
      las rutas como strings relativas a la raíz del repo. Puro. Cubre CA-01,
      CA-02. · archivos: `.spec/scripts/check_governance_surface.py` · cubre: CA-01, CA-02
- [x] T4 — Implementar `find_declared_governance(filesystem_root) -> list[str]`:
      busca en la raíz del repo archivos que declaren `mcpServers`, `mcp`,
      `pce-mcp` o gobieranza explícita, usando `grep -rln` o equivalente Python
      sobre los formatos JSON/JSONC/MD. Devuelve rutas relativas. Excluye
      `.git/`, `.spec/`, `.gitnexus/`. Puro. Cubre CA-06, CA-07. · archivos: `.spec/scripts/check_governance_surface.py` · cubre: CA-06, CA-07
- [x] T5 — Implementar `check(unit, spec, filesystem_root) -> Report` que orquesta
      T2..T4: (a) lee `configuration_contract` del spec; (b) verifica que cada
      ruta declarada exista en el filesystem (CA-01..CA-02); (c) cruza la lista
      declarada contra `find_declared_governance`: cualquier ruta declarada
      debe aparecer como encontrada, cualquier archivo que declare MCP/gob y
      no esté en la lista declarada causa `superficie-no-declarada` (CA-03);
      (d) calcula `fs_hash` actual; (e) emite un Report estructurado con
      `verdict`, `causes` (lista), `declared`, `found`, `fs_hash`. El main()
      retorna 0 si verdict es "ok", != 0 en otro caso (con causas en stderr).
      · archivos: `.spec/scripts/check_governance_surface.py` · cubre: CA-03
- [x] T6 — Tests de las funciones puras en
      `.spec/scripts/tests/test_check_governance_surface.py` con `tmp_path`:
      `u0003_ca01_*` (filesystem con lista declarada y todos los archivos
      presentes → exit 0); `u0003_ca02_*` (ruta declarada ausente → exit != 0
      causa `configuration_contract-inconsistente`); `u0003_ca03_*`
      (`opencode.jsonc` declarando `pce-mcp` sin estar en la lista → exit != 0
      causa `superficie-no-declarada`); `u0003_ca06_*` (filesystem limpio →
      exit 0 sin superficie declarada); `u0003_ca07_*` (filesystem con
      `opencode.jsonc` declarando `pce-mcp` → exit != 0); `u0003_ca15_*`
      (testeable con `tmp_path`, sin I/O fuera del argumento). · archivos: `.spec/scripts/tests/test_check_governance_surface.py` · cubre: CA-01, CA-02, CA-03, CA-06, CA-07, CA-15
- [x] T7 — Tests de integración en
      `.spec/scripts/tests/test_check_governance_integration.py`: `u0003_ca21_*`
      (smoke test del script contra el filesystem real del kit — verifica que
      `AGENTS.md`, `.mcp.json` y `opencode.jsonc` están todos en el resultado
      de `find_declared_governance` y que la lista declarativa del spec de
      esta unit los cubre sin escalar). Si el kit no los tiene a los tres,
      el test hace `pytest.skip` con motivo claro. `u0003_ca16a_*` (smoke
      test del gate de spec de la unit 0001 cerrada — corre el gate con la
      lógica inline (commit anterior a T8), guarda el veredicto; corre el
      gate con el script nuevo (commit posterior a T8), compara veredictos;
      deben ser idénticos para que el reemplazo no introduzca regresión).
      Cubre CA-16a, CA-21. · archivos: `.spec/scripts/tests/test_check_governance_integration.py` · cubre: CA-16a, CA-21

### G2 — Integración en el gate (dep G1, complejo)

- [x] T8 — Modificar `.agents/skills/sdd-gate/SKILL.md` paso 3 ("recuperar
      gobernanza aplicable"): sustituir la lógica inline del chequeo de
      superficie por `subprocess.run([sys.executable, ".spec/scripts/check_governance_surface.py", "--unit", unit, "--spec", spec, "--filesystem-root", "."])` y traducir el Report a veredicto del gate (exit 0 → `governance_consultada: si`; exit != 0 → escalar con causa del Report). Mantener la triada literal por nombre fijo como **red de seguridad** (no fuente de verdad) cuando el spec no declare `configuration_contract:`. Cubre CA-16b. · archivos: `.agents/skills/sdd-gate/SKILL.md` · depende de: T1 · cubre: CA-16b
- [x] T9 — Modificar `.agents/skills/sdd-gate/references/deterministic-layer.md`
      para añadir a la capa determinista de plan y tasks un chequeo de hash de
      filesystem: comparar `fs_hash` actual (vía `compute_fs_hash` del script)
      contra el `fs_hash` persistido al cerrar spec. Si difiere, escalar con
      causa `superficie-cambiada` y forzar re-ejecución del chequeo de
      superficie del paso 3. Si `fs_hash` no cambió, atajo: no invocar el
      chequeo completo (opt-in para re-verificación explícita). Cubre CA-04,
      CA-05. · archivos: `.agents/skills/sdd-gate/references/deterministic-layer.md` · depende de: T1, T8 · cubre: CA-04, CA-05
- [x] T10 — Modificar `.agents/skills/sdd-gate/references/rubrica-plan.md`
      para añadir L4 (Gobernanza) como lente activo: el crítico debe verificar
      que el plan no contradice la lista declarativa del spec y que el chequeo
      de superficie pasó. Documentar el bloque del lente en el cuerpo de la
      rúbrica (no solo en la tabla resumen). Cubre CA-10, CA-11. · archivos: `.agents/skills/sdd-gate/references/rubrica-plan.md` · cubre: CA-10, CA-11
- [x] T11 — Modificar `.agents/skills/sdd-gate/references/rubrica-tasks.md` idéntico
      a T10 pero para tasks. Cubre CA-10, CA-11. · archivos: `.agents/skills/sdd-gate/references/rubrica-tasks.md` · cubre: CA-10, CA-11
- [x] T12 — Crear `.agents/skills/sdd-gate/references/subagent-fallback.md`:
      documento de referencia con la regla de fallback para `subagent_type` no
      registrados, análoga a la de críticos (skill `sdd-gate/SKILL.md:189-195`).
      Estructura obligatoria: (a) **qué rol se degradó** (nombre del rol `sdd-*`);
      (b) **qué se inyecta** desde `.agents/agents/<rol>.md` en el prompt del
      orquestador; (c) **qué se persiste** en `modelo_ejecucion.<fase>`
      (`subagente: general|general-purpose` literal, sin inventar el rol
      previsto). Cubre los roles: `sdd-explorador`, `sdd-especificar-redactor`,
      `sdd-planificar-redactor`, `sdd-tareas-redactor`, `sdd-implementador`,
      `sdd-implementador-xhigh`, `sdd-critico-profundo`, `sdd-critico-estructural`,
      `sdd-critico-cumplimiento`, `sdd-refutador`. Cubre CA-08. · archivos: `.agents/skills/sdd-gate/references/subagent-fallback.md` · cubre: CA-08
- [x] T13 — Modificar `.agents/skills/sdd-gate/SKILL.md` para referenciar
      `references/subagent-fallback.md` desde la tabla "Qué rol lanzar por
      lente" (líneas 181-188) y desde la regla de fallback de críticos
      existente (líneas 189-195), consolidando la política en un solo lugar.
      Test `u0003_ca09_*` verifica que cuando el orquestador dispatcha un rol
      `sdd-*` y opencode devuelve `Unknown agent type`, el orquestador anota en
      bitácora qué rol se degradó y persiste `subagente: general|general-purpose`
      en `modelo_ejecucion.<fase>`. Cubre CA-08, CA-09. · archivos: `.agents/skills/sdd-gate/SKILL.md`, `.spec/scripts/tests/test_subagent_fallback.py` · depende de: T12 · cubre: CA-08, CA-09

### G3 — Vigencia del centinela (sin deps, estandar)

- [x] T14 — Modificar `.spec/scripts/sdd_retomar.py` para que el campo
      `governance_refs` del JSON retornado incluya `verificado_en` (ISO-8601 del
      último chequeo de superficie) y `fs_hash` (hash del filesystem al momento
      del chequeo). Documentar en docstring que el script nuevo
      `check_governance_surface.py` rechaza entradas con `verificado_en` >
      24h (en interactivo) o con `fs_hash` distinto del actual. No es cambio de
      schema formal (los campos son comentarios/convención, no validados por
      `yaml.safe_load`); la verificación ocurre en el script nuevo (T5/T8).
      Cubre CA-12, CA-13. · archivos: `.spec/scripts/sdd_retomar.py` · cubre: CA-12, CA-13

### G4 — Cross-check del orquestador (sin deps, complejo)

- [x] T15 — Añadir sección "Cross-check de superficie" a
      `.agents/skills/sdd-orquestar/SKILL.md` después de la fase 2
      (exploradores). La sección enumera tres acciones: (a) verificar
      existencia de archivos esperados con `ls -la`; (b) ejecutar `grep` o
      `find` sobre el filesystem actual; (c) comparar el resultado del
      explorador contra los resultados de (a) y (b) antes de persistir el
      veredicto. Si hay divergencia (explorador dice "no surface" pero
      filesystem tiene archivos que podrían declararla), el orquestador
      ejecuta su propio probe antes de aceptar el resultado. Cubre CA-17. · archivos: `.agents/skills/sdd-orquestar/SKILL.md` · cubre: CA-17
- [x] T16 — Test estructural `u0003_ca18a_*` en
      `.spec/scripts/tests/test_orquestar_crosscheck.py`: verifica que la
      sección "Cross-check de superficie" existe en
      `sdd-orquestar/SKILL.md` y enumera al menos las tres acciones (a),
      (b), (c). Test por grep estructural sobre el skill. Cubre CA-18a. · archivos: `.spec/scripts/tests/test_orquestar_crosscheck.py` · depende de: T15 · cubre: CA-18a
- [x] T17 — Test funcional `u0003_ca18b_*` en el mismo archivo: con un
      `tmp_path` que contiene `opencode.jsonc` declarando `pce-mcp` (réplica
      del incidente del 2026-09-29) y un explorador simulado que devuelve
      "no surface", la regla del orquestador hace que el veredicto del gate
      sea `escalado` (no `aprobado`). Reproduce el incidente del 2026-09-29
      con filesystem real. Cubre CA-18b. · archivos: `.spec/scripts/tests/test_orquestar_crosscheck.py` · depende de: T15 · cubre: CA-18b

### G5 — Cierre (dep G1, G2, G3, G4, estandar)

- [x] T18 — Verificación final: ejecutar `python3 -m pytest installer -q
      --collect-only -k u0003` y comprobar que lista al menos un test por cada
      CA-NN del spec (CA-29 ampliado a CA-01..CA-21 y sub-CA: 16a/b, 18a/b, total
      23); ejecutar el comando de validación
      `python3 -m pytest .spec/scripts/tests installer -q` y comprobar que
      termina con código 0 (CA-32). Ningún test preexistente se rompe
      (CA-20). Actualizar `_estado.yaml > fase: done, estado: completado`
      cuando ambos pasen. · archivos: `.spec/units/0003-.../_estado.yaml`, `.spec/units/0003-.../bitacora.md` · depende de: T1, T6, T7, T8, T9, T10, T11, T12, T13, T14, T15, T16, T17 · cubre: CA-19, CA-20

## Validación final

- [x] Ejecutar comando de validación (ver `plan.md`)
- [x] Verificar criterios de aceptación de `spec.md` (uno por uno, por id — 23 CA)
- [x] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto registrado
- [x] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`
- [ ] Confirmar que unit 0002 ahora puede re-correr su gate de spec post-delta con el script nuevo (informar al operador para que dispare esa re-corrida; fuera del alcance de esta unit)

## Notas de implementación

Vacía al cierre del plan; cualquier desvío se anota aquí.

**Notas registradas durante la ejecución:**

- El plan maestro es de una sola unidad (0003); no hay "siguiente unidad" tras esta. Cuando 0003 llegue a `done`, el operador debe disparar manualmente la re-corrida del gate de spec de 0002 con el script nuevo (CA-21 de 0003 es smoke test, no la re-corrida real; ver `## Validación final > Confirmar` arriba).
- La regla de fallback de `subagent_type` (CA-08/CA-09) cubre todos los roles `sdd-*` porque la inconsistencia de registro se da en cualquier rol, no solo en exploradores/redactores. Los críticos normalmente sí están registrados; la regla raramente aplica a ellos, pero cuando aplica sigue el mismo patrón.
- El cross-check del orquestador (CA-17/CA-18) es una disciplina del orquestador, no un subagente. La sección en `sdd-orquestar/SKILL.md` documenta la disciplina; los tests CA-18a/CA-18b la anclan a comportamiento ejecutable.
