# Consola web (`railspec-console`)

La consola es un cliente más de `railspec-server`: administra organizaciones,
workspaces, roles y repositorios vinculados; configura perfiles, presupuestos
y proveedores de contexto; muestra estadísticas de costo y de convergencia de
gates; y explora unidades (tablero, DAG de fases, línea de tiempo,
checkpoints, integración, trazabilidad CA-NN), el grafo de código y la
auditoría. El chat de contexto (fase 8) vive aparte y se enchufa en la ruta
que la consola le reserva.

Nunca muestra código: de una orden solo salen sus metadatos (nunca
instrucciones, plantilla ni contexto), de un snapshot solo rutas y símbolos,
y del grafo solo nombres, rutas y relaciones.

## Piezas

| Pieza | Dónde |
| --- | --- |
| SPA (React + TypeScript, Vite, TanStack Router y Query, Tailwind, React Flow, Sigma.js, ECharts) | `railspec/packages/railspec-console/` |
| API de la consola (`/consola/api`) | `railspec/packages/railspec-server/src/railspec/server/consola/` |
| Pruebas de la API | `railspec-server/tests/test_consola.py` |
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
- **Permisos**: Organization → Members: *Read-only* (equipos del usuario para
  roles por equipo). Sin ese permiso la consola funciona, pero solo resuelve
  roles asignados a personas.
- Instalarla en la organización de GitHub cuyos equipos se usen en roles.

El token de usuario de GitHub solo se usa dentro del callback (leer el usuario
y sus equipos); no se guarda y al terminar se revoca en GitHub.

## Variables de entorno

Las que Julian debe suministrar:

| Variable | Dónde | Uso |
| --- | --- | --- |
| `RAILSPEC_GITHUB_APP_CLIENT_ID` | Secret | Client ID de la GitHub App. Sin ella no hay botón "Entrar con GitHub". |
| `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | Secret | Client secret de la App. |
| `RAILSPEC_CONSOLA_SECRETO` | Secret | Clave HMAC de sesiones y tokens (p. ej. `openssl rand -base64 48`), **mínimo 32 caracteres**. Obligatoria con URL pública https o con GitHub App: sin ella, o con una más corta, el servidor no arranca (el `state` de OAuth que entrega `/auth/github/inicio` es texto conocido más su MAC: una clave débil se rompe sin conexión y permite forjar sesiones). Solo en desarrollo (URL http local, sin GitHub App) se puede omitir: clave efímera con aviso, que no sobrevive a un reinicio ni se comparte entre réplicas. |
| `RAILSPEC_CONSOLA_ADMINS` | ConfigMap (`renderizar.py`) | `github_id` numéricos, separados por coma, que administran la plataforma: crean organizaciones y son `org-admin` en todas. El de Julian es `83125327`. |
| `RAILSPEC_CONSOLA_URL` | ConfigMap (lo deriva el render de `RAILSPEC_DOMINIO`) | URL pública: base de la redirección de OAuth y cookie `Secure`. Obligatoria con GitHub App. |
| `RAILSPEC_CONSOLA_DIR` | Imagen | Carpeta de la SPA compilada; la imagen ya la fija. |
| `RAILSPEC_CONSOLA_AUTH_LIMITE` | opcional | Peticiones por minuto y por IP en `/consola/api/auth/*` (60 por defecto; 0 lo desactiva). 429 con `Retry-After` al pasarse. |
| `RAILSPEC_CONSOLA_SESION_HORAS` | opcional | Vida de la sesión en horas (4 por defecto, de 1 a 24). Es también la ventana en que quedan congelados los equipos de GitHub de la cookie. |

`RAILSPEC_TOKENS_DESARROLLO` (ya existente) habilita además el inicio de sesión
con token de desarrollo, solo para entornos sin GitHub App.

## Primer arranque

1. Desplegar con las variables de arriba (ver `despliegue.md`).
2. Entrar con GitHub como alguien de `RAILSPEC_CONSOLA_ADMINS`.
3. Administración → crear la organización, luego sus workspaces.
4. Asignar roles (`org-admin` a nivel organización; `workspace-admin`,
   `desarrollador` o `lector` por workspace) a personas, por login, o a
   equipos de GitHub, por su id numérico (`GET /orgs/{org}/teams/{slug}` en
   la API de GitHub lo da).
5. Vincular repositorios al workspace (nivel `restringido` por defecto).

## Sesión, tokens y anti-CSRF

- **Cookie** `railspec_sesion`: HttpOnly, SameSite=Lax, `Path=/consola`,
  `Secure` con URL https, y con https se llama `__Secure-railspec_sesion` (el
  navegador la rechaza si no es `Secure`; `__Host-` exigiría `Path=/`). Formato `rsc1.<carga>.<firma HMAC-SHA256>`; lleva
  login, `github_id`, equipos de GitHub y expiración. La carga trae el tipo
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
  `rsc1`; sin cookie no hace falta la cabecera anti-CSRF. Un script que ya
  tiene un token de GitHub lo usa directamente (en `/v1` y en `/consola/api`):
  no necesita `POST /auth/token`. El token `rsc1` `api` no vale en
  `/consola/api` (ni en lecturas ni en escrituras): la SPA usa la cookie y
  solo manda el token al chat.
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
| Editar workspace, vínculos, roles del workspace, configuración del workspace | `workspace-admin` |
| Crear workspaces, roles `org-admin`, configuración de la organización | `org-admin` |
| Crear organizaciones | administrador de la plataforma |

Las tools llamadas desde la consola (`POST /consola/api/tools/{nombre}`) pasan
por el mismo registro que MCP y `/v1/tools`, con un autorizador que además
resuelve equipos. Por MCP y `/v1/tools` los roles de equipo todavía no se
resuelven (el token de GitHub del arnés no trae equipos); hasta entonces, a
quien use el arnés hay que asignarle el rol como persona.

## Aprobaciones e integración (R7)

Aprobar un checkpoint desde la consola es opcional y nunca bloquea al
desarrollador: es la misma operación que `unit.approve`, gana la primera
resolución que llegue por cualquier canal y la segunda recibe
`checkpoint-ya-resuelto` (409). Integrar (`unit.integrate`) solo existe tras
el cierre y no lo condiciona. El estado registra el canal `consola`.

## Auditoría

Toda escritura de administración y configuración queda en `auditoria` con su
actor: `cambio-configuracion`, `cambio-nivel` (con nivel anterior, nuevo y
motivo obligatorio) y `desvinculo-repositorio` (motivo obligatorio; borra
vínculo y grafo del repositorio). Los cambios a nivel organización se
auditan en el workspace reservado `org` (`org`, `administracion` y
`configuracion` no se pueden usar como nombre de workspace); se ven en `/consola/api/orgs/{org}/workspaces/org/auditoria`
como `org-admin`.

## API

Prefijo `/consola/api`, JSON. Errores propios: `{"detalle", "errores"?}`;
errores de tools: `ErrorTool` (`{"codigo", "detalle", "version_estado"?}`).
Entidades de configuración = JSON del contrato (`railspec/schemas/v1`), con
`version` para bloqueo optimista: cada escritura manda la versión que editó
(ninguna al crear); un conflicto responde 409 con `version_actual`.

| Método y ruta | Qué hace |
| --- | --- |
| `GET /auth/config` | Métodos de inicio de sesión disponibles (`github`, `desarrollo`). |
| `GET /auth/github/inicio?volver=` | Redirige a GitHub; `volver` es una ruta interna de la SPA. |
| `GET /auth/github/callback` | Lo llama GitHub; pone la cookie y vuelve a la SPA. |
| `POST /auth/desarrollo` `{token}` | Sesión con token de desarrollo. |
| `POST /auth/salir` | Borra la cookie y revoca la sesión y sus tokens `api`; exige `X-Railspec-Consola: 1`. |
| `POST /auth/token` | Token `rsc1` de una hora para `/v1/*`; exige la cookie de sesión y `X-Railspec-Consola: 1`. |
| `GET /yo` | Persona, si administra la plataforma, organizaciones y workspaces visibles con su rol. |
| `GET /tools`, `POST /tools/{nombre}` | Registro único de tools por la superficie HTTP, canal `consola` (`unit.list`, `unit.status`, `unit.approve`, `unit.integrate`, `unit.set_mode`, `unit.start`, `telemetry.query`, `graph.query`). |
| `GET/POST /orgs`, `PUT /orgs/{org}` | Organizaciones. |
| `GET/POST /orgs/{org}/workspaces`, `PUT /orgs/{org}/workspaces/{ws}` | Workspaces. |
| `GET/POST /orgs/{org}/roles?workspace=`, `DELETE /orgs/{org}/roles/{id}` | Roles; el sujeto puede ir por login (`{"tipo": "usuario", "login": "ana"}`). La última asignación `org-admin` no se puede quitar. |
| `GET /orgs/{org}/workspaces/{ws}/repositorios`, `PUT …/repositorios/{repo}`, `DELETE …/repositorios/{repo}?motivo=` | Vínculos. Sin `chat_contexto_codigo` se usa la política por defecto del nivel. |
| `GET /orgs/{org}/catalogo`, `POST …/catalogo/sincronizar` | Catálogo de modelos. Sincronizar responde 501 hasta que el servidor sepa leer el catálogo de cada proveedor. |
| `GET/PUT /orgs/{org}/perfiles[/{nombre}]?workspace=` | Perfiles; validados contra el catálogo (422 si un modelo no está o no admite el effort, las salidas estructuradas o el contexto pedidos; aviso si no hay catálogo de ese proveedor). |
| `GET/PUT /orgs/{org}/presupuestos?workspace=` | Presupuestos. |
| `GET /orgs/{org}/proveedores-contexto?workspace=`, `PUT/DELETE …/{rol}/{nombre}` | Proveedores de contexto; credenciales solo como `secret://<secreto>/<clave>`. |
| `GET /orgs/{org}/workspaces/{ws}/resumen` | Unidades por fase y estado, integradas, checkpoints pendientes, convergencia de gates, gasto del mes contra presupuesto, commit del grafo por repositorio. |
| `GET /orgs/{org}/workspaces/{ws}/auditoria?evento=&repositorio=&unidad=&desde=&hasta=&cursor=&limite=` | Auditoría, más reciente primero. |
| `GET /orgs/{org}/workspaces/{ws}/unidades/{u}` | Estado y resumen de la orden vigente. |
| `GET …/unidades/{u}/linea-de-tiempo` | Eventos de sincronización y resumen de órdenes con su reporte (archivos tocados, tareas completadas). |
| `GET …/unidades/{u}/trazabilidad` | CA-NN → tareas → archivos → símbolos → hallazgos (de órdenes, reportes y snapshots). |
| `GET …/unidades/{u}/eventos` | SSE (R8): `event: sync` con el `EventoSync`, `event: estado` cuando cambia la versión. `id` = `<secuencia remoto→local>:<secuencia local→remoto>`, así que `Last-Event-ID` retoma sin repetir. |
| `GET /orgs/{org}/workspaces/{ws}/grafo/repositorios` | Repositorios vinculados con nivel, rol y commit canónico del grafo. |

## Chat de contexto (fase 8)

- **Servidor**: si existe `railspec.server.chat` con
  `router_consola(ctx: ContextoConsola) -> APIRouter`, la consola lo monta en
  `/consola/api/chat`. Las rutas `/v1/chat/*` del chat pueden vivir aparte; para
  resolver el actor usan la identidad del servidor, que ya acepta tokens `rsc1`.
- **SPA**: la ruta `/<org>/<ws>/chat` carga `src/chat/index.tsx` si existe (export
  `ChatContexto` o el default) con props `{apiBase: "", token, org, workspace}`.
  El token sale de `POST /consola/api/auth/token`, vive solo en memoria y la
  SPA lo renueva unos minutos antes de expirar. `src/chat/` es del hilo del chat.

## Desarrollo

```
# API con Mongo simulado y token de desarrollo
RAILSPEC_TOKENS_DESARROLLO=tk-dev=juliancardonagaleano:83125327 \
RAILSPEC_CONSOLA_ADMINS=83125327 railspec-server

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

## Pendiente

- Sincronizar el catálogo de modelos por API de cada proveedor (hoy 501).
- Roles por equipo en MCP y `/v1/tools` (hoy solo en la consola).
- Editar `contexto.yaml` y `.railspecignore` por repositorio (viven en el
  repositorio; hoy la consola edita las `exclusiones` del vínculo).
- Notificaciones de gates escalados y presupuestos (Teams o correo).
