# Migración desde el kit SDD

El kit SDD (`.spec/`, las skills, agentes y comandos `sdd-*`, `installer/`, sus guardias y hooks) se
retiró de este repositorio. Esta guía queda para quien aún lo tiene instalado en otro repositorio: qué
hace Railspec con cada pieza del kit, cómo traer unidades y cómo quitarlo. No hay migración
automática: cada unidad se trae cuando tú lo pides.

**Dónde queda el kit.** En el historial de git: el último commit de `master` que todavía lo trae es
`b49d427` (2026-10-08). Para volver a instalarlo en un destino, descarga ese commit y corre su
`python3 installer/cli.py --target <ruta-destino> --install`. Ya no se mantiene.

**Lo que ya no se prueba aquí.** `installer/tests/test_convivencia_railspec.py` instalaba el kit y
`railspec instalar` en repositorios temporales, en los dos órdenes, y comprobaba que ninguno pisaba al
otro, y ejecutaba el procedimiento de «Quitar el kit» tal cual está más abajo. Se retiró con el
instalador. Lo que dicen las secciones «Convivir en un repositorio» y «Quitar el kit» se probó por
última vez en `b49d427`.

## Equivalencias

| Kit | En Railspec | Estado |
| --- | --- | --- |
| `/sdd`, skill `sdd-orquestar` | `/railspec <petición>` llama `unit_start`; la skill `railspec-bucle` repite `unit_advance`, ejecutar y `unit_report`. En Codex y Copilot es la skill `railspec` (`$railspec`, `/railspec`) | Equivale. Dicta el servidor, no el arnés |
| `sdd-especificar`, `sdd-planificar`, `sdd-tareas` | Órdenes de redactar `spec`, `plan` y `tasks` que emite el servidor; los artefactos quedan en `.railspec/unidades/<unidad>/` del worktree | Equivale |
| `sdd-implementar` | Orden de implementar por grupo del plan, en el worktree de la unidad (`railspec/<unidad>`, hermano del clon); `unit_report` corre el comando de validación | Equivale |
| `sdd-gate` y los agentes críticos y refutadores | El gate corre en el servidor (panel de críticos y refutador, tope según riesgo y perfil). El arnés no lo invoca | Equivale, pero no hay comando para correrlo a mano |
| `sdd-retomar` | `/railspec NNNN-slug`, `unit_status` o `railspec estado --unidad NNNN-slug` | Equivale |
| Modos `interactivo` y `semi-autonomo` | Los mismos nombres: `interactivo` abre `aprobar-spec` y `aprobar-plan`; `semi-autonomo`, un `paquete-aprobacion` | Equivale |
| `sdd-supervisado`, `sdd-desatendido`, `sdd-preflight` | Los modos descansan en un mandato que aprueba una persona desde la consola ([mandato.md](mandato.md)) | Equivale en lo esencial |
| `sdd-perfil` y `.spec/perfiles.yaml` | Una unidad nueva nace con el «perfil por defecto» del workspace (`estandar` si el workspace no está registrado; `/railspec` no pasa perfil) y una importada trae el del kit; los perfiles por rol (modelo, effort) de cada organización o workspace se editan en la consola | Distinto: no hay `/sdd-perfil` ni archivo por clon |
| `kit-doctor` | `railspec doctor` (solo lectura: repositorio, comando, servidor, sesión, contrato, adaptadores, indexador y worktrees; no mira `pce-mcp` ni `gitnexus`), `railspec instalar --verificar` (deriva de los adaptadores), `railspec estado` y, en el servidor, `/livez` y `/healthz` | Parcial |
| `pce-mcp` en el clon | El servidor consulta la gobernanza por su cuenta en cada gate | Ver «pce-mcp y kit-doctor» |
| Hooks `guard_*` de `.spec/scripts` | `railspec hook <arnés>` aplica las reglas de conducta con una unidad en curso | Distinto: otras reglas |
| Gate de pre-push (`.spec/scripts/pre-push-gate.sh`) | No hay equivalente | Se retira con el kit |

## Convivir en un repositorio

El kit y Railspec escriben en cuatro archivos comunes. Cada uno reconoce lo suyo por una
marca y no toca lo demás:

| Archivo | El kit | Railspec |
| --- | --- | --- |
| `.mcp.json` | `mcpServers.pce-mcp`, con `_sdd_kit: true` | `mcpServers.railspec` |
| `opencode.jsonc` | `mcp.pce-mcp` y los `agent.sdd-*`, con `_sdd_kit: true` | `mcp.railspec` y `permission.railspec_*` |
| `.claude/settings.json` | Entradas de `hooks.PreToolUse` con `_sdd_kit: true` (`guard_generated_paths`, `guard_bash_spec_writes`) | Una entrada de `hooks.PreToolUse` con el comando `railspec hook claude-code`, y `permissions.allow` y `ask` |
| `AGENTS.md` | Todo el archivo (el contrato agéntico del kit) | Un bloque entre `<!-- railspec:inicio … -->` y `<!-- railspec:fin -->`, al final |

Lo que no es de ninguno de los dos (un servidor MCP tuyo, un hook, un permiso, la clave
`theme` de OpenCode) se queda como estaba. `CLAUDE.md` solo lo toca Railspec, también con un
bloque delimitado. Los espejos del kit en `.claude/commands/` y `.claude/skills/` no copian ni
podan lo que se llama `railspec*` (`railspec.md`, `railspec-bucle/`), ni copian a `.claude/skills/`
las skills de Codex de `.agents/skills/railspec*`.

- **El orden da igual.** Se puede instalar primero el kit y luego `railspec instalar`, o al
  revés: reinstalar o actualizar el kit conserva lo de Railspec, y reinstalar Railspec conserva lo
  del kit.
- **La verificación no ve deriva por lo del otro.** El verificador del kit mide en esos cuatro
  archivos solo la parte del kit (las entradas con `_sdd_kit` y, en `AGENTS.md`, todo menos el
  bloque de Railspec), y `railspec instalar --verificar` mide solo lo de Railspec.
- **`AGENTS.md` es del kit.** Cada `--install` lo sobrescribe: la prosa propia que le hayas
  añadido no sobrevive (el bloque de Railspec sí). Si la necesitas, ponla en otro archivo.
- **Un JSON ilegible no se pisa.** Si `.mcp.json`, `opencode.jsonc` o `.claude/settings.json`
  no se pueden leer, el instalador del kit termina con código 2 sin escribir nada, y
  `railspec instalar` también se niega. Arréglalo y repite.
- **Los comentarios de `opencode.jsonc` se pierden.** Tanto el kit como Railspec lo reescriben
  como JSON.
- **Los dos hooks de Claude Code corren.** El del kit rechaza escrituras con las herramientas
  del arnés en `.claude/skills/`, `.claude/commands/` y `.claude/agents/sdd-*`; el de Railspec
  rechaza, con una unidad en curso, escribir fuera de la orden vigente. `railspec instalar`
  escribe sus archivos desde fuera del arnés, así que no choca con el primero; no los edites
  a mano con el arnés.

### Un aviso sobre el pre-push

El kit instala `.git/hooks/pre-push`, que llama a `.spec/scripts/pre-push-gate.sh`. Cuando un
push toca rutas del protocolo, ese gate exige una corrida verde registrada (`.spec/.pilot-verde`)
y un `.spec/protocolo-datos.yaml`; `.agents/skills/` es una de esas rutas. **El adaptador de Codex
escribe `.agents/skills/railspec/` y `.agents/skills/railspec-bucle/`**, así que el primer push con
el adaptador de Codex en el diff se rechaza en un repositorio con el kit. Claude Code, OpenCode y
Copilot no escriben en rutas del protocolo y pasan.

**Si tu clon tiene ese gancho y el kit ya no está en el árbol, quítalo antes de volver a hacer
push:** el gancho sigue llamando a un script que ya no existe y todo `git push` fallaría. Desde la
raíz del clon:

```
head -2 .git/hooks/pre-push | grep -qF "# sdd-kit pre-push" && rm .git/hooks/pre-push
```

(Si usas `core.hooksPath`, es el `pre-push` de ese directorio.) El gancho es del kit solo si su
primera o segunda línea dice `# sdd-kit pre-push`; cualquier otro `pre-push` es tuyo y no se toca.

## Traer unidades

