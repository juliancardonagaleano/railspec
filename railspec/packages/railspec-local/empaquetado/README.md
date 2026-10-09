# Empaquetado del binario `railspec`

Receta para congelar `railspec-local` en un ejecutable único (PyInstaller,
onefile) por plataforma. La CI la corre en `.github/workflows/railspec-binario.yml`;
cómo se descarga, verifica y publica: sección "Binario autocontenido" de `railspec/docs/proxy-local.md`.

| Archivo | Qué es |
| --- | --- |
| `railspec.spec` | Receta de PyInstaller: plantillas, metadatos (entry point del indexador) e imports ocultos. |
| `entrada.py` | Arranque del binario (`railspec.local.cli:main`). |
| `construir.py` | Instala railspec-contracts y railspec-local desde una copia temporal y corre PyInstaller. |
| `humo.py` | Humo del binario ya construido; solo biblioteca estándar. |
| `requirements.in` / `requirements.lock` | Dependencias de terceros, fijadas con hashes. |
| `requirements-windows.lock` | Lo que solo existe en Windows (`pefile`, `pywin32`…), con hashes; se mantiene a mano. |
| `bloquear.sh` | Regenera el lock (Docker, misma imagen base que railspec-server). |

## Construir en local

Desde la raíz del repositorio, con Python 3.11 (el de la CI) en Linux o macOS:

```
python3.11 -m venv /tmp/railspec-binario
/tmp/railspec-binario/bin/pip install --require-hashes --no-deps \
  -r railspec/packages/railspec-local/empaquetado/requirements.lock
/tmp/railspec-binario/bin/python railspec/packages/railspec-local/empaquetado/construir.py
python3 railspec/packages/railspec-local/empaquetado/humo.py \
  railspec/packages/railspec-local/empaquetado/dist/railspec
```

En Windows (PowerShell, Python 3.11) se instala además `requirements-windows.lock` y el binario sale como
`railspec.exe`:

```
py -3.11 -m venv $env:TEMP\railspec-binario
& $env:TEMP\railspec-binario\Scripts\pip install --require-hashes --no-deps `
  -r railspec\packages\railspec-local\empaquetado\requirements.lock `
  -r railspec\packages\railspec-local\empaquetado\requirements-windows.lock
```

El binario queda en `empaquetado/dist/railspec` (~22 MB) y el directorio de
trabajo de PyInstaller en `empaquetado/build/`; los dos están en
`.gitignore`. `--salida` y `--trabajo` los llevan a otro sitio. El binario
solo sirve para la plataforma y arquitectura donde se construye, y en Linux
exige una glibc igual o más nueva que la de la máquina de construcción.

## Al cambiar el paquete

- Módulo nuevo en `railspec.local` o `railspec.contracts`: nada que hacer,
  la receta los recoge todos (`collect_submodules`).
- Archivo de datos nuevo leído con `importlib.resources`: si no está bajo
  `adaptadores/plantillas/`, añádelo a `datas` en `railspec.spec`.
- Dependencia nueva que se consulte por `importlib.metadata` (versión o
  entry points): `copy_metadata` en `railspec.spec`.
- Dependencias de terceros cambiadas en un `pyproject`: actualiza
  `requirements.in` y regenera el lock con `bloquear.sh`.

`humo.py` falla si falta una plantilla, el entry point `railspec.indexadores`
o el módulo del indexador, o si una tool MCP pierde su descripción.
