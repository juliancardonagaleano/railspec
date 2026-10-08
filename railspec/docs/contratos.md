# Contratos de Railspec v1 (fase 1)

Esta fase fija los mensajes que cruzan las fronteras entre el motor remoto
(`railspec-server`), el proxy local (`railspec-local`), los arneses y la
consola web (`railspec-console`). Los hilos de las fases 2 (motor), 3 (proxy y
adaptadores) y 4 (repositorio central) trabajan contra estos contratos sin
esperarse entre sí.

## Fuente de verdad y artefactos

| Qué | Dónde |
|---|---|
| Modelos Pydantic (fuente de verdad) | `packages/railspec-contracts/src/railspec/contracts/` |
| JSON Schema 2020-12 generados | `schemas/v1/*.schema.json` |
| Manifiesto del registro de tools | `schemas/v1/tools.json` |
| Ejemplo válido por mensaje | `examples/v1/*.json` |
| Pruebas de deriva e invariantes | `tests/test_contratos.py` |

Regenerar tras cambiar un modelo:

```
python -m railspec.contracts.esquemas railspec/schemas/v1
python railspec/tests/fabricas.py
python -m pytest railspec
```

Las pruebas fallan si los esquemas o los ejemplos en disco no coinciden con
los modelos, así que un cambio de contrato siempre viaja con su esquema.

## Mensajes

| Mensaje | Módulo | Quién lo escribe | Quién lo lee |
|---|---|---|---|
| Orden de trabajo (`redactar`, `refinar`, `implementar`, `validar`) | `orden` | Motor | Arnés vía proxy |
| Reporte de orden | `reporte` | Proxy | Motor |
| Snapshot | `snapshot` | Proxy | Motor, grafo central |
| Evento de sincronización | `eventos` | Ambos | Ambos; la consola lo consume en vivo (SSE) |
| Estado de unidad (remoto) | `estado` | Solo el motor | Proxy (espejo), consola |
| Estado local | `estado` | Proxy | Proxy |
| Insumo `railspec.insumo/v1` | `insumo` | Chat de la consola | `unit.start`, `railspec insumo pull` |
| Paquete de unidad `railspec.unidad/v1` | `portabilidad` | `unit.export`, importador del kit | `unit.import` |
| Respuesta del chat y veredicto del gate de salida | `chat` | Agente del chat y gate | Consola |
| Conversación y mensaje del chat | `chat` | Servidor | Consola |
| Entidades de configuración, catálogo, telemetría y auditoría | `repositorio` | Servidor y consola | Todos |

## Tools

Un único registro (`tools.py`) define cada tool una vez, con efecto, rol
mínimo y superficies. El servidor genera desde él el servidor MCP y la API
HTTP de la consola (R1).

| Tool | Efecto | MCP | HTTP | Chat |
|---|---|---|---|---|
| `unit.start` | escritura | sí | sí | no |
| `unit.advance` | escritura | sí | no | no |
| `unit.report` | escritura | sí | no | no |
| `unit.approve` | escritura | sí | sí | no |
| `unit.integrate` | escritura | sí | sí | no |
| `unit.set_mode` | escritura | sí (solo humano) | sí (solo humano) | no |
| `mandate.propose` | escritura | sí | sí | no |
| `mandate.approve` | escritura | no | sí (solo humano, canal consola) | no |
| `mandate.revoke` | escritura | sí (solo humano) | sí (solo humano) | no |
| `mandate.review` | escritura | no | sí (solo humano, canal consola) | no |
| `mandate.get`, `mandate.list` | lectura | sí | sí | no |
| `unit.import` | escritura | sí (solo humano) | sí (solo humano) | no |
| `unit.export` | lectura | sí | sí | no |
| `unit.status` | lectura | sí | sí | sí |
| `unit.list` | lectura | sí | sí | sí |
| `sync.pull` | lectura | sí | no | no |
| `sync.push` | escritura | sí | no | no |
| `graph.query` | lectura | sí | sí | sí |
| `graph.index` | escritura | no | sí (solo servicio) | no |
| `code.read` | lectura | no | no | sí (solo) |
| `insumo.get` | lectura | sí | sí | sí |
| `telemetry.query` | lectura | no | sí | sí |

