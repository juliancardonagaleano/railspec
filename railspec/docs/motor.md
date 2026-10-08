# Motor de protocolo (`railspec-server`)

El servidor ejecuta el protocolo SDD como un DAG de Microsoft Agent Framework
(Python) por unidad. El arnés solo llama tools; el servidor decide qué orden
toca, corre los gates y abre los checkpoints.

## Piezas

| Módulo | Qué hace |
| --- | --- |
| `estado/mongo.py` | `AlmacenMongo`: `StateStore` más lo propio del motor (órdenes, snapshots, entradas pendientes, turno por unidad, configuración). Toda consulta pasa por un filtro con organización y workspace. |
| `estado/checkpoints.py` | `CheckpointsMongo`: `CheckpointStorage` de MAF sobre Mongo; un workflow por unidad (`railspec:{org}:{ws}:{unidad}`), con orden por contador atómico. |
| `proveedores/` | `ProveedorModelo` con los SDK oficiales: Foundry (despliegues Claude por `/anthropic` y el resto por chat completions, clave o Entra ID) es el primario; Anthropic directo solo con `RAILSPEC_ANTHROPIC_HABILITADO`, para cualquier repositorio (decisión consciente del usuario). Catálogo por API, selección por rol sin restricción por nivel ni zona de datos. Ver [proveedores.md](proveedores.md). |
| `contexto/` | Herramientas de contexto conectables por rol (PCE primero): implementan el proveedor de gobernanza del gate. Ver [proveedores.md](proveedores.md). |
| `motor/artefactos.py` | Capa determinista: plantillas, secciones obligatorias, `CA-NN`, grupos del plan, comando de validación y tareas trazables. Sin tokens. |
| `motor/gate.py` | Panel de críticos en paralelo (un lente por crítico), refutador para severidad alta y regla de convergencia. |
| `motor/escaneo.py` | Reescaneo de secretos del texto de código de un snapshot (`diff` y fragmentos) con los patrones del proxy; lo usa `unit.report`. |
| `motor/dag.py` | Nodos del DAG: triaje, redacción, gate, decisión humana, avance, implementación y cierre. |
| `motor/motor.py` | Tools `unit.*` y `telemetry.query`; runner que reanuda el DAG desde el último checkpoint. |
| `api/` | Registro único de tools (R1) expuesto por MCP en `/mcp` (proxy local, con el alias `nombre_mcp`: `unit_start`) y por HTTP en `/v1/tools/{nombre}` (consola, nombre canónico `unit.start`). El registro acepta las dos formas. |

## Recorrido

```
triaje → redacción(spec) → gate → decisión → avance → redacción(plan) → gate → …
       → redacción(tasks) → gate → implementación(grupos) → validar → gate de código → cierre
```

- Cada orden se emite con `request_info` de MAF y el workflow queda en pausa
  en un checkpoint. `unit.report` y `unit.approve` guardan una entrada
  pendiente y la entregan al DAG con `run(responses=…)`.
- Un turno por unidad en Mongo hace a cada unidad de un solo escritor entre
  réplicas. Si la réplica cae tras registrar la entrada, el siguiente
  `unit.advance` la procesa.
- Modos: `interactivo` abre `aprobar-spec` y `aprobar-plan`; `semi-autonomo`
  abre un `paquete-aprobacion`; `supervisado` y `desatendido` solo paran en
  paradas y gates escalados.
