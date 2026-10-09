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
import shutil
import subprocess
import textwrap

import pytest
import yaml
from _workflows import WORKFLOWS, requisitos
from railspec.local import indexador_cbm

PAQUETE = re.compile(r"codebase[-_.]memory[-_.]mcp(?![\w.-])", re.IGNORECASE)
#: La forma con la que los workflows piden la versión fijada: correcta por construcción.
DERIVADA = "$(python -m railspec.local.indexador_cbm)"
#: Sustituye a DERIVADA antes de partir en palabras, para que valga con o sin comillas.
MARCA = "@derivada@"


def _requisitos(texto_yaml: str) -> list[str]:
    """Cada palabra con la que el workflow pide instalar el indexador, tal como la verá pip."""

    return requisitos(texto_yaml, PAQUETE, {DERIVADA: MARCA})


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


# --- el índice completo programado de railspec-reindexar.yml -----------------------------------------


class _Contexto:
    """Un contexto de GitHub Actions para evaluar expresiones: lo que no existe vale cadena vacía."""

    def __init__(self, **valores) -> None:
        self.__dict__.update(valores)

    def __getattr__(self, nombre: str):
        return ""


def _evaluar(expresion: str, evento: str, *, fork: bool = False, **variables: str):
    """Evalúa una expresión de ``if:`` o de ``env:`` del workflow (solo ``&&``, ``||``, ``==``, ``!=``,
    cadenas, ``false`` y contextos), con la semántica de Actions para lo que falta."""

    python = re.sub(r"\bfalse\b", "False", expresion.replace("&&", " and ").replace("||", " or "))
    return eval(  # el texto sale del propio repositorio y los contextos son los de arriba
        python,
        {"__builtins__": {}},
        {
            "vars": _Contexto(**variables),
            "inputs": _Contexto(),
            "github": _Contexto(
                event_name=evento,
                ref="refs/heads/master",
                event=_Contexto(repository=_Contexto(fork=fork)),
            ),
        },
    )


def _reindexar() -> dict:
    return yaml.safe_load((WORKFLOWS / "railspec-reindexar.yml").read_text("utf-8"))


def _paso_reindexar(flujo: dict) -> dict:
    return next(p for p in flujo["jobs"]["reindexar"]["steps"] if p.get("name") == "Reindexar")


VARIABLES = {
    "RAILSPEC_URL": "https://railspec.example.com",
    "RAILSPEC_OIDC_AUDIENCIA": "audiencia-larga-y-no-adivinable",
    "RAILSPEC_ORGANIZACION": "acme",
    "RAILSPEC_WORKSPACE": "ws",
}


def test_el_reindexado_se_programa_una_vez_por_semana():
    flujo = _reindexar()
    disparadores = flujo.get("on", flujo.get(True))
    assert {"push", "schedule", "workflow_dispatch"} <= disparadores.keys()
    (programado,) = disparadores["schedule"]
    minuto, hora, dia_mes, mes, dia_semana = programado["cron"].split()
    # Una vez por semana: minuto y hora fijos, un solo día de la semana y ninguna restricción más.
    assert minuto.isdigit() and hora.isdigit() and dia_semana.isdigit()
    assert (dia_mes, mes) == ("*", "*")
    assert minuto != "0", "la hora en punto es cuando GitHub retrasa o descarta los programados"


def test_el_programado_corre_en_la_rama_por_defecto_y_con_las_variables_de_oidc():
    condicion = _reindexar()["jobs"]["reindexar"]["if"]
    assert _evaluar(condicion, "schedule", **VARIABLES)
    # Un fork no corre el programado, tenga o no las variables.
    assert not _evaluar(condicion, "schedule", fork=True, **VARIABLES)
    for falta in VARIABLES:
        assert not _evaluar(condicion, "schedule", **{**VARIABLES, falta: ""}), f"corre sin {falta}"
    # El push conserva su condición de siempre: solo exige RAILSPEC_URL.
    assert _evaluar(condicion, "push", RAILSPEC_URL=VARIABLES["RAILSPEC_URL"])
    assert not _evaluar(condicion, "push")


def test_el_programado_corre_el_indice_completo_sin_retirar_todas():
    """``--anterior ""`` es el índice completo; sin ``--sin-cobertura`` retira solo lo cubierto."""

    bash = shutil.which("bash")
    if bash is None:
        pytest.skip("sin bash")
    paso = _paso_reindexar(_reindexar())
    completo = paso["env"]["COMPLETO"].removeprefix("${{").removesuffix("}}").strip()
    assert _evaluar(completo, "schedule")
    assert not _evaluar(completo, "push")

    def lanzar(evento: str, **entorno: str) -> list[str]:
        guion = re.sub(r"\$\{\{\s*vars\.(\w+)\s*\}\}", r"v-\1", paso["run"])
        guion = "python() { printf '<%s>\\n' \"$@\"; }\n" + guion
        env = {
            "PATH": "/usr/bin:/bin",
            "GITHUB_REF_NAME": "master",
            "GITHUB_SHA": "a" * 40,
            "GITHUB_WORKSPACE": "/ws",
            "REPOSITORIO": "repo",
            "COMPLETO": "true" if evento == "schedule" else "",
            "RETIRAR_TODAS": "",
            "ANTERIOR": "" if evento == "schedule" else "b" * 40,
            **entorno,
        }
        salida = subprocess.run([bash, "-c", guion], env=env, capture_output=True, text=True, check=True)
        return re.findall(r"<(.*)>", salida.stdout)

    programado = lanzar("schedule")
    assert programado[programado.index("--anterior") + 1] == ""
    assert "--sin-cobertura" not in programado
    # Es el modo completo aunque llegara un commit anterior.
    forzado = lanzar("schedule", ANTERIOR="b" * 40)
    assert forzado[forzado.index("--anterior") + 1] == ""
    # El push normal sigue siendo delta.
    delta = lanzar("push")
    assert delta[delta.index("--anterior") + 1] == "b" * 40
