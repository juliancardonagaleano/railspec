# Plan técnico — Cierre de pendientes post-breakdown

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de `spec.md`
> aprobado (123 líneas, dentro del presupuesto 2000).

## Enfoque

Cinco cambios puntuales sobre archivos del kit, cada uno en su grupo, con
archivos disjuntos entre grupos para que el fan-out de implementación corra
en paralelo sin coordinarse. **No** se introduce lógica nueva: solo se crea
un documento de referencia, se mueven dos docstrings, se reformulan 2 CAs
de U-0004 y 3 comentarios inline de U-0002/U-0004. Ninguno de los 5 cambios
toca `installer/`, `scripts/kit_*.py` ni `.agents/` — el contrato de
instalación del kit y la morfología de los subagentes quedan intactos
(CA-09 lo verifica vía `git diff --stat`).

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `.spec/MODELO-AGENTES.md` | crear | Tabla de roles por fase y perfil (modelo+effort), 2 secciones canónicas (`## Modo desatendido`, `## Auditoría de modelo/effort`), nota sobre `subagent_type` por rol. Derivado de `.spec/perfiles.yaml` (fuente única). |
| `.spec/scripts/test_subset.py` | modificar | Eliminar la mención literal `orchestrator/src/iark_orchestrator/` del docstring de `map_pytest_subtree` (línea 136) y mover la nota histórica a un bloque `# ref:` al final del módulo. |
| `.spec/units/0004-purga-y-gitignore/spec.md` | modificar (CA-05 y CA-08) | (a) Prepender `set -f` al bucle bash de CA-05 (línea 92); (b) Reformular CA-08 (línea 95) para aceptar exit 0 **o** exit != 0 con falla documentada en bitácora. |
| `.spec/units/0002-.../_estado.yaml` | modificar (1 comentario) | Reformular línea ≈115 para apuntar a `§ Auditoría de modelo/effort`. |
| `.spec/units/0004-.../_estado.yaml` | modificar (2 comentarios) | Reformular línea 49 (mantener `§ Modo desatendido` si la sección existe tras P1) y línea 105 (añadir `§ Auditoría de modelo/effort`). |
| `.spec/units/0010-.../spec.md`, `plan.md`, `tasks.md`, `paquete-aprobacion.md`, `bitacora.md`, `_estado.yaml` | crear/modificar | Artefactos SDD de U-0010. |

## Reutilización (no reinventar)

- `.spec/perfiles.yaml` — fuente única de la tabla de roles/effort del nuevo
  `MODELO-AGENTES.md`. El parser ya existe como
  `.spec/scripts/effort_profile.py`; **no** se reimplementa la lectura.
- `effort_profile.py resolve --role <rol>` (línea 1-50) — patrón para
  documentar cada rol (modelo+effort+subagent_type).
- `.spec/SUPERVISADO.md` § "Por qué es una skill inline, sin modelo propio"
  (líneas 272-282) — prosa explicativa que MODELO-AGENTES.md reusará en su
  sección sobre subagentes.
- `.spec/README.md` § Ejecución multi-agente — prosa canónica sobre
  auditoría de modelo/effort que MODELO-AGENTES.md formaliza como sección
  propia.
- `.spec/scripts/test_subset.py:1-100` — el docstring de cabecera ya
  contiene prosa análoga a la nota histórica que se mueve al bloque `# ref:`.

## Decisiones de diseño

- **DD-1: `set -f` por línea, no por bloque.** El bucle de CA-05 está
  embebido en un solo `for` dentro de un `- [ ] CA-05 — ...` (prosa de
  criterio, no script ejecutable). Anteponer `set -f` en la misma línea
  mantiene el bucle extraíble como `bash -c 'set -f; ...'` sin reescribir
  la prosa del CA. **Razón:** menos superficie textual cambia, y el
  bucle sigue siendo ejecutable en verificación (CA-05 vía `grep -B1`).
- **DD-2: Mover la nota histórica a `# ref:` al final del módulo en vez
  de al inicio.** La nota sobre el "selector de origen" ya está en el
  docstring de cabecera del módulo (líneas 1-50); un bloque `# ref:` al
  final del módulo (`test_subset.py:280+`) conserva la trazabilidad sin
  contaminar la API visible (`inspect.getsource`). **Razón:** el test
  P2 verifica `inspect.getsource(test_subset)` literal — el bloque al
  final del módulo entra dentro de `getsource` también, pero ya no
  contiene la cadena prohibida `orchestrator/src/iark_orchestrator/`.
  Solo reescribimos el docstring del `map_pytest_subtree`.
