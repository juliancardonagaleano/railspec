# Despliegue en Render desde GitHub

Servidor y consola web de Railspec en **un solo servicio web de Render**, con el estado en **Supabase Postgres** ([estado-postgres.md](estado-postgres.md)) y los modelos en Azure AI Foundry. El grafo de código vive en la misma base de Supabase (`RAILSPEC_GRAFO_POSTGRES`, ver [grafo.md](grafo.md#motor-en-postgres)): no hace falta FalkorDB ni un segundo servicio. Es el camino de menor costo para probar; el despliegue de producción sigue siendo [AKS](despliegue.md).

```
push a master ─▶ Render construye el Dockerfile del repositorio y arranca el servicio
                      └─▶ el servidor crea su tabla en Supabase al arrancar
```

Hay dos caminos. El de esta guía, **Render construye** (`render.yaml` de la raíz), no necesita GHCR, ni token de registro, ni Docker local, ni GitHub Actions: Render clona el repositorio y construye `railspec/deploy/servidor/Dockerfile` en cada push a `master` que toque el servidor, la consola o el Dockerfile. La variante **imagen en GHCR** (`railspec/deploy/render/render-ghcr.yaml`) queda al final, para cuando Actions funcione o tengas Docker.

Todo lo de esta guía se hace **una vez**.

## Qué se necesita

| Qué | Dónde se consigue |
| --- | --- |
| Cuenta de Render (plan Hobby) conectada a GitHub, con acceso a este repositorio | render.com |
| Proyecto de Supabase | supabase.com. Elige una región cercana a la de Render (`oregon` en el Blueprint) |
| GitHub App de la consola | [consola.md](consola.md); su URL de retorno depende de la URL del servicio (paso 4) |
| Despliegues de Foundry y una clave de API | [proveedores.md](proveedores.md) |

Las claves y cadenas de conexión las pones tú en el panel de Render. No las pegues en el chat, en un PR ni en un archivo del repositorio.

## Pasos

1. **Supabase.** *Connect > Session pooler* y copia la cadena (`postgresql://postgres.<ref>:<clave>@aws-0-<región>.pooler.supabase.com:5432/postgres`), con tu contraseña de base. Es el valor de `RAILSPEC_POSTGRES_URL`. Detalles y por qué el pooler: [estado-postgres.md](estado-postgres.md#supabase).
2. **Crear el servicio desde el Blueprint.** En Render, *New > Blueprint*, este repositorio, rama `master` (usa el `render.yaml` de la raíz). Render pide los valores marcados `sync: false`: ver la tabla de variables. Déjalos en blanco los que aún no tengas (la GitHub App, en el paso 4) y complétalos en *Environment* del servicio. La primera construcción tarda varios minutos.
3. **Comprobar el arranque.** `curl https://<url>/healthz` debe dar `{"estado":"ok","postgres":"ok"}` (la primera vez tarda: el servicio gratuito arranca en frío). Si la construcción falla, el registro de *Events* en Render dice qué paso del Dockerfile.
4. **GitHub App de la consola.** Con la URL real del servicio (`https://<nombre>.onrender.com`; si el nombre `railspec` ya estaba tomado, Render le añade un sufijo), crea la App con URL de retorno `https://<url>/consola/api/auth/github/callback` ([consola.md](consola.md)) y pon su client id y secret en `RAILSPEC_GITHUB_APP_CLIENT_ID` y `RAILSPEC_GITHUB_APP_CLIENT_SECRET`. Guardar el entorno redespliega el servicio.
5. **Comprobar la consola.** Entra en `https://<url>/consola/` con tu cuenta de GitHub; `RAILSPEC_CONSOLA_ADMINS` te hace administrador de la plataforma y desde ahí creas la organización.
6. **Clave maestra.** Genera `openssl rand -base64 32`, pégala en `RAILSPEC_CLAVE_MAESTRA` (panel de Render) y guarda una copia fuera de Render: cifra las claves de las suscripciones y, si se pierde, hay que escribirlas otra vez. Sin ella la consola no guarda suscripciones.
7. **Modelos.** En la consola, Configuración → Suscripciones: registra tu recurso de Foundry (endpoint, proyecto, región, zona de datos y clave), pulsa Descubrir, elige los modelos y asocia cada perfil a la suscripción ([proveedores.md](proveedores.md#suscripciones-de-modelos)). Ya no hace falta `RAILSPEC_FOUNDRY_*`; si las tenías, sigue [Migrar de `RAILSPEC_FOUNDRY_*`](proveedores.md#suscripciones-de-modelos). Para validar la configuración del servidor sin gastar tokens, ejecuta en una sesión con red a Azure `python -m railspec.server.humo --sin-llamada`.

## Variables del servicio

| Variable | Valor | Quién la pone |
| --- | --- | --- |
| `RAILSPEC_PUERTO` | `10000`, el puerto que Render espera | Blueprint |
| `FORWARDED_ALLOW_IPS` | `*` | Blueprint |
| `RAILSPEC_POSTGRES_URL` | Cadena del *session pooler* de Supabase | Tú, en el panel |
| `RAILSPEC_POSTGRES_ESQUEMA` | `railspec` (no `public`: Supabase lo expone por su API REST) | Blueprint |
| `RAILSPEC_GRAFO_POSTGRES` | `true`: el grafo de código en la misma base, en tablas `grafo_*` del mismo esquema | Blueprint |
| `RAILSPEC_OIDC_AUDIENCIA`, `RAILSPEC_OIDC_REPOSITORIOS` | Opcionales, juntas: la audiencia larga y no adivinable (`openssl rand -hex 24`) y los `owner/repo` que pueden llamar `graph.index` ([despliegue.md](despliegue.md)); vacías, el grafo existe pero nadie lo alimenta | Tú, en el panel |
| `RAILSPEC_CONSOLA_SECRETO` | Aleatorio de 256 bits que genera Render | Render |
| `RAILSPEC_CONSOLA_ADMINS` | `83125327` (el github_id de Julian) | Blueprint |
| `RAILSPEC_GITHUB_APP_CLIENT_ID`, `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | De la GitHub App | Tú, en el panel |
| `RAILSPEC_GITHUB_APP_ID`, `RAILSPEC_GITHUB_APP_CLAVE_PRIVADA` | Opcionales, juntas: id numérico de la App y su clave privada PEM (pégala con saltos de línea reales o con `\n`); con ellas la consola abre PR para editar `contexto.yaml` y `.railspecignore` ([consola.md](consola.md#archivos-del-repositorio)) | Tú, en el panel |
| `RAILSPEC_CLAVE_MAESTRA` | `openssl rand -base64 32`; cifra las claves de las suscripciones de la consola y los webhooks de Teams de los avisos | Tú, en el panel |
| `RAILSPEC_SMTP_URL` | opcional: `smtp://usuario:clave@host:587?desde=correo`; correo de los avisos y del informe semanal ([consola.md](consola.md#avisos-e-informes)) | Tú, en el panel |
| `RAILSPEC_METRICAS_TOKEN` | Opcional: Bearer de `GET /metrics`, 16 caracteres o más (`openssl rand -base64 24`); sin él no hay endpoint | Tú, en el panel |
| `RAILSPEC_FOUNDRY_ENDPOINT`, `RAILSPEC_FOUNDRY_API_KEY` | Del recurso de Foundry; opcionales desde 1.6 (respaldo de los perfiles sin suscripción) | Tú, en el panel |
| `RAILSPEC_FOUNDRY_REGION`, `RAILSPEC_FOUNDRY_ZONA_DATOS`, `RAILSPEC_FOUNDRY_DESPLIEGUES` | Región, zona de datos y despliegues declarados ([proveedores.md](proveedores.md)) | Tú, en el panel |

La URL pública de la consola sale de `RENDER_EXTERNAL_URL`, que fija Render; define `RAILSPEC_CONSOLA_URL` solo si usas un dominio propio. Render ignora los `sync: false` al actualizar un Blueprint ya creado: los cambios de esos valores se hacen en el panel.

## Variante: imagen en GHCR y Deploy Hook

Úsala si Render no puede construir el Dockerfile (por ejemplo, por memoria en el plan gratuito) o si quieres desplegar siempre la misma imagen que publica el CI. Es el flujo del job `render` de [`railspec-imagen.yml`](../../.github/workflows/railspec-imagen.yml):

```
push a master ─▶ railspec-imagen: construye y publica ghcr.io/<owner>/railspec-server@sha256:…
                      └─▶ job «render»: Deploy Hook de Render con imgURL=<ese digest>
```

> **GitHub Actions hoy no arranca en este repositorio** (`startup_failure`, pendiente de la cuenta). Mientras tanto, el camino manual de abajo hace lo mismo con el mismo script.

1. **Primera imagen.** Con Actions funcionando, basta un push a `master` (o *Run workflow* en `railspec-imagen`). A mano, con Docker `buildx` y un token con `write:packages`:
   ```sh
   echo "$GITHUB_TOKEN" | docker login ghcr.io -u <tu-usuario> --password-stdin
   sh railspec/deploy/render/desplegar.sh --solo-imagen
   ```
2. **Credencial de registro `ghcr-railspec`.** Si el paquete de GHCR es privado, crea en Render *Workspace Settings > Registry Credentials* una con ese nombre, tu usuario de GitHub y un token clásico con `read:packages`.
3. **Blueprint.** En *New > Blueprint* indica la ruta `railspec/deploy/render/render-ghcr.yaml` en lugar de `render.yaml`. Los pasos 1, 3, 4 y 5 de arriba valen igual.
4. **Conectar CI.** En Render, servicio > *Settings > Deploy Hook*: copia la URL. En GitHub, *Settings > Secrets and variables > Actions*: secreto `RENDER_DEPLOY_HOOK_URL` (esa URL; lleva una clave) y variable `RAILSPEC_RENDER_URL` (`https://<nombre>.onrender.com`). Sin la variable el job `render` no corre.

Camino manual, sin Actions (equivale al job `render`):

```sh
export RENDER_DEPLOY_HOOK_URL='<URL del Deploy Hook>'          # secreto
export RAILSPEC_RENDER_URL='https://<nombre>.onrender.com'
sh railspec/deploy/render/desplegar.sh                          # construye, publica y despliega el commit actual
sh railspec/deploy/render/desplegar.sh ghcr.io/<owner>/railspec-server@sha256:…   # solo despliega ese digest
```

Con el digest del argumento se **vuelve a una versión anterior**: el workflow imprime `RAILSPEC_IMAGEN=…@sha256:…` en el resumen de cada ejecución. También sirve *Manual Deploy* en el panel de Render.

## Qué hace y qué no hace el despliegue

- **Migración:** no hay paso aparte. Al arrancar, el servidor crea el esquema, la tabla y los índices únicos si faltan, bajo un bloqueo que evita carreras entre arranques simultáneos.
- **Grafo:** el servidor crea las tablas `grafo_*` al arrancar, igual que la del estado. Empieza vacío: lo llena `railspec-reindexar` (`.github/workflows/railspec-reindexar.yml`) cuando defines en GitHub `RAILSPEC_URL`, `RAILSPEC_WORKSPACE`, `RAILSPEC_REPOSITORIO` y `RAILSPEC_OIDC_AUDIENCIA` (la misma que en Render) y GitHub Actions corre. Sin grafo poblado el gate de código no ve impacto y la rebanada de grafo de spec, plan y tasks sale con el aviso de grafo vacío. Un grafo de unos 12 000 símbolos y 31 000 aristas ocupa unos 30 MB con sus índices, dentro de los 500 MB de Supabase gratuito. El grafo usa su propio pool (hasta 5 conexiones) además del del estado: el *session pooler* del plan gratuito admite pocas conexiones simultáneas, así que no escales réplicas sin mirarlo.
- **Salud no prueba la versión:** mira *Events* en el panel de Render para confirmar que el despliegue nuevo terminó (con la variante GHCR, el script espera a `/healthz`, pero la imagen anterior también responde mientras Render la reemplaza).
- **Construcción en Render:** el Dockerfile instala `git` con apt, compila la consola con Node y usa `RUN --mount=type=bind` (BuildKit). Si el plan gratuito no alcanza para construir, usa la variante GHCR.
- **Servicio gratuito:** se duerme tras 15 minutos sin tráfico, tiene 512 MB de RAM y 0,1 CPU, y no tiene disco; el primer comando tras un rato tarda. Las sesiones MCP viven en memoria y se pierden al dormirse. Los clones del chat (`RAILSPEC_CHAT_CLONES`) no caben sin disco: el chat responde sin leer código.
- **Supabase gratuito:** 500 MB, sin respaldos, y el proyecto se pausa tras una semana sin actividad. Conviene un `pg_dump` periódico.
- **Datos fuera de Azure:** el estado queda en Supabase y Render, no en la zona de datos de Foundry. Para repositorios propietarios es una decisión de política: el nivel de código del repositorio ya no restringe proveedores, solo el material que viaja ([chat.md](chat.md), [proveedores.md](proveedores.md#política-por-nivel)).
- **`FORWARDED_ALLOW_IPS=*`:** uvicorn confía en `X-Forwarded-*` de cualquier origen, así que un cliente puede falsear su IP y esquivar el límite de `/consola/api/auth/*`. Es el mismo compromiso que en [AKS](despliegue.md); cambiarlo por la lista de IPs del proxy de Render queda pendiente.

## Sin verificar

Esta guía se escribió sin cuenta de Render ni de Supabase y sin ejecutar Actions. Los nombres de campos del Blueprint (`runtime: docker`, `dockerfilePath`, `dockerContext`, `buildFilter`, `autoDeployTrigger`, `generateValue`, `sync: false`; en la variante GHCR, `runtime: image`, `creds.fromRegistryCreds` y el parámetro `imgURL` del Deploy Hook) salen de la documentación de Render consultada por búsqueda, no de una ejecución. Si Render rechaza el Blueprint, el panel dice qué campo; corrige el archivo y vuelve a aplicarlo. Tampoco se probó la construcción del Dockerfile en Render (soporte de BuildKit, memoria y tiempo en el plan gratuito), el arranque en frío con 0,1 CPU ni la cadena de Supabase. El Dockerfile no se pudo construir en la sesión de desarrollo porque la red del entorno bloquea los repositorios de apt.
