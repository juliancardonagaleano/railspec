# Tareas — Cierre de pendientes post-breakdown

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**.
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece
> en ninguna tarea.

## Pendientes

### Grupo G1 — P1 + P5 (archivos: `MODELO-AGENTES.md` nuevo, U-0002/U-0004 `_estado.yaml`)

- [x] T1 — Crear `.spec/MODELO-AGENTES.md` con secciones:
  - `# Modelo de agentes` (header).
  - `## Tabla de roles` — 24 filas (3 perfiles × 8 roles) con columnas
    `rol`, `subagent_type`, `modelo`, `effort`. Datos derivados literal de
    `.spec/perfiles.yaml` perfiles `estandar` + `ligero` + `profundo`.
  - `## Modo desatendido` — prosa sobre paralelismo, citando el patrón
    `G1..Gn` de `.spec/SUPERVISADO.md` y el precedente U-0005.
  - `## Auditoría de modelo/effort` — prosa sobre `_estado.yaml >
    modelo_ejecucion`, citando `effort_profile.py` como fuente única.
  - `## Subagentes y roles` — patrón `resolve --role <rol>` para que el
    gate decida `subagent_type`/`model` por unidad.
  - `## Si un subagente nombrado no está disponible` — fallback a
    `general-purpose` con misma lente y mismas instrucciones.
  · archivos: `.spec/MODELO-AGENTES.md` · cubre: CA-01, CA-02

- [x] T2 — Reformular `.spec/units/0002-.../_estado.yaml` línea ≈115:
  cambiar `# .spec/MODELO-AGENTES.md para el porqué y la tabla completa`
  por `# .spec/MODELO-AGENTES.md § Auditoría de modelo/effort para el
  porqué y la tabla completa`.
  · archivos: `.spec/units/0002-.../_estado.yaml` · cubre: CA-08

- [x] T3 — Reformular `.spec/units/0004-.../_estado.yaml` línea 49:
  prefijo `# Ver .spec/MODELO-AGENTES.md § Modo desatendido` — verificar
  que la sección `## Modo desatendido` existe en MODELO-AGENTES.md (T1
  ya la creó). Sin cambio funcional si la sección existe.
  · archivos: `.spec/units/0004-.../_estado.yaml` · cubre: CA-08

- [x] T4 — Reformular `.spec/units/0004-.../_estado.yaml` línea 105:
  cambiar `# .spec/MODELO-AGENTES.md para el porqué y la tabla completa`
  por `# .spec/MODELO-AGENTES.md § Auditoría de modelo/effort para el
  porqué y la tabla completa`.
  · archivos: `.spec/units/0004-.../_estado.yaml` · cubre: CA-08

### Grupo G2 — P2 (archivo: `test_subset.py`)

- [x] T5 — Eliminar la mención literal `orchestrator/src/iark_orchestrator/`
  del docstring de `map_pytest_subtree` (línea 136 de
  `.spec/scripts/test_subset.py`). Reformular el docstring a:
  "Cambio de comportamiento deliberado respecto al selector de origen
  (gate de código, unidad de extracción del kit): el origen restringía el
  mapeo por stem a un paquete fuente concreto cableado; esta versión
  genérica lo aplica a cualquier `.py` bajo `ruta` que no viva en
  `tests/`, porque el destino ya no declara qué es 'paquete fuente real'
  dentro de un subárbol — solo su raíz. Esto amplía, respecto al origen,
  los casos donde un stem con exactamente un test candidato se toma como
  match confiable en vez de caer al fallback (`no cae en ninguna
  convención mapeable`, que fuerza la corrida completa): un stem
  duplicado por casualidad entre un script suelto del subárbol y un
  módulo real puede producir un subset que no cubre el archivo tocado.
  Un destino que necesite recuperar la garantía de origen debe mantener
  disjuntos los nombres de módulo entre su paquete fuente y cualquier
  script suelto bajo el mismo subárbol. Ver bloque `# ref:` al final del
  módulo para la trazabilidad histórica del selector de origen."
  · archivos: `.spec/scripts/test_subset.py` · cubre: CA-03, CA-07

