# Migración desde el kit SDD

Railspec y el kit SDD (`.spec/`, las skills y comandos `sdd-*`, `installer/`)
pueden vivir en el mismo repositorio. Esta guía describe lo que hay hoy: qué hace
Railspec con cada pieza del kit, cómo conviven los dos, cómo traer unidades y cómo
quitar el kit si lo decides. No retira el kit ni promete hacerlo, y no hay migración
automática: cada unidad se trae cuando tú lo pides.

Lo que afirma la sección «Convivir en un repositorio» lo prueba
`installer/tests/test_convivencia_railspec.py`: instala el kit y `railspec instalar` en
repositorios temporales, en los dos órdenes, y comprueba que ninguno pisa al otro. Corre en el CI
(entrada `installer` de la matriz de `railspec-ci.yml`, con railspec-local instalado) y en local con
`python -m pytest installer/tests`.

## Equivalencias

| Kit | En Railspec | Estado |
| --- | --- | --- |
| `/sdd`, skill `sdd-orquestar` | `/railspec <petición>` llama `unit_start`; la skill `railspec-bucle` repite `unit_advance`, ejecutar y `unit_report`. En Codex y Copilot es la skill `railspec` (`$railspec`, `/railspec`) | Equivale. Dicta el servidor, no el arnés |
| `sdd-especificar`, `sdd-planificar`, `sdd-tareas` | Órdenes de redactar `spec`, `plan` y `tasks` que emite el servidor; los artefactos quedan en `.railspec/unidades/<unidad>/` del worktree | Equivale |
| `sdd-implementar` | Orden de implementar por grupo del plan, en el worktree de la unidad (`railspec/<unidad>`, hermano del clon); `unit_report` corre el comando de validación | Equivale |
| `sdd-gate` y los agentes críticos y refutadores | El gate corre en el servidor (panel de críticos y refutador, tope según riesgo y perfil). El arnés no lo invoca | Equivale, pero no hay comando para correrlo a mano |
| `sdd-retomar` | `/railspec NNNN-slug`, `unit_status` o `railspec estado --unidad NNNN-slug` | Equivale |
| Modos `interactivo` y `semi-autonomo` | Los mismos nombres: `interactivo` abre `aprobar-spec` y `aprobar-plan`; `semi-autonomo`, un `paquete-aprobacion` | Equivale |
| `sdd-supervisado`, `sdd-desatendido`, `sdd-preflight` | Los modos existen en el contrato, pero hoy solo omiten los checkpoints de aprobación | Parcial: ver abajo |
| `sdd-perfil` y `.spec/perfiles.yaml` | Una unidad nueva nace `estandar` (`/railspec` no pasa perfil) y una importada trae el del kit; los perfiles por rol (modelo, effort) de cada organización o workspace se editan en la consola | Distinto: no hay `/sdd-perfil` ni archivo por clon |
| `kit-doctor` | No hay `railspec doctor`. Sirven `railspec instalar --verificar` (deriva de los adaptadores), `railspec estado` y, en el servidor, `/livez` y `/healthz` | Parcial |
| `pce-mcp` en el clon | El servidor consulta la gobernanza por su cuenta en cada gate | Ver «pce-mcp y kit-doctor» |
| Hooks `guard_*` de `.spec/scripts` | `railspec hook <arnés>` aplica las reglas de conducta con una unidad en curso | Distinto: otras reglas |
| Gate de pre-push (`.spec/scripts/pre-push-gate.sh`) | No hay equivalente | Sigue siendo del kit |

**Supervisado y desatendido.** Piden un mandato (`--plan <slug>`), pero el servidor
solo guarda ese slug: ninguna tool crea un mandato, ni existen las paradas tipificadas,
el `auto-deferred` ni las decisiones delegadas del kit. Lo único que hacen los dos modos
es no abrir `aprobar-spec`, `aprobar-plan` ni `paquete-aprobacion`; un gate escalado sigue
abriendo su checkpoint. Para trabajo con mandato, sigue usando `sdd-supervisado` y
`sdd-desatendido` del kit. Al importar una unidad de esos modos entra como `interactivo`
(ver «Traer unidades»).

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

- **El orden da igual.** Puedes instalar primero el kit y luego `railspec instalar`, o al
  revés. Reinstalar o actualizar el kit (`python3 installer/cli.py --target . --install`)
  conserva lo de Railspec, y reinstalar Railspec conserva lo del kit. El kit deja sus entradas
  al final de cada lista, así que la primera reinstalación puede reordenar `.mcp.json` y los
  hooks; las siguientes no cambian ni un byte.
