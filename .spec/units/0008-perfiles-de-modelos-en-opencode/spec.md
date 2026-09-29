# Spec — Perfiles de modelos en opencode.jsonc

> Fase 1 (Especificar). Define el QUÉ y el POR QUÉ. **No** describe el cómo técnico (eso va en `plan.md`).

## Problema / Motivación

El kit declara agentes canónicos en `.agents/agents/*.md` (con espejos materializados en `.claude/agents/*.md` para Claude Code) y un contrato de harness doble — Claude Code lee `.mcp.json`, Opencode lee `opencode.jsonc` — fusionados con marcador `_sdd_kit: true` por `installer/config_cableado.py` (U-0002 CA-45..CA-52, U-0007 CA-01..CA-15). Sin embargo, el harness Opencode disponible en este destino tiene catálogo nativo `minimax/MiniMax-M{3,2.7,2.7-highspeed,2.5,2.5-highspeed,2.1,2}` (verificado con `opencode models`), mientras que `.spec/perfiles.yaml:6-14` declara `opus/sonnet/haiku` para los roles del kit (Anthropic) y `.spec/protocolo-datos.yaml:13-16` fija `modelos_permitidos: [opus, sonnet, haiku]`. Si un operador invoca el harness sin mapping local, Opencode cae al `default_agent` con `model` propio del destino, que no es lo que el kit declara como flujo SDD. Hoy no hay forma de que el operador declare por-agente "qué modelo corre este rol del kit en este harness" sin editar `.agents/agents/*.md` o `.spec/perfiles.yaml` — dos archivos fuera del alcance de este spec (U-0005 fija los espejos como derivados, y `.spec/perfiles.yaml` es contrato del protocolo).

## Resultado esperado

`opencode.jsonc` del kit declara `agent.<rol>.model` para cada rol canónico del kit (incluidos los espejos `*-low`, `*-high`, `*-max`, `*-medium`, `*-xhigh`) mapeando a modelos de la familia `minimax/*`, y la fusión con marcador de `installer/config_cableado.py:mezclar_opencode_jsonc` preserva la sección `agent` a través del `--install` (idempotente, byte-a-byte entre dos runs, opt-out simétrico al patrón U-0007 CA-07). El catálogo del protocolo (`.spec/protocolo-datos.yaml`) queda intacto — es del alcance de gobernanza del destino, no del harness.

## Alcance

**Incluye:**

- Modificar `sdd-kit/opencode.jsonc` para añadir la sección `agent` con mapping por rol (9 base + 9 variantes `*-low|high|max|medium|xhigh` = 18 entries) a modelos MiniMax nativos del harness verificados por `opencode models`.
- Modificar `sdd-kit/installer/config_cableado.py:mezclar_opencode_jsonc` para que la fusión con marcador reconozca entries de `agent` con `_sdd_kit: true` (reemplazo atómico simétrico a U-0007 CA-09), respete opt-out (entry sin marcador → intacto, simétrico a U-0007 CA-07), y preserve los entries de `agent` sin marcador (simétrico a U-0007 CA-11).
- Tests en `installer/tests/` que verifiquen: (a) el mapping existe para todos los roles del kit, (b) los modelos son de la familia `minimax/*`, (c) la fusión con marcador preserva `agent` entre dos `--install` consecutivos (idempotencia), (d) opt-out respetado.

**No incluye (fuera de alcance):**

- Migrar `.spec/protocolo-datos.yaml:modelos_permitidos` ni `.spec/perfiles.yaml` a la familia MiniMax. Es catálogo del protocolo y de los roles, no del harness — pertenece a gobernanza del destino, no a esta unidad.
- Modificar `.agents/agents/*.md` (fuentes canónicas de los roles) ni `.claude/agents/*.md` (espejos materializados por `scripts/materialize_claude_*.py`).
- Declarar o autenticar el provider `minimax/*` en el destino — el harness ya lo lista en `opencode models` y la autenticación es runtime del operador (`opencode auth login`).
- Configurar `default_agent` ni `model` global en `opencode.jsonc` — el operador del destino decide eso en su propia capa, no en la carga del kit.
- Cambiar `mezclar_mcp_json` ni `mezclar_claude_settings` (mismo patrón, sin cambios en este spec).
- Tocar `installer/kit_manifest.yaml` (la entrada `source: opencode.jsonc` ya existe, U-0007 CA-12).

