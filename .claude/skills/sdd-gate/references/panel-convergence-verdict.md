# Mecánica del panel y cierre del gate (`sdd-gate`, pasos 4-8)

> Esta referencia solo aplica a `fase: spec` y `fase: codigo` — desde
> `0117-D6` (2026-09-20), `plan` y `tasks` terminan en la capa determinista
> (paso 2 de `SKILL.md`) y nunca llegan a esta mecánica.

## Mecánica del panel (paso 4)

Cada crítico recibe: el artefacto (más `tasks.md` en `fase: codigo`, por el
paso 1), la gobernanza recuperada en el paso 3 — es la fuente; el crítico solo
vuelve a `pce-mcp` por un detalle puntual que el paso 3 no le dio, no para
repetir la búsqueda —, y **el texto completo de su lente** copiado de
`references/rubrica-<fase>.md` (no solo el nombre del lente).

**Cuánto del artefacto recibe depende de la iteración** (CA-10/CA-11, ver
`references/delta-critique.md`): en la **iteración 1** es el artefacto
completo, siempre. Desde la **iteración > 1**, el crítico recibe en su lugar
el diff de `gate_delta.py rotate` (paso 1) **y** la lista de hallazgos de la
iteración anterior — ambos insumos, no solo el diff: sin los hallazgos
previos no puede verificar que una corrección puntual resolvió lo que la
motivó.

Qué subagente lanzar por lente. El modelo y el effort de cada uno salen del
frontmatter de su propio archivo en `.claude/agents/` — esta tabla solo asigna
el rol, no los repite. La asignación de modelo es un mecanismo propio de este
repo, ver `.spec/MODELO-AGENTES.md` para el porqué:

| Fase | Lentes | Subagente |
|---|---|---|
| `spec` | L1, L2 (tier `alto`: crítico A) · L3, L4 (tier `alto`: crítico B) · todos (tier `medio`/`bajo`: un solo crítico) | `sdd-critico-profundo` |
| `codigo` | L2 — Correctitud | `sdd-critico-profundo` |
| `codigo` | L1 — Cumplimiento de `CA-NN`, L3 — Encaje | `sdd-critico-cumplimiento` |

`plan` y `tasks` no aparecen: desde `0117-D6` no corren panel (ver nota de
arriba).

**Si un `subagent_type` de la tabla no resuelve** (falta `.claude/agents/`,
checkout sin materializar): no bloquear el gate por eso — es un mecanismo
temporal, no una dependencia dura del protocolo. Lanzar `general-purpose` con
la misma lente y las mismas instrucciones que le tocaban a ese rol, anotarlo
en bitácora, y en el paso 8 persistir `subagente: general-purpose` en vez de
inventar que corrió el rol previsto. Detalle completo en
`.spec/MODELO-AGENTES.md` § "Si un subagente nombrado no está disponible".

Cada crítico devuelve una lista de hallazgos con esta forma:

```
severidad: alta | media | baja
lente: <nombre del lente>
donde: <sección o línea del artefacto>
problema: <qué está mal, en una frase>
correccion: <qué habría que cambiar>
```

Reglas del panel:

- Un crítico que no encuentra nada devuelve lista vacía. **No se inventan
  hallazgos para justificar la corrida.**
- Severidad `alta` = el artefacto es inservible o incumple gobernanza.
  `media` = degrada calidad o deja ambigüedad real. `baja` = mejora opcional.

## 5. Verificación adversarial (solo tier `alto`)

Antes de refinar, cada hallazgo de severidad `alta` pasa por un subagente
`sdd-refutador` cuya instrucción es **refutarlo**: "intenta demostrar que este
hallazgo es falso o irrelevante; ante la duda, refuta". Los hallazgos
refutados se descartan y se anotan en bitácora. Esto evita que el
refinamiento persiga críticas plausibles pero incorrectas.

## 6. Refinar

- Aplicar al artefacto las correcciones de los hallazgos `alta` y `media`
  supervivientes. Los `baja` se anotan en bitácora y no bloquean.
- Si un hallazgo `alta` **no puede** resolverse sin una decisión humana
  (conflicto de gobernanza, ambigüedad del negocio, cambio de alcance): no se
  inventa la respuesta. Se marca como hallazgo sin resolver y el gate escalará.

## 7. Convergencia

Una **iteración** = una tanda de panel (paso 4) más su refinamiento (paso 6).
`gates.<fase>.iteraciones` es el contador **acumulado por fase entre
invocaciones** del gate, no de sesión: toda re-corrida del gate sobre la
misma fase suma sobre ese mismo contador, sea cual sea el veredicto que dejó
la corrida anterior. El presupuesto por tier es **techo duro** sobre ese
acumulado: no hay excepción de ningún tipo — ni por criterio del panel ni por
autorización humana — que extienda ese tope. La única vía para seguir
trabajando una fase que llegó al tope
es dejarla `escalado` y resolver fuera del gate.

Tras cada iteración, decidir con esta regla, en este orden:

1. **Un hallazgo `alta`/`media` reaparece** — mismo lente **y** misma
   ubicación o misma causa raíz que un hallazgo que el refinamiento (paso 6)
   de una iteración anterior ya había dado por corregido — → veredicto
   `escalado` con causa `sin-convergencia`, sin gastar más iteraciones: el
   refinamiento ya lo intentó una vez sobre ese punto y no cerró. Esto
   incluye el caso en que la iteración *n+1* no introdujo hallazgos nuevos
   respecto de la iteración *n* — todos los que quedaron abiertos ya estaban
   ahí. Esto **no** es aprobar por agotamiento: se compara el conjunto de
   hallazgos entre iteraciones, no el saldo del presupuesto.
2. **La iteración no reduce el número de hallazgos `alta`/`media` abiertos**
   que encontró el panel (paso 4) respecto de los que encontró la iteración
   anterior — igual o más, nunca menos — → veredicto `escalado` con causa
   `sin-convergencia`, aunque los hallazgos concretos sean distintos y aunque
   el refinamiento de esta iteración los haya resuelto todos: el conteo sin
   bajar es la señal de que el artefacto no converge, no de que el panel
   encontró cosas nuevas legítimas.
3. **Quedan hallazgos `alta` o `media` sin resolver** (y no aplican 1 ni 2)
   → veredicto `escalado`, sin gastar más iteraciones. La `causa` que se
   persiste en el paso 8 depende de por qué quedaron sin resolver:
   `hallazgos-sin-resolver` si no se pudieron corregir dentro del alcance;
   `decision-humana` si lo que falta es una decisión que el gate no puede
   tomar por su cuenta.
4. **Todos los hallazgos `alta` y `media` quedaron resueltos** (y no
   aplican 1 ni 2):
   - Si al menos una corrección quedó **ambigua** — se aplicó sin certeza de
     que cierra el hallazgo por completo, o tocó una decisión de diseño no
     trivial que ningún hallazgo anterior había cubierto — y **queda
     presupuesto** de iteraciones en el tier → correr una tanda más de panel
     sobre el artefacto ya refinado, para verificar esa corrección concreta.
     Si esa tanda no encuentra nada nuevo `alta`/`media`, veredicto `refinado`.
     Si encuentra, volver a 6 — y re-evaluar 1-2 contra esta iteración en la
     siguiente vuelta.
   - Si **ninguna** corrección quedó ambigua (todas fueron mecánicas: mover,
     citar, renombrar, completar un campo, corregir una ruta) → veredicto
     `refinado` de una vez, **sin gastar una tanda de confirmación** aunque
     quede presupuesto (decisión de Julian, `0117-D4` de `.spec/
     MODELO-AGENTES.md`, 2026-09-20: una segunda tanda que no persigue nada es
     el mismo costo de panel completo por una confirmación que la propia
     corrección ya deja verificable a simple vista).
   - Presupuesto agotado → veredicto `refinado` en cualquiera de los dos casos
     de arriba. Un hallazgo corregido es un hallazgo cerrado: agotar el tope
     **después** de resolver todo no convierte el resultado en `escalado`.
5. **La primera tanda no encontró ningún hallazgo `alta`/`media`** → veredicto
   `aprobado`.

En tier `bajo` **y** `medio` (1 iteración cada uno, `0117-D4`) esto significa
lo mismo: una tanda de panel; si no hay nada, `aprobado`; si hubo hallazgos y
se corrigieron todos, `refinado` sin re-crítica (no hay presupuesto para una
segunda tanda aunque la corrección haya quedado ambigua — eso queda anotado en
bitácora como limitación, no como escalado); si alguno no se pudo corregir,
`escalado`. Solo tier `alto` (2 iteraciones) puede correr una segunda tanda de
verificación cuando una corrección quedó ambigua.

Un gate **nunca** devuelve `aprobado` ni `refinado` con hallazgos `alta` o
`media` abiertos, y **nunca** devuelve `escalado` por el solo hecho de haber
usado todo su presupuesto — `sin-convergencia` (1-2 arriba) escala por el
**patrón** entre iteraciones (reaparición o conteo estancado), no porque se
acabaron las iteraciones.

**Re-correr una fase `escalado` exige decisión humana registrada antes**
(`SKILL.md` § 1). El humano registra
`gates.<fase>.rehabilitado_por: {por, en, motivo}` en `_estado.yaml` y una
línea en `bitacora.md` antes de que el gate vuelva a correr sobre esa fase.
Esa re-corrida **no reinicia** `iteraciones` — sigue sumando sobre el mismo
contador acumulado (arriba) y sigue sujeta al mismo techo duro.

## 8. Persistir el veredicto

En `_estado.yaml > gates > <fase>`:

```yaml
gates:
  <fase>:
    veredicto: aprobado | refinado | escalado
    tier: bajo | medio | alto  # valor de `_estado.yaml > riesgo`; se persiste
                                # siempre, en cualquier modo
    causa: <solo si escalado>   # sin-gobernanza | decision-humana |
                                # hallazgos-sin-resolver | validacion-en-rojo |
                                # determinista-irresoluble | sin-convergencia
    iteraciones: <n>            # acumulado por fase entre invocaciones (§ 7);
                                # una re-corrida SUMA sobre este mismo valor,
                                # nunca lo reinicia
    rehabilitado_por:           # SOLO si esta corrida re-abre una fase cuyo
      por: <quién>              # último veredicto persistido era `escalado`
      en: <ISO-8601>            # (§ 7); ausente en cualquier otro caso
      motivo: <por qué se re-habilita>
    hallazgos:                  # SOLO los que quedaron sin resolver
      - severidad: alta
        lente: <nombre>
        donde: <sección o línea>
        problema: <una frase>
        correccion: <qué habría que cambiar>
    hallazgos_resueltos: <n>    # cuántos se corrigieron en este gate
    governance_consultada: si | parcial | no
    cobertura:                  # conteo mecánico, CA-12 — nunca la afirmación del modelo
                                  # "n/a" completo en plan/tasks (0117-D6: sin panel, nada que contar)
      lentes: "<activos>/<total>" | "n/a"  # `gate_coverage.py lenses --fase <fase> --tier <tier>`
      decisiones: "<n>/<n>" | "n/a"   # `gate_coverage.py decisions --fase <fase> [--artefacto <ruta>]`
                                        # "n/a" en plan/tasks/codigo (sin sección de
                                        # decisiones abiertas/resueltas); nunca un cero fabricado
    criticos:                   # qué subagente lanzaste por cada lente (paso 4)
      - {lente: <nombre>, subagente: <sdd-critico-*>, modelo: <el de su definición>, effort: <el de su definición>}
      # tier medio/bajo: una entrada por lente igual, aunque una sola llamada al
      # subagente haya cubierto todos los lentes activos (mismo `subagente` repetido
      # en cada entrada) — la lista sigue siendo por lente, no por llamada.
    refutador:                  # solo si corrió el paso 5 (tier alto); si no, []
      - {subagente: sdd-refutador, modelo: <el de su definición>, effort: <el de su definición>, hallazgos_evaluados: <n>}
```

Los hallazgos se guardan **con su forma completa**, no como texto suelto: quien
retome la unidad tiene que poder ver qué lente encontró qué y dónde, sin releer
la bitácora. `hallazgos_resueltos` es un contador — el detalle de lo corregido
va en la bitácora. `cobertura` se calcula corriendo los scripts, no
preguntándole al modelo cuántos lentes corrió o cuántas decisiones resolvió
(CA-12) — se corre una vez, después de que el paso 4 y el paso 6 ya
terminaron esta iteración, y su salida se copia literal a estos dos campos.
`criticos`/`refutador` son auditoría de modelo/effort —
mecanismo propio de este repo, ver `.spec/MODELO-AGENTES.md` — y no
hay que preguntarle a cada crítico qué modelo es: el `subagente` sale de a
quién lanzaste (tabla del paso 4), y su `modelo`/`effort` salen del frontmatter
de ese mismo archivo en `.claude/agents/` — o son `general-purpose` sin modelo
fijo si se usó la degradación del paso 4.

Y anexar a `bitacora.md` el detalle narrativo: qué lente encontró qué, qué se
corrigió, qué se descartó por refutación, qué quedó abierto.
