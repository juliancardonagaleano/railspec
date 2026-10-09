# Despliegue de `railspec-server` en AKS

Todo lo desplegable vive en `railspec/deploy/` y en `.github/workflows/`:

| Pieza | Qué es |
| --- | --- |
| `deploy/servidor/Dockerfile` | Imagen del servidor. Base fijada por digest, dependencias desde `requirements.lock` con `--require-hashes`, usuario 10001, raíz de solo lectura. |
| `deploy/servidor/requirements.lock` | Lock con hashes de las dependencias de terceros. Se regenera con `deploy/servidor/bloquear.sh` al cambiar `requirements.in` o los `pyproject`. |
| `deploy/k8s/*.yaml` | Namespace, ServiceAccount, ConfigMap, Deployment, Service, Ingress y PodDisruptionBudget, con `${VARIABLES}`; y, opcionales por bandera (Mongo y FalkorDB en el clúster, NetworkPolicy, respaldos, clones del chat), los de [despliegue-datos.md](despliegue-datos.md). |
| `deploy/renderizar.py` | Sustituye las variables desde el entorno y falla si falta una obligatoria. Solo biblioteca estándar. |
| `deploy/ci/reindexar.py` | Cliente de `graph.index` que usa el workflow de reindexado: el índice por lotes, los commits que cubre y el resumen de su contenido. |
| `railspec-ci.yml` | Lint, pruebas de cada paquete por separado y validación de manifiestos con kubeconform. |
| `railspec-imagen.yml` | Construye la imagen en cada PR (con prueba de humo) y la publica en `master` y en tags `railspec-server-v*`. |
| `railspec-reindexar.yml` | En cada push a `master`, y con un índice completo cada lunes, sube el índice del canónico a `graph.index` con OIDC. |

## Lo que se prepara una vez

1. **Registro.** Si hay ACR, en el repositorio de GitHub definir las variables
   `RAILSPEC_ACR_NOMBRE`, `AZURE_CLIENT_ID`, `AZURE_TENANT_ID` y
   `AZURE_SUBSCRIPTION_ID`. La identidad de `AZURE_CLIENT_ID` necesita el rol
   `AcrPush` y una credencial federada con emisor
   `https://token.actions.githubusercontent.com` y sujeto
   `repo:juliancardonagaleano/sdd-mcp:ref:refs/heads/master` (más uno por
   `refs/tags/railspec-server-v*` si se publican tags). Sin
   `RAILSPEC_ACR_NOMBRE` la imagen va a `ghcr.io/<dueño>/railspec-server` y
   el clúster necesita un `imagePullSecret` para leerla. El clúster lee de
   ACR con `az aks update --attach-acr`.
2. **Ingress.** El add-on de enrutamiento de aplicaciones de AKS
   (`az aks approuting enable`) o ingress-nginx. El Ingress usa anotaciones
   NGINX: afinidad por la cabecera `Mcp-Session-Id` (las sesiones MCP viven
   en la memoria de la réplica que las creó), SSE sin buffering y cuerpo de
   hasta 16 MB para los lotes de `graph.index`.

   **Riesgo conocido: `FORWARDED_ALLOW_IPS: "*"`.** El ConfigMap hace que
   uvicorn confíe en `X-Forwarded-For` y `X-Forwarded-Proto` de *cualquier*
   origen, porque no se conoce de antemano la red del ingress. Con `*`, uvicorn
   toma como IP del cliente el primer valor de `X-Forwarded-For`, que el propio
   cliente puede escribir (el ingress añade la IP real al final). Efecto: el
   límite de peticiones por IP de `/consola/api/auth/*` (ver `consola.md`) se
   evade cambiando esa cabecera, y la IP que ven los registros no es de fiar.
   No abre acceso a nada más (la identidad no depende de la IP). Para cerrarlo,
   cambiar el valor de `FORWARDED_ALLOW_IPS` en `deploy/k8s/20-configmap.yaml` por
   la IP o el CIDR de los pods del controlador de ingress (p. ej. el rango de
   pods de AKS o el de `ingress-nginx`); no se fija aquí porque depende del
   clúster. El código usa siempre la IP que resuelve uvicorn, nunca la
   cabecera por su cuenta.
3. **TLS.** Un Secret `kubernetes.io/tls` para el dominio en el namespace
   (cert-manager o Key Vault con el add-on).
4. **Secret de la aplicación.** Se crea a mano; los manifiestos solo lo
   nombran:

   ```
   kubectl create namespace railspec
   kubectl -n railspec create secret generic railspec-server \
     --from-literal=RAILSPEC_MONGO_URI='mongodb://…' \
     --from-literal=RAILSPEC_FALKORDB_URL='redis://…' \
     --from-literal=RAILSPEC_FOUNDRY_API_KEY='…' \
     --from-literal=RAILSPEC_PCE_API_KEY='…' \
     --from-literal=RAILSPEC_CONSOLA_SECRETO="$(openssl rand -base64 48)" \
     --from-literal=RAILSPEC_CLAVE_MAESTRA="$(openssl rand -base64 32)" \
     --from-literal=RAILSPEC_METRICAS_TOKEN="$(openssl rand -base64 24)" \
     --from-literal=RAILSPEC_GITHUB_APP_CLIENT_ID='Iv1.…' \
     --from-literal=RAILSPEC_GITHUB_APP_CLIENT_SECRET='…' \
     --from-literal=RAILSPEC_GITHUB_APP_ID='123456' \
     --from-file=RAILSPEC_GITHUB_APP_CLAVE_PRIVADA=./clave-privada-de-la-app.pem
   ```

   | Clave | Obligatoria | Uso |
   | --- | --- | --- |
   | `RAILSPEC_MONGO_URI` | sí | Estado y checkpoints. Sin ella el contenedor no arranca (ni con la imagen, que no trae el Mongo simulado, ni en memoria sin `RAILSPEC_PERMITIR_DESARROLLO=1`). |
   | `RAILSPEC_FALKORDB_URL` | no | Grafo central; sin él (y sin `RAILSPEC_GRAFO_POSTGRES`, que lo guarda en la base del estado cuando esta es Postgres: [grafo.md](grafo.md#motor-en-postgres)) no hay `graph.query` ni impacto en el gate de código. |
   | `RAILSPEC_FOUNDRY_API_KEY` | no | Clave de Foundry. Sin ella, Entra ID (Workload Identity si se da `RAILSPEC_AZURE_CLIENT_ID`). |
   | `RAILSPEC_PCE_API_KEY` | no | Gobernanza por defecto (`RAILSPEC_PCE_URL`). Las credenciales de otras herramientas de contexto van por `credencial_ref` ([proveedores.md](proveedores.md#herramientas-de-contexto)). |
   | `RAILSPEC_ANTHROPIC_API_KEY` | si `RAILSPEC_ANTHROPIC_HABILITADO=true` | Anthropic directo, solo nivel `abierto`. |
   | `RAILSPEC_CONSOLA_SECRETO` | sí | Clave de las sesiones de la consola web, de al menos 32 caracteres (`openssl rand -base64 48` da 64). El ConfigMap fija una URL pública https, así que sin ella, o con una más corta, el servidor no arranca. |
   | `RAILSPEC_CLAVE_MAESTRA` | no, pero sin ella la consola no guarda suscripciones de modelos | Clave AES de 32 bytes en base64 (`openssl rand -base64 32`) que cifra las claves de las suscripciones de Foundry y Anthropic. **Guárdala aparte**: si se pierde, hay que volver a escribir las claves. Para rotarla ver [proveedores.md](proveedores.md#suscripciones-de-modelos). |
   | `RAILSPEC_CLAVE_MAESTRA_ANTERIOR` | no | Durante una rotación: la clave anterior (o varias, separadas por coma), solo para descifrar lo guardado. |
   | `RAILSPEC_SMTP_URL` | no | Servidor de correo de los avisos y del informe semanal: `smtp://usuario:clave@host:587?desde=railspec@empresa.com` (usuario y clave codificados como en un URL; `smtps://` para TLS directo; `seguridad=ninguna` solo para un relé interno sin credenciales). Lleva la contraseña: va en el Secret. Sin ella no hay correo (Teams funciona igual). Una URL mal formada impide arrancar. Ver [consola.md](consola.md#avisos-e-informes). |
   | `RAILSPEC_METRICAS_TOKEN` | no | Bearer de respaldo de `GET /metrics` (texto de Prometheus): el scraper envía `Authorization: Bearer <token>`. 16 caracteres o más (`openssl rand -base64 24`); con uno más corto el servidor no arranca. Lo habitual es no definirla y crear una clave por origen en la consola (Plataforma → Operación); sin ella ni claves, `/metrics` responde 404. Ver [Esquema del estado y métricas](#esquema-del-estado-y-métricas). |
   | `RAILSPEC_GITHUB_APP_CLIENT_ID` y `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | sí, para cualquier acceso con token de GitHub | GitHub App de Railspec (ver `consola.md`). Inicia sesión en la consola y comprueba que cada token de GitHub (MCP, `/v1`, `/consola/api`) lo emitió esa App; sin ellas el servidor rechaza todos los tokens de GitHub. |
   | `RAILSPEC_GITHUB_APP_ID` y `RAILSPEC_GITHUB_APP_CLAVE_PRIVADA` | no, juntas | Identidad de la misma App como instalación (id numérico y clave privada PEM). Con ellas la consola lee `contexto.yaml` y `.railspecignore` de los repositorios vinculados y abre un PR para cambiarlos (la App necesita contenido:escritura y pull requests:escritura); sin ellas esa edición queda en modo manual (la consola da el diff). Ver [consola.md](consola.md#archivos-del-repositorio). |

**Modo desarrollo apagado.** `RAILSPEC_TOKENS_DESARROLLO` y
`RAILSPEC_PERMITIR_DESARROLLO` no son variables del despliegue: el Deployment
las fija vacías en `env`, que gana a `envFrom`, para que una clave sobrante
en este Secret no pueda activar los tokens de desarrollo (identidad de
GitHub sustituida y acceso por `POST /consola/api/auth/desarrollo`) ni el
modo en memoria. Aun sin esa guarda, el servidor se niega a arrancar con
tokens de desarrollo si hay `RAILSPEC_MONGO_URI` o GitHub App, salvo
`RAILSPEC_PERMITIR_DESARROLLO=1`.

## Variables de los manifiestos

`python3 railspec/deploy/renderizar.py --variables` imprime esta tabla. El
significado de las variables de proveedores, catálogo y contexto, y las que
no pasan por el renderizador (`RAILSPEC_FOUNDRY_PROYECTO_API_VERSION`,
`RAILSPEC_SECRETOS_DIR`), está en [proveedores.md](proveedores.md). Las que fijan
cuándo se borra lo abandonado en el grafo y cuándo se avisa que está viejo
(`RAILSPEC_GRAFO_*`) se explican en [Lo que queda a
medias](#lo-que-queda-a-medias).

| Variable | Defecto | Uso |
| --- | --- | --- |
| `RAILSPEC_IMAGEN` | obligatoria | Imagen por digest; el workflow de imagen la deja en el resumen del job. |
| `RAILSPEC_DOMINIO` | obligatoria | Host público del Ingress. |
| `RAILSPEC_TLS_SECRETO` | obligatoria | Secret TLS del dominio. |
| `RAILSPEC_NAMESPACE` | `railspec` | Namespace. |
| `RAILSPEC_SECRETO` | `railspec-server` | Secret con las claves de arriba. |
| `RAILSPEC_INGRESS_CLASE` | `webapprouting.kubernetes.azure.com` | IngressClass (`nginx` con ingress-nginx). |
| `RAILSPEC_REPLICAS` | `2` | Réplicas. El motor admite varias: un turno por unidad en Mongo. |
| `RAILSPEC_CPU_SOLICITUD` | `250m` | CPU solicitada (sin límite de CPU). |
| `RAILSPEC_MEMORIA` | `1Gi` | Memoria solicitada y límite. |
| `RAILSPEC_MONGO_DB` | `railspec` | Base de datos. |
| `RAILSPEC_FOUNDRY_ENDPOINT` | vacío | Recurso de Azure AI Foundry. |
| `RAILSPEC_FOUNDRY_REGION` | vacío | Región del recurso (`eastus2`), la que se audita en los despliegues Standard. No restringe qué repositorios sirve el modelo. |
| `RAILSPEC_FOUNDRY_ZONA_DATOS` | vacío | Zona de datos del recurso (`us`, `eu`), la de los SKU DataZone (se audita; no restringe). |
| `RAILSPEC_FOUNDRY_PROYECTO` | vacío | Endpoint del proyecto de Foundry para leer los despliegues por API. |
| `RAILSPEC_FOUNDRY_DESPLIEGUES` | vacío | Despliegues declarados en la forma compacta `despliegue=modelo[:SKU],…`. La lista JSON no cabe en el ConfigMap: el renderizador la rechaza. |
| `RAILSPEC_CATALOGO_TTL_S` | `3600` | Vigencia del catálogo de modelos, en segundos. |
| `RAILSPEC_CACHE_NODOS_S` | `86400` | Caché de nodos de modelo por hash de entradas, en segundos; `0` la desactiva. |
| `RAILSPEC_PCE_URL` | vacío | Gobernanza por MCP; sin ella todo gate escala con `sin-gobernanza`. |
| `RAILSPEC_PROVEEDORES_HOSTS` | vacío | Hosts permitidos (coma o espacio; `*.dominio` admite subdominios) para los proveedores de contexto que configura una organización; se suma el host de `RAILSPEC_PCE_URL`. **Vacía, ninguna organización configura proveedores.** Los secretos de `credencial_ref` se llaman `<org>--<nombre>` ([proveedores.md](proveedores.md#herramientas-de-contexto)). |
| `RAILSPEC_CONTEXTO_CACHE_S` | `900` | Caché de consultas a las herramientas de contexto, en segundos; `0` la desactiva. |
| `RAILSPEC_VINCULOS_OWNERS` | vacío | Owners de GitHub (coma) que puede vincular una organización sin `github_org`. Vacía: esas organizaciones no vinculan repositorios hasta que la plataforma les fije `github_org` ([consola.md](consola.md#vínculos-de-repositorio)). |
| `RAILSPEC_ANTHROPIC_HABILITADO` | `false` | Anthropic directo. |
| `RAILSPEC_AZURE_CLIENT_ID` | vacío | Identidad administrada para Workload Identity (Foundry por Entra ID). Activa la etiqueta del pod. |
| `RAILSPEC_OIDC_AUDIENCIA` | vacío (OIDC desactivado) | Audiencia del token OIDC de CI; debe coincidir con la variable del mismo nombre del repositorio que corre el workflow de reindexado. **Un valor largo y no adivinable** (`openssl rand -hex 24`): cualquier repositorio de GitHub puede pedir un token con la audiencia que quiera, y `railspec` se rechaza. Vacía desactiva `graph.index` (el renderizador respeta la cadena vacía). |
| `RAILSPEC_OIDC_EMISOR` | `https://token.actions.githubusercontent.com` | Emisor OIDC. |
| `RAILSPEC_OIDC_REPOSITORIOS` | vacío | Lista `owner/repo,…` de repositorios que pueden llamar `graph.index`. **Obligatoria con audiencia**: sin ella el renderizador y el servidor se niegan (antes, vacía admitía a cualquier repositorio con vínculo). |
| `RAILSPEC_CONSOLA_ADMINS` | vacío | `github_id` (numéricos, separados por coma) que administran la plataforma en la consola: crean organizaciones y son `org-admin` en todas. |
| `RAILSPEC_CONSOLA_SESION_HORAS` | `4` | Vida de la sesión de la consola, de 1 a 24 horas. Es también la ventana en que quedan congelados los equipos de GitHub de la cookie ([consola.md](consola.md#variables-de-entorno)). |
| `RAILSPEC_CONSOLA_AUTH_LIMITE` | `60` | Peticiones por minuto y por IP en `/consola/api/auth/*`; `0` lo desactiva. Depende de `FORWARDED_ALLOW_IPS` (ver arriba). |
| `RAILSPEC_CONSOLA_SSE_MAX_USUARIO` | `5` | Flujos de eventos en vivo abiertos a la vez por persona y por réplica; al excederlo, 429. |
| `RAILSPEC_CONSOLA_SSE_MAX_GLOBAL` | `200` | Ídem en total por réplica. |
| `RAILSPEC_CONSOLA_SSE_REVALIDAR_S` | `30` | Cada cuántos segundos un flujo en vivo vuelve a comprobar el rol `lector` y se cierra si lo perdió. |
| `RAILSPEC_CHAT_MODELO` | vacío | Despliegue de Foundry del rol `chat`. Vacío, el del servidor (`claude-sonnet-5-5`). |
| `RAILSPEC_CHAT_CLONES_PVC` | vacío | PersistentVolumeClaim con un clon por repositorio. El Deployment lo monta de solo lectura en `/var/lib/railspec/clones` y el ConfigMap le pasa esa ruta al servidor como `RAILSPEC_CHAT_CLONES`. Vacío, el chat responde sin leer código. Ver [Chat de contexto](#chat-de-contexto-modelo-y-clones). |

## Chat de contexto: modelo y clones

El chat ([chat.md](chat.md)) responde sin código mientras no tenga clones. El
renderizador avisa por stderr (`renderizar: aviso: …`) cuando
`RAILSPEC_CHAT_CLONES_PVC` queda vacía. Desde el 2026-10-06 el chat **ya no
exige zona de datos** (`RAILSPEC_CHAT_ZONA_DATOS` se eliminó): el nivel del
repositorio no restringe qué proveedor o región usa, solo qué material de código
puede salir en las respuestas.

- **Modelo.** `RAILSPEC_CHAT_MODELO` es el despliegue del rol `chat`; tiene que
  existir en el catálogo de Foundry ([proveedores.md](proveedores.md)) o en la
  suscripción del perfil por defecto del workspace.
- **Clones.** `code.read` y la comprobación de referencias obsoletas de los
  insumos leen con `git show` el repositorio vinculado en
  `<RAILSPEC_CHAT_CLONES>/<owner>/<repo>`, en `refs/remotes/origin/<rama>` o
  `refs/heads/<rama>`. La imagen trae `git`. Con `RAILSPEC_CHAT_CLONES_PVC`
  el Deployment monta ese PVC en `/var/lib/railspec/clones`, `readOnly`, y la
  raíz del contenedor sigue de solo lectura; el servidor no escribe en él. El
  volumen debe admitir `ReadWriteMany` (varias réplicas, más quien lo
  actualiza). No hace falta que los archivos sean del uid 10001: `ClonesGit`
  declara `safe.directory` solo para cada clon.

Quién llena y mantiene los clones no lo decide el despliegue: se necesita algo
que corra `git` con un token de lectura. Lo más simple es un CronJob con la
misma imagen del servidor (ya trae `git` y corre como 10001). **Ejemplo sin
probar en un clúster** (el bucle sí se ejecutó contra repositorios locales:
clona, actualiza, poda ramas borradas y un repositorio que falla no detiene a
los demás). Un clon `--bare` basta, no deja árbol de trabajo y se actualiza con
`fetch` a `refs/heads/*`.

```yaml
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: railspec-clones            # el valor de RAILSPEC_CHAT_CLONES_PVC
  namespace: railspec
spec:
  accessModes: [ReadWriteMany]
  storageClassName: azurefile-csi  # cualquiera que admita ReadWriteMany
  resources:
    requests:
      storage: 10Gi
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: railspec-clones
  namespace: railspec
spec:
  schedule: "*/15 * * * *"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 1
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      backoffLimit: 1
      template:
        spec:
          restartPolicy: Never
          automountServiceAccountToken: false
          securityContext:
            runAsNonRoot: true
            runAsUser: 10001
            runAsGroup: 10001
            seccompProfile:
              type: RuntimeDefault
          containers:
            - name: actualizar
              image: registro.azurecr.io/railspec-server@sha256:…   # la misma que el servidor
              command: ["/bin/sh", "-ceu"]
              args:
                - |
                  fallo=0
                  export GIT_CONFIG_COUNT=1
                  export GIT_CONFIG_KEY_0=http.https://github.com/.extraheader
                  export GIT_CONFIG_VALUE_0="Authorization: Basic $(printf 'x-access-token:%s' "$GITHUB_TOKEN" | base64 | tr -d '\n')"
                  cd /clones
                  for repo in $REPOSITORIOS; do
                    if [ -d "$repo" ]; then
                      git -C "$repo" fetch --quiet --prune origin '+refs/heads/*:refs/heads/*'
                    else
                      mkdir -p "$(dirname "$repo")" && rm -rf "$repo.tmp" &&
                        git clone --quiet --bare "https://github.com/$repo.git" "$repo.tmp" && mv "$repo.tmp" "$repo"
                    fi || { echo "falló $repo" >&2; fallo=1; }
                  done
                  exit $fallo
              env:
                - name: HOME
                  value: /tmp
                - name: REPOSITORIOS            # <owner>/<repo> de los vínculos, separados por espacios
                  value: "acme/certificados-api acme/web"
                - name: GITHUB_TOKEN
                  valueFrom:
                    secretKeyRef:
                      name: railspec-clones     # lo creas tú; no está en el repositorio
                      key: GITHUB_TOKEN
              securityContext:
                allowPrivilegeEscalation: false
                readOnlyRootFilesystem: true
                capabilities:
                  drop: ["ALL"]
              volumeMounts:
                - name: clones
                  mountPath: /clones
                - name: tmp
                  mountPath: /tmp
          volumes:
            - name: clones
              persistentVolumeClaim:
                claimName: railspec-clones
            - name: tmp
              emptyDir:
                sizeLimit: 64Mi
```

El token (`GITHUB_TOKEN`) necesita solo lectura de contenido en los repositorios
de la lista: un token de acceso personal de grano fino, o el de instalación de una
GitHub App (este caduca en una hora y habría que acuñarlo antes de cada
corrida). Si el volumen no deja escribir al uid 10001, fija `uid=10001,gid=10001`
en `mountOptions` de la StorageClass. Mientras un repositorio no esté clonado,
`code.read` responde `no-encontrado` para ese repositorio y el chat contesta
sin su código.

## Desplegar

```
export RAILSPEC_IMAGEN=miacr.azurecr.io/railspec-server@sha256:…
export RAILSPEC_DOMINIO=railspec.midominio.com
export RAILSPEC_TLS_SECRETO=railspec-tls
export RAILSPEC_FOUNDRY_ENDPOINT=https://….services.ai.azure.com
export RAILSPEC_FOUNDRY_REGION=eastus2
export RAILSPEC_CHAT_CLONES_PVC=railspec-clones   # opcional; ver Chat de contexto
python3 railspec/deploy/renderizar.py > railspec.yaml
kubectl apply --dry-run=server -f railspec.yaml
kubectl apply -f railspec.yaml
kubectl -n railspec rollout status deploy/railspec-server
curl https://$RAILSPEC_DOMINIO/healthz
```

El proxy local apunta a `https://<dominio>/mcp/` y la consola a
`https://<dominio>/v1/tools`. Sondas: arranque y vida en `GET /livez` (200
mientras el proceso atiende) y disponibilidad en `GET /healthz` (503 si Mongo
o FalkorDB no responden), así una caída de la base saca la réplica del
Service sin reiniciarla.

La raíz `/` redirige (307) a `/consola/` cuando el servidor sirve la SPA
(`RAILSPEC_CONSOLA_DIR`); sin SPA no hay a dónde redirigir y responde 404.

### Esquema del estado y métricas

Al arrancar, el servidor deja la versión del esquema del estado en la colección
`meta_esquema` (`estado/esquema.py`). Con una base vacía escribe la suya; con una
versión menor aplica las migraciones registradas, en orden y de forma
idempotente; con una **mayor** no arranca (`EsquemaIncompatible`): un retroceso
de despliegue no debe escribir sobre un estado que no entiende. Cambiar la forma
de lo guardado de modo que el código anterior no lo lea exige subir
`VERSION_ESQUEMA` y registrar su migración en `MIGRACIONES`.

`GET /metrics` (texto de Prometheus) exige `Authorization: Bearer <clave>`.
Vale una clave creada en la consola (Plataforma → Operación, una por origen,
revocable; ver [consola.md](consola.md#operación-del-servidor-y-claves-de-métricas))
o `RAILSPEC_METRICAS_TOKEN` (16 caracteres o más), que sigue valiendo como
respaldo. Sin ninguna de las dos responde 404. Publica la versión del servidor, la versión del
esquema del código y la guardada (`railspec_estado_esquema{origen}`), el estado
de cada sonda (`railspec_sonda_ok{sonda}`), las peticiones por superficie y
clase de estado y el instante de arranque. Los contadores son de cada réplica:
Prometheus los suma.

Un trabajo de Prometheus que lo consulta con el token como credencial (en
Render, `scheme: https` y el host del servicio; en AKS, el Service interno):

```yaml
scrape_configs:
  - job_name: railspec
    metrics_path: /metrics
    scheme: https
    authorization:
      type: Bearer
      credentials_file: /etc/prometheus/secretos/railspec-metricas-token
    static_configs:
      - targets: ["railspec.onrender.com"]
```

Para rotar sin cortes, crea en la consola una clave nueva para ese origen con
otro nombre, cámbiala en el archivo de credenciales del scraper y después
revoca la vieja. Con `RAILSPEC_METRICAS_TOKEN` hay que cambiarlo, reiniciar el
servicio y actualizar el scraper en seguida: entre ambos pasos las consultas
reciben 401, que `up == 0` refleja sin perder las series.
Las mismas alertas, en formato de reglas de Prometheus, están en
`deploy/prometheus/alertas.yaml` (`rule_files` del servidor Prometheus; añaden
`RailspecSinMetricas`, que dispara si el scrape deja de funcionar). Para
comprobar a mano que un servidor desplegado las alimentaría, sin Prometheus:

```bash
RAILSPEC_METRICAS_TOKEN=... python3 railspec/deploy/prometheus/verificar_metricas.py https://railspec.onrender.com
```

La variable lleva la clave, sea una de la consola o el token del servicio.
Sale con 0 si `/metrics` responde 200 con las familias esperadas y todas las
sondas en 1; con 1 y la causa (401 clave distinta o revocada, 404 sin token ni claves,
sonda caída, esquema desalineado) si no. Las alertas del archivo solo se
verificaron contra el formato que publica el servidor, no contra un
Prometheus real.

Alertas mínimas sugeridas:

| Alerta | Expresión | Para qué |
| --- | --- | --- |
| Servicio no responde a las sondas | `railspec_sonda_ok == 0` sostenido 5 minutos | La base o el grafo no responden |
| Errores del servidor | `sum(rate(railspec_http_peticiones_total{estado="5xx"}[5m])) > 0` sostenido | Respuestas 5xx por superficie |
| Esquema desalineado | `railspec_estado_esquema{origen="codigo"} != on() railspec_estado_esquema{origen="almacenado"}` | Código y estado guardado en versiones distintas |
| Reinicios repetidos | `changes(railspec_proceso_inicio_segundos[1h]) > 2` | El proceso se reinicia en bucle |

## Reindexado del canónico

`railspec-reindexar.yml` corre en cada push a `master` si la variable
`RAILSPEC_URL` del repositorio está definida; además necesita
`RAILSPEC_ORGANIZACION`, `RAILSPEC_WORKSPACE` y, si el slug no es el nombre
del repositorio, `RAILSPEC_REPOSITORIO`. `RAILSPEC_OIDC_AUDIENCIA` (sin valor por defecto, la
misma del servidor) es la audiencia del token.

- Delta entre `github.event.before` y el commit empujado; índice completo si
  no hay commit anterior utilizable (rama nueva, force-push) o si el servidor
  responde 409 `base-commit-distinto` (el canónico no está en esa base). Un
  lanzamiento manual con `completo` fuerza el completo.
- **Índice completo semanal.** Un disparo `schedule` (lunes 05:17 UTC) corre el
  modo `completo` sin intervención: reemplaza lo que los deltas acumularon y
  corrige cualquier divergencia que se les hubiera escapado, sin trabajo manual.
  Declara `commits_cubiertos` como cualquier índice (no es `retirar_todas`: las
  superposiciones varadas siguen pidiendo ese lanzamiento manual). El job
  programado no corre en un fork ni si faltan `RAILSPEC_ORGANIZACION`,
  `RAILSPEC_WORKSPACE` o `RAILSPEC_OIDC_AUDIENCIA` (sin ellas fallaría cada semana
  en lugar de no correr); el push conserva su condición de siempre. Comparte el
  grupo de concurrencia con los demás reindexados, así que no se pisa con un push.
  GitHub desactiva los programados de un repositorio público sin actividad en 60 días: un
  `workflow_dispatch` o un push lo reactiva.
- **Resumen del contenido (contrato 1.10).** Con el índice, el job calcula el
  resumen del commit (por archivo, cuántos símbolos tiene y una huella de sus
  `id` y `sha256`; el algoritmo es `railspec.contracts.resumen`, el mismo que usa
  el servidor) y lo manda en el último lote (`lote == lotes`), con
  `version_contrato` `1.10`. El servidor lo compara con el canónico tras aplicar
  el índice y avisa la divergencia en `graph.query`. Detalles:
  - Cubre el **árbol completo** del commit, no el delta. En un índice completo
    sale del propio delta; en uno incremental el job le pide al indexador el índice
    completo del árbol (una pasada más de `codebase-memory-mcp` en el runner,
    con las mismas exclusiones de secretos y el mismo parser) y toma solo los
    símbolos. Manda todo lo que el indexador ve: las exclusiones del vínculo las
    aplica el servidor en ambos lados.
  - **Tope.** `MAX_ARCHIVOS_RESUMEN` (20 000 archivos con símbolos). Si el commit
    tiene más, el job no manda un resumen recortado (cada archivo que faltara sería
    una divergencia falsa): lo omite, habla 1.9 y lo dice en la salida (`se sube sin
    verificación de contenido`). Tampoco se manda si el cálculo falla; el índice
    sube igual, porque verificar nunca debe impedir que el canónico avance.
  - `--sin-resumen` en `reindexar.py` lo apaga a propósito (1.9, sin la pasada
    completa en los deltas) y es independiente de `--sin-cobertura`, que habla 1.4
    y por eso tampoco manda resumen.
  - **Servidor 1.9.** Uno que rechaza `resumen` o la versión `1.10` con 422
    recibe el índice sin él, con `version_contrato` `1.9` y sus
    `commits_cubiertos`, y el job avisa `sin verificación de contenido`. Un
    servidor 1.4 retrocede en dos pasos (primero el resumen, luego la lista). Un
    422 sobre el contenido del resumen (`resumen.archivos...`) no se reintenta:
    sería un error del cliente. Despliega primero el servidor.
- Desde el contrato 1.5 cada índice declara en `commits_cubiertos` los
  commits que incorpora (`git rev-list --first-parent`, hasta 1000) y el
  servidor retira solo las superposiciones de unidades integradas en ellos.
  Si una superposición quedó retenida sin que ningún índice la cubra, un
  lanzamiento manual con `retirar_todas` sube un índice completo sin cobertura
  y retira todas las retenidas (desde que existe `RAILSPEC_GRAFO_RETENIDAS_DIAS`
  una retenida varada también se retira sola al vencer su plazo, y el servidor
  recuerda los commits que cubrieron los últimos índices para retirar al instante
  la de una unidad integrada en uno de ellos). Despliega primero el servidor: uno 1.4 rechaza
  la lista con 422 y `reindexar.py` sube entonces el índice sin ella (regla de
  1.4, con lo que el índice completo vuelve a retirar todas las retenidas).
- Otros errores detienen el job sin reintentar: 401 token inválido, 403
  `fuera-de-alcance` (repositorio o rama distintos de los del vínculo), 404
  sin vínculo, 422 `snapshot-invalido` (lotes de un commit que no casan).
  Los 5xx y 429 se reintentan hasta cuatro veces.
- Las exclusiones de secretos son las del proxy (`.railspecignore` incluido)
  y el servidor las vuelve a aplicar.
- Un solo reindexado a la vez; si se encola más de uno, GitHub descarta los
  intermedios y el siguiente cae en índice completo.

El servidor verifica el token: firma contra el JWKS del emisor, `iss`,
`aud`, caducidad y que `repository` esté en `RAILSPEC_OIDC_REPOSITORIOS`
(obligatoria). La tool exige además que `repository` sea el de la URL del
vínculo y que el `@ref` de `workflow_ref` sea `refs/heads/<rama por defecto>`
del vínculo. La identidad de servicio no tiene rol en ninguna organización ni
workspace: `graph.index` es la única tool que la admite (`tipos_actor`), así
que un OIDC válido no puede leer unidades, órdenes, telemetría ni grafo.

### Lo que queda a medias

Una corrida de CI que falla entre lotes deja en FalkorDB el grafo de
preparación `...:i:<commit>` del índice, y una unidad que nadie retoma deja su
superposición. El servidor los borra solo (paquete `railspec-graph`; ver
[grafo.md](grafo.md#lo-abandonado-superposiciones-y-preparaciones)); no hay
nada que limpiar a mano.

| Variable | Defecto | Uso |
| --- | --- | --- |
| `RAILSPEC_GRAFO_SUPERPOSICION_DIAS` | `30` | Días sin snapshot nuevo tras los que se borra la superposición de una unidad no integrada (la retención por defecto de los snapshots; este plazo es del servidor, no del vínculo). Las integradas esperan a que un índice cubra su commit y caducan por `RAILSPEC_GRAFO_RETENIDAS_DIAS`. |
| `RAILSPEC_GRAFO_RETENIDAS_DIAS` | `30` | Días desde que se integra una unidad tras los que se retira su superposición retenida si ningún índice cubrió su commit. `graph.query` con `unidad` avisa desde la mitad del plazo; `GET /consola/api/orgs/{org}/workspaces/{ws}/grafo/retenidas` las lista. `0` no caduca nunca. El barrido corre al llegar un `graph.index`. |
| `RAILSPEC_GRAFO_INDEXADO_HORAS` | `24` | Horas sin lotes nuevos tras las que se borra la preparación de un índice que no completó. |
| `RAILSPEC_GRAFO_FRESCURA_HORAS` | `72` | Horas desde el último índice canónico aplicado tras las que `graph.query` marca el repositorio como `desactualizado` y lo avisa en `avisos` (y el contexto de las órdenes de spec, plan y tasks, en `grafo_avisos`). No borra nada; `0` no avisa nunca. Un canónico sin instante guardado (indexado antes de esta variable) no se marca hasta su próximo índice. |

- `0` desactiva cada una (en la frescura, `0` deja de avisar) y admiten fracciones (`0.5`). El servidor las lee al
  arrancar: un valor que no es un número, negativo o desmesurado impide
  arrancar, con el nombre de la variable en el error.
- Pasan por `renderizar.py` al ConfigMap con esos mismos defectos, así que
  con ellos no hay nada que hacer. El renderizador solo admite decimales
  (`7`, `0.5`; no `1e3`) y rechaza un valor negativo o desmesurado antes de
  aplicar nada al clúster, porque el servidor no arrancaría con él. Vacía
  vuelve al defecto, como en el servidor; `0` llega tal cual.
- El barrido corre cuando llega un `graph.index` del repositorio (un índice
  aplicado, o el primer lote de un commit nuevo), así que un repositorio que
  no se indexa no se barre. Lo borrado queda en el log del servidor (INFO,
  `railspec.graph.indexado`); la respuesta de `graph.index` no cambia.
- Un sello de actividad nuevo acompaña a las superposiciones y preparaciones
  que se escriban desde ahora; las que ya existen se sellan en el primer
  barrido y su plazo cuenta desde ese momento.

## Pendiente fuera de este directorio

- **Coste del resumen en los deltas.** Calcularlo en un push incremental suma una
  pasada de índice completo al job (ver arriba); no está medida contra el límite de
  60 minutos. `--sin-resumen` la quita si un repositorio grande se pasa, a costa de
  la verificación; el índice completo semanal la paga una vez por semana, sobre
  el propio delta.
- **Tiempo de un índice completo.** El job usa el mismo indexador que el
  proxy (`railspec.local.indexador_cbm`): una sola sesión MCP por stdio con
  `codebase-memory-mcp` por delta, con el arranque (unos 6 s) pagado una vez y
  unas cien filas por página ([proxy-local.md](proxy-local.md#indexador-local));
  solo si la sesión no arranca vuelve al modo `cli`, que paga el arranque en
  cada página. No hay una medición de un índice completo de un repositorio
  grande contra el límite de 60 minutos del job (`timeout-minutes` de
  `railspec-reindexar.yml`); si una corrida se pasa, lo que dejó a medias lo
  borra el barrido de arriba.
