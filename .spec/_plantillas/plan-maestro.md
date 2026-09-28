# Plantilla — Plan maestro por objetivo (modo supervisado)

> Plantilla del protocolo SDD de este repo (`0109a`). Define el
> **mandato supervisado** cuando el objetivo requiere **varias unidades SDD** (D-1). Para
> una sola unidad usar `.spec/_plantillas/mandato.md` (D-15/D-16). Instanciar en
> `.spec/planes/<id>/plan-maestro.md` (D-17) — no editar esta plantilla en el sitio.
>
> **Formato de entrada en las secciones-registro** (S-22, `plan.md` de `0109a` §
> Decisiones de diseño): cada entrada es un bloque `### <clave>` seguido de bullets
> `- campo: valor`. La clave es una **fecha ISO-8601** en `## Mandato`,
> `## Aprobación`, `## Paradas`, `## Punto de retoma`, `## Registro de revisiones` y
> `## Revisión posterior`; es un **identificador** (`<id-unidad>-D<secuencia>`) en
> `## Registro de decisiones`. `## Delegaciones` usa filas de tabla
> `PD-n` / `CR-n` / `RS-n` bajo sus tres subsecciones, no el formato `### <clave>`.
>
> **Forma canónica del identificador de actor** (CA-01c): la cadena literal con la
> que un humano se identifica en este repo — el `dueño` de un `_estado.yaml` o el
> `user.name` de git, escrita **igual** en todos los campos de actor (`autor`,
> `lanza`, `quien`, `desbloqueo-quien`). Todo campo de actor se compara por
> **igualdad literal**, nunca por heurística.
>
> Validador: `.spec/scripts/validate_mandate.py --plan <id|ruta>`. Lista única de
> paradas: `.spec/PARADAS-SUPERVISADO.md` (no se reproduce aquí — se referencia).
> Documentación de cada artefacto y cada campo nuevo: `.spec/SUPERVISADO.md`.

- id: <id-del-plan>

## Objetivo y criterio de salida

<Qué objetivo persigue el plan y cómo se sabe, de forma verificable y sin
interpretar, que se alcanzó.>

## Unidades miembro

<Lista de unidades `.spec/units/<NNNN-slug>/` **amparadas** por este plan — el
plan **habilita** el modo `supervisado` o `desatendido` para ellas (mismo
mecanismo de amparo, distinta política de escalado — `.spec/MODELO-AGENTES.md`
§ Modo desatendido), no lo asigna: convertir una unidad a `supervisado` o a
`desatendido` exige una entrada `modo_conversion` en su `_estado.yaml`
registrada por un humano tras `research`/`spec` (ver `.spec/SUPERVISADO.md` §
"Modo por decisión humana y unidades en vuelo"); la unidad recibe la
referencia al plan al aprobarse (lo hace `0109b`, nunca esta plantilla).>

## Cadena de dependencias

Vigente: `<id> → <id> → <id>/<id> → <id>`

<Forma canónica de la cadena: la línea `Vigente:` de arriba, con los ids
separados por `→` (`/` entre ramas paralelas). El validador la lee de esa
línea, así que toda anotación —`(no amparada)`, la fecha de una revisión, una
remisión a otro artefacto— va **entre paréntesis**, donde no se confunde con
un id. Orden vigente de las unidades miembro. Toda unidad citada que **no** sea
miembro del plan (p. ej. una unidad ya cerrada de la que este plan hereda
decisiones, D-12) se marca con el literal `(no amparada)` a continuación de su
id, para distinguirla de las miembro (CA-01a). Esta cadena vive **una sola
vez**, aquí — ningún otro documento la transcribe (`pri-gob-fuente-verdad-unica`,
CA-06). Toda revisión de esta sección es una entrada nueva de
`## Registro de revisiones`, nunca una edición silenciosa.>

## Registro de revisiones

Una entrada fechada por cada cambio a `## Cadena de dependencias`: qué cambió
y por qué. El validador falla con `revision-sin-fecha` si una entrada carece
de fecha ISO-8601 y con `cadena-unidad-inexistente` si la cadena vigente cita
una unidad que no existe en `.spec/units/` (existencia, no pertenencia: una
unidad no amparada citada es válida, CA-01a).

### <fecha ISO-8601>
- que-cambio: <qué cambió en la cadena>
- motivo: <por qué cambió>

## Mandato

Lista **append-only**: cada renovación es una **entrada nueva**, nunca una
edición de la anterior — la firma cualquier humano con acceso al repo (D-19).
El **autor de la entrada vigente** (la de `inicio` más reciente cuyo `fin` no
se cumplió aún) es la referencia contra la que se comparan los desbloqueos de
parada (CA-10, `## Paradas`) y las revisiones de decisiones
(`## Revisión posterior`); tras una renovación, ese rol pasa al autor de la
entrada nueva. El `fin` es una fecha/hora ISO-8601 **obligatoria**: una
condición verificable puede acompañarla, nunca sustituirla. El validador falla
con `mandato-sin-fin` si la entrada vigente carece de fin y con
`mandato-entrada-sin-fecha` si alguna entrada carece de fecha.

### <fecha ISO-8601 de la entrada>
- autor: <forma canónica del actor — ver cabecera de este archivo>
- lanza: <actor previsto para lanzar la orquestación — opcional e informativo; no restringe quién puede lanzarla, D-19>
- inicio: <fecha/hora ISO-8601>
- fin: <fecha/hora ISO-8601 — obligatoria>

## Delegaciones

Tres categorías mutuamente excluyentes (D-13). Lo que no cae en
`### Pre-decididas` ni en `### Con criterio` cae en `### Reservadas`: ahí
decide el humano, siempre.

### Pre-decididas

| Id | Decisión | Pre-decisión | Impacto |
|---|---|---|---|
| PD-1 | <decisión ya tomada de antemano> | <la pre-decisión aplicable, literal> | <impacto si se aplica> |

### Con criterio

| Id | Decisión | Criterio a aplicar | Impacto |
|---|---|---|---|
| CR-1 | <decisión que se resuelve con una regla, no de antemano> | <criterio citable> | <impacto> |

### Reservadas

| Id | Decisión | Por qué se reserva | Impacto |
|---|---|---|---|
| RS-1 | <decisión que exige presencia humana> | <por qué ningún criterio la cubre> | <impacto de no decidirla> |

## Condiciones de parada

Referencia — **no transcripción** (`pri-gob-fuente-verdad-unica`,
`pol-ia-no-embeber-conocimiento`): `.spec/PARADAS-SUPERVISADO.md`. El validador
falla con `paradas-sin-referencia` si esta sección no contiene esa referencia.

## Paralelismo

- carriles: <carriles tocados durante la ventana del mandato>
- tope-worktrees: <entero ≤ 4 — D-11; el validador falla con `paralelismo-excede-tope` si N > 4>
- dueno-stack-vivo: <quién es el único dueño del stack vivo compartido durante la ventana>
- presupuesto-mcp: <presupuesto esperado del MCP de gobernanza para la ventana>
- conducta-presupuesto-agotado: parar — condición "MCP de gobernanza ausente o sin presupuesto" de `.spec/PARADAS-SUPERVISADO.md` (D-10); en ningún caso continuar un gate con gobernanza de memoria.

El validador falla con `paralelismo-sin-declarar` si falta cualquiera de estos
campos.

## Registro de decisiones

Fuente **única** de alternativas consideradas y de cómo revertir cada
decisión (CA-13): ninguna bitácora ni `plan.md` de unidad reproduce este
contenido — todos citan solo el identificador. La orquestación (`0109b`)
serializa la escritura de este registro.

Identificador: `<id-unidad>-D<secuencia>`, por unidad, secuencia sin huecos
(CA-01b). El validador falla con `decision-id-duplicado` si dos entradas
comparten identificador o si la secuencia de una unidad tiene huecos, y con
`decision-campo-ausente` si a una entrada le falta cualquier campo de abajo.

### <id-unidad>-D<secuencia>
- tipo: autonoma | heredada
- unidad: <unidad afectada>
- que-se-decidio: <qué se decidió>
- alternativas: <alternativas consideradas>
- criterio: <id de la pre-decisión/criterio del mandato citado — para `tipo: heredada` admite el literal `heredada:<id-unidad>`, que cuenta como presente>
- reversion: <cómo revertirla>
- revision: pendiente | aceptada | revertida
- revision-fecha: <ISO-8601>
- revision-quien: <forma canónica del actor>

El validador falla con `decision-fuera-de-delegacion` si una entrada
`autonoma` cita como criterio algo que no es una pre-decisión ni un criterio
del mandato, y con `decision-sobre-reservada` si cita una delegación de
`### Reservadas` (CA-12). Las entradas `heredada` están exentas de ambos
checks. Al cierre (`## Estado: cerrado`), el validador falla con
`cierre-con-pendientes` si alguna entrada `autonoma` sigue `pendiente` (las
`heredada` no bloquean, D-12), y con `revertida-sin-tarea` si una entrada
`revertida` no referencia la tarea nueva que la deshace.

## Paradas

Ownership y regla de colisión concurrente: `.spec/SUPERVISADO.md` § 3 "Escritor
único y colisión concurrente de `## Paradas` / `## Punto de retoma`" —
no se repite aquí.

Aplica a **toda** parada: una condición de `.spec/PARADAS-SUPERVISADO.md`, el
fin del mandato o una instrucción humana directa (CA-17). El validador falla
con `parada-abierta-sin-estado-parado` si existe una parada sin decisión de
desbloqueo y `## Estado` no es `parado`, y con `desbloqueo-no-autor` si quien
desbloquea no coincide, literal, con el `autor` de la entrada de `## Mandato`
vigente en el momento del desbloqueo.

### <fecha ISO-8601>
- unidad: <unidad afectada>
- disparador: <gate `escalado` con su fase, o la condición citada de `.spec/PARADAS-SUPERVISADO.md`>
- causa: <causa observada>
- invalida-el-objetivo: sí | no
- desbloqueo-quien: <forma canónica del actor — debe coincidir literal con el autor del mandato vigente>
- desbloqueo-fecha: <ISO-8601>
- desbloqueo-que: <qué decidió>

## Punto de retoma

Ownership y regla de colisión concurrente: `.spec/SUPERVISADO.md` § 3 "Escritor
único y colisión concurrente de `## Paradas` / `## Punto de retoma`" —
no se repite aquí.

Una entrada por **cada** vez que el mandato quedó parado (cualquier condición
de `## Paradas`, fin de mandato o instrucción humana). El validador falla con
`retoma-incompleta` si `## Estado` es `parado` y la entrada más reciente
carece de algún campo o es anterior a la parada abierta.

### <fecha ISO-8601>
- rama: <rama de git>
- commits: <hashes de los commits relevantes>
- validacion: <comandos corridos y su resultado literal>
- unidades-en-curso: <unidad y fase en la que quedó cada unidad amparada en curso>
- pasos: <pasos numerados>

## Estado

<Uno solo, del vocabulario cerrado (CA-01d): `borrador` | `aprobado` | `parado` | `cerrado`.>

## Instancia en curso

<Mientras una orquestación corre: identificador de sesión y fecha de arranque
(`0109b` CA-13). Se vacía al parar o al cerrar. Vacío en un plan recién
instanciado.>

## Aprobación

La aprobación cubre el **mandato y las delegaciones** — no cada tarea dentro
de ellas (`0109` § Resultado esperado). Una entrada nueva de
**reaprobación** cada vez que la cadena o las delegaciones cambian tras la
última aprobación. El validador falla con `aprobacion-ausente` (sin entrada),
`aprobacion-desactualizada` (revisiones/delegaciones posteriores sin
reaprobación) e `implementacion-sin-aprobacion` (unidad amparada en
`fase: implement` o posterior sin aprobación).

### <fecha ISO-8601>
- quien: <forma canónica del actor>
- hash: <hash sha256 del contenido aprobado — `validate_mandate.py --hash`>

## Revisión posterior

Por cada decisión revisada (CA-01g):

### <fecha ISO-8601>
- decision: <identificador de la decisión revisada>
- quien: <forma canónica del actor>
- resultado: aceptada | revertida
- tarea-de-reversion: <solo si `resultado: revertida` — la tarea nueva de la unidad correspondiente que la deshace>

## Ancla de aditividad

`mandate_anchor.py write` la completa al primer lanzamiento (unidad `0114`,
`CA-29`/`CA-30`): una sola línea `commit: <40 hex>`, el commit de `HEAD` en ese
momento. No se edita a mano.