- **Nombres (desde 1.3).** El nombre canónico lleva punto (`unit.start`) y
  es el de la API HTTP (`POST /v1/tools/unit.start`), el registro y la
  documentación. Toda superficie MCP, la del servidor y la del proxy, expone
  el alias `nombre_mcp` (`unit_start`), porque varios arneses rechazan el
  punto. El alias es determinista (punto → guion bajo), único y viaja en el
  manifiesto como `mcp_name`; `resolver_tool` acepta las dos formas.
- El actor nunca viaja en la entrada: el servidor lo deriva del token
  (GitHub OAuth para personas, OIDC de GitHub Actions para CI) y de la
  superficie.
- Los campos de salida con texto de código llevan
  `"x-railspec-clase": "codigo_interno"` en el esquema; `ToolDef.campos_codigo_interno()`
  los lista para que el gate de salida calcule huellas. Hoy solo
  `code.read → fragmentos[].texto`.
- `ToolDef.tipos_actor` restringe quién puede llamar una tool; `graph.index`
  solo acepta identidades de servicio (OIDC de GitHub Actions) y es la única
  tool que las admite: el valor por defecto es `{humano, agente}` y un
  `ToolDef` con `servicio` en otra tool no valida. Ajuste restrictivo sin
  cambio de versión: antes el defecto incluía `servicio` y cualquier workflow
  de GitHub Actions leía las tools de lectura de cualquier organización.
- Errores de negocio: `ErrorTool` con un `CodigoError` común.
- Bucle del arnés: `unit.start` → `unit.advance` → ejecutar la orden →
  `unit.report` → `unit.advance`… El arnés nunca decide fase ni gate; un
  reporte que no corresponde a la orden vigente se rechaza.

## Reglas que el contrato impone

Estas reglas son validadores de los modelos, no convenciones; cada una tiene
su prueba negativa.

- **Política de código propietario.** Un snapshot `restringido` no lleva diff
  ni fragmentos; en `interno` los fragmentos solo pueden ser de archivos
  tocados; un snapshot con secretos detectados es inválido; `solo-hashes` no
  lleva delta. Los embeddings llegan calculados en local (int8, 768 dimensiones).
- **Gates.** Nunca aprueban por agotamiento (hallazgos alta o media sin
  refutar obligan a escalar); nunca critican de memoria (sin gobernanza, el
  gate escala con `sin-gobernanza`); un escalado siempre lleva causa y solo
  un humano lo rehabilita.
- **Unidad.** Nace `interactivo` salvo que el humano fije otro modo en
  `unit.start`; todo cambio de modo lo hace un humano con `unit.set_mode`
  (tras research o tras el checkpoint del spec) y queda en `modo_conversion`;
  `supervisado` y `desatendido` exigen un mandato (`unidad.plan`); no tiene a la vez orden vigente y checkpoint pendiente;
  `done` exige el gate de código superado o rehabilitado; `integrado` solo
  existe tras el cierre y no lo condiciona.
- **Checkpoints (R7).** Se resuelven por cualquier canal (elicitation,
  `unit.approve`, consola) y gana la primera resolución; la resolución
  registra actor humano y canal. La consola nunca bloquea al desarrollador.
- **Sincronización.** Eventos idempotentes por `id`, secuencia monótona por
  unidad y dirección, y la dirección debe corresponder al tipo. La cola
  local solo contiene eventos local→remoto aún no confirmados, en orden.
  En el protocolo gana el remoto; en el código, el local. Desde 1.3 el
  proxy trae los eventos remoto→local con `sync.pull(unidad, desde)` y sube
  su cola local→remoto con `sync.push`, que responde `confirmada_hasta`. La
  subida no lleva actor (lo pone el servidor) y un hueco de secuencia se
  rechaza con `secuencia-con-hueco`. `snapshot.subido` y `orden.reportada`
  solo avisan: los datos viajan en `unit.report`. El servidor no recibe
  webhooks de push (no hay receptor en `railspec-server`): `commit.empujado`
  solo llega por `sync.push`, así que una rama empujada desde otra máquina no
  genera el evento hasta que un proxy la vea.
