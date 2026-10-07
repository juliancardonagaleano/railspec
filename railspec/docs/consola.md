# Consola web (`railspec-console`)

La consola es un cliente más de `railspec-server`: administra organizaciones,
workspaces, roles y repositorios vinculados; configura perfiles, presupuestos
y proveedores de contexto; muestra estadísticas de costo y de convergencia de
gates; y explora unidades (tablero, DAG de fases, línea de tiempo,
checkpoints, integración, trazabilidad CA-NN), el grafo de código y la
auditoría. Incluye el chat de contexto (fase 8), que habla con `/v1/chat/*`
con un token `rsc1` y no con `/consola/api`.

Nunca muestra código, y lo garantiza la API, no solo la SPA: de una orden solo
salen sus metadatos (nunca instrucciones, plantilla, contexto ni comando de
validación), de un hallazgo no salen `evidencia` ni `propuesta` (texto libre de
los críticos), del estado tampoco `comando_validacion`, de un snapshot solo
rutas y símbolos (la capa de datos ni carga `diff` ni `fragmentos`), y del
grafo solo nombres, rutas y relaciones. Todo es lista blanca (`vistas.py`):
un campo nuevo del contrato no sale hasta que se agregue allí. Lo que sí sale
es texto escrito por personas o por el arnés (`pedido`, `titulo`, comentarios,
motivos, la `pregunta` de un checkpoint) y los títulos de hallazgos. `/v1` y
MCP no pasan por estos filtros: el arnés necesita la orden completa.

## Piezas

| Pieza | Dónde |
| --- | --- |
| SPA (React + TypeScript, Vite, TanStack Router y Query, Tailwind, React Flow, Sigma.js, ECharts) | `railspec/packages/railspec-console/` |
| API de la consola (`/consola/api`) | `railspec/packages/railspec-server/src/railspec/server/consola/` |
| Pruebas de la API | `railspec-server/tests/test_consola*.py` |
| Prueba contra Mongo y FalkorDB reales | `railspec/integracion/tests/test_consola_e2e.py` |

El servidor sirve la SPA compilada en `/consola/` (mismo origen que la API,
sin CORS) cuando `RAILSPEC_CONSOLA_DIR` apunta a su `dist`; la imagen de
`railspec/deploy/servidor/Dockerfile` la compila en una etapa con Node y la
deja en `/opt/railspec-console`. Las rutas del cliente vuelven a
`index.html`; los archivos de `assets/` llevan caché inmutable.

## GitHub App

El inicio de sesión es el flujo web de OAuth de una GitHub App (usuarios de la
App). Configuración de la App en GitHub (Settings → Developer settings →
GitHub Apps):

