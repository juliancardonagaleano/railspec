# Mandato: los modos supervisado y desatendido

Desde el contrato 1.11 un modo sin checkpoints humanos descansa en un **mandato**: una aprobación única,
acotada y con fecha de caducidad que ampara varias unidades. Antes `supervisado` y `desatendido` solo
omitían los checkpoints de spec, plan y paquete; no había nada que aprobar, límite que respetar ni parada
tipificada, y cualquier `plan` valía. Ahora no se puede arrancar ni convertir una unidad a esos modos sin
un mandato aprobado, y el motor lo vuelve a comprobar en cada paso.

La semántica viene de las skills `sdd-supervisado` y `sdd-desatendido` del kit
(`.spec/PARADAS-SUPERVISADO.md`): un plan aprobado una sola vez, paradas tipificadas, decisiones delegadas
con registro y, en desatendido, `auto-deferred`. Lo que cambia es dónde vive: el servidor lo impone con el
estado, no una conversación.

## Qué es y qué no es

- Es una **autorización acotada**: dice en qué repositorios y rutas pueden trabajar las unidades, cuánto
  pueden gastar entre todas, cuántas son, cuánto dura y qué decisiones pueden tomar solas.
- **No** apaga los gates. Un gate rojo (`escalado`) sigue escalando siempre; el mandato solo decide qué se
  detiene cuando eso pasa.
- **No** lo aprueba nadie más que una persona, desde la consola, sobre el contenido exacto que vio.

## Ciclo de vida