- **Grafo (1.4).** `graph.query` añade `impact` (exige `unidad`: símbolos
  tocados por la superposición con `distancia` 0 y afectados aguas arriba con
  `distancia` >= 1 y `relacion`, todos con `riesgo`) y `trace` (por `criterio`,
  que exige `unidad`, devuelve `RefSimbolo`; por `simbolo` devuelve
  `RefCriterio`). Los resultados admiten `RefCriterio`.
- **Cobertura del índice (1.5).** `graph.index` añade `commits_cubiertos`:
  los commits de la rama por defecto que el índice incorpora al canónico
  (`git rev-list --first-parent`, los más recientes primero, a lo sumo
  `MAX_COMMITS_CUBIERTOS` = 1000; puede incluir el propio `commit`). El
  servidor no tiene git: con la lista retira, al avanzar el canónico, solo las
  superposiciones retenidas de unidades integradas en el commit del índice o
  en alguno de ellos. Vacía = no cubre más que el commit del índice; ausente
  (cliente 1.4) = las integradas en el commit del índice y, con índice
  completo, todas. Es un campo de entrada opcional y la salida no cambia: un
  servidor 1.5 acepta mensajes 1.4 y les responde con su `version_contrato`;
  uno 1.4 rechaza el campo con 422 (`reindexar.py` reintenta sin él). Orden de
  despliegue: servidor primero, luego el workflow.
- **Portabilidad (1.4).** `unit.import` crea una unidad nueva desde un
  paquete `railspec.unidad/v1`. Los artefactos presentes forman un prefijo
  (spec, plan, tasks) y `fase_retomar` es la primera fase sin artefacto, o
  `aprobacion`, `implement` o `done` si están los tres. Quedan aprobados por
  importación a nombre del humano del token y se auditan con el evento
  `importacion` (origen y número de artefactos); el gate de la fase
  siguiente corre normal. Solo un humano importa; un agente recibe
  `fuera-de-alcance`. Una unidad cerrada entra con el gate de código
  escalado con causa `importado` y rehabilitado por ese humano.
  `supervisado` y `desatendido` no se importan. Es idempotente por
  workspace, repositorio primario y origen, y responde `ya_existia`.
  `unit.export` devuelve el paquete con origen `railspec`.
- **Topes, nivel congelado y auditoría (1.7).** Todo es opcional y un
  mensaje 1.6 sigue siendo válido.
  - `Presupuesto.llamadas_max` topa las llamadas al modelo que salen hacia el
    proveedor (un acierto de caché no cuenta); `Consumo.llamadas` es su
    contador. `PresupuestoConfig.por_tier` da un `Presupuesto` por riesgo
    (`bajo`, `medio`, `alto`): rige junto a `por_unidad` y, por cada tope, el
    efectivo es el menor de los dos. Los topes siguen escalando al humano al
    alcanzarse.
  - `RepositorioUnidad.nivel_codigo` congela el nivel del vínculo al crear la
    unidad y `EstadoUnidad.nivel_efectivo` es el más restrictivo de ellos
    (`restringido` < `interno` < `abierto`, helper `mas_restrictivo`). El
    contrato exige que se congelen en todos los repositorios o en ninguno y que
    `nivel_efectivo` coincida; fijarlo al crear y aplicarlo (modelos del gate,
    qué ve cada lector) es del servidor. Sin nivel (unidad anterior a 1.7) el
    servidor lo lee del vínculo en cada uso, como hasta 1.6.
  - `RegistroAuditoria` gana el evento `cambio-modo` (`unit.set_mode`:
    `modo_anterior` y `modo_nuevo`, distintos) y exige para `rehabilitacion-gate`
    el `gate` rehabilitado. Ambos piden `unidad` y un actor humano. El servidor
    todavía no los escribe; el contrato los deja listos.