- [x] T6 — Añadir bloque `# ref:` al final del módulo `test_subset.py`
  con la nota histórica sobre el selector de origen (qué paquete se
  cableaba en el origen y por qué el genérico lo abrió). El bloque NO
  contiene la cadena prohibida `orchestrator/` ni `studio/app/`.
  · archivos: `.spec/scripts/test_subset.py` · cubre: CA-03, CA-07

### Grupo G3 — P3 + P4 (archivo: `U-0004/spec.md`)

- [x] T7 — Prepender `set -f` al bucle bash de
  `.spec/units/0004-purga-y-gitignore/spec.md:92` (CA-05). Cambio
  literal: insertar `set -f; ` antes de `for p in __pycache__/ ...`.
  Verificar con `grep -B1 "for p in" spec.md` que `set -f` aparece en
  la línea anterior al `for`.
  · archivos: `.spec/units/0004-purga-y-gitignore/spec.md` · cubre: CA-05

- [x] T8 — Reformular CA-08 de `.spec/units/0004-purga-y-gitignore/spec.md:95`:
  cambiar
  `python3 scripts/kit_doctor.py desde la raíz del repo termina con
  exit code 0 (sin nuevos faltantes de carga ni de configuración del
  entorno agéntico).`
  por
  `python3 scripts/kit_doctor.py desde la raíz del repo termina con
  exit code 0 **o** exit != 0 con al menos una línea en `bitacora.md`
  de la unidad que documente la falla como ambiental
  (precedente U-0004 § Governance aplicable — pce-mcp y/o graph-index
  no responden en este clon); sin nuevos faltantes de carga ni de
  configuración del entorno agéntico.`
  · archivos: `.spec/units/0004-purga-y-gitignore/spec.md` · cubre: CA-04, CA-06

## Validación final

- [x] T9 — Verificar `git diff --stat` post-implementación lista exactamente:
  `.spec/MODELO-AGENTES.md` (nuevo), `.spec/scripts/test_subset.py`,
  `.spec/units/0004-purga-y-gitignore/spec.md`, `_estado.yaml` de U-0002 y
  U-0004, más artefactos SDD de U-0010. Ningún archivo de `installer/`,
  `scripts/`, `.claude/`, `.agents/` aparece en el diff.
  · archivos: — · cubre: CA-09

- [ ] Ejecutar `python3 -m pytest .spec/scripts/tests -q` (debe terminar
  con exit 0, `0 failed`, `passed` ≥ 754).
- [ ] Verificar criterios de aceptación de `spec.md` uno por uno:
  - [ ] CA-01: `test -f .spec/MODELO-AGENTES.md && echo OK` imprime `OK`.
  - [ ] CA-02: tres greps sobre `.spec/MODELO-AGENTES.md` retornan conteos
    esperados (≥ 8 / ≥ 1 / ≥ 1).
  - [ ] CA-03: pytest del test específico retorna `1 passed`.
  - [ ] CA-04: awk sobre U-0004 spec.md encuentra `falla documentada`.
  - [ ] CA-05: grep -B1 sobre U-0004 spec.md muestra `set -f`.
  - [ ] CA-06: `python3 scripts/kit_doctor.py` exit 0 OR bitácora documenta.
  - [ ] CA-07: pytest suite verde.
  - [ ] CA-08: tres greps sobre U-0002/U-0004 `_estado.yaml` apuntan a
    secciones canónicas.
  - [ ] CA-09: `git diff --stat` lista exactamente los archivos esperados
    (ver T9).
- [ ] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto
  registrado en `_estado.yaml > gates.codigo`.
- [ ] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`.

## Notas de implementación

- Las reformulaciones literales en T5 y T8 deben preservar la prosa
  normativa previa; no añadir ni quitar secciones.
- T1 genera un archivo nuevo: ejecutar `test -f` antes de T1 es
  precondición de falla limpia.
- Si T5 deja inadvertidamente el literal `orchestrator/` en otro lugar
  del módulo (no esperado, pero el test escanea `inspect.getsource`
  completo), CA-03 falla y se reabre el grupo.
- Si pytest muestra un `F` adicional distinto del P2 durante la
  Validación final, queda fuera de alcance (restricciones duras): no
  reasignar el `F` a este unit.
