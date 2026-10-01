# -*- mode: python ; coding: utf-8 -*-
"""Receta de PyInstaller del binario autocontenido ``railspec`` (onefile).

Se ejecuta desde ``construir.py`` (o ``pyinstaller railspec.spec``) con
railspec-contracts y railspec-local ya instalados como paquetes normales (no
editables) en el entorno que corre PyInstaller: de ahí salen el código, las
plantillas y los metadatos de distribución.

Lo que el análisis estático de PyInstaller no ve y hay que declarar:

- las plantillas ``railspec/local/adaptadores/plantillas/*`` (.md, .js), que se leen
  con ``importlib.resources``;
- el indexador, que se carga por entry point (grupo ``railspec.indexadores``)
  con ``importlib.metadata``: hacen falta los metadatos de railspec-local
  (``entry_points.txt``) y el módulo ``railspec.local.indexador_cbm``, al que
  nadie importa por nombre;
- los metadatos de las distribuciones que se consultan en tiempo de ejecución
  (versiones de pydantic y mcp). opentelemetry, pydantic, jsonschema y
  uvicorn ya traen hook en pyinstaller-hooks-contrib.
"""

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

datas = collect_data_files("railspec.local.adaptadores", includes=["plantillas/*"])
for distribucion in ("railspec-local", "railspec-contracts", "mcp", "mcp-types", "pydantic"):
    datas += copy_metadata(distribucion)

# Todo el código propio, también lo que solo se alcanza por entry point o por
# import perezoso dentro de funciones.
hiddenimports = collect_submodules("railspec.local") + collect_submodules("railspec.contracts")

a = Analysis(
    ["entrada.py"],
    pathex=[],
    binaries=[],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    # Extras de mcp que el proxy no usa (CLI de desarrollo con typer/rich) y
    # bibliotecas pesadas que algún hook podría arrastrar.
    excludes=["mcp.cli", "typer", "rich", "tkinter", "IPython", "pytest"],
    noarchive=False,
    # Sin -OO: las descripciones de las tools MCP salen de los docstrings.
    optimize=0,
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.datas,
    [],
    name="railspec",
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    runtime_tmpdir=None,
    console=True,
    disable_windowed_traceback=False,
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
)