- **Proveedores compatibles (1.8).** Todo es opcional y un mensaje 1.7 sigue
  siendo válido.
  - `Proveedor.compatible`: un endpoint con la API de Anthropic o la de OpenAI
    (OpenCode Zen, MiniMax...). `SuscripcionModelo` gana `servicio` (uno
    conocido), `endpoint` (chat completions de OpenAI) y `endpoint_mensajes`
    (mensajes de Anthropic); `ModeloSuscripcion` gana `protocolo`
    (`anthropic-messages` u `openai-chat`, obligatorio en las compatibles) y
    `precio_usd_mtok` (sin él el modelo cuenta 0 USD y `costo_usd_max` no lo
    frena).
  - `ModeloCatalogo.hosting` admite `externo`. Era un modelo que solo servía a `abierto`;
    desde la decisión del 2026-10-06 es un dato informativo y sirve a cualquier repositorio
    (ver la regla del vínculo, abajo).
- **Embeddings.** Son opcionales en el delta y en la búsqueda. Si el proxy
  no tiene codificador local, sube el delta sin embeddings y busca sin
  `vector_b64`; los símbolos sin vector solo se encuentran por texto en la superposición
  de la unidad. El servidor nunca calcula embeddings de código.
- **Chat (R9, R10).** La respuesta del modelo es estructurada (afirmaciones
  y referencias tipadas) y su esquema rechaza bloques de código, HTML y
  URL. El veredicto del gate evalúa las siete reglas exactamente una vez y
  solo guarda hashes de huellas. Una respuesta bloqueada no se guarda y no
  puede pasar a un insumo.
- **Insumo (R6).** Sin texto de código en ningún nivel, protegido por un hash
  de su contenido canónico y solo existe si el gate de salida lo permitió.
- **Actor (R2).** Humano con `github_id` y login; servicio con su identidad
  OIDC; agente con su nodo y, si actúa por alguien, `en_nombre_de`.
- **Vínculo de repositorio (R4).** El nivel de código solo gobierna qué material de código
  viaja al modelo (`restringido`: nada; `interno`: fragmentos; `abierto`: diff) y se audita; **no
  restringe proveedor, modelo, región ni zona de datos** (decisión del 2026-10-06: usar
  Anthropic, modelos abiertos o proveedores compatibles es decisión consciente del usuario, y
  esos servicios pueden tener otras condiciones de retención y región). El validador del
  vínculo ya no exige `hosting: azure-zona-datos` ni prohíbe `fragmentos_en_respuesta` en
  `restringido`/`interno`; `PoliticaChat.hosting` y `Workspace.zona_datos_azure` quedan por
  compatibilidad como datos informativos. El nivel se congela al crear la unidad
  (`RepositorioUnidad.nivel_codigo`, `EstadoUnidad.nivel_efectivo`, 1.7).

## Versionado

- `version_contrato` va en todo mensaje de primer nivel; hoy es `1.11`.
- Menor (`1.x`): solo añade campos opcionales o valores de enum nuevos que
  el receptor puede ignorar. Mayor: cualquier otro cambio, con esquemas en
  `schemas/v2` en paralelo.
- Los modelos rechazan campos desconocidos. Por eso el cliente declara su
  versión en `unit.start` (`version_contrato_cliente`) y el servidor responde
  en la mínima común (`version_contrato_negociada`) y rechaza mayores que no
  conoce con `version-contrato-no-soportada`.
