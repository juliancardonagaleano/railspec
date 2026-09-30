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
     --from-literal=RAILSPEC_PCE_API_KEY='…'
   ```

   | Clave | Obligatoria | Uso |
   | --- | --- | --- |
   | `RAILSPEC_MONGO_URI` | sí | Estado y checkpoints. Sin ella el contenedor no arranca: la imagen no trae el Mongo simulado de desarrollo. |
   | `RAILSPEC_FALKORDB_URL` | no | Grafo central; sin él no hay `graph.query` ni impacto en el gate de código. |
   | `RAILSPEC_FOUNDRY_API_KEY` | no | Clave de Foundry. Sin ella, Entra ID (Workload Identity si se da `RAILSPEC_AZURE_CLIENT_ID`). |
   | `RAILSPEC_PCE_API_KEY` | no | Gobernanza. |
   | `RAILSPEC_ANTHROPIC_API_KEY` | si `RAILSPEC_ANTHROPIC_HABILITADO=true` | Anthropic directo, solo nivel `abierto`. |

## Variables de los manifiestos

`python3 railspec/deploy/renderizar.py --variables` imprime esta tabla.

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
| `RAILSPEC_PCE_URL` | vacío | Gobernanza por MCP; sin ella todo gate escala con `sin-gobernanza`. |
| `RAILSPEC_ANTHROPIC_HABILITADO` | `false` | Anthropic directo. |
| `RAILSPEC_AZURE_CLIENT_ID` | vacío | Identidad administrada para Workload Identity (Foundry por Entra ID). Activa la etiqueta del pod. |

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
`https://<dominio>/v1/tools`. Las sondas usan `GET /healthz`.

## Reindexado del canónico

`railspec-reindexar.yml` corre en cada push a `master` si la variable
`RAILSPEC_URL` del repositorio está definida; además necesita
`RAILSPEC_ORGANIZACION`, `RAILSPEC_WORKSPACE` y, si el slug no es el nombre
del repositorio, `RAILSPEC_REPOSITORIO`. `RAILSPEC_OIDC_AUDIENCIA` (por
defecto `railspec`) es la audiencia del token.

- Delta entre `github.event.before` y el commit empujado; índice completo si
  no hay commit anterior utilizable (rama nueva, force-push) o si el servidor
  responde que el canónico no está en esa base (`base-commit-distinto` o
  `conflicto-version`). Un lanzamiento manual con `completo` fuerza el
  completo.
- Las exclusiones de secretos son las del proxy (`.railspecignore` incluido)
  y el servidor las vuelve a aplicar.
- Un solo reindexado a la vez; si se encola más de uno, GitHub descarta los
  intermedios y el siguiente cae en índice completo.

Lo que el servidor debe verificar del token (es su lado del contrato): firma
contra `https://token.actions.githubusercontent.com/.well-known/jwks`,
`iss` igual a ese emisor, `aud` igual a la audiencia configurada,
`repository` igual al repositorio del vínculo y `ref` igual a su rama por
defecto.

## Pendiente fuera de este directorio

- `railspec-server` aún no tiene la identidad OIDC ni el manejador de
  `graph.index` (`docs/motor.md` § Pendiente). Hasta entonces el workflow de
  reindexado fallará con 401 o 404; mejor no definir `RAILSPEC_URL` todavía.
- `/healthz` no comprueba Mongo ni FalkorDB: una réplica sin base de datos
  sigue recibiendo tráfico. Una sonda de disponibilidad con ping a Mongo es
  del hilo dueño del servidor.
- El indexador local por CLI de `codebase-memory-mcp` pagina de a unas 60 a
  180 filas y cada llamada cuesta unos 4 s de arranque, así que un índice
  completo de un repositorio mediano tarda decenas de minutos (el job tiene
  60 de límite). Es del paquete `railspec-local`.