| Estado | Qué significa | Cómo se sale |
| --- | --- | --- |
| `propuesto` | Redactado o editado. Nadie lo aprobó, o cambió después de aprobarse. No ampara trabajo. | `mandate.approve` |
| `aprobado` | Una persona aprobó este contenido y la aprobación no ha caducado. | caduca, se detiene o se revoca |
| `parado` | Una condición de parada lo detuvo (ver [Paradas](#paradas-tipificadas)). Las unidades se retienen. | aprobarlo de nuevo (`mandate.approve`) |
| `revocado` | Una persona lo cerró. No se reabre; se redacta otro. | (final) |

- **Id.** Es el `plan` de las unidades que ampara: `unit.start` con `plan: "pdf-a"` pertenece al mandato
  `pdf-a` del mismo workspace.
- **Aprobar** (`mandate.approve`) solo la hace una persona, solo por HTTP desde la consola (canal
  `consola`; el MCP del arnés no la expone) y lleva la `huella` del contenido que vio: SHA-256 del contenido
  canónico (`MandatoContenido.huella()`). Si el contenido cambió entre medias, se rechaza con
  `conflicto-version`. Cada aprobación se añade a `aprobaciones` y fija la caducidad
  (`ahora + limites.vigencia_horas`).
- **Renovar** es aprobar otra vez: sirve para un mandato que caducó, que se detuvo o que está por caducar. La
  historia de aprobaciones solo crece.
- **Editar** (`mandate.propose` con `version_vista`) lo devuelve a `propuesto`, también si estaba aprobado o
  parado, y borra la parada: lo nuevo hay que aprobarlo. Las unidades que corrían se retienen hasta entonces.
- **Redactarlo** lo puede hacer una persona o un agente (el arnés propone, la persona aprueba).
- **Revocar** (`mandate.revoke`) lo puede pedir una persona por la consola o por el MCP: detener es siempre
  más seguro que seguir.
- **Caducidad.** `aprobado` sigue siendo el estado mientras no se mire; la primera vez que el motor lo
  comprueba y ve el reloj pasado lo deja `parado` con `mandato-caducado`.

## Límites (`LimitesMandato`)

| Campo | Defecto | Qué impone |
| --- | --- | --- |
| `repositorios` | (obligatorio) | Una unidad solo puede vincular repositorios de esta lista. |
| `max_unidades` | 5 (1 a 50) | `unit.start` rechaza la unidad que pasa de ahí (`fuera-de-alcance`). |
| `rutas_permitidas` | `[]` | Con alguna, un snapshot que toca una ruta que no casa con ninguna detiene la unidad (`fuera-de-alcance`). Vacía: sin restricción adicional a la del plan de cada unidad. Las órdenes la llevan en `mandato.rutas_permitidas` para que el proxy lo compruebe antes de reportar. |
| `presupuesto` | sin tope | Total del mandato (suma del consumo de sus unidades). Al alcanzarse detiene el mandato entero. **Un mandato `desatendido` exige al menos un tope**. Es aparte de los topes por unidad, fase y mes. |
| `reintentos_parada` | 0 (0 a 3) | Veces que el servidor reintenta, sin preguntar, una orden que el arnés reportó `fallido`. `bloqueado` no se reintenta nunca. |
| `vigencia_horas` | 24 (1 a 168) | Cuánto vale cada aprobación. |

## Supervisado y desatendido

| | `supervisado` | `desatendido` |
| --- | --- | --- |
| Checkpoints de spec, plan y paquete | no se abren: los cubre el mandato | igual |
| Gate `escalado` | **congela el mandato entero** (`gate-escalado`) y la unidad espera su decisión | **difiere la unidad** (`unidad-amparada-fallida`): sigue esperando en su checkpoint y las demás unidades siguen |
| Causa común al escalar (`sin-gobernanza`, `error-proveedor`, presupuesto del mandato o del mes) | congela el mandato (`gate-escalado`) | aborta el mandato (`plan-incompleto`) |
| Presupuesto del mandato al escalar | `presupuesto-mandato` | `presupuesto-mandato` |
| Orden `fallido` | reintenta hasta `reintentos_parada`; luego parada de la unidad (`reintentos-agotados`) | igual |
| Orden `bloqueado` | parada de la unidad (`decision-reservada`) | igual |
| Presupuesto total | detiene el mandato (`presupuesto-mandato`) | igual |
| Salida del alcance | parada de la unidad (`fuera-de-alcance`) | igual |

Un mandato y sus unidades tienen **el mismo modo**: no se puede convertir una unidad a `desatendido` bajo un
mandato `supervisado`. Bajar la autonomía (de `supervisado` o `desatendido` a `interactivo` o
`semi-autonomo`) se admite en cualquier momento, también con el mandato caducado o revocado, para no dejar una
unidad sin salida; subirla solo tras research o tras el checkpoint del spec, con el mandato vigente.

## Paradas tipificadas

Cada parada tiene una causa (`CausaParada`) y un ámbito. Las de **mandato** se guardan en `Mandato.parada`,
dejan el mandato `parado` y retienen todas sus unidades; las de **unidad** viven en el checkpoint pendiente de
esa unidad (`Checkpoint.causa_parada`) y las demás siguen.

| Causa | Ámbito | Cuándo | Cómo se reanuda |
| --- | --- | --- | --- |
| `gate-escalado` | mandato (supervisado) o unidad | un gate escaló | la persona resuelve el checkpoint de la unidad y renueva el mandato |
| `plan-incompleto` | mandato | desatendido: un gate escaló por una causa que afecta a todas las unidades | renovar el mandato |
| `presupuesto-mandato` | mandato | el consumo total alcanzó `limites.presupuesto` | editar el tope (vuelve a `propuesto`) y aprobar |
| `mandato-caducado` | mandato | pasó la vigencia | renovar |
| `mandato-revocado` | mandato | una persona lo revocó | (final) |
| `unidad-amparada-fallida` | unidad | desatendido: el gate escaló y la unidad se difirió (`ResultadoGate.diferido`) | la persona rehabilita el gate o pide cambios |
| `fuera-de-alcance` | unidad | un snapshot tocó rutas no permitidas | la persona decide (reintentar con indicaciones o rechazar) |
| `decision-reservada` | unidad | el arnés reportó `bloqueado`: necesita una decisión que el mandato no delega | la persona decide |
| `reintentos-agotados` | unidad | la orden falló y no quedan reintentos delegados | la persona decide |

**Mientras el mandato no ampara trabajo** (no vigente, parado, propuesto o presupuesto alcanzado) el motor no
avanza ninguna de sus unidades: acepta los reportes y los guarda como entradas pendientes, pero no corre
gates ni emite órdenes; `unit.advance` responde `mandato-parado` (con la causa y el detalle) y el arnés se
detiene. Al renovar el mandato las entradas pendientes se entregan y todo sigue donde estaba.

## Decisiones delegadas

El mandato lista **delegaciones** (`D-1`, `D-2`…) de tres tipos:

- `pre-decidida`: ya está decidido; se aplica.
- `con-criterio`: el mandato da un criterio y quien ejecuta decide dentro de él.
- `reservada`: nunca se decide por criterio; si aparece, se detiene y decide una persona.

Cada orden de una unidad con mandato lleva `mandato` (delegaciones, rutas permitidas, reintentos y caducidad).
Si el arnés decide algo apoyándose en una delegación, lo reporta en `unit.report` (`decisiones`: delegación,
qué decidió, alternativas, cómo revertirlo). El servidor lo registra en `EstadoUnidad.decisiones` con el id
`DD-n`, lo audita (`decision-delegada`) y lo deja **pendiente de revisión**. Una persona lo acepta o lo revierte
desde la consola (`mandate.review`, auditado como `revision-decision`); revertir no deshace el código, deja
constancia y es la persona quien lo revierte.

- Una decisión que cita una delegación `reservada` o que no existe **se rechaza** (`fuera-de-alcance`): el
  arnés debe reportar la orden como `bloqueado` y la decide una persona.
- Los reintentos automáticos también se registran, con la delegación `reintento`.

## Auditoría

`cambio-mandato` (propuesto, aprobado, parado, revocado; `detalle.accion` y `detalle.mandato`),
`decision-delegada` y `revision-decision`. Cada parada de unidad queda además en su checkpoint.

## Superficies

| Tool | Quién | Superficie |
| --- | --- | --- |
| `mandate.propose` | persona o agente | MCP y HTTP |
| `mandate.approve` | solo persona, canal `consola` | solo HTTP |
| `mandate.revoke` | solo persona | MCP y HTTP |
| `mandate.review` | solo persona, canal `consola` | solo HTTP |
| `mandate.get`, `mandate.list` | lectura | MCP y HTTP |

El proxy local no expone `mandate.approve` ni `mandate.review` al arnés: aprobar es un acto humano en la
consola. Despliegue: el proxy primero, el servidor después (el servidor 1.11 puede responder
`mandato-parado` a `unit.advance`, que un proxy anterior no entiende).

## Defectos elegidos

Estos defectos los fijó el hilo que lo implementó, sin pregunta previa, para quedar del lado seguro; son
campos o constantes que se cambian sin tocar el diseño:

- Los modos no se pueden usar sin mandato aprobado, y `supervisado` sigue siendo el modo con mandato más
  conservador (el gate rojo congela todo).
- Sin reintentos (`reintentos_parada: 0`) y con vigencia de 24 horas.
- Un mandato `desatendido` sin tope de presupuesto total no existe.
- Aprobar solo desde la consola; detener también por el MCP.
- Un mandato y sus unidades comparten modo.
- Una unidad de un mandato antiguo (`supervisado`/`desatendido` con `plan` sin mandato, anterior a 1.11) queda
  retenida hasta que se redacte y apruebe un mandato con ese id (`plan`), o se baje su autonomía.

## Pendiente

Ver [deuda-tecnica.md](deuda-tecnica.md#mandato-notificaciones-y-lo-que-el-servidor-no-impone).
