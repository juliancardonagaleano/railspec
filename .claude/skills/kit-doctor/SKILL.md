---
name: kit-doctor
description: "Use when: diagnosticar el estado del entorno agéntico local — los servidores MCP de los que depende este repositorio (`pce-mcp`, `gitnexus`, `codebase-memory-mcp`) y el índice de grafo local — con un reporte determinista, y remediar uno de ellos solo con confirmación explícita del operador en la misma corrida. Does NOT reconectar un servidor caído dentro de la sesión viva (no existe ese subcomando; la única vía es `/mcp`), ni tocar `.mcp.json`/`~/.claude.json` como salida. Keywords: doctor, diagnóstico, MCP caído, gitnexus, codebase-memory-mcp, pce-mcp, índice de grafo, reindexar, entorno agéntico."
compatibility: "Claude-first (read-only por defecto, remediación gated por confirmación TTY). Ver `.agents/skills/COMPATIBILIDAD.md`"
license: "Proprietary"
context: fork
model: haiku
metadata:
  user-invocable: "true"
---

# Skill: kit-doctor

Orquestador **delgado**: invoca `scripts/kit_doctor.py` con lo que recibe y
devuelve, íntegro y sin interpretar, el reporte de filas y el código de
salida que imprimió. No decide nada por su cuenta —qué tratamiento dar a una
fila degradada es de quien invoca la skill—, no reintenta comandos, ni
reconecta nada por su cuenta: el contrato de solo lectura por defecto y la
confirmación explícita antes de escribir son del script, y esta skill no le
añade ni le quita nada.

## Cuándo usar / NO usar

| Usar | NO usar |
|---|---|
| El operador quiere saber si `pce-mcp`, `gitnexus`, `codebase-memory-mcp` o el índice de grafo local están sanos, antes de seguir trabajando | Consultar contenido de gobernanza — eso es `pce-mcp` vía su protocolo MCP, no esta skill |
| Reinstalar `gitnexus` (paquete npm global) o `codebase-memory-mcp` (instalador oficial de su repositorio), con confirmación explícita en la misma corrida | Reconectar un servidor MCP caído dentro de la sesión viva — no existe ese subcomando; la única vía es `/mcp` dentro del cliente |
| Reindexar el grafo local cuando quedó desactualizado, con confirmación explícita | Remediar `pce-mcp` — no tiene remediación disponible por diseño (es un script del propio repositorio; su caída es de infraestructura) |
| Ver qué comando produjo cada estado del reporte, para reproducirlo a mano | Decidir el tratamiento de una fila degradada o interpretar el reporte por el operador — eso lo hace quien invoca esta skill |

## Flujo

1. Invocar, sin ningún argumento, para un diagnóstico de solo lectura:

   ```
   python3 scripts/kit_doctor.py
   ```

   Con `--action <sujeto>` para pedir, además, una acción de escritura sobre
   un único sujeto (`gitnexus`, `codebase-memory-mcp` o `graph-index`; pedirla
   sobre `pce-mcp` está soportado pero nunca ejecuta nada, porque no tiene
   remediación disponible):

   ```
   python3 scripts/kit_doctor.py --action gitnexus
   python3 scripts/kit_doctor.py --action codebase-memory-mcp
   python3 scripts/kit_doctor.py --action graph-index
   ```

   La acción de escritura solo se aplica si, en esa misma corrida, la
   confirma una respuesta afirmativa leída de una entrada estándar
   interactiva (un TTY real). Una invocación sin TTY, o con un argumento o
   variable de entorno que pretenda sustituir esa confirmación, nunca cuenta
   como confirmación: el script la rechaza sin ejecutar nada.

2. Capturar stdout (el reporte completo, fila por fila, más cualquier pista
   de reconexión que aparezca al final) y el código de salida.

3. Devolver ambos **tal cual** a quien invocó, sin resumir ni traducir. El
   comando citado al final de cada fila (`command=<literal>`) es lo que el
   operador reproduce a mano si quiere ver el detalle crudo — **no**
   interpretarlo ni parafrasearlo aquí.

Esta skill nunca reintenta un comando ni compone dos acciones de escritura en
la misma corrida: pedir más de una a la vez se rechaza sin ejecutar ninguna,
con el mismo código que la falta de confirmación.

## Output

El reporte de `kit_doctor.py`, filas con la forma
`subject=<sujeto> check=<chequeo> state=<estado> command=<comando citado>`
(la fila de frescura del grafo suma `commits_behind=<n> threshold=<n>` antes
del comando), más una pista literal `/mcp` cuando algún servidor quedó
`not_responding` —esta herramienta nunca ofrece un subcomando de reinicio,
porque no existe—, sin interpretar. Quien invoca decide el tratamiento.

Códigos de salida, de mayor a menor precedencia — se emite el primero que
aplique:

| Código | Significado |
|---|---|
| 4 | Se pidió una acción de escritura sobre un sujeto sin remediación disponible (`pce-mcp`): nada se ejecutó |
| 3 | Se pidió una acción de escritura sin confirmación explícita en la misma corrida, se pidió más de una a la vez, o la remediación confirmada falló — en este último caso el reporte cita el comando exacto y el stderr del intento, para que el operador lo reproduzca o revise a mano |
| 2 | Al menos una fila quedó en `state=unknown` |
| 1 | Al menos una fila quedó en `not_responding`, `absent` o `stale`, y ninguna en `unknown` |
| 0 | Todas las filas sanas; si hubo una acción de escritura confirmada, se aplicó sin error |

Remediación por sujeto, cuando existe: `gitnexus` se reinstala como paquete
npm global; `codebase-memory-mcp` se reinstala con el instalador oficial de
su repositorio de origen, invocado sin que configure clientes; el índice de
grafo local se reindexa con el comando que el contrato del repositorio ya
define. Ninguna usa un gestor de paquetes del sistema operativo ni delega
configuración de clientes al instalador. `pce-mcp` no tiene camino de
remediación: su caída se diagnostica y se reporta, nunca se repara desde
aquí.
