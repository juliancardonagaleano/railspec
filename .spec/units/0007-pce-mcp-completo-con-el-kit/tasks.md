# Tareas — Instalación completa de pce-mcp con el kit

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**:
> el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en
> ninguna tarea.

## Pendientes

- [x] T1 — Añadir bloque `env` (3 vars) al entry `mcpServers.pce-mcp` de `sdd-kit/.mcp.json`; mantener `type: stdio`, `command: sh`, `args: ["scripts/mcp-pce.sh"]`, `_sdd_kit: true`; no incluir `gitnexus` · archivos: `sdd-kit/.mcp.json` · cubre: CA-01, CA-02, CA-03, CA-04
- [x] T2 — Añadir bloque `env` (3 vars) al entry `mcp.pce-mcp` de `sdd-kit/opencode.jsonc`; mantener `type: local`, `command: ["sh","scripts/mcp-pce.sh"]`, `timeout: 20000`, `_sdd_kit: true` · archivos: `sdd-kit/opencode.jsonc` · cubre: CA-05
- [x] T3 — En `mezclar_mcp_json` (`installer/config_cableado.py:171-212`), introducir check de opt-out: en el bucle que agrega entries del kit, saltar si `dest_servers.get(name)` existe con `body.get(DISCRIMINATOR) is not TRUE`; mantener el barrido previo de entries con `_sdd_kit` para que el reemplazo atómico funcione · archivos: `installer/config_cableado.py` · cubre: CA-07, CA-09
- [x] T4 — En `mezclar_opencode_jsonc` (`installer/config_cableado.py:215-256`), aplicar el mismo check de opt-out simétrico al de T3 (misma lógica, distinto path JSONC) · archivos: `installer/config_cableado.py` · depende de: T3 · cubre: CA-08
- [x] T5 — Crear `installer/tests/test_u0007_env_sync.py`: test que carga `sdd-kit/.mcp.json` y `sdd-kit/opencode.jsonc`, compara `env` de ambos entries `pce-mcp` (claves y valores idénticos); falla con `assert sorted(mcp_env) == sorted(opencode_env)` y `assert mcp_env == opencode_env` · archivos: `installer/tests/test_u0007_env_sync.py` · depende de: T1, T2 · cubre: CA-06
- [x] T6 — Crear `installer/tests/test_u0007_mcp_opt_out.py` con 4 casos: (a) destino con `pce-mcp` sin `_sdd_kit` → entry del destino preservado, kit no agrega (mezclar_mcp_json); (b) simétrico para mezclar_opencode_jsonc; (c) destino con `pce-mcp` con `_sdd_kit` y `env` arbitrario distinto al del kit → entry reemplazado atómicamente, `env` final idéntico al del kit; (d) `gitnexus` u otro server sin `_sdd_kit` sobrevive sin cambios · archivos: `installer/tests/test_u0007_mcp_opt_out.py` · depende de: T1, T2, T3, T4 · cubre: CA-07, CA-08, CA-09, CA-11
- [x] T7 — Extender `test_u0002_ca51_mcp_json_fusion_idempotent_and_preserves_others` (líneas 152-182) y `test_u0002_ca52_opencode_jsonc_fusion_idempotent_and_preserves_others` (líneas 208-224) en `installer/tests/test_config_cableado.py` con asserts adicionales sobre `pce["env"]` (claves esperadas y `MCP_PCE_CACHE_TTL_SECS == "86400"`); los asserts originales (líneas 178-182, 222-224) siguen vigentes — cambio puramente aditivo · archivos: `installer/tests/test_config_cableado.py` · depende de: T1, T2, T3, T4 · cubre: CA-10
- [x] T8 — Verificar que los contratos preexistentes no fueron tocados: ejecutar `grep -nE 'source: (\.mcp\.json|opencode\.jsonc)' installer/kit_manifest.yaml` (esperar 2 líneas en ~159 y ~161 — CA-12), `python3 -m pytest installer/tests/test_manifest.py installer/tests/test_manifest_matches_inventory.py -q` (esperar 0 fallos — CA-13), `grep -nE 'SSL_CERT_FILE|NODE_EXTRA_CA_CERTS|MCP_PCE_CACHE_TTL_SECS' scripts/mcp-pce.py` (esperar ≥3 líneas — CA-14), `! grep -nE '\bunset\b|env -i' scripts/mcp-pce.sh` (esperar exit 1, sin matches — CA-15); archivar la salida literal en bitácora para el crítico de contexto fresco · archivos: `installer/kit_manifest.yaml` (sólo lectura), `installer/tests/test_manifest*.py` (sólo lectura), `scripts/mcp-pce.py` (sólo lectura), `scripts/mcp-pce.sh` (sólo lectura) · depende de: T1, T2, T3, T4 · cubre: CA-12, CA-13, CA-14, CA-15

## Validación final

- [x] Ejecutar `python3 -m pytest installer -q -k u0007` y verificar 0 fallos
- [x] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto registrado
- [x] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`, `modelo_ejecucion.implementar` con 8 entradas (T1..T8)

## Notas de implementación

Detalle que surja al implementar (decisiones puntuales, desvíos del plan).

- **T3/T4 implementación**: el check de opt-out se materializa como `if name in dest_servers: continue` después del barrido previo de entries con `_sdd_kit`. La invariante: `dest_servers` post-barrido contiene exactamente las entries del destino que el kit NO controla (`_sdd_kit: false` o ausente), y son intocables. Las entries con `_sdd_kit: true` que el kit controla ya fueron eliminadas en el barrido previo, así que el kit puede agregar sus propias versiones sin conflicto.
- **T7**: extensión puramente aditiva de `test_u0002_ca51/ca52_*` con asserts sobre `pce["env"]`; los asserts originales (líneas 178-182, 222-224) siguen vigentes.
- **Cierre del plan**: la unidad cumplió el plan sin desvíos; las 4 suposiciones cerradas (P1+P2 en spec, P3+P4 en plan) y los 4 riesgos aceptados (R1..R4) se verificaron en la implementación sin hallazgos.