- El gate escala sin gastar tokens si la gobernanza no respondió o respondió
  a medias (`sin-gobernanza`) o si el presupuesto se agotó (ver
  [Presupuestos y telemetría](#presupuestos-y-telemetría)). Un fallo del
  proveedor escala con `error-proveedor`.
- Los artefactos de la unidad (`.railspec/unidades/<unidad>/`) viven en su
  worktree: la orden `implementar` los admite en `alcance.permitidos` y el gate
  de código no los cuenta como archivos fuera del plan.
- El gate de código revisa primero archivos fuera del plan y la validación
  fallida. Con `railspec-graph` configurado, suma a los críticos el impacto
  de la superposición de la unidad (`impacto`: tocados, afectados aguas
  arriba, procesos y riesgo) y cuántos símbolos tiene enlazados cada `CA-NN`
  (ver `grafo.md`). El grafo informa, no genera hallazgos deterministas.
- Las órdenes de redactar y refinar de spec, plan y tasks llevan en
  `contexto.grafo` una rebanada del grafo remoto (`motor/contexto_grafo.py`):
  el motor consulta `graph.query` (`resolve` para los nombres entre comillas
  invertidas del pedido, `search` para las palabras con contenido del título,
  el pedido y los criterios) sobre los repositorios de la unidad, con hasta 6
  consultas de 8 resultados, y deja como mucho 20 nodos y 6000 caracteres:
  símbolo, nombre, tipo, ruta y por qué entró. Nunca texto de código, que el
  grafo no guarda. La búsqueda es por nombre de símbolo, no semántica: un
  título en español rara vez coincide con identificadores en inglés. La orden
  de implementar no la lleva (el arnés usa `graph_query`).
  - Nunca falla la orden: sin grafo configurado (el despliegue de Render no
    define FalkorDB), con un repositorio sin índice canónico, con el índice
    desactualizado o con el grafo caído, la orden sale igual y
    `contexto.grafo_avisos` lo dice, con `grafo_frescura` (commit e instante
    del último índice por repositorio) cuando hubo consulta; ahí viajan también
    el aviso de que el contenido del canónico no coincide con el repositorio
    (`contenido_verificado`, 1.10) y el de una superposición retenida de la
    unidad que lleva más de la mitad de su plazo. El arnés debe leer una
    rebanada vacía como «no sé», no «no existe».
  - Los críticos del gate de spec, plan y tasks no reciben este contexto:
    evalúan el artefacto con la gobernanza. Solo el gate de código suma
    información del grafo (impacto y trazas).
- `unit.report` ingiere el snapshot en el grafo con la política del vínculo y
  enlaza los criterios de las tareas completadas; `unit.integrate` retiene
  la superposición de la unidad en el repositorio primario hasta que un
  `graph.index` cubra `commit_integrado` (el propio commit del índice o uno de
  los `commits_cubiertos` que declara CI desde 1.5; el canónico los recuerda,
  así que una unidad integrada en un commit que ya cubrió se retira al integrar)
  y descarta las de los demás (todas, si no hay commit). Una retenida que ningún
  índice cubre caduca a los `RAILSPEC_GRAFO_RETENIDAS_DIAS` (30); ver `grafo.md`.
- El modo lo fija el humano en `unit.start` o con `unit.set_mode`, solo
  tras research o tras el checkpoint del spec (contratos 1.2); rige desde el
  siguiente gate. `supervisado` y `desatendido` exigen un mandato: la entrada
  de `unit.set_mode` ya pide `unidad.plan`, y si la unidad guardada no
  pertenece a ninguno responde `conversion-no-permitida` (no guarda nada).
- `unit.report` reconoce el reenvío de un reporte ya aceptado (misma orden y
  secuencia) y responde `secuencia-duplicada` en vez de `orden-no-vigente`, para
  que el proxy lo dé por entregado.
- `unit.report` vuelve a escanear el texto de código del snapshot (`diff` y
  `fragmentos[].texto`; solo existen en `interno` y `abierto`, en `restringido`
  no hay nada que revisar) porque `escaneo_secretos.hallazgos == 0` lo declara
  el cliente y el servidor no se fía. Usa los mismos 13 patrones y el mismo
  criterio línea a línea que el proxy (`chat/secretos.py` copia
  `railspec/local/secretos.py` y una prueba compara las dos listas). Un
  hallazgo rechaza el reporte con `snapshot-invalido`, nombra tipo y ruta
  (nunca el valor), no guarda el reporte ni el snapshot ni lo ingiere al grafo,
  y deja solo un log de advertencia, sin evento de auditoría. La orden sigue
  vigente: el proxy puede reportar de nuevo sin el secreto. Un falso positivo
  se corrige en el patrón de los dos lados, nunca con un bypass.
  - El diff incluye las líneas borradas y el proxy solo escanea el árbol
    final: un cambio que retira un secreto ya commiteado se rechaza en
    `abierto` porque el diff lleva el valor viejo.
  - Ningún patrón cuesta más que lineal sobre el texto del cliente:
    `cadena-conexion` (esquema de hasta 32 caracteres, usuario de hasta 128 y
    clave de hasta 256) y `jwt` (cabecera de hasta 256) llevan cota en sus
    cuantificadores, igual en el servidor y en el proxy. Sin ellas eran
    cuadráticos en una corrida larga de `[a-z0-9+.-]` (`a.a.a.…`) y un diff de
    2 MB hecho a propósito ocupaba el servidor media hora; ahora se revisa en
    menos de un segundo. Una cadena de conexión que pase de esas cotas (o un
    `jwt` con cabecera de más de 256) ya no se detecta.
  - Como segunda barrera, las líneas de más de 1024 caracteres se revisan por
    tramos solapados (un secreto de hasta 512 caracteres cabe entero en
    alguno; la cadena de conexión más larga que detecta el patrón mide 422).
- Sincronización (contrato 1.3): `sync.pull` pagina los eventos remoto→local;
  `sync.push` recibe la cola local→remoto del proxy, idempotente por id y sin
  huecos (`secuencia-con-hueco`). Esa dirección la numera solo el proxy: el
  servidor no emite `orden.reportada` ni `snapshot.subido` al recibir
  `unit.report`.
- Las aprobaciones web son opcionales y gana la primera resolución; la
  segunda recibe `checkpoint-ya-resuelto`.
- Importar y exportar (contrato 1.4, módulo `portabilidad`): `unit.import`
  crea una unidad desde un paquete `railspec.unidad/v1` y entra al DAG por
  triaje con `Importacion` en vez de `Arranque`. Los artefactos del paquete
  pasan la capa determinista en orden; el primero que no encaja en la
  plantilla (lo habitual con el kit SDD) vuelve a refinar con esos hallazgos,
  y si todos encajan la unidad retoma en `fase_retomar`: redacción, paquete de
  aprobación según el modo, implementación o cierre. Una unidad cerrada entra
  cerrada, con el gate de código escalado por `importado` y rehabilitado por
  quien importa; los gates de spec, plan y tasks no se fingen. Es idempotente
  por workspace, repositorio primario y origen (colección `importaciones`) y
  cada importación se audita con su origen. `unit.export` devuelve el paquete
  con los artefactos del checkpoint aprobados como prefijo según la fase.

## Presupuestos y telemetría

El gate comprueba tres topes de `PresupuestoConfig` (los edita la consola) con
lo ya consumido; al alcanzar uno escala con `presupuesto-agotado` y el motivo
del checkpoint dice cuál:

| Tope | Se compara con | Motivo |
| --- | --- | --- |
| `por_unidad` | El `consumo` de la unidad. Se copia a la unidad al arrancarla: cambiarlo después no la alcanza. | `por_unidad: tokens 1200/1000` |
| `por_fase` | La telemetría de esa unidad en esa fase; el gate de código cuenta para `implement`. Se lee vigente. | `por_fase plan: costo 1.02/1.00 USD` |
| `mensual_usd` | El costo de toda la telemetría del workspace en el mes calendario UTC, chat incluido. Se lee vigente. | `mensual_usd 2026-10: costo 50.02/50.00 USD` |

- Una configuración de la organización (sin workspace) rige para cada
  workspace por separado, también el tope mensual.
- Se comprueba al empezar el gate y antes de cada llamada del panel al
  proveedor, sumando lo que ya costaron las llamadas del mismo panel (aún sin
  registrar). Los críticos salen a la vez y comparten lo gastado hasta ese
  momento: el tope frena la tanda siguiente (el refutador), no la que está en
  vuelo, así que un panel puede pasarlo por lo que cueste esa tanda. Lo ya
  llamado se registra y consume igual.
- Una respuesta de la caché no gasta y no se comprueba. El chat no comprueba
  estos topes todavía; su gasto sí cuenta para el mes.
- `segundos_max` por fase suma la duración de las llamadas fallidas también;
  el `consumo` de la unidad no las cuenta.
- El costo es la estimación de `PRECIOS_USD_MTOK`; manda la factura de Azure.

Cada llamada deja su fila de auditoría y de telemetría una sola vez: el
consumo se aplica aparte y es lo único que se reintenta ante un
`conflicto-version`. `TelemetriaNodo.veredicto` es el de la iteración del gate
a la que pertenece la llamada: `aprobado` si el gate cierra en la primera,
`refinado` si pide otra iteración o cierra tras refinar, `escalado` si escala
(también tras un error del proveedor o un tope agotado a mitad del panel).

La clave de la caché de nodos incluye el commit del código evaluado
(`repositorio@commit` de cada repositorio de la unidad): el mismo material
sobre otro commit es otra pregunta. Ver [proveedores.md](proveedores.md).

## Avisos

Cuando `Dag.escalar` cierra un gate como `escalado`, llama a `Nucleo.avisar_escalado`, que arma un `EventoAviso`
(organización, workspace, unidad, gate, causa y, si la causa es `presupuesto-agotado`, el tope y el consumo que
extrae del motivo) y se lo pasa a `ServicioAvisos.notificar` (`railspec.server.avisos`). Ese método encola el trabajo
en un hilo y nunca lanza ni espera a la red. El hilo registra el escalado en `avisos_eventos` (de ahí sale el conteo
del informe, aunque los avisos estén apagados) y, si la organización los tiene activos, lo manda por sus canales.
Configuración, canales, reservas y el informe semanal: [consola.md](consola.md#avisos-e-informes). No cambia el
contrato.

## Política de datos con varios repositorios

Una unidad puede tocar varios repositorios (el primero es el primario; los demás,
transversales), cada uno con su `nivel_codigo`. **Gana el más restrictivo**
(`restringido` > `interno` > `abierto`; un repositorio sin vínculo cuenta como
`restringido`). Ese nivel efectivo (`Nucleo.nivel(estado)`) es el que rige el
material de código que sale de la unidad hacia un modelo. **No elige proveedor,
modelo, región ni zona de datos** (decisión del 2026-10-06): usar Anthropic,
modelos abiertos o proveedores compatibles es decisión consciente del usuario,
que debe saber que esos servicios pueden tener otras condiciones de retención y
región (ver [proveedores.md](proveedores.md#política-por-nivel)).

- `unit.start` congela el nivel de cada repositorio (`RepositorioUnidad.nivel_codigo`)
  y el efectivo (`EstadoUnidad.nivel_efectivo`, contrato 1.7): un cambio posterior
  del vínculo en la consola no altera las unidades en curso. Una unidad anterior a
  1.7 (sin nivel congelado) sigue leyendo el vínculo vivo. `unit.import` los
  congela igual.
- `unit.start` valida el perfil contra el catálogo: si no puede servirlo (modelo
  ausente o sin la capacidad pedida), responde `perfil-insatisfacible` antes de
  crear la unidad. El nivel efectivo solo escoge las claves por nivel del perfil
  (`rol:nivel`); no excluye proveedores.
- Cada llamada del gate se audita con el nivel efectivo (`nivel_codigo`), el
  congelado de la unidad.
- El material del gate de código solo incluye el diff si ningún repositorio de la
  unidad es `restringido`. Los símbolos y las rutas (sin texto de código) van siempre.
- La comprobación del snapshot (`la unidad fija nivel …`) sigue siendo por
  repositorio: cada snapshot declara el nivel congelado de su repositorio.

Los roles son por workspace, no por repositorio: un `lector` ve (`graph.query`,
`unit.list`, `unit.status`, estadísticas) todos los repositorios vinculados a su
workspace y ninguno de otro workspace. Limitar por repositorio exigiría un rol por
repositorio, que el modelo de datos no tiene; el límite de visibilidad es el workspace.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `RAILSPEC_POSTGRES_URL`, `RAILSPEC_POSTGRES_ESQUEMA` | Estado y checkpoints en Postgres en lugar de Mongo (excluyente con `RAILSPEC_MONGO_URI`); ver [estado-postgres.md](estado-postgres.md). |
| `RAILSPEC_MONGO_URI`, `RAILSPEC_MONGO_DB` | Estado y checkpoints. Sin URI, el servidor solo arranca con `RAILSPEC_PERMITIR_DESARROLLO=1` (estado en memoria, solo desarrollo) y rechaza el arranque si no. |
| `RAILSPEC_FOUNDRY_ENDPOINT`, `RAILSPEC_FOUNDRY_API_KEY` | Recurso de Azure Foundry. Sin clave, Entra ID. El resto de variables de proveedores, catálogo y contexto está en [proveedores.md](proveedores.md). |
| `RAILSPEC_ANTHROPIC_HABILITADO`, `RAILSPEC_ANTHROPIC_API_KEY` | Anthropic directo (para cualquier repositorio; decisión consciente del usuario). |
| `RAILSPEC_PCE_URL`, `RAILSPEC_PCE_API_KEY` | Gobernanza por MCP. Sin ella, todo gate escala con `sin-gobernanza`. |
| `RAILSPEC_FALKORDB_URL` | Grafo central (`railspec-graph`, extra `grafo`). |
| `RAILSPEC_OIDC_AUDIENCIA` | Audiencia de los tokens OIDC de GitHub Actions: un valor largo y no adivinable, el mismo que usa el workflow de reindexado. Sin valor (el defecto) la identidad de servicio queda desactivada; `railspec` se rechaza. |
| `RAILSPEC_OIDC_EMISOR` | Emisor y JWKS (por defecto `https://token.actions.githubusercontent.com`). |
| `RAILSPEC_OIDC_REPOSITORIOS` | Lista `owner/repo` separada por comas que puede presentar tokens OIDC. Obligatoria con audiencia: vacía, el servidor no arranca. |
| `RAILSPEC_TOKENS_DESARROLLO` | `token=login:github_id,…` para desarrollo sin GitHub App; sustituye la identidad de GitHub. Con `RAILSPEC_MONGO_URI` o GitHub App configurados, el servidor no arranca salvo `RAILSPEC_PERMITIR_DESARROLLO=1`. |
| `RAILSPEC_PERMITIR_DESARROLLO` | `1` permite el modo desarrollo: arrancar sin Mongo (todo en memoria; cualquier persona autenticada sin asignaciones es `desarrollador` en toda organización) y los tokens de desarrollo junto a Mongo o a la GitHub App. Deja un WARNING al arrancar. Nunca en producción: el Deployment de AKS la fuerza vacía. |
| `RAILSPEC_HOST`, `RAILSPEC_PUERTO` | Escucha HTTP (por defecto `0.0.0.0:8080`). |

Arranque: `pip install -e "railspec/packages/railspec-server[motor]"` y
`railspec-server`.

## Reindexado del canónico (`graph.index`)

- Identidad: un token cuyo `iss` es el emisor de GitHub Actions se verifica
  como OIDC (firma contra el JWKS, `iss`, `aud`, caducidad y, si hay lista,
  `repository`) y da un actor de servicio con canal `ci`. Cualquier otro token
  sigue la identidad humana.
- La tool comprueba además que `repository` sea el repositorio de la URL del
  vínculo y que el workflow corriera en su rama por defecto (el `@ref` de
  `workflow_ref`).
- Los lotes se guardan en el grafo de preparación del commit y el canónico
  avanza al llegar el último (`IndexadorCanonico` de `railspec-graph`); con
  `resumen` (1.10, en el último lote) compara entonces el contenido del canónico
  con el que calculó CI y lo avisa en `graph.query` sin rechazar el índice. Un
  delta cuya base no es el canónico vigente responde `base-commit-distinto`
  (409) y el cliente repite con índice completo.

## Sondas

- `GET /healthz` (disponibilidad): comprueba Mongo (`ping`) y FalkorDB con un
  tope de 2 s por sonda; 503 con el detalle si alguna falla.
- `GET /livez` (vida): solo que el proceso atiende. La sonda de vida del
  despliegue debe usar esta, para no reiniciar réplicas por una caída de la
  base.

## Pendiente

- `insumo.get` se enchufa siempre; `code.read`, solo con una fuente de código
  (`RAILSPEC_CHAT_CLONES`), y `graph.query` y `graph.index`, con el grafo. El
  registro no anuncia la tool cuyo manejador falta.
- Presupuesto por tier, meta de llamadas y contador de aciertos de la caché
  (contrato 1.6); `unit.advance` ignora `version_vista` y `dueno` no se exige.
- La forma de la respuesta de PCE no está verificada contra el servicio real.