- Historial: `1.0` (fase 1); `1.1` añade `vector_b64` y `modelo_embedding`
  opcionales en la búsqueda de `graph.query`, la tool `graph.index` para CI,
  `repositorio_destino` en las aristas y `tipos_actor` en el registro;
  `1.2` añade la causa de escalado `sin-convergencia`, la tool
  `unit.set_mode`, `modo` opcional en `unit.start`, `tras` en cada
  conversión de modo y `pedido` en el estado de la unidad; `1.3` añade las
  tools `sync.pull` y `sync.push`, el código de error
  `secuencia-con-hueco` y el alias MCP de cada tool (`mcp_name`); `1.4`
  añade los verbos `impact` y `trace` de `graph.query`, `RefCriterio` en sus
  resultados, las tools `unit.import` (solo humanos) y `unit.export`, el evento de
  auditoría `importacion`, el paquete
  `railspec.unidad/v1`, la causa de escalado `importado` y
  `commit_integrado` opcional en `unit.integrate`, para conservar la
  superposición de la unidad en el grafo hasta que el canónico alcance ese
  commit; `1.5` añade `commits_cubiertos` opcional en `graph.index`, con el
  que CI declara los commits que cada índice incorpora y el servidor retira
  solo las superposiciones de unidades integradas en ellos. La salida de
  `graph.index` no cambia. Sin el campo (clientes 1.4) rige la regla de 1.4;
  `1.6` añade la entidad `SuscripcionModelo` (colección `suscripciones`: conexiones de la
  organización a Foundry o Anthropic con sus modelos descubiertos y elegidos, sin la clave) y
  `PerfilConfig.suscripcion` opcional. Sin ella el perfil se resuelve como en 1.5;
  `1.7` añade `Presupuesto.llamadas_max`, `Consumo.llamadas`, `PresupuestoConfig.por_tier`,
  el nivel congelado (`RepositorioUnidad.nivel_codigo`, `EstadoUnidad.nivel_efectivo`) y,
  en el registro de auditoría, el evento `cambio-modo` con `gate`, `modo_anterior` y
  `modo_nuevo`. Sin ellos rige lo de 1.6;
  `1.8` añade `Proveedor.compatible`, `SuscripcionModelo.servicio`/`endpoint_mensajes`,
  `ModeloSuscripcion.protocolo`/`precio_usd_mtok` y el `hosting` `externo` de `ModeloCatalogo`.
  Sin ellos rige lo de 1.7.
  `1.9` añade, todo con valor por defecto, la frescura del grafo y el contexto de grafo: `graph.query` añade a su
  salida `frescura` (por repositorio consultado: `indexado`, `commit`, `indexado_en` y
  `desactualizado`, también para los repositorios sin índice, que no están en `commits`) y `avisos`
  (frases legibles); ambos con valor por defecto, así que un cliente que no los lee no cambia.
  `ContextoArmado` añade `grafo_frescura` y `grafo_avisos`, y `grafo`, que existía y nunca se
  llenaba, lo llena el servidor en las órdenes de spec, plan y tasks. Un servidor anterior no los
  manda y el cliente nuevo los toma como vacíos. Como con todo campo de salida nuevo, un proxy
  anterior valida con `extra="forbid"` y rechazaría una respuesta que los trae: el servidor no
  rebaja la salida de `graph.query` ni las órdenes a la versión del cliente, así que se actualizan
  juntos (primero el proxy, que ignora lo que no sabe de un servidor anterior). Sin ellos rige lo de 1.8.
  `1.9` no cambia ninguna forma de datos por la decisión del 2026-10-06 sobre el nivel de código
  (se quitó un validador, ver el vínculo de repositorio, R4).
  `1.10` añade, todo con valor por defecto, la reconciliación del canónico por contenido:
  `GraphIndexEntrada.resumen` (`ResumenIndice`: por archivo, ruta, número de símbolos y una huella
  de 16 hex; solo en el último lote, hasta 20 000 archivos) que CI calcula sobre el índice completo
  del commit, y `FrescuraGrafo.contenido_verificado`, `divergencias_total` y `rutas_divergentes`
  (a lo sumo 20) en la salida de `graph.query`. El algoritmo de la huella vive en
  `railspec.contracts.resumen`, que usan CI y servidor. `GraphIndexSalida` no cambia: un cliente
  anterior la valida con campos prohibidos. Un CI 1.9 no manda resumen y el servidor deja
  `contenido_verificado` en `None`; un servidor 1.9 rechaza el resumen con 422 y `reindexar.py`
  reintenta sin él. Como en 1.9, un proxy anterior rechazaría los campos nuevos de `frescura`: se
  actualiza primero el proxy. Sin ellos rige lo de 1.9.
  `1.11` añade el mandato de los modos supervisado y desatendido ([mandato.md](mandato.md)): el
  modelo `Mandato` y las seis tools `mandate.*`; `AvanceMandatoParado` (`tipo: "mandato-parado"`) como
  quinto `Avance` de `unit.advance`; `OrdenDeTrabajo.mandato`, `ReporteOrden.decisiones`,
  `EstadoUnidad.decisiones`, `Checkpoint.causa_parada` y `ResultadoGate.diferido`; el código de
  error `mandato-no-vigente` (409) y el evento de auditoría `cambio-mandato`, `decision-delegada` y
  `revision-decision`. Todo con valor por defecto, salvo que `unit.start` y `unit.set_mode` ya no
  admiten supervisado ni desatendido sin mandato aprobado. Un proxy anterior no entiende
  `mandato-parado`: se actualiza primero el proxy. `Consumo.llamadas` (de 1.7) empieza a contarse.