- **DD-3: `MODELO-AGENTES.md` con prosa normativa, no prosa ejecutiva.**
  El archivo es un **registro** (tabla de roles + secciones canónicas),
  no un instructivo. Cada rol expone `modelo`, `effort`, `subagent_type`
  y `perfil` (3 perfiles × 8 roles ≈ 24 filas). Las decisiones sobre
  qué perfil elegir viven en `.spec/README.md § Los cuatro modos`; este
  archivo las hace ejecutables.

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| **G1** | P1 + P5: crear `MODELO-AGENTES.md`; reformular 3 comentarios inline en U-0002/U-0004 `_estado.yaml` | `.spec/MODELO-AGENTES.md` (nuevo), `.spec/units/0002-.../_estado.yaml`, `.spec/units/0004-.../_estado.yaml` | — | estandar |
| **G2** | P2: mover nota histórica del docstring `map_pytest_subtree` a bloque `# ref:` al final de `test_subset.py` | `.spec/scripts/test_subset.py` | — | estandar |
| **G3** | P3 + P4: reformular CA-08 (acepta falla ambiental) y prepender `set -f` al bucle de CA-05 en U-0004/spec.md | `.spec/units/0004-purga-y-gitignore/spec.md` | — | estandar |

Los 3 grupos son disjuntos en archivos: G1 toca 3 archivos de metadatos,
G2 toca 1 archivo de motor, G3 toca 1 archivo de spec. Ningún archivo
aparece en 2 grupos. Cada grupo puede ejecutarse en paralelo.

> Ver [.spec/README.md § Reglas del fan-out](README.md#reglas-del-fan-out) para
> justificación detallada y ejemplos.

## Riesgos y mitigaciones

- **R1 — `set -f` añadido a CA-05 cambia la prosa del criterio y rompe
  tests de otros lados.** *Mitigación:* la única verificación que toca
  esa línea es CA-05 mismo (un `grep -B1`); no hay otros tests que lean
  la prosa del CA-05. `validate_artifact_size.py plan` no valida la
  prosa interna del spec — solo el conteo total.
- **R2 — Al mover la nota histórica al bloque `# ref:`, queda como
  prosa redundante con el docstring de cabecera del módulo.** *Mitigación:*
  el bloque `# ref:` lleva un one-liner que dice "este comportamiento
  difiere del selector de origen — ver docstring de cabecera del módulo";
  ningún lector externo lee bloques internos de prosa.
- **R3 — `MODELO-AGENTES.md` cita `.spec/perfiles.yaml` y queda desincronizado
  si perfiles.yaml cambia.** *Mitigación:* el spec declara
  explícitamente "fuente única `.spec/perfiles.yaml`" y la tabla enumera
  roles en el mismo orden que `perfiles.yaml > perfiles`. Si el orden
  cambia, el CA-02 sigue pasando (≥ 8), pero los lectores humanos
  podrían desincronizarse. Aceptable: cualquier divergencia posterior
  cae en una unidad de mantenimiento.
- **R4 — Reformular 3 comentarios inline los desalinea con el
  `validate_mode_conversion.py` u otro verificador que parsee
  `_estado.yaml` por patrones literales.** *Mitigación:* los
  reformulaciones son solo dentro de comentarios `#`; las claves
  YAML (`aprobacion_paquete`, `modelo_ejecucion`, etc.) quedan
  intactas. `yaml.safe_load` sigue parseando.

## Comando de validación

```
python3 -m pytest .spec/scripts/tests -q \
  && test -f .spec/MODELO-AGENTES.md \
  && grep -q "set -f" .spec/units/0004-purga-y-gitignore/spec.md \
  && grep -q "falla documentada" .spec/units/0004-purga-y-gitignore/spec.md \
  && python3 scripts/kit_doctor.py
```

Cualquier exit != 0 con la falla documentada en `bitacora.md` de U-0010
como ambiental es aceptable (precedente CA-08 reformulado, ver
`spec.md CA-06`). El comando de subset opcional
(`.spec/scripts/test_subset.py plan --root .`) produce el subset por
archivos tocados si la implementación toca `<ruta>tests/`; aquí no aplica
(los archivos tocados viven bajo `.spec/` y la suite se corre completa).
