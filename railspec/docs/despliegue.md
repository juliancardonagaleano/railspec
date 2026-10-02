# Despliegue de `railspec-server` en AKS

Todo lo desplegable vive en `railspec/deploy/` y en `.github/workflows/`:

| Pieza | Qué es |
| --- | --- |
| `deploy/servidor/Dockerfile` | Imagen del servidor. Base fijada por digest, dependencias desde `requirements.lock` con `--require-hashes`, usuario 10001, raíz de solo lectura. |
| `deploy/servidor/requirements.lock` | Lock con hashes de las dependencias de terceros. Se regenera con `deploy/servidor/bloquear.sh` al cambiar `requirements.in` o los `pyproject`. |
| `deploy/k8s/*.yaml` | Namespace, ServiceAccount, ConfigMap, Deployment, Service, Ingress y PodDisruptionBudget, con `${VARIABLES}`; y, opcionales por bandera (Mongo y FalkorDB en el clúster, NetworkPolicy, respaldos, clones del chat), los de [despliegue-datos.md](despliegue-datos.md). |
| `deploy/renderizar.py` | Sustituye las variables desde el entorno y falla si falta una obligatoria. Solo biblioteca estándar. |
| `deploy/ci/reindexar.py` | Cliente de `graph.index` que usa el workflow de reindexado. |
| `railspec-ci.yml` | Lint, pruebas de cada paquete por separado y validación de manifiestos con kubeconform. |
| `railspec-imagen.yml` | Construye la imagen en cada PR (con prueba de humo) y la publica en `master` y en tags `railspec-server-v*`. |
| `railspec-reindexar.yml` | En cada push a `master`, sube el índice del canónico a `graph.index` con OIDC. |

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
     --from-literal=RAILSPEC_GITHUB_APP_CLIENT_ID='Iv1.…' \
     --from-literal=RAILSPEC_GITHUB_APP_CLIENT_SECRET='…'
   ```

   | Clave | Obligatoria | Uso |
   | --- | --- | --- |
   | `RAILSPEC_MONGO_URI` | sí | Estado y checkpoints. Sin ella el contenedor no arranca (ni con la imagen, que no trae el Mongo simulado, ni en memoria sin `RAILSPEC_PERMITIR_DESARROLLO=1`). |
   | `RAILSPEC_FALKORDB_URL` | no | Grafo central; sin él no hay `graph.query` ni impacto en el gate de código. |
   | `RAILSPEC_FOUNDRY_API_KEY` | no | Clave de Foundry. Sin ella, Entra ID (Workload Identity si se da `RAILSPEC_AZURE_CLIENT_ID`). |
   | `RAILSPEC_PCE_API_KEY` | no | Gobernanza por defecto (`RAILSPEC_PCE_URL`). Las credenciales de otras herramientas de contexto van por `credencial_ref` ([proveedores.md](proveedores.md#herramientas-de-contexto)). |
   | `RAILSPEC_ANTHROPIC_API_KEY` | si `RAILSPEC_ANTHROPIC_HABILITADO=true` | Anthropic directo, solo nivel `abierto`. |
   | `RAILSPEC_CONSOLA_SECRETO` | sí | Clave de las sesiones de la consola web, de al menos 32 caracteres (`openssl rand -base64 48` da 64). El ConfigMap fija una URL pública https, así que sin ella, o con una más corta, el servidor no arranca. |
   | `RAILSPEC_GITHUB_APP_CLIENT_ID` y `RAILSPEC_GITHUB_APP_CLIENT_SECRET` | sí, para cualquier acceso con token de GitHub | GitHub App de Railspec (ver `consola.md`). Inicia sesión en la consola y comprueba que cada token de GitHub (MCP, `/v1`, `/consola/api`) lo emitió esa App; sin ellas el servidor rechaza todos los tokens de GitHub. |

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
`RAILSPEC_SECRETOS_DIR`), está en [proveedores.md](proveedores.md).

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
| `RAILSPEC_FOUNDRY_REGION` | vacío | Región del recurso (`eastus2`). Sin ella, `restringido` e `interno` no tienen modelo en zona. |
| `RAILSPEC_FOUNDRY_ZONA_DATOS` | vacío | Zona de datos del recurso (`us`, `eu`), la de los SKU DataZone. |
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
| `RAILSPEC_CHAT_ZONA_DATOS` | vacío | Regiones de Azure (coma, **minúsculas**: `eastus2,swedencentral`) donde el chat puede enviar código a un modelo en `restringido` e `interno`. **Vacía, el chat no responde en esos niveles** (falla cerrado) y el renderizador lo avisa por stderr. Ver [Chat de contexto](#chat-de-contexto-zona-de-datos-modelo-y-clones). |
| `RAILSPEC_CHAT_MODELO` | vacío | Despliegue de Foundry del rol `chat`. Vacío, el del servidor (`claude-sonnet-5-5`). |
| `RAILSPEC_CHAT_CLONES_PVC` | vacío | PersistentVolumeClaim con un clon por repositorio. El Deployment lo monta de solo lectura en `/var/lib/railspec/clones` y el ConfigMap le pasa esa ruta al servidor como `RAILSPEC_CHAT_CLONES`. Vacío, el chat responde sin leer código. Ver [Chat de contexto](#chat-de-contexto-zona-de-datos-modelo-y-clones). |

## Chat de contexto: zona de datos, modelo y clones

El chat ([chat.md](chat.md)) falla cerrado: sin estos valores se niega o
responde sin código. El renderizador avisa por stderr (`renderizar: aviso: …`)
cuando `RAILSPEC_CHAT_ZONA_DATOS` o `RAILSPEC_CHAT_CLONES_PVC` quedan vacías.

- **Zona de datos.** `RAILSPEC_CHAT_ZONA_DATOS` lista las regiones de Azure
  donde el chat puede enviar código; para un despliegue de Foundry con SKU
  `DataZone*` la región es `zona-us` o `zona-eu`
  ([despliegue-datos.md](despliegue-datos.md#zona-de-datos-del-chat)). En
  `restringido` e `interno` solo sirve un
  despliegue de Foundry con región fija y esa región debe estar en la lista,
  escrita en minúsculas igual que la que devuelve Azure (una mayúscula no
  falla al arrancar: el chat se niega con 422 `perfil-insatisfacible`, y por
  eso el renderizador la rechaza). Pon la región de tu recurso de Foundry
  (`RAILSPEC_FOUNDRY_REGION`). En `abierto` no hace falta salvo que el vínculo
  exija hosting en la zona de datos.
- **Modelo.** `RAILSPEC_CHAT_MODELO` es el despliegue del rol `chat`; tiene que
  existir en el catálogo de Foundry ([proveedores.md](proveedores.md)) y, en
  `restringido` e `interno`, ser de un SKU DataZone o Standard.
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
export RAILSPEC_CHAT_ZONA_DATOS=eastus2
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
- Desde el contrato 1.5 cada índice declara en `commits_cubiertos` los
  commits que incorpora (`git rev-list --first-parent`, hasta 1000) y el
  servidor retira solo las superposiciones de unidades integradas en ellos.
  Si una superposición quedó retenida sin que ningún índice la cubra, un
  lanzamiento manual con `retirar_todas` sube un índice completo sin cobertura
  y retira todas las retenidas. Despliega primero el servidor: uno 1.4 rechaza
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

## Pendiente fuera de este directorio

- El indexador local por CLI de `codebase-memory-mcp` pagina de a unas 60 a
  180 filas y cada llamada cuesta unos 4 s de arranque, así que un índice
  completo de un repositorio mediano tarda decenas de minutos (el job tiene
  60 de límite). Es del paquete `railspec-local`.
