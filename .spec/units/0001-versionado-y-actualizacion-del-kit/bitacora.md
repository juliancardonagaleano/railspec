# Bitácora — Versionado y actualización del kit

> Log append-only de handoff. Cada sesión añade una entrada de exactamente
> **1 línea** al **final**, sin cuerpo narrativo. El detalle de los hallazgos
> de gate vive en `_estado.yaml > gates`; este archivo es un índice temporal.

**Convención de merge (unidad 0098):** un conflicto de git en este archivo se resuelve tomando **ambos lados en orden cronológico** — cada entrada está delimitada por `## ` y trae su propio timestamp, así que basta con intercalarlas por fecha. Nunca se resuelve descartando una entrada ni reescribiendo la de otro colaborador.

---

## 2026-09-28T00:00:00Z · fase:spec · Unidad sembrada en modo bootstrap con research.md y spec.md escritos a mano, en el mismo commit que fija installer/kit_manifest.yaml.

## 2026-09-28T00:00:00Z · gate:spec:refinado · Panel de 1 crítico (L1+L4, tier bajo): 0 alta, 3 media, 2 baja, todos corregidos; L4 confirmó con evidencia del repo que no hay superficie de gobernanza propia del kit, por lo que no se emitió sin-gobernanza.

## 2026-09-28T00:00:00Z · fase:done · Unidad cerrada; el diseño de las tres capacidades queda sembrado para una unidad sucesora todavía sin crear.
