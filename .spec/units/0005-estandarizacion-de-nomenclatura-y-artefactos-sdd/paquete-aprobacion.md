# Paquete de aprobación — Estandarización de nomenclatura, unificación de artefactos SDD y reducción de ventana de contexto

> Artefacto del **modo semi-autonomo**. Único checkpoint duro antes de implementar.

## Petición original

"Estandarización de nombramiento de archivos, unificación de archivos de especificación, y optimización del consumo de la ventana de contexto."

## Qué se va a hacer (2-5 líneas)

Tres cambios coordinados: (a) declarar una **convención única de nombres** en una sección canónica `# nomenclatura:` de `.spec/README.md` y un verificador que la aplique; (b) **consolidar las 7 plantillas SDD en alcance** (`_estado.yaml` 185→≤100 líneas, mover 102 líneas de comentarios al README, reemplazar duplicación de prosa por anchors); (c) **centralizar las 2 convenciones de concurrencia** en una sección `## concurrencia` del README. Excluidos: `plan-maestro.md`, `mandato.md`, `historial.md`, `paquete-tanda-aprobacion.md` (los decide U-0009).

**Perfil de esfuerzo:** `estandar` — se puede cambiar en este mismo checkpoint con `/sdd-perfil <nombre>`.

## Criterios de aceptación

| Id | Criterio | Tareas que lo cubren |
|---|---|---|
| CA-01 | `grep -n '^# nomenclatura' .spec/README.md` devuelve exactamente una línea | T1, T3 |
| CA-02 | Verificador de nomenclatura en `test_u0005_ca02.py` lee la convención desde el README (no hardcoded) y falla ante violación sobre 6 árboles | T9 |
| CA-03 | El verificador lista outliers en stdout con exit 0 (no aborta) | T10 |
| CA-04 | `install_pre_push_hook.sh` aparece en la salida del verificador Y en la tabla de excepciones del README § nomenclatura | T1, T11 |
| CA-05 | Cada bloque de prosa ≥5 líneas (o comentarios `#` consecutivos ≥5 en `_estado.yaml`) aparece a lo sumo en una plantilla | T7, T12 |
| CA-06 | Las secciones re-declaradas referencian el README con anchor reproducible | T13 |
| CA-07 | `git status --short .spec/_plantillas/` solo lista paths permitidos (7 en alcance + 4 de U-0009 intactos) | T8, T14 |
| CA-08 | `wc -l .spec/_plantillas/_estado.yaml` ≤ 100 + `yaml.safe_load` parseable + set de claves preservado | T4, T15 |
| CA-09 | Cada bloque de comentarios `#` consecutivos ≥3 en `_estado.yaml` se reemplaza por referencia al README o una línea explicativa | T4, T16 |
| CA-10 | El bloque "Complejidad: complejo" en `plan.md:36-52` queda ≤5 líneas no-vacías | T6, T17 |
| CA-11 | Las 2 convenciones de concurrencia se referencian desde el README `## concurrencia`; los greps literales retornan 0 hits en sus plantillas originales | T2, T5, T18 |
| CA-12 | Las 4 plantillas excluidas permanecen byte-idénticas durante toda la corrida | T8, T19 |
| CA-13 | Cada CA-NN (02-12) tiene al menos un test propio coleccionable por `-k u0005_ca<NN>`; ≥12 tests listados por `--collect-only -k u0005` | T9..T19 + T20 |
| CA-14 | `python3 -m pytest .spec/scripts/tests installer -q -k u0005` exit 0 | Validación final |

## Alcance del cambio

- **Archivos a crear/modificar:** 16 archivos (2 modificados en G1 README canónico; 4 modificados en G2 consolidación; 10 tests nuevos en G3 verificadores).
- **Fuera de alcance (declarado):**
  - Tocar `plan-maestro.md`, `mandato.md`, `historial.md`, `paquete-tanda-aprobacion.md` — los decide U-0009.
  - Tocar `installer/cli.py`, `installer/installer.py` — U-0006.
  - Tocar `.mcp.json`, `opencode.jsonc` — U-0007.
  - Modificar `.spec/perfiles.yaml` — U-0008.
  - Renombrar archivos existentes (`scripts/install_pre_push_hook.sh` queda como excepción documentada con motivo "load-bearing para `installer/installer.py:81`").
  - Crear archivos nuevos — la restricción operativa de la unidad prohíbe crear archivos. Los 2 nuevos en `.spec/scripts/tests/test_u0005_*.py` son tests verificadores, no carga del protocolo.
  - Resolver la cita colgante a `.spec/MODELO-AGENTES.md` (hallazgo adicional registrado, fuera de alcance).
