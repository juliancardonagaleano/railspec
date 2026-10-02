"""Versión fijada de codebase-memory-mcp: se comprueba al arrancar el indexador.

Usa binarios falsos (scripts que imprimen una versión), así corre sin el binario real.
"""

from __future__ import annotations

import logging
import stat
import subprocess
import sys
from pathlib import Path

import pytest
from railspec.local import indexador_cbm
from railspec.local.indexador_cbm import (
    BINARIO,
    VERSION_FIJA,
    DiagnosticoBinario,
    IndexadorCodebaseMemory,
    crear,
    diagnosticar,
)


def binario_falso(carpeta: Path, cuerpo: str) -> Path:
    carpeta.mkdir(parents=True, exist_ok=True)
    ruta = carpeta / BINARIO
    ruta.write_text(f"#!/bin/sh\n{cuerpo}\n", encoding="utf-8")
    ruta.chmod(ruta.stat().st_mode | stat.S_IXUSR)
    return ruta


def en_path(monkeypatch, carpeta: Path) -> None:
    monkeypatch.setenv("PATH", str(carpeta))


def test_la_version_fijada_tiene_forma_de_version():
    assert VERSION_FIJA.count(".") == 2 and all(parte.isdigit() for parte in VERSION_FIJA.split("."))


def test_la_misma_version_es_compatible(tmp_path):
    ruta = binario_falso(tmp_path, f'echo "{BINARIO} {VERSION_FIJA}"')

    diagnostico = diagnosticar(str(ruta))

    assert diagnostico == DiagnosticoBinario(binario=str(ruta), version=VERSION_FIJA)
    assert diagnostico.compatible and diagnostico.problema is None


def test_otra_version_dice_cual_hay_y_que_instalar(tmp_path):
    ruta = binario_falso(tmp_path, f'echo "{BINARIO} 9.9.9"')

    diagnostico = diagnosticar(str(ruta))

    assert diagnostico.version == "9.9.9" and not diagnostico.compatible
    assert diagnostico.problema is not None
    assert "9.9.9" in diagnostico.problema and VERSION_FIJA in diagnostico.problema
    assert f"pip install {BINARIO}=={VERSION_FIJA}" in diagnostico.problema


@pytest.mark.parametrize("cuerpo", ['echo "sin version"', "exit 3"])
def test_una_version_ilegible_o_un_binario_que_falla_no_rompen_el_diagnostico(tmp_path, cuerpo):
    ruta = binario_falso(tmp_path, cuerpo)

    diagnostico = diagnosticar(str(ruta))

    assert diagnostico.version is None and not diagnostico.compatible
    assert diagnostico.problema is not None and "no se pudo leer la versión" in diagnostico.problema


def test_sin_binario_en_el_path_no_hay_diagnostico_de_version(tmp_path, monkeypatch):
    en_path(monkeypatch, tmp_path / "vacio")

    diagnostico = diagnosticar()

    assert diagnostico == DiagnosticoBinario(binario=None, version=None)
    assert diagnostico.problema == f"{BINARIO} no está instalado."


def test_crear_sin_binario_devuelve_none_sin_avisar(tmp_path, monkeypatch, caplog):
    en_path(monkeypatch, tmp_path / "vacio")
    with caplog.at_level(logging.WARNING):
        assert crear() is None
    assert caplog.records == []  # el indexador es opcional: no estar instalado no es un problema


def test_crear_con_otra_version_apaga_el_indexador_y_avisa(tmp_path, monkeypatch, caplog):
    binario_falso(tmp_path / "bin", f'echo "{BINARIO} 9.9.9"')
    en_path(monkeypatch, tmp_path / "bin")

    with caplog.at_level(logging.WARNING, logger=indexador_cbm.__name__):
        assert crear() is None

    (registro,) = caplog.records
    assert "9.9.9" in registro.getMessage() and f"=={VERSION_FIJA}" in registro.getMessage()


def test_crear_con_la_version_fijada_entrega_el_indexador_sin_volver_a_preguntar(tmp_path, monkeypatch):
    llamadas = tmp_path / "llamadas"
    binario_falso(tmp_path / "bin", f'echo x >> "{llamadas}"\necho "{BINARIO} {VERSION_FIJA}"')
    en_path(monkeypatch, tmp_path / "bin")

    indexador = crear()

    assert isinstance(indexador, IndexadorCodebaseMemory)
    assert indexador.version() == VERSION_FIJA and indexador.version() == VERSION_FIJA
    assert llamadas.read_text(encoding="utf-8").count("x") == 1  # la versión leída al arrancar se reutiliza


def test_el_indexador_construido_a_mano_sigue_preguntando_su_version(tmp_path):
    ruta = binario_falso(tmp_path, f'echo "{BINARIO} 1.2.3-rc1"')

    assert IndexadorCodebaseMemory(str(ruta)).version() == "1.2.3-rc1"


# --- python -m railspec.local.indexador_cbm ----------------------------------------------------------


def test_main_imprime_la_version_fijada(capsys):
    assert indexador_cbm.main([]) == 0
    assert capsys.readouterr().out.strip() == VERSION_FIJA


def test_main_verificar_sale_con_0_o_con_1_segun_el_binario(tmp_path, monkeypatch, capsys):
    binario_falso(tmp_path / "bien", f'echo "{BINARIO} {VERSION_FIJA}"')
    binario_falso(tmp_path / "mal", f'echo "{BINARIO} 9.9.9"')

    en_path(monkeypatch, tmp_path / "bien")
    assert indexador_cbm.main(["--verificar"]) == 0
    assert capsys.readouterr().out.strip() == VERSION_FIJA

    en_path(monkeypatch, tmp_path / "mal")
    assert indexador_cbm.main(["--verificar"]) == 1
    captura = capsys.readouterr()
    assert captura.out == "" and "9.9.9" in captura.err


def test_main_rechaza_argumentos_desconocidos(capsys):
    assert indexador_cbm.main(["--otra-cosa"]) == 2
    assert "uso:" in capsys.readouterr().err


def test_se_puede_pedir_la_version_por_python_m():
    # Los workflows hacen `pip install codebase-memory-mcp==$(python -m railspec.local.indexador_cbm)`.
    salida = subprocess.run(
        [sys.executable, "-m", "railspec.local.indexador_cbm"], capture_output=True, text=True, check=True
    )
    assert salida.stdout.strip() == VERSION_FIJA
