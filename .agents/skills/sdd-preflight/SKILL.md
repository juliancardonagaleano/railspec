---
name: sdd-preflight
description: "Use when: correr el chequeo previo sin efectos laterales de un mandato supervisado, antes de que `sdd-supervisado` tome el lock — lock, MCP de gobernanza, árbol git y validador de mandatos, cada uno con su causa literal de BLOCK. Does NOT tomar el lock ni escribir estado: cero efectos laterales por defecto es su contrato; ver § Self-heal para la excepción opt-in. Keywords: preflight, mandato supervisado, MCP, lock de instancia, validador, chequeo previo."
compatibility: "Claude-first (read-only, cero escrituras). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
context: fork
model: haiku
metadata:
  user-invocable: "true"
---

# Skill: SDD — Preflight de mandato

Orquestador **delgado**: invoca `.spec/scripts/preflight.py` con lo que recibe y
devuelve, íntegra y sin interpretar, la tabla PASS/BLOCK y el código de salida que
imprimió. No decide nada por su cuenta —qué tratamiento dar a un BLOCK es de
`sdd-supervisado` (tabla causa → tratamiento de CA-08)—, no toma el lock, no escribe
`_estado.yaml`, ni bitácora, ni el mandato: el contrato de cero escrituras es del
script (`preflight.py`), y esta skill no le añade ninguna.

Mecanismo propio de este repo. Nace de la unidad `0114` para adelantar, antes de
escribir cualquier estado, las paradas que antes solo se descubrían **después** de
que `sdd-supervisado` § 1.1 tomara el lock (K5 de la línea base de `0113`).

## Cuándo usar / NO usar

| Usar | NO usar |
|---|---|
| Al inicio de **todo** arranque de instancia de `sdd-supervisado` —lanzamiento o retoma—, antes de § 1.1 | Tomar el lock → `instance_lock.py acquire`, invocado por `sdd-supervisado` § 1.1 (comprobación decisiva, no duplicada aquí) |
| Probar si el MCP de gobernanza responde con las credenciales de la sesión, antes de comprometerse a nada | Consultar contenido de gobernanza (resolver una entidad de conocimiento, aplicar una regla) → eso es `pol-dev-patron-retrieval`, fuera de esta skill |
| Saber si el árbol de la(s) unidad(es) en curso está sucio antes de escribir sobre él | Decidir qué hacer con el resultado — eso es `sdd-supervisado` |

## Entradas

Quien invoca (siempre `sdd-supervisado`, en lanzamiento o retoma) da:

- **mandato**: la ruta del archivo (`.spec/planes/<id>/plan-maestro.md` o
  `.spec/units/<NNNN-slug>/mandato.md`).
- **modo**: `launch` o `resume` — informativo, solo se imprime en la cabecera.
- **unidades del alcance de CA-03**, por sus **dos fuentes**, unidas por quien
  invoca (esta skill no las infiere): la unidad recibida como argumento explícito de
  la invocación (única fuente en lanzamiento y en unidad aislada) más
  `unidades-en-curso` de la entrada vigente de `## Punto de retoma` cuando la hay
  (retoma con `## Punto de retoma` no vacío).
- Opcional: `SDD_PREFLIGHT_MCP_CONFIG` del entorno, si la sesión (o el runner del
  piloto) la exportó — la pasa como `--mcp-config` para que el preflight compruebe
  **la misma** configuración MCP que la sesión, nunca una distinta (D-3 de `plan.md`
  de `0114`: un origen que no sea `default` deja la fila del MCP no representativa
  de la sesión real, y la tabla lo dice).

## Flujo

1. Invocar:

   ```
   python3 .spec/scripts/preflight.py \
     --mandate <ruta-del-mandato> \
     --unit <dir-unidad-1> [--unit <dir-unidad-2> …] \
     --mode launch|resume \
     [--mcp-config "$SDD_PREFLIGHT_MCP_CONFIG"]
   ```

   Sin `--unit` el script sale con `preflight-error` (nunca infiere el alcance de la
   fila git, CA-03) — por eso el paso previo de quien invoca es reunir las unidades,
   no esta skill.

2. Capturar stdout (la tabla completa, con las cuatro filas — lock, mcp, git,
   validador — evaluadas siempre, sin detenerse en el primer BLOCK) y el código de
   salida (`0` PASS, `1` BLOCK por regla, `2` `preflight-error`).

3. Devolver ambos **tal cual** a quien invocó, sin resumir ni traducir. La causa
   literal de cada fila (`ausente`, `401`, `presupuesto agotado`, `timeout`,
   `coincide`/rutas sucias, los códigos del validador, `preflight-error: <motivo>`)
   es lo que `sdd-supervisado` cita en su bitácora y en `## Paradas` — **no**
   interpretarla ni parafrasearla aquí.

