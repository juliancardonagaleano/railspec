# Railspec

Plugin que da rieles SDD a arneses existentes (Claude Code y opencode en la
primera ola): un motor remoto dicta órdenes de trabajo y verifica, un proxy
MCP local ejecuta en el clon del desarrollador, y una consola web administra,
mide y explora.

Vive dentro de `sdd-mcp` mientras madura; el kit actual (`.spec/`,
`installer/`) queda como referencia y cantera de código, sin compatibilidad.

## Estructura

```
railspec/
  packages/
    railspec-contracts/   contratos v1 (fase 1): modelos Pydantic y esquemas
    railspec-server/      esqueleto del motor y del repositorio central (fases 2 y 4)
    railspec-local/       esqueleto del proxy local y adaptadores (fase 3)
  schemas/v1/             JSON Schema generados + manifiesto de tools
  examples/v1/            un ejemplo válido por mensaje
  docs/contratos.md       qué fija cada contrato y cómo se versiona
  tests/                  deriva de esquemas y ejemplos, invariantes
```

`railspec-server` y `railspec-local` dependen solo de `railspec-contracts`;
entre ellos nunca se importan.

## Desarrollo

```
python -m pip install -e railspec/packages/railspec-contracts[test] \
  -e railspec/packages/railspec-server -e railspec/packages/railspec-local
python -m pytest railspec
```

Ver [docs/contratos.md](docs/contratos.md).
