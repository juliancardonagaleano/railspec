# Grafo de código centralizado (railspec-graph)

`railspec/packages/railspec-graph` implementa el repositorio central de
conocimiento de código: `GraphStore` y `VectorStore` de `railspec-contracts`
sobre FalkorDB o Postgres, la capa que codebase-memory-mcp no trae (clusters, procesos,
impacto con riesgo, referencias entre repositorios) y el RAG sobre el grafo.
Depende solo de `railspec-contracts`; `railspec-server` lo cablea detrás de
`graph.query`.

## Capas

| Módulo | Qué hace |
|---|---|
| `motor` | Protocolo `MotorGrafo`: primitivas de un motor físico. Cambiar de motor es implementar solo esto. |
| `motor_falkordb` | `MotorFalkor`: un grafo físico de FalkorDB por nombre, vectores en el propio nodo (índice vectorial coseno, 768). |
| `motor_postgres` | `MotorPostgres`: el grafo en tablas `grafo_*` de la base del estado, sin servicio aparte ([abajo](#motor-en-postgres)). |
| `memoria` | `MotorMemoria`: doble de pruebas con la misma semántica. |
| `acceso` | Único módulo de acceso a datos: construye los nombres de grafo e impone el filtro de workspace. |
| `almacen` | `AlmacenGrafo` (`GraphStore`) y `AlmacenVectores` (`VectorStore`), con superposiciones por unidad y su limpieza (`limpiar_huerfanos`). |
| `analitica` | Clusters (Louvain con semilla fija), procesos desde puntos de entrada y nivel de riesgo. |
| `rag` | `RecuperadorContexto`: k-NN por repositorio visible y expansión por el grafo; devuelve referencias. |
| `ingesta` | `ingerir_snapshot`: aplica el delta de un snapshot con la política del vínculo y enlaza los `CA-NN` de las tareas completadas. |
| `indexado` | `IndexadorCanonico`: `graph.index`, el canónico por lotes desde CI (contrato 1.1), y el barrido de lo abandonado. |

## Motor en Postgres

`MotorPostgres` guarda el grafo en la misma base que el estado del servidor (`RAILSPEC_POSTGRES_URL`), sin FalkorDB ni
licencia aparte. Se activa con `RAILSPEC_GRAFO_POSTGRES=true`; exige `RAILSPEC_POSTGRES_URL` y excluye a
`RAILSPEC_FALKORDB_URL` (el servidor no arranca con las dos). No migra datos: un grafo nuevo empieza vacío y lo
llena el siguiente índice de CI.

Por qué existe: lo que el servidor le pide al grafo son primitivas simples (insertar y borrar símbolos y aristas,
vecinos a un salto, búsqueda por nombre) y el grafo de un repositorio de tamaño real cabe en decenas de MB; el impacto,
los clusters y los procesos ya se calculan en Python por encima de `MotorGrafo`. Un motor de grafos aparte pesaba más que
lo que hacía.

Tablas, todas en el esquema del estado y colgando de `grafo_grafos` con `ON DELETE CASCADE` (borrar un grafo es un `DELETE`):

| Tabla | Contenido |
|---|---|
| `grafo_grafos` | Una fila por grafo físico: su `Meta` en `json` y la columna `actualizado` (que `sellar` fija sin pisar el resto). |
| `grafo_simbolos` | `id, nombre, tipo, ruta, linea_inicio, linea_fin, sha256` y `embedding real[]` opcional. Nunca texto de código. |
| `grafo_aristas` | `origen, destino, relacion`, sin clave foránea: un extremo puede ser una referencia a otro repositorio. |
| `grafo_clusters`, `grafo_procesos`, `grafo_trazas` | La analítica y las trazas `CA-NN`. |

- El servidor crea las tablas al arrancar, bajo un bloqueo asesor, con RLS activa (Supabase expone por REST lo que no la tiene).
- Cada operación es una transacción propia y el pool no usa sentencias preparadas: sirve con el *pooler* de Supabase en modo sesión y en modo transacción. Los lotes grandes viajan como arreglos (`unnest`), un viaje por llamada.
- `knn` compara en Python los embeddings del grafo (coseno, fuerza bruta). El servidor hoy guarda pocos o ninguno; si hiciera falta búsqueda vectorial remota, el paso siguiente es pgvector sin cambiar el protocolo.
- `/healthz` y `railspec_sonda_ok` lo llaman `grafo` (no `falkordb`).
- El recorrido del impacto y de `callers`/`callees` pide las aristas por nivel (una consulta por vista y nivel, no por símbolo), para cualquier motor remoto.

Medido en un Postgres 16 local con un grafo sintético del tamaño del índice de este repositorio (11 936 símbolos, 31 165
aristas): índice completo de un golpe ≈ 10 s (la mayor parte es la analítica en Python, ≈ 7 s con el motor en memoria); el
impacto de una unidad de 50 símbolos (302 afectados) pasó de 446 consultas de aristas a 6, y tarda unos 80 ms sin latencia
de red. No se midió contra Supabase real (ver [deuda-tecnica.md](deuda-tecnica.md)).

Las pruebas del grafo corren contra él con `RAILSPEC_PRUEBAS_POSTGRES=<url>` (la misma variable que la suite del servidor).

## Espacios de nombres

- Grafo canónico: `railspec:<org>:<workspace>:<repositorio>` (`nombre_grafo`
  del contrato). Superposición de una unidad: el mismo nombre más
  `:u:<unidad>`, así que vive dentro del espacio de su repositorio.
- Solo `acceso` construye esos nombres; una prueba falla si otro módulo lo
  intenta. El resto del paquete recibe un `Espacio` atado a su grafo.
- `consultar` recibe los repositorios visibles que calculó el servidor
  (vínculos y roles del actor). Un visible de otro workspace u organización
  es `FueraDeWorkspace`; pedir un repositorio no visible es
  `RepositorioNoVisible`. Son errores, nunca resultados vacíos.
- Los ids de símbolo son globales (SHA-256 de repositorio, ruta, tipo y
  nombre), pero dos workspaces con el mismo slug de repositorio nunca se
  mezclan porque cada uno tiene su grafo físico.

## Canónico y superposición

- El canónico avanza por deltas incrementales del indexador y guarda su
  commit. Tras cada delta se recalculan clusters y procesos.
- La superposición de una unidad se reconstruye con cada snapshot, porque
  su delta siempre es base..árbol de trabajo. Sus borrados son lápidas que
  ocultan símbolos y aristas del canónico.
- Una consulta con `unidad` ve canónico más superposición; sin ella, solo
  canónico. El código de la unidad llega al canónico con el reindexado de CI
  del commit integrado, y mientras tanto la superposición sigue ahí (ver
  [Superposición de una unidad integrada](#superposición-de-una-unidad-integrada)).
- El servidor ingiere cada snapshot de `unit.report` con
  `AlmacenGrafo.ingerir` (es decir, `ingerir_snapshot`): las exclusiones del
  vínculo se vuelven a aplicar en el servidor. Un fallo del grafo se registra
  y no tumba el reporte, que ya está guardado.

## Superposición de una unidad integrada

`unit.integrate` trae desde 1.4 `commit_integrado`: el commit resultante en la
rama destino. (La consola lo prellena con la punta del clon canónico y no deja
integrar sin elegir; ver `consola.md`, «Aprobaciones e integración».) El proxy lo
manda (el que da el arnés o, si no, la punta de la rama por defecto del remoto tras un `git fetch`) y el servidor, en vez de
descartar la superposición del repositorio primario, la **retiene**:
`AlmacenGrafo.retener_superposicion` guarda `integrado` en su meta y la
superposición sigue visible a las consultas con esa `unidad` (incluidos
`impact` y `trace`) hasta que el canónico alcance ese commit. Sin
`commit_integrado`, o si el canónico ya está en él o ya lo cubrió un índice
anterior (ver [Commits cubiertos](#commits-cubiertos-por-el-canónico)), se
borra al integrar, y también se borran al integrar las de los repositorios
transversales: el contrato trae un solo commit. Al retenerla se sella
`retenido_en` en su meta, de donde cuenta su [plazo
máximo](#retenidas-varadas-plazo-aviso-y-listado).

`IndexadorCanonico` retira las retenidas
(`AlmacenGrafo.retirar_superposiciones`) justo después de avanzar el canónico.
El servidor no tiene git y no puede ordenar un commit de otro: lo que cubre
un índice lo declara CI.

**Con `commits_cubiertos` (contrato 1.5).** `graph.index` trae la lista de
commits de la rama por defecto que el índice incorpora, los más recientes
primero y a lo sumo 1000: `reindexar.py` la calcula con
`git rev-list --first-parent` (`commit_anterior..commit` en un delta; los
últimos 1000 de `commit` en un índice completo; las puntas de la rama, que es
lo que `commit_integrado` toma, son primeros padres). Se retiran las
retenidas cuyo `integrado` es el commit del índice o está en la lista, y nada
más: el índice completo ya no arrasa con todas. Así una unidad integrada en un
commit que CI nunca indexó (corrida cancelada o saltada: GitHub descarta las
intermedias y el siguiente delta parte de un commit que el canónico no tiene)
se retira con el índice que lo alcanza, y una integrada en un commit posterior
al índice espera a su propio índice en vez de perder su código antes de
tiempo. Una lista vacía declara que no se cubre más que el commit del índice.
En un índice por lotes la lista viaja igual en todos; vale la del lote que
aplica el commit.

**Sin `commits_cubiertos` (cliente 1.4, o `--sin-cobertura`).** Vale la regla
de 1.4: las integradas en el commit del índice y, si el índice es completo
(sin `commit_anterior`), todas las retenidas.

En los dos casos el reenvío de un commit ya aplicado (tras una caída entre
avanzar el canónico y retirar) repite el retiro de lo que cubre.

Las superposiciones de unidades en curso no llevan `integrado` y este retiro
no las toca; si nadie las retoma, las borra el barrido de [lo
abandonado](#lo-abandonado-superposiciones-y-preparaciones). Límites
conocidos:

- Si `commit_integrado` no llega nunca al canónico (otra rama que la por
  defecto, el merge no se empujó, o un force-push lo borró de la historia) o
  queda fuera de los 1000 commits que declara un índice completo, la
  superposición queda retenida hasta que caduca por su plazo (ver [Retenidas
  varadas](#retenidas-varadas-plazo-aviso-y-listado)); el lanzamiento manual de
  `railspec-reindexar` con `retirar_todas` (un índice completo sin cobertura,
  que retira todas las retenidas) sigue valiendo para no esperar.
- Al desplegar, un servidor 1.4 rechaza `commits_cubiertos` y la versión 1.5
  con 422; `reindexar.py` lo detecta y sube el índice sin la lista (regla de
  1.4) mientras no se actualice el servidor. La respuesta de `graph.index` no
  gana campos y habla la versión del cliente (`version_contrato` 1.4 a un
  cliente 1.4), así que sigue validándola. Una réplica anterior no
  lee la meta de una superposición retenida (falla solo la consulta con esa
  `unidad`); las metas del canónico no cambian de forma, y el sello de
  actividad de las demás (`actualizado`, ver abajo) y todo lo que se añadió
  en 1.10 (`cubiertos`, la reconciliación de contenido, el resumen de la
  preparación y `retenido_en`) van como propiedades aparte del nodo `Meta`, no
  dentro de su json, para que una réplica anterior las siga leyendo.

### Commits cubiertos por el canónico

El canónico recuerda los commits que cubren sus últimos índices
(`Meta.cubiertos`): la unión de `commit` y `commits_cubiertos` de cada
índice aplicado, los más recientes primero y a lo sumo 1000
(`MAX_COMMITS_CUBIERTOS`). Un delta añade los suyos a los anteriores; un índice
completo **reemplaza** la lista (CI declara de nuevo los últimos 1000). Un
cliente 1.4, sin `commits_cubiertos`, cubre al menos su `commit`. Con ella
`retener_superposicion` ya no compara solo con el commit del canónico: una
unidad integrada en un commit que el canónico ya pasó (el arnés entregó un commit
viejo, o CI indexó por delante) se retira al integrar en vez de quedar varada.

### Retenidas varadas: plazo, aviso y listado

Una retenida que ningún índice cubre (el commit nunca llegó al canónico) ya no
queda para siempre:

- **Plazo máximo.** `RAILSPEC_GRAFO_RETENIDAS_DIAS` (30 por defecto; `0` = no
  caducan nunca; admite fracciones). `limpiar_huerfanos` borra las retenidas
  cuyo `retenido_en` es más viejo que el plazo y las reporta aparte
  (`Huerfanos.retenidas`). Cuenta desde que se retuvo, no desde el último
  snapshot de la unidad. Una retenida de antes de esta versión no tiene
  `retenido_en`: el primer barrido que la ve lo sella y el plazo cuenta desde ahí.
  No toca el canónico, las trazas `CA-NN` ni las superposiciones en curso (sin
  `integrado`), que siguen con su plazo de abandono.
- **Aviso antes de borrar.** Una consulta `graph.query` con `unidad` cuya
  superposición lleva retenida más de la mitad del plazo añade a `avisos`:
  la unidad, el commit en que se integró y cuántos días faltan para
  retirarla (o que ya superó el plazo y la retira el próximo barrido). Solo
  avisa de la unidad consultada, así que el contexto de spec, plan y tasks de
  esa unidad (`grafo_avisos`) también lo lleva.
- **Listarlas.** `AlmacenGrafo.retenidas(alcance)` devuelve, por unidad,
  `Retenida(unidad, integrado, desde, vence)` (`vence` es `None` sin plazo o sin
  `desde`). La consola lo expone en `GET /orgs/{org}/workspaces/{ws}/grafo/retenidas`
  ([consola.md](consola.md)), solo lectura con rol `lector`.

El barrido corre cuando llega un índice (ver abajo): un repositorio que no
recibe `graph.index` no barre sus retenidas; el aviso sí sale en cada consulta.

## Lo abandonado: superposiciones y preparaciones

Dos cosas se quedan a medias cuando nadie vuelve a tocarlas: la superposición
de una unidad que se abandonó, y el grafo de preparación `...:i:<commit>` de un
índice que nunca completó (una corrida de CI que falló entre lotes).
`AlmacenGrafo.limpiar_huerfanos(alcance, superposicion, indexado, retenida)` las borra
(y, con `retenida`, las retenidas varadas):

| Qué | Se borra cuando | Variable | Defecto |
|---|---|---|---|
| Superposición sin `integrado` | Sin snapshot nuevo desde hace N días | `RAILSPEC_GRAFO_SUPERPOSICION_DIAS` | `30` (la retención por defecto de los snapshots) |
| Preparación `:i:<commit>` | Sin lotes nuevos desde hace N horas | `RAILSPEC_GRAFO_INDEXADO_HORAS` | `24` |
| Superposición retenida (con `integrado`) | N días desde que se retuvo sin que un índice cubra su commit | `RAILSPEC_GRAFO_RETENIDAS_DIAS` | `30` |

- Cada una lleva en su `Meta` el instante de su última actividad
  (`actualizado`, UTC): lo sella cada snapshot que reconstruye la
  superposición y cada lote **nuevo** de una preparación; reenviar un lote ya
  recibido no cuenta.
- Un grafo sin sello (de antes de esta versión) no se borra por un tiempo que
  nadie vio pasar: el primer barrido lo sella (sin tocar el resto de su meta) y
  el plazo cuenta desde ahí.
- Las superposiciones retenidas (con `integrado`) no cuentan por el plazo de
  abandono: salen cuando un índice las cubre (`commits_cubiertos`), con
  `retirar_todas` o, si ninguno las cubre, por su propio plazo (`retenido_en`;
  ver arriba). El canónico y las trazas `CA-NN` nunca se tocan, ni nada de
  otros repositorios.
- `IndexadorCanonico` barre el repositorio del índice tras cada índice aplicado
  (también al reenviar uno ya aplicado) y al llegar el **primer** lote de un
  commit nuevo, así que un repositorio cuyo índice nunca completa tampoco
  acumula preparaciones. Un repositorio que no recibe `graph.index` no se
  barre: no hay otro disparador.
- Lo borrado se registra (INFO, logger `railspec.graph.indexado`: superposiciones,
  retenidas que ningún índice cubrió e índices sin completar, por repositorio).
  La respuesta de `graph.index` no cambia, y si el barrido falla (FalkorDB caído) se registra con su traza y el
  índice sigue su curso.
- Las variables se leen al construir `IndexadorCanonico` (el servidor lo hace al
  arrancar); `0` desactiva ese lado y admiten fracciones (`0.5`). Un valor que
  no es un número, negativo, `nan`, `inf` o desmesurado impide arrancar, con
  el nombre de la variable. En las pruebas, `AlmacenGrafo(acceso, reloj=...)`
  inyecta el reloj y `IndexadorCanonico(..., superposicion_dias=, indexado_horas=, retenidas_dias=)`
  los plazos (`AlmacenGrafo(..., retenidas_dias=)`, el que usa para avisar y listar).
- El barrido no toma lock. Si una unidad vuelve a la vida justo cuando se borra
  su superposición, el siguiente snapshot la reconstruye completa (siempre es
  base..árbol de trabajo); si llega un lote de un índice tras 24 horas de
  silencio justo en ese instante, el índice no completa y CI lo repite.

## Comparación base contra snapshot (impacto)

`AlmacenGrafo.impacto(consulta, visibles, profundidad=3)` devuelve un
`Impacto`:

- `tocados`: lo que la superposición cambia (símbolos con upsert y lápidas);
  `refs_tocados` los resuelve, un borrado contra el canónico.
- `afectados`: aguas arriba de los tocados por las relaciones de dependencia,
  sin los propios tocados, con distancia y relación de llegada.
- `procesos`: procesos que pasan por tocados o afectados.
- `riesgo`: la regla de abajo sobre afectados y procesos, así que un cambio
  sin nada aguas arriba que toca muchos procesos ya no sale `bajo`.

El gate de código lo pone en el material de los críticos (riesgo, cuentas y
hasta 100 afectados). Informa, no decide: no genera hallazgos deterministas,
porque un hallazgo determinista se salta el panel y un riesgo alto no tiene
"arreglo" que el implementador pueda aplicar. `impacto_superposicion` queda
como forma anterior `(tocados, afectados)`.

La comparación es contra el canónico vigente, no contra el commit base de la
unidad: si el canónico avanzó, el impacto se mide sobre el código de hoy.

## Trazabilidad CA-NN

Cada `unit.report` de una orden `implementar` con snapshot enlaza los
criterios de las tareas que completa con los símbolos que **ese** snapshot
cambió respecto del anterior de la misma unidad (sha256 distinto, símbolo
nuevo o lápida nueva). Como el delta del snapshot es siempre base..árbol de
trabajo, lo que un grupo anterior ya había cambiado no se atribuye al nuevo.

- Las trazas `(unidad, criterio, símbolo)` viven en un grafo aparte por
  repositorio, `railspec:<org>:<workspace>:<repositorio>:t`, que sobrevive a
  reindexados y al retiro de la superposición; se borra con el
  repositorio. Se acumulan: un reporte posterior no borra las anteriores.
- Las exclusiones del vínculo se aplican antes de enlazar.
- El gate de código lista por criterio cuántos símbolos tiene enlazados, o
  "sin símbolos enlazados", para el crítico de cumplimiento.
- Un símbolo trazado que ya no existe (borrado, o de una superposición
  descartada que nunca llegó al canónico) no aparece al consultar.

## Consultas (`graph.query`)

| Verbo | Resultado |
|---|---|
| `resolve` | Símbolos con ese nombre o sufijo calificado (`.x`, `::x`, `/x`, `#x`). |
| `search` | Subcadena del nombre, filtrable por tipo; con `semantica`, también k-NN con el `vector_b64` que calcula el proxy (1.1) o, si falta, con un `CodificadorConsulta` opcional de `AlmacenGrafo` (el servidor no cablea ninguno). |
| `traverse` | BFS por relaciones con distancia y relación de llegada; aguas arriba lleva riesgo. |
| `related` | Clusters y procesos del símbolo, sus vecinos directos y sus compañeros de cluster. |
| `impact` (1.4) | Exige `unidad`. Tocados con `distancia` 0 y afectados aguas arriba con `distancia` ≥ 1 y `relacion`, todos con el `riesgo` de `Impacto`. `profundidad` 1 a 5 (3). |
| `trace` (1.4) | Con `criterio` (exige `unidad`): `RefSimbolo` de los símbolos enlazados, por ruta y nombre. Con `simbolo`: `RefCriterio` de cada unidad y criterio que lo tocó (filtrado por `unidad` si viene). |

Ejemplos para la consola (navegador del grafo y trazabilidad):

```json
{"alcance": {"org": "acme", "workspace": "certificados"}, "unidad": "0001-emitir-pdf",
 "consulta": {"verbo": "impact", "profundidad": 3}}
{"alcance": {"org": "acme", "workspace": "certificados"}, "unidad": "0001-emitir-pdf",
 "consulta": {"verbo": "trace", "criterio": "CA-01"}}
{"alcance": {"org": "acme", "workspace": "certificados"},
 "consulta": {"verbo": "trace", "simbolo": "<sha256 del símbolo>"}}
```

Riesgo aguas arriba, por símbolos afectados (a) y procesos tocados (p):
`critico` si a ≥ 50 o p ≥ 10; `alto` si a ≥ 20 o p ≥ 5; `medio` si a ≥ 5 o
p ≥ 2; si no, `bajo`. Todos los resultados de una misma consulta llevan el
mismo riesgo.

### Frescura y avisos (aditivo, contrato 1.9)

`graph.query` dice qué tan fiable es lo que devuelve, porque hasta ahora un
repositorio sin índice respondía vacío igual que uno sin coincidencias, y un
canónico de hace un mes igual que uno de hoy. La salida añade, con valor por
defecto (un cliente anterior no los usa):

- `frescura`: por cada repositorio consultado, también los que no tienen
  índice y por eso no están en `commits`: `indexado` (falso si el canónico
  nunca recibió un índice), `commit` y `indexado_en` del canónico (no los de
  la superposición de la unidad, que sí lleva `commits`) y `desactualizado`.
- `avisos`: frases legibles. Hoy cuatro casos: «sin índice canónico» (con la
  aclaración de que un vacío no prueba que el código no exista; si la consulta
  trae `unidad` con superposición, dice que solo se ve esa), «el índice
  canónico se aplicó hace N h, más que el plazo», «el contenido del índice
  canónico no coincide con el que calculó CI» (ver [Reconciliación del
  contenido](#reconciliación-del-contenido-con-el-repositorio-contrato-110)) y
  «la superposición de la unidad sigue retenida» (ver [Retenidas
  varadas](#retenidas-varadas-plazo-aviso-y-listado)).

`indexado_en` sale de `Meta.actualizado` del canónico, que `graph.index` (y
`aplicar_delta` sin unidad) fija al aplicar el índice; el barrido de lo
abandonado no toca el canónico. Un canónico indexado antes de que se guardara
no tiene instante: no se marca `desactualizado` hasta su siguiente índice.

El plazo es `RAILSPEC_GRAFO_FRESCURA_HORAS` (72 por defecto; admite
fracciones; `0` no avisa nunca). Lo lee `AlmacenGrafo` al construirse
(`frescura_horas=` lo sustituye) y un valor inválido impide arrancar, como las
otras dos variables `RAILSPEC_GRAFO_*`. Es un aviso por tiempo, no por
contenido: el servidor no tiene git y no sabe si la rama por defecto avanzó. Esa
comparación la hace el proxy local, que añade su aviso a los del servidor
([proxy-local.md](proxy-local.md#consultas-al-grafo-y-sus-avisos)).

La rebanada de grafo que el motor pone en el contexto de las órdenes de spec,
plan y tasks usa esta misma consulta (ver [motor.md](motor.md#recorrido)) y
repite `frescura` y `avisos` como `grafo_frescura` y `grafo_avisos`.

## Indexado del canónico (`graph.index`)

Un job de GitHub Actions corre codebase-memory-mcp en cada push a la rama
por defecto y llama `graph.index` (solo HTTP, solo actor de servicio) con el
delta por lotes. `IndexadorCanonico.recibir(entrada, vinculo)`:

- rechaza (`IndiceRechazado`) otro repositorio, una rama que no es la por
  defecto del vínculo o lotes de un mismo commit con distinto total o base;
- con `commit_anterior` aplica un delta incremental y exige que el canónico
  esté en ese commit (`IndiceDesfasado` si no); sin él, el índice es
  completo y reemplaza al canónico;
- acumula los lotes en un grafo de preparación `...:i:<commit>` (sobrevive a
  reinicios y a varias réplicas) y solo al llegar el último aplica todo al
  canónico, recalcula la analítica y borra la preparación;
- reenviar un lote o un commit ya aplicado es idempotente;
- vuelve a aplicar las exclusiones del vínculo;
- sella en el canónico el instante del índice aplicado (`Meta.actualizado`),
  del que sale la frescura de `graph.query`;
- tras avanzar el canónico retira las superposiciones de unidades integradas
  que ese commit cubre, según los `commits_cubiertos` que declara CI (ver
  arriba), y recuerda esos commits en `Meta.cubiertos`;
- con `resumen` (1.10, solo en el último lote) compara el contenido del canónico
  con el repositorio (ver la sección siguiente);
- borra lo abandonado del repositorio (ver [Lo
  abandonado](#lo-abandonado-superposiciones-y-preparaciones)).

### Reconciliación del contenido con el repositorio (contrato 1.10)

Los controles de commit y de reloj no ven un delta que perdió un símbolo o una
exclusión mal aplicada: el canónico queda en el commit correcto pero distinto del
código. El job de CI, que sí tiene el repositorio, calcula un `resumen` del
índice completo del commit (por archivo: número de símbolos y una huella de sus
`id` y `sha256`; el algoritmo vive en `railspec.contracts.resumen`, compartido por
CI y servidor) y lo manda en el **último lote**. El servidor no puede aplicarlo
hasta que llegan todos los lotes y estos pueden llegar desordenados, así que lo
guarda en la meta de la preparación (`Meta.resumen`) hasta aplicar el índice.

Al aplicar el índice, `IndexadorCanonico` resume lo que quedó en el canónico y lo
compara ruta por ruta con el de CI (`divergencias`: una ruta diverge si falta de
un lado o cambia su número de símbolos o su huella), **después de quitar de los dos
lados las rutas que el vínculo excluye**: el servidor ya filtró el delta, así que
el resumen de CI trae rutas que el canónico nunca tuvo. El resultado se guarda en
la meta del canónico:

| Campo de `Meta` | Qué dice |
|---|---|
| `contenido_verificado` | `True` coincide; `False` hay rutas que difieren; `None` el índice no trajo resumen (cliente anterior a 1.10) o la comparación no pudo hacerse. |
| `divergencias_total` | Cuántas rutas difieren. |
| `rutas_divergentes` | Las primeras 20, en orden alfabético. |

- Un índice sin resumen deja `None` y no arrastra el resultado del anterior.
- Una divergencia **nunca hace fallar el índice**: se aplica, se registra
  (`log.warning`, logger `railspec.graph.indexado`, con el total y las primeras
  rutas) y `graph.query` la avisa en `avisos` y la expone en `frescura`
  (`contenido_verificado`, `divergencias_total`, `rutas_divergentes`). El aviso
  dice cuántas rutas y las primeras, y que un índice `completo`
  (`railspec-reindexar`) lo corrige. El contexto de spec, plan y tasks lo repite
  en `grafo_avisos` y `grafo_frescura`.
- Un delta incremental con resumen se compara contra el canónico entero (anterior
  más delta), así que una divergencia que no se corrige persiste en los índices
  siguientes hasta un índice completo.
- Persistencia: en FalkorDB, `cubiertos`, el resultado de la comparación, el
  resumen de la preparación y `retenido_en` van en una propiedad aparte del nodo
  `Meta` (`ext`, con el commit al que pertenecen), igual que `actualizado`, para que
  una réplica anterior siga leyendo el json. Una réplica anterior que avanza el
  canónico no actualiza `ext`: al leerla, el commit ya no coincide y se ignora.

## Referencias entre repositorios

Un delta puede traer aristas hacia símbolos de otro repositorio del
workspace (las aristas `CROSS_*` de codebase-memory-mcp, marcadas con
`repositorio_destino` desde 1.1). El proxy y CI calculan el id del destino
con `id_simbolo` y el slug del vínculo destino. El destino se guarda como stub (solo id) en el grafo de origen. Como los ids son globales,
el recorrido las sigue solo por la unión de los repositorios visibles; si el
repositorio destino no es visible, la referencia se corta al resolver.

## Política de código propietario

- El grafo nunca guarda texto de código, en ningún nivel: por símbolo solo
  id, nombre, tipo, ruta, rango de líneas y sha256 (`PROPIEDADES_SIMBOLO`),
  más el embedding. Una prueba lo verifica sobre cada motor.
- `ingerir_snapshot` rechaza un snapshot de otro repositorio o workspace, o
  que declare un nivel más abierto que el del vínculo, y vuelve a aplicar
  las exclusiones del vínculo en el servidor.
- El servidor nunca calcula embeddings de código: guarda y consulta los que
  lleguen (int8, 768, `Embedding.vector_b64`) y la búsqueda semántica usa el
  vector de la consulta que calcula el proxy (1.1). Hoy el indexador local
  (`codebase-memory-mcp` 0.11) no expone sus vectores, así que los deltas
  viajan sin embeddings y la búsqueda semántica espera a un codificador local
  ([proxy-local.md](proxy-local.md#pendiente)).
- El RAG devuelve referencias tipadas, nunca texto; el arnés las resuelve
  contra su clon y el chat, con `code.read`.

## Pruebas

```
python -m pip install -e railspec/packages/railspec-graph[falkordb,test]
python -m pytest railspec/packages/railspec-graph            # doble en memoria
docker run -d -p 6379:6379 falkordb/falkordb
RAILSPEC_FALKORDB_URL=redis://localhost:6379 python -m pytest railspec/packages/railspec-graph
```

Con `RAILSPEC_FALKORDB_URL` cada prueba corre también contra FalkorDB real,
en una organización propia que se borra al terminar.
