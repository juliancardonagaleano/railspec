# Despliegue de `railspec-server` en AKS

Todo lo desplegable vive en `railspec/deploy/` y en `.github/workflows/`:

| Pieza | Qué es |
| --- | --- |
| `deploy/servidor/Dockerfile` | Imagen del servidor. Base fijada por digest, dependencias desde `requirements.lock` con `--require-hashes`, usuario 10001, raíz de solo lectura. |
| `deploy/servidor/requirements.lock` | Lock con hashes de las dependencias de terceros. Se regenera con `deploy/servidor/bloquear.sh` al cambiar `requirements.in` o los `pyproject`. |
| `deploy/k8s/*.yaml` | Namespace, ServiceAccount, ConfigMap, Deployment, Service, Ingress y PodDisruptionBudget, con `${VARIABLES}`. |
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

## Desplegar

```
export RAILSPEC_IMAGEN=miacr.azurecr.io/railspec-server@sha256:…
export RAILSPEC_DOMINIO=railspec.midominio.com
export RAILSPEC_TLS_SECRETO=railspec-tls
export RAILSPEC_FOUNDRY_ENDPOINT=https://….services.ai.azure.com
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

- La identidad OIDC, el manejador de `graph.index` y `/livez` llegan con el
  cambio del hilo de integración, aún no en `master`. Hasta que llegue, no
  definir `RAILSPEC_URL` (el reindexado daría 401 o 404) ni desplegar estos
  manifiestos (la sonda de vida en `/livez` daría 404 y reiniciaría las
  réplicas).
- El indexador local por CLI de `codebase-memory-mcp` pagina de a unas 60 a
  180 filas y cada llamada cuesta unos 4 s de arranque, así que un índice
  completo de un repositorio mediano tarda decenas de minutos (el job tiene
  60 de límite). Es del paquete `railspec-local`.
