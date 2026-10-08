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

## Grafo de Render en Postgres: falta verlo contra Supabase real

**Qué hay hoy (2026-10-07).** Julian descartó levantar FalkorDB para el despliegue de Render (un servicio
privado con disco de pago para un grafo de pocos MB) y eligió un motor sobre la base que ya hay. El blueprint
define `RAILSPEC_GRAFO_POSTGRES=true`: `MotorPostgres` guarda el grafo en tablas `grafo_*` del esquema del estado
([grafo.md](grafo.md#motor-en-postgres)), así que `graph.query` y `graph.index` existen en Render, el gate de código
vuelve a ver impacto y trazas `CA-NN`, y spec, plan y tasks reciben su rebanada de grafo. Las pruebas del grafo
(134) y una prueba del servidor de punta a punta pasan contra un Postgres 16 local; con un grafo sintético del
tamaño de este repositorio el impacto hace 6 consultas de aristas, no 446.

**Qué falta.**

- Probarlo contra Supabase real (*session pooler*, IPv4, RLS, latencia de red). Todo se midió en un Postgres local:
  con cada consulta de aristas a 30 ms de ida y vuelta, el impacto de una unidad grande aún cuesta unos segundos
  (6 consultas por nivel y vista, más las de símbolos).
- El grafo empieza vacío en Render y solo lo alimenta `railspec-reindexar`, que necesita `RAILSPEC_URL`,
  `RAILSPEC_OIDC_AUDIENCIA` y `RAILSPEC_OIDC_REPOSITORIOS` en el servicio, y GitHub Actions funcionando (el
  bloqueo de facturación sigue ahí). Sin eso el grafo de Render queda vacío y el servidor avisa de ello.
- Vectores remotos: `knn` compara en Python; con embeddings por símbolo (≈ 3 KB cada uno) no cabría el plan
  gratuito. Depende de la decisión de embeddings locales (arriba).
- El grafo y el estado son dos pools sobre el mismo *session pooler*: no escalar réplicas sin revisar el límite de
  conexiones de Supabase.
- FalkorDB sigue siendo el motor de AKS. No hay migración entre motores; un cambio de motor reindexa desde CI.

**Resuelta cuando.** Una unidad de prueba contra Supabase real recibe una rebanada de grafo en su orden de spec y
su gate de código ve impacto, con el reindexado de CI funcionando.

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

## Superposición retenida: barrido y pantalla hechos; falta probar contra FalkorDB real

**Qué hay hoy (2026-10-07).** El canónico recuerda los commits que cubrieron sus últimos índices
(hasta 1000, el completo reemplaza la lista), así que integrar una unidad en un commit que ya
cubrió se retira al instante. Una retenida cuyo commit nunca llega caduca a los
`RAILSPEC_GRAFO_RETENIDAS_DIAS` (30; `0` no caduca), `graph.query` con `unidad` avisa desde la mitad
del plazo y `GET /consola/api/orgs/{org}/workspaces/{ws}/grafo/retenidas` las lista
([grafo.md](grafo.md#superposición-de-una-unidad-integrada)). `retirar_todas` ya no es la única salida.

**Qué falta.**

- Sin probar contra FalkorDB real (ver arriba): el barrido por su cuenta usa `listar` por prefijo del
  motor, probado en memoria y, si hay `RAILSPEC_PRUEBAS_POSTGRES`, en Postgres.
- El barrido corre cada hora en cada réplica con grafo y no se puede ajustar por variable: el plazo es
  de días y una hora sobra. Si algún día hace falta, es una variable más en el renderizador.

**Hecho (2026-10-07).** `AlmacenGrafo.barrer_retenidas()` retira las vencidas de todos los repositorios
sin esperar un índice y el servidor la llama cada hora desde una tarea de fondo
(`server/api/fondo.py`); la consola las muestra en «Retenidas del grafo»
(`/{org}/{ws}/grafo/retenidas`), con las vencidas primero.

## `contexto.yaml`: la consola lo edita, pero nada lo lee; falta probar el PR contra GitHub real

**Qué hay hoy (2026-10-08).** La consola edita `contexto.yaml` y `.railspecignore` de un repositorio
vinculado (botón «Archivos» en «Repositorios vinculados»): los lee de la rama del vínculo con la
GitHub App como instalación, los valida y propone el cambio como PR, nunca como commit directo
([consola.md](consola.md#archivos-del-repositorio)). Sin las credenciales de instalación o sin
permisos de escritura, entrega el diff para aplicarlo a mano. Las pruebas usan un GitHub simulado
(`httpx.MockTransport`) y firman el JWT de la App con una clave RSA de prueba. El formato de
`contexto.yaml` lo fijó este cambio (`version: 1` y `proveedores`, la forma de `ProveedorContexto`
sin `org` ni `workspace`); no cambia el contrato.

**Qué falta.**

- **Que algo lea `contexto.yaml`.** Ni el motor, ni el proxy local, ni el gate lo consumen: el
  archivo se versiona y se valida, y los proveedores de contexto siguen saliendo de la configuración
  de la consola. Cuando se lea, tendrá que decidir qué gana si declaran lo mismo
  (la configuración de la organización o el archivo), y no deberá dejar que un repositorio fije
  `url` o `credencial_ref` fuera de la allowlist (hoy la consola solo lo avisa, no lo impide).
- **Probar el PR contra GitHub real.** Está sin probar con una App real: el JWT, el token de
  instalación acotado a un repositorio, la rama, el commit y el PR salen de la documentación de la
  API. Quien administra la App tiene que crear su clave privada, ponerle *Contents* y *Pull
  requests* en lectura y escritura, instalarla en los repositorios vinculados y definir
  `RAILSPEC_GITHUB_APP_ID` y `RAILSPEC_GITHUB_APP_CLAVE_PRIVADA`.
- **`.railspecignore` no se revalida al indexar.** El PR puede fusionarse con un patrón que el
  proxy interprete distinto de lo avisado; el servidor solo conoce el subconjunto de gitignore que
  documenta el proxy.
- La consola no ve si el PR se fusionó ni lo vigila: la lectura siguiente muestra la rama.

**Resuelta cuando.** Con una App real instalada, una edición desde la consola abre un PR que se
fusiona, y la lectura siguiente trae el contenido nuevo; y algún componente consume `contexto.yaml`.
