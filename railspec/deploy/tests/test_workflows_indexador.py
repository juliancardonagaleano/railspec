"""El indexador ``codebase-memory-mcp`` se instala fijado en todos los workflows.

Los ids de símbolo y las aristas de un índice salen del parser del binario: un delta que calcula un
proxy con otra versión no casa con el índice canónico que construye la CI. Por eso la versión vive en
un solo sitio (``railspec.local.indexador_cbm.VERSION_FIJA``) y los workflows la derivan con
``pip install "codebase-memory-mcp==$(python -m railspec.local.indexador_cbm)"``. Estas pruebas
fallan si un workflow lo instala sin versión, con otra distinta de ``VERSION_FIJA`` o con un
rango (``>=``, ``~=``), y si la CI deja de correr las pruebas del instalador del kit.
"""

from __future__ import annotations

import re
import shlex
import textwrap
from collections.abc import Iterator
from pathlib import Path

import pytest
import yaml
from railspec.local import indexador_cbm

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
PAQUETE = re.compile(r"codebase[-_.]memory[-_.]mcp(?![\w.-])", re.IGNORECASE)
#: La forma con la que los workflows piden la versión fijada: correcta por construcción.
DERIVADA = "$(python -m railspec.local.indexador_cbm)"
#: Sustituye a DERIVADA antes de partir en palabras, para que valga con o sin comillas.
MARCA = "@derivada@"
SEPARADORES = {"&&", "||", ";", "|"}
#: Claves que solo rotulan (nombre de un paso, descripción de una entrada): mencionan el paquete sin
#: instalarlo.
ROTULOS = {"name", "description"}


def _textos(nodo, clave: str | None = None) -> Iterator[tuple[str | None, str]]:
    """Cada cadena del YAML con la clave que la contiene, salvo los rótulos."""
    if isinstance(nodo, dict):
        for k, v in nodo.items():
            if k not in ROTULOS:
                yield from _textos(v, k)
    elif isinstance(nodo, list):
        for v in nodo:
            yield from _textos(v, clave)
    elif isinstance(nodo, str):
        yield clave, nodo


def _ordenes(texto: str) -> Iterator[list[str]]:
    """Las órdenes de un guion de shell, ya partidas en palabras: sin comentarios, con las
    continuaciones (``\\``) unidas y cada tramo de un ``&&``, ``||``, ``;`` o ``|`` aparte."""
    for linea in texto.replace(DERIVADA, MARCA).replace("\\\n", " ").splitlines():
        linea = re.sub(r"(^|\s)#.*$", "", linea).strip()
        if not linea:
            continue
        try:
            palabras = shlex.split(linea)
        except ValueError:  # comillas sin cerrar: mejor una partición burda que callar
            palabras = linea.split()
        orden: list[str] = []
        for palabra in [*palabras, ";"]:
            if palabra in SEPARADORES:
                if orden:
                    yield orden
                orden = []
            else:
                orden.append(palabra)


def _requisitos(texto_yaml: str) -> list[str]:
    """Cada palabra con la que el workflow pide instalar el indexador, tal como la verá pip.

    En un ``run`` solo cuentan las líneas con ``install`` (``codebase-memory-mcp --version`` lo
    ejecuta, no lo instala). En cualquier otra clave (la lista ``instalar`` de la matriz, un ``with``)
    toda mención es un requisito: el workflow la pasará luego a pip."""

    requisitos = []
    for clave, texto in _textos(yaml.safe_load(texto_yaml)):
        for palabras in _ordenes(texto):
            if clave == "run" and "install" not in palabras:
                continue
            requisitos += [p for p in palabras if PAQUETE.search(p)]
    return requisitos


def _fijado(requisito: str) -> bool:
    """Solo ``==VERSION_FIJA`` o ``==$(python -m railspec.local.indexador_cbm)``, sin rangos.
    ``requisito`` ya trae la sustitución como ``MARCA``."""

    resto = re.sub(r"^\[[^\]]*\]", "", PAQUETE.sub("", requisito, count=1))  # sin extras
    return PAQUETE.match(requisito) is not None and resto in (
        f"=={indexador_cbm.VERSION_FIJA}",
        f"=={MARCA}",
    )


def _sin_fijar(texto_yaml: str) -> list[str]:
    return [r for r in _requisitos(texto_yaml) if not _fijado(r)]


def test_todo_workflow_instala_el_indexador_con_la_version_fijada():
    sin_fijar = {
        f.name: malos for f in sorted(WORKFLOWS.glob("*.yml")) if (malos := _sin_fijar(f.read_text("utf-8")))
    }
    assert not sin_fijar, (
        f"instalan codebase-memory-mcp sin la versión de VERSION_FIJA ({indexador_cbm.VERSION_FIJA}): "
        f'{sin_fijar}. Usar pip install "codebase-memory-mcp==$(python -m railspec.local.indexador_cbm)"; '
        "para subir la versión se cambia VERSION_FIJA, no los workflows."
    )


@pytest.mark.parametrize(
    ("workflow", "minimo"),
    [("railspec-ci.yml", 2), ("railspec-reindexar.yml", 1), ("railspec-binario.yml", 1)],
)
def test_los_workflows_que_usan_el_indexador_lo_instalan_y_la_prueba_los_ve(workflow, minimo):
    """Sin esto, un cambio de forma en el YAML dejaría la prueba anterior comprobando nada."""

    vistos = _requisitos((WORKFLOWS / workflow).read_text("utf-8"))
    assert len(vistos) >= minimo, f"{workflow}: se esperaban al menos {minimo} instalaciones, hay {vistos}"


# --- el comprobador falla donde debe --------------------------------------------------------------

FIJA = indexador_cbm.VERSION_FIJA
OTRA = f"{FIJA}.post1"


def _run(comando: str) -> str:
    return "jobs:\n  j:\n    steps:\n      - run: |\n" + textwrap.indent(comando, " " * 10) + "\n"


def _matriz(instalar: str) -> str:
    return f"jobs:\n  j:\n    strategy:\n      matrix:\n        include:\n          - instalar: {instalar}\n"


FIJADOS = {
    "derivada": _run(f'python -m pip install "codebase-memory-mcp=={DERIVADA}"'),
    "derivada_sin_comillas": _run(f"python -m pip install codebase-memory-mcp=={DERIVADA}"),
    "literal": _run(f"python -m pip install codebase-memory-mcp=={FIJA}"),
    "con_extras": _run(f"pip install 'codebase-memory-mcp[x]=={FIJA}'"),
    "con_continuacion": _run(f'pip install -e a \\\n  "codebase-memory-mcp=={DERIVADA}"'),
    "uv": _run(f"uv pip install codebase-memory-mcp=={FIJA}"),
    "tras_otro_tramo": _run(f"cd a && pip install codebase-memory-mcp=={FIJA} && pytest"),
    "matriz": _matriz(f"pytest codebase-memory-mcp=={FIJA}"),
}
SIN_FIJAR = {
    "sin_version": _run("python -m pip install -e a codebase-memory-mcp"),
    "otra_version": _run(f"python -m pip install codebase-memory-mcp=={OTRA}"),
    "otra_sustitucion": _run('pip install "codebase-memory-mcp==$(cat version.txt)"'),
    "rango_mayor_o_igual": _run(f"pip install 'codebase-memory-mcp>={FIJA}'"),
    "rango_compatible": _run(f"pip install 'codebase-memory-mcp~={FIJA}'"),
    "rango_menor_o_igual": _run(f"pip install 'codebase-memory-mcp<={FIJA}'"),
    "nombre_con_guion_bajo": _run("pip install codebase_memory_mcp"),
    "nombre_en_mayusculas": _run("pip install Codebase-Memory-MCP"),
    "desde_git": _run("pip install git+https://github.com/DeusData/codebase-memory-mcp"),
    "con_continuacion": _run("pip install -e a \\\n  codebase-memory-mcp"),
    "tras_otro_tramo": _run("cd a && pip install codebase-memory-mcp && pytest"),
    "yaml_plegado": (
        "jobs:\n  j:\n    steps:\n      - run: >-\n"
        "          python -m pip install\n          -e a\n          pytest codebase-memory-mcp\n"
    ),
    "matriz_sin_pin": _matriz("-e a pytest codebase-memory-mcp"),
    "matriz_otra_version": _matriz(f"pytest codebase-memory-mcp=={OTRA}"),
}
NO_INSTALAN = {
    "ejecuta_el_binario": _run("codebase-memory-mcp --version"),
    "ejecuta_tras_instalar_otra_cosa": _run("pip install -e a\ncodebase-memory-mcp --version"),
    "ejecuta_en_la_misma_linea": _run("pip install -e a && codebase-memory-mcp --version"),
    "comentario_de_shell": _run("# pip install codebase-memory-mcp\npip install -e a"),
    "comentario_yaml": "# pip install codebase-memory-mcp\n" + _run("pip install -e a"),
    "nombre_del_paso": (
        "jobs:\n  j:\n    steps:\n      - name: Instalar codebase-memory-mcp\n        run: pip install -e a\n"
    ),
    "otro_paquete": _run("pip install codebase-memory-mcp-extra==1.0 other"),
}


@pytest.mark.parametrize("texto", FIJADOS.values(), ids=FIJADOS)
def test_el_comprobador_acepta_lo_fijado(texto):
    assert _requisitos(texto), "debía detectar la instalación"
    assert _sin_fijar(texto) == []


@pytest.mark.parametrize("texto", SIN_FIJAR.values(), ids=SIN_FIJAR)
def test_el_comprobador_rechaza_lo_que_no_esta_fijado_a_version_fija(texto):
    assert _sin_fijar(texto), "debía marcar la instalación como sin fijar"


@pytest.mark.parametrize("texto", NO_INSTALAN.values(), ids=NO_INSTALAN)
def test_el_comprobador_no_confunde_ejecutar_comentar_o_rotular_con_instalar(texto):
    assert _requisitos(texto) == []


# --- las pruebas del instalador del kit corren en la CI ---------------------------------------------


def test_la_ci_corre_las_pruebas_del_instalador_con_railspec_local_instalado():
    """``test_convivencia_railspec.py`` hace ``importorskip("railspec.local.cli")``: sin railspec-local
    en el entorno del job se saltaría en silencio y la CI saldría en verde sin probar la convivencia."""

    ci = yaml.safe_load((WORKFLOWS / "railspec-ci.yml").read_text("utf-8"))
    entradas = [
        e for e in ci["jobs"]["pruebas"]["strategy"]["matrix"]["include"] if e["pruebas"] == "installer/tests"
    ]
    assert len(entradas) == 1, (
        "la matriz de railspec-ci.yml debe tener una entrada con pruebas: installer/tests"
    )
    assert "-e railspec/packages/railspec-local" in entradas[0]["instalar"]
    assert "pyyaml" in entradas[0]["instalar"].split()
    # Y los cambios del instalador disparan la CI (si no, la entrada solo correría por casualidad).
    disparadores = ci.get("on", ci.get(True))  # PyYAML (YAML 1.1) lee la clave ``on`` como True
    for evento in ("pull_request", "push"):
        assert "installer/**" in disparadores[evento]["paths"]