- **La verificación no ve deriva por lo del otro.** `python3 installer/cli.py --target .`
  mide en esos cuatro archivos solo la parte del kit (las entradas con `_sdd_kit` y, en
  `AGENTS.md`, todo menos el bloque de Railspec), y `railspec instalar --verificar` mide solo lo
  de Railspec. Editar o borrar una entrada del kit sigue siendo deriva. Un registro de
  instalación anterior, con el digest del archivo entero, se sigue aceptando.
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
y un `.spec/protocolo-datos.yaml` (el kit no lo instala: es de cada destino); `.agents/skills/`
es una de esas rutas. **El adaptador
de Codex escribe `.agents/skills/railspec/` y `.agents/skills/railspec-bucle/`**, así que el
primer push con el adaptador de Codex en el diff se rechaza en un repositorio con el kit.
Claude Code, OpenCode y Copilot no escriben en rutas del protocolo y pasan. Para el caso de
Codex tienes dos salidas: cumplir el gate como dice su mensaje, o quitar el gancho si ya no
usas el protocolo supervisado del kit (`python3 installer/cli.py install-hook pre-push
--uninstall`). Cambiar el patrón del gate es tocar `.spec/`, que esta guía no hace.

## Traer unidades

```
railspec importar .spec/units/0007-pce-mcp                    # una unidad del kit
railspec importar .spec/units/* --solo-convertir paquetes/    # revisar antes, sin servidor
```

Necesita `railspec instalar` hecho y `RAILSPEC_URL` y `RAILSPEC_TOKEN` en el entorno
(`--solo-convertir` no contacta el servidor). Solo lee `.spec/units/<id>/`: la unidad del kit
queda donde estaba. Se trae por unidad y cuando tú lo pides; ver
[proxy-local.md](proxy-local.md#importar-y-exportar-unidades) para el formato del paquete.

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

El kit no trae desinstalador. Este procedimiento quita lo que él instaló y deja a Railspec como
estaba (`railspec instalar --verificar` sigue limpio); `installer/tests/test_convivencia_railspec.py`
lo ejecuta tal cual está aquí. No lo corras en el repositorio `sdd-mcp`, que es la fuente del kit
y no tiene registro de instalación.

Antes, importa o archiva lo que quieras conservar de `.spec/units/`: no es parte de la carga del kit
y el procedimiento no lo toca, pero tampoco lo mueve. Luego, desde la raíz del repositorio:

<!-- prueba:desinstalar-kit -->
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
borrarlos a mano. Después de quitar el kit, `python3 installer/cli.py` ya no existe en el repositorio:
si vuelves a instalarlo, corres el instalador desde su propio clon con `--target`.

## pce-mcp y kit-doctor

**`pce-mcp`.** Es el servidor MCP local del kit (`scripts/mcp-pce.sh`, entrada `pce-mcp` en
`.mcp.json` y `opencode.jsonc`) con el que el arnés consulta la gobernanza. Railspec no lo
necesita en el clon: el servidor consulta la gobernanza por su cuenta en cada gate, con
`RAILSPEC_PCE_URL` y `RAILSPEC_PCE_API_KEY` o con la herramienta de contexto de la organización
([proveedores.md](proveedores.md#herramientas-de-contexto)); sin ninguna, todo gate escala con
`sin-gobernanza`. Que `pce-mcp` esté caído en el clon no afecta al bucle de Railspec, pero sí a las skills del
kit que lo consultan. Las dos entradas conviven en
`.mcp.json`.

**`kit-doctor`.** Diagnostica `pce-mcp`, `gitnexus`, `codebase-memory-mcp` y el índice de grafo
local del kit, y sigue sirviendo mientras haya kit. Railspec no usa `gitnexus`: su grafo es el
del servidor (`graph_query`). Sí usa `codebase-memory-mcp` como indexador local opcional
([proxy-local.md](proxy-local.md#indexador-local)), por su cuenta. Para Railspec no hay `railspec
doctor`: revisa los adaptadores con `railspec instalar --verificar`, el estado de una unidad con
`railspec estado`, y el servidor con `/livez` y `/healthz`.

## Lo que queda abierto

- No hay desinstalador del kit ni migración masiva de unidades.
- El gate de pre-push del kit rechaza el primer push con el adaptador de Codex (ver arriba).
- Supervisado, desatendido, mandato y paradas tipificadas no están en Railspec.
- La importación necesita un servidor desplegado; sin él solo sirve `--solo-convertir`.
