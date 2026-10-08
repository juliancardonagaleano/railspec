---
name: railspec-bucle
description: "Use when: hay una unidad de Railspec en curso y toca pedir la siguiente orden, ejecutarla y reportarla (bucle unit_advance → ejecutar → unit_report). Does NOT decidir fases, gates ni checkpoints: eso es del servidor y del humano. Keywords: railspec, unidad, orden de trabajo, unit_advance, unit_report, checkpoint."
---

# Railspec — bucle del cliente

El servidor Railspec conduce la unidad; tú ejecutas órdenes. Repite hasta que `unit_advance`
devuelva `cerrada`:

1. **Pide lo siguiente** con `unit_advance` (pasa `unidad` si hay más de una en local).
2. **Según `tipo`:**
   - `orden` — ejecútala (ver abajo) y luego `unit_report`.
   - `checkpoint` — llama `unit_checkpoint`: el arnés muestra al humano un formulario y su
     respuesta se registra sin pasar por ti. Si tu arnés no admite formularios (la tool falla por
     capacidad), muestra la pregunta al humano tal cual, espera su respuesta y llama
     `unit_approve` con su decisión exacta y su comentario. Nunca decidas tú.
   - `en-espera` — el servidor trabaja (gate, redacción). Espera `reintentar_en_s` y vuelve al 1.
   - `sin-conexion` — puedes seguir con la orden en curso si la hay; el reporte se encola.
     Avisa al humano de que los gates esperan a la reconexión.
   - `mandato-parado` — el mandato de la unidad ya no ampara trabajo (caducó, se detuvo, se revocó
     o no está aprobado). Detente: no trabajes más en las unidades de ese mandato y dile al humano la
     `causa` y el `detalle`. Solo una persona lo renueva o aprueba, en la consola web; no hay tool
     para eso. No reintentes en bucle: `reintentar_en_s` es la pista de cuándo volver a mirar.
   - `cerrada` — informa al humano y termina.
3. Si un resultado trae `reportes_rechazados` o `rechazos`, cuéntaselo al humano: el servidor
   manda y la orden siguiente ya lo tiene en cuenta. Un rechazo marcado `incierto` puede que el
   servidor sí lo tuviera: díselo así, sin afirmar ninguna de las dos cosas.
4. Si el humano pide cambiar de modo, llama `unit_set_mode` con el modo y su motivo. Solo lo
   admite el servidor tras research o tras el checkpoint del spec (si no, responde
   `conversion-no-permitida`: díselo). Nunca cambies de modo por iniciativa propia.

## Mandato (supervisado y desatendido)

`supervisado` y `desatendido` no abren los checkpoints de spec, plan y paquete porque una persona
aprobó antes un **mandato**: `plan` es su id. Úsalos solo si el humano los pidió y el mandato existe
y está aprobado (`mandate_get`). Puedes redactar un borrador con `mandate_propose` y pasarle al
humano la `huella` que devuelve; **nunca se aprueba desde el arnés**, lo hace él en la consola web.
`mandate_revoke` solo si el humano lo pide explícitamente: detiene todo el mandato.

- Si `unit_advance` trae un `checkpoint` con `causa_parada`, sigue su `como_resolver`. En
  `unidad-amparada-fallida` (desatendido) la unidad quedó diferida: no esperes, informa y pasa a la
  siguiente unidad del mandato. En `fuera-de-alcance`, `decision-reservada`, `reintentos-agotados` y
  `gate-escalado` es una parada: no la resuelvas tú, la decide una persona.
- `orden.mandato` trae las delegaciones (`D-n`) y `rutas_permitidas`. Puedes decidir solo lo que cubre
  una delegación `pre-decidida` o `con-criterio`, y debes declararlo en `decisiones` de `unit_report`
  (`delegacion`, `que`, `alternativas`, `revertir`) para que una persona lo revise.
- Si hace falta decidir algo `reservada`, o que ninguna delegación cubre, no decidas: repórtalo como
  `bloqueado` con el motivo.

## Ejecutar una orden

- Trabaja **solo en el `worktree`** que devuelve la tool, nunca en el clon principal.
- `redactar` / `refinar`: escribe el artefacto en `orden.ruta_artefacto` siguiendo
  `orden.instrucciones`, `orden.plantilla` y, al refinar, cada hallazgo de `orden.hallazgos`.
- `implementar`: resuelve `orden.tareas` tocando solo archivos de `orden.alcance.permitidos` y
  ninguno de `orden.alcance.prohibidos`. Cubre los criterios `CA-NN` que cita cada tarea.
- `validar`: no ejecutes nada; llama `unit_report` y el proxy corre el comando.
- Usa `orden.contexto` (gobernanza, rebanada del grafo, insumos, hallazgos previos). Las
  referencias apuntan a símbolos y archivos: léelos del worktree. Para más contexto usa
  `graph_query`; devuelve referencias, nunca código.
- La rebanada del grafo (`contexto.grafo`) solo viene en las órdenes de spec, plan y tasks, y
  busca por nombre de símbolo, no por significado. Lee `contexto.grafo_avisos` y los `avisos` de
  `graph_query`: si dicen que no hay grafo, que el repositorio no está indexado o que el índice
  está desactualizado, una rebanada vacía significa «no sé», no «no existe»: busca en el worktree.
- Para buscar por lo que hace el código y no por su nombre, usa `code_search` (palabras del cuerpo,
  sin red). Si dice que no hay índice, llama `code_index` una vez; lee sus `avisos`.

## Reportar

Llama `unit_report` con `resultado` (`completado`, `fallido` o `bloqueado`), las
`tareas_completadas` (`T-NN`), `decisiones` si decidiste algo amparado por el mandato, y `motivo` si no
completaste. No adjuntes diffs ni código: el proxy
construye el snapshot según la política de código del repositorio, corre la validación en local y
lee el artefacto del disco. Si el proxy rechaza el reporte (archivos fuera de alcance, secretos
detectados), corrige lo que indica y vuelve a reportar.
