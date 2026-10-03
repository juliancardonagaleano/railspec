# Despliegue en Render desde GitHub

Servidor y consola web de Railspec en **un solo servicio web de Render**, con el estado en **Supabase Postgres** ([estado-postgres.md](estado-postgres.md)) y los modelos en Azure AI Foundry. Sin FalkorDB: el grafo es opcional y sin él solo el gate de código pierde el análisis de impacto. Es el camino de menor costo para probar; el despliegue de producción sigue siendo [AKS](despliegue.md).

```
push a master ─▶ railspec-imagen: construye y publica ghcr.io/<owner>/railspec-server@sha256:…
                      └─▶ job «render»: Deploy Hook de Render con imgURL=<ese digest>
                              └─▶ Render descarga la imagen y arranca; el servidor crea su tabla en Supabase
```

Todo lo de esta guía se hace **una vez**. Después, cada push a `master` que cambie el servidor, la consola o el Dockerfile despliega solo (workflow [`railspec-imagen.yml`](../../.github/workflows/railspec-imagen.yml)).

> **GitHub Actions hoy no arranca en este repositorio** (`startup_failure`, pendiente de la cuenta). El workflow queda listo para cuando funcione. Mientras tanto, el camino manual de abajo hace exactamente lo mismo con el mismo script.

## Qué se necesita

| Qué | Dónde se consigue |
| --- | --- |
| Cuenta de Render (plan Hobby) conectada a GitHub | render.com |
| Proyecto de Supabase | supabase.com. Elige una región cercana a la de Render (`oregon` en el Blueprint) |
| GitHub App de la consola | [consola.md](consola.md); su URL de retorno depende de la URL del servicio (paso 5) |
| Despliegues de Foundry y una clave de API | [proveedores.md](proveedores.md) |
| Docker con `buildx` y un token de GitHub con `write:packages` | Solo para la primera publicación a mano (paso 1) |

Las claves y cadenas de conexión las pones tú en el panel de Render o en los secretos de GitHub. No las pegues en el chat, en un PR ni en un archivo del repositorio.

## Pasos

1. **Publicar la primera imagen.** El Blueprint necesita que la imagen exista. Con Actions funcionando, basta un push a `master` (o *Run workflow* en `railspec-imagen`). A mano:
   ```sh
   echo "$GITHUB_TOKEN" | docker login ghcr.io -u <tu-usuario> --password-stdin
   sh railspec/deploy/render/desplegar.sh --solo-imagen
   ```
   Imprime `ghcr.io/<owner>/railspec-server@sha256:…` y las etiquetas `sha-<commit>` y `master`.
