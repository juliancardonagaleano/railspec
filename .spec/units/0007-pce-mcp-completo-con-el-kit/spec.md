# Spec — Instalación completa de pce-mcp con el kit

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

El kit declara `pce-mcp` en `.mcp.json` con shape mínimo (`type`/`command`/`args`/`_sdd_kit`); el destino real `iark/.mcp.json` añade 3 vars de entorno (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `MCP_PCE_CACHE_TTL_SECS`) necesarias para que `scripts/mcp-pce.py` negocie TLS y respete el TTL de cache contra el backend de la PCE (consumo en `mcp-pce.py:482,487,500,501`). Hoy:

- Un destino que recibe el kit hereda `.mcp.json` y `opencode.jsonc` sin bloque `env` → `pce-mcp` armado pero falla al primer handshake TLS.
- `installer/config_cableado.py:mezclar_mcp_json` (líneas 198-209) sobrescribe el entry `pce-mcp` del destino por el del kit aunque el destino lo declare **sin** `_sdd_kit: true`: opt-out no respetado.

Si no se cierra, el contrato agéntico del destino queda a medias; editar `.mcp.json`/`opencode.jsonc` a mano post-install viola el principio antidrift del hook de pre-push.

## Resultado esperado

Tras `python3 installer/cli.py --install` en un destino limpio:

1. `.mcp.json` declara `mcpServers.pce-mcp.env` con las 3 vars y `_sdd_kit: true`.
2. `opencode.jsonc` declara `mcp.pce-mcp.env` con las mismas 3 vars (cobertura doble) y `timeout: 20000` preservado.
3. Destino con `pce-mcp` y `_sdd_kit: true` → entry reemplazado atómicamente con el del kit (incluido `env`).
4. Destino con `pce-mcp` **sin** `_sdd_kit` → entry del destino preservado intacto (opt-out).
5. Otros `mcpServers`/`mcp` sin discriminador (p.ej. `gitnexus` en iark) sobreviven sin cambios.
6. Dos `--install` consecutivos producen `.mcp.json` y `opencode.jsonc` byte-idénticos.

## Alcance

**Incluye:**

- Modificar `sdd-kit/.mcp.json` para añadir el bloque `env` con las 3 vars.
- Modificar `sdd-kit/opencode.jsonc` para añadir el mismo bloque `env` (cobertura doble Claude + Opencode) preservando `timeout: 20000`.
- Forma de las vars de path: `${HOME}/.siste/pce-valvula-0040.pem` (placeholder portable — ver P1).
- Cambiar `installer/config_cableado.py` para que `mezclar_mcp_json` y `mezclar_opencode_jsonc` respeten el opt-out: si el destino tiene `pce-mcp` sin `_sdd_kit: true`, el entry del destino queda intacto y el del kit no se agrega (no se duplica, no se sobreescribe).
- Tests en `installer/tests/` cubriendo: (a) `env` propagado en ambos configs; (b) sincronización kit↔destino con marcador; (c) opt-out preserva entry del destino; (d) idempotencia del bloque `env`.

**No incluye (fuera de alcance):**

- Añadir `gitnexus` u otros `mcpServers` al kit (cada destino los declara).
- Modificar `scripts/mcp-pce.sh` ni `scripts/mcp-pce.py` (contrato de las 3 vars ya implementado; el wrapper las hereda del entorno).
- Cambiar `DISCRIMINATOR = "_sdd_kit"` ni el orden de invocación de `cablear()` (`config_cableado.py:259-265`).
- Modificar entradas de `installer/kit_manifest.yaml` (`.mcp.json` línea 159-160, `opencode.jsonc` línea 161-162 ya declaradas).
- Distribuir ni versionar el cert `pce-valvula-0040.pem` (lo instala el operador).
- Cambiar el `type` (`stdio`/`local`) de los entries — contrato fijo del cableado.
- Renombrar o mover `scripts/mcp-pce.sh`.

## Criterios de aceptación

