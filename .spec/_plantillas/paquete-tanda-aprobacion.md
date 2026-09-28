# Paquete de aprobación de tanda — <id de la tanda>

> Fase 0 (Aprobación de tanda). Documenta la aprobación humana para una tanda completa de unidades ejecutadas bajo `modo: desatendido`.

## Resumen de la tanda

- **id de la tanda:** <id>
- **objetivo:** <descripción de una oración>
- **número de unidades:** <N>
- **riesgo agregado:** bajo | medio | alto
- **fecha de aprobación:** <ISO-8601>

## Lista de unidades miembro

| # | id | título | riesgo | carril |
|---|---|---|---|---|
| 1 | <NNNN-slug> | <título> | bajo/medio/alto | <carril> |
| ... | ... | ... | ... | ... |

## Criterios compartidos (aplican a todas las unidades)

- <criterio verificable 1>
- <criterio verificable 2>
- ...

## Paradas tipificadas heredadas

- Las 14 condiciones de `.spec/PARADAS-SUPERVISADO.md` aplican tal cual.
- `tanda-completa` — abort: si una unidad falla de modo que afecta a las siguientes, abortar la tanda.
- `tanda-miembro-fallida` — auto-deferred: si una unidad tiene un gate que escala, marcar `escalado: auto-deferred` en `_estado.yaml` y continuar con la siguiente.

## Decisiones delegadas (la IA ejecuta sin pausa)

- <decisión 1>
- <decisión 2>
- ...

## Riesgos conocidos

- <riesgo 1>
- <riesgo 2>
- ...

## Suposiciones

- <suposición 1>
- <suposición 2>
- ...

## Aprobación humana

```yaml
aprobacion:
  decision: aprobado | aprobado-con-cambios | rechazado
  fecha: <ISO-8601>
  nota: <cambios si aplica>
```
