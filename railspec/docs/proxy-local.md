# Proxy local (`railspec-local`)

El proxy es lo único de Railspec que corre en la máquina del desarrollador.
El arnés (Claude Code, OpenCode, Codex o GitHub Copilot CLI) lo lanza como servidor MCP por stdio; el
proxy habla con `railspec-server` por MCP Streamable HTTP usando solo los
contratos de `railspec-contracts`. El servidor decide fase, gate y
presupuesto; el arnés escribe el código; el proxy hace en local lo que el
protocolo exige en local.

## Instalación en un repositorio

```
pip install railspec-local            # instala el comando `railspec`
pip install codebase-memory-mcp       # opcional: indexado local (delta de símbolos y aristas)
railspec instalar --org acme --workspace certificados --repositorio certificados-api \
  --arnes claude-code --arnes opencode   # también: --arnes codex, --arnes copilot
export RAILSPEC_URL=https://railspec.example/mcp   # endpoint MCP del servidor
export RAILSPEC_TOKEN=...                          # token OAuth de GitHub del desarrollador
```

`railspec instalar` escribe `.railspec/config.json` (versionable, sin
secretos: org, workspace, slug del repositorio, nivel de código, arnés) y el
adaptador de cada arnés. `--nivel` fija el nivel del vínculo; si falta rige
`restringido`. `railspec instalar --verificar` informa deriva sin escribir.
El token nunca se escribe en disco. El comando `railspec` tiene que estar en
el `PATH` que ve el arnés (`pipx install railspec-local` sirve); si no lo
está, `instalar` lo avisa.

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

## Adaptadores: el stack de tres piezas

| Pieza | Claude Code | OpenCode |
|---|---|---|
| Registro del proxy | `.mcp.json` → `mcpServers.railspec` | `opencode.json` u `opencode.jsonc` (el que exista) → `mcp.railspec` |
| Comando de arranque `/railspec` | `.claude/commands/railspec.md` | `.opencode/commands/railspec.md` |
| Bucle de cliente | `.claude/skills/railspec-bucle/SKILL.md` | `.opencode/skills/railspec-bucle/SKILL.md` |
| Reglas de conducta | bloque delimitado en `CLAUDE.md` | bloque delimitado en `AGENTS.md` |
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
espacio de trabajo. OpenCode no tiene configuración de proyecto fuera de git:
pide permiso (`external_directory`) la primera vez que toca esa carpeta, y
`instalar` avisa de que hay que contestarle que siempre.

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
confianza y Copilot solo carga los servidores de `.mcp.json` en carpetas de
confianza; los dos lo preguntan al abrir el repositorio la primera vez y lo
guardan en la configuración del usuario, que `instalar` no toca. Ninguno tiene
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
`allowed-tools` al invocar la skill (lo valida en ese momento).

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

## Tools que ve el arnés

Las que envuelven una tool del contrato usan su alias `nombre_mcp` (contrato
1.3: el punto pasa a guion bajo, porque varios arneses no lo admiten); el
proxy también llama al servidor por ese alias. `unit_checkpoint`,
`insumo_pull` y `railspec_sync` solo existen en el proxy. El actor nunca
viaja: el servidor lo deriva del token.

| Tool local | Tool del contrato | Qué añade el proxy |
|---|---|---|
| `unit_start` | `unit.start` | Base en HEAD del clon, rama `railspec/<unidad>`, worktree hermano |
| `unit_advance` | `unit.advance` | Vacía la cola antes; sin red devuelve la orden en curso |
| `unit_report` | `unit.report` | Snapshot, validación y artefacto construidos en local |
| `unit_checkpoint` | `unit.approve` | Formulario al humano (elicitation) |
| `unit_approve` | `unit.approve` | — |
| `unit_set_mode` | `unit.set_mode` | Solo a petición del humano (1.2) |
| `unit_integrate`, `unit_status`, `unit_list` | homónimas | Espejo local actualizado |
| `graph_query` | `graph.query` | Vector de la consulta calculado en local (1.1) |
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

## Pendiente

- **Hooks del arnés.** La hoja de ruta prevé hooks donde el arnés los tenga
  (por ejemplo, impedir en Claude Code una edición en el clon principal con
  una unidad en curso). Hoy solo lo dicen las reglas de conducta.
- **Instalación por usuario.** Los adaptadores se instalan por repositorio; un
  alcance de usuario (`~/.claude`, `~/.config/opencode`) queda para otra
  iteración, igual que distribuir `railspec` como binario.

- **Embeddings.** `codebase-memory-mcp` no expone sus vectores por CLI y el
  servidor nunca calcula embeddings de código: el delta viaja sin ellos y la
  búsqueda semántica sin vector hasta integrar un codificador local.
- **Rebase de una unidad.** Si el servidor emite una orden con otro commit
  base, el proxy se detiene y lo dice; rebasar el worktree queda para otra
  iteración.
