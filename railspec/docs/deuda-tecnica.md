# Deuda técnica

Lo que se sabe que falta y se decidió no hacer todavía. Cada entrada dice qué hay hoy, qué falta y
cómo se sabrá que está resuelta. Se verificó contra `master` el 2026-10-06.

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

- **Definir el token y poner un scraper.** Nadie consulta el endpoint: el token no está definido en
  ningún despliegue real y no hay Prometheus (o un servicio gestionado que acepte
  `Authorization: Bearer`) que lo lea. En Render hay que decidir dónde vive y cuánto cuesta.
- **Probar el ejemplo contra un servidor real y crear las alertas.** El trabajo de Prometheus y las
  alertas documentadas no se han ejecutado; se escribieron contra el formato que publica `metricas.py`.
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

**Qué falta.** Decidir si Render tendrá grafo. Si sí: un FalkorDB alcanzable desde ese servicio
(gestionado o propio), su URL como variable secreta en el blueprint, `RAILSPEC_OIDC_*` para el
reindexado desde CI y documentarlo. Si no: dejarlo escrito como límite del despliegue de prueba
y que quien despliega en Render lo sepa antes de probar.

**Resuelta cuando.** El blueprint de Render define el grafo y una unidad de prueba recibe una
rebanada de grafo en su orden de spec, o la decisión de no tenerlo queda documentada.

## Reconciliación del grafo canónico con el repositorio, sin comparación

**Qué hay hoy.** El canónico avanza por deltas de CI y nada verifica que su contenido coincida con
el repositorio en ese commit. Los únicos controles son que `commit_anterior` sea el commit del canónico
(`base-commit-distinto` si no), el aviso del servidor por tiempo
(`RAILSPEC_GRAFO_FRESCURA_HORAS`) y el del proxy local, que compara el commit del canónico con tu
rama base ([proxy-local.md](proxy-local.md#consultas-al-grafo-y-sus-avisos)). Ambos miran
commits y reloj, no contenido: un delta que perdió un símbolo, o una exclusión mal aplicada, dejan
un canónico del commit correcto pero distinto del código, y solo un índice completo manual
(`completo` en `railspec-reindexar`) lo corrige. El servidor no tiene git, así que no puede
comparar por su cuenta.

**Qué falta.** Una comparación periódica: que el job de CI, que sí tiene el repositorio, calcule un
resumen del índice (conteo y hash de símbolos por archivo) y el servidor lo compare con el del
canónico, o que un índice completo programado reemplace al incremental cada cierto tiempo; y que
una divergencia se avise en `graph.query` junto con la frescura.

**Resuelta cuando.** Un canónico adulterado a propósito en una prueba se detecta y se avisa sin
intervención humana, y un índice completo programado deja de ser trabajo manual.

## Superposición retenida que ningún índice cubre

**Qué hay hoy.** Al integrar una unidad con `commit_integrado`, su superposición queda
retenida hasta que `graph.index` cubra ese commit; las retenidas no caducan por tiempo
([grafo.md](grafo.md#superposición-de-una-unidad-integrada)). Si ese commit nunca llega al canónico
(el merge fue a otra rama, no se empujó, un force-push lo borró, o quedó fuera de los 1000
commits que declara un índice completo) la superposición queda varada para siempre, visible a las
consultas con esa `unidad` y cuenta en el volumen del grafo. El servidor tampoco guarda qué commits
cubrió ya cada índice, así que una unidad integrada con un commit que el canónico ya pasó no se
retira sola.

**Qué falta.** Una salida que no dependa de una acción manual: un plazo máximo de retención
(configurable, con aviso antes de borrar), o que el servidor guarde los commits cubiertos por
cada índice para retirar al integrar las que ya están cubiertas, y listar las varadas.
Hoy se limpia lanzando `railspec-reindexar` con `retirar_todas`.

**Resuelta cuando.** Una superposición retenida cuyo commit no llega al canónico se retira (o se
avisa y se puede retirar) sin lanzar el workflow a mano, y una prueba cubre el
caso de un commit que el canónico ya pasó.
