# Plan técnico — Perfiles de modelos en opencode.jsonc

> Fase 2 (Planificar). Construido a partir de `spec.md` aprobado (gate `spec: aprobado`).

## Enfoque

Tres reconciliaciones que extienden el contrato de cableado del kit con simetría estricta al patrón U-0007 CA-07..CA-11, aplicadas a la sección `agent` de `opencode.jsonc`:

1. **`agent` declarada en la carga del kit con `_sdd_kit: true` por entry.** El kit declara 18 entries en `opencode.jsonc > agent` (9 roles base + 9 variantes `*-low|high|max|medium|xhigh` materializadas en `.claude/agents/`), cada uno con `_sdd_kit: true` y `model` apuntando a `minimax/MiniMax-{M3, M2.7-highspeed}`. La asignación por capacidad: críticos profundos, especificadores, planificadores y refutador → `MiniMax-M3` (más capaz); implementador, explorador, tareas, críticos estructural/cumplimiento → `MiniMax-M2.7-highspeed` (rápido y barato). Ningún `provider`/`default_agent`/`model` global — son runtime del operador del destino (P1+P2 del spec).
2. **Fusión con marcador para `agent` simétrica a U-0007 CA-07..CA-09.** Hoy `mezclar_opencode_jsonc` (`config_cableado.py:222-269`) solo toca `data["mcp"]`. La extensión agrega una segunda pasada sobre `data["agent"]`: (a) barrido previo de entries del destino con `_sdd_kit: true` (las controla el kit), (b) merge con el mismo patrón — entries del kit con `_sdd_kit` se reemplazan atómicamente; entries del kit sin `_sdd_kit` (ninguno en este spec, regla de futuro) se mantienen en destino; entries del destino sin `_sdd_kit` se preservan intactos (opt-out). La función queda con dos secciones independientes (`mcp` y `agent`), cada una con su discriminador.
3. **Idempotencia byte-a-byte preservada.** `_write_json` (`config_cableado.py:97-104`) ya garantiza byte-identidad con `indent=2` y orden estable de dict Python 3.7+. La nueva pasada sobre `agent` no altera el orden de `mcp`, ni viceversa. Test dedicado `test_u0008_agent_idempotent_*` extiende el patrón de `test_u0002_ca52_*` (`installer/tests/test_config_cableado.py:208-224`) con la sección `agent`.

Cobertura doble con Claude Code no aplica aquí: Claude Code lee `.mcp.json` (no `opencode.jsonc`); el mapping `agent.<rol>.model` es Opencode-only. `.claude/agents/*.md` espejos materializados (U-0005) siguen siendo la fuente canónica de prompts para Claude; el mapping por-modelo solo vive en `opencode.jsonc`.

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `sdd-kit/opencode.jsonc` | modificar | Añadir sección `agent` con 18 entries (9 base + 9 variantes), cada uno `{_sdd_kit: true, model: "minimax/..."}`. Mantener `mcp.pce-mcp` intacto. No añadir `provider`/`default_agent`/`model` global. |
| `sdd-kit/installer/config_cableado.py` | modificar | En `mezclar_opencode_jsonc` (líneas 222-269), añadir pasada de fusión con marcador sobre `agent` antes de `_write_json`: (a) barrido de entries con `_sdd_kit: true` del destino, (b) merge preservando entries ajenos sin `_sdd_kit`, (c) reemplazo atómico de entries del kit con `_sdd_kit`. Resto de la función intacto. |
| `sdd-kit/installer/tests/test_u0008_agent_model_mapping.py` | crear | Tests del mapping por rol: (a) 18 entries presentes, (b) split M3/M2.7-highspeed según CA-05, (c) familia `minimax/*` (CA-04), (d) `_sdd_kit: true` en cada entry (CA-06), (e) ausencia de `model`/`default_agent`/`provider` global (CA-10). |
| `sdd-kit/installer/tests/test_u0008_agent_opt_out.py` | crear | Tests del cableado: (a) destino con `agent.<rol>` sin `_sdd_kit` → preservado intacto (CA-07, opt-out), (b) destino con `agent.<rol>` con `_sdd_kit` → reemplazado atómicamente (CA-07), (c) entries ajenos sin `_sdd_kit` sobreviven al `--install` (CA-08). |
| `sdd-kit/installer/tests/test_u0008_agent_idempotent.py` | crear | Test de idempotencia: dos llamadas consecutivas a `mezclar_opencode_jsonc` producen `agent` byte-idéntico (CA-09), extiende patrón de `test_u0002_ca52_*` (`installer/tests/test_config_cableado.py:208-224`). |

