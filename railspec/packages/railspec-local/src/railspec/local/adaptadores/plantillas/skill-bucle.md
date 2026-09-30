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
   - `cerrada` — informa al humano y termina.
3. Si un resultado trae `reportes_rechazados` o `rechazos`, cuéntaselo al humano: el servidor
   manda y la orden siguiente ya lo tiene en cuenta.
4. Si el humano pide cambiar de modo, llama `unit_set_mode` con el modo y su motivo. Solo lo
   admite el servidor tras research o tras el checkpoint del spec (si no, responde
   `conversion-no-permitida`: díselo). Nunca cambies de modo por iniciativa propia.

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

## Reportar

Llama `unit_report` con `resultado` (`completado`, `fallido` o `bloqueado`), las
`tareas_completadas` (`T-NN`) y `motivo` si no completaste. No adjuntes diffs ni código: el proxy
construye el snapshot según la política de código del repositorio, corre la validación en local y
lee el artefacto del disco. Si el proxy rechaza el reporte (archivos fuera de alcance, secretos
detectados), corrige lo que indica y vuelve a reportar.
