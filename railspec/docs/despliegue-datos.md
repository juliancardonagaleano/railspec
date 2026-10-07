# Bases de datos, red, respaldos y clones en AKS

Complemento de [despliegue.md](despliegue.md). Todo lo de aquí es **opcional y
apagado por defecto**: sin banderas, `renderizar.py` produce exactamente los
mismos manifiestos de antes (se comprobó con una comparación byte a byte con el
de `master`). Cada pieza se enciende con una variable del renderizador.

| Bandera | Qué añade | Archivos de `deploy/k8s/` |
| --- | --- | --- |
| `RAILSPEC_MONGO_INTERNO` | Mongo 7 en el clúster: StatefulSet de una réplica con volumen, Service y usuario de la aplicación | `70-mongo.yaml` |
| `RAILSPEC_FALKORDB_INTERNO` | FalkorDB en el clúster: StatefulSet de una réplica con volumen, contraseña y AOF, y Service | `71-falkordb.yaml` |
| `RAILSPEC_RED_POLITICAS` | NetworkPolicy del servidor (solo desde el ingress) y de cada base interna (solo desde el servidor y el respaldo) | `80-red-servidor.yaml`, `81-red-mongo.yaml`, `82-red-falkordb.yaml` |
| `RAILSPEC_RESPALDO` | Volumen de respaldos y un CronJob por base interna | `90-respaldo-volumen.yaml`, `91-respaldo-mongo.yaml`, `92-respaldo-falkordb.yaml` |
| `RAILSPEC_CLONES_CREAR_PVC` | El PVC de clones del chat (`RAILSPEC_CHAT_CLONES_PVC`) | `95-clones-volumen.yaml` |
| `RAILSPEC_CLONES_ACTUALIZAR` | CronJob que clona y actualiza los repositorios del chat | `96-clones-actualizar.yaml` |

Con un Mongo o un FalkorDB externos (Atlas, un servicio gestionado) se dejan
las banderas en `false` y `RAILSPEC_MONGO_URI` / `RAILSPEC_FALKORDB_URL` del Secret
del servidor apuntan fuera, como hasta ahora.

## Variables

El renderizador valida cada una antes de aplicar nada al clúster
(`python3 railspec/deploy/renderizar.py --variables` las imprime todas).

| Variable | Defecto | Uso |
| --- | --- | --- |
| `RAILSPEC_DATOS_SECRETO` | `railspec-datos` | Secret con las credenciales de las bases internas (claves abajo). Se crea aparte; los manifiestos solo lo nombran. |
| `RAILSPEC_DATOS_CLASE` | `managed-csi` | StorageClass de Mongo y FalkorDB (disco, `ReadWriteOnce`). `managed-csi` es la de disco de AKS. |
| `RAILSPEC_ARCHIVOS_CLASE` | `azurefile-csi` | StorageClass con `ReadWriteMany` de los respaldos y de los clones. |
| `RAILSPEC_MONGO_INTERNO` | `false` | `true` despliega Mongo en el clúster. |
| `RAILSPEC_MONGO_IMAGEN` | `mongo:7.0.43@sha256:9854f7139445…` | Imagen de Mongo, por versión y digest. |
| `RAILSPEC_MONGO_TAMANO` | `20Gi` | Volumen de Mongo. |
| `RAILSPEC_MONGO_MEMORIA` | `2Gi` | Memoria solicitada y límite. La caché de WiredTiger sale de este límite (512 MB con `2Gi`). |
| `RAILSPEC_FALKORDB_INTERNO` | `false` | `true` despliega FalkorDB en el clúster. |
| `RAILSPEC_FALKORDB_IMAGEN` | `falkordb/falkordb:6.0.1@sha256:e2765e207e5b…` | Imagen de FalkorDB, por versión y digest. |
| `RAILSPEC_FALKORDB_TAMANO` | `10Gi` | Volumen de FalkorDB. |
| `RAILSPEC_FALKORDB_MEMORIA` | `2Gi` | Memoria solicitada y límite. Todo el grafo vive en memoria: el tamaño del volumen no la sustituye. |
| `RAILSPEC_RED_POLITICAS` | `false` | `true` añade las NetworkPolicy. |
| `RAILSPEC_RED_INGRESS_NAMESPACE` | `app-routing-system` | Namespace del controlador de ingress: el del add-on de enrutamiento de AKS; `ingress-nginx` con ese controlador. |
| `RAILSPEC_RESPALDO` | `false` | `true` añade volumen y CronJobs de respaldo. Exige `RAILSPEC_MONGO_INTERNO` o `RAILSPEC_FALKORDB_INTERNO`. |
| `RAILSPEC_RESPALDO_CRON` | `17 3 * * *` | Horario de los respaldos, en UTC. |
| `RAILSPEC_RESPALDO_RETENCION_DIAS` | `7` | Días que se conserva cada respaldo. |
| `RAILSPEC_RESPALDO_TAMANO` | `20Gi` | Volumen de respaldos. |
| `RAILSPEC_CLONES_CREAR_PVC` | `false` | `true` crea el PVC de clones, con el nombre de `RAILSPEC_CHAT_CLONES_PVC`. |
| `RAILSPEC_CLONES_TAMANO` | `10Gi` | Tamaño de ese PVC. |
| `RAILSPEC_CLONES_ACTUALIZAR` | `false` | `true` añade el CronJob de clones. Exige `RAILSPEC_CHAT_CLONES_PVC` y `RAILSPEC_CLONES_REPOSITORIOS`. |
| `RAILSPEC_CLONES_REPOSITORIOS` | vacío | `owner/repo` (coma o espacio) que mantiene el CronJob: los de los vínculos del chat. |
| `RAILSPEC_CLONES_CRON` | `*/15 * * * *` | Horario del CronJob de clones. |
| `RAILSPEC_CLONES_SECRETO` | `railspec-clones` | Secret con la clave `GITHUB_TOKEN`, de solo lectura de contenido. |

## Secrets

Los manifiestos no traen ninguna credencial: la referencian. Hay que crearlos
antes de aplicar. Las contraseñas deben ser alfanuméricas (`openssl rand -hex 24`):
FalkorDB las recibe por una cadena de argumentos separada por espacios y el
servidor las lleva sin codificar en la URL.

| Secret | Clave | Para qué |
| --- | --- | --- |
| `railspec-datos` | `MONGO_INITDB_ROOT_USERNAME`, `MONGO_INITDB_ROOT_PASSWORD` | Usuario raíz de Mongo. Solo lo usan la inicialización y el respaldo; el servidor no. |
| `railspec-datos` | `RAILSPEC_MONGO_CLAVE` | Contraseña del usuario `railspec`, con `readWrite` solo sobre `RAILSPEC_MONGO_DB`. |
| `railspec-datos` | `FALKORDB_PASSWORD` | Contraseña de FalkorDB. |
| `railspec-clones` | `GITHUB_TOKEN` | Token de solo lectura de los repositorios de los clones (un token de acceso personal de grano fino, o el de instalación de una GitHub App, que caduca en una hora). |

Las dos claves del servidor tienen que coincidir con las de arriba:

```
MONGO_RAIZ=$(openssl rand -hex 24); MONGO_APP=$(openssl rand -hex 24); FALKOR=$(openssl rand -hex 24)
kubectl -n railspec create secret generic railspec-datos \
  --from-literal=MONGO_INITDB_ROOT_USERNAME=admin \
  --from-literal=MONGO_INITDB_ROOT_PASSWORD="$MONGO_RAIZ" \
  --from-literal=RAILSPEC_MONGO_CLAVE="$MONGO_APP" \
  --from-literal=FALKORDB_PASSWORD="$FALKOR"
# En el Secret del servidor (RAILSPEC_SECRETO), en vez de las URL de un Mongo externo:
#   RAILSPEC_MONGO_URI=mongodb://railspec:$MONGO_APP@railspec-mongo.railspec.svc.cluster.local:27017/railspec?authSource=railspec
#   RAILSPEC_FALKORDB_URL=redis://:$FALKOR@railspec-falkordb.railspec.svc.cluster.local:6379
```

La base de la URL de Mongo y `authSource` son `RAILSPEC_MONGO_DB` (`railspec` por
defecto). **El usuario de la aplicación se crea solo al inicializar un volumen
vacío.** Cambiar `RAILSPEC_MONGO_CLAVE` después no cambia la contraseña de Mongo:
hay que hacerlo con `db.changeUserPassword` desde el usuario raíz y actualizar
la URL del servidor.

## Desplegar

```
export RAILSPEC_MONGO_INTERNO=true RAILSPEC_FALKORDB_INTERNO=true RAILSPEC_RED_POLITICAS=true RAILSPEC_RESPALDO=true
python3 railspec/deploy/renderizar.py > railspec.yaml     # y el resto de variables de despliegue.md
kubectl apply --dry-run=server -f railspec.yaml
kubectl apply -f railspec.yaml
kubectl -n railspec rollout status statefulset/railspec-mongo
kubectl -n railspec rollout status statefulset/railspec-falkordb
```

El servidor puede arrancar antes que las bases: su sonda de disponibilidad
(`/healthz`) lo mantiene fuera del Service hasta que las dos respondan.
El renderizador avisa por stderr si hay bases internas sin `RAILSPEC_RESPALDO`.

## Lo que corre

- **Una réplica por base, con volumen persistente** (`volumeClaimTemplates`).
  No hay alta disponibilidad: si el nodo cae o se drena, la base queda sin
  servicio hasta que el pod se reprograma y el disco se vuelve a adjuntar
  (puede tardar un par de minutos con Azure Disk, que además es de una sola zona). Mongo va
  sin conjunto de réplicas; el servidor no abre transacciones (no hay
  `start_transaction` ni `with_transaction` en su código). Pasar
  a un conjunto de réplicas, a Atlas o a otra base es cambiar `RAILSPEC_MONGO_URI`.
- **Sin privilegios**: uid 999 (Mongo) y 10002 (FalkorDB), raíz de solo
  lectura, sin capacidades, `RuntimeDefault`. La imagen de FalkorDB corre como
  root por defecto; aquí no, y su navegador web (puerto 3000) va apagado.
- **Autenticación**: Mongo con `--auth` y un usuario de aplicación mínimo;
  FalkorDB con `--requirepass`. Persistencia de FalkorDB por AOF (`everysec`).
- **Sin TLS dentro del clúster.** Lo único que limita quién llega es la
  NetworkPolicy (y las credenciales). Si hace falta TLS en tránsito, es una
  malla de servicio o un Mongo gestionado.
- **Borrado**: por defecto, borrar el StatefulSet no borra sus PVC, pero la
  StorageClass `managed-csi` los libera con `reclaimPolicy: Delete`: si se borra
  el PVC, se borra el disco. Para datos que importan, una clase propia con
  `Retain` (ejemplo sin aplicar en un clúster):

  ```yaml
  apiVersion: storage.k8s.io/v1
  kind: StorageClass
  metadata: {name: railspec-datos}
  provisioner: disk.csi.azure.com
  parameters: {skuname: Premium_LRS}
  reclaimPolicy: Retain
  allowVolumeExpansion: true
  volumeBindingMode: WaitForFirstConsumer
  ```

  y `RAILSPEC_DATOS_CLASE=railspec-datos`.

## Versiones fijas

Mongo `7.0.43` y FalkorDB `6.0.1`, cada una con el digest del índice
multiarquitectura (amd64 y arm64). Son las mismas cadenas en tres sitios:
`IMAGEN_MONGO` e `IMAGEN_FALKORDB` de `renderizar.py`, los `services` de
`railspec-ci.yml` y `integracion/docker-compose.yml`. Una prueba
(`test_las_versiones_de_las_bases_son_las_mismas…`) rompe si se separan o si
vuelve un `latest`.

Antes se usaba `falkordb/falkordb:latest`, que el 2026-10-01 pasó a ser la 6.0.1.
Con las dos, la 6.0.1 y la `v4.22.0` (la última de la línea 4), pasaron las
mismas suites: grafo (120), servidor (384) y el recorrido extremo a extremo
(11). Se fijó la 6.0.1 por ser la que `latest` resuelve hoy; si prefieres la
línea 4 por ser más antigua, basta cambiar la cadena en esos tres sitios.

Para subir de versión: cambiar las tres cadenas (el digest se lee con
`docker buildx imagetools inspect <imagen:versión>`), correr las suites contra
la nueva y desplegar. Para que el clúster no dependa de Docker Hub,
`az acr import --name <acr> --source docker.io/library/mongo:7.0.43 --image mongo:7.0.43`
y apuntar `RAILSPEC_MONGO_IMAGEN` al ACR, comprobando que el digest del ACR
coincide con el fijado.

## NetworkPolicy

Hace falta un motor de políticas en el clúster (Azure Network Policy Manager,
Calico o Cilium, elegido al crearlo en AKS): sin él Kubernetes acepta las
políticas y no las aplica, sin avisar.

- **Servidor**: solo entra tráfico al puerto 8080 desde el namespace del
  controlador de ingress (`RAILSPEC_RED_INGRESS_NAMESPACE`). Las salidas quedan
  abiertas a propósito: dependen del despliegue (Foundry, la gobernanza, GitHub
  y el JWKS de OIDC, Entra ID, un Mongo externo) y cerrarlas sin conocerlas
  rompería el servicio.
- **Mongo y FalkorDB**: solo entran, a su puerto, los pods del servidor y los de
  respaldo del mismo namespace, y no salen a ninguna parte salvo al DNS.

Comprobación: un pod en otro namespace no debe poder conectar:
`kubectl run -n default prueba --rm -it --image=busybox --restart=Never -- nc -zv -w 3 railspec-mongo.railspec.svc 27017`
(tiene que agotar el tiempo), y desde el servidor sí.

## Respaldos

`RAILSPEC_RESPALDO=true` crea un PVC `railspec-respaldo` (`ReadWriteMany`) y un
CronJob por base interna (a las 03:17 UTC por defecto):

- **Mongo**: `mongodump --gzip` de la base de la aplicación a
  `/respaldo/mongo/railspec-<fecha>.archive.gz`, con el usuario raíz.
- **FalkorDB**: instantánea RDB por replicación (`redis-cli --rdb`, incluye los
  grafos) a `/respaldo/falkordb/railspec-<fecha>.rdb`. El grafo se puede
  reconstruir reindexando, pero tarda; la copia ahorra esa espera.

Cada trabajo escribe a un `.tmp`, comprueba el archivo y solo entonces lo
renombra, y borra lo de más de `RAILSPEC_RESPALDO_RETENCION_DIAS`. Para probarlo
sin esperar: `kubectl -n railspec create job --from=cronjob/railspec-respaldo-mongo prueba-1`
y mirar `kubectl -n railspec logs job/prueba-1`.

**Límite importante**: los respaldos viven en el mismo clúster y cuenta. Protegen
de un borrado o una corrupción de datos, no de perder el clúster. Para eso,
copiarlos fuera (Azure Backup del recurso compartido de Azure Files, o
instantáneas del volumen) es un paso que no hace este despliegue.

### Restaurar

Un pod auxiliar con la imagen de la base y el volumen de respaldos montado en
solo lectura (`sleep infinity`, con las credenciales del Secret como variables)
basta para ambos. Los comandos se probaron en contenedores con las mismas
imágenes, argumentos y scripts renderizados; el pod auxiliar no se aplicó en un
clúster.

**Mongo.** Con el servidor en 0 réplicas (`kubectl -n railspec scale
deploy/railspec-server --replicas=0`):

```
mongorestore --host railspec-mongo -u "$MONGO_INITDB_ROOT_USERNAME" -p "$MONGO_INITDB_ROOT_PASSWORD" \
  --authenticationDatabase admin --archive=/respaldo/mongo/<archivo> --gzip --drop --nsInclude 'railspec.*'
```

Para comprobar un respaldo sin tocar los datos, restaurarlo a otra base
(`--nsFrom 'railspec.*' --nsTo 'comprobacion.*'` en vez de `--drop --nsInclude`)
y borrarla después: es lo que se probó de extremo a extremo (3 documentos de
vuelta).

**FalkorDB.** Ojo: con AOF activado, un `dump.rdb` copiado al volumen **se ignora
sin avisar** y la base arranca vacía (se comprobó). Hay que poner la copia como
base del AOF, con el StatefulSet en 0 y el volumen `data-railspec-falkordb-0`
montado en el pod auxiliar (con `fsGroup: 10002`):

```
d=/var/lib/falkordb/data/appendonlydir
rm -rf "$d" /var/lib/falkordb/data/dump.rdb && mkdir -p "$d"
cp /respaldo/falkordb/<archivo> "$d/appendonly.aof.1.base.rdb"
: > "$d/appendonly.aof.1.incr.aof"
printf 'file appendonly.aof.1.base.rdb seq 1 type b\nfile appendonly.aof.1.incr.aof seq 1 type i\n' > "$d/appendonly.aof.manifest"
```

Luego `kubectl -n railspec scale statefulset/railspec-falkordb --replicas=1`. Con
esto los grafos volvieron intactos y siguieron ahí tras reiniciar.

## Clones del chat

El chat lee el código de los repositorios vinculados de unos clones `--bare` en
`<RAILSPEC_CHAT_CLONES>/<owner>/<repo>` ([chat.md](chat.md)). Para que el
despliegue los mantenga:

```
export RAILSPEC_CHAT_CLONES_PVC=railspec-clones
export RAILSPEC_CLONES_CREAR_PVC=true RAILSPEC_CLONES_ACTUALIZAR=true
export RAILSPEC_CLONES_REPOSITORIOS="acme/certificados-api acme/web"
kubectl -n railspec create secret generic railspec-clones --from-literal=GITHUB_TOKEN='github_pat_…'
```

El CronJob usa la imagen del servidor (trae `git`, corre como 10001), clona lo que
falta, actualiza el resto con `fetch` a `refs/heads/*`, poda ramas borradas y un
repositorio que falla no detiene a los demás (el trabajo termina en error al
final). El token va por la configuración de git, no por la URL. El servidor monta
el mismo PVC de solo lectura. La lista de repositorios debe coincidir con los
vínculos del chat: el renderizador solo acepta `owner/repo` sin `..`.
Mientras un repositorio no esté clonado, el chat contesta sin su código.
Para otro volumen, `RAILSPEC_CLONES_CREAR_PVC=false` y se aporta el PVC con ese nombre.

## Zona de datos del chat

Desde el 2026-10-06 el chat ya no exige zona de datos: `RAILSPEC_CHAT_ZONA_DATOS`
se eliminó (el renderizador la ignora) y ningún nivel de repositorio impide usar
un despliegue Global, Anthropic directo o un proveedor compatible
([proveedores.md](proveedores.md#política-por-nivel)). Lo que queda es
`RAILSPEC_FOUNDRY_ZONA_DATOS`, que solo etiqueta la región que se audita en los
despliegues `DataZone*` (`zona-us`, `zona-eu`).

## CI

- `railspec-ci.yml` tiene un job nuevo, `integracion`, que corre
  `railspec/integracion` entero contra Mongo y FalkorDB reales (las imágenes de
  arriba, con sondas de salud) y **falla** si las pruebas se saltan por no
  encontrar las bases, para que un servicio caído no dé un verde sin probar nada.
- El job `pruebas` ya no usa `falkordb/falkordb:latest`.
- El job `manifiestos` valida con kubeconform 1.31 el render por defecto y otro
  con todas las banderas activadas.

## Qué se probó y qué no

Probado en esta sesión, con Docker y las imágenes fijadas: el render por defecto es
idéntico al de `master`; el render con todo activado valida con kubeconform 1.31
(20 recursos); Mongo y FalkorDB arrancan con los argumentos, el uid, la raíz de
solo lectura y las capacidades de los manifiestos, con autenticación y
persistencia tras reinicio; el usuario de la aplicación no puede escribir fuera
de su base; el recorrido extremo a extremo (11 pruebas) pasa con los paquetes que instala
el job `integracion` y las dos imágenes fijadas, también (10 más 1 salto
esperado) con el servidor conectado como el usuario de la aplicación, que solo
tiene `readWrite` sobre su base; la guarda de saltos del job falla cuando no hay
bases; los scripts renderizados de los dos
respaldos, su retención, la restauración de ambos y el script de clones
(clonar, actualizar, podar, aislar un fallo) funcionan; el servidor conecta a
FalkorDB con contraseña. Las suites de grafo, servidor y despliegue pasan.

No probado: nada en un clúster (ni AKS ni uno local): los permisos de Azure Disk
y Azure Files, `fsGroup`, el reprogramado de pods, que las NetworkPolicy se
apliquen, el pod auxiliar de restauración y la descarga de las imágenes por
digest desde AKS. GitHub Actions sigue sin ejecutar (`startup_failure` en todos
los runs), así que el job `integracion` y los cambios de `railspec-ci.yml` no
corrieron en Actions; se reprodujeron sus pasos a mano.
