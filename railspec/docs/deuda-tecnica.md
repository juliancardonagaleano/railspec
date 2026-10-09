# Deuda técnica

Lo que se sabe que falta y se decidió no hacer todavía. Cada entrada dice qué hay hoy, qué falta y
cómo se sabrá que está resuelta. Se verificó contra `master` el 2026-10-07 (la sección de avisos, el 2026-10-08).

## GitHub Actions no ejecuta ningún job de CI

**Qué hay hoy.** Todos los jobs de `railspec-ci`, `railspec-imagen` y `railspec-binario` fallan en
3 a 5 segundos sin runner asignado (`runner_id: 0`), sin pasos ni logs, en `master` y en cada PR:
es el límite de gasto o la facturación de la cuenta, no el código. Mientras tanto la verificación
es local y cada PR lo dice.

**Resuelta cuando.** Un run de `railspec-ci` en `master` llega a ejecutar sus pasos. El primer fallo
real que cabe esperar entonces son las pruebas que no se han visto en CI (FalkorDB real, `humo.mjs`).

## Avisos e informes: sin probar contra un Teams ni un SMTP reales

**Qué hay hoy (2026-10-08).** Un gate que escala o un presupuesto que se agota avisan por Teams (webhook
entrante cifrado por organización) y por correo (SMTP de la plataforma, `RAILSPEC_SMTP_URL`), y cada semana sale un
informe de unidades cerradas, gates escalados y gasto por tier ([consola.md](consola.md#avisos-e-informes)). Las
pruebas cubren los canales con transportes y SMTP simulados, el cifrado, las reservas, el tope por hora, el
informe y la API; el contrato no cambió. «Informes» no estaba definido: lo mínimo útil es este resumen semanal, no
un generador de informes.

**Qué falta.**

- **Probarlo de verdad** (solo lo puede hacer quien administra Teams y el correo): crear el flujo de Teams, definir
  `RAILSPEC_SMTP_URL` en Render y usar «Enviar aviso de prueba» e «Enviar informe ahora» en la consola. Hasta
  entonces el formato de la tarjeta (Adaptive Card en un mensaje de Flujos de trabajo) no está verificado contra Teams.
- **Hosts de Teams.** La lista de hosts admitidos es fija (nube comercial de Microsoft). Para nubes soberanas o un
  relé propio haría falta una variable de plataforma, como `RAILSPEC_PROVEEDORES_HOSTS`.
- **Umbrales previos.** Solo avisa al agotarse un tope, no al acercarse (80 %). Tampoco avisa de aprobaciones
  pendientes, de unidades varadas ni del tope del chat (que no se comprueba, [motor.md](motor.md#presupuestos-y-telemetría)).
- **Historia del informe.** Cuenta los escalados desde que existen los avisos (el registro dura 120 días) y fecha el
  cierre de una unidad por su último cambio, que integrarla mueve; las unidades se recorren por workspace (hasta
  20 000 por barrido). Sin entrega garantizada: un canal caído deja el intento en «Últimos avisos» y reintenta solo
  el informe.
- **Rotar la clave maestra.** El webhook se cifra con `RAILSPEC_CLAVE_MAESTRA`; tras rotarla sigue legible con
  `RAILSPEC_CLAVE_MAESTRA_ANTERIOR`, pero se reescribe con la nueva solo al volver a guardarlo en la pantalla.

**Resuelta cuando.** Una organización recibe en Teams y en el correo el aviso de prueba, el de un escalado real y el
informe de un lunes, y se confirmó el formato y los destinos.

## Métricas de operación: sin scraper ni alertas en marcha (`/metrics`)

**Qué hay hoy.** El servidor publica `GET /metrics` en texto de Prometheus
(`packages/railspec-server/src/railspec/server/metricas.py`): versión del servidor, versión del
esquema del estado (la del código y la guardada), estado de cada sonda, peticiones por superficie y
clase de estado, e instante de arranque. Exige `Authorization: Bearer <clave>`: una clave por origen
creada en la consola (Plataforma → Operación; revocable, guardada como hash, con su último uso) o
`RAILSPEC_METRICAS_TOKEN` como respaldo; sin ninguna responde 404. La misma pantalla muestra todo eso
en vivo a quien administra la plataforma, sin clave
([consola.md](consola.md#operación-del-servidor-y-claves-de-métricas)). La documentación trae un trabajo
de Prometheus de ejemplo, cómo rotar las claves y cuatro alertas sugeridas.

**Decisión (2026-10-09).** Sin scraper por ahora: en Render gratis, consultar cada minuto mantendría el
servicio despierto todo el mes y consumiría casi todas las 750 horas gratis de la cuenta. Se mira en la
consola y se comprueba a mano con `deploy/prometheus/verificar_metricas.py` y una clave de la consola.

**Qué falta.**

- **Poner un scraper** cuando el despliegue lo justifique (un plan de pago o AKS): crear su clave en
  la consola y apuntar Prometheus (o un servicio gestionado que acepte `Authorization: Bearer`).
- **Crear y probar las alertas.** Las reglas están en `deploy/prometheus/alertas.yaml` (con una
  más, `RailspecSinMetricas`, para un scrape caído) y una prueba verifica su forma y que cada
  métrica que usan la publique el servidor; `deploy/prometheus/verificar_metricas.py <url>` consulta
  un servidor desplegado con una clave y dice si Prometheus lo podría leer (`up == 1`), sin montar
  Prometheus. Falta cargar las reglas en un Prometheus real y dispararlas a propósito una vez.
- **Aceptar los límites.** Los contadores son por réplica y en memoria (se reinician con el proceso,
  también cuando Render duerme el servicio; `rate()` lo tolera y Prometheus suma entre réplicas) y no
  hay métricas de negocio (costo, caché, latencia de los gates): eso sigue saliendo de la consola de
  estadísticas. Las claves no van a la auditoría de ninguna organización (son de la plataforma).

**Resuelta cuando.** Un scraper consulta un servidor desplegado con su clave con éxito (`up == 1`) y
las alertas de [despliegue.md](despliegue.md#esquema-del-estado-y-métricas) están creadas y se
probaron una vez disparándolas a propósito.

## Búsqueda semántica: codificador local hecho; falta decidir si se sube y con qué modelo

**Qué hay hoy (2026-10-08).** El proxy puede calcular embeddings **en local** y mezclarlos con BM25 en
`code_search` ([proxy-local.md](proxy-local.md#búsqueda-semántica-opcional)): `railspec modelo instalar`
(`jina-embeddings-v2-base-code`, 768 dimensiones, hash fijado), `railspec indice --vectores`, `modo` en la búsqueda
y `railspec evaluar-busqueda` para medirla con consultas propias. Es opcional y no envía nada.

**Qué sigue sin existir.** Los vectores no suben al servidor. El contrato y el servidor saben guardarlos y
compararlos (int8, 768, `Embedding.vector_b64`; `graph.query` admite `semantica`), pero el contrato nombra
`nomic-embed-code` (`Literal`) y el modelo local es otro; `codebase-memory-mcp` 0.11 tampoco expone los suyos
(`indexador_cbm.py`), así que los deltas siguen sin embeddings, `Indexador.embedding_consulta` devuelve `None` y el
servidor no cablea un `CodificadorConsulta`. La rebanada de grafo del contexto de spec, plan y tasks sigue buscando
por nombre de símbolo ([motor.md](motor.md#recorrido)).

**Qué falta.**

- Decidir el modelo con evidencia: la medida hecha (12 consultas, un repositorio con nombres en español) dio una
  mejora pequeña de la posición del acierto (MRR 0,26 → 0,38) y ninguna del acierto entre los 5 primeros. Hay que
  correr `railspec evaluar-busqueda` sobre repositorios y consultas reales antes de subir nada.
- Si se sube: cambiar el `Literal` del contrato por el modelo elegido (contrato nuevo, aditivo; proxy antes que
  servidor), calcular los embeddings del delta en el job de CI y cablear el `CodificadorConsulta` del servidor.
- El binario autocontenido no trae `onnxruntime`; hoy solo funciona con `pip install "railspec-local[embeddings]"`.
- Un modelo mejor para código con identificadores en español, o un `prefijo_consulta`/pesos distintos por repositorio.

**Resuelta cuando.** Una medida con consultas reales justifica el modelo, un índice de CI sube embeddings, una
búsqueda con `semantica: true` desde el proxy devuelve resultados por similitud sin avisos, y una prueba contra un
repositorio real lo verifica.

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

## Mandato: notificaciones y lo que el servidor no impone

**Qué hay hoy (2026-10-08).** Los modos supervisado y desatendido descansan en un mandato aprobado por
una persona en la consola, con límites, caducidad y paradas tipificadas ([mandato.md](mandato.md)). Un
mandato caducado, parado, revocado o sin presupuesto retiene las unidades; aprobar de nuevo las reanuda.

**Qué falta.**

- **Nadie se entera.** Una parada, un gate diferido o una caducidad próxima solo se ven si alguien abre la
  consola o el arnés llama a `unit.advance` (la consola avisa de los mandatos parados en el tablero). Falta
  la notificación (correo, Slack, push) y un informe de lo que hizo una unidad desatendida mientras nadie
  miraba. Es la tercera decisión pendiente de Julian (notificaciones e informes).
- **El cupo no es atómico.** `max_unidades` se comprueba al arrancar leyendo las unidades del plan; dos
  `unit.start` simultáneos pueden pasarse en uno.
- **Presupuesto total con retraso.** El consumo de una unidad se suma al estado tras cada panel de
  críticos; el tope del mandato se comprueba al arrancar cada llamada del panel y en cada paso, pero las
  llamadas de dos unidades que corren a la vez no se ven hasta que escriben su consumo. Puede pasarse de
  un panel por unidad.
- **Recuento de `llamadas`.** Hasta 1.11 `Consumo.llamadas` quedaba en 0 y `llamadas_max` no se aplicaba
  en ninguna parte. Ahora cuenta las llamadas que salen al proveedor; las unidades anteriores a 1.11
  parten de 0 y la telemetría anterior no se reconstruye.
- **Una decisión revertida no deshace nada.** `mandate.review` deja constancia; el código lo revierte la
  persona. La revisión pendiente no bloquea cerrar ni integrar la unidad.
- **Snapshot fuera de alcance.** Se guarda (la persona necesita verlo) pero no entra al grafo; queda en el
  almacén hasta su retención normal.
- **Unidades anteriores a 1.11.** Una `supervisado` o `desatendido` con `plan` sin mandato queda retenida
  hasta que se redacte y apruebe un mandato con ese id, o se baje su autonomía. No hay migración.
- **`mandate.review` y bloqueo optimista.** La versión que exige es la del estado de la unidad;
  `UnidadDeMandato` no la trae y la consola la lee justo antes de revisar. Añadir `version` a
  `UnidadDeMandato` lo haría estricto.
- **Probado solo con el arnés simulado.** Ningún mandato ha corrido con un arnés ni un modelo reales.

**Resuelta cuando.** Una unidad desatendida se detiene de noche y avisa por un canal que alguien lee, y un
mandato real de varias unidades corre de punta a punta con un arnés real.

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