- **Callback URL**: `https://<dominio>/consola/api/auth/github/callback`.
- **Request user authorization (OAuth) during installation**: no hace falta.
- **Enable Device Flow**: sí, lo usa `railspec login` para que el arnés consiga su token
  ([proxy-local.md](proxy-local.md#iniciar-sesión-con-github), con la decisión sobre la
  expiración de los tokens de usuario).
- **Permisos**: Organization → Members: *Read-only* (equipos del usuario para
  roles por equipo). Sin ese permiso la consola y el arnés funcionan, pero solo
  resuelven roles asignados a personas.
- Instalarla en la organización de GitHub cuyos equipos se usen en roles.

En el inicio de sesión, el token de usuario de GitHub solo se usa dentro del
callback (leer el usuario y sus equipos); no se guarda y al terminar se revoca
en GitHub. Cuando alguien presenta su propio token de GitHub (el del arnés por
MCP, o un script) el servidor comprueba con las credenciales de la App que lo
emitió ella, y lo usa para identificarlo y leer sus equipos (`GET /user/teams`,
el mismo permiso de arriba, o el alcance `read:org` en un token OAuth); guarda
el resultado cinco minutos bajo el hash del token y nunca el token.

## Variables de entorno

Las que Julian debe suministrar:

| Variable | Dónde | Uso |
| --- | --- | --- |
| `RAILSPEC_GITHUB_APP_CLIENT_ID` | Secret | Client ID de la GitHub App. Sin ella no hay botón "Entrar con GitHub". |
| `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | Secret | Client secret de la App. |
| `RAILSPEC_CONSOLA_SECRETO` | Secret | Clave HMAC de sesiones y tokens (p. ej. `openssl rand -base64 48`), **mínimo 32 caracteres**. Obligatoria con URL pública https o con GitHub App: sin ella, o con una más corta, el servidor no arranca (el `state` de OAuth que entrega `/auth/github/inicio` es texto conocido más su MAC: una clave débil se rompe sin conexión y permite forjar sesiones). Solo en desarrollo (URL http local, sin GitHub App) se puede omitir: clave efímera con aviso, que no sobrevive a un reinicio ni se comparte entre réplicas. |
| `RAILSPEC_CONSOLA_ADMINS` | ConfigMap (`renderizar.py`) | `github_id` numéricos, separados por coma, que administran la plataforma: crean organizaciones y son `org-admin` en todas (p. ej. `1234567`; el id numérico de tu usuario sale de `GET https://api.github.com/users/<login>`). |
| `RAILSPEC_CONSOLA_URL` | ConfigMap (lo deriva el render de `RAILSPEC_DOMINIO`) | URL pública: base de la redirección de OAuth y cookie `Secure`. Obligatoria con GitHub App. |
| `RAILSPEC_CONSOLA_DIR` | Imagen | Carpeta de la SPA compilada; la imagen ya la fija. |
| `RAILSPEC_CONSOLA_AUTH_LIMITE` | ConfigMap (`renderizar.py`) | Peticiones por minuto y por IP en `/consola/api/auth/*` (60 por defecto; 0 lo desactiva). 429 con `Retry-After` al pasarse. |
| `RAILSPEC_CONSOLA_SESION_HORAS` | ConfigMap (`renderizar.py`) | Vida de la sesión en horas (4 por defecto, de 1 a 24). Es también la ventana en que quedan congelados los equipos de GitHub de la cookie. |
| `RAILSPEC_CONSOLA_SSE_MAX_USUARIO` | ConfigMap (`renderizar.py`) | Flujos de eventos en vivo abiertos a la vez por persona y por réplica (5 por defecto); al exceder, 429. |
| `RAILSPEC_CONSOLA_SSE_MAX_GLOBAL` | ConfigMap (`renderizar.py`) | Ídem en total por réplica (200 por defecto). |
| `RAILSPEC_CONSOLA_SSE_REVALIDAR_S` | ConfigMap (`renderizar.py`) | Cada cuántos segundos un flujo vuelve a comprobar el rol `lector` y se cierra si lo perdió (30 por defecto). |
| `RAILSPEC_PROVEEDORES_HOSTS` | ConfigMap (`renderizar.py`) | Hosts permitidos para los proveedores de contexto que configura una organización (`host`, `*.dominio`, coma); se suma el de `RAILSPEC_PCE_URL`. Vacía: ninguno ([proveedores.md](proveedores.md#herramientas-de-contexto)). |
| `RAILSPEC_VINCULOS_OWNERS` | ConfigMap (`renderizar.py`) | Owners de GitHub (coma) que puede vincular una organización que **no** tiene `github_org`. Vacía (por defecto): esas organizaciones no pueden vincular repositorios. Una organización con `github_org` solo vincula repositorios de ese owner, con o sin esta variable. |

`RAILSPEC_TOKENS_DESARROLLO` (ya existente) habilita además el inicio de sesión
con token de desarrollo y **sustituye por completo** la identidad de GitHub.
Es solo para máquinas de desarrollo: el servidor se niega a arrancar con ella
si hay `RAILSPEC_MONGO_URI` o GitHub App, salvo `RAILSPEC_PERMITIR_DESARROLLO=1`
(que deja un WARNING), y el Deployment de `railspec/deploy/` la fuerza vacía.
`GET /auth/config` solo anuncia `desarrollo: true` con el modo permitido.

## Primer arranque

1. Desplegar con las variables de arriba (ver `despliegue.md`).
2. Entrar con GitHub como alguien de `RAILSPEC_CONSOLA_ADMINS`.
3. Administración → crear la organización, luego sus workspaces.
4. Asignar roles (`org-admin` a nivel organización; `workspace-admin`,
   `desarrollador` o `lector` por workspace) a personas, por login, o a
   equipos de GitHub, por su id numérico (`GET /orgs/{org}/teams/{slug}` en
   la API de GitHub lo da).
5. Vincular repositorios al workspace (nivel `restringido` por defecto; el nivel solo
   decide qué material de código se comparte con el modelo, no qué proveedor se usa). La URL
   es `https://github.com/<owner>/<repo>` y el owner, el `github_org` de la
   organización (ver «Vínculos de repositorio»); antes, la plataforma tiene que
   fijar ese `github_org` al crear o editar la organización.

## Sesión, tokens y anti-CSRF

- **Cookie** `railspec_sesion`: HttpOnly, SameSite=Lax, `Path=/consola`,
  `Secure` con URL https. Con https se llama `__Secure-railspec_sesion` (el
  navegador la rechaza si no es `Secure`; `__Host-` exigiría `Path=/`).
  Formato `rsc1.<carga>.<firma HMAC-SHA256>`; lleva login, `github_id`,
  equipos de GitHub, el id de la sesión y la expiración. La carga trae el tipo
  (`t`: `sesion`, `api` u `oauth`) y la audiencia (`aud`), y cada tipo se firma
  con su propia subclave (HKDF-SHA256 del secreto): el `state` de OAuth, que es
  público, no sirve de cookie ni de token `api`, y el servidor siempre abre un
  token exigiendo el tipo que espera.
- **Anti-CSRF**: con cookie, toda petición que no sea GET exige la cabecera
  `X-Railspec-Consola: 1` (un formulario de otro sitio no puede ponerla).
- **Token para `/v1/*`**: `POST /consola/api/auth/token` devuelve
  `{"token": "rsc1…", "expira_en": ISO}`, válido una hora (nunca más que la
  sesión que lo pide) como `Authorization: Bearer` en `/v1/tools` y en las
  rutas que resuelven el actor con la identidad del servidor
  (`identidad.actor_desde_token(token, "consola")`): Actor humano con
  `github_id`, login y canal `consola`. Es el que usa el chat. Solo se emite con
  la **cookie de sesión** y la cabecera anti-CSRF: un `Bearer` (token `api`, de
  GitHub o de desarrollo) recibe 401, de modo que una fuga del token `api` no
  da acceso indefinido (no se renueva solo ni sobrevive a la sesión). Lleva
  `aud: "v1"` y vale únicamente con canal `consola` (`/v1/tools`, `/v1/chat`);
  no es credencial del arnés (`/mcp`) ni de la API de la consola. Un token de
  sesión (cookie) no vale como Bearer.
- **Bearer en la consola (scripts)**: `/consola/api` acepta
  `Authorization: Bearer` con un **token de GitHub o de desarrollo**, no con
  `rsc1`; sin cookie no hace falta la cabecera anti-CSRF. El token de GitHub
  tiene que ser de la GitHub App de Railspec: se comprueba con las credenciales
  de la App (`POST /applications/{client_id}/token`), así que uno personal (PAT)
  o de otra OAuth app se rechaza con 401, y si GitHub no responde, con 503
  (falla cerrado). Sin GitHub App configurada se rechaza todo token de GitHub,
  salvo con `RAILSPEC_PERMITIR_DESARROLLO=1`. Con token de GitHub los equipos
  cuentan igual que en la sesión (se releen al vencer la caché de cinco
  minutos); los tokens de desarrollo no tienen equipos. Un script que ya tiene
  un token de GitHub lo usa directamente (en `/v1` y en `/consola/api`): no
  necesita `POST /auth/token`. El token `rsc1` `api` no vale en `/consola/api`
  (ni en lecturas ni en escrituras): la SPA usa la cookie y solo manda el token
  al chat.
- **Cerrar sesión** (`POST /auth/salir`, con la cabecera anti-CSRF como toda
  escritura) borra la cookie y además **revoca** la sesión: cookie y tokens `api`
  llevan el id de su sesión (`sid`) y el servidor lo guarda en la colección
  `sesiones_revocadas` de Mongo (índice TTL: el registro desaparece cuando la
  sesión habría expirado), que consulta cada réplica al validar. Una cookie
  copiada o un token `api` pedido antes del cierre dejan de valer en el acto.
  Solo se cierra esa sesión (no las demás de la misma persona).
- **Tras el callback de OAuth** el token de usuario de GitHub se revoca
  (`DELETE /applications/{client_id}/token`, mejor esfuerzo: si GitHub no
  responde, el inicio de sesión sigue).
- **Vida de la sesión**: 4 horas por defecto (`RAILSPEC_CONSOLA_SESION_HORAS`,
  de 1 a 24). Es también cuánto quedan congelados los equipos de GitHub de la
  cookie (ver límites).

### Límites conocidos

- **Equipos de GitHub congelados.** Los equipos se leen una vez, al iniciar
  sesión, y viajan en la cookie y en los tokens `api` que salen de ella. Quitar
  a alguien de un equipo en GitHub no le quita el rol heredado de ese equipo
  hasta que la sesión expire (como mucho `RAILSPEC_CONSOLA_SESION_HORAS`) o se
  cierre. No se pueden refrescar sin guardar el token de GitHub del usuario, y
  la consola decide no guardarlo. Quitar una *asignación* de rol en Railspec sí
  surte efecto de inmediato: los roles se leen de la base en cada petición.
- **Equipos y el inicio de sesión.** Si GitHub falla justo al leer los equipos
  durante el inicio de sesión, la sesión nace sin equipos y así sigue hasta que
  expire o se vuelva a iniciar; el arnés, que los relee, no tiene ese límite.
- **Administradores de plataforma.** `RAILSPEC_CONSOLA_ADMINS` es configuración
  del ConfigMap: añadir o quitar a alguien exige cambiar el ConfigMap y
  reiniciar las réplicas (no hay edición desde la consola). Tras el reinicio la
  baja es inmediata, porque la cookie no lleva el permiso: se compara con la
  configuración en cada petición.
- **Revocación solo de lo que emite la consola.** Cierra la cookie y los
  tokens `api` de la sesión. Los tokens de GitHub o de desarrollo que un script
  mande como `Bearer` se invalidan donde nacen (GitHub, `RAILSPEC_TOKENS_DESARROLLO`).
  No hay (todavía) una operación de "cerrar todas las sesiones de una persona".
- **Cambio de formato.** Las cookies y los tokens `rsc1` emitidos por versiones
  anteriores dejan de valer al desplegar esta (subclaves por tipo): hay que
  volver a iniciar sesión una vez.

## Cabeceras de seguridad y límite de peticiones

El servidor las pone él mismo (`consola/seguridad.py`, middleware ASGI), sin
depender del ingress, en toda respuesta bajo `/consola`: la SPA, sus estáticos,
las redirecciones y la API, incluidos los 401 y 404.

| Cabecera | Valor |
| --- | --- |
| `Content-Security-Policy` (SPA) | `default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'`. Revisada contra el build de Vite: `index.html` solo trae un `<script type="module" src>` y un `<link>` del propio origen, sin scripts ni estilos en línea; `'unsafe-inline'` va solo en estilos (React y las librerías de gráficos fijan estilos en los elementos). Sin `eval`: no añadir librerías que lo necesiten. |
| `Content-Security-Policy` (API) | `default-src 'none'; frame-ancestors 'none'` |
| `X-Frame-Options` | `DENY` |
| `X-Content-Type-Options` | `nosniff` |
| `Referrer-Policy` | `no-referrer` |
| `Strict-Transport-Security` | `max-age=31536000`, solo con `RAILSPEC_CONSOLA_URL` https |
| `Cache-Control: no-store` | En `/consola/api/auth/*` (sesión y token); lo que ya fija cada ruta se conserva |

**Límite en `/consola/api/auth/*`**: ventana deslizante de un minuto por IP, en
memoria y por réplica (con N réplicas el tope efectivo es N veces el
configurado). La IP es la que resuelve uvicorn a partir de
`FORWARDED_ALLOW_IPS`; con el `*` del ConfigMap un cliente puede falsear
`X-Forwarded-For` y evadir el límite (ver el riesgo en `despliegue.md`).


## Autorización

Rol efectivo en un workspace = el mayor entre las asignaciones de la persona y
de sus equipos de GitHub, a nivel organización o de ese workspace
(`lector` < `desarrollador` < `workspace-admin` < `org-admin`). La consola
oculta lo que el rol no permite, pero decide el servidor.

| Acción | Rol mínimo |
| --- | --- |
| Ver unidades, estadísticas, auditoría, grafo, configuración | `lector` |
| Aprobar checkpoints, integrar, cambiar modo, arrancar unidades (tools) | el `rol_minimo` de la tool (`desarrollador`) |
| Editar workspace, vínculos, roles del workspace, configuración del workspace (**salvo relajar la política**, abajo) | `workspace-admin` |
| Fijar la `url` y la `credencial_ref` de un proveedor de contexto (y crearlo) | `org-admin`; el `workspace-admin` edita el resto del proveedor |
| Relajar la política de código: bajar `nivel_codigo`, habilitar `chat_contexto_codigo.permitido`, subir los presupuestos de fuga o `huella_tokens_n`, ampliar `modelos_permitidos` o `fragmentos_en_respuesta`, o crear un vínculo menos restrictivo que el por defecto; siempre con motivo | `org-admin` |
| Crear workspaces, roles `org-admin`, configuración de la organización | `org-admin` |
| Crear organizaciones; fijar o cambiar su `github_org` | administrador de la plataforma |

Las tools llamadas desde la consola (`POST /consola/api/tools/{nombre}`) pasan
por el mismo registro que MCP y `/v1/tools`, y los roles de equipo valen igual
por las tres vías: la identidad del token devuelve un actor con los equipos de
GitHub de la persona y el autorizador los suma a las asignaciones personales
(la misma consulta que usa la consola). La regla de qué asignaciones cuentan y
cuál gana es una sola función (`api/roles.py`, `rol_efectivo`) que usan los
dos autorizadores (el del arnés y el de la consola) y el chat, y una prueba
(`test_roles_paridad.py`) compara ambos sobre la misma matriz de personas,
equipos y alcances.

Una diferencia es deliberada: **administrar la plataforma**
(`RAILSPEC_CONSOLA_ADMINS`) solo vale en la consola, donde actúa como `org-admin`
en toda organización. Por el arnés (`/mcp` y `/v1`, también con el `rsc1` de esa
persona) no da ningún rol: para arrancar unidades ahí hace falta una asignación
como a cualquiera, de modo que nadie gana permisos por el arnés que no tuviera
ya. La consola sí permite asignársela (es `org-admin`).

Una tool negada por rol responde `fuera-de-alcance` con el rol que pide, el que
tienes y, si no hay rol y el token no trae equipos, que solo cuentan los roles
asignados a la persona.

| Token | Equipos |
| --- | --- |
| `rsc1` de `POST /consola/api/auth/token` (SPA y chat) | Los del login, firmados en el token. |
| Token de GitHub (el del arnés por MCP, scripts) | `GET /user/teams` con ese mismo token, cacheado cinco minutos. Sin permiso o sin respuesta de GitHub, ninguno: el token sigue valiendo y cuentan las asignaciones personales. |
| Token de desarrollo | Ninguno: asignar el rol como persona. |
| OIDC de Actions | No aplica: el alcance lo fija la tool. |

Leer los equipos falla cerrado (sin permiso o sin respuesta de GitHub no hay
equipos) pero solo se recuerda una respuesta definitiva: equipos leídos, o
GitHub diciendo que este token no puede verlos (401, 403, 404). Un fallo
pasajero (429, 5xx, límite de tasa, red) no se guarda, de modo que quien
trabaja desde el arnés recupera sus roles de equipo en la siguiente llamada y
no a los cinco minutos; lo que se devuelva mientras tanto nunca es más que lo
que GitHub confirmó.

Los equipos no se guardan en el estado ni en la auditoría. Un token `rsc1`
vencido o con la carga alterada no es actor (401), y un rol de equipo vale solo
en la organización y el workspace en que se asignó. Un cambio de membresía se
nota al renovar el token de GitHub (cinco minutos) o al volver a iniciar
sesión (el `rsc1` conserva los equipos del login).

## Suscripciones de modelos

Configuración → Suscripciones registra las conexiones de la organización a
Foundry y a Anthropic (contrato 1.6; reglas, cifrado y política de datos en
[proveedores.md](proveedores.md#suscripciones-de-modelos)). Cada tarjeta muestra
el proveedor, el endpoint, la región y la zona, si tiene clave, el último
resultado de lectura y los perfiles que la usan.

- **Formulario.** Proveedor, nombre (de él sale el id), endpoint, proyecto,
  región, zona de datos y autenticación. La clave es de solo escritura: el
  campo nunca se rellena con la guardada y, al editar, vacío significa «conservar».
  Cambiar el endpoint pide la clave otra vez. Solo un `org-admin` edita; los demás
  roles ven la lista.
- **Compatibles (contrato 1.8).** Con el proveedor «Compatible» el formulario pide un
  servicio (OpenCode Zen, MiniMax) cuyos endpoints fija el servidor, o
  «Personalizado» con al menos un endpoint propio (chat completions de OpenAI o
  mensajes de Anthropic). Sirven solo a repositorios `abierto`: el formulario y el
  editor de perfiles lo avisan, y el effort queda deshabilitado. Declarar un
  modelo a mano pide su protocolo y, si se sabe, su tarifa en USD por millón de
  tokens (entrada y salida juntas); sin tarifa el costo cuenta 0 USD.
- **Descubrir modelos.** Lee los modelos del proveedor. Si falla, la pantalla
  muestra el código y el detalle sin tocar la elección anterior. Cada modelo
  aparece con su región y su SKU (datos de auditoría: ya no deciden a qué repositorios
  sirve); se marcan
  los que quedan disponibles y se guarda. Un despliegue que la API no lista se
  declara a mano y los marcados que ya no aparecen quedan `ausente`.
- **Perfiles.** El editor de perfiles pide la suscripción y limita los modelos de
  cada rol a los elegidos en ella.
- **Sin clave maestra.** Si el servidor no tiene `RAILSPEC_CLAVE_MAESTRA`, la
  pestaña lo dice y no deja guardar claves.
- **Auditoría.** Cada cambio queda como `cambio-configuracion` con
  `entidad: suscripcion` y la acción (`crear`, `editar`, `borrar`, `descubrir`,
  `seleccionar-modelos`, `declarar-modelo`, `retirar-modelo`). Nunca lleva la clave,
  solo si se escribió (`clave_escrita`).

## Catálogo de modelos

Configuración → Catálogo de modelos muestra, por proveedor, si el servidor lo
tiene (`configurado`), de dónde lee (`declarados`, `proyecto` de Foundry o
`api`), cuántos modelos hay guardados para la organización y cuándo se
leyeron, y cómo salió el último intento. Un `org-admin` puede sincronizar todos
los proveedores o uno solo. No pide ni guarda credenciales: usa las del
servidor (`RAILSPEC_FOUNDRY_*`, `RAILSPEC_ANTHROPIC_*`), y la lectura es la que
ya describe [proveedores.md](proveedores.md#catálogo-de-modelos).

- **Estado persistido.** Cada lectura real deja un documento por
  organización y proveedor en la colección `catalogo_estado` (`_id`
  `org/proveedor`), también las automáticas que dispara `unit.start` al caducar
  el TTL; así es el mismo en todas las réplicas. Lleva el resultado, quién
  sincronizó (o `automatica`), cuántos modelos rigen, `leido_en` y, si falló,
  `{codigo, detalle}`. No lleva URLs, credenciales ni cuerpos de respuesta.
- **Un fallo no borra nada.** Si un proveedor falla, la organización conserva su
  catálogo anterior (el último leído o el guardado en Mongo) y el estado dice
  que falló y por qué. Los códigos de error son los de
  [proveedores.md](proveedores.md#catálogo-de-modelos).
- **Reutilización.** Dos sincronizaciones seguidas del mismo proveedor llaman una
  sola vez a su API (`reutilizada: true` si se aprovechó una lectura de hace
  menos de 30 s, aunque la haya hecho otra organización).
- **Auditoría.** Cada sincronización queda en el workspace reservado `org` como
  `cambio-configuracion` con `entidad: catalogo`, `accion: sincronizar`,
  `resultado` (`ok`, `parcial` o `error`), el total de modelos y el resultado por
  proveedor (`foundry: ok`, `anthropic: error:autenticacion`), también cuando falla.
- **Sin credenciales reales, sin inventar.** La forma de la respuesta del proyecto
  de Foundry sigue sin verificarse contra un recurso real: si no coincide con la
  esperada, el estado dice `forma` en vez de mostrar un catálogo vacío.

## Vínculos de repositorio

- **URL**: exactamente `https://github.com/<owner>/<repo>` (con `.git` o `/` finales
  opcionales). Se rechazan otros hosts, credenciales en la URL, puerto, consulta,
  fragmento, segmentos de más y los nombres `.` y `..`. `ClonesGit` usa
  `<owner>/<repo>` como ruta bajo `RAILSPEC_CHAT_CLONES` y además comprueba que la
  ruta resuelta no salga de esa carpeta (tampoco por enlaces simbólicos: los clones
  deben ser directorios reales dentro de ella).
- **Owner**: tiene que ser el `github_org` de la organización (sin distinguir
  mayúsculas). Falla cerrado: si la organización no tiene `github_org`, solo vale un
  owner de `RAILSPEC_VINCULOS_OWNERS`; sin ninguno de los dos, el vínculo se rechaza
  (422) con el motivo. Un `org-admin` no puede cambiar `github_org` (403): lo fija la
  plataforma, porque los clones son compartidos por owner/repo y quien eligiera su
  owner a gusto podría apuntar al clon de otro tenant.
- **Política (`nivel_codigo`, `chat_contexto_codigo`)**: el nivel solo decide qué material
  de código se comparte con el modelo; **no decide qué proveedor, modelo o región se usa**
  (decisión del 2026-10-06). Endurecer sigue siendo del `workspace-admin` (bajar
  `huella_tokens_n` o los presupuestos de fuga, apagar el chat, acotar los modelos).
  Relajar cualquiera de esos campos exige `org-admin` (o la plataforma, que actúa como
  tal) y un `motivo` no vacío; si no, 403 o 422. Un vínculo nuevo se compara con la
  política por defecto de `restringido`: crearlo en `interno` o `abierto` (o con una
  política más laxa) también es relajar; si no, bastaría desvincular y volver a crear
  para esquivar el cambio de nivel. `chat_contexto_codigo.hosting` y
  `Workspace.zona_datos_azure` son datos informativos heredados: cambiarlos ya no relaja
  nada ni exige motivo (el cambio queda en la auditoría). El nivel de una unidad ya
  creada no cambia con el del vínculo: se congela en `unit.start`. El motivo viaja en
  `motivo` del cuerpo (`PUT …/repositorios/{repo}` y `PUT …/workspaces/{ws}`). La SPA solo
  pide motivo al cambiar el nivel; el resto de relajaciones desde la SPA devuelve el error
  del servidor.
- **Auditoría del diff**: cada `PUT` de vínculo deja el campo, el valor anterior y el nuevo
  como `cambio_<campo>: "antes -> después"` (`cambio_chat_huella_tokens_n: "12 -> 24"`),
  `relaja` con los campos que relajan y el `motivo`. El cambio de nivel sigue siendo el
  evento `cambio-nivel` (`de`, `a`, `motivo`) con el diff del resto de la política; los
  demás van en `cambio-configuracion`. Igual para `zona_datos_azure` al editar un
  workspace (`cambio_zona_datos_azure`).
- **Límite**: los vínculos guardados antes de esta regla no se revalidan contra
  `github_org` al leer código (solo se descartan los de forma inválida). Conviene
  revisar `GET …/repositorios` de cada organización al desplegar.

## Aprobaciones e integración (R7)

Aprobar un checkpoint desde la consola es opcional y nunca bloquea al
desarrollador: es la misma operación que `unit.approve`, gana la primera
resolución que llegue por cualquier canal y la segunda recibe
`checkpoint-ya-resuelto` (409). Integrar (`unit.integrate`) solo existe tras
el cierre y no lo condiciona. El estado registra el canal `consola`.

El diálogo de integrar pide la especificación viva, admite la URL del PR y
pide el `commit_integrado` (sha completo del commit resultante en la rama por
defecto, contrato 1.4). Con él el servidor retiene la superposición de la
unidad en el grafo hasta que el índice canónico llegue a ese commit (ver
`grafo.md`, «Superposición de una unidad integrada»); sin él la descarta al
integrar y las consultas de la unidad dejan de ver su código hasta el
siguiente índice.

Como omitirlo en silencio anulaba esa retención, el diálogo no lo deja pasar:

- Al abrirse pide a `GET …/unidades/{u}/commit-integrable` la **punta de la
  rama por defecto del repositorio primario** en el clon canónico del servidor
  (`RAILSPEC_CHAT_CLONES`, el mismo que lee `code.read`) y la deja escrita en el
  campo, editable. La respuesta lleva `repositorio`, `rama`, `commit`,
  `commit_indexado` (hasta dónde llegó el índice del grafo, si se puede leer) y
  `motivo` cuando no hay `commit` (`sin-clones`: el servidor no tiene carpeta de
  clones; `sin-clon`: no hay clon de ese repositorio o no se pudo leer;
  `sin-vinculo`).
- Es una **sugerencia, no una verdad**: el CronJob de clones la actualiza cada
  cierto tiempo y puede ir por detrás del merge. Un commit anterior al merge
  libera la superposición antes de tiempo, o la deja retenida si el índice ya lo
  pasó (límite conocido de `grafo.md`). Por eso el texto de ayuda pide
  confirmarla solo si ya incluye el merge, o pegar el sha del merge o squash. El
  proxy local, que sí hace `git fetch`, sigue calculándolo solo.
- Sin commit (sin clon, sugerencia borrada o consulta fallida) el botón queda
  bloqueado hasta pegar uno o marcar «Integrar sin conservar el grafo de la
  unidad»: renunciar es una decisión explícita y entonces no viaja
  `commit_integrado`. Marcarlo no cambia lo que hace el servidor (descartar al
  integrar); solo evita que ocurra sin que nadie lo haya elegido.

La integración por la API (`POST /tools/unit.integrate`) no cambia: el
servidor sigue aceptando la tool sin `commit_integrado` (el contrato lo declara
opcional) y la sugerencia solo la usa el diálogo.

## Auditoría

Toda escritura de administración y configuración queda en `auditoria` con su
actor: `cambio-configuracion`, `cambio-nivel` (con nivel anterior, nuevo y
motivo obligatorio; los cambios de política llevan su diff, ver «Vínculos de
repositorio») y `desvinculo-repositorio` (motivo obligatorio; borra
vínculo, grafo y snapshots del repositorio). Los cambios a nivel organización se
auditan en el workspace reservado `org` (`org`, `administracion` y
`configuracion` no se pueden usar como nombre de workspace); se ven en `/consola/api/orgs/{org}/workspaces/org/auditoria`
como `org-admin`.

## API

Prefijo `/consola/api`, JSON. Errores propios: `{"detalle", "errores"?}`;
errores de tools: `ErrorTool` (`{"codigo", "detalle", "version_estado"?}`).
Entidades de configuración = JSON del contrato (`railspec/schemas/v1`), con
`version` para bloqueo optimista: cada escritura manda la versión que editó
(ninguna al crear); un conflicto responde 409 con `version_actual`.

En la SPA, cada guardado que acepta el servidor se anuncia como «Guardado ·
versión N · hora» (y «Guardado con avisos:» con la lista, en los perfiles). Lo
escribe `useGuardar` en la caché de consultas bajo una clave por pantalla
(`lib/mutaciones.tsx`, `useGuardado(clave)`), no en el estado del componente:
los formularios se remontan al subir la versión (`key={version}`) y los diálogos
se cierran al guardar, y el aviso sigue visible en el formulario nuevo o en la
tarjeta que abrió el diálogo. Empezar otro guardado lo borra y la caché lo descarta
cinco minutos después de que nadie lo mire; los borrados y el
catálogo (que ya resume su sincronización) no lo usan.

Un 409 también sube la versión: la vista recarga y el formulario se remonta con
lo vigente, y «Otra persona modificó este registro mientras lo editabas (versión actual N)» sigue ahí.
`useGuardar` deja el 409 en la caché bajo la misma clave (`["conflicto", clave]`)
y `error` lo devuelve aunque la mutación que lo recibió ya no exista, así que
`<ErrorGuardado error={guardar.error} />` basta. Los cambios sin guardar de la
persona se pierden con la recarga (el aviso lo dice: «cierra, revisa y vuelve a
guardar»). El aviso se retira cuando empieza otro guardado, sea cual sea su
resultado, y cuando el formulario se va de verdad: al desmontarse, si en el turno
siguiente nadie lo lee, se descarta. El remontaje por versión monta al sustituto
en el mismo ciclo y lo conserva; cerrar el diálogo, cambiar de pestaña de perfil
o salir de la pantalla no, así que no reaparece la próxima vez. Solo se guardan
los 409 (los únicos que recargan la versión) y solo con `aviso`. Los diálogos de las
listas (organizaciones, workspaces, repositorios vinculados y proveedores de contexto)
recuerdan solo el identificador del registro que se edita y buscan el registro en la
lista viva: tras un 409 la lista se recarga, el formulario se remonta con la versión
vigente (`key={version}`) y el siguiente guardado la envía. Si otra persona borra el
registro mientras tanto, el diálogo se cierra.

| Método y ruta | Qué hace |
| --- | --- |
| `GET /auth/config` | Métodos de inicio de sesión disponibles (`github`, `desarrollo`). |
| `GET /auth/github/inicio?volver=` | Redirige a GitHub; `volver` es una ruta interna de la SPA. |
| `GET /auth/github/callback` | Lo llama GitHub; pone la cookie y vuelve a la SPA. |
| `POST /auth/desarrollo` `{token}` | Sesión con token de desarrollo. |
| `POST /auth/salir` | Borra la cookie y revoca la sesión y sus tokens `api`; exige `X-Railspec-Consola: 1`. |
| `POST /auth/token` | Token `rsc1` de una hora para `/v1/*`; exige la cookie de sesión y `X-Railspec-Consola: 1`. |
| `GET /yo` | Persona, si administra la plataforma, organizaciones y workspaces visibles con su rol. |
| `GET /tools`, `POST /tools/{nombre}` | Registro único de tools por la superficie HTTP, canal `consola`, solo la lista blanca `unit.list`, `unit.status`, `unit.approve`, `unit.integrate`, `unit.set_mode`, `unit.start`, `telemetry.query`, `graph.query`; cualquier otra (`unit.export`, `unit.import`, `insumo.get`…) responde 403 `fuera-de-alcance` (404 si no existe). La salida va filtrada: `unit.status` devuelve la orden vigente como resumen (sin instrucciones, plantilla, contexto ni comando de validación) y las que devuelven el estado lo sirven sin evidencia ni propuesta. |
| `GET/POST /orgs`, `PUT /orgs/{org}` | Organizaciones. |
| `GET/POST /orgs/{org}/workspaces`, `PUT /orgs/{org}/workspaces/{ws}` | Workspaces. |
| `GET/POST /orgs/{org}/roles?workspace=`, `DELETE /orgs/{org}/roles/{id}` | Roles; el sujeto puede ir por login (`{"tipo": "usuario", "login": "ana"}`). La última asignación `org-admin` no se puede quitar (409), salvo por quien administra la plataforma. |
| `GET /orgs/{org}/workspaces/{ws}/repositorios`, `PUT …/repositorios/{repo}`, `DELETE …/repositorios/{repo}?motivo=` | Vínculos. Sin `chat_contexto_codigo` se usa la política por defecto del nivel. La URL es `https://github.com/<owner>/<repo>` del `github_org` de la organización (422 si no). |
| `GET /orgs/{org}/suscripciones` | Suscripciones de la organización, sin claves, y si el cifrado está disponible (`cifrado.disponible`). Cualquier rol de la organización. |
| `GET/PUT/DELETE /orgs/{org}/suscripciones/{id}` | Una suscripción. `PUT` crea (sin `version`) o edita (con `version`); la clave va en `clave` y nunca se devuelve; 409 por versión o si hay perfiles que la usan al borrar. `org-admin`. |
| `POST /orgs/{org}/suscripciones/{id}/descubrir` | Lee los modelos del proveedor. 200 con la suscripción actualizada, o 502 con `codigo` y `detalle` si el proveedor falla. `org-admin`. |
| `PUT /orgs/{org}/suscripciones/{id}/modelos` `{seleccionados, version}` | Fija los modelos disponibles. `POST` `{modelo, despliegue, sku, …, version}` declara uno a mano y `DELETE …/modelos/{clave}?version=` lo retira. `org-admin`. |
| `GET /orgs/{org}/catalogo` | Catálogo de modelos guardado para la organización (lector). |
| `GET /orgs/{org}/catalogo/estado` | Por proveedor: si el servidor lo tiene configurado, de qué fuentes lee, cuántos modelos hay guardados, cuándo se leyeron y cómo salió el último intento (quién, cuándo, resultado, error saneado). Lector. |
| `POST /orgs/{org}/catalogo/sincronizar?proveedor=` | Lee ya el catálogo de todos los proveedores o del indicado. `org-admin`. 200 con el estado de cada uno si alguno se leyó (`resultado`: `ok` o `parcial`), 502 si todos los pedidos fallaron, 404 si el servidor no tiene ese proveedor, 422 si no existe, 409 si el servidor no tiene proveedores de modelo. Ver «Catálogo de modelos». |
| `GET/PUT /orgs/{org}/perfiles[/{nombre}]?workspace=` | Perfiles; validados contra el catálogo (422 si un modelo no está o no admite el effort, las salidas estructuradas o el contexto pedidos; aviso si no hay catálogo de ese proveedor). |
| `GET/PUT /orgs/{org}/presupuestos?workspace=` | Presupuestos. |
| `GET /orgs/{org}/proveedores-contexto?workspace=`, `PUT/DELETE …/{rol}/{nombre}` | Proveedores de contexto; credenciales solo como `secret://<org>--<nombre>/<clave>` (namespace de la organización). La `url` debe ser de un host permitido por la plataforma. Solo `org-admin` fija `url` y `credencial_ref`; los demás roles reciben `credencial_configurada` en vez de `credencial_ref`. |
| `GET /orgs/{org}/workspaces/{ws}/resumen` | Unidades por fase y estado, integradas, checkpoints pendientes, convergencia de gates, gasto del mes contra presupuesto, commit del grafo por repositorio. |
| `GET /orgs/{org}/workspaces/{ws}/auditoria?evento=&repositorio=&unidad=&desde=&hasta=&cursor=&limite=` | Auditoría, más reciente primero. |
| `GET /orgs/{org}/workspaces/{ws}/unidades/{u}` | Estado (sin evidencia ni propuesta de los hallazgos) y resumen de la orden vigente. |
| `GET …/unidades/{u}/linea-de-tiempo` | Eventos de sincronización y resumen de órdenes con su reporte (archivos tocados, tareas completadas). |
| `GET …/unidades/{u}/trazabilidad` | CA-NN → tareas → archivos → símbolos → hallazgos (de órdenes, reportes y snapshots). |
| `GET …/unidades/{u}/commit-integrable` | Sugerencia de `commit_integrado` al integrar: punta de la rama por defecto del repositorio primario en el clon canónico del servidor (`commit`, `rama`, `commit_indexado`; `motivo` si no hay). Lector; solo lee un sha, nunca código. Ver «Aprobaciones e integración». |
| `GET …/unidades/{u}/eventos` | SSE (R8): `event: sync` con el `EventoSync`, `event: estado` cuando cambia la versión. `id` = `<secuencia remoto→local>:<secuencia local→remoto>`, así que `Last-Event-ID` retoma sin repetir (un valor inválido se ignora y empieza desde el principio). Tope por persona y global (429 con `Retry-After`), consulta en un executor propio y revalida el rol `lector` cada `RAILSPEC_CONSOLA_SSE_REVALIDAR_S`: si se revoca, el flujo se corta en ese plazo (no hasta los 300 s). Los topes son por réplica. |
| `GET /orgs/{org}/workspaces/{ws}/grafo/repositorios` | Repositorios vinculados con nivel, rol y commit canónico del grafo. |

Crear perfiles, presupuestos y proveedores de contexto es atómico: el documento
nace con un `_id` determinista (`<org>/<workspace o *>/<nombre>` para perfiles,
`<org>/<workspace o *>` para presupuestos y
`<org>/<workspace o *>/<rol>/<nombre>` para proveedores de contexto, el mismo
que escribe el motor) y hay un índice único por clave natural, así que dos
`PUT` simultáneos que crean lo mismo dejan un solo documento y el perdedor
recibe 409. Los documentos anteriores (`_id` ObjectId, creados por la consola
antes de esto) no se migran: se leen y editan por su clave natural y conservan
su `_id`. Si ya hubiera duplicados de aquella carrera, el servidor arranca igual,
avisa en el log (`hay documentos duplicados`) y no crea el índice hasta que se
borren a mano.

## Chat de contexto (fase 8)

- **Servidor**: las rutas son `/v1/chat/*` (`railspec.server.chat.http`), no
  `/consola/api/chat`. Resuelven el actor con la identidad del servidor, que ya
  acepta tokens `rsc1`. `GET /v1/chat/conversaciones?org=&workspace=&limite=`
  lista las conversaciones vigentes de la persona en ese workspace (recientes
  primero, tope 50); ver `chat.md`.
- **SPA**: la ruta `/<org>/<ws>/chat` carga `src/chat/ChatContexto.tsx` de forma
  diferida (`vistas/chat/cargador.ts`) y es parte de este paquete. El token sale
  de `POST /consola/api/auth/token`, vive solo en memoria y la SPA lo renueva unos
  minutos antes de expirar.
  - **Repositorios**: el selector ofrece casillas con los vínculos del workspace
    (`GET …/repositorios`, todos marcados). El servidor valida el nombre del
    repositorio vinculado (`^[a-z0-9][a-z0-9-]{0,62}$`), así que un texto libre como
    `owner/repo` daba 422; sin vínculos el chat lo dice en vez de pedir texto.
  - **Retomar**: la SPA guarda en `localStorage` el id de la última conversación
    por organización y workspace (`lib/conversacionChat.ts`) y la reabre al
    volver. Si el servidor responde 404 (expiró o no es de esa persona) la olvida,
    avisa y vuelve al selector; «Nueva conversación» también la olvida. Cerrar sesión
    borra los ids guardados.
  - **Elegir otra**: sobre el chat, un selector «Conversación» lista las de la
    persona en el workspace (`GET /v1/chat/conversaciones`, con el token del chat;
    fecha y repositorios de cada una) y deja abrir cualquiera, no solo la última
    del navegador. Elegir una la guarda como última y remonta el chat para que
    cargue esa; «Nueva conversación» (también en el selector) vuelve a pedir
    repositorios. La lista se recarga al crear una conversación o al descubrir que
    una ya no existe. Si no se puede listar, el chat sigue y el aviso trae un
    botón para reintentar.
  - **Crear una unidad desde el insumo**: tras exportar el insumo el chat ofrece
    «Crear unidad con este insumo» (solo `desarrollador` o más). Abre el diálogo de
    nueva unidad con título, pedido, restricciones, repositorios (primario primero) y
    `base_commit` tomados del insumo, la rama del vínculo y el id del insumo en
    `insumos`; el servidor comprueba que el insumo exista en el workspace.

## Tablero, estadísticas y grafo

- **Tablero en vivo**: «En vivo» (por defecto) vuelve a pedir `unit.list` cada
  15 s mientras la pestaña está visible y resalta las unidades nuevas o que
  cambiaron (fase, estado, modo, riesgo, integración o checkpoint). No hay un flujo de eventos por workspace (el SSE
  es por unidad), así que es sondeo; un SSE de workspace sería un endpoint nuevo.
  Los carriles reparten las unidades por repositorio, dueño, modo o riesgo
  (`?carriles=`); `?vivo=no` apaga el sondeo.
- **Estadísticas**: `telemetry.query` agrupado por fase, nodo del DAG, modelo,
  proveedor, tier, repositorio, workspace (toda la organización, exige rol
  lector a ese nivel) o veredicto del gate; el gráfico muestra costo y tokens,
  duración media por llamada o caché. La tasa de caché es
  `cache / (entrada + cache)`, porque `tokens_entrada` ya excluye las lecturas de caché.
- **Grafo contra el snapshot de una unidad**: en el navegador del grafo, «Comparar
  con una unidad» pide el vecindario dos veces (`graph.query` sin y con `unidad`,
  que mira el canónico más la superposición) y colorea lo nuevo, lo que la
  unidad oculta y lo que toca (verbo `impact`, contrato 1.4; con un servidor
  anterior compara igual y avisa que no marca lo tocado). Se llega también desde
  las pestañas Impacto y Trazabilidad de una unidad (`?unidad=`).

## Desarrollo

```
# API con Mongo simulado y token de desarrollo. SOLO LOCAL, con datos ficticios: el
# login y los ids son inventados y la bandera exige que lo pidas explícitamente.
# Nunca en el Secret ni en el ConfigMap de un clúster.
RAILSPEC_PERMITIR_DESARROLLO=1 \
RAILSPEC_TOKENS_DESARROLLO=tk-dev=usuario-demo:1000001 \
RAILSPEC_CONSOLA_ADMINS=1000001 railspec-server

# SPA con recarga en caliente (proxy de /consola/api a localhost:8080)
npm --prefix railspec/packages/railspec-console ci
npm --prefix railspec/packages/railspec-console run dev     # http://localhost:5173/consola/

# Compilar y servir desde el servidor
npm --prefix railspec/packages/railspec-console run build
RAILSPEC_CONSOLA_DIR=railspec/packages/railspec-console/dist railspec-server   # http://localhost:8080/consola/
```

Pruebas: `npm --prefix railspec/packages/railspec-console test` (Vitest),
`python -m pytest railspec/packages/railspec-server` y, con Mongo y FalkorDB
del compose, `python -m pytest railspec/integracion`. El job `consola` de
`railspec-ci.yml` corre `npm ci`, typecheck, pruebas y build.

Humo con navegador (`railspec-console/humo/humo.mjs`): arranca `railspec-server` en
memoria (modo desarrollo, con `dist/`) y recorre con Chromium el camino de una persona
nueva: login con token (y token rechazado), Inicio, crear organización y workspace,
Configuración, alta de una suscripción Foundry (la clave no vuelve a verse), modelo
declarado, perfil asociado a la suscripción, rol `lector` y solo lectura, y Salir. Falla
si el navegador registra una excepción, un error de consola o un 4xx/5xx no declarado.
No toca Foundry ni Anthropic: el descubrimiento real de modelos sigue sin probarse con
credenciales de verdad. Necesita Playwright con Chromium, que no es dependencia del
paquete (`npm i --no-save playwright`, o `RAILSPEC_HUMO_PLAYWRIGHT` con su carpeta):

```
npm --prefix railspec/packages/railspec-console run build
npm --prefix railspec/packages/railspec-console run humo     # capturas en humo/salida/
```

## Pendiente

- Editar `contexto.yaml` y `.railspecignore` por repositorio (viven en el
  repositorio; hoy la consola edita las `exclusiones` del vínculo).
- Notificaciones de gates escalados y presupuestos (Teams o correo).
- Un flujo de eventos por workspace (SSE) para el tablero, que hoy sondea cada
  15 s: sería una ruta nueva del servidor.