- [ ] **CA-01** — `sdd-kit/.mcp.json` declara `mcpServers.pce-mcp.env` con exactamente las 3 claves `SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`, `MCP_PCE_CACHE_TTL_SECS`. `python3 -c "import json; d=json.load(open('sdd-kit/.mcp.json')); print(sorted(d['mcpServers']['pce-mcp']['env']))"` imprime las 3 claves en orden lexicográfico.
- [ ] **CA-02** — `MCP_PCE_CACHE_TTL_SECS` es la cadena literal `"86400"` en ambos configs. `grep -F '"MCP_PCE_CACHE_TTL_SECS": "86400"' sdd-kit/.mcp.json sdd-kit/opencode.jsonc` imprime 2 líneas.
- [ ] **CA-03** — `SSL_CERT_FILE` y `NODE_EXTRA_CA_CERTS` contienen `${HOME}` y terminan en `pce-valvula-0040.pem` (forma portable, no absoluta). Verificable con el `python3 -c` del CA-01 extendido: `assert '${HOME}' in d['SSL_CERT_FILE'] and 'pce-valvula-0040.pem' in d['SSL_CERT_FILE']` (y simétrico para `NODE_EXTRA_CA_CERTS`).
- [ ] **CA-04** — `sdd-kit/.mcp.json` **no** contiene la clave `gitnexus` en `mcpServers`. `! grep -F '"gitnexus"' sdd-kit/.mcp.json` retorna exit 1.
- [ ] **CA-05** — `sdd-kit/opencode.jsonc` declara `mcp.pce-mcp` con `_sdd_kit: true`, `type: local`, `command: ["sh", "scripts/mcp-pce.sh"]`, `timeout: 20000`. `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); p=d['mcp']['pce-mcp']; assert p['_sdd_kit'] is True and p['type']=='local' and p['command']==['sh','scripts/mcp-pce.sh'] and p['timeout']==20000; print('ok')"`.
- [ ] **CA-06** — Cobertura doble: las 3 vars de `env` en `.mcp.json` y `opencode.jsonc` son idénticas (mismas claves, mismos valores). Test nuevo `installer/tests/test_u0007_env_sync.py` que falle si difieren.
- [ ] **CA-07** — `mezclar_mcp_json` no agrega el entry `pce-mcp` del kit cuando el destino ya tiene `pce-mcp` sin `_sdd_kit: true` (opt-out). Test nuevo `test_u0007_mcp_opt_out_preserves_unmarked_entry` en `installer/tests/test_config_cableado.py`.
- [ ] **CA-08** — `mezclar_opencode_jsonc` no agrega el entry `pce-mcp` del kit cuando el destino ya tiene `pce-mcp` sin `_sdd_kit: true` (semántica simétrica a CA-07).
- [ ] **CA-09** — Si el destino tiene `pce-mcp` con `_sdd_kit: true`, el instalador reemplaza el entry **completo** (incluido `env`) con el del kit — no mergea campo a campo. Test con `env` arbitrario en el destino marcado: el resultado coincide 1-a-1 con el `env` del kit.
- [ ] **CA-10** — Idempotencia: dos `--install` consecutivos producen `.mcp.json` y `opencode.jsonc` byte-idénticos. Reuso del patrón `test_u0002_ca51_mcp_json_fusion_idempotent_and_preserves_others` extendido para verificar también el bloque `env`.
- [ ] **CA-11** — Otros `mcpServers` declarados por el destino sin `_sdd_kit` (p.ej. `gitnexus` en iark) sobreviven al `--install` sin cambios.
- [ ] **CA-12** — `installer/kit_manifest.yaml` sigue declarando `source: .mcp.json, dest: .mcp.json` (línea ~159) **y** `source: opencode.jsonc, dest: opencode.jsonc` (línea ~161). `grep -nE 'source: (\.mcp\.json|opencode\.jsonc)' installer/kit_manifest.yaml` imprime 2 líneas.
- [ ] **CA-13** — `installer/tests/test_manifest.py` (o equivalente) sigue pasando: el kit cubre la carga declarada sin ruta huérfana ni faltante.
- [ ] **CA-14** — `scripts/mcp-pce.py` lee las 3 env vars. `grep -nE 'SSL_CERT_FILE|NODE_EXTRA_CA_CERTS|MCP_PCE_CACHE_TTL_SECS' scripts/mcp-pce.py` imprime ≥3 líneas.
- [ ] **CA-15** — `scripts/mcp-pce.sh` no sanitiza el entorno del proceso spawneado (`exec python3` hereda el env del caller). `grep -nE '\bunset\b|env -i' scripts/mcp-pce.sh` retorna exit 1 (no hay purgado).

## Governance aplicable

`governance_refs: [ninguna-aplicable]` en `_estado.yaml`. El kit no se gobierna a sí mismo vía `pce-mcp` (ver `AGENTS.md § Gobernanza`); los cambios a `.mcp.json`/`opencode.jsonc` quedan bajo el contrato del cableado (`installer/config_cableado.py`), cuyo discriminador `_sdd_kit` y semántica de fusión están fijados por `config_cableado.py:31-32, 198-209, 242-253`. No se relajan en esta unidad. No se introduce ADR ni policy nueva.

## Preguntas abiertas

- **P1 (cerrada con suposición — conservadora):** Forma de los valores de path (`SSL_CERT_FILE`, `NODE_EXTRA_CA_CERTS`) → `${HOME}/.siste/pce-valvula-0040.pem`. Razón: iark ya lo usa así y es el contrato desplegado; portable entre hosts; Claude Code y Opencode expanden `${HOME}` al construir el `env` del proceso spawneado. Alternativa rechazada: path absoluta — rompe el contrato de carga genérica del kit.
- **P2 (cerrada con suposición — conservadora):** `opencode.jsonc` replica el bloque `env` (SÍ). Cobertura doble: ambas harnesses pasan las mismas vars al proceso. Alternativa rechazada: `env` solo en `.mcp.json` — ahorra duplicación pero rompe simetría si Opencode no hereda el env automáticamente.
- **P3 (abierta para `plan.md`):** Si el destino trae `pce-mcp` con `_sdd_kit: true` y un `env` distinto al del kit, ¿el reemplazo atómico debe loggear warning? Decisión del plan; conservadora: loggear en `INFO` durante el `--install` (no abortar).
- **P4 (abierta para `plan.md`):** Tests del opt-out — ¿dedicado (`test_u0007_mcp_opt_out_*`) o parametrizar CA-51? Decisión del plan; conservadora: dedicado para mantener trazabilidad CA-NN → test por nombre.