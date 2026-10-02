"""``ruff`` se instala con versión exacta en todos los workflows.

``ruff format --check`` es el job ``lint`` de ``railspec-ci.yml``, y el formato cambia entre versiones de
ruff (la 0.16 pasó a formatear los bloques de Python de los ``.md``): con ``pip install ruff`` sin fijar,
una versión nueva publicada entre dos corridas pone en rojo un PR que no tocó nada. Estas pruebas fallan
si un workflow instala ruff sin versión, con un rango (``>=``, ``~=``), con una versión incompleta o con
versiones distintas en workflows distintos. Para subir la versión se cambia el pin de ``railspec-ci.yml``
tras correr ``ruff check`` y ``ruff format --check`` con la nueva y arreglar lo que marque.
"""

from __future__ import annotations

import re
import textwrap

import pytest
import yaml
from _workflows import WORKFLOWS, requisitos

#: ``ruff`` como palabra entera (``ruff==0.16.10``, ``ruff>=0.16``); no ``ruff-action`` ni ``ruffian``.
PAQUETE = re.compile(r"^ruff(?![\w.-])", re.IGNORECASE)
#: Una versión exacta: ``ruff==X.Y.Z`` y nada más (sin extras, sin marcadores, sin ``.*``).
EXACTO = re.compile(r"^ruff==(?P<version>\d+\.\d+\.\d+)$", re.IGNORECASE)


def _requisitos(texto_yaml: str) -> list[str]:
    return requisitos(texto_yaml, PAQUETE)


def _sin_fijar(texto_yaml: str) -> list[str]:
    return [r for r in _requisitos(texto_yaml) if not EXACTO.match(r)]


def _versiones(texto_yaml: str) -> set[str]:
    return {m["version"] for r in _requisitos(texto_yaml) if (m := EXACTO.match(r))}


def _workflows() -> dict[str, str]:
    return {f.name: f.read_text("utf-8") for f in sorted(WORKFLOWS.glob("*.yml"))}


def test_todo_workflow_instala_ruff_con_version_exacta():
    sin_fijar = {nombre: malos for nombre, texto in _workflows().items() if (malos := _sin_fijar(texto))}
    assert not sin_fijar, (
        f"instalan ruff sin versión exacta: {sin_fijar}. Usar pip install ruff==X.Y.Z; el formato cambia "
        "entre versiones y una nueva pondría en rojo la CI sin que nadie la toque."
    )


def test_una_sola_version_de_ruff_en_todos_los_workflows():
    versiones = {nombre: v for nombre, texto in _workflows().items() if (v := _versiones(texto))}
    distintas = {x for v in versiones.values() for x in v}
    assert len(distintas) <= 1, f"versiones de ruff distintas entre workflows: {versiones}"


def test_el_job_lint_instala_ruff_y_corre_check_y_format():
    """Sin esto, un cambio de forma en el YAML dejaría las pruebas anteriores comprobando nada."""

    ci = yaml.safe_load((WORKFLOWS / "railspec-ci.yml").read_text("utf-8"))
    pasos = ci["jobs"]["lint"]["steps"]
    instalaciones = _requisitos(yaml.safe_dump({"jobs": {"lint": {"steps": pasos}}}))
    assert len(instalaciones) == 1, (
        f"el job lint de railspec-ci.yml debe instalar ruff una vez: {instalaciones}"
    )
    ordenes = [p["run"] for p in pasos if "run" in p]
    assert any(o.startswith("ruff check ") for o in ordenes), "el job lint ya no corre ruff check"
    assert any(o.startswith("ruff format ") and "--check" in o for o in ordenes), (
        "el job lint ya no corre ruff format --check"
    )


# --- el comprobador falla donde debe ----------------------------------------------------------------


def _run(comando: str) -> str:
    return "jobs:\n  j:\n    steps:\n      - run: |\n" + textwrap.indent(comando, " " * 10) + "\n"


def _matriz(instalar: str) -> str:
    return f"jobs:\n  j:\n    strategy:\n      matrix:\n        include:\n          - instalar: {instalar}\n"


FIJADOS = {
    "pip": _run("python -m pip install ruff==0.16.10"),
    "comillas": _run('python -m pip install "ruff==0.16.10"'),
    "con_otros_paquetes": _run("pip install -e a ruff==0.16.10 pytest"),
    "con_continuacion": _run("pip install -e a \\\n  ruff==0.16.10"),
    "uv": _run("uv pip install ruff==0.16.10"),
    "tras_otro_tramo": _run("cd a && pip install ruff==0.16.10 && ruff check ."),
    "mayusculas": _run("pip install Ruff==0.16.10"),
    "matriz": _matriz("pytest ruff==0.16.10"),
}
SIN_FIJAR = {
    "sin_version": _run("python -m pip install ruff"),
    "rango_mayor_o_igual": _run("pip install 'ruff>=0.16'"),
    "rango_compatible": _run("pip install 'ruff~=0.16.10'"),
    "rango_menor": _run("pip install 'ruff<0.17'"),
    "comodin": _run("pip install 'ruff==0.16.*'"),
    "version_incompleta": _run("pip install ruff==0.16"),
    "con_marcador": _run("pip install 'ruff==0.16.10; python_version>\"3.8\"'"),
    "con_continuacion": _run("pip install -e a \\\n  ruff"),
    "tras_otro_tramo": _run("cd a && pip install ruff && pytest"),
    "yaml_plegado": (
        "jobs:\n  j:\n    steps:\n      - run: >-\n"
        "          python -m pip install\n          -e a\n          pytest ruff\n"
    ),
    "matriz_sin_pin": _matriz("-e a pytest ruff"),
    "matriz_rango": _matriz("pytest ruff>=0.16.10"),
}
NO_INSTALAN = {
    "ejecuta_check": _run("ruff check --config railspec/pyproject.toml railspec"),
    "ejecuta_format": _run("pip install -e a\nruff format --check railspec"),
    "ejecuta_en_la_misma_linea": _run("pip install -e a && ruff check ."),
    "comentario_de_shell": _run("# pip install ruff\npip install -e a"),
    "comentario_yaml": "# pip install ruff\n" + _run("pip install -e a"),
    "nombre_del_paso": (
        "jobs:\n  j:\n    steps:\n      - name: Instalar ruff\n        run: pip install -e a\n"
    ),
    "otro_paquete": _run("pip install ruff-lsp==0.0.1 ruffian other"),
}


@pytest.mark.parametrize("texto", FIJADOS.values(), ids=FIJADOS)
def test_el_comprobador_acepta_lo_fijado(texto):
    assert _requisitos(texto), "debía detectar la instalación"
    assert _sin_fijar(texto) == []
    assert _versiones(texto) == {"0.16.10"}


@pytest.mark.parametrize("texto", SIN_FIJAR.values(), ids=SIN_FIJAR)
def test_el_comprobador_rechaza_lo_que_no_tiene_version_exacta(texto):
    assert _sin_fijar(texto), "debía marcar la instalación como sin fijar"


@pytest.mark.parametrize("texto", NO_INSTALAN.values(), ids=NO_INSTALAN)
def test_el_comprobador_no_confunde_ejecutar_comentar_o_rotular_con_instalar(texto):
    assert _requisitos(texto) == []


def test_el_comprobador_ve_versiones_distintas():
    a, b = _run("pip install ruff==0.16.10"), _run("pip install ruff==0.15.22")
    assert _versiones(a) != _versiones(b)
