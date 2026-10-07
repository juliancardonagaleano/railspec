# Deuda técnica

Lo que se sabe que falta y se decidió no hacer todavía. Cada entrada dice qué hay hoy, qué falta y
cómo se sabrá que está resuelta. Se verificó contra `master` el 2026-10-07.

## GitHub Actions no ejecuta ningún job de CI

**Qué hay hoy.** Todos los jobs de `railspec-ci`, `railspec-imagen` y `railspec-binario` fallan en
3 a 5 segundos sin runner asignado (`runner_id: 0`), sin pasos ni logs, en `master` y en cada PR:
es el límite de gasto o la facturación de la cuenta, no el código. Mientras tanto la verificación
es local y cada PR lo dice.

**Resuelta cuando.** Un run de `railspec-ci` en `master` llega a ejecutar sus pasos. El primer fallo
real que cabe esperar entonces son las pruebas que no se han visto en CI (FalkorDB real, `humo.mjs`).

## Métricas de operación: sin scraper ni alertas en marcha (`/metrics`)

**Qué hay hoy.** El servidor publica `GET /metrics` en texto de Prometheus
(`packages/railspec-server/src/railspec/server/metricas.py`): versión del servidor, versión del
esquema del estado (la del código y la guardada), estado de cada sonda, peticiones por superficie y
clase de estado, e instante de arranque. Solo existe si `RAILSPEC_METRICAS_TOKEN` está definido
(16 caracteres o más) y exige `Authorization: Bearer <token>`. La variable ya está cableada en
`render.yaml`, `deploy/render/render-ghcr.yaml` (secreta, sin valor), en el Secret de AKS
([despliegue.md](despliegue.md#esquema-del-estado-y-métricas)) y en las pruebas de variables del
despliegue; la documentación trae un trabajo de Prometheus de ejemplo con el token como credencial,
cómo rotarlo y cuatro alertas sugeridas.

**Qué falta.**

- **Definir el token y poner un scraper** (esto solo lo puede hacer quien administra el servicio). Nadie consulta el endpoint: el token no está definido en
  ningún despliegue real y no hay Prometheus (o un servicio gestionado que acepte
  `Authorization: Bearer`) que lo lea. En Render hay que decidir dónde vive y cuánto cuesta.
- **Crear y probar las alertas.** Las reglas están en `deploy/prometheus/alertas.yaml` (con una
  más, `RailspecSinMetricas`, para un scrape caído) y una prueba verifica su forma y que cada
  métrica que usan la publique el servidor; `deploy/prometheus/verificar_metricas.py <url>` consulta
  un servidor desplegado con el token y dice si Prometheus lo podría leer (`up == 1`), sin montar
  Prometheus. Falta cargar las reglas en un Prometheus real y dispararlas a propósito una vez.
- **Aceptar los límites.** Los contadores son por réplica y en memoria (se reinician con el proceso;
  `rate()` lo tolera y Prometheus suma entre réplicas) y no hay métricas de negocio (costo, caché,
  latencia de los gates): eso sigue saliendo de la consola de estadísticas.

**Resuelta cuando.** Un servidor desplegado tiene el token definido, un scraper lo consulta con
éxito (`up == 1`) y las alertas de [despliegue.md](despliegue.md#esquema-del-estado-y-métricas) están
creadas y se probaron una vez disparándolas a propósito.

## Búsqueda semántica sin codificador local de embeddings

**Qué hay hoy.** El contrato y el servidor saben guardar y comparar embeddings (int8, 768,
`Embedding.vector_b64`) y `graph.query` admite `semantica` con el `vector_b64` que calcula el
proxy. Pero ningún eslabón los produce: `codebase-memory-mcp` 0.11 no expone sus vectores
(`indexador_cbm.py`), así que los deltas viajan sin embeddings; `embedding_consulta` devuelve
`None`; y el servidor no cablea un `CodificadorConsulta` (`app.py` construye `AlmacenGrafo` sin él, y
nunca calcula embeddings de código). Una búsqueda semántica cae a texto y la respuesta lo avisa. La
rebanada de grafo del contexto de spec, plan y tasks tampoco es semántica: busca por nombre de
símbolo ([motor.md](motor.md#recorrido)).

**Decisión pendiente (no se cierra desde el repositorio).** Elegir el modelo de embeddings y dónde
corre (el proxy de cada desarrollador y el job de CI) exige bajar pesos de varios GB y probar su
calidad sobre código real, y el entorno de las sesiones en la nube bloquea esas descargas.

**Qué falta.** Un codificador local (el modelo `nomic-embed-code` que fija el contrato, o una
vía para leer los vectores del indexador) que calcule los embeddings del delta en el proxy y en
el job de CI, y el de la consulta en el proxy; y decidir si el motor, al armar la rebanada del
contexto, pide `semantica` cuando haya vector.

**Resuelta cuando.** Un índice de CI sube embeddings, una búsqueda con `semantica: true` desde
el proxy devuelve resultados por similitud sin avisos, y una prueba contra un repositorio real lo
verifica.

## FalkorDB sin definir en el despliegue de Render

**Qué hay hoy.** El despliegue de Render (`render.yaml`, `deploy/render/render-ghcr.yaml`,
[despliegue-render.md](despliegue-render.md)) no define `RAILSPEC_FALKORDB_URL`: sin ella el
servidor arranca sin grafo. Por eso `graph.query` y `graph.index` no existen allí, el gate de
código pierde el impacto y las trazas `CA-NN`, y las órdenes de spec, plan y tasks salen sin
rebanada de grafo y con el aviso de que no hay grafo configurado. Tampoco corre ni tiene
sentido el workflow de reindexado. Solo el despliegue de AKS lleva FalkorDB.

**Decisión pendiente (cuesta dinero, no se hace sin pedirlo).** FalkorDB es un módulo de Redis: no
corre en el Key Value gestionado de Render, sino como servicio privado con imagen propia y disco
persistente de pago, y el plan gratuito de Render no ofrece discos.

**Qué falta.** Decidir si Render tendrá grafo. Si sí: un FalkorDB alcanzable desde ese servicio
(gestionado o propio), su URL como variable secreta en el blueprint, `RAILSPEC_OIDC_*` para el
reindexado desde CI y documentarlo. Si no: dejarlo escrito como límite del despliegue de prueba
y que quien despliega en Render lo sepa antes de probar.

**Resuelta cuando.** El blueprint de Render define el grafo y una unidad de prueba recibe una
rebanada de grafo en su orden de spec, o la decisión de no tenerlo queda documentada.

## Reconciliación del grafo canónico: falta verla funcionar contra un repositorio real

**Qué hay hoy (2026-10-07).** Cada índice de CI trae un resumen del contenido del commit (por
archivo: ruta, número de símbolos y una huella de los símbolos y sus hashes;
`GraphIndexEntrada.resumen`, contrato 1.10). El servidor calcula el suyo sobre el canónico recién
actualizado, lo compara ruta por ruta (sin las rutas que el vínculo excluye) y deja el resultado en
`graph.query` (`frescura.contenido_verificado`, `divergencias_total`, `rutas_divergentes`) y en
`avisos`, que el contexto de spec, plan y tasks también arrastra ([grafo.md](grafo.md)). El
reindexado hace además un índice completo semanal programado (lunes, `railspec-reindexar.yml`), que
sustituye al incremental y corrige lo que haya divergido. Las pruebas adulteran un canónico a
propósito y lo detectan; un índice sin resumen deja `None`.

**Qué falta.**

- Probarlo contra un repositorio real y un FalkorDB real: las pruebas usan el motor en memoria y la
  consulta Cypher que guarda la meta nueva no se ha ejecutado contra FalkorDB. CI no corre en esta
  cuenta (ver más abajo), así que el workflow y el cron tampoco se han visto ejecutarse.
- Medir el costo de la pasada extra del indexador por push incremental (el resumen necesita todos
  los símbolos del commit) frente al límite de 60 minutos del job; `--sin-resumen` la quita.
- Un repositorio con más de 20 000 archivos con símbolos no manda resumen y no se verifica.
- Una divergencia no corregida persiste en los índices incrementales siguientes hasta un índice
  completo; el cron semanal la limita a una semana.

**Resuelta cuando.** Un índice real subido por el job de CI queda `contenido_verificado: true` en
una consulta, y una adulteración a propósito en un despliegue de prueba se avisa.

## Superposición retenida: salida automática hecha; falta el barrido sin índices

**Qué hay hoy (2026-10-07).** El canónico recuerda los commits que cubrieron sus últimos índices
(hasta 1000, el completo reemplaza la lista), así que integrar una unidad en un commit que ya
cubrió se retira al instante. Una retenida cuyo commit nunca llega caduca a los
`RAILSPEC_GRAFO_RETENIDAS_DIAS` (30; `0` no caduca), `graph.query` con `unidad` avisa desde la mitad
del plazo y `GET /consola/api/orgs/{org}/workspaces/{ws}/grafo/retenidas` las lista
([grafo.md](grafo.md#superposición-de-una-unidad-integrada)). `retirar_todas` ya no es la única salida.

**Qué falta.**

- El barrido de caducadas corre al llegar un `graph.index`, como los otros: un repositorio que deja
  de recibir índices no barre sus retenidas (el aviso sí sale en cada consulta con `unidad`).
- La lista de retenidas existe como API, no como pantalla de la consola.
- Sin probar contra FalkorDB real (ver arriba).

**Resuelta cuando.** Una retenida de un repositorio sin índices nuevos se retira sin intervención
(barrido propio o un disparador) y la consola las muestra.
