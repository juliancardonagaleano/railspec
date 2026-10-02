# Grafo de código centralizado (railspec-graph)

`railspec/packages/railspec-graph` implementa el repositorio central de
conocimiento de código: `GraphStore` y `VectorStore` de `railspec-contracts`
sobre FalkorDB, la capa que codebase-memory-mcp no trae (clusters, procesos,
impacto con riesgo, referencias entre repositorios) y el RAG sobre el grafo.
Depende solo de `railspec-contracts`; `railspec-server` lo cablea detrás de
`graph.query`.

## Capas

| Módulo | Qué hace |
|---|---|
| `motor` | Protocolo `MotorGrafo`: primitivas de un motor físico. Cambiar de motor es implementar solo esto. |
| `motor_falkordb` | `MotorFalkor`: un grafo físico de FalkorDB por nombre, vectores en el propio nodo (índice vectorial coseno, 768). |
| `memoria` | `MotorMemoria`: doble de pruebas con la misma semántica. |
| `acceso` | Único módulo de acceso a datos: construye los nombres de grafo e impone el filtro de workspace. |
| `almacen` | `AlmacenGrafo` (`GraphStore`) y `AlmacenVectores` (`VectorStore`), con superposiciones por unidad. |
| `analitica` | Clusters (Louvain con semilla fija), procesos desde puntos de entrada y nivel de riesgo. |
| `rag` | `RecuperadorContexto`: k-NN por repositorio visible y expansión por el grafo; devuelve referencias. |
| `ingesta` | `ingerir_snapshot`: aplica el delta de un snapshot con la política del vínculo y enlaza los `CA-NN` de las tareas completadas. |
| `indexado` | `IndexadorCanonico`: `graph.index`, el canónico por lotes desde CI (contrato 1.1). |

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
rama destino. El proxy lo manda (el que da el arnés o, si no, la punta de la
rama por defecto del remoto tras un `git fetch`) y el servidor, en vez de
descartar la superposición del repositorio primario, la **retiene**:
`AlmacenGrafo.retener_superposicion` guarda `integrado` en su meta y la
superposición sigue visible a las consultas con esa `unidad` (incluidos
`impact` y `trace`) hasta que el canónico alcance ese commit. Sin
`commit_integrado`, o si el canónico ya está en él, se borra al integrar, y
también se borran al integrar las de los repositorios transversales: el
contrato trae un solo commit.

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

Las superposiciones de unidades en curso no llevan `integrado` y no se
tocan. Límites conocidos:

- Si `commit_integrado` no llega nunca al canónico (otra rama que la por
  defecto, el merge no se empujó, o un force-push lo borró de la historia) o
  queda fuera de los 1000 commits que declara un índice completo, la
  superposición queda retenida. Se limpia con el lanzamiento manual de
  `railspec-reindexar` con `retirar_todas`: un índice completo sin
  cobertura, que retira todas las retenidas, también las que ningún índice
  cubre.
- Una unidad integrada cuando el canónico ya pasó de su commit (el arnés
  entrega un commit viejo) no se retira por sí sola: el servidor no guarda
  qué commits cubrió ya. El proxy manda la punta de la rama tras un
  `git fetch`, así que no ocurre en el camino normal.
- Al desplegar, un servidor 1.4 rechaza `commits_cubiertos` y la versión 1.5
  con 422; `reindexar.py` lo detecta y sube el índice sin la lista (regla de
  1.4) mientras no se actualice el servidor. La respuesta de `graph.index` no
  gana campos y habla la versión del cliente (`version_contrato` 1.4 a un
  cliente 1.4), así que sigue validándola. Una réplica anterior no
  lee la meta de una superposición retenida (falla solo la consulta con esa
  `unidad`); las metas del canónico no cambian de forma.

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
| `search` | Subcadena del nombre, filtrable por tipo; con `semantica`, también k-NN con el `vector_b64` que calcula el proxy (1.1) o, si falta, con un `CodificadorConsulta` del servidor. |
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
- tras avanzar el canónico retira las superposiciones de unidades integradas
  que ese commit cubre, según los `commits_cubiertos` que declara CI (ver
  arriba).

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
- Los embeddings llegan calculados en local (int8, 768); el servidor solo
  los guarda y consulta.
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
