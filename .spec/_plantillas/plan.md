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

> Si el cambio no admite paralelismo real, sigue siendo **una fila de esta
> misma tabla**, no una nota aparte que la reemplace: un solo `G1` con Alcance
> "grupo único — cambios acoplados" y Archivos igual a todo lo que toca el
> cambio. Así conserva su columna Complejidad — sin fila, ese grupo no tendría
> dónde marcarse como complejo y nunca podría escalar a un modelo más capaz,
> justo el caso (una migración de datos, p. ej., que no admite paralelismo)
> donde más se necesitaría. Un fan-out sobre archivos compartidos produce
> conflictos, no velocidad — con un solo grupo, esto no aplica.
>
> `Complejidad: complejo` marca un grupo que exige más razonamiento que el
> resto (migración de datos, algoritmo no trivial, decisión de diseño con
> muchos grados de libertad) — la fase de implementación lo asigna a un modelo
> más capaz. Valores válidos: `estandar` (default) o `complejo`, siempre sin
> tilde y en minúscula tal como están en la tabla — es el literal que se
> persiste en `_estado.yaml > modelo_ejecucion > implementar`, no texto libre
> para reformular. No marcar todo como complejo, eso vacía la señal. Ver
> `.spec/MODELO-AGENTES.md`.

## Riesgos y mitigaciones

- **Riesgo:** ... — **Mitigación:** ...

## Comando de validación

Comando(s) que verifican el cambio end-to-end (build/typecheck/tests/validate del repo):

```
<comando>
```
