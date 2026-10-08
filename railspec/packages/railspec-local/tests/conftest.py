"""Aislamiento común de las pruebas del proxy local."""

from __future__ import annotations

import pytest
from railspec.local import codificador


@pytest.fixture(autouse=True)
def sin_modelo_de_embeddings_del_equipo(tmp_path_factory, monkeypatch):
    """Que el modelo que la persona tenga instalado no cambie el resultado de las pruebas.

    Las pruebas que quieren un codificador lo inyectan (``proxy.codificador``) o fijan la variable."""

    monkeypatch.setenv(codificador.ENV_MODELO, str(tmp_path_factory.mktemp("sin-modelo") / "no-existe"))
