# Tareas — <título de la unidad>

> Fase 3-4. Checklist derivado de `plan.md`. **Fuente de verdad resumible**: el estado de estas casillas indica qué falta. Marca `[x]` al completar cada tarea.
>
> Cada tarea declara los criterios de aceptación de `spec.md` que cubre
> (`cubre:`). El gate de tareas falla si algún `CA-NN` del spec no aparece en
> ninguna tarea.

## Pendientes

- [ ] T1 — <tarea atómica> · archivos: `...` · cubre: CA-01
- [ ] T2 — <tarea atómica> · archivos: `...` · depende de: T1 · cubre: CA-02, CA-03
- [ ] T3 — ...

## Validación final

- [ ] Ejecutar comando de validación (ver `plan.md`)
- [ ] Verificar criterios de aceptación de `spec.md` (uno por uno, por id)
- [ ] Gate de código ejecutado (`sdd-gate` fase `codigo`) con veredicto registrado
- [ ] Actualizar `_estado.yaml` → `fase: done`, `estado: completado`

## Notas de implementación

Detalles que surjan al implementar (decisiones puntuales, desvíos del plan).