- Los esquemas se publican con `$id` `https://railspec.dev/schemas/v1/<nombre>.schema.json`
  (dominio sin reservar; el `$id` es solo un identificador).

## Ids de símbolo y referencias entre repositorios

`id_simbolo(repositorio, ruta, tipo, nombre)` es SHA-256 de
`repositorio\0ruta\0tipo\0nombre calificado`, donde `repositorio` es el
slug del vínculo en el workspace, no el nombre del repositorio en GitHub.
El proxy local y el CI calculan con la misma función el id de destino de
una arista hacia otro repositorio del workspace, y marcan esa arista con
`repositorio_destino`. Así la referencia resuelve contra el grafo de ese
vínculo, y el servidor la corta si el repositorio destino no es visible.

## Almacenamiento e identidad

`almacen.py` define las interfaces:
`StateStore` (Mongo: `AlmacenMongo` en `railspec-server`), `GraphStore` y
`VectorStore` (FalkorDB, un grafo por workspace y repositorio, con LadybugDB
como alternativa: `AlmacenGrafo` y `AlmacenVectores` en `railspec-graph`) y
`ProveedorIdentidad` (GitHub). Cada método exige su alcance tipado, así que
no hay consulta sin workspace. `CheckpointStorage` es el de Microsoft Agent
Framework y lo implementa `CheckpointsMongo` en `railspec-server`.

`repositorio.py` fija las colecciones con su clave de aislamiento y su campo
de TTL, y el nombre de grafo `railspec:<org>:<workspace>:<repositorio>`.

## Variables de entorno del servidor

Los secretos (URI de Mongo, URL de FalkorDB y claves) vienen de un Kubernetes
Secret y el resto del ConfigMap. Aquí van las principales; la lista completa
está en [despliegue.md](despliegue.md#variables-de-los-manifiestos).

| Variable | Uso |
|---|---|
| `RAILSPEC_MONGO_URI`, `RAILSPEC_MONGO_DB` | Estado, checkpoints, telemetría |
| `RAILSPEC_POSTGRES_URL`, `RAILSPEC_POSTGRES_ESQUEMA` | Lo mismo en Postgres en lugar de Mongo; excluyente con `RAILSPEC_MONGO_URI` ([estado-postgres.md](estado-postgres.md)) |
| `RAILSPEC_FALKORDB_URL` | Grafo central y vectores |
| `RAILSPEC_FOUNDRY_ENDPOINT`, `RAILSPEC_FOUNDRY_API_KEY` | Proveedor primario (sin clave = Entra ID) |
| `RAILSPEC_CLAVE_MAESTRA` | Cifra las claves de las suscripciones de modelos que se registran en la consola ([proveedores.md](proveedores.md#suscripciones-de-modelos)) |
| `RAILSPEC_ANTHROPIC_HABILITADO`, `RAILSPEC_ANTHROPIC_API_KEY` | Adaptador de Anthropic tras bandera |
| `RAILSPEC_GITHUB_APP_CLIENT_ID`, `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | Identidad: inicio de sesión de la consola y comprobación de que cada token de GitHub lo emitió la App |
| `RAILSPEC_GITHUB_APP_ID`, `RAILSPEC_GITHUB_APP_CLAVE_PRIVADA` | Opcionales, juntas: la App como instalación, para que la consola abra PR con `contexto.yaml` y `.railspecignore` |
| `RAILSPEC_PCE_URL`, `RAILSPEC_PCE_API_KEY` | Proveedor de gobernanza |

## Lo que queda abierto

- Umbrales del gate de salida (N de huella por nivel, presupuestos de fuga):
  `politica_chat_por_defecto` fija valores iniciales editables por vínculo;
  el hilo del chat los calibra con el corpus de ataques.
