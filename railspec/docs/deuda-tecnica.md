# Deuda técnica

Lo que se sabe que falta y se decidió no hacer todavía. Cada entrada dice qué hay hoy, qué falta y
cómo se sabrá que está resuelta. Se verificó contra `master` el 2026-10-06.

## Métricas de operación sin habilitar (`/metrics`)

**Qué hay hoy.** El servidor sabe publicar `GET /metrics` en texto de Prometheus
(`packages/railspec-server/src/railspec/server/metricas.py`): versión del servidor, versión del
esquema del estado (la del código y la guardada), estado de cada sonda, peticiones por superficie y
clase de estado, e instante de arranque. El endpoint solo existe si `RAILSPEC_METRICAS_TOKEN` está
definido (16 caracteres o más, `config.py`) y exige `Authorization: Bearer <token>`
([despliegue.md](despliegue.md#esquema-del-estado-y-métricas)). Sin la variable no hay endpoint, y
hoy ningún despliegue la define: ni `render.yaml`, ni el ConfigMap, ni el Secret de AKS.

**Qué falta.**

- **Cablear la variable en cada despliegue.** Añadir `RAILSPEC_METRICAS_TOKEN` a `renderizar.py` y a
  `despliegue.md` (en AKS como clave del Secret, no del ConfigMap, porque es una credencial) y a
  `render.yaml`/`despliegue-render.md` (en Render como variable de entorno secreta).
- **Corregir el test de variables del despliegue, que hoy falla en `master`.**
  `deploy/tests/test_despliegue.py::test_toda_variable_que_lee_el_servidor_esta_en_el_despliegue_o_declarada_fuera`
  reporta `RAILSPEC_METRICAS_TOKEN` como variable huérfana: el servidor la lee, pero ni el ConfigMap,
  ni el Deployment, ni `CLAVES_DEL_SECRET`, ni `FUERA_DEL_CONFIGMAP` la cubren. Lo introdujo el PR
  que añadió `/metrics`. Se arregla al cablear la variable (si va en el Secret, se añade a
  `CLAVES_DEL_SECRET`); declararla en `FUERA_DEL_CONFIGMAP` sería la salida corta si se decide
  dejar `/metrics` apagado por ahora, pero el test deja de proteger entonces esa variable.
- **Documentar la autenticación del scraper.** `despliegue.md` nombra el encabezado, pero falta un
  ejemplo completo de cómo se configura el scraper (Prometheus u otro que lea ese formato) con el
  token como credencial, y de cómo rotar el token sin perder la serie.
- **Poner un scraper.** Nadie consulta el endpoint hoy. En Render hay que decidir dónde vive el
  scraper (Prometheus propio, o un servicio gestionado que acepte `Authorization: Bearer`) y cuánto
  cuesta.
- **Definir alertas.** Como mínimo: `railspec_sonda_ok == 0` sostenido, tasa de respuestas 5xx por
  superficie, `railspec_estado_esquema` distinto entre código y almacenado, y reinicios repetidos
  (cambios de `railspec_proceso_inicio_segundos`).
- **Aceptar que los contadores son por réplica y en memoria.** Se reinician con el proceso; el
  scraper debe tratar los reinicios como reinicios de contador (`rate()` lo hace) y sumar entre
  réplicas. No hay métricas de negocio (costo, caché, latencia de los gates): eso sigue saliendo
  de la consola de estadísticas, no de `/metrics`.

**Resuelta cuando.** El despliegue de Render y el de AKS definen el token, el test de variables
pasa, un scraper de ejemplo está documentado y probado contra un servidor real, y hay al menos las
alertas de arriba.
