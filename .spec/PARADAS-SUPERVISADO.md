# Condiciones de parada indelegables — modo supervisado

> **Lista única** de condiciones de parada del modo supervisado. Ambas plantillas de mandato
> (`.spec/_plantillas/mandato.md`, única plantilla vigente post-U-0009) la referencian por
> ruta en `## Condiciones de parada` y **no la reproducen**; `.spec/scripts/validate_mandate.py`
> falla con `paradas-sin-referencia` si un mandato no contiene esa referencia.
>
> **Dos clases de condición.** Las condiciones **9, 10, 11 y 12** son condiciones propias del
> kit, vinculantes por remisión desde `.agents/skills/sdd-supervisado/SKILL.md` (§ 5 y § 6 remiten
> a ellas): se leen al tier de esa skill, que es quien las hace vinculantes; este archivo es su
> registro operativo, no una fuente normativa autónoma. Su origen es "kit SDD de este repo /
> decisión de la unidad 0027" y su texto vive aquí y en ningún otro lugar del kit. Las demás
> condiciones citan su origen —un identificador de gobernanza o una `ruta:línea` del kit— **sin
> transcribirlo**.
>
> **Por qué esa transcripción no infringe `pol-ia-no-embeber-conocimiento`.** La policy vincula
> a las **skills**: les prohíbe embeber conocimiento de arquitectura **recuperable vía el MCP de
> gobernanza**. Este archivo no es una skill —se alinea con la policy por ser parte del kit que
> `sdd-supervisado` lee— y las condiciones 9-12 **no están en el catálogo** del MCP: no son
> conocimiento recuperable, son reglas operativas de este repo. Lo que la policy sí prohíbe
> —transcribir texto de `pri-ia-humano-decide`, `pol-gob-no-creacion-directa`,
> `pol-ia-retrieval-mcp` o cualquier otro identificador resoluble— este archivo lo cumple: esas
> condiciones solo citan.
>
> **Clases de ámbito de la parada (operacional, introducido por la unidad `0139`).** Cada
> condición de esta lista pertenece a una de dos clases que el conductor distingue antes de
> decidir qué se congela — el vocabulario "parada" se conserva único (decisión del humano-
> decisor, `0139` § Preguntas abiertas, respuesta 5); la diferencia es el **ámbito** del
> efecto:
>
> - **Unit-level (regla por defecto).** La condición afecta a **una sola unidad amparada** y,
>   por la cadena `## Cadena de dependencias` del mandato (la línea `Vigente:`, fuente única
>   `pri-gob-fuente-verdad-unica`), a sus **dependientes directos** (unidades cuyo id aparece
>   aguas abajo del id afectado). Las unidades independientes y las posteriores (no-
>   dependientes) **siguen**. El `## Estado` del mandato permanece en `aprobado`; el freeze
>   vive en `## Punto de retoma.unidades-en-curso` (estado `esperando-A-parada` /
>   `parada` / `en-verde` / `no-iniciada` por unidad). Las tres reglas adicionales rigen aquí
>   del mismo modo: **visibilidad agregada** (vista de mandato + vista por-unidad, § 6.A);
>   **notificación diferenciada por severidad** (alta/crítica notifica en el momento,
>   media/baja se acumula al resumen, § 6.B); **reanudación determinista** (re-fase desde el
>   checkpoint congelado, no reinicio, § 6.C).
> - **Mandato-nivel (caso particular).** La condición exige **parar el mandato entero** — el
>   motivo es la **propagación**, no la **decisión**: o afecta a la infraestructura
>   compartida (stack vivo, knowledge-router, PCE, base viva), o es una instrucción humana
>   directa, un gate `escalado` de carrera completa, o el fin del mandato. `## Estado` del
>   mandato se eleva a `parado`; el conductor trata las unidades amparadas como **todas**
>   congeladas. Las tres reglas adicionales rigen aquí en su forma **mandato-nivel**:
>   visibilidad agregada degenera en una sola línea ("mandato `parado`"); notificación
>   diferenciada usa la severidad original sin acumularla al resumen (es una sola parada);
>   reanudación requiere una **entrada nueva de `## Mandato`** (no basta el desbloqueo de
>   una sola entrada de `## Paradas`).
>
> **Mapeo clase ↔ condición.** Las condiciones **1, 2, 3, 4, 5, 7 y 14** son **unit-level**
> por defecto — afectan a una unidad amparada y, por la cadena vigente, a sus dependientes
> directos. Las condiciones **8, 9, 10, 11, 12, 13 y el `fin` del mandato (regla § 7) y la
> "instrucción humana directa" (cap. de § 6)** son **mandato-nivel** — exigen congelar toda la
> orquestación. La condición **6** (fin del mandato) es **mandato-nivel** por definición
> (cierre del mandato). Este mapeo es la **convención operativa**; no se enumera por
> condición abajo — la lista numerada conserva su numeración y redacción intactas.
>
> Ante cualquiera de estas condiciones: **parar**, registrar la parada en `## Paradas` del
> mandato y esperar la decisión de un humano. Qué hace la orquestación con la parada en vivo es
> de la skill `sdd-supervisado`; esta lista solo dice **cuáles** son las condiciones.

## Origen: Principio `pri-ia-humano-decide`

1. **Cambio destructivo en lectura amplia** — cualquier operación irreversible o
   de alcance amplio sobre lo ya versionado o sobre un estado vivo compartido —
   **salvo** que sea una operación concreta, pre-decidida y **nombrada en el
   mandato**, con su dry-run cuando aplique (excepción D-8). Kit:
   `.agents/skills/sdd-implementar/SKILL.md:63` (la regla de que un cambio
   destructivo reportado sin ejecutar se confirma con el humano aunque el
   paquete esté aprobado).
2. **Decisión con alternativas válidas fuera de la delegación** — ninguna de
   las categorías de `## Delegaciones` (`### Pre-decididas`, `### Con
   criterio`) la cubre.
3. **Divergencia entre plan y realidad** — sin calificador: cualquier
   divergencia entre lo que el mandato describe y lo que se observa para.

## Origen: diseño del modo supervisado (`0109a`)

4. **Decisión de `### Reservadas`** — aunque una entrada de `### Con criterio`
   parezca cubrirla (D-13): lo reservado nunca se decide aplicando un
   criterio, se para.
5. **Tarea reservada a presencia humana** — declarada como tal en el mandato.
6. **Fin del mandato** — la entrada vigente de `## Mandato` llegó a su `fin`.

## Origen: kit SDD (`ruta:línea`)

7. **Gate `escalado`** — cualquier fase cuyo gate (`sdd-gate`) devuelva
   `escalado`. `.agents/skills/sdd-gate/SKILL.md:190`, `.spec/README.md:117`.
8. **MCP de gobernanza ausente o sin presupuesto** — se trata como "sin
   gobernanza": parar y decirlo, nunca seguir con conocimiento de
   entrenamiento ni con una copia local (D-10).
   `.agents/skills/sdd-gate/SKILL.md:100-101`, `.spec/README.md:118`,
   `pol-ia-retrieval-mcp`.

## Origen: kit SDD de este repo / decisión de la unidad 0027

9. **Denegación del harness** — una denegación del clasificador de permisos
   **nunca** se puentea citando el mandato: el mandato delega checkpoints
   SDD, no anula denegaciones del harness.
10. **Único dueño del stack vivo** — nadie más despliega ni redespliega el
    stack compartido (compose, `knowledge-router`, `sdd`, base viva) mientras el mandato
    está en marcha.
11. **Congelamiento por renombres** — un renombre que toque el árbol exige
    todos los carriles parados.
12. **No reconstruir la PCE con corridas en vuelo** — el knowledge-router/
    corpus es recurso compartido: no se reconstruye su grafo mientras haya
    una corrida en vuelo.

## Origen: `pol-gob-no-creacion-directa`

13. **Cambio que crea o modifica un artefacto de gobernanza** — se para y lo
    tramita un humano con las skills de authoring (`adr-crear`,
    `politica-crear`, `politica-modificar`), nunca a mano y nunca bajo el
    amparo de este mandato.

## Origen: diseño del modo supervisado (orquestación, `0109b` CA-08)

14. **Fallo del validador de mandatos** (exit ≠ 0) — `0109b` CA-08; lista
    humana sin acción: `aprobacion-desactualizada`, `retoma-incompleta`,
    `cierre-con-pendientes`.

## Paradas tipificadas — modo desatendido (0031, 2026-09-23; renombradas al
agrupador único del mandato el 2026-09-26)

Adicionales a las 14 paradas existentes:

### `plan-incompleto` (abort)

**Cuándo aplica:** una unidad amparada por el mandato falla de modo que afecta materialmente a las demás unidades amparadas (e.g., un cambio de mandato invalida el resto de las unidades; una falla de gobernanza bloquea el MCP para todo lo que sigue).

**Acción:** detener el resto de unidades amparadas por el mandato; reportar al humano con lista de unidades ejecutadas + unidad fallida + razón; preservar estado de cada `_estado.yaml`.

### `unidad-amparada-fallida` (auto-deferred)

**Cuándo aplica:** una unidad tiene un gate que escala a `escalado` (cualquier hallazgo `alta` o `media` sin resolver).

**Acción:** cerrar el gate como `escalado: auto-deferred` en `_estado.yaml > gates.<fase>.veredicto` con causa `hallazgos-sin-resolver`; agregar entrada en bitácora; **continuar con la siguiente unidad amparada por el mismo mandato**. El gate escalado se acumula como trabajo futuro en una unidad posterior (la numeración tentativa se publica en el reporte final).
