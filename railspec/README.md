# Railspec

Plugin que da rieles SDD a arneses existentes (Claude Code, OpenCode, Codex y
GitHub Copilot CLI): un motor remoto dicta órdenes de trabajo y verifica, un
proxy MCP local ejecuta en el clon del desarrollador, y una consola web
administra, mide y explora.

Vive dentro de `sdd-mcp`, que también aloja el kit SDD (`.spec/`, `installer/`).
Los dos pueden instalarse en el mismo repositorio sin pisarse, y las unidades del
kit se traen a Railspec a demanda: ver [docs/migracion-kit.md](docs/migracion-kit.md).

## Estructura

```
railspec/
  packages/
    railspec-contracts/   contratos v1: modelos Pydantic y esquemas
    railspec-server/      motor DAG, estado en Mongo, API MCP y HTTP, chat y consola (API)
    railspec-local/       proxy MCP local, CLI `railspec` y adaptadores de arnés
    railspec-graph/       grafo de código centralizado y RAG sobre FalkorDB
    railspec-console/     consola web (SPA React + Vite) que sirve railspec-server
  schemas/v1/             JSON Schema generados + manifiesto de tools
  examples/v1/            un ejemplo válido por mensaje
  deploy/                 imagen, manifiestos de Kubernetes y cliente de reindexado
  docs/contratos.md       qué fija cada contrato y cómo se versiona
  integracion/            entorno docker compose y pruebas extremo a extremo
  tests/                  deriva de esquemas y ejemplos, invariantes
```

`railspec-server` y `railspec-local` dependen solo de `railspec-contracts`;
entre ellos nunca se importan.

## Desarrollo

```
python -m pip install -e railspec/packages/railspec-contracts[test] \
  -e railspec/packages/railspec-server -e railspec/packages/railspec-local \
  -e railspec/packages/railspec-graph[falkordb,test]
python -m pytest railspec
```

Las pruebas extremo a extremo (Mongo, FalkorDB, servidor y proxy por stdio) se
saltan si Mongo y FalkorDB no responden: ver [integracion/README.md](integracion/README.md).

Documentación: [contratos](docs/contratos.md), [motor](docs/motor.md),
[proveedores](docs/proveedores.md), [grafo](docs/grafo.md), [proxy local](docs/proxy-local.md),
[chat](docs/chat.md), [consola web](docs/consola.md), [estado en Postgres](docs/estado-postgres.md), [despliegue en AKS](docs/despliegue.md) (bases, red, respaldos y clones: [despliegue-datos](docs/despliegue-datos.md)) y
[migración desde el kit SDD](docs/migracion-kit.md).
