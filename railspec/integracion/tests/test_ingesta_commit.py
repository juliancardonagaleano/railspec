"""Ingesta por commit del grafo canónico, de punta a punta, sin GitHub Actions.

Recorre el mismo camino que ``railspec-reindexar.yml`` en cada push a la rama
por defecto, con piezas reales salvo GitHub:

repositorio git → ``deploy/ci/reindexar.py`` (subproceso, como lo lanza el
workflow) → codebase-memory-mcp calcula el delta → lotes a
``POST /v1/tools/graph.index`` con un token OIDC → ``VerificadorOidcActions``
(firma contra un JWKS por HTTP) → ``IndexadorCanonico`` → FalkorDB.

GitHub lo pone ``railspec_e2e.oidc.EmisorOidc``: el endpoint de tokens del
runner (``ACTIONS_ID_TOKEN_REQUEST_URL``) y el JWKS del emisor. El servidor
corre en un subproceso propio con ese emisor y un vínculo de repositorio solo
de esta prueba, así su grafo canónico no se cruza con el del ciclo.

Pushes, en orden (el recorrido se corre una vez por módulo):

A. Primer push a la rama (``before`` = ceros): índice completo.
B. Push con ``before`` = A: delta incremental que añade, cambia y borra
   funciones y archivos.
C. Push con ``before`` = A, pero el canónico ya está en B (un reindexado
   intermedio se perdió): el servidor responde ``base-commit-distinto`` y
   ``reindexar.py`` repite con índice completo.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.repositorio import nombre_grafo
from railspec_e2e import ORG, TOKEN, WS
from railspec_e2e.arnes import MARCA_CODIGO, git
from railspec_e2e.oidc import EmisorOidc

pytestmark = pytest.mark.skipif(
    shutil.which("codebase-memory-mcp") is None, reason="sin codebase-memory-mcp no hay delta que subir"
)

REINDEXAR = Path(__file__).resolve().parents[2] / "deploy" / "ci" / "reindexar.py"
SLUG = "ingesta-commit"
REPO_GH = f"{ORG}/{SLUG}"
RAMA = "main"
AUDIENCIA = "railspec"
GRAFO = nombre_grafo(AlcanceRepositorio(org=ORG, workspace=WS, repositorio=SLUG))
CERO = "0" * 40
#: Lotes pequeños: cada índice viaja en varios ``graph.index``.
TAMANO_LOTE = 4

# --- repositorio de ejemplo ------------------------------------------------------------
#
# Llamadas en A:  cli.principal → informe.informe → nucleo.promedio → nucleo.sumar → nucleo.normalizar
#                                                └→ informe.formatear
#                 viejo.usar_legado → viejo.legado → nucleo.normalizar
# Procesos en A:  principal y usar_legado. En B desaparece viejo.py (y su proceso)
# y aparece informe.resumen, un punto de entrada nuevo. cli.py no cambia nunca:
# sus aristas solo siguen en el canónico si el delta no las pierde.

NUCLEO_A = f'''"""Núcleo de cálculo. {MARCA_CODIGO}"""


def normalizar(x):
    return abs(x)


def sumar(a, b):
    return normalizar(a) + normalizar(b)


def promedio(xs):
    total = 0
    for x in xs:
        total = sumar(total, x)
    return total / len(xs)
'''

NUCLEO_B = f'''"""Núcleo de cálculo. {MARCA_CODIGO}"""


def normalizar(x):
    return abs(x)


def sumar(a, b):
    # {MARCA_CODIGO}: cambia el cuerpo, no el nombre.
    resultado = normalizar(a)
    return resultado + normalizar(b)


def restar(a, b):
    return normalizar(a) - normalizar(b)


def promedio(xs):
    total = 0
    for x in xs:
        total = sumar(total, x)
    return total / len(xs)
'''

INFORME_A = f'''from calc.nucleo import promedio

_SELLO = "{MARCA_CODIGO}"


def formatear(v):
    return f"{{v:.2f}}"


def informe(xs):
    return formatear(promedio(xs))


def obsoleta():
    return _SELLO
'''

INFORME_B = f'''from calc.nucleo import promedio, restar

_SELLO = "{MARCA_CODIGO}"


def formatear(v):
    return f"{{v:.2f}}"


def informe(xs):
    return formatear(promedio(xs))


def resumen(xs):
    return informe(xs) + formatear(restar(max(xs), min(xs)))
'''

VIEJO = f"""from calc.nucleo import normalizar


def legado(x):
    return normalizar(x)  # {MARCA_CODIGO}


def usar_legado():
    return legado(-1)
"""

CLI = """from calc.informe import informe


def principal():
    print(informe([1, 2, 3]))
"""

ARBOLES = {
    "A": {
        "calc/__init__.py": "",
        "calc/nucleo.py": NUCLEO_A,
        "calc/informe.py": INFORME_A,
        "calc/viejo.py": VIEJO,
        "calc/cli.py": CLI,
    },
    "B": {
        "calc/nucleo.py": NUCLEO_B,
        "calc/informe.py": INFORME_B,
        "calc/viejo.py": None,
        "calc/nuevo.py": f'def nueva():\n    return "{MARCA_CODIGO}"\n',
    },
    "C": {
        "calc/nuevo.py": None,
        "calc/extra.py": "def multiplicar(a, b):\n    return a * b\n",
    },
}


def commitear(clon: Path, nombre: str) -> str:
    for ruta, contenido in ARBOLES[nombre].items():
        archivo = clon / ruta
        if contenido is None:
            archivo.unlink()
        else:
            archivo.parent.mkdir(parents=True, exist_ok=True)
            archivo.write_text(contenido, encoding="utf-8")
    git(clon, "add", "-A")
    git(clon, "commit", "-q", "-m", nombre)
    return git(clon, "rev-parse", "HEAD")


# --- servidor y consultas -------------------------------------------------------------


def graph_query(base: str, consulta: dict[str, Any], limite: int = 200) -> dict[str, Any]:
    """``graph.query`` por HTTP con el token de desarrollo, como la consola."""

    cuerpo = {
        "alcance": {"org": ORG, "workspace": WS},
        "repositorios": [SLUG],
        "consulta": consulta,
        "limite": limite,
    }
    peticion = urllib.request.Request(
        f"{base}/v1/tools/graph.query",
        data=json.dumps(cuerpo).encode(),
        method="POST",
        headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(peticion, timeout=30) as r:
        return json.load(r)


@dataclass
class Estado:
    """Lo que ``graph.query`` dice del canónico tras un push."""

    commit: str
    salida: str
    commits: dict[str, str]
    ids: dict[str, str]  # nombre calificado → id de símbolo
    procesos: dict[str, set[str]] = field(default_factory=dict)  # símbolo → procesos (related)
    clusters: dict[str, set[str]] = field(default_factory=dict)  # símbolo → clusters (related)
    vecinos: dict[str, set[str]] = field(default_factory=dict)  # símbolo → vecinos a distancia 1


#: Símbolos cuyo ``related`` se guarda en cada push (los que existan en él).
RELACIONADOS = ("calc.nucleo.normalizar", "calc.nucleo.sumar", "calc.informe.informe")


def leer_canonico(base: str, commit: str, salida: str) -> Estado:
    todo = graph_query(base, {"verbo": "search", "texto": "calc."})
    assert not todo["truncado"]
    ids = {r["ref"]["nombre"]: r["ref"]["simbolo"] for r in todo["resultados"]}
    estado = Estado(commit, salida, todo["commits"], ids)
    for nombre in RELACIONADOS:
        if nombre not in ids:
            continue
        resultados = graph_query(base, {"verbo": "related", "simbolo": ids[nombre]})["resultados"]
        refs = [r["ref"] for r in resultados]
        estado.procesos[nombre] = {r["nombre"] for r in refs if r.get("clase") == "proceso"}
        estado.clusters[nombre] = {r["nombre"] for r in refs if r.get("clase") == "cluster"}
        estado.vecinos[nombre] = {
            r["ref"]["nombre"]
            for r in resultados
            if r["ref"]["tipo"] == "simbolo" and r.get("distancia") == 1
        }
    return estado


@dataclass
class Recorrido:
    pushes: dict[str, Estado]
    emisor: EmisorOidc
    grafos: dict[str, list]  # grafo de FalkorDB de la prueba → nodos y aristas
    mongo: dict[str, list]  # colección → documentos


def reindexar(clon: Path, base: str, emisor: EmisorOidc, commit: str, anterior: str) -> str:
    """El paso ``Reindexar`` del workflow, con las variables que GitHub pone en el runner."""

    proc = subprocess.run(
        [
            sys.executable,
            str(REINDEXAR),
            "--servidor",
            base,
            "--org",
            ORG,
            "--workspace",
            WS,
            "--repositorio",
            SLUG,
            "--rama",
            RAMA,
            "--commit",
            commit,
            "--anterior",
            anterior,
            "--audiencia",
            AUDIENCIA,
            "--raiz",
            str(clon),
            "--tamano-lote",
            str(TAMANO_LOTE),
        ],
        cwd=clon,
        env={**os.environ, **emisor.entorno_runner(), "GITHUB_SHA": commit, "GITHUB_REF_NAME": RAMA},
        capture_output=True,
        text=True,
        timeout=600,
    )
    assert proc.returncode == 0, f"reindexar.py terminó con {proc.returncode}:\n{proc.stdout}\n{proc.stderr}"
    return proc.stdout


def _borrar_grafos(db) -> None:
    for nombre in db.list_graphs():
        if nombre == GRAFO or nombre.startswith(GRAFO + ":"):
            db.select_graph(nombre).delete()


@pytest.fixture(scope="module")
def recorrido(lanzador, tmp_path_factory) -> Recorrido:
    tmp = tmp_path_factory.mktemp("ingesta")
    clon = tmp / SLUG
    subprocess.run(["git", "init", "-q", "-b", RAMA, str(clon)], check=True)
    git(clon, "config", "user.email", "e2e@railspec.test")
    git(clon, "config", "user.name", "Railspec e2e")

    with EmisorOidc(REPO_GH, RAMA) as emisor:
        extra = {
            "RAILSPEC_E2E_REPO": SLUG,
            "RAILSPEC_OIDC_AUDIENCIA": AUDIENCIA,
            "RAILSPEC_OIDC_EMISOR": emisor.emisor,
            "RAILSPEC_OIDC_REPOSITORIOS": REPO_GH,
        }
        with lanzador(tmp, extra) as entorno:
            fk = entorno.falkordb()
            _borrar_grafos(fk)  # restos de una corrida interrumpida
            try:
                pushes: dict[str, Estado] = {}
                # A: rama nueva, github.event.before son ceros.
                a = commitear(clon, "A")
                pushes["A"] = leer_canonico(entorno.base, a, reindexar(clon, entorno.base, emisor, a, CERO))
                # B: push normal sobre A.
                b = commitear(clon, "B")
                pushes["B"] = leer_canonico(entorno.base, b, reindexar(clon, entorno.base, emisor, b, a))
                # C: el reindexado de B "se perdió" para el runner, que cree que el canónico sigue en A.
                c = commitear(clon, "C")
                pushes["C"] = leer_canonico(entorno.base, c, reindexar(clon, entorno.base, emisor, c, a))

                grafos = {}
                for nombre in fk.list_graphs():
                    if nombre == GRAFO or nombre.startswith(GRAFO + ":"):
                        g = fk.select_graph(nombre)
                        grafos[nombre] = [
                            *g.query("MATCH (n) RETURN labels(n), properties(n)").result_set,
                            *g.query("MATCH ()-[r]->() RETURN type(r), properties(r)").result_set,
                        ]
                db = entorno.mongo()
                mongo = {c: list(db[c].find()) for c in db.list_collection_names()}
                yield Recorrido(pushes, emisor, grafos, mongo)
            finally:
                _borrar_grafos(fk)


# --- pruebas ---------------------------------------------------------------------------


def test_el_runner_se_autentica_con_oidc_contra_el_jwks(recorrido):
    emitidos = recorrido.emisor.emitidos
    # Un token por corrida de reindexar.py (se reusa entre lotes) y el servidor lo verificó
    # con la clave pública que leyó del JWKS por HTTP.
    assert len(emitidos) == 3
    assert {t["aud"] for t in emitidos} == {AUDIENCIA}
    assert {t["repository"] for t in emitidos} == {REPO_GH}
    assert all(t["workflow_ref"].endswith(f"@refs/heads/{RAMA}") for t in emitidos)
    assert recorrido.emisor.lecturas_jwks >= 1


def test_primer_push_indice_completo_por_lotes(recorrido):
    a = recorrido.pushes["A"]
    assert "índice completo" in a.salida
    lotes = [linea for linea in a.salida.splitlines() if linea.startswith("lote ")]
    assert len(lotes) > 1, a.salida  # el índice viajó partido
    assert lotes[-1].endswith("aplicado=True") and all("aplicado=False" in x for x in lotes[:-1])
    assert f"canónico en {a.commit}" in a.salida

    assert a.commits == {SLUG: a.commit}
    assert {
        "calc.cli.principal",
        "calc.informe.informe",
        "calc.informe.formatear",
        "calc.informe.obsoleta",
        "calc.nucleo.normalizar",
        "calc.nucleo.sumar",
        "calc.nucleo.promedio",
        "calc.viejo.legado",
        "calc.viejo.usar_legado",
    } <= set(a.ids)
    # Procesos y clusters calculados al aplicar el índice.
    assert a.procesos["calc.nucleo.normalizar"] == {"calc.cli.principal", "calc.viejo.usar_legado"}
    assert a.clusters["calc.nucleo.sumar"]
    assert {"calc.nucleo.promedio", "calc.nucleo.normalizar"} <= a.vecinos["calc.nucleo.sumar"]


def test_push_incremental_aplica_el_delta(recorrido):
    a, b = recorrido.pushes["A"], recorrido.pushes["B"]
    assert "delta:" in b.salida and "índice completo" not in b.salida
    assert f"canónico en {b.commit}" in b.salida
    assert b.commits == {SLUG: b.commit}

    nombres = set(b.ids)
    # Lo borrado (una función y un archivo entero) ya no está.
    assert "calc.informe.obsoleta" not in nombres
    assert not [n for n in nombres if n.startswith("calc.viejo")]
    # Lo nuevo sí.
    assert {"calc.nucleo.restar", "calc.informe.resumen", "calc.nuevo.nueva"} <= nombres
    # Lo que no cambió (o cambió solo de cuerpo) conserva el id.
    for nombre in ("calc.cli.principal", "calc.informe.informe", "calc.nucleo.sumar", "calc.nucleo.promedio"):
        assert b.ids[nombre] == a.ids[nombre]
    # Las aristas que no viajaron en el delta siguen: las de cli.py (que no cambió)
    # y las de los archivos tocados que no cambiaron (promedio → sumar).
    assert {"calc.nucleo.promedio", "calc.nucleo.normalizar"} <= b.vecinos["calc.nucleo.sumar"]
    assert "calc.cli.principal" in b.vecinos["calc.informe.informe"]
    assert "calc.informe.resumen" in b.vecinos["calc.informe.informe"]


def test_push_incremental_recalcula_clusters_y_procesos(recorrido):
    a, b = recorrido.pushes["A"], recorrido.pushes["B"]
    # usar_legado desapareció con viejo.py; resumen es un punto de entrada nuevo y
    # principal sigue entero, aunque sus primeras aristas vienen de un archivo sin cambios.
    assert b.procesos["calc.nucleo.normalizar"] == {"calc.cli.principal", "calc.informe.resumen"}
    assert b.procesos["calc.informe.informe"] == {"calc.cli.principal", "calc.informe.resumen"}
    assert b.clusters["calc.nucleo.sumar"]
    assert b.procesos != a.procesos


def test_delta_desfasado_cae_a_indice_completo(recorrido):
    b, c = recorrido.pushes["B"], recorrido.pushes["C"]
    lineas = c.salida.splitlines()
    # reindexar.py planeó un delta desde A, el servidor dijo que el canónico está en B
    # (409 base-commit-distinto) y repitió con índice completo.
    assert lineas[0].startswith("delta:")
    desfase = next(i for i, x in enumerate(lineas) if "canónico desfasado (base-commit-distinto)" in x)
    assert lineas[desfase + 1].startswith("índice completo:")
    reintento = lineas[desfase + 2 : -1]
    assert reintento and all(x.startswith("lote ") for x in reintento)
    assert reintento[-1].endswith("aplicado=True")
    assert lineas[-1] == f"canónico en {c.commit}"
    assert c.commits == {SLUG: c.commit}

    nombres = set(c.ids)
    assert "calc.extra.multiplicar" in nombres and "calc.nuevo.nueva" not in nombres
    # El índice completo reconstruye todo lo de B que sigue en C.
    nuevo, extra = {"calc.nuevo", "calc.nuevo.nueva"}, {"calc.extra", "calc.extra.multiplicar"}
    assert nombres == (set(b.ids) - nuevo) | extra
    assert c.procesos["calc.nucleo.normalizar"] == b.procesos["calc.nucleo.normalizar"]


def test_no_quedan_grafos_de_preparacion(recorrido):
    # Los lotes se acumulan en ``...:i:<commit>`` y se borran al aplicar el último.
    assert not [g for g in recorrido.grafos if ":i:" in g[len(GRAFO) :]]
    assert GRAFO in recorrido.grafos


def test_el_grafo_no_lleva_codigo(recorrido):
    assert recorrido.grafos
    for nombre, filas in recorrido.grafos.items():
        assert MARCA_CODIGO not in json.dumps(filas, default=str), f"código en el grafo {nombre}"
    # Un símbolo solo lleva estructura: ruta, rango y hash.
    permitidas = {"id", "nombre", "tipo", "ruta", "linea_inicio", "linea_fin", "sha256", "stub", "embedding"}
    for etiquetas, propiedades in recorrido.grafos[GRAFO]:
        if etiquetas == ["Simbolo"]:
            assert set(propiedades) <= permitidas, propiedades
    for coleccion, documentos in recorrido.mongo.items():
        assert MARCA_CODIGO not in json.dumps(documentos, default=str), f"código en Mongo: {coleccion}"
