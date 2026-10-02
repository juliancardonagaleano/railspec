# Proxy local (`railspec-local`)

El proxy es lo único de Railspec que corre en la máquina del desarrollador.
El arnés (Claude Code, OpenCode, Codex o GitHub Copilot CLI) lo lanza como servidor MCP por stdio; el
proxy habla con `railspec-server` por MCP Streamable HTTP usando solo los
contratos de `railspec-contracts`. El servidor decide fase, gate y
presupuesto; el arnés escribe el código; el proxy hace en local lo que el
protocolo exige en local.

## Instalación en un repositorio

```
pip install railspec-local            # instala el comando `railspec` (o el binario: ver "Binario autocontenido")
pip install codebase-memory-mcp       # opcional: indexado local (delta de símbolos y aristas)
railspec instalar --org acme --workspace certificados --repositorio certificados-api \
  --arnes claude-code --arnes opencode   # también: --arnes codex, --arnes copilot
export RAILSPEC_URL=https://railspec.example/mcp   # endpoint MCP del servidor
railspec login --client-id Iv23li...               # inicia sesión con GitHub (una vez): ver "Iniciar sesión con GitHub"
```

`railspec instalar` escribe `.railspec/config.json` (versionable, sin
secretos: org, workspace, slug del repositorio, nivel de código, arnés) y el
adaptador de cada arnés. `--nivel` fija el nivel del vínculo; si falta rige
`restringido`. `railspec instalar --verificar` informa deriva sin escribir.
El token nunca va al repositorio: lo guarda `railspec login` fuera de él, o llega
por `RAILSPEC_TOKEN`. No sirve un token `rsc1` de la consola (vale
solo en `/v1` y el chat, no en `/mcp`). El servidor identifica a la persona con
él y, si la GitHub App tiene el permiso de miembros de la organización, lee sus
equipos de GitHub (cinco minutos de caché): un rol asignado a un equipo vale en
el arnés igual que en la consola ([consola.md](consola.md#autorización)).
Cuando el servidor no acepta el token, el proxy lo dice con el motivo del
servidor y qué hacer según de dónde salió (`railspec login` si no hay sesión o
venció; `RAILSPEC_TOKEN` si lo exportas); cuando se niega una tool por rol, el
mensaje trae el rol que pide, el que tienes y, si no hay equipos resueltos, que
solo cuentan los roles asignados a tu persona. El comando `railspec` tiene que estar en
el `PATH` que ve el arnés (el binario de la release en `~/.local/bin` o
`pipx install railspec-local`); si no lo está, `instalar` lo avisa. El arnés
lo usa para el proxy (`railspec mcp`) y para los hooks (`railspec hook`).

```
railspec desinstalar                  # todos los adaptadores presentes
railspec desinstalar --arnes opencode # solo uno
railspec desinstalar --config         # además, .railspec/config.json
```

`desinstalar` quita exactamente lo que puso `instalar` y deja el resto de
cada archivo como estaba; si un archivo queda vacío (o, en OpenCode, solo con
`$schema`), lo borra, y también las carpetas que queden vacías. Nunca toca
las unidades: sus worktrees y su estado local siguen donde estaban y la salida
los lista en `unidades_en_local`.

Por defecto el adaptador se instala en el repositorio. Para dejarlo en la
configuración de tu usuario (todos los repositorios, sin tocar cada clon) añade
`--alcance usuario`: ver [Instalación por usuario](#instalación-por-usuario).
Y si algo no funciona, `railspec doctor` lo diagnostica sin cambiar nada: ver
[Diagnóstico](#diagnóstico-railspec-doctor).

## Iniciar sesión con GitHub

El servidor identifica a cada persona con un **token de usuario de la GitHub App
de Railspec**: no sirve un token personal (PAT), uno de otra OAuth app ni el
`rsc1` de la consola. `railspec login` lo consigue con el [device flow de
GitHub](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/generating-a-user-access-token-for-a-github-app#using-the-device-flow-to-generate-a-user-access-token):
no hay nada que pegar a mano y el client secret de la App no sale del servidor.

```
export RAILSPEC_URL=https://railspec.example/mcp
export RAILSPEC_GITHUB_CLIENT_ID=Iv23li...   # no es secreto; lo publica quien administra el servidor
railspec login
#   Abre https://github.com/login/device e introduce el código:
#       ABCD-1234
#   Esperando la autorización (el código vence en 15 min; Ctrl+C cancela)…
railspec whoami             # quién eres para el proxy y de dónde sale el token
railspec whoami --comprobar # además, pregunta a GitHub si el token sigue valiendo
railspec logout             # borra la sesión de este equipo
```

- **Por servidor.** La sesión es la del servidor de `RAILSPEC_URL` (la URL se
  compara sin barra final y sin distinguir mayúsculas en el host): un mismo equipo puede tener sesión en
  varios. Los tres comandos funcionan desde cualquier carpeta, no hace falta un
  repositorio.
- **Client id.** `--client-id`, o `RAILSPEC_GITHUB_CLIENT_ID`, o el de la sesión
  guardada: tras el primer `login` basta `railspec login`.
- **Dónde se guarda.** `~/.config/railspec/credenciales.json` (`$XDG_CONFIG_HOME`
  si está definida; `RAILSPEC_CREDENCIALES` fija otra ruta, absoluta), fuera de cualquier
  repositorio. Carpeta 0700, archivo 0600 creado así desde el primer byte y
  reemplazado de forma atómica. Una entrada por servidor con el token, el client
  id, el login y el id de GitHub, y cuándo vence. Si el archivo está más abierto
  que 0600, `railspec` lo cierra antes de usarlo (y si no puede, se niega); si
  está dañado, lo dice sin repetir su contenido y `railspec login` lo reescribe.
  No se guarda el refresh token. Solo POSIX: el proxy no corre en Windows.
- **Quién gana.** `RAILSPEC_TOKEN`, si está exportada, manda sobre la sesión
  guardada (como `GH_TOKEN` en `gh`); `login` avisa cuando lo está y `whoami`
  dice cuál se usa y qué sesión ignora.
- **Sin reiniciar el arnés.** El proxy resuelve el token en cada petición, no al
  arrancar: un `railspec login` o `logout` con el arnés abierto vale en la
  llamada siguiente. Lo que sí tiene que llegar al proceso del proxy es
  `RAILSPEC_URL` y un `HOME` desde el que encuentre el archivo (o
  `RAILSPEC_CREDENCIALES`); si tu arnés lo lanza con un entorno reducido,
  configúralo ahí.
- **Si el servidor rechaza el token** el mensaje trae su motivo y lo que hay que
  hacer: «No hay sesión iniciada: ejecuta `railspec login`», «La sesión de
  `railspec login` como ana venció el …», o, si el token viene de
  `RAILSPEC_TOKEN`, que tiene prioridad y qué tipo de token vale. Un token
  vencido ya no se manda al servidor.
- **`logout` solo olvida el token en este equipo.** Revocarlo en GitHub exige el
  client secret de la App; mientras no venza sigue valiendo y se quita desde
  <https://github.com/settings/apps/authorizations>.

### Configurar la GitHub App (quien administra el servidor)

En Settings → Developer settings → GitHub Apps → la App de Railspec (la misma de
[consola.md](consola.md#github-app)):

1. Marca **Enable Device Flow**. Sin eso `railspec login` falla con
   «la GitHub App de Railspec no tiene activado el device flow».
2. Publica el **Client ID** de la App junto a `RAILSPEC_URL` (no es secreto).
   Tiene que ser el de la App cuyo client id y secret están en el Secret del
   servidor (`RAILSPEC_GITHUB_APP_CLIENT_ID`): el servidor rechaza los tokens de
   cualquier otra App.
3. Decide **Expire user authorization tokens**. Activada (lo que GitHub propone
   en Apps nuevas) el token dura 8 horas y `railspec login` no puede renovarlo:
   renovarlo exige el client secret, que no debe viajar a las máquinas de los
   desarrolladores, así que hay que repetir `railspec login` cuando venza (el
   proxy lo dice con la fecha). Desactivada, el token no vence y solo se quita
   revocándolo en GitHub. La primera cuida más un token que vive en disco; la
   segunda evita repetir el inicio de sesión cada día.

## Adaptadores: el stack de tres piezas

| Pieza | Claude Code | OpenCode |
|---|---|---|
| Registro del proxy | `.mcp.json` → `mcpServers.railspec` | `opencode.json` u `opencode.jsonc` (el que exista) → `mcp.railspec` |
| Comando de arranque `/railspec` | `.claude/commands/railspec.md` | `.opencode/commands/railspec.md` |
| Bucle de cliente | `.claude/skills/railspec-bucle/SKILL.md` | `.opencode/skills/railspec-bucle/SKILL.md` |
| Reglas de conducta | bloque delimitado en `CLAUDE.md` | bloque delimitado en `AGENTS.md` |
| Reglas aplicadas (hooks) | `hooks.PreToolUse` en `.claude/settings.json` | plugin `.opencode/plugins/railspec.js` |
| Permisos | `.claude/settings.json` y `.claude/settings.local.json` | `permission` en el mismo archivo de configuración |

La fusión solo toca la entrada `railspec`, los permisos que añade y el bloque
entre marcadores; el resto de cada archivo se conserva. Un JSON inválido no
se pisa. Al reescribir un `opencode.jsonc` se pierden sus comentarios.

**Permisos.** El bucle corre sin preguntas, pero lo que registra una decisión
humana pregunta siempre:

| | Claude Code | OpenCode |
|---|---|---|
| Sin preguntar | `permissions.allow`: `mcp__railspec__<tool>` para `unit_start`, `unit_advance`, `unit_report`, `unit_checkpoint`, `unit_status`, `unit_list`, `graph_query`, `insumo_pull`, `railspec_sync` | por defecto (OpenCode permite las tools MCP) |
| Pregunta siempre | `permissions.ask` (gana a `allow`): `unit_approve`, `unit_set_mode`, `unit_integrate` | `permission.railspec_<tool>: "ask"` para esas tres |

En Claude Code, la parte por máquina va a `.claude/settings.local.json`, que
`instalar` excluye de git en `info/exclude`: `enabledMcpjsonServers:
["railspec"]` (el servidor del `.mcp.json` queda aprobado cuando la carpeta es
de confianza) y `permissions.additionalDirectories` con la carpeta de
worktrees, para que el arnés edite en el worktree de la unidad sin salir de su
espacio de trabajo. OpenCode no tiene configuración de proyecto fuera de git;
la carpeta de worktrees se abre desde el plugin (ver abajo), así que tampoco
pregunta por `external_directory`.

Las piezas se probaron contra Claude Code (`claude mcp get railspec`:
conectado) y OpenCode 1.18.33 (`opencode mcp list`, `opencode debug config`
y `opencode debug skill` reconocen servidor, comando, skill y permisos). Las
versiones anteriores del adaptador de OpenCode escribían en `.opencode/command/`
y `.opencode/skill/`; `instalar` las quita.

### Segunda ola: Codex y GitHub Copilot CLI

Ninguno de los dos tiene comandos de barra definidos en el repositorio: el
arranque es una skill `railspec` con el mismo texto que el comando
`/railspec` (sale de la misma plantilla), y el bucle, la skill
`railspec-bucle` de siempre.

| Pieza | Codex | GitHub Copilot CLI |
|---|---|---|
| Registro del proxy | `.codex/config.toml` → `[mcp_servers.railspec]`, en un bloque entre comentarios marcadores | `.mcp.json` → `mcpServers.railspec`, la misma entrada que Claude Code |
| Arranque | skill `.agents/skills/railspec/SKILL.md`; se invoca `$railspec <petición>` | skill `.github/skills/railspec/SKILL.md`; se invoca `/railspec <petición>` |
| Bucle de cliente | `.agents/skills/railspec-bucle/SKILL.md` | `.github/skills/railspec-bucle/SKILL.md` |
| Reglas de conducta | bloque delimitado en `AGENTS.md` | bloque delimitado en `AGENTS.md` |
| Reglas aplicadas (hooks) | `hooks.PreToolUse` en `.codex/hooks.json` | `preToolUse` en `.github/hooks/railspec.json` |
| Permisos | `approval_mode` por tool en el mismo bloque TOML | `allowed-tools` en las dos skills |

**Permisos.** En Codex, las tools del bucle llevan `approval_mode =
"approve"` y las tres decisiones humanas `"prompt"`;
`default_tools_approval_mode = "prompt"` cubre las tools que el proxy añada
después. Copilot pide permiso para toda tool MCP y no admite listas de
permisos versionadas en el repositorio: `allowed-tools`
(`railspec(unit_advance)`, ...) aprueba las tools del bucle durante la sesión
en que se invoca la skill, y las decisiones humanas no se listan, así que
preguntan siempre.

**Confianza y worktrees.** Codex solo lee `.codex/config.toml` en proyectos de
confianza y Copilot solo carga los servidores de `.mcp.json` y los hooks de
`.github/hooks` en carpetas de confianza; los dos lo preguntan al abrir el
repositorio la primera vez y lo guardan en la configuración del usuario, que
`instalar` no toca. Los hooks de Codex piden además su propia confianza (ver
abajo). Ninguno tiene
una configuración de proyecto fuera de git para la carpeta de worktrees: se
lanzan con `codex --add-dir <worktrees>` o `copilot --add-dir <worktrees>`
(Copilot exige que la carpeta exista), y `instalar` imprime la ruta.

**Piezas compartidas.** El bloque de `AGENTS.md` es el mismo para OpenCode,
Codex y Copilot, y la entrada de `.mcp.json` la misma para Claude Code y
Copilot. `desinstalar` de un arnés conserva lo que otro instalado sigue
usando, y un arnés solo cuenta como instalado si tiene alguna pieza propia.

Verificado con codex-cli 0.159.3 (`codex mcp get railspec --json` y `codex
doctor`: configuración cargada; `codex debug prompt-input` lista las dos
skills y el bloque de `AGENTS.md`) y GitHub Copilot CLI 1.0.90 (`copilot mcp
list --json`, `copilot skill list --json`, `copilot instruction list --json`),
en una carpeta de confianza y sin sesión iniciada. Sin credenciales en el
entorno de pruebas no se pudo correr un turno de modelo en ninguno: queda sin
verificar que el modelo invoque las tools y que Copilot aplique
`allowed-tools` al invocar la skill (lo valida en ese momento). Los turnos con
un modelo de pega que sí corrieron, para la guardia de hooks, están en
«Reglas de conducta aplicadas con hooks».

## Instalación por usuario

`railspec instalar --alcance usuario --arnes <arnés>` deja el stack de tres
piezas, el servidor MCP, los permisos y la guardia en la configuración que cada
arnés lee para **cualquier** repositorio: se instala una vez por máquina y no hay
que tocar cada clon. Sirve para Claude Code y OpenCode (Codex y Copilot,
abajo).

```
railspec instalar --alcance usuario --arnes claude-code --arnes opencode
railspec instalar --alcance usuario --arnes claude-code --verificar   # deriva, sin escribir; sale con 1 si la hay
railspec desinstalar --alcance usuario --arnes opencode
```

| Pieza | Claude Code (`~` = tu carpeta personal) | OpenCode (`$XDG_CONFIG_HOME/opencode`, por defecto `~/.config/opencode`) |
|---|---|---|
| Registro del proxy | `~/.claude.json` → `mcpServers.railspec` (alcance *user*, la entrada de `claude mcp add --scope user`) | `opencode.json` u `opencode.jsonc` (el que exista) → `mcp.railspec` |
| Comando de arranque `/railspec` | `~/.claude/commands/railspec.md` | `commands/railspec.md` |
| Bucle de cliente | `~/.claude/skills/railspec-bucle/SKILL.md` | `skills/railspec-bucle/SKILL.md` |
| Reglas de conducta | bloque delimitado en `~/.claude/CLAUDE.md` | bloque delimitado en `AGENTS.md` |
| Reglas aplicadas (hooks) | `hooks.PreToolUse` en `~/.claude/settings.json` | plugin `plugins/railspec.js` |
| Permisos | `~/.claude/settings.json` (`allow` y `ask`, los de la tabla de arriba) | `permission` del archivo de configuración |

La fusión sigue las mismas reglas que por repositorio (solo la entrada
`railspec`, los permisos que añade y el bloque entre marcadores; un JSON
inválido no se pisa; los archivos se reescriben de forma atómica, conservando
sus permisos y respetando un enlace simbólico). Las rutas se resuelven con el
`HOME` del proceso, así que un `HOME` temporal sirve para probar sin tocar el
tuyo.

- **Lo del repositorio sigue siendo del repositorio.** El alcance de usuario no
  escribe `.railspec/config.json` (org, workspace, slug y nivel son de cada
  repositorio): ejecuta en cada clon `railspec instalar --org … --workspace …
  --repositorio …` **sin** `--arnes` ni `--alcance`, que solo escribe esa
  configuración. Con `--alcance usuario`, `--org`, `--workspace`,
  `--repositorio` y `--nivel` se rechazan, y `desinstalar --alcance usuario`
  rechaza `--config`.
- **Las reglas de conducta de usuario** son las mismas con un encabezado que
  dice que valen en los repositorios con `.railspec/config.json`: en cualquier
  otro no piden nada.
- **El servidor arranca en cualquier carpeta.** `railspec mcp` busca el
  repositorio y su configuración al llamar una tool, no al arrancar: fuera de un
  repositorio con Railspec el arnés ve las tools igual y la primera llamada
  responde que falta `railspec instalar --org …` (o `--repo <ruta>`). Un
  arranque que fallara a la entrada dejaría el servidor caído en cada carpeta
  del usuario.
- **Carpeta de worktrees.** La unidad trabaja en `<repo>.railspec/<unidad>`, que
  queda fuera del repositorio. Por repositorio, `instalar` la abre en
  `settings.local.json`; en alcance de usuario no puede, porque cambia con cada
  repositorio, así que Claude Code pregunta la primera vez que edita en la
  carpeta de worktrees de cada uno. Para abrirla de una vez, fija
  `RAILSPEC_WORKTREES` a una carpeta común y repite el comando: va a
  `permissions.additionalDirectories` de `~/.claude/settings.json` (y exporta la
  misma variable en el entorno del arnés, para que el proxy cree ahí los
  worktrees). Es una concesión: Claude Code edita sin preguntar en esa carpeta
  para todos tus repositorios. En OpenCode no hace falta: el plugin abre la
  carpeta al arrancar, como por repositorio.
- **Los dos alcances a la vez** funcionan: en Claude Code el servidor aparece una
  vez y el hook de la guardia corre una vez. `railspec doctor` revisa ambos.
- **El comando en el `PATH`.** `instalar` avisa si `railspec` no está en el
  `PATH` que ve el arnés; tras instalar, reinicia el arnés, exporta
  `RAILSPEC_URL` y ejecuta `railspec login`.

**Codex y GitHub Copilot CLI: no soportados.** `--alcance usuario --arnes codex`
(o `copilot`) termina con un error que lo dice y manda a instalarlos por
repositorio. No se verificó dónde cargan en la configuración del usuario los
hooks, las skills y el servidor MCP de cada uno, y una ruta supuesta dejaría la
guardia sin aplicar **sin ningún error** (en Codex ya ocurre con un hook sin
confiar). Hasta verificarlo contra cada CLI, siguen siendo por repositorio.

**Verificado de verdad** (2026-10-02, con un `HOME` temporal y el comando
`railspec` real en el `PATH`):

- Claude Code 2.1.287: desde una carpeta que no es un repositorio, `claude mcp
  get railspec` informa «User config (available in all your projects)» y
  «Connected»; el evento `init` de `claude -p` lista el comando `railspec`, la
  skill `railspec-bucle`, el servidor con origen `user` y las doce tools
  `mcp__railspec__*`; con un `railspec` de pega en el `PATH` que responde
  `deny`, un `Write` del modelo se rechaza con el motivo (el hook corre una sola
  vez, también con los dos alcances instalados). La entrada que escribe
  `instalar` en `~/.claude.json` es la que produce `claude mcp add --scope user`.
- OpenCode 1.18.34: `opencode mcp list` (servidor `railspec` conectado desde una
  carpeta sin repositorio), `opencode debug config` (el servidor, los tres
  `permission.railspec_<tool>: "ask"` y el plugin global) y `opencode debug
  skill`; con un proveedor OpenAI-compatible de pega, la petición al modelo
  lleva las doce tools `railspec_*` y el bloque de reglas del `AGENTS.md` global.

**Sin verificar.** En OpenCode, que el plugin global rechace una escritura con
una unidad en curso (por repositorio sí se probó, es el mismo archivo); ningún
turno de modelo real con alcance de usuario; los dos alcances a la vez en
OpenCode; Windows y macOS.

## Diagnóstico: `railspec doctor`

```
railspec doctor           # informe para leer
railspec doctor --json    # lo mismo, para otra herramienta
railspec --repo ~/src/certificados-api doctor   # desde otra carpeta
```

Responde a «¿por qué no funciona?» en una pasada y **sin cambiar nada**: no
escribe en el repositorio, en los worktrees ni en la configuración de ningún
arnés, no corrige permisos (si `credenciales.json` está abierto lo dice, no lo
cierra) y no cambia nada en el servidor: su única llamada es `unit.list`, de
lectura. Cada comprobación termina en ✓ (bien), `!` (algo que mirar, no impide
trabajar) o ✗ (roto, con el remedio debajo). **Sale con 1 si hay algún ✗** y con
0 si solo hay avisos; el resumen final cuenta fallos, avisos y bien. En
`--json`: `ok` (no hay ningún fallo), `resumen` y `comprobaciones` (cada una con
`nombre`, `estado`, `detalle` y, si los hay, `remedio` e `items`).

| Comprobación | Qué mira | Falla si… | Avisa si… |
|---|---|---|---|
| `repositorio` | Hay un clon y `.railspec/config.json` válido (org, workspace, repositorio, nivel) | no es un repositorio git o falta o es inválida la configuración | — |
| `comando` | `railspec` en el `PATH` | no está: el arnés no puede lanzar el proxy ni la guardia | — |
| `servidor` | `RAILSPEC_URL` responde (lista las tools por MCP; tope de 20 s) | falta `RAILSPEC_URL`, no responde, da error o agota el tiempo | — |
| `sesion` | De dónde sale el token (`RAILSPEC_TOKEN` o `railspec login`) y que el servidor lo acepta (`unit.list`) | no hay sesión, venció, el archivo está dañado o abierto a otros usuarios, el servidor rechaza el token o el rol | no hay `RAILSPEC_URL`, no se pudo probar el token (sin conexión, o sin configuración del repositorio) o vence en menos de una hora |
| `contrato` | El servidor publica las tools de este proxy (contrato 1.5) y su respuesta cumple el contrato. Solo si el servidor respondió | faltan tools (contrato anterior) o la respuesta de `unit.list` no valida | sobran tools (¿contrato más nuevo?) o el transporte no las lista |
| `adaptadores` | Deriva de cada adaptador instalado, por repositorio y por usuario, respecto de esta versión | alguna pieza falta o no coincide (cada una, listada) | ningún arnés tiene el adaptador |
| `codex` | Solo si el repositorio instaló Codex: hay confianza guardada para el hook de la guardia | el hook no está confiado (Codex no lo ejecuta y las ediciones pasan sin revisar) | no se pudo leer `~/.codex/config.toml` |
| `indexador` | `codebase-memory-mcp` en el `PATH` y en la versión fijada | tiene otra versión (el delta no se garantiza) | no está instalado (los snapshots viajan solo con hashes; es opcional) |
| `worktrees` | Los worktrees de unidades de este repositorio | — | huérfanos (git los registra y la carpeta no existe, o no tienen estado local, o hay una carpeta con estado que git no conoce) o un estado local que no se puede leer |

- **El contrato no se negocia en `doctor`.** La versión se fija al arrancar una
  unidad (`unit_start`), no al conectar; `doctor` comprueba lo observable: que el
  servidor publica las tools que este proxy llama y que `unit.list` responde con
  la forma del contrato. Lo dice en el detalle.
- **Codex: la confianza no se puede validar del todo.** Codex guarda en
  `~/.codex/config.toml` (o `$CODEX_HOME`) un `trusted_hash` por hook, ligado a su
  contenido. `doctor` comprueba que hay una entrada para el hook de este
  repositorio; no recalcula el hash, así que no detecta que `railspec instalar`
  cambió el hook después de confiarlo (Codex lo pide de nuevo en `/hooks`). Lo
  advierte en el detalle del ✓.
- **Sin repositorio.** Fuera de un clon, `repositorio` falla, `codex` y
  `worktrees` no aparecen, `sesion` avisa de que no probó el token contra el
  servidor (la prueba usa el workspace del repositorio) y `adaptadores` mira solo
  los de usuario; `comando`, `servidor` y el resto responden igual.

## Importar y exportar unidades

```
railspec importar .spec/units/0007-pce-mcp                    # una unidad del kit SDD embebido
railspec importar .spec/units/* --solo-convertir paquetes/    # revisar antes, sin servidor
railspec importar paquetes/0007-pce-mcp                       # un paquete en disco
railspec exportar --unidad 0012-cola --destino 0012-cola.railspec-unidad
```

El formato es el paquete `railspec.unidad/v1` del contrato 1.4: una carpeta
con `unidad.json` (el paquete sin el texto de los artefactos, con el sha256 de
cada uno) y `spec.md`, `plan.md`, `tasks.md`. Leer un paquete verifica los
hashes, así que uno editado a medias no entra. `borradores/` guarda, fuera
del paquete, el artefacto que el origen estaba redactando.

No hay retrocompatibilidad automática con el kit SDD: cada unidad se importa
cuando el humano lo pide. La conversión lee `_estado.yaml`, `spec.md`,
`plan.md` y `tasks.md` (la bitácora y el resto no viajan):

- Los artefactos que la fase de origen da por cerrados quedan aprobados por
  importación, siempre como prefijo spec, plan, tasks; si falta uno, la
  unidad retoma en su fase. El de la fase en curso es un borrador.
- El pedido es la sección «Problema» del spec (o el título).
- `supervisado` y `desatendido` exigen mandato: la unidad entra interactiva
  y el modo lo fija el humano. Riesgo, perfil, `governance_refs`, comando de
  validación, dependencias e historial de gates viajan; los dos últimos solo
  como información.

`importar` revisa secretos en artefactos y pedido antes de salir del clon,
llama `unit.import` (idempotente por origen: la segunda vez devuelve la
unidad existente) y abre la unidad en local como `unit_start`: rama, worktree
y los artefactos en `.railspec/unidades/<unidad>/`, la ruta de las órdenes de
redactar. `exportar` pide el paquete con `unit.export`.

## Reglas de conducta aplicadas con hooks

Las reglas de conducta están escritas para el agente, pero donde el arnés
tiene un hook previo a cada tool, Railspec además las impone. Los cuatro
arneses llaman a la misma guardia, `railspec hook <arnés>` (`guardia.py`),
que lee la llamada por stdin y responde por stdout, cada uno en su formato.
Con una unidad en curso en el repositorio:

| Intento | Respuesta |
|---|---|
| Escribir en el clon principal | Rechazada: se trabaja en el worktree de la unidad, que el mensaje nombra |
| Escribir código en el worktree | Solo dentro de `alcance.permitidos` y fuera de `prohibidos` de la orden vigente |
| Escribir `spec.md`, `plan.md` o `tasks.md` de la unidad | Solo con la orden de redactar o refinar que pide ese artefacto |
| Escribir en `.railspec/` del worktree (estado del proxy) | Rechazada siempre |
| Escribir sin orden vigente | Rechazada: primero `unit_advance` |
| Escribir en el worktree de una unidad cerrada | Rechazada |
| `unit_approve`, `unit_set_mode`, `unit_integrate` | Pide confirmación al humano (Claude Code, Copilot); en Codex, ver abajo |
| Cualquier otra cosa, o sin unidades en curso | La guardia no opina; decide el arnés |

Una unidad está en curso si su worktree tiene estado local y su fase no es
`done`. Lo que cae fuera del repositorio y de la carpeta de worktrees queda a
los permisos del arnés. Si la guardia falla (estado ilegible, error
inesperado), rechaza la escritura y lo dice. La salida de emergencia es lanzar
el arnés con `RAILSPEC_GUARDIA=0`: lo decide el humano, y las reglas de
conducta piden al agente no esquivar un rechazo (tampoco con la shell).

**Claude Code.** `instalar` añade a `hooks.PreToolUse` de
`.claude/settings.json` una entrada con `matcher`
`Write|Edit|MultiEdit|NotebookEdit|mcp__railspec__unit_approve|mcp__railspec__unit_set_mode|mcp__railspec__unit_integrate`
y el comando `railspec hook claude-code`. La entrada propia se reconoce por el
comando: reinstalar la reemplaza y `desinstalar` la quita sin tocar los hooks
ajenos. El hook responde `permissionDecision: deny` con el motivo, que el
modelo recibe, o `ask` para las tools humanas; el `ask` del hook se aplica
aunque alguien haya puesto la tool en `allow` o la sesión corra con
`--permission-mode bypassPermissions`.

**OpenCode.** `instalar` escribe `.opencode/plugins/railspec.js`, que OpenCode
carga al arrancar:

- `tool.execute.before` envía a la guardia las llamadas a `edit`, `write`,
  `multiedit`, `patch` y `apply_patch` (de un parche se revisan todas las
  rutas) y lanza un error con el motivo si las rechaza.
- `config` añade `permission.external_directory["<carpeta de worktrees>/**"] =
  "allow"`. La ruta es de cada máquina y no puede versionarse en
  `opencode.json`; el plugin la calcula al arrancar (respeta
  `RAILSPEC_WORKTREES`). Así OpenCode ya no pregunta la primera vez que entra
  en la carpeta. No se usa el hook `permission.ask`: en OpenCode 1.18.34 no se
  invoca.

**Codex.** `instalar` añade a `hooks.PreToolUse` de `.codex/hooks.json` (mismo
formato de archivo que los hooks de Claude Code) una entrada con `matcher`
`apply_patch|mcp__railspec__unit_approve|mcp__railspec__unit_set_mode|mcp__railspec__unit_integrate`
y el comando `railspec hook codex`. `apply_patch` es la única tool de edición
de Codex (de un parche se revisan todas las rutas). La entrada propia se
reconoce por el comando, como en Claude Code. Tres cosas lo distinguen:

- **El hook tiene que estar confiado.** Codex no ejecuta un hook de proyecto
  hasta que el humano lo revisa (`/hooks` en el TUI avisa de que hay hooks por
  revisar). La confianza queda por máquina en `~/.codex/config.toml`
  (`[hooks.state."<repo>/.codex/hooks.json:pre_tool_use:0:0"]`,
  `trusted_hash`) y va ligada al contenido del hook: si `instalar` lo cambia,
  Codex pide revisarlo otra vez. **Sin esa confianza la guardia no corre y no
  hay ningún error: la tool pasa.** `instalar` lo avisa; `--verificar` no puede
  saberlo. (`codex --dangerously-bypass-hook-trust` la salta en una invocación.)
- **Solo `deny`.** Un `ask` del hook cuenta como error del hook y la tool corre,
  así que nunca se envía. La confirmación de las tres tools humanas sigue
  siendo `approval_mode = "prompt"` del bloque TOML: con aprobaciones activas
  Codex muestra «Allow the railspec MCP server to run tool …?» y el hook no
  opina. Sin aprobaciones (`approval_policy = "never"`, que Codex informa a los
  hooks como `permission_mode: bypassPermissions`) nadie puede confirmar: con
  `-s workspace-write` Codex ya rechaza la tool, pero con
  `--dangerously-bypass-approvals-and-sandbox` `approval_mode` no se respeta y
  la tool corre. Ahí el hook rechaza las tres, con el motivo; `RAILSPEC_GUARDIA=0`
  lo abre.
- **No falla cerrado.** Un hook que no llega a ejecutarse (`railspec` fuera del
  `PATH`, 30 s agotados) deja pasar la tool. La guardia en sí sí falla cerrada:
  ante un error interno responde `deny`.

**GitHub Copilot CLI.** `instalar` escribe `.github/hooks/railspec.json`:
Copilot carga todos los `*.json` de esa carpeta, así que es un archivo solo de
Railspec y los hooks ajenos no se tocan. Contiene un `preToolUse` con
`"bash"` y `"powershell"` = `railspec hook copilot`, `timeoutSec` 30 y `matcher`
`create|edit|apply_patch|railspec-unit_approve|railspec-unit_set_mode|railspec-unit_integrate`:
Copilot edita con `create` y `edit` o, con los modelos GPT-5 de código, con
`apply_patch` (el parche llega como texto), y nombra las tools MCP
`<servidor>-<tool>`. El hook responde `permissionDecision` `deny` o `ask` con el
motivo. El `ask` se aplica aunque la sesión corra con `--allow-all-tools`: en
modo interactivo Copilot muestra «Hook permission request» con Sí/No; en `-p`,
sin nadie a quien preguntar, lo rechaza («unable to ask user for
confirmation»). Si el hook falla Copilot rechaza la tool; si agota el tiempo,
la deja pasar. Solo corre en carpetas de confianza (también en `-p`); en una
carpeta sin confianza no hay guardia.

**Lo que no se aplica.** Los comandos de shell (`Bash` en Claude Code y Codex,
`bash` en OpenCode y Copilot) no se revisan: no hay forma fiable de saber qué escribe un
comando. Ahí siguen rigiendo solo las reglas escritas, y el proxy rechaza al
reportar los archivos fuera de alcance (`unit_report` con un cambio fuera de
`alcance.permitidos` falla). En OpenCode, un `tool.execute.before` no puede
pedir confirmación; las tres tools humanas preguntan por
`permission.railspec_<tool>: "ask"`. En Codex, tampoco: ver arriba.

**Verificado de verdad** (2026-10-01) con una unidad en curso creada por el
proxy contra el servidor doble de las pruebas:

- Claude Code 2.1.286 (`claude -p`, `--permission-mode bypassPermissions`):
  Edit en el clon principal y en `README.md` del worktree (fuera de
  `src/**`) rechazados con el motivo; Edit en `src/calc.py` del worktree
  aplicado; `mcp__railspec__unit_approve` pidió permiso, también con la tool
  en `allow` y sin la regla `ask`.
- OpenCode 1.18.34 (`opencode run` con un proveedor OpenAI-compatible de pega
  que dicta las tool calls): los mismos tres casos con el mismo resultado y
  sin preguntar por `external_directory`; sin el hook `config` OpenCode pidió
  ese permiso y, en `run`, lo rechazó.

**Verificado de verdad en Codex y Copilot** (2026-10-02), con el adaptador que
instala `railspec instalar`, `railspec` en el `PATH`, una unidad en curso
creada por el proxy contra el servidor doble de las pruebas (orden de
implementar con `src/**` permitido y `src/generado/**` prohibido) y un modelo
de pega que dicta las tool calls. Solo el servidor MCP `railspec` fue un
doble (no hay servidor remoto en el entorno de pruebas); el hook, el
`matcher` y los archivos son los reales.

- Codex 0.160.0 (`codex exec` y el TUI): `hooks/list` del app-server descubre
  el hook como de proyecto y `untrusted`; sin confiarlo, `apply_patch` en el
  clon principal pasa; confiado (el `trusted_hash` que informa Codex, escrito
  en la configuración del usuario), `apply_patch` rechazado en el clon
  principal, en `README.md` del worktree, en `.railspec/`, en `src/generado/` y
  en un parche mezclado (no se aplica ninguna de sus rutas), y aplicado en
  `src/calc.py`; el motivo llega al modelo. `unit_advance` pasa. Con
  `approval_policy = "never"` y con `--dangerously-bypass-approvals-and-sandbox`
  el hook rechaza `unit_approve`, `unit_set_mode` y `unit_integrate`; con
  `RAILSPEC_GUARDIA=0` la tool corre. Un hook de prueba que responde `ask`
  termina «Failed» y la tool corre (por eso no se envía). En el TUI con
  `approval_policy = "on-request"` el hook corre sin opinar y Codex muestra su
  diálogo: Cancelar («user cancelled MCP tool call») y Permitir (la tool corre).
- Copilot CLI 1.0.91 (`copilot -p` con un proveedor BYOK de pega y el TUI): en
  una carpeta de confianza `create`, `edit` y `apply_patch` rechazados en el
  clon principal, fuera del alcance, en `.railspec/` y en `src/generado/`, y
  aplicados dentro del alcance; las tools de lectura y `bash` no invocan el
  hook (el `matcher` filtra). En `-p`, `unit_approve`, `unit_set_mode` y
  `unit_integrate` se rechazan con «unable to ask user for confirmation»; en el
  TUI con `--allow-all-tools` aparece «Hook permission request» con el motivo:
  Sí ejecuta la tool y No no. Con `RAILSPEC_GUARDIA=0` el `create` en el clon
  principal pasa. En una carpeta sin confianza la guardia no corre.

**Sin verificar.** En Codex, el flujo de confianza por `/hooks`: la confianza se
simuló escribiendo el hash que informa el app-server; la existencia de `/hooks`
y del aviso de hooks por revisar salen del binario. Los nombres de las tools de
edición de Copilot (`create`, `edit`, `apply_patch`) salen de las
herramientas que Copilot declara para varios identificadores de modelo
(Claude, GPT-4.1, GPT-5, Gemini), no de turnos con esos modelos, y tampoco se
probó que cada modelo elija esas tools. `toolArgs` como texto JSON (versiones
anteriores de Copilot) solo lo cubre una prueba unitaria. Nada en Windows ni
macOS (la entrada `powershell` de Copilot no se ejecutó). Las subagentes de
cada arnés no se probaron.

## Tools que ve el arnés

Las que envuelven una tool del contrato usan su alias `nombre_mcp` (contrato
1.3: el punto pasa a guion bajo, porque varios arneses no lo admiten); el
proxy también llama al servidor por ese alias. `unit_checkpoint`,
`insumo_pull` y `railspec_sync` solo existen en el proxy. El actor nunca
viaja: el servidor lo deriva del token.

| Tool local | Tool del contrato | Qué añade el proxy |
|---|---|---|
| `unit_start` | `unit.start` | Base en HEAD del clon, rama `railspec/<unidad>`, worktree hermano |
| `unit_advance` | `unit.advance` | Vacía la cola antes; sin red devuelve la orden en curso; si la orden parte de otro commit base, rebasa el worktree (ver [Rebase](#cuando-la-base-de-la-unidad-avanza-rebase)) |
| `unit_report` | `unit.report` | Snapshot, validación y artefacto construidos en local |
| `unit_checkpoint` | `unit.approve` | Formulario al humano (elicitation) |
| `unit_approve` | `unit.approve` | — |
| `unit_set_mode` | `unit.set_mode` | Solo a petición del humano (1.2) |
| `unit_integrate` | `unit.integrate` | Manda `commit_integrado` (1.4): el del arnés o, si no, la punta de la rama por defecto del remoto tras un `git fetch`; sin remoto ni red no lo manda y el servidor descarta la superposición |
| `unit_status`, `unit_list` | homónimas | Espejo local actualizado |
| `graph_query` | `graph.query` | Vector de la consulta calculado en local (1.1); si no hay con qué calcularlo, la respuesta trae `avisos` (ver [Indexador local](#indexador-local)) |
| `insumo_pull` | `insumo.get` | Markdown en `.railspec/insumos/` |
| `railspec_sync` | `unit.report`, `sync.push`, `sync.pull` | Vacía la cola y trae eventos remotos |

## Worktree por unidad

`unit_start` crea la rama `railspec/<unidad>` desde HEAD y su worktree en
`<padre>/<repo>.railspec/<unidad>` (configurable con `RAILSPEC_WORKTREES`).
Queda fuera del clon para que pytest o los linters del repositorio no lo
recorran. El proxy encuentra las unidades con `git worktree list`, sin
registro propio. Una unidad la atiende una sola sesión del proxy por
máquina (`bloqueo` del estado local; se recupera si el proceso murió).

Estado local en el worktree, siempre fuera de git (`info/exclude`):

| Ruta | Contenido |
|---|---|
| `.railspec/estado-local.json` | `EstadoLocal`: espejo remoto, orden en curso, cola |
| `.railspec/pendientes/<orden>.json` | Reporte completo esperando conexión (`.enviado` si ya salió una vez) |
| `.railspec/ultimo-empuje` | Último commit de la rama avisado como `commit.empujado` |
| `.railspec/validacion/<orden>.log` | Salida completa del comando de validación |
| `.railspec/insumos/<id>.md` | Insumos traídos de la consola |

### Cuando la base de la unidad avanza: rebase

`unit_start` fija el `base_commit` (HEAD del clon) y el reporte lo lleva de
vuelta: el servidor rechaza un reporte cuyo `base_commit` no es el de la orden
vigente. Si la base avanzó y el servidor emite una orden con otro commit base,
`unit_advance` lleva el worktree a ese commit **antes** de devolver la orden:

```
git -C <worktree> rebase --onto <base nueva> <base anterior>
```

- **Solo con el worktree limpio.** Un rebase reescribe la rama y no debe
  llevarse trabajo sin commit. Con cambios sin commit, `unit_advance` falla sin
  tocar nada y dice qué archivos son y cómo guardarlos (`git commit`, o `git
  stash` y `git stash pop`). Los artefactos sin seguimiento de `.railspec/` no
  cuentan.
- **Un conflicto no deja nada a medias.** Si reaplicar los commits de la unidad
  choca con la base nueva, el proxy aborta el rebase, el worktree queda como
  estaba y el error lista los archivos en conflicto y el comando para rebasar a
  mano. Tras resolverlo (`git add`, `git rebase --continue`), el siguiente
  `unit_advance` ve que la rama ya parte de la base nueva (`movido: false`) y
  sigue: es repetible.
- **La base nueva se guarda después de rebasar, no antes.** El estado local
  (`base_commit` y la orden en curso, que tienen que coincidir) se escribe solo
  tras un rebase sin error; si falla, nada cambia y se reintenta.
- **Si el commit no está en el clon**, el proxy lo trae con `git fetch` del
  remoto `origin` (primero las ramas, luego el commit por su SHA) antes de
  rebasar; si ni así existe, `unit_advance` falla diciéndolo y no inventa nada. Si la
  rama ya no parte de ninguna de las dos bases, o hay un rebase a medias, o el
  worktree está en otra rama, tampoco adivina: lo explica y no toca nada.
- **El proxy no empuja.** Si la rama ya estaba en el remoto, el rebase la
  reescribe y el push normal falla: la respuesta trae un aviso con el comando,
  `git -C <worktree> push --force-with-lease` (nunca `--force`), y empujar sigue
  siendo del desarrollador.

La respuesta de `unit_advance` añade `rebase` cuando hubo orden con otra base:
`base_anterior`, `base_nueva`, `reaplicados` (commits de la unidad que quedaron
encima) y `movido` (`false` si la rama ya partía de la base nueva); y `avisos`
cuando hay que forzar el push. Sin red `unit_advance` devuelve la orden en curso
y no rebasa. El rebase no usa nada del servidor, solo el repositorio: se probó
con repositorios temporales (limpio, sucio, en conflicto, con remoto).

## Snapshot y política de código

`unit_report` construye el snapshot sobre un índice temporal (`git add -A`
+ `git write-tree`), así que incluye lo no commiteado sin tocar el índice del
desarrollador. Por orden:

1. **Exclusión.** `.env*`, claves, `node_modules/`, `build/`, etc. y lo que
   liste `.railspecignore` no viajan, ni siquiera su hash.
2. **Secretos.** Cada archivo tocado se revisa con patrones (claves
   privadas, tokens de GitHub, AWS, Azure, cadenas de conexión con
   contraseña, asignaciones `password = "..."`). Un hallazgo impide el
   snapshot y se informa por ruta, línea y tipo, nunca con el valor.
3. **Alcance.** En una orden `implementar` completada, un archivo fuera de
   `alcance.permitidos` o dentro de `prohibidos` impide el reporte.
4. **Nivel.** `restringido`: rutas, hashes y delta del índice. `interno`:
   además, fragmentos de los símbolos tocados. `abierto`: además, el diff
   (sin excluidos), si cabe en el tope del contrato.

Sin indexador local el snapshot va en `solo-hashes` en cualquier nivel.

La validación corre en el worktree; la salida completa queda en local. En
`restringido` no viaja nada de la salida (un fallo de pruebas imprime código),
solo código de salida y duración; en `interno` y `abierto`, la cola recortada
y con secretos redactados.

## Indexador local

Interfaz `Indexador` en `indice.py`, cargada por *entry point*
(`railspec.indexadores`). La implementación incluida usa `codebase-memory-mcp`
sin escribir `.codebase-memory/` en el árbol: indexa el worktree y, aparte,
los archivos tocados tal como estaban en el commit base para calcular
símbolos y aristas borrados. Los ids salen de `id_simbolo` del contrato.
Probado con la versión 0.11.0.

Cada delta abre una sola sesión MCP por stdio con el binario y la cierra al
terminar: arrancar el binario cuesta unos 6 segundos y cada consulta,
milésimas, así que un proceso por operación (el modo `cli`) multiplicaba ese
arranque por cada página. Las consultas piden el tope de filas y de salida
(`max_rows`, `max_output_tokens`); con el presupuesto por defecto el binario
devuelve unas cien filas por página. Si la sesión no arranca, se vuelve al
modo `cli`. Se usa la caché del usuario (`CBM_CACHE_DIR`): el binario tiene un
demonio por cuenta y rechaza dos cachés distintas a la vez. Sin embeddings,
como antes.

**Búsqueda semántica sin vector.** `graph_query` con `verbo: "search"` y
`semantica: true` lleva el vector que calcula el indexador local. Si no hay con
qué calcularlo (no hay indexador, o el instalado no calcula embeddings de
consulta, que es el caso de `codebase-memory-mcp`), la consulta sigue, pero el
servidor solo puede buscar por texto (salvo que tenga su propio codificador) y el
resultado puede no ser por similitud. Antes pasaba sin avisar; ahora la
respuesta lleva en `avisos` la causa y la sugerencia de buscar por nombre
(`resolve`) o con texto literal.

## Sincronización y cola sin conexión

Desde el contrato 1.3 la dirección local→remoto la numera solo el proxy:
`unit.report` ya no genera eventos en el servidor. Cada reporte se guarda y
encola como `snapshot.subido` (si hay snapshot) y `orden.reportada`, con
secuencia monótona. Si la rama de la unidad aparece empujada con un commit
nuevo (`refs/remotes/*/railspec/<unidad>`), se encola `commit.empujado`; el
webhook de la GitHub App sigue cubriendo los empujes hechos desde otra
máquina.

Al sincronizar (`unit_advance`, `unit_report`, `railspec_sync`):

1. Cada reporte pendiente viaja por `unit.report`.
   - Aceptado: sale el pendiente; sus avisos esperan al paso 2.
   - `secuencia-duplicada`: el servidor ya lo tenía; cuenta como aceptado.
   - Otro error de negocio: el remoto gana. El reporte y sus avisos salen de la
     cola, lo que queda se renumera y el rechazo se informa al arnés.
2. Los avisos suben en orden por `sync.push`; lo confirmado
   (`confirmada_hasta`) sale de la cola. Es idempotente por `id`, así que una
   respuesta perdida se reintenta sin duplicar.
3. `unit_advance` y `railspec_sync` traen los eventos remoto→local con
   `sync.pull` y avanzan `ultima_secuencia_recibida`; si llegó alguno, el
   espejo se refresca con `unit.status`.

Sin red, la cola queda intacta y `unit_advance` devuelve la orden en curso
para seguir editando; los gates esperan a la reconexión.

Si un envío de `unit.report` se quedó sin respuesta, el reenvío vuelve con
`secuencia-duplicada` cuando el servidor ya lo tenía, y cuenta como entregado.
Contra un servidor que responda `orden-no-vigente` en ese caso, el proxy no
puede saber si el primero llegó: lo informa como rechazo marcado `incierto`,
no sube sus avisos y deja que la orden siguiente lo aclare.

## Checkpoints

`unit_advance` devuelve el checkpoint; `unit_checkpoint` pregunta al humano
con un formulario del arnés (en la revisión MCP 2026-07-28 viaja como
`InputRequiredResult`) y registra su decisión sin que el modelo la vea ni
la invente. Si el arnés no declara *elicitation*, las reglas de conducta
le obligan a preguntar y llamar `unit_approve` con la decisión exacta. La
consola web puede resolverlo también; gana la primera resolución.

## Binario autocontenido

Cada versión de `railspec-local` se publica también como un ejecutable único
por plataforma que no necesita Python instalado: `railspec-linux-x86_64`,
`railspec-linux-arm64`, `railspec-macos-arm64` y `railspec-macos-x86_64`
(Windows no se soporta: el proxy usa `fcntl`). Lleva dentro el intérprete,
railspec-contracts, el SDK de MCP y las plantillas de los adaptadores; es el
mismo comando que instala `pip install railspec-local`.

### Descargar y verificar

Con la CLI de GitHub (sirve también si el repositorio es privado):

```
VERSION=0.1.0
PLATAFORMA=linux-x86_64        # linux-arm64 | macos-arm64 | macos-x86_64
REPO=juliancardonagaleano/sdd-mcp
gh release download "railspec-local-v$VERSION" --repo "$REPO" \
  --pattern "railspec-$PLATAFORMA" --pattern SHA256SUMS
```

Sin `gh`, desde la página de la release o con
`curl -fLO https://github.com/$REPO/releases/download/railspec-local-v$VERSION/railspec-$PLATAFORMA`
(y lo mismo para `SHA256SUMS`).

Antes de ejecutarlo, comprueba la suma:

```
sha256sum --check --ignore-missing SHA256SUMS      # Linux
shasum -a 256 --check --ignore-missing SHA256SUMS  # macOS
```

Debe decir `railspec-<plataforma>: OK`. Si no, no lo ejecutes.

### Ponerlo en el PATH

El arnés lanza `railspec mcp`, así que el binario tiene que llamarse
`railspec` y estar en el `PATH` que ve el arnés (no solo el de tu shell):

```
install -m 0755 "railspec-$PLATAFORMA" ~/.local/bin/railspec
railspec --version                  # railspec-local 0.1.0
```

`~/.local/bin` debe estar en el `PATH` (en macOS no viene por defecto:
añádelo en `~/.zprofile` o usa `/usr/local/bin`). `railspec instalar` avisa si
no encuentra el comando en el `PATH`.

En macOS, un archivo descargado con el navegador queda en cuarentena y
Gatekeeper lo bloquea ("no se puede verificar el desarrollador"): el binario
lleva firma ad hoc, no está notarizado. Tras verificar la suma, quita la
marca:

```
xattr -d com.apple.quarantine ~/.local/bin/railspec
```

(`gh release download` y `curl` no ponen la marca; si `xattr` responde
`No such xattr`, no hacía falta.)

El primer arranque de cada ejecución descomprime el binario en un directorio
temporal (unas décimas de segundo; `railspec --version` tarda ~0,7 s frente a
~0,35 s de la instalación con pip). Si `TMPDIR` apunta a un sistema de
archivos montado con `noexec`, el binario no arranca: apunta `TMPDIR` a otro
sitio.

El indexado local sigue siendo opcional y aparte: instala
`codebase-memory-mcp` (`pipx install codebase-memory-mcp`, o su binario) en el
mismo `PATH`; sin él, el proxy trabaja en `solo-hashes`.

Para actualizar, repite la descarga con la nueva versión y sobrescribe
`~/.local/bin/railspec`; para desinstalarlo, bórralo (antes,
`railspec desinstalar` en cada repositorio si quieres quitar los adaptadores).

### Publicar una versión

El workflow `railspec-binario` construye y prueba el binario en cada push a
`master` y en cada PR que toque `railspec-local` o `railspec-contracts`
(artefactos `railspec-<plataforma>` en la ejecución, 14 días). La release sale
de un tag:

1. Sube `version` en `railspec/packages/railspec-local/pyproject.toml` (y
   `__version__` en `railspec/local/__init__.py`) y fusiona en `master`.
2. Etiqueta ese commit y empuja el tag:

   ```
   git tag railspec-local-v0.2.0
   git push origin railspec-local-v0.2.0
   ```

3. El workflow construye las cuatro plataformas, pasa el humo en cada una,
   comprueba que el tag coincide con la versión del `pyproject.toml` y que
   `railspec --version` la imprime, y crea la release
   `railspec-local-v0.2.0` con los cuatro binarios y `SHA256SUMS`.

Si un job falla, no hay release: corrige, borra el tag
(`git push origin :railspec-local-v0.2.0`) y vuelve a etiquetar.

Las dependencias de terceros del binario están fijadas con hashes en
`empaquetado/requirements.lock`; tras cambiar las dependencias de los
`pyproject` o subir PyInstaller, regenera el lock con
`railspec/packages/railspec-local/empaquetado/bloquear.sh` y súbelo en el
mismo PR. Construir en local: `empaquetado/README.md`.

## Pendiente

- **Renovar el token de la sesión.** Con la expiración de tokens de la GitHub App
  activada, `railspec login` hay que repetirlo cada 8 horas. Renovarlo sin que
  el proxy conozca el client secret pide un endpoint del servidor que lo
  refresque con él (hoy el servidor solo verifica tokens); no se hizo.
- **Instalación por usuario de Codex y Copilot.** Claude Code y OpenCode se
  instalan por usuario; para Codex y Copilot falta verificar contra cada CLI
  dónde cargan hooks, skills y servidor MCP en la configuración del usuario (ver
  [Instalación por usuario](#instalación-por-usuario)).
- **Binario firmado.** El binario de macOS no está notarizado (hay que quitar
  la cuarentena) y no hay binario para Windows (el proxy usa `fcntl`).

- **Embeddings.** `codebase-memory-mcp` no expone sus vectores por CLI y el
  servidor nunca calcula embeddings de código: el delta viaja sin ellos y la
  búsqueda semántica va sin vector (con el aviso de arriba) hasta integrar un
  codificador local.
