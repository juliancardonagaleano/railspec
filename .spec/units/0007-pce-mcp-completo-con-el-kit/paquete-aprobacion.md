# Paquete de aprobación — U-0007 Instalación completa de pce-mcp con el kit

> Modo `desatendido`: la aprobación se registra **upfront** antes de
> implementar, sin checkpoints intermedios. El operador aprobó esta corrida
> del flujo SDD completo en una sola petición del chat (sin mediación de
> un humano entre fases); el paquete documenta qué se aprobó y bajo qué
> suposiciones.

## Resumen ejecutivo

| Aspecto | Valor |
|---|---|
| Unidad | `0007-pce-mcp-completo-con-el-kit` |
| Modo | `desatendido` (convertido desde `semi-autonomo` vía `modo_conversion`) |
| Riesgo | `alto` |
| Perfil | `estandar` |
| Spec gate | `aprobado` (0 hallazgos, 15/15 CA-NN testeables) |
| Plan gate | `aprobado` (0 hallazgos, 73 líneas ≤120, 3 grupos paralelos) |
| Tasks gate | `aprobado` (0 hallazgos, cobertura 15/15, 8 tareas T1..T8) |
| Comando de validación | `python3 -m pytest installer -q -k u0007` |
| Decisión | **`aprobado`** |
| Fecha | `2026-09-29T21:00:00Z` |
| Por | Operador (chat explícito: "Conduce el flujo SDD restante ... en modo desatendido (sin pausas para confirmación)") |

## Alcance aprobado

Lo que la implementación va a tocar (ver `plan.md § Archivos a crear / modificar`):

- `sdd-kit/.mcp.json` — añadir bloque `env` con 3 vars al entry `pce-mcp`
- `sdd-kit/opencode.jsonc` — añadir bloque `env` con 3 vars al entry `pce-mcp`, preservando `timeout: 20000`
- `installer/config_cableado.py` — parche de opt-out en `mezclar_mcp_json` y `mezclar_opencode_jsonc`
- `installer/tests/test_u0007_env_sync.py` (crear) — cobertura doble (CA-06)
- `installer/tests/test_u0007_mcp_opt_out.py` (crear) — opt-out (CA-07/08/09/11)
- `installer/tests/test_config_cableado.py` (modificar) — extiende `test_u0002_ca51/ca52_*` con asserts `env` (CA-10)

Lo que **no** se va a tocar (ver `spec.md § No incluye` y `plan.md § Archivos`):

- `installer/kit_manifest.yaml` (CA-12)
- `scripts/mcp-pce.sh` (CA-15)
- `scripts/mcp-pce.py` (CA-14)
- Espejos `.claude/agents/`, `.claude/commands/`, `.claude/skills/` (regenerados por materializadores)
- `installer/cli.py` (no se modifica el entry point del instalador)
- `DISCRIMINATOR = "_sdd_kit"` (constante del cableado)
- Forma `type: stdio` / `type: local` de los entries (contrato fijo)

## Suposiciones conservadoras cerradas en el spec

Estas suposiciones se tomaron en `spec.md § Preguntas abiertas` sin esperar
confirmación humana, por la naturaleza desatendida del modo. El operador
puede corregirlas en una unidad posterior si lo desea:

- **P1** (cerrada) — Forma de los paths: `${HOME}/.siste/pce-valvula-0040.pem`
  (portable, expansión por harness). Alternativa rechazada: path absoluto.
- **P2** (cerrada) — `opencode.jsonc` replica el bloque `env` (cobertura doble
  deliberada, auditada por `test_u0007_env_sync.py`). Alternativa rechazada:
  `env` solo en `.mcp.json`.
- **P3** (derivada al plan, cerrada) — Reemplazo atómico con `_sdd_kit` se
  persiste sin log INFO (P3 queda para una unidad posterior; no aborta, no
  loggea en este flujo).
- **P4** (derivada al plan, cerrada) — Tests del opt-out dedicados
  (`test_u0007_mcp_opt_out.py`) en lugar de parametrizar CA-51 de U-0002.

## Riesgos aceptados

R1..R4 del plan. El más relevante en este modo desatendido es **R4** (CA-12
o CA-13 fallan por regresión preexisting) — precedentes U-0006 y U-0009 ya
mostraron que `kit_manifest.yaml` y los tests de inventario pueden tener
regresiones no atribuibles a la unidad; si aparecen, el gate de código
cierra como `refinado` con la nota literal.

## Cierre del paquete

Esta aprobación se persiste en `_estado.yaml > aprobacion_paquete` con
`decision: aprobado` y `fecha: 2026-09-29T21:00:00Z`. A partir de este
punto `sdd-implementar` arranca y ejecuta T1..T8 según `tasks.md` sin
intervención humana intermedia. El gate de código (`sdd-gate` fase
`codigo`) corre al cierre con crítico de contexto fresco y verifica los
15 CA-NN con comandos literales antes de marcar la unidad como `done`.