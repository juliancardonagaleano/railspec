# Plan técnico — Instalación completa de pce-mcp con el kit

> Fase 2. Construido a partir de `spec.md` aprobado (gate `spec: aprobado`).

## Enfoque

Tres reconciliaciones que convergen en un solo contrato de cableado:
1. **`env` block propagado a la carga del kit.** `.mcp.json` y `opencode.jsonc` del kit reciben `env` literal con 3 vars (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `MCP_PCE_CACHE_TTL_SECS="86400"`). Forma portable `${HOME}/.siste/pce-valvula-0040.pem` — Claude Code y Opencode expanden `${HOME}` al construir el `env` del child process (precedente iark). `MCP_PCE_CACHE_TTL_SECS` queda literal en string, igual que lo consume `mcp-pce.py:482` (`int(env.get(...))`).
2. **Opt-out respetado en el cableado con marcador.** Hoy `mezclar_mcp_json` y `mezclar_opencode_jsonc` (`config_cableado.py:198-209, 242-253`) sobre-escriben el entry `pce-mcp` del destino por el del kit aunque el destino lo declare **sin** `_sdd_kit: true`. La corrección es un check antes de agregar el entry del kit: si el destino ya tiene `pce-mcp` con `body.get(DISCRIMINATOR) is not TRUE`, el entry del kit **no** se agrega y el del destino queda intacto. Si lo trae **con** `_sdd_kit`, el reemplazo atómico es completo (incluido `env`, no mergea campo a campo).
3. **Cobertura doble preservada, opt-out simétrico.** Ambas harnesses apuntan a `scripts/mcp-pce.sh` (kit_manifest.yaml líneas 159-164, CA-55). El bloque `env` se replica en ambos configs porque la expansión de `${HOME}` la hace cada harness al construir el child process — no es algo que Claude herede de Opencode ni viceversa.

La idempotencia byte-a-byte (CA-46/CA-51/CA-52 ya vigente) se preserva: `_write_json` usa `indent=2` y la discriminación por valor (`_sdd_kit: true`) no depende de la posición.

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `sdd-kit/.mcp.json` | modificar | Añadir `env` con 3 vars al entry `mcpServers.pce-mcp`. Mantener `type: stdio`, `command: sh`, `args: ["scripts/mcp-pce.sh"]`, `_sdd_kit: true`. Sin `gitnexus`. |
| `sdd-kit/opencode.jsonc` | modificar | Añadir `env` con 3 vars al entry `mcp.pce-mcp`. Mantener `type: local`, `command: ["sh","scripts/mcp-pce.sh"]`, `timeout: 20000`, `_sdd_kit: true`. |
| `installer/config_cableado.py` | modificar | En `mezclar_mcp_json` y `mezclar_opencode_jsonc`, introducir check de opt-out antes de la fusión: si `dest_servers.get(name)` existe y discriminador no es TRUE, skip entry del kit. Resto intacto. |
| `installer/tests/test_u0007_env_sync.py` | crear | Cobertura doble (CA-06): compara claves y valores de `env` en `.mcp.json` y `opencode.jsonc` del kit. |
| `installer/tests/test_u0007_mcp_opt_out.py` | crear | Tests de opt-out (CA-07, CA-08, CA-09, CA-11). |
| `installer/tests/test_config_cableado.py` | modificar | Extender `test_u0002_ca51_*` y `test_u0002_ca52_*` con asserts sobre `env` (CA-10 idempotencia). |

> No se toca `installer/kit_manifest.yaml` (CA-12), `scripts/mcp-pce.sh` (CA-15) ni `scripts/mcp-pce.py` (CA-14).

## Reutilización (no reinventar)

- `installer/config_cableado.py:31-32` — constantes `DISCRIMINATOR`/`TRUE`.
- `installer/config_cableado.py:48-58, 84-94, 97-104` — `_read_json` / `_read_jsonc` / `_write_json`. Idempotencia byte-a-byte ya vigente.
- `installer/config_cableado.py:198-200, 242-244` — patrón "borrar entries con `_sdd_kit` antes de re-agregar". El opt-out extiende ese mismo barrido identificando entries con el nombre del kit pero discriminador ausente.
- `installer/tests/test_config_cableado.py:152-182` — `test_u0002_ca51_mcp_json_fusion_idempotent_and_preserves_others`. El test de idempotencia del `env` (CA-10) extiende este patrón añadiendo asserts sobre `pce["env"]` tras la segunda fusión. Mismo patrón para `:208-224` en Opencode.

## Decisiones de diseño

- **DD-1 — Opt-out: no agregar si ya existe sin marcador.** — **Razón:** el operador que reemplazó `pce-mcp` por su propio wrapper debe quedar intacto. Si lo trae con `_sdd_kit`, sí lo reemplaza atómicamente (CA-09). Check: `dest_servers.get(name)` y `body.get(DISCRIMINATOR) is not TRUE` → skip.
- **DD-2 — Forma de los paths: `${HOME}/.siste/pce-valvula-0040.pem`.** — **Razón:** iark ya lo consume; portable entre hosts; Claude Code (≥2.x) y Opencode (≥1.x) expanden `${HOME}` al construir el `env`. Path absoluto rechazado en `spec.md § P1`.
- **DD-3 — Cobertura doble: replicar `env` en `.mcp.json` y `opencode.jsonc`.** — **Razón:** harnesses independientes en cómo arman el `env` del child. Duplicación deliberada, auditada por `test_u0007_env_sync.py` (CA-06).
- **DD-4 — Reemplazo atómico (no merge campo a campo) con marcador.** — **Razón:** CA-09. El kit es dueño absoluto de los entries que firma; merge preservaría vars huérfanas. El barrido previo de entries con `_sdd_kit` ya garantiza idempotencia.

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | Configs del kit: añadir bloque `env` | `sdd-kit/.mcp.json`, `sdd-kit/opencode.jsonc` | — | estandar |
| G2 | Cableado: opt-out en `mezclar_mcp_json` y `mezclar_opencode_jsonc` | `installer/config_cableado.py` | — | estandar |
| G3 | Tests: suite u0007 + extensión de u0002 | `installer/tests/test_u0007_env_sync.py`, `installer/tests/test_u0007_mcp_opt_out.py`, `installer/tests/test_config_cableado.py` | G1, G2 | estandar |

> G1 y G2 corren en paralelo (configs vs código de instalación). G3 los reúne y los ejercita — depende de ambos porque los tests leen el shape del kit y el comportamiento del cableado.

## Riesgos y mitigaciones

- **R1 — Regresión en `test_u0002_ca51_*` idempotencia.** — **Mitigación:** la modificación de `test_config_cableado.py` es puramente aditiva (asserts extra sobre `env`); los originales (líneas 178-182, 222-224) siguen vigentes. `pytest installer -q -k u0002` debe pasar 58/58 antes de cerrar.
- **R2 — Opt-out demasiado permisivo (operador olvida marcador, re-install borra su config).** — **Mitigación:** semántica explícita (`spec.md § Resultado esperado (4)`, CA-07/CA-08): opt-out requiere ausencia de `_sdd_kit`. Cualquier re-instalación deliberada puede restaurar el comportamiento kit-editando el entry. Log INFO de reemplazo atómico queda para una unidad posterior (P3).
- **R3 — Disco no byte-idéntico entre dos `--install` consecutivos.** — **Mitigación:** `_write_json` con `indent=2`; el orden del dict `env` en `json.dumps` sigue el orden de inserción (Python 3.7+); la rama nueva de opt-out no altera el orden de entries ya en `dest_servers`. CA-10 verifica con dos llamadas y `assert first == second`.
- **R4 — CA-12/CA-13 fallan por regresión preexistente (precedente U-0006/U-0009).** — **Mitigación:** el plan no toca `installer/kit_manifest.yaml` ni `test_manifest*.py`; CA-12/CA-13 son verificables sin cambios. Si el gate de código los marca en rojo, es regresión preexisting y se cierra como `refinado` con la nota literal en `_estado.yaml > gates.codigo.hallazgos`.

## Comando de validación

```bash
python3 -m pytest installer -q -k u0007
```

Comandos auxiliares del crítico de contexto fresco en el gate de código (ver `spec.md § Criterios de aceptación`):

```
python3 -c "import json; d=json.load(open('sdd-kit/.mcp.json')); print(sorted(d['mcpServers']['pce-mcp']['env']))"
grep -F '"MCP_PCE_CACHE_TTL_SECS": "86400"' sdd-kit/.mcp.json sdd-kit/opencode.jsonc
! grep -F '"gitnexus"' sdd-kit/.mcp.json
grep -nE 'source: (\.mcp\.json|opencode\.jsonc)' installer/kit_manifest.yaml
grep -nE 'SSL_CERT_FILE|NODE_EXTRA_CA_CERTS|MCP_PCE_CACHE_TTL_SECS' scripts/mcp-pce.py
! grep -nE '\bunset\b|env -i' scripts/mcp-pce.sh
```