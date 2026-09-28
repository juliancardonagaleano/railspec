# Plantilla — Mandato supervisado de unidad aislada

> Define el
> **mandato supervisado** cuando el objeto es **una sola unidad SDD** (D-15), no un
> plan por objetivo (para eso, `.spec/_plantillas/plan-maestro.md`). Se instancia
> como archivo propio dentro de `.spec/units/<NNNN-slug>/`, con el nombre
> `mandato.md` **salvo que `plan.md` fije otro nombre** (D-16); `_estado.yaml`
> referencia esta ruta en su campo `mandato`.
>
> Comparte con la plantilla del plan todas las anclas **salvo**
> `## Unidades miembro`, `## Cadena de dependencias` y `## Registro de
> revisiones` (esas tres son propias de una agrupación de varias unidades) —
> en su lugar, esta plantilla declara `## Unidad amparada`. El resto del
> contenido (`## Mandato`, `## Delegaciones`, `## Registro de decisiones`,
> `## Paradas`, `## Punto de retoma`, `## Paralelismo`,
> `## Revisión posterior`) es el **mismo concepto que en el plan**, redactado
> una sola vez y adaptado a la forma de unidad — no una copia literal.
>
> **Formato de entrada en las secciones-registro** (S-22, `plan.md` de `0109a` §
> Decisiones de diseño): cada entrada es un bloque `### <clave>` seguido de
> bullets `- campo: valor`. La clave es una **fecha ISO-8601** en `## Mandato`,
> `## Aprobación`, `## Paradas`, `## Punto de retoma` y `## Revisión
> posterior`; es un **identificador** (`<id-unidad>-D<secuencia>`) en
> `## Registro de decisiones`. `## Delegaciones` usa filas de tabla
> `PD-n` / `CR-n` / `RS-n` bajo sus tres subsecciones.
>
> **Forma canónica del identificador de actor** (CA-01c, heredada de CA-02):
> la cadena literal con la que un humano se identifica en este repo — el
> `dueño` de un `_estado.yaml` o el `user.name` de git, escrita **igual** en
> todos los campos de actor. Se compara por **igualdad literal**.
>
> **Relación entre fase, avance y estado del mandato** (CA-02): esta unidad
> tiene tres campos con nombre propio que no son intercambiables —
> **fase de la unidad** (`fase` de `_estado.yaml`: `research…done`),
> **avance de la unidad** (`estado` de `_estado.yaml`: `en-progreso` |
> `bloqueado` | `completado`) y **estado del mandato** (`## Estado` de este
> archivo, vocabulario de CA-01d). `fase: done` y `estado: completado` son
> **la misma condición** — el protocolo SDD los escribe juntos al cerrar, y el
> validador trata cualquiera de los dos como "unidad cerrada". Son
> **inválidas**, con código `mandato-estado-inconsistente` (mutuamente
> excluyentes entre sí: a lo sumo una dispara, porque el mandato tiene un solo
> estado):
>
> - (i) mandato `cerrado` con unidad **no** cerrada;
> - (ii) unidad en `fase: implement` o cerrada con mandato `borrador`;
> - (iii) mandato `parado` con unidad cerrada.
>
> Cualquier otra combinación de los tres campos no se valida.
>
> Validador: `.spec/scripts/validate_mandate.py --unidad <ruta>`. Lista única
> de paradas: `.spec/PARADAS-SUPERVISADO.md` (no se reproduce — se referencia).
> Documentación de cada artefacto y cada campo nuevo: `.spec/SUPERVISADO.md`.
>
> **Visibilidad agregada del estado del mandato (operacional, introducido por la unidad
> `0139`).** El vocabulario cerrado `## Estado` (CA-01d: `borrador` | `aprobado` | `parado` |
> `cerrado`) **no se amplía** — no introduce un valor `parcial` ni `parado-parcial`. La
> visibilidad agregada que el conductor rinde al reportar el estado del mandato se escribe
> en el campo `unidades-en-curso` de cada entrada de `## Punto de retoma` (esta plantilla
> tiene una sola unidad amparada — la entrada lleva el estado por fase de esa unidad:
> `en-verde` | `esperando-A-parada` | `parada` | `no-iniciada`). El conductor rinde dos
> vistas simultáneas cuando la parada disparada es unit-level: **vista del mandato
> completo** + **vista por-unidad**. Para paradas mandato-nivel, la vista del mandato
> colapsa a una sola línea ("mandato `parado`"). Ver `.agents/skills/sdd-supervisado/SKILL.md`
> § 6.A para la regla completa.

## Objetivo y criterio de salida

<Qué objetivo persigue esta unidad bajo mandato supervisado y cómo se sabe, de
forma verificable y sin interpretar, que se alcanzó.>

## Unidad amparada

<Id y slug de la unidad amparada por este mandato — la misma en la que vive
este archivo, `.spec/units/<NNNN-slug>/`. Este mandato **habilita** el modo
`supervisado` para esa unidad, no lo asigna: convertirla exige una entrada
`modo_conversion` en su `_estado.yaml` registrada por un humano tras
`research`/`spec` (ver `.spec/SUPERVISADO.md` § "Modo por decisión humana y
unidades en vuelo").>

## Mandato

Lista **append-only**: cada renovación es una **entrada nueva**, nunca una
edición de la anterior — la firma cualquier humano con acceso al repo (D-19).
El **autor de la entrada vigente** es la referencia contra la que se comparan
los desbloqueos de parada (CA-10) y las revisiones de decisiones
(`## Revisión posterior`); tras una renovación, ese rol pasa al autor de la
entrada nueva. El `fin` es una fecha/hora ISO-8601 **obligatoria**. El
validador falla con `mandato-sin-fin` si la entrada vigente carece de fin y
con `mandato-entrada-sin-fecha` si alguna entrada carece de fecha.

### <fecha ISO-8601 de la entrada>
- autor: <forma canónica del actor — ver cabecera de este archivo>
- lanza: <actor previsto para lanzar la orquestación — opcional e informativo; no restringe quién puede lanzarla, D-19>
- inicio: <fecha/hora ISO-8601>
- fin: <fecha/hora ISO-8601 — obligatoria>

## Delegaciones

Tres categorías mutuamente excluyentes (D-13). Lo que no cae en
`### Pre-decididas` ni en `### Con criterio` cae en `### Reservadas`.

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
- tope-worktrees: <entero — nunca mayor que el tope por unidad de `sdd-implementar` (4, S-15); el validador falla con `paralelismo-excede-tope` si N > 4>
- dueno-stack-vivo: <quién es el único dueño del stack vivo compartido durante la ventana>
- presupuesto-mcp: <presupuesto esperado del MCP de gobernanza para la ventana>
- conducta-presupuesto-agotado: parar — condición "MCP de gobernanza ausente o sin presupuesto" de `.spec/PARADAS-SUPERVISADO.md` (D-10); en ningún caso continuar un gate con gobernanza de memoria.

El validador falla con `paralelismo-sin-declarar` si falta cualquiera de estos
campos.

## Registro de decisiones

Fuente **única** de alternativas consideradas y de cómo revertir cada
decisión (CA-13): la bitácora y `plan.md` de esta unidad citan cada decisión
por su identificador, sin reproducirla.

Identificador: `<id-unidad>-D<secuencia>`, secuencia sin huecos (CA-01b). El
validador falla con `decision-id-duplicado` si dos entradas comparten
identificador o hay huecos en la secuencia, y con `decision-campo-ausente` si
a una entrada le falta cualquier campo de abajo.

### <id-unidad>-D<secuencia>
- tipo: autonoma | heredada
- unidad: <esta unidad amparada>
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
checks. Al cierre (`## Estado: cerrado`), falla con `cierre-con-pendientes`
si alguna entrada `autonoma` sigue `pendiente` (las `heredada` no bloquean), y
con `revertida-sin-tarea` si una entrada `revertida` no referencia la tarea
nueva que la deshace.

## Paradas

Ownership y regla de colisión concurrente: `.spec/SUPERVISADO.md` § 3 "Escritor
único y colisión concurrente de `## Paradas` / `## Punto de retoma`" —
no se repite aquí.

Aplica a **toda** parada: una condición de `.spec/PARADAS-SUPERVISADO.md`, el
fin del mandato o una instrucción humana directa. El validador falla con
`parada-abierta-sin-estado-parado` si existe una parada sin decisión de
desbloqueo y `## Estado` no es `parado`, y con `desbloqueo-no-autor` si quien
desbloquea no coincide, literal, con el `autor` de la entrada de `## Mandato`
vigente en el momento del desbloqueo.

### <fecha ISO-8601>
- unidad: <esta unidad amparada>
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

Una entrada por **cada** vez que el mandato quedó parado. El validador falla
con `retoma-incompleta` si `## Estado` es `parado` y la entrada más reciente
carece de algún campo o es anterior a la parada abierta.

### <fecha ISO-8601>
- rama: <rama de git>
- commits: <hashes de los commits relevantes>
- validacion: <comandos corridos y su resultado literal>
- unidades-en-curso: <fase en la que quedó esta unidad amparada>
- pasos: <pasos numerados>

## Estado

<Uno solo, del vocabulario cerrado (CA-01d): `borrador` | `aprobado` | `parado` | `cerrado`.>

## Instancia en curso

<Mientras una orquestación corre: identificador de sesión y fecha de arranque
(`0109b` CA-13). Se vacía al parar o al cerrar. Vacío en un mandato recién
instanciado.>

## Aprobación

La aprobación cubre el **mandato y las delegaciones** — no cada tarea dentro
de ellas. Una entrada nueva de **reaprobación** cada vez que las delegaciones
cambian tras la última aprobación. A diferencia del plan, cada entrada declara
además la **fase SDD** en que se aprobó y **qué artefactos tenía a la vista**
el humano (CA-09). El validador falla con `aprobacion-ausente` (sin entrada),
`aprobacion-desactualizada` (delegaciones posteriores sin reaprobación),
`implementacion-sin-aprobacion` (unidad en `fase: implement` o posterior sin
aprobación) y `aprobacion-sin-contexto` (entrada sin fase o sin artefactos a
la vista).

### <fecha ISO-8601>
- quien: <forma canónica del actor>
- hash: <hash sha256 del contenido aprobado — `validate_mandate.py --hash`>
- fase: <fase en que se aprobó>
- artefactos: ninguno | spec | spec+plan

## Revisión posterior

Por cada decisión revisada:

### <fecha ISO-8601>
- decision: <identificador de la decisión revisada>
- quien: <forma canónica del actor>
- resultado: aceptada | revertida
- tarea-de-reversion: <solo si `resultado: revertida` — la tarea nueva de esta unidad que la deshace>
