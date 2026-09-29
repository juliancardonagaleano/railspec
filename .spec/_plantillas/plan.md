# Plan técnico — <título de la unidad>

> Fase 2 (Planificar). Define el CÓMO. Se construye a partir de un `spec.md` aprobado.

## Enfoque

Resumen de la estrategia técnica elegida y por qué (1-3 párrafos).

## Archivos a crear / modificar

| Archivo | Acción | Detalle |
|---|---|---|
| `ruta/al/archivo` | crear/modificar/eliminar | qué cambia y por qué |

## Reutilización (no reinventar)

Funciones, utilidades o patrones existentes que se aprovechan, con su ruta.

- `ruta:linea` — ...

## Decisiones de diseño

- **Decisión:** ... — **Razón:** ... (citar ADR/policy si aplica)

## Grupos de tareas paralelizables

Insumo del fan-out de la fase de implementación. Cada grupo debe poder
ejecutarse **sin coordinar con los demás**: si dos grupos tocan el mismo
archivo, van en el mismo grupo. Máximo 4 grupos.

| Grupo | Alcance | Archivos (disjuntos entre grupos) | Depende de | Complejidad |
|---|---|---|---|---|
| G1 | ... | `...` | — | estandar |
| G2 | ... | `...` | G1 | estandar |

> Si el cambio no admite paralelismo real, sigue siendo **una fila de esta misma tabla** (un solo `G1` con Alcance "grupo único — cambios acoplados"). Valores válidos de `Complejidad`: `estandar` (default) o `complejo`, literal que se persiste en `_estado.yaml > modelo_ejecucion > implementar`. `Complejidad: complejo` marca un grupo que exige más razonamiento que el resto (la fase de implementación lo asigna a un modelo más capaz); no marcar todo como complejo, eso vacía la señal.
>
> Ver [.spec/README.md § Reglas del fan-out](README.md#reglas-del-fan-out) para justificación detallada y ejemplos.

## Riesgos y mitigaciones

- **Riesgo:** ... — **Mitigación:** ...

## Comando de validación

Comando(s) que verifican el cambio end-to-end (build/typecheck/tests/validate del repo):

```
<comando>
```