Esta skill **no** consulta gobernanza por su cuenta: su único contacto con el MCP es
la prueba de conectividad y credencial que hace `preflight.py` (resolver una entidad
conocida y mirar si la llamada tuvo éxito), no una consulta de conocimiento que
informe un criterio — por eso no lleva el paso de retrieval en dos tiempos que exige
`pol-dev-patron-retrieval`.

## Output

La tabla PASS/BLOCK completa y el código de salida de `preflight.py`, sin
interpretar. Quien invoca decide el tratamiento (tabla causa → tratamiento de CA-08
en `sdd-supervisado`).

## Self-heal (opt-in `--self-heal`)

Extensión **opt-in** del mismo script, misma skill delgada: sin el flag, cada fila
corre exactamente la ruta de solo lectura de arriba — output y código de salida
byte-idénticos a los de siempre. Con `--self-heal` y su `--session` obligatorio
(el id de la propia sesión, nunca inferido), tres remediaciones pueden disparar,
cada una con su propia evidencia explícita:

| Fila | Remediación | Evidencia exigida |
|---|---|---|
| `lock` | Reap del lock huérfano: vacía `## Instancia en curso` del mandato y anexa una línea a `bitacora.md` (timestamp, sesión, PID, evidencia), y hace un `git commit` real de esos dos archivos (mandato + `bitacora.md`) en la rama **actual** — nunca en la rama seed que crea la remediación de la fila `git` | `sesion` del lock coincide con `--session` **y** el `pid` registrado ya no está vivo (`pid_alive()`). Sin bullet `pid` (lock preexistente) no hay reap — falta de evidencia nunca se trata como evidencia de abandono |
| `git` | Auto-commit (nunca stash) de las rutas sucias del mismo alcance que ya calcula la fila `git`, hacia una rama seed nueva `self-heal/<sesión saneada>-<timestamp UTC>`; el árbol vuelve a la rama original tras el commit | Rutas sucias detectadas bajo el alcance recibido (`--unit`/mandato) |
| `mcp` | Reintento de `pce-mcp` con backoff exponencial acotado (`--self-heal-mcp-retries`, default 3; cada intento abre su propio presupuesto `--mcp-timeout`) | El primer intento no resuelve la entidad conocida |

Toda remediación **exitosa** etiqueta esa fila y el resultado agregado como
`DEGRADED` (exit `0`, igual que `PASS`) — nunca un `ok`/`PASS` sin calificar. El
agotamiento total de los reintentos del MCP sigue siendo `BLOCK` ("sin
gobernanza"), igual que hoy: `DEGRADED` nunca sustituye ese bloqueo. Una
remediación que falla a mitad de camino se reporta `BLOCK` con detalle explícito
`self-heal-fallo: <paso>` — nunca `PASS` ni un `DEGRADED` silencioso, y sin dejar
el árbol o el lock en un estado intermedio ambiguo.

**Ciclo de vida de la rama seed.** El self-heal crea la rama `self-heal/*` y la
deja en el repo tras la corrida — **no** la fusiona ni la borra por su cuenta. Es
el operador quien la revisa después y decide fusionarla o borrarla antes de seguir
trabajando; sin esa revisión, el repo acumula ramas seed indefinidamente. Igual de
explícito: activar `--self-heal` puede sacar a esta corrida del contrato de
cero-escrituras que rige por defecto — solo cuando una remediación realmente actúa
(lock reapeado, commit de rama seed, línea de bitácora) deja de ser una corrida de
solo lectura.

**Excepción acotada a `sdd-supervisado/SKILL.md` § 1.1.** Esa sección fija la regla
general: "No expira sola: un lock huérfano lo libera **solo el autor de la entrada
de mandato vigente**, como cualquier parada, dejando entrada de bitácora del
mandato con actor, fecha y motivo." El reap de la fila `lock` de arriba es una
excepción **acotada a esta skill**, invocada explícitamente vía `--self-heal`, y
solo alcanza al lock de la **propia** sesión/mandato que corre la invocación — un
lock de otra sesión nunca se reapea aquí, sigue siendo ese acto humano del autor de
la entrada de mandato vigente. Esta sección documenta la excepción del lado de
`sdd-preflight`; `sdd-supervisado/SKILL.md` § 1.1 no se edita (fuera del alcance
exclusivo de esta unidad) — su regla general sigue siendo el comportamiento
correcto y completo para quien no usa `--self-heal`.
