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
| `ingesta` | `ingerir_snapshot`: aplica el delta de un snapshot con la política del vínculo. |

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
  canónico. `descartar_superposicion` la borra al integrarse la unidad.
- `impacto_superposicion` da los símbolos que la unidad toca y lo que
  afectan aguas arriba con su riesgo: la comparación base contra snapshot
  que usa el gate de código.

## Consultas (`graph.query`)

| Verbo | Resultado |
|---|---|
| `resolve` | Símbolos con ese nombre o sufijo calificado (`.x`, `::x`, `/x`, `#x`). |
| `search` | Subcadena del nombre, filtrable por tipo; con `semantica` y un `CodificadorConsulta`, también k-NN. |
| `traverse` | BFS por relaciones con distancia y relación de llegada; aguas arriba lleva riesgo. |
| `related` | Clusters y procesos del símbolo, sus vecinos directos y sus compañeros de cluster. |

Riesgo aguas arriba, por símbolos afectados (a) y procesos tocados (p):
`critico` si a ≥ 50 o p ≥ 10; `alto` si a ≥ 20 o p ≥ 5; `medio` si a ≥ 5 o
p ≥ 2; si no, `bajo`. Todos los resultados de una misma consulta llevan el
mismo riesgo.

## Referencias entre repositorios

Un delta puede traer aristas hacia símbolos de otro repositorio del
workspace (las aristas `CROSS_*` de codebase-memory-mcp). El destino se
guarda como stub (solo id) en el grafo de origen. Como los ids son globales,
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

## Pendiente fuera de este paquete

- **Búsqueda semántica sin modelo en el servidor.** `ConsultaSearch` no
  lleva vector; hoy `semantica` solo funciona si el servidor tiene un
  `CodificadorConsulta` y, si no, cae a texto. Propuesta de contrato 1.1:
  `vector_b64` opcional en `ConsultaSearch`, calculado por el proxy.
- **Quién sube el delta canónico.** No hay tool para ello: propuesta de un
  `graph.index` de escritura, solo HTTP y solo actor de servicio (OIDC de
  GitHub Actions), que un job de CI con codebase-memory-mcp llame en cada
  push a la rama por defecto.
- **Ids de destino entre repositorios.** El proxy debe calcular el id del
  destino con el slug del repositorio destino tal como está vinculado en el
  workspace, para que la referencia resuelva.
- **Enlace con CA-NN.** Vincular símbolos tocados con criterios del spec
  depende de datos del motor DAG; queda para cuando exista.