> No se toca `installer/kit_manifest.yaml` (CA-11), `.spec/protocolo-datos.yaml` (CA-12), `.spec/perfiles.yaml` (CA-12), `.agents/agents/*.md` (CA-12), ni `.claude/agents/*.md` (espejos materializados, U-0005).

## Reutilización (no reinventar)

- `installer/config_cableado.py:31-32` — constantes `DISCRIMINATOR`/`TRUE`.
- `installer/config_cableado.py:222-269` — `mezclar_opencode_jsonc` con su pasada sobre `mcp`. La nueva pasada sobre `agent` reusa la misma estructura (barrido previo, merge, write).
- `installer/config_cableado.py:97-104` — `_write_json` con `indent=2` y orden estable (idempotencia byte-a-byte ya vigente).
- `installer/config_cableado.py:61-81` — `_strip_jsonc` para el caso de JSONC con `//` comments. En esta unidad `opencode.jsonc` queda como JSON estricto (sin comments) — ver CA-01 y DD-2 — pero el patrón de stripping ya está disponible si una unidad posterior reintroduce comments.
- `installer/tests/test_config_cableado.py:208-224` — `test_u0002_ca52_opencode_jsonc_fusion_idempotent_and_preserves_others`. Patrón para el test de idempotencia de `agent` (CA-09).
- `installer/tests/test_u0007_mcp_opt_out.py` — patrón completo de opt-out + reemplazo atómico + preservación de ajenos para los tests de `agent` (CA-07/CA-08).
- `.agents/agents/*.md` y `.claude/agents/*.md` — fuente canónica de los 18 nombres de rol a mapear (no se modifican).

## Decisiones de diseño

- **DD-1 — Forma del entry: `{_sdd_kit: true, model: "minimax/..."}`.** — **Razón:** simetría exacta con el patrón MCP (U-0007 CA-09). El discriminador es por entry, no por sección, así el merge reconoce cada agente individualmente. Solo `model` se declara en cada entry — el resto de configuración de agente (prompt, tools, description) lo provee el harness desde `.agents/agents/*.md` o `.claude/agents/*.md` según el ecosistema.
- **DD-2 — Sin `provider`/`default_agent`/`model` global en el kit, y sin `//` comments en `opencode.jsonc`.** — **Razón:** P1+P2 del spec. El provider `minimax` es nativo del harness (verificado `opencode models`); auth es runtime del operador. `default_agent` es preferencia del operador del destino — declararlo en el kit sería pisar sin marcador. `model` global tendría el mismo problema. Test CA-10 los greps negativo. Sin comments porque (a) el kit es producción y los comments del repo viven en spec/plan/tasks, no en el archivo cargado; (b) comments rompen los `python3 -c "import json; json.load(...)"` literales de CA-01/02/03/04/06; (c) un regex naive `re.sub(r'//[^\n]*','',t)` strip también `https://` del `$schema`. Si una unidad posterior reintroduce comments, el patrón completo vive en `installer/config_cableado.py:_strip_jsonc:61-81`.
- **DD-3 — M3 para roles de razonamiento profundo, M2.7-highspeed para el resto.** — **Razón:** el split replica la capacidad de cada rol declarada en `.spec/perfiles.yaml` (roles críticos profundos / especificadores / planificadores / refutador → `sonnet`; resto → `haiku`). El split MiniMax es funcionalmente análogo (M3 ≈ opus/sonnet capability; M2.7-highspeed ≈ haiku speed/cost) — no es calibración empírica, es la traducción literal. Si el operador quiere reasignar, edita `opencode.jsonc` del destino sin `_sdd_kit: true` (opt-out por entry).
- **DD-4 — Opt-out simétrico a U-0007 CA-07: si el destino tiene `agent.<rol>` sin `_sdd_kit`, el entry del kit no se agrega.** — **Razón:** el operador que reemplaza un agente del kit con su propio wrapper (otro modelo, otra config) debe quedar intacto. La invariante es: `dest["agent"]` post-fusión contiene exactamente las entries que el operador quiere, con las del kit marcadas para reemplazo atómico y las ajenas preservadas.
- **DD-5 — Tests dedicados `test_u0008_*.py`, no parametrización de u0002/u0007.** — **Razón:** P4 del spec — trazabilidad CA-NN → test por nombre. La unidad anterior (U-0007) usó la misma convención (`test_u0007_env_sync.py`, `test_u0007_mcp_opt_out.py`); mantener la convención evita ambigüedad de selector y permite correr `pytest -k u0008` para aislar la unidad.

## Grupos de tareas paralelizables

| Grupo | Alcance | Archivos (disjuntos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | Mapping del kit: declarar 18 entries en `opencode.jsonc > agent` con `_sdd_kit` y `model` | `sdd-kit/opencode.jsonc` | — | estandar |
| G2 | Cableado: extender `mezclar_opencode_jsonc` con pasada sobre `agent` (barrido + merge + reemplazo atómico + opt-out) | `sdd-kit/installer/config_cableado.py` | — | estandar |
| G3 | Tests: suite u0008 (mapping + opt-out + idempotencia) | `sdd-kit/installer/tests/test_u0008_*.py` | G1, G2 | estandar |

> G1 y G2 corren en paralelo (config del kit vs cableado). G3 los reúne y los ejercita — depende de ambos porque los tests leen el shape de `opencode.jsonc` (G1) y el comportamiento del cableado (G2).

## Riesgos y mitigaciones

- **R1 — Regresión en `test_u0002_ca52_*` idempotencia.** — **Mitigación:** la modificación de `config_cableado.py` es aditiva — nueva pasada sobre `agent`, sin tocar la pasada `mcp`. `pytest installer -q -k u0002` debe pasar 58/58 antes de cerrar (precedente U-0007 R1).
- **R2 — Opt-out del agente demasiado permisivo (operador olvida marcador, re-install borra su config).** — **Mitigación:** semántica explícita simétrica a U-0007 CA-07. Cualquier re-instalación deliberada puede restaurar el comportamiento kit — el operador agrega `_sdd_kit: true` al entry del destino. Log INFO de reemplazo atómico queda para una unidad posterior (P3 del spec, simétrico a U-0007 P3).
- **R3 — Disco no byte-idéntico entre dos `--install` consecutivos.** — **Mitigación:** `_write_json` con `indent=2`; el orden del dict `agent` en `json.dumps` sigue el orden de inserción (Python 3.7+). La rama nueva de `agent` no altera el orden de entries ya en `dest_servers`/`dest_agents`. Test `test_u0008_agent_idempotent.py` verifica con `assert first["agent"] == second["agent"]` tras dos llamadas (CA-09).
- **R4 — CA-11/CA-12 fallan por regresión preexisting (precedente U-0006/U-0009).** — **Mitigación:** el plan no toca `installer/kit_manifest.yaml`, `.spec/protocolo-datos.yaml`, `.spec/perfiles.yaml`, ni `.agents/agents/*.md`; CA-11/CA-12 son verificables con `grep` literal + `git diff --stat`. Si el gate de código los marca en rojo, es regresión preexisting y se cierra como `refinado` con la nota literal en `_estado.yaml > gates.codigo.hallazgos`.

## Comando de validación

```bash
python3 -m pytest installer -q -k u0008
```

Comandos auxiliares del crítico de contexto fresco en el gate de código (ver `spec.md § Criterios de aceptación`):

```
python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); print('ok')"
python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert 'model' not in d and 'default_agent' not in d and 'provider' not in d; print('ok')"
python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); a=d['agent']; assert all(k in a for k in ['sdd-critico-cumplimiento','sdd-critico-estructural','sdd-critico-profundo','sdd-especificar-redactor','sdd-explorador','sdd-implementador','sdd-planificar-redactor','sdd-refutador','sdd-tareas-redactor']); print('ok')"
python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert all(v['model'].startswith('minimax/') for v in d['agent'].values()); print('ok')"
python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert all(v.get('_sdd_kit') is True for v in d['agent'].values()); print('ok')"
! grep -E '"model"\s*:\s*"|"default_agent"\s*:\s*"|"provider"\s*:\s*\{' sdd-kit/opencode.jsonc
grep -nE 'source: opencode\.jsonc' installer/kit_manifest.yaml
grep -A4 'modelos_permitidos:' .spec/protocolo-datos.yaml
```
