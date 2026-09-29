# Paquete de aprobación — Perfiles de modelos en opencode.jsonc

> **Modo:** semi-autonomo. **Único checkpoint humano de este modo.** Tras la
> aprobación, `sdd-implementar` arranca sin más pausas intermedias.

## Resumen

Unidad `0008-perfiles-de-modelos-en-opencode` declarada el 2026-09-29T22:35:00Z en respuesta a la petición:

> "Tener la posibilidad de definir con que modelo trabaja un agente o skill en este arnes específicamente."

Aclaración del operador (chat): el usuario **no** quiere migrar el catálogo del kit de `opus/sonnet/haiku` (Anthropic) a `minimax/*` (MiniMax). Quiere un **mapping local** por agente/skill del kit en `opencode.jsonc`, declarando qué modelo concreto usa cada uno en este harness.

## Alcance aprobado

5 archivos en 3 grupos paralelos (G1/G2 paralelos, G3 reuniendo):

| Archivo | Acción | Tarea |
|---|---|---|
| `sdd-kit/opencode.jsonc` | modificar | T1 — añadir `agent` con 18 entries `{_sdd_kit, model: "minimax/..."}` |
| `sdd-kit/installer/config_cableado.py` | modificar | T2 — extender `mezclar_opencode_jsonc` con pasada sobre `agent` |
| `sdd-kit/installer/tests/test_u0008_agent_model_mapping.py` | crear | T3 — asserts sobre shape + mapping |
| `sdd-kit/installer/tests/test_u0008_agent_opt_out.py` | crear | T4 — 4 casos de opt-out / reemplazo atómico / preservación |
| `sdd-kit/installer/tests/test_u0008_agent_idempotent.py` | crear | T5 — dos llamadas consecutivas + no-regresión mcp |

Más T6 (verificación read-only de contratos preexistentes, no toca archivos):
- `installer/kit_manifest.yaml` (sigue declarando `source: opencode.jsonc` línea 161 — CA-11)
- `.spec/protocolo-datos.yaml` (sigue con `[opus, sonnet, haiku]` — CA-12)
- `.spec/perfiles.yaml` (intacto — CA-12)
- `.agents/agents/*.md` y `.claude/agents/*.md` (intactos — CA-12)

## Criterios cubiertos por las tareas

12/12 CA-NN cubiertos sin criterios inventados:

| CA | Cubre | Tarea |
|---|---|---|
| CA-01 | parseo JSONC | T1+T3 |
| CA-02 | 9 roles base | T1+T3 |
| CA-03 | 9 variantes | T1+T3 |
| CA-04 | familia `minimax/*` | T1+T3 |
| CA-05 | split M3 vs M2.7-highspeed | T1+T3 |
| CA-06 | `_sdd_kit: true` por entry | T1+T3 |
| CA-07 | opt-out + reemplazo atómico | T2+T4 |
| CA-08 | preservación de entries no-controlados | T2+T4 |
| CA-09 | idempotencia | T2+T5 |
| CA-10 | sin `provider`/`default_agent`/`model` global | T1+T3 |
| CA-11 | kit_manifest intacto | T6 |
| CA-12 | protocolo-datos/perfiles/agentes intactos | T6 |

## Historial de gates

| Fase | Tier | Veredicto | Iteraciones | Hallazgos |
|---|---|---|---|---|
| spec | medio | aprobado | 1 | 0 |
| plan | medio | aprobado | 1 | 0 |
| tasks | medio | aprobado | 1 | 0 |

`governance_consultada: si` en los tres gates, centinela `[ninguna-aplicable]` mantenido (caso típico kit self-installer, precedente U-0005/U-0007, `AGENTS.md § Gobernanza`).

## Suposiciones tomadas

- **P1 (cerrada en spec):** `provider` `minimax` no se declara en `opencode.jsonc`. Razón: es nativo del harness (`opencode models` lo lista), auth es runtime del operador.
- **P2 (cerrada en spec):** `default_agent` y `model` global no se declaran. Razón: son preferencia del operador del destino, declararlos sería pisar sin marcador.
- **P3 (cerrada en plan, conservadora):** sin log INFO en reemplazo atómico. Razón: el spec lo deja para una unidad posterior (simétrico a U-0007 P3, no es requisito para que el cableado funcione).
- **P4 (cerrada en plan, conservadora):** tests dedicados `test_u0008_*.py`, no parametrización de u0002/u0007. Razón: trazabilidad CA-NN → test por nombre; convención del kit.

## Riesgos aceptados

- **R1 — Regresión en `test_u0002_ca52_*`.** Mitigación: T2 es aditivo (nueva pasada sobre `agent`, sin tocar `mcp`); `pytest -k u0002` debe pasar 58/58 antes de cerrar.
- **R2 — Opt-out del agente demasiado permisivo.** Mitigación: semántica explícita simétrica a U-0007 CA-07; cualquier re-instalación deliberada puede restaurar `_sdd_kit: true` en el entry del destino.
- **R3 — Disco no byte-idéntico entre dos `--install` consecutivos.** Mitigación: `_write_json` con `indent=2` y orden estable de dict Python 3.7+; T5 verifica con dos llamadas + `assert first["agent"] == second["agent"]`.
- **R4 — CA-11/CA-12 fallan por regresión preexisting (precedente U-0006/U-0009).** Mitigación: T6 verifica read-only con `grep` literal + `git diff --stat`; si el gate de código los marca en rojo, es regresión preexisting y se cierra como `refinado` con nota literal.

## Decisión

`aprobacion_paquete.decision: aprobado` registrado en `_estado.yaml` con fecha ISO-8601 por el operador (chat) tras la redacción de este paquete. `sdd-implementar` arranca — T1..T6 en orden de dependencias declaradas en `tasks.md` (T1+T2 paralelos, T3+T4+T5 reuniendo, T6 al cierre).