```
railspec importar .spec/units/0007-pce-mcp                    # una unidad del kit
railspec importar .spec/units/* --solo-convertir paquetes/    # revisar antes, sin servidor
```

Necesita `railspec instalar` hecho, `RAILSPEC_URL` en el entorno y una sesión (`railspec login`
o `RAILSPEC_TOKEN`); `--solo-convertir` no contacta el servidor. Solo lee `.spec/units/<id>/`: la unidad del kit
queda donde estaba. Se trae por unidad y cuando tú lo pides; ver
[proxy-local.md](proxy-local.md#importar-y-exportar-unidades) para el formato del paquete. Las
unidades de este mismo repositorio ya no están en el árbol: siguen en el historial, en `b49d427`.

- Viajan `_estado.yaml`, `spec.md`, `plan.md` y `tasks.md`. La bitácora y el resto no viajan.
- Los artefactos que la fase de origen da por cerrados quedan aprobados por importación, siempre
  como prefijo spec, plan, tasks; el de la fase en curso es un borrador, y si falta uno la
  unidad retoma en su fase. El servidor pasa los artefactos por su plantilla y, si uno no encaja
  (lo habitual con el kit), vuelve a refinarlo antes de seguir.
- **Supervisado y desatendido entran como interactivo.** Esos modos exigen un mandato y el
  paquete no lo trae, así que la unidad nace `interactivo`, con un aviso, y el modo lo fijas tú
  después. Los modos `interactivo` y `semi-autonomo` se conservan.
- Riesgo, perfil, `governance_refs` y comando de validación viajan; las dependencias y el
  historial de gates, solo como información. Una unidad cerrada entra cerrada, con el gate de
  código escalado por `importado` hasta que quien importa lo rehabilita.
- Antes de salir del clon se buscan secretos en los artefactos y en el pedido: si hay, la
  importación se rechaza.
- Importar dos veces el mismo origen devuelve la unidad ya creada. `unit.import` lo hacen
  solo humanos.

## Quitar el kit

El kit no trae desinstalador. Este procedimiento quita lo que él instaló en un destino y deja a
Railspec como estaba (`railspec instalar --verificar` sigue limpio). Ya no lo ejecuta ninguna prueba
(ver arriba). Sirve en un repositorio donde el kit se instaló con `installer/cli.py`, porque lee el
registro de instalación en `.git/`; este repositorio ya no lo necesita.

Antes, importa o archiva lo que quieras conservar de `.spec/units/`: no es parte de la carga del kit
y el procedimiento no lo toca, pero tampoco lo mueve. Luego, desde la raíz del repositorio:

```python
import re
import subprocess
from hashlib import sha256
from json import dumps, loads
from pathlib import Path


def git(*args):
    return subprocess.check_output(["git", *args], text=True).strip()


raiz = Path(git("rev-parse", "--show-toplevel"))
registro = Path(git("rev-parse", "--absolute-git-dir")) / "sdd-kit-install-record.yaml"
if not registro.is_file():
    raise SystemExit("No hay registro de instalación del kit en este repositorio.")
rutas = re.findall(r'^  "([^"]+)": "([0-9a-f]{64})"$', registro.read_text(encoding="utf-8"), re.M)
COMPARTIDOS = {".mcp.json", "opencode.jsonc", ".claude/settings.json", "AGENTS.md"}
conservados = []


def borrar(ruta):
    ruta.unlink()
    padre = ruta.parent
    while padre != raiz and not any(padre.iterdir()):
        padre.rmdir()
        padre = padre.parent


# 1. El gancho de pre-push, solo si es del kit.
subprocess.run(["bash", "scripts/install_pre_push_hook.sh", "--uninstall"], cwd=raiz)

# 2. Los archivos de la carga que siguen como los dejó el kit; los que cambiaste se conservan.
for rel, digest in rutas:
    ruta = raiz / rel
    if rel in COMPARTIDOS or not ruta.is_file():
        continue
    if sha256(ruta.read_bytes()).hexdigest() == digest:
        borrar(ruta)
    else:
        conservados.append(rel)

# 3. Los espejos de .claude/ (agentes, comandos y skills): lo que lista cada manifiesto.
for manifiesto in raiz.glob(".claude/*/.claude-*-manifest.yaml"):
    for rel in re.findall(r'^\s+- path: "([^"]+)"$', manifiesto.read_text(encoding="utf-8"), re.M):
        if (manifiesto.parent / rel).is_file():
            borrar(manifiesto.parent / rel)
    borrar(manifiesto)


# 4. Las entradas del kit en los JSON compartidos; lo demás se queda.
def sin_kit(valor):
    marcada = lambda v: isinstance(v, dict) and v.get("_sdd_kit") is True  # noqa: E731
    if isinstance(valor, dict):
        resto = {}
        for clave, v in valor.items():
            if marcada(v):
                continue
            nuevo = sin_kit(v)
            if nuevo != v and nuevo in ({}, []):  # quedó vacío al quitar lo del kit
                continue
            resto[clave] = nuevo
        return resto
    if isinstance(valor, list):
        return [sin_kit(v) for v in valor if not marcada(v)]
    return valor


for rel in (".mcp.json", "opencode.jsonc", ".claude/settings.json"):
    ruta = raiz / rel
    if not ruta.is_file():
        continue
    datos = sin_kit(loads(ruta.read_text(encoding="utf-8")))
    if datos in ({}, {"$schema": "https://opencode.ai/config.json"}):
        borrar(ruta)
    else:
        ruta.write_text(dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")

# 5. AGENTS.md es del kit: se queda solo el bloque de Railspec, si lo hay.
agentes = raiz / "AGENTS.md"
if agentes.is_file():
    bloque = re.search(
        r"<!-- railspec:inicio.*?<!-- railspec:fin -->\n?", agentes.read_text(encoding="utf-8"), re.S
    )
    if bloque:
        agentes.write_text(bloque.group(0), encoding="utf-8")
    else:
        borrar(agentes)

# 6. El registro de instalación.
registro.unlink()
print("Kit desinstalado. Conservados por tener cambios tuyos:", ", ".join(conservados) or "ninguno")
```

Quedan `.spec/units/`, `.spec/protocolo-datos.yaml` y lo que haya en `.spec/.usage/`: son tuyos y puedes
borrarlos a mano. Después de quitar el kit, `python3 installer/cli.py` ya no existe en el destino.

## pce-mcp y kit-doctor

**`pce-mcp`.** Era el servidor MCP local del kit (`scripts/mcp-pce.sh`, entrada `pce-mcp` en
`.mcp.json` y `opencode.jsonc`) con el que el arnés consultaba la gobernanza. Railspec no lo
necesita en el clon: el servidor consulta la gobernanza por su cuenta en cada gate, con
`RAILSPEC_PCE_URL` y `RAILSPEC_PCE_API_KEY` o con la herramienta de contexto de la organización
([proveedores.md](proveedores.md#herramientas-de-contexto)); sin ninguna, todo gate escala con
`sin-gobernanza`. Este repositorio ya no lo declara. En un destino con el kit, que `pce-mcp` esté
caído no afecta al bucle de Railspec, pero sí a las skills del kit que lo consultan.

**`kit-doctor`.** Diagnosticaba `pce-mcp`, `gitnexus`, `codebase-memory-mcp` y el índice de grafo
local del kit. Railspec no usa `gitnexus`: su grafo es el del servidor (`graph_query`). Sí usa
`codebase-memory-mcp` como indexador local opcional
([proxy-local.md](proxy-local.md#indexador-local)), por su cuenta. Para Railspec, `railspec doctor`
diagnostica sin cambiar nada el proxy y su entorno (repositorio, comando, servidor, sesión,
contrato, adaptadores, indexador y worktrees; no mira `pce-mcp` ni `gitnexus`); además, los
adaptadores se revisan con `railspec instalar --verificar`, el estado de una unidad con
`railspec estado`, y el servidor con `/livez` y `/healthz`.

## Lo que queda abierto

- No hay desinstalador del kit ni migración masiva de unidades.
- La importación necesita un servidor desplegado; sin él solo sirve `--solo-convertir`.
- Sin el kit no queda una segunda red de seguridad: si Railspec falla con modelos reales, este
  repositorio ya no tiene el protocolo anterior al que volver sin pasar por el historial.
