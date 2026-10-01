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
  roles por equipo). Sin ese permiso la consola y el arnés funcionan, pero solo
  resuelven roles asignados a personas.
- Instalarla en la organización de GitHub cuyos equipos se usen en roles.

En el inicio de sesión, el token de usuario de GitHub solo se usa dentro del
callback (leer el usuario y sus equipos) y se descarta. Cuando alguien presenta
su propio token de GitHub (el del arnés por MCP, o un script) el servidor lo usa
solo para identificarlo (`GET /user`) y leer sus equipos (`GET /user/teams`, el
mismo permiso de arriba, o el alcance `read:org` en un token OAuth); guarda el
resultado cinco minutos bajo el hash del token y nunca el token.

## Variables de entorno

Las que Julian debe suministrar:

| Variable | Dónde | Uso |
| --- | --- | --- |
| `RAILSPEC_GITHUB_APP_CLIENT_ID` | Secret | Client ID de la GitHub App. Sin ella no hay botón "Entrar con GitHub". |
| `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | Secret | Client secret de la App. |
| `RAILSPEC_CONSOLA_SECRETO` | Secret | Clave HMAC de sesiones y tokens (p. ej. `openssl rand -base64 48`). Sin ella cada réplica genera una efímera: las sesiones se pierden al reiniciar o cambiar de réplica. |
| `RAILSPEC_CONSOLA_ADMINS` | ConfigMap (`renderizar.py`) | `github_id` numéricos, separados por coma, que administran la plataforma: crean organizaciones y son `org-admin` en todas. El de Julian es `83125327`. |
| `RAILSPEC_CONSOLA_URL` | ConfigMap (lo deriva el render de `RAILSPEC_DOMINIO`) | URL pública: base de la redirección de OAuth y cookie `Secure`. Obligatoria con GitHub App. |
| `RAILSPEC_CONSOLA_DIR` | Imagen | Carpeta de la SPA compilada; la imagen ya la fija. |
| `RAILSPEC_CONSOLA_SESION_HORAS` | opcional | Vida de la sesión (8 por defecto). |

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
  `Secure` con URL https. Formato `rsc1.<carga>.<firma HMAC-SHA256>`, sin
  estado en el servidor; lleva login, `github_id`, equipos de GitHub y
  expiración.
- **Anti-CSRF**: con cookie, toda petición que no sea GET exige la cabecera
  `X-Railspec-Consola: 1` (un formulario de otro sitio no puede ponerla).
- **Token para `/v1/*`**: `POST /consola/api/auth/token` devuelve
  `{"token": "rsc1…", "expira_en": ISO}`, válido una hora como
  `Authorization: Bearer` en `/v1/tools` y en cualquier ruta que resuelva el
  actor con la identidad del servidor (`identidad.actor_desde_token(token,
  "consola")`): Actor humano con `github_id`, login y canal `consola`. Es el
  que usa el chat. Un token de sesión (cookie) no vale como Bearer.
- **Bearer en la consola**: `/consola/api` también acepta
  `Authorization: Bearer` (token de GitHub, de desarrollo o `rsc1`) para
  scripts; sin cookie no hace falta la cabecera anti-CSRF. Con token de GitHub
  o `rsc1` los equipos cuentan igual que en la sesión; los tokens de desarrollo
  no tienen equipos.
- Cerrar sesión borra la cookie; un token ya emitido vale hasta su expiración.

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
por el mismo registro que MCP y `/v1/tools`, y los roles de equipo valen igual
por las tres vías: la identidad del token devuelve un actor con los equipos de
GitHub de la persona y el autorizador los suma a las asignaciones personales
(la misma consulta que usa la consola).

| Token | Equipos |
| --- | --- |
| `rsc1` de `POST /consola/api/auth/token` (SPA y chat) | Los del login, firmados en el token. |
| Token de GitHub (el del arnés por MCP, scripts) | `GET /user/teams` con ese mismo token, cacheado cinco minutos. Sin permiso o sin respuesta de GitHub, ninguno: el token sigue valiendo y cuentan las asignaciones personales. |
| Token de desarrollo | Ninguno: asignar el rol como persona. |
| OIDC de Actions | No aplica: el alcance lo fija la tool. |

Los equipos no se guardan en el estado ni en la auditoría. Un token `rsc1`
vencido o con la carga alterada no es actor (401), y un rol de equipo vale solo
en la organización y el workspace en que se asignó. Un cambio de membresía se
nota al renovar el token de GitHub (cinco minutos) o al volver a iniciar
sesión (el `rsc1` conserva los equipos del login).

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
| `POST /auth/salir` | Borra la cookie. |
| `POST /auth/token` | Token `rsc1` de una hora para `/v1/*`. |
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
- Editar `contexto.yaml` y `.railspecignore` por repositorio (viven en el
  repositorio; hoy la consola edita las `exclusiones` del vínculo).
- Notificaciones de gates escalados y presupuestos (Teams o correo).
