# Bitácora — <título de la unidad>

> Log append-only de handoff. Cada sesión añade una entrada de exactamente
> **1 línea** al **final**, sin cuerpo narrativo. El detalle de los hallazgos
> de gate vive en `_estado.yaml > gates`; este archivo es un índice temporal.

**Formato de cada entrada:**

```
## <ISO-8601> · <fase|gate>:<veredicto> · <resumen de una oración>
```

**Convención de merge (unidad 0098):** un conflicto de git en este archivo se resuelve tomando **ambos lados en orden cronológico** — cada entrada está delimitada por `## ` y trae su propio timestamp, así que basta con intercalarlas por fecha. Nunca se resuelve descartando una entrada ni reescribiendo la de otro colaborador.

---

## <ISO-8601> · fase:<spec|plan|tasks|implement> · <resumen>

## <ISO-8601> · gate:<spec|plan|tasks|codigo>:<veredicto> · <N> hallazgos en _estado.yaml