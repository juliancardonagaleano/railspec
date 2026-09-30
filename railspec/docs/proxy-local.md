# Proxy local (`railspec-local`)

El proxy es lo único de Railspec que corre en la máquina del desarrollador.
El arnés (Claude Code, OpenCode) lo lanza como servidor MCP por stdio; el
proxy habla con `railspec-server` por MCP Streamable HTTP usando solo los
contratos de `railspec-contracts`. El servidor decide fase, gate y
presupuesto; el arnés escribe el código; el proxy hace en local lo que el
protocolo exige en local.

## Instalación en un repositorio

```
pip install railspec-local            # instala el comando `railspec`
pip install codebase-memory-mcp       # opcional: indexado local (delta de símbolos y aristas)
railspec instalar --org acme --workspace certificados --repositorio certificados-api \
  --arnes claude-code --arnes opencode
export RAILSPEC_URL=https://railspec.example/mcp   # endpoint MCP del servidor
export RAILSPEC_TOKEN=...                          # token OAuth de GitHub del desarrollador
```

`railspec instalar` escribe `.railspec/config.json` (versionable, sin
secretos: org, workspace, slug del repositorio, nivel de código, arnés) y el
adaptador de cada arnés. `--nivel` fija el nivel del vínculo; si falta rige
`restringido`. `railspec instalar --verificar` informa deriva sin escribir.
El token nunca se escribe en disco.

## Adaptadores: el stack de tres piezas

| Pieza | Claude Code | OpenCode |
|---|---|---|
| Registro del proxy | `.mcp.json` → `mcpServers.railspec` | `opencode.jsonc` → `mcp.railspec` |
| Comando de arranque `/railspec` | `.claude/commands/railspec.md` | `.opencode/command/railspec.md` |
| Bucle de cliente | `.claude/skills/railspec-bucle/SKILL.md` | `.opencode/skill/railspec-bucle/SKILL.md` |
| Reglas de conducta | bloque delimitado en `CLAUDE.md` | bloque delimitado en `AGENTS.md` |

La fusión solo toca la entrada `railspec` y el bloque entre marcadores; el
resto de cada archivo se conserva. Un JSON inválido no se pisa.

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
en modo `cli` (sin demonio, sin escribir `.codebase-memory/` en el árbol):
indexa el worktree y, aparte, los archivos tocados tal como estaban en el
commit base para calcular símbolos y aristas borrados. Los ids salen de
`id_simbolo` del contrato. Probado con la versión 0.11.0.

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

- **Embeddings.** `codebase-memory-mcp` no expone sus vectores por CLI y el
  servidor nunca calcula embeddings de código: el delta viaja sin ellos y la
  búsqueda semántica sin vector hasta integrar un codificador local.
- **Rebase de una unidad.** Si el servidor emite una orden con otro commit
  base, el proxy se detiene y lo dice; rebasar el worktree queda para otra
  iteración.