2. **Credencial de registro `ghcr-railspec`.** Si el paquete de GHCR es privado (lo es por defecto con un repositorio privado), crea en Render *Workspace Settings > Registry Credentials* una con el nombre `ghcr-railspec`, tu usuario de GitHub y un token clásico con `read:packages`. Si el paquete es público no hace falta que funcione, pero conviene crearla igual: el Blueprint la nombra.
3. **Supabase.** *Connect > Session pooler* y copia la cadena (`postgresql://postgres.<ref>:<clave>@aws-0-<región>.pooler.supabase.com:5432/postgres`), con tu contraseña de base. Es el valor de `RAILSPEC_POSTGRES_URL`. Detalles y por qué el pooler: [estado-postgres.md](estado-postgres.md#supabase).
4. **Crear el servicio desde el Blueprint.** En Render, *New > Blueprint*, este repositorio, rama `master` (usa el `render.yaml` de la raíz). Render pide los valores marcados `sync: false`: ver la tabla de variables. Déjalos en blanco los que aún no tengas (la GitHub App, en el paso 5) y complétalos en *Environment* del servicio.
5. **GitHub App de la consola.** Con la URL real del servicio (`https://<nombre>.onrender.com`; si el nombre `railspec` ya estaba tomado, Render le añade un sufijo), crea la App con URL de retorno `https://<url>/consola/api/auth/github/callback` ([consola.md](consola.md)) y pon su client id y secret en `RAILSPEC_GITHUB_APP_CLIENT_ID` y `RAILSPEC_GITHUB_APP_CLIENT_SECRET`. Guardar el entorno redespliega el servicio.
6. **Conectar CI.** En Render, servicio > *Settings > Deploy Hook*: copia la URL. En GitHub, *Settings > Secrets and variables > Actions*:
   - Secreto `RENDER_DEPLOY_HOOK_URL`: esa URL (lleva una clave; es un secreto).
   - Variable `RAILSPEC_RENDER_URL`: `https://<nombre>.onrender.com`. Sin ella el job `render` no corre.
7. **Comprobar.** `curl https://<url>/healthz` debe dar `{"estado":"ok","postgres":"ok"}` (la primera vez tarda: el servicio gratuito arranca en frío). Entra en `https://<url>/consola/` con tu cuenta de GitHub; `RAILSPEC_CONSOLA_ADMINS` te hace administrador de la plataforma y desde ahí creas la organización.

## Variables del servicio

| Variable | Valor | Quién la pone |
| --- | --- | --- |
| `RAILSPEC_PUERTO` | `10000`, el puerto que Render espera | Blueprint |
| `FORWARDED_ALLOW_IPS` | `*` | Blueprint |
| `RAILSPEC_POSTGRES_URL` | Cadena del *session pooler* de Supabase | Tú, en el panel |
| `RAILSPEC_POSTGRES_ESQUEMA` | `railspec` (no `public`: Supabase lo expone por su API REST) | Blueprint |
| `RAILSPEC_CONSOLA_SECRETO` | Aleatorio de 256 bits que genera Render | Render |
| `RAILSPEC_CONSOLA_ADMINS` | `83125327` (el github_id de Julian) | Blueprint |
| `RAILSPEC_GITHUB_APP_CLIENT_ID`, `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | De la GitHub App | Tú, en el panel |
| `RAILSPEC_FOUNDRY_ENDPOINT`, `RAILSPEC_FOUNDRY_API_KEY` | Del recurso de Foundry | Tú, en el panel |
| `RAILSPEC_FOUNDRY_REGION`, `RAILSPEC_FOUNDRY_ZONA_DATOS`, `RAILSPEC_FOUNDRY_DESPLIEGUES` | Región, zona de datos y despliegues declarados ([proveedores.md](proveedores.md)) | Tú, en el panel |
| `RAILSPEC_CHAT_ZONA_DATOS` | Regiones de Azure donde el chat puede enviar código; vacío = el chat se niega en `restringido` e `interno` | Tú, en el panel |

La URL pública de la consola sale de `RENDER_EXTERNAL_URL`, que fija Render; define `RAILSPEC_CONSOLA_URL` solo si usas un dominio propio. Render ignora los `sync: false` al actualizar un Blueprint ya creado: los cambios de esos valores se hacen en el panel.

## Camino manual (sin Actions)

Equivale al job `render`; usa el mismo script:

```sh
export RENDER_DEPLOY_HOOK_URL='<URL del Deploy Hook>'          # secreto
export RAILSPEC_RENDER_URL='https://<nombre>.onrender.com'
sh railspec/deploy/render/desplegar.sh                          # construye, publica y despliega el commit actual
sh railspec/deploy/render/desplegar.sh ghcr.io/<owner>/railspec-server@sha256:…   # solo despliega ese digest
```

Con el digest del argumento se **vuelve a una versión anterior**: el workflow imprime `RAILSPEC_IMAGEN=…@sha256:…` en el resumen de cada ejecución. Sin hook (por ejemplo, para la primera imagen) existe `--solo-imagen`. También sirve *Manual Deploy* en el panel de Render.

## Qué hace y qué no hace el despliegue

- **Migración:** no hay paso aparte. Al arrancar, el servidor crea el esquema, la tabla y los índices únicos si faltan, bajo un bloqueo que evita carreras entre arranques simultáneos.
- **Reindexado del grafo:** no aplica. Sin FalkorDB no hay grafo; `railspec-reindexar` solo corre si defines `RAILSPEC_URL`, y no lo hagas hasta tener grafo.
- **Salud no prueba la versión:** tras desplegar, el script espera a que `/healthz` responda, pero la imagen anterior también responde mientras Render la reemplaza. Para confirmar la nueva, mira *Events* en el panel.
- **Servicio gratuito:** se duerme tras 15 minutos sin tráfico, tiene 512 MB de RAM y 0,1 CPU, y no tiene disco; el primer comando tras un rato tarda. Las sesiones MCP viven en memoria y se pierden al dormirse. Los clones del chat (`RAILSPEC_CHAT_CLONES`) no caben sin disco: el chat responde sin leer código.
- **Supabase gratuito:** 500 MB, sin respaldos, y el proyecto se pausa tras una semana sin actividad. Conviene un `pg_dump` periódico.
- **Datos fuera de Azure:** el estado queda en Supabase y Render, no en la zona de datos de Foundry. Para repositorios propietarios es una decisión de política ([chat.md](chat.md)).
- **`FORWARDED_ALLOW_IPS=*`:** uvicorn confía en `X-Forwarded-*` de cualquier origen, así que un cliente puede falsear su IP y esquivar el límite de `/consola/api/auth/*`. Es el mismo compromiso que en [AKS](despliegue.md); cambiarlo por la lista de IPs del proxy de Render queda pendiente.

## Sin verificar

Esta guía se escribió sin cuenta de Render ni de Supabase y sin ejecutar Actions. Los nombres de campos del Blueprint (`runtime: image`, `creds.fromRegistryCreds`, `autoDeployTrigger`, `generateValue`, `sync: false`) y el parámetro `imgURL` del Deploy Hook salen de la documentación de Render consultada por búsqueda, no de una ejecución. Si Render rechaza el Blueprint, el panel dice qué campo; corrige `render.yaml` y vuelve a aplicarlo. Tampoco se probó la imagen en Render (arranque en frío con 0,1 CPU, límite de memoria) ni la cadena de Supabase.
