# sdd-kit — Contrato Agéntico del kit

> Este archivo es **prosa de gobernanza** que el kit lleva como carga al
> destino (ver `installer/kit_manifest.yaml`). En un destino donde `pce-mcp`
> esté configurado, `pce-mcp` lo lee para evaluar el trabajo del agente.
> En el repo del kit mismo no se ejecuta `pce-mcp` (no hay superficie local —
> ver § Gobernanza).

Índice:

- [Propósito](#propósito)
- [Carga y destinos](#carga-y-destinos)
- [Configuración del entorno de desarrollo](#configuración-del-entorno-de-desarrollo)
- [Gobernanza](#gobernanza)
- [Reglas operativas](#reglas-operativas)
- [Spec-Driven Development (SDD)](#spec-driven-development-sdd)
- [Code intelligence — GitNexus y codebase-memory-mcp](#code-intelligence--gitnexus-y-codebase-memory-mcp)

## Propósito

`sdd-kit` es el **kit de autoinstalación** del protocolo Spec-Driven
Development (SDD). Su propósito es:

1. Declarar y versionar la **carga** (el conjunto de archivos que el protocolo
   necesita en un destino) en `installer/kit_manifest.yaml`.
2. Distribuir esa carga en un destino mediante `installer/cli.py --install`,
   fusionando con marcador los archivos de configuración del entorno
   (Claude y Opencode), y manejando el ciclo de actualización coherente con
   la premisa de reempaquetamiento (ver unit
   `0002-versionado-y-actualizacion-del-instalador`).
3. Proveer el **protocolo antidrift** (hook `pre-push` + cableado de guards
   `PreToolUse`) que impide que un destino acumule ediciones manuales sobre
   rutas del kit sin reempaquetar.
4. Llevar el **andamiaje de gates y modos** del SDD (`.spec/scripts/`,
   `.spec/_plantillas/`, `.spec/perfiles.yaml`, `scripts/kit_doctor.py`) para
   que el destino pueda operar el flujo.

`sdd-kit` **no es** una plataforma de modelado ni un orquestador: esos son
servicios de IArk (`../iark`), que es un destino que recibe este kit. El kit
no sabe nada de C4Model, `iark-executions`, ni `studio/`.

## Carga y destinos

El contrato de carga vive en `installer/kit_manifest.yaml` — **un solo
archivo** que nombra cada `source` y `dest`. La cobertura se divide en tres
clases:

- **Carga de protocolo** (motor genérico, prosa normativa, plantillas,
  perfiles): siempre parte de la carga. La declara el manifiesto.
- **Carga materializada** (`.claude/agents|commands|skills` desde
  `.agents/`): los espejos bajo `.claude/` los regenera el instalador en
  cada `--install` corriendo `scripts/materialize_claude_*.py`. Las fuentes
  canónicas en `.agents/` sí son carga; los espejos derivados no aparecen
  en el manifiesto.
- **Configuración del entorno** (ver siguiente sección): se fusiona con
  marcador, no se copia byte a byte.

Una ruta que el manifiesto declara y la línea base del destino no registra
es **colisión de ruta nueva** → abortar con código 3 sin escribir nada.
Una ruta que la línea base registra y el manifiesto ya no declara es
**huérfana** → retirar sin drift, retener con drift salvo `--force`. El
contrato exacto vive en la unit `0002-versionado-y-actualizacion-del-instalador`
(CA-01..CA-50).

## Configuración del entorno de desarrollo

El kit cubre **dos ecosistemas** de agente que coexisten en un mismo
destino: **Claude Code** y **Opencode**. Cada uno lee sus archivos de
configuración con forma y semántica propias. El kit los administra a todos
bajo el mismo contrato de fusión con marcador (`_sdd_kit: true` en cada
entrada propia del kit) para no pisar lo ajeno y mantener la idempotencia
entre dos `--install` consecutivos.

| Ecosistema | Archivos que el kit gestiona | Comportamiento |
|---|---|---|
| **Claude Code** | `.claude/settings.json` | Fusión con marcador en entries de `hooks.*` (CA-45..CA-47). Cableado de guards `PreToolUse` por el kit. |
| **Claude Code** | `.mcp.json` (raíz) | Fusión con marcador en entries de `mcpServers`. El kit declara `pce-mcp` y `scripts/mcp-pce.sh`. El destino puede tener otros `mcpServers` propios. |
| **Opencode** | `opencode.jsonc` (raíz) | Fusión con marcador en entries de `mcp`. Mismo patrón que Claude, con sintaxis JSONC. |
| **Opencode** | `.opencode/opencode.jsonc` | Reservado para configuración per-dir de Opencode si existe; el kit no lo crea hoy. |
| **Gobernanza** | `AGENTS.md` (raíz) | Este archivo. Se sobreescribe con el contenido del kit en cada `--install`; el destino no debería tener `AGENTS.md` propio (si lo tiene, ese es trabajo del destino y el kit lo pisa — decisión pendiente en unit `0002`). |
| **Espejos materializados** | `.claude/agents/`, `.claude/commands/`, `.claude/skills/` | Generados por `scripts/materialize_claude_*.py` desde `.agents/<surface>/`. No son carga directa del manifiesto. |

> **Cobertura doble.** Claude y Opencode leen archivos distintos con
> semánticas distintas, pero los **dos** apuntan a la misma herramienta:
> `scripts/mcp-pce.sh` en el destino. La unidad `0002` debe portar ambos
> archivos (`.mcp.json` y `opencode.jsonc`) en su carga, y la fusión con
> marcador debe respetar la forma JSON de cada uno. **No** es válido
> portar solo uno y dejar al otro como derivado: cada ecosistema tiene su
> propio ciclo de vida de configuración.

## Gobernanza

`sdd-kit` **no tiene superficie de gobernanza propia** en este repo: no
declara `AGENTS.md` en su propio git (este archivo es prosa que viaja al
destino, no prosa que el kit se aplica a sí mismo), y el `pce-mcp` que sus
configuraciones invocan (`scripts/mcp-pce.sh`) no está conectado al kit
mismo sino a cada destino. La consecuencia operativa es:

- En el repo del kit: `pce-mcp` no aplica. Las decisiones de fuente única
  (versión del kit, formato de línea base, ubicación del registro de
  instalación) se toman sin contrastar contra el catálogo de gobernanza.
  Esta es la salvedad registrada en unit `0002` (`spec.md` § Governance
  aplicable).
- En cada destino: `pce-mcp` **sí** aplica. El destino lee este `AGENTS.md`
  como prosa de gobernanza entregada por el kit, y `pce-mcp` (si está
  configurado) lo usa como entrada para evaluar el trabajo del agente en
  ese destino.

Las tres configuraciones que el kit lleva (`.mcp.json`, `opencode.jsonc`,
`AGENTS.md`) **deben viajar juntas** como una unidad: si una de las tres
llega sin las otras, el destino tiene un entorno agéntico a medias. Esto
lo fija la unit `0002` como un único requisito de cobertura.

## Reglas operativas

- **Comandos del instalador**: `python3 installer/cli.py --help` lista las
  operaciones y los códigos de salida. El contrato de `--install` está
  documentado en la unit `0002` (CA-01..CA-50).
- **Versión del kit**: una sola fuente, `installer/kit_manifest.yaml:
  kit_version`. Se imprime con `python3 installer/cli.py --version`. CA-43
  falla si la cadena aparece en otro archivo de datos del repo.
- **Línea base de instalación**: `<git_dir>/sdd-kit-install-record.yaml`
  fuera de la carga y del worktree. Por clon, no versionada.
- **Diagnóstico del entorno agéntico**: `python3 scripts/kit_doctor.py`
  (con `--help` para opciones). El script verifica MCP servers, hooks,
  pre-push gate y consistencia de carga.
- **Tests del kit**: `python3 -m pytest installer -q` (validación declarada
  en unit `0002`). El comando ampliado `python3 -m pytest .spec/scripts/tests
  installer -q` cubre también los tests del andamiaje.
- **Cualquier cambio a archivos que el kit lleva como carga** debe pasar
  por el flujo SDD (spec → plan → tasks → implementar) — la cobertura doble
  (Claude + Opencode) hace que un cambio en una sola superficie rompa el
  contrato.

## Spec-Driven Development (SDD)

El protocolo SDD rige toda unidad de trabajo de este repo. Sus piezas:

- **Fases**: `research` (opcional) → `spec` → `plan` → `tasks` →
  `aprobacion` (solo `semi-autonomo`) → `implement` → `done`.
- **Gates**: validación determinista + panel de críticos por fase.
  Perfil `estandar` por defecto (`/sdd-perfil ligero|estandar|profundo` para
  cambiar). Tier `bajo|medio|alto` se asigna en el triaje y dimensiona
  el presupuesto del gate.
- **Modos**: `interactivo` (default — checkpoints humanos), `semi-autonomo`
  (un paquete de aprobación antes de implementar), `supervisado` (un
  mandato gobierna la unidad), `desatendido` (tanda con aprobación upfront).
- **Plantillas y referencias**: `.spec/_plantillas/{spec,plan,mandato,
  plan-maestro,tasks}.md` y `.spec/scripts/`. Los agentes que ejecutan cada
  fase viven en `.agents/agents/sdd-*`.
- **Modelo de subagentes y esfuerzo**: `.spec/MODELO-AGENTES.md`.

Una unidad no puede saltarse el gate de su fase: aunque el humano la apruebe
manualmente, el veredicto del gate es lo que cierra la fase en
`_estado.yaml > gates`.

## Code intelligence — GitNexus y codebase-memory-mcp

Dos servidores MCP que viven en este repo y que el destino hereda por
carga (`.mcp.json` y `opencode.jsonc` los declaran ambos):

- **GitNexus** (`/opt/homebrew/bin/gitnexus mcp`): indexa el repo y provee
  herramientas de query sobre el grafo de código. Su uso por un agente es
  opt-in (no es mandatorio). El cache vive en `.gitnexus/`, excluido del
  worktree por `.gitignore`.
- **codebase-memory-mcp**: mantiene un grafo de conocimiento del codebase
  con búsqueda semántica y traversal. Cache en `.codebase-memory/`. Mismo
  principio de opt-in.

Ninguno de los dos es una superficie de gobernanza: no se les delega
decisiones MUST/SHOULD del protocolo SDD. Su rol es acelerar la
comprensión del código por los agentes.
