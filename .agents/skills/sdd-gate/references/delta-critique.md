# Recorte por delta entre iteraciones (`sdd-gate`, paso 1 y paso 4)

Mecánica de CA-10/CA-11: desde la iteración 2 en adelante, el panel no vuelve
a leer el artefacto completo. Lee **qué cambió** desde la corrección anterior.

## Cuándo se activa

- **Iteración 1 de cualquier gate**: nunca se recorta. El panel recibe el
  artefacto completo, sin excepción (CA-11) — no hay "iteración anterior"
  contra la cual calcular un delta.
- **Iteración *n* > 1**: el panel recibe el delta desde la iteración *n-1*, no
  el artefacto completo de nuevo (CA-10).

## El script: `gate_delta.py rotate`

Invocado en el paso 1 de `sdd-gate` (al cargar contexto y rúbrica), una vez
por iteración, sobre el artefacto que esa iteración va a evaluar:

```bash
python3 .spec/scripts/gate_delta.py rotate \
  --unidad .spec/units/<NNNN-slug> \
  --fase <spec|plan|tasks|codigo> \
  --artefacto <ruta-al-artefacto-en-evaluacion>
```

- **Sin caché previa** para esa `<fase>` bajo `<unidad>/.gate-delta/` → imprime
  el texto completo del artefacto, marcado `ITERACIÓN 1`. Esto **nunca** es un
  error: una unidad retomada a mitad de gate, sin la caché de una sesión
  anterior (no persiste entre procesos por diseño), simplemente vuelve a leer
  completo una vez de más — el peor caso posible, nunca un fallo (plan.md §
  Riesgos y mitigaciones).
- **Con caché previa** → imprime el diff unificado (`difflib.unified_diff`,
  stdlib) entre el contenido cacheado y el actual, marcado `DELTA`.
- En ambos casos, **rota la caché** al contenido actual antes de devolver el
  control: la próxima llamada, en la siguiente iteración, calculará el delta
  contra lo que se acaba de evaluar — nunca acumulado desde la iteración 1
  (literal del "Resultado esperado" del spec de la unidad 0116).

La caché vive en `.spec/units/<unidad>/.gate-delta/<fase>.md` — **efímera y
gitignored**, mismo tratamiento que `.spec/.usage/`. No es una copia
competidora del artefacto ni un registro persistente
(`pri-gob-fuente-verdad-unica`): existe solo mientras el gate de esa fase está
en curso.

## Qué recibe cada crítico en iteración > 1

El prompt de cada crítico lleva **dos** insumos, no uno (literal de CA-10):

1. El diff que imprimió `gate_delta.py rotate` para esta iteración.
2. La lista de hallazgos de la iteración anterior — ya está en memoria de la
   corrida del gate (los pasos 6/7 la conservan para decidir convergencia); no
   hace falta releerla de ningún lado.

El diff solo no basta: sin los hallazgos previos, el crítico no puede
verificar que una corrección puntual efectivamente resolvió el hallazgo que
la motivó — solo vería que algo cambió, no si cambió lo correcto.

## Qué NO recorta

El recorte por delta acota **únicamente** la relectura del artefacto (paso 4).
No toca:

- **La gobernanza** (paso 3): la consulta a `pce-mcp` se repite completa en
  cada iteración, sin excepción. Recortar qué gobernanza se re-consulta
  reduciría silenciosamente lo que el gate verifica (plan.md § Riesgos) —
  distinto de recortar cuánto del artefacto se relee.
- **El contrato de tiers/lentes/iteraciones** de `.spec/README.md` (CA-13):
  el recorte cambia *cuánto texto* recibe cada crítico, nunca *cuántos*
  críticos corren ni *cuántas* iteraciones caben en el presupuesto del tier.

## Al persistir el veredicto

`sdd-gate` limpia la caché de delta de esa `<fase>` (`gate_delta.py clear`) en
el paso 8, al escribir el veredicto en `_estado.yaml`. Así una retoma
posterior de la unidad — sea porque el gate escaló, sea porque una fase nueva
vuelve a invocar el gate — siempre arranca en "iteración 1, texto completo",
nunca hereda una caché de una corrida ya cerrada.