## Criterios de aceptación

- [ ] **CA-01** — `opencode.jsonc` parsea como JSON estricto (sin comentarios JSONC, ver DD-2): `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); print('ok')"` imprime `ok` y exit 0.
- [ ] **CA-02** — `opencode.jsonc` declara `agent` con un entry por cada rol canónico del kit (los 9 base de `.agents/agents/*.md`): `sdd-critico-cumplimiento`, `sdd-critico-estructural`, `sdd-critico-profundo`, `sdd-especificar-redactor`, `sdd-explorador`, `sdd-implementador`, `sdd-planificar-redactor`, `sdd-refutador`, `sdd-tareas-redactor`. Verificable con `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); a=d['agent']; assert all(k in a for k in ['sdd-critico-cumplimiento','sdd-critico-estructural','sdd-critico-profundo','sdd-especificar-redactor','sdd-explorador','sdd-implementador','sdd-planificar-redactor','sdd-refutador','sdd-tareas-redactor'])"` exit 0.
- [ ] **CA-03** — `opencode.jsonc` declara los espejos `*-low|high|max|medium|xhigh` materializados en `.claude/agents/` (los 9: `sdd-critico-profundo-low`, `sdd-critico-profundo-max`, `sdd-especificar-redactor-medium`, `sdd-especificar-redactor-xhigh`, `sdd-implementador-high`, `sdd-implementador-low`, `sdd-implementador-max`, `sdd-implementador-xhigh`, `sdd-refutador-max`). Mismo patrón de verificación que CA-02.
- [ ] **CA-04** — Cada `agent.<rol>.model` declarado es de la familia `minimax/`: `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert all(v['model'].startswith('minimax/') for v in d['agent'].values()); print('ok')"` imprime `ok`.
- [ ] **CA-05** — Roles críticos profundos (`sdd-critico-profundo` y sus variantes, `sdd-especificar-redactor` y sus variantes, `sdd-planificar-redactor`, `sdd-refutador` y su variante `*-max`) están mapeados a `minimax/MiniMax-M3`. Roles de implementación, exploración, crítica estructural y cumplimiento (`sdd-implementador` y sus variantes, `sdd-explorador`, `sdd-tareas-redactor`, `sdd-critico-estructural`, `sdd-critico-cumplimiento`) están mapeados a `minimax/MiniMax-M2.7-highspeed`. Verificable con un test `installer/tests/test_u0008_agent_model_mapping.py` que enumere los 18 entries.
- [ ] **CA-06** — Cada `agent.<rol>` declarado por el kit lleva `_sdd_kit: true` como discriminador de fusión: `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert all(v.get('_sdd_kit') is True for v in d['agent'].values())"` exit 0.
- [ ] **CA-07** — `mezclar_opencode_jsonc` preserva la sección `agent` durante la fusión con marcador: si el destino tiene un entry `agent.<rol>` **sin** `_sdd_kit: true`, el kit NO lo sobreescribe (opt-out simétrico a U-0007 CA-07). Si el destino tiene un entry **con** `_sdd_kit: true`, el kit lo reemplaza atómicamente (reemplazo atómico simétrico a U-0007 CA-09). Test `installer/tests/test_u0008_agent_opt_out.py`.
- [ ] **CA-08** — `mezclar_opencode_jsonc` NO elimina entries de `agent` ajenos al kit (sin `_sdd_kit`). Test cubre preservación de entries no-controlados (simétrico a U-0007 CA-11).
- [ ] **CA-09** — Dos llamadas consecutivas a `mezclar_opencode_jsonc` sobre el mismo destino producen `agent` byte-idéntico (idempotencia, simétrica a U-0007 CA-10). Test con `agent` arbitrario del operador: `assert first["agent"] == second["agent"]`.
- [ ] **CA-10** — `opencode.jsonc` del kit NO declara `model` global, NO declara `default_agent`, y NO declara `provider` para MiniMax (la auth es runtime del operador, no del kit). `python3 -c "import json; d=json.load(open('sdd-kit/opencode.jsonc')); assert 'model' not in d and 'default_agent' not in d and 'provider' not in d; print('ok')"` imprime `ok` y exit 0 (las 3 claves ausentes a nivel raíz; `model` vive bajo `agent.<rol>` que es un dict anidado y NO cuenta como global).
- [ ] **CA-11** — `installer/kit_manifest.yaml` sigue declarando `source: opencode.jsonc, dest: opencode.jsonc` (línea ~161) y NO añade entradas nuevas. `grep -nE 'source: opencode\.jsonc' installer/kit_manifest.yaml` imprime exactamente 1 línea.
- [ ] **CA-12** — `.spec/protocolo-datos.yaml` queda intacto: `modelos_permitidos` sigue siendo `[opus, sonnet, haiku]`. `grep -A4 'modelos_permitidos:' .spec/protocolo-datos.yaml` imprime las mismas 4 líneas que antes de la unidad (línea 13 con `modelos_permitidos:` + 3 líneas `- opus/- sonnet/- haiku`). No se toca `.spec/perfiles.yaml` ni `.agents/agents/*.md` (verificable con `git diff --stat` mostrando 0 modificaciones en esos paths).

## Governance aplicable

`governance_refs: [ninguna-aplicable]` en `_estado.yaml`. El kit no se gobierna a sí mismo vía `pce-mcp` (`AGENTS.md § Gobernanza`; U-0005, U-0007 precedentes con centinela aceptado por el operador). El cambio al harness Opencode queda bajo el contrato del cableado (`installer/config_cableado.py`), cuyo discriminador `_sdd_kit` y semántica de fusión están fijados por `config_cableado.py:31-32, 222-269` y extendidos aquí con simetría al patrón U-0007 CA-07..CA-11. No se introduce ADR ni policy nueva; `.spec/protocolo-datos.yaml` queda fuera del alcance del kit por construcción (U-0002 § "Configuración del entorno", `kit_manifest.yaml:14-16`).

## Preguntas abiertas

- **P1 (cerrada con suposición — conservadora):** Forma del provider `minimax/*` → NO se declara en `opencode.jsonc`. Razón: el provider es nativo del harness (`opencode models` lo lista, sin config extra); auth es runtime del operador (`opencode auth login`). `provider` global declarado sería sobre-ingeniería — solo lo agregaríamos si la verificación de un CA futuro lo exigiera.
- **P2 (cerrada con suposición — conservadora):** `default_agent` y `model` global → NO se declaran. Razón: el operador del destino decide su default en su capa; el kit solo aporta el mapping por-rol, no el default. Si el kit fija un default, pisa al operador sin marcador (mismo principio que el opt-out de U-0007 CA-07).
- **P3 (abierta para `plan.md`):** Si el destino trae `agent.<rol>` con `_sdd_kit: true` y un `model` distinto al del kit, ¿el reemplazo atómico debe loggear warning? Decisión del plan; conservadora: loggear en `INFO` durante el `--install` (no abortar), simétrico a U-0007 P3.
- **P4 (abierta para `plan.md`):** Tests del opt-out — ¿dedicado (`test_u0008_agent_opt_out.py`) o parametrizar CA-52? Decisión del plan; conservadora: dedicado para mantener trazabilidad CA-NN → test por nombre.
