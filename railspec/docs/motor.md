# Motor de protocolo (`railspec-server`)

El servidor ejecuta el protocolo SDD como un DAG de Microsoft Agent Framework
(Python) por unidad. El arnés solo llama tools; el servidor decide qué orden
toca, corre los gates y abre los checkpoints.

## Piezas

| Módulo | Qué hace |
| --- | --- |
| `estado/mongo.py` | `AlmacenMongo`: `StateStore` más lo propio del motor (órdenes, snapshots, entradas pendientes, turno por unidad, configuración). Toda consulta pasa por un filtro con organización y workspace. |
| `estado/checkpoints.py` | `CheckpointsMongo`: `CheckpointStorage` de MAF sobre Mongo; un workflow por unidad (`railspec:{org}:{ws}:{unidad}`), con orden por contador atómico. |
| `proveedores/` | `ProveedorModelo` con los SDK oficiales: Foundry (despliegues Claude por `/anthropic` y el resto por chat completions, clave o Entra ID) es el primario; Anthropic directo solo con `RAILSPEC_ANTHROPIC_HABILITADO` y solo para nivel `abierto`. Catálogo por API, selección por rol con política de zona de datos. Ver [proveedores.md](proveedores.md). |
| `contexto/` | Herramientas de contexto conectables por rol (PCE primero): implementan el proveedor de gobernanza del gate. Ver [proveedores.md](proveedores.md). |
| `motor/artefactos.py` | Capa determinista: plantillas, secciones obligatorias, `CA-NN`, grupos del plan, comando de validación y tareas trazables. Sin tokens. |
| `motor/gate.py` | Panel de críticos en paralelo (un lente por crítico), refutador para severidad alta y regla de convergencia. |
| `motor/dag.py` | Nodos del DAG: triaje, redacción, gate, decisión humana, avance, implementación y cierre. |
| `motor/motor.py` | Tools `unit.*` y `telemetry.query`; runner que reanuda el DAG desde el último checkpoint. |
| `api/` | Registro único de tools (R1) expuesto por MCP en `/mcp` (proxy local, con el alias `nombre_mcp`: `unit_start`) y por HTTP en `/v1/tools/{nombre}` (consola, nombre canónico `unit.start`). El registro acepta las dos formas. |

## Recorrido

```
triaje → redacción(spec) → gate → decisión → avance → redacción(plan) → gate → …
       → redacción(tasks) → gate → implementación(grupos) → validar → gate de código → cierre
```

- Cada orden se emite con `request_info` de MAF y el workflow queda en pausa
  en un checkpoint. `unit.report` y `unit.approve` guardan una entrada
  pendiente y la entregan al DAG con `run(responses=…)`.
- Un turno por unidad en Mongo hace a cada unidad de un solo escritor entre
  réplicas. Si la réplica cae tras registrar la entrada, el siguiente
  `unit.advance` la procesa.
- Modos: `interactivo` abre `aprobar-spec` y `aprobar-plan`; `semi-autonomo`
  abre un `paquete-aprobacion`; `supervisado` y `desatendido` solo paran en
  paradas y gates escalados.
- El gate escala sin gastar tokens si la gobernanza no respondió o respondió
  a medias (`sin-gobernanza`) o si el presupuesto se agotó. Un fallo del
  proveedor escala con `error-proveedor`.
- Los artefactos de la unidad (`.railspec/unidades/<unidad>/`) viven en su
  worktree: la orden `implementar` los admite en `alcance.permitidos` y el gate
  de código no los cuenta como archivos fuera del plan.
- El gate de código revisa primero archivos fuera del plan y la validación
  fallida. Con `railspec-graph` configurado, suma a los críticos el impacto
  aguas arriba de la superposición de la unidad (`impacto_superposicion`).
- El modo lo fija el humano en `unit.start` o con `unit.set_mode`, solo
  tras research o tras el checkpoint del spec (contratos 1.2); rige desde el
  siguiente gate.
- Sincronización (contrato 1.3): `sync.pull` pagina los eventos remoto→local;
  `sync.push` recibe la cola local→remoto del proxy, idempotente por id y sin
  huecos (`secuencia-con-hueco`). Esa dirección la numera solo el proxy: el
  servidor no emite `orden.reportada` ni `snapshot.subido` al recibir
  `unit.report`.
- Las aprobaciones web son opcionales y gana la primera resolución; la
  segunda recibe `checkpoint-ya-resuelto`.

## Variables de entorno

| Variable | Uso |
| --- | --- |
| `RAILSPEC_MONGO_URI`, `RAILSPEC_MONGO_DB` | Estado y checkpoints. Sin URI, el servidor usa Mongo simulado en memoria (solo desarrollo). |
| `RAILSPEC_FOUNDRY_ENDPOINT`, `RAILSPEC_FOUNDRY_API_KEY` | Recurso de Azure Foundry. Sin clave, Entra ID. El resto de variables de proveedores, catálogo y contexto está en [proveedores.md](proveedores.md). |
| `RAILSPEC_ANTHROPIC_HABILITADO`, `RAILSPEC_ANTHROPIC_API_KEY` | Anthropic directo (solo nivel `abierto`). |
| `RAILSPEC_PCE_URL`, `RAILSPEC_PCE_API_KEY` | Gobernanza por MCP. Sin ella, todo gate escala con `sin-gobernanza`. |
| `RAILSPEC_FALKORDB_URL` | Grafo central (`railspec-graph`, extra `grafo`). |
| `RAILSPEC_OIDC_AUDIENCIA` | Audiencia de los tokens OIDC de GitHub Actions (por defecto `railspec`, la misma que usa el workflow de reindexado). Vacía desactiva la identidad de servicio. |
| `RAILSPEC_OIDC_EMISOR` | Emisor y JWKS (por defecto `https://token.actions.githubusercontent.com`). |
| `RAILSPEC_OIDC_REPOSITORIOS` | Lista `owner/repo` separada por comas que puede presentar tokens OIDC. Vacía: cualquiera, siempre que coincida con el vínculo. |
| `RAILSPEC_TOKENS_DESARROLLO` | `token=login:github_id,…` para desarrollo sin GitHub App. |
| `RAILSPEC_HOST`, `RAILSPEC_PUERTO` | Escucha HTTP (por defecto `0.0.0.0:8080`). |

Arranque: `pip install -e "railspec/packages/railspec-server[motor]"` y
`railspec-server`.

## Reindexado del canónico (`graph.index`)

- Identidad: un token cuyo `iss` es el emisor de GitHub Actions se verifica
  como OIDC (firma contra el JWKS, `iss`, `aud`, caducidad y, si hay lista,
  `repository`) y da un actor de servicio con canal `ci`. Cualquier otro token
  sigue la identidad humana.
- La tool comprueba además que `repository` sea el repositorio de la URL del
  vínculo y que el workflow corriera en su rama por defecto (el `@ref` de
  `workflow_ref`).
- Los lotes se guardan en el grafo de preparación del commit y el canónico
  avanza al llegar el último (`IndexadorCanonico` de `railspec-graph`). Un
  delta cuya base no es el canónico vigente responde `base-commit-distinto`
  (409) y el cliente repite con índice completo.

## Sondas

- `GET /healthz` (disponibilidad): comprueba Mongo (`ping`) y FalkorDB con un
  tope de 2 s por sonda; 503 con el detalle si alguna falla.
- `GET /livez` (vida): solo que el proceso atiende. La sonda de vida del
  despliegue debe usar esta, para no reiniciar réplicas por una caída de la
  base.

## Pendiente

- Manejadores de `insumo.get` y `code.read` (`graph.query` y `graph.index` ya se enchufan con el grafo): el registro los
  acepta enchufados y no los anuncia mientras falten.
- Roles por equipo de GitHub: hoy solo se resuelven asignaciones de usuario.
- La forma de la respuesta de PCE no está verificada contra el servicio real.
