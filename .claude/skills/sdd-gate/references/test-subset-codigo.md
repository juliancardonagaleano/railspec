# Subset mínimo de test por archivos tocados (`fase: codigo`, CA-11/CA-12/CA-13)

Aplica **solo** a `fase: codigo`, dentro de la capa determinista (paso 2, ver
`deterministic-layer.md`). El algoritmo de mapeo vive en
`.spec/scripts/test_subset.py` — este documento no lo duplica en prosa
(`pol-ia-no-embeber-conocimiento`); referencia el script y describe la regla
de integración.

## Regla operacionalizada (CA-13)

1. Obtener los archivos tocados de la unidad en curso (los mismos que
   `comando_validacion` de esta iteración cubriría).
2. Invocar:

   ```bash
   python3 .spec/scripts/test_subset.py plan --root <raíz-del-repo> --files <archivo-1> <archivo-2> ...
   ```

3. El script cubre **solo** dos subárboles: `orchestrator/` (pytest) y
   `studio/app/` (Jest). Para cada uno imprime una de tres líneas:
   - `SUBSET — <subárbol>: <comando>` — el mapeo fue limpio; ese comando
     reemplaza la porción de `comando_validacion` correspondiente a ese
     subárbol.
   - `FALLBACK — <subárbol>: <comando>` — algún archivo tocado en ese
     subárbol no fue mapeable con confianza (archivo de configuración,
     `conftest.py`, cero o más de un test por convención de stem). El
     comando impreso es la corrida completa por defecto de ese subárbol
     (ya optimizada por CA-04/05 en `orchestrator/`, sin cobertura por
     CA-01 en `studio/app/`) — **nunca** se corre menos que hoy.
   - `NO-APLICA — <subárbol>: no-aplica` — ningún archivo tocado cae en ese
     subárbol; no se emite comando para él y no cuenta como incumplimiento.
4. Si **ambos** subárboles resultan `no-aplica`, el script imprime además
   `SIN-SUBSET: no-aplica` — el resto de `comando_validacion` de la unidad
   (lo que no sea `orchestrator/`ni `studio/app/`) sigue corriendo tal cual,
   sin ningún recorte.
5. Cualquier archivo tocado que caiga fuera de `orchestrator/` y
   `studio/app/` (p. ej. `studio/` raíz, `.agents/skills/`, `.spec/`) no lo
   toca este script — la porción de `comando_validacion` correspondiente a
   esos subárboles se ejecuta completa, sin recorte, siempre.

## Regla de "ante cualquier duda, correr todo"

El script está diseñado para nunca inferir de más: cualquier archivo tocado
que no encaje exactamente en una de las convenciones documentadas en el
docstring de `test_subset.py` descarta el subset **completo** de ese
subárbol, no solo el archivo dudoso — cae al comando por defecto de ese
subárbol. El subset, cuando se activa, es siempre superconjunto o igual del
test del archivo tocado, nunca menos (`pol-gob-puerta-calidad-pre-entrega`).

## Fallback conservador al `comando_validacion` completo

Si `test_subset.py` no está disponible, o su ejecución falla con un error no
documentado en su propio header, la capa determinista **no** intenta
adivinar el subset a mano: corre `comando_validacion` completo tal como está
declarado en `_estado.yaml` de la unidad, sin recorte alguno. El script en sí
mismo no falla nunca por diseño (exit 0 siempre, ver su propio header) — solo
el proceso que lo invoca podría no encontrarlo o no poder ejecutarlo, y ese
es el único caso que activa este fallback de nivel superior.
