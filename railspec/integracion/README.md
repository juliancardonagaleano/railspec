# Integración extremo a extremo

Recorre una unidad completa con las tres piezas de verdad: el arnés simulado
lanza `railspec mcp` por stdio (como Claude Code), el proxy habla con
`railspec-server` por MCP Streamable HTTP y el servidor guarda en Mongo y
FalkorDB reales.

```
unit.start → orden redactar spec → unit.report → checkpoint (formulario) → plan → checkpoint
  → tasks → implementar (snapshot + delta del índice) → validar → cierre
  → commit empujado (sync.push) → unit.integrate → sync.pull
```

Todo en nivel `restringido`: las pruebas buscan una marca que solo existe en
el código y en la salida de las pruebas del repositorio de ejemplo, y
comprueban que no aparece en ninguna colección de Mongo ni en ningún grafo de
FalkorDB.

## Piezas

| Ruta | Qué es |
| --- | --- |
| `docker-compose.yml` | Mongo 7, FalkorDB y `railspec-server` (imagen de `Dockerfile.servidor`). |
| `railspec_e2e/servidor.py` | El servidor real (`ensamblar`) con dos dobles: proveedor de modelo guionado (los críticos responden sin hallazgos) y gobernanza fija. Siembra el vínculo del repositorio en `restringido` y el rol del usuario de desarrollo. No necesita credenciales de Foundry ni de PCE. |
| `railspec_e2e/oidc.py` | Emisor OIDC local que imita a GitHub Actions: JWKS y endpoint de tokens del runner (`ACTIONS_ID_TOKEN_REQUEST_URL`). |
| `railspec_e2e/arnes.py` | Arnés simulado: cliente MCP por stdio que escribe artefactos y código en el worktree, responde los checkpoints por *elicitation* y sigue `unit_advance` hasta `cerrada`. |
| `tests/` | El recorrido, una vez por módulo, y una prueba por cada parte que comprueba. |
| `tests/test_ingesta_commit.py` | Ingesta por commit del canónico: `deploy/ci/reindexar.py` (como lo lanza `railspec-reindexar.yml`) sube índice completo, delta y delta desfasado a `graph.index` con OIDC verificado contra el JWKS local. |

## Correr las pruebas

Requisitos: Docker, Python 3.11 y los paquetes instalados como dice
`railspec/README.md`. `codebase-memory-mcp` en el PATH activa la prueba del
delta del índice en el grafo y la de ingesta por commit; sin él, se saltan.
La ingesta por commit siempre levanta su propio servidor en un subproceso
(con el emisor OIDC local), también con `RAILSPEC_E2E_URL`.

Con Mongo y FalkorDB del compose y el servidor en un subproceso (base de Mongo
nueva por corrida, se borra al terminar):

```
docker compose -f railspec/integracion/docker-compose.yml up -d mongo falkordb
python -m pytest railspec/integracion
```

Contra el servidor en contenedor:

```
docker compose -f railspec/integracion/docker-compose.yml up -d --build --wait
RAILSPEC_E2E_URL=http://127.0.0.1:8080 python -m pytest railspec/integracion
```

Tras un proxy corporativo que reemplaza el certificado TLS, pip necesita su CA
al construir la imagen: `RAILSPEC_E2E_CA=/ruta/ca.pem docker compose ... build`.

| Variable | Por defecto |
| --- | --- |
| `RAILSPEC_E2E_MONGO_URI` | `mongodb://127.0.0.1:27017` |
| `RAILSPEC_E2E_FALKORDB_URL` | `redis://127.0.0.1:6379` |
| `RAILSPEC_E2E_URL` | sin definir: el servidor corre en un subproceso |
| `RAILSPEC_E2E_MONGO_DB` | `railspec_e2e` (solo con `RAILSPEC_E2E_URL`) |
| `RAILSPEC_E2E_TOKEN` | `token-e2e` |

Si Mongo o FalkorDB no responden, las pruebas se saltan con el comando para
levantarlos.
