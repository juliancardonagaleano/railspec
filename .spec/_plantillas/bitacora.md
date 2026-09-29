# Bitácora — <título de la unidad>

> Log append-only de handoff. Cada sesión añade una entrada de exactamente
> **1 línea** al **final**, sin cuerpo narrativo. El detalle de los hallazgos
> de gate vive en `_estado.yaml > gates`; este archivo es un índice temporal.

**Formato de cada entrada:**

```
## <ISO-8601> · <fase|gate>:<veredicto> · <resumen de una oración>
```

**Convención de merge:** ver [.spec/README.md § concurrencia](README.md#concurrencia).

---

## <ISO-8601> · fase:<spec|plan|tasks|implement> · <resumen>

## <ISO-8601> · gate:<spec|plan|tasks|codigo>:<veredicto> · <N> hallazgos en _estado.yaml