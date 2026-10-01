"""Indexador real sobre codebase-memory-mcp; se salta si el binario no está instalado.

``pip install codebase-memory-mcp`` instala un lanzador que descarga el binario
verificado por checksum la primera vez.
"""

from __future__ import annotations

import shutil

import pytest
from local_fabricas import repo_git, sh
from railspec.contracts.snapshot import Relacion, TipoSimbolo, id_simbolo
from railspec.local.indexador_cbm import BINARIO, IndexadorCodebaseMemory

binario = shutil.which(BINARIO)
pytestmark = pytest.mark.skipif(binario is None, reason="codebase-memory-mcp no instalado")


@pytest.fixture
def indexador(tmp_path, monkeypatch):
    monkeypatch.setenv("CBM_CACHE_DIR", str(tmp_path / "cbm-cache"))
    return IndexadorCodebaseMemory(binario)


def test_delta_entre_base_y_arbol_de_trabajo(tmp_path, indexador):
    repo = repo_git(tmp_path / "repo")
    base = sh(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "calc.py").write_text(
        "def suma(a, b):\n    return a + b\n\n\ndef doble(x):\n    return suma(x, x)\n", encoding="utf-8"
    )
    (repo / "src" / "viejo.py").write_text("def resta(a, b):\n    return a - b\n", encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "mas")
    base = sh(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "calc.py").write_text(
        "def suma(a, b):\n    return a + b\n\n\ndef triple(x):\n    return suma(suma(x, x), x)\n",
        encoding="utf-8",
    )
    (repo / "src" / "viejo.py").unlink()

    delta = indexador.delta(repo, "certificados-api", base, ["src/calc.py", "src/viejo.py"], [])

    nombres = {s.nombre: s for s in delta.simbolos_upsert}
    assert {"src.calc.suma", "src.calc.triple"} <= set(nombres)
    triple = nombres["src.calc.triple"]
    assert triple.tipo == TipoSimbolo.funcion and (triple.linea_inicio, triple.linea_fin) == (5, 6)
    assert triple.id == id_simbolo("certificados-api", "src/calc.py", "funcion", "src.calc.triple")
    borrados = set(delta.simbolos_borrados)
    assert id_simbolo("certificados-api", "src/calc.py", "funcion", "src.calc.doble") in borrados
    assert id_simbolo("certificados-api", "src/viejo.py", "funcion", "src.viejo.resta") in borrados
    suma = nombres["src.calc.suma"].id
    assert any(
        a.origen == triple.id and a.destino == suma and a.relacion == Relacion.llama
        for a in delta.aristas_agregadas
    )
    assert delta.motor.nombre == "codebase-memory-mcp"
    # El índice nunca deja artefactos en el árbol de trabajo.
    assert not (repo / ".codebase-memory").exists()


def test_rutas_excluidas_no_se_indexan(tmp_path, indexador):
    repo = repo_git(tmp_path / "repo")
    base = sh(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    delta = indexador.delta(repo, "r", base, ["src/calc.py"], ["src/"])
    assert delta.simbolos_upsert == [] and delta.simbolos_borrados == []


def test_un_delta_usa_una_sola_sesion_y_no_deja_procesos(tmp_path, indexador, monkeypatch):
    from railspec.local import indexador_cbm

    sesiones = []
    original = indexador_cbm._SesionMcp

    def contar(*args, **kwargs):
        sesiones.append(original(*args, **kwargs))
        return sesiones[-1]

    monkeypatch.setattr(indexador_cbm, "_SesionMcp", contar)
    repo = repo_git(tmp_path / "repo")
    (repo / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    sh(repo, "add", "-A")
    sh(repo, "commit", "-q", "-m", "calc")
    base = sh(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "calc.py").write_text("def suma(a, b):\n    return b + a\n\n\ndef uno():\n    return 1\n")
    # Indexar el árbol, indexar la base, dos consultas por lado y borrar la base: una sesión.
    delta = indexador.delta(repo, "r", base, ["src/calc.py"], [])
    assert "src.calc.uno" in {s.nombre for s in delta.simbolos_upsert}
    assert len(sesiones) == 1 and not sesiones[0].viva and indexador._sesion is None


def test_sin_modo_servidor_vuelve_a_un_proceso_por_operacion(tmp_path, indexador, monkeypatch):
    from railspec.local import indexador_cbm

    def sin_servidor(*_args, **_kwargs):
        raise indexador_cbm.ErrorIndexador("sin modo servidor")

    monkeypatch.setattr(indexador_cbm, "_SesionMcp", sin_servidor)
    repo = repo_git(tmp_path / "repo")
    base = sh(repo, "rev-parse", "HEAD").strip()
    (repo / "src" / "calc.py").write_text("def suma(a, b):\n    return a + b\n", encoding="utf-8")
    delta = indexador.delta(repo, "r", base, ["src/calc.py"], [])
    assert "src.calc.suma" in {s.nombre for s in delta.simbolos_upsert}
    assert indexador._solo_cli and indexador._sesion is None