- **Comando de validación:** `python3 -m pytest .spec/scripts/tests installer -q -k u0005`

## Historial de gates

| Fase | Veredicto | Iteraciones | Hallazgos resueltos | Hallazgos sin resolver | Governance |
|---|---|---|---|---|---|
| spec | refinado | 1 | 3 (1 alta + 2 media) | 2 (baja) | si — centinela [ninguna-aplicable] confirmado |
| plan | refinado | 1 | 3 (2 alta + 1 media) | 1 (baja) | si — centinela [ninguna-aplicable] confirmado |
| tasks | refinado | 1 | 2 (1 media + 1 baja) | 2 (baja) | si — centinela [ninguna-aplicable] confirmado |

**Correcciones más relevantes:**

1. **CA-11 (spec):** la premisa original era factualmente errónea — `"Convención de merge (unidad 0098)"` solo aparece en `bitacora.md:13`, NO duplicada en `_estado.yaml`. **Corrección:** CA-11 reescrito para centralizar las 2 convenciones de concurrencia (bitácora y `_estado.yaml`) en una sección reproducible de `.spec/README.md`.
2. **DD-4 (plan):** un solo archivo `test_u0005_ca02.py` con métodos `test_ca03_*` (sin prefijo `u0005_`) no satisfacía `-k u0005_ca03`. **Corrección:** 3 archivos separados con métodos `test_u0005_ca<NN>_*`.
3. **CA-06 y CA-07 sin verificador en plan.** **Corrección:** añadidos `test_u0005_ca06.py` y `test_u0005_ca07.py`.
4. **T4 y T5 fusionados en `tasks` (T4 cubre CA-08+CA-09).** Eran dos caras del mismo edit sobre `_estado.yaml` que ningún CA satisfacía por sí solo. Numeración renumerada T1..T20 contiguos.

## Suposiciones tomadas (requieren confirmación)

| # | Pregunta | Suposición tomada | Impacto si es errada |
|---|---|---|---|
| 1 | ¿La sección canónica de nomenclatura vive en `.spec/README.md` o se crea `.spec/NOMENCLATURA.md`? | En `.spec/README.md` § `# nomenclatura:`. Restricción operativa prohíbe crear archivos. | Si querés archivo dedicado, la unidad no puede implementarlo aquí; cae a U-0009 o una unidad posterior. |
| 2 | ¿Se renombran los outliers conocidos o se documentan como excepciones? | Se documentan como excepciones en la tabla del README. `install_pre_push_hook.sh` tiene 5 referencias load-bearing en `installer/`. | Si querés rename, U-0007 (manifiesto) y U-0002 (hook byte-identidad) son prerrequisito. |
| 3 | ¿Se mueve la prosa de CA-09 a `.spec/README.md` o a `.spec/SUPERVISADO.md`? | A `.spec/README.md` por ser canónico; `SUPERVISADO.md` se reserva para modo supervisado. | Mínimo: si querés en `SUPERVISADO.md`, requiere justificación que el plan no consideró. |

## Riesgos aceptados

- **`yaml.safe_load` podría romperse al mover comentarios.** Mitigación: T15 verifica parseo + set de claves top-level preservado.
- **`sdd-retomar.py` podría romperse.** Mitigación: el script consume `_estado.yaml` vía `_common.field()`/`_common.top_level_block()` que ignoran comentarios por construcción.
- **El verificador de CA-02 lee `.spec/README.md`; si alguien cambia el anchor, queda roto.** Mitigación: el verificador declara `EXPECTED_ANCHOR = "# nomenclatura:"` como constante y falla con mensaje literal; CA-01 mismo detecta antes que el verificador corra.
- **Tests nuevos interfieren con suite preexistente.** Mitigación: `-k u0005` aísla la corrida; ningún test preexistente se modifica.
- **Una de las 4 excluidas cambia por accidente.** Mitigación: T8 (gate pre-commit) + T19 (test persistente).
- **Cita a `.spec/MODELO-AGENTES.md` (8 referencias) queda colgante.** No es riesgo de esta unidad — es hallazgo adicional prerrequisito de otra unidad, fuera de alcance.

## Gobernanza aplicada

`governance_refs`: `[ninguna-aplicable]` — consultada vía gates de spec, plan, tasks. Centinela confirmado con evidencia del repo en cada corrida (mismo caso que U-0001, U-0002, U-0004). El kit no tiene superficie de gobernanza propia; `pce-mcp` aparece solo como carga hacia el destino, no como cliente conectado al repo.

## Decisión

- [ ] **Aprobar** — implementar según `tasks.md`
- [ ] **Aprobar con cambios** — indicar cuáles
- [ ] **Rechazar** — la unidad queda en `estado: bloqueado` con el motivo en bitácora