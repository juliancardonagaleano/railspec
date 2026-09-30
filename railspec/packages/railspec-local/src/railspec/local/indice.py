"""Punto de extensión del indexador local.

La arquitectura fija ``codebase-memory-mcp`` como motor de indexado local
(tree-sitter, embeddings ``nomic-embed-code`` sin clave). El proxy define aquí
la interfaz que necesita y carga la implementación por *entry point* (grupo
``railspec.indexadores``); la de este paquete está en ``indexador_cbm`` y
otra se puede instalar sin tocar el proxy.

Sin indexador, el snapshot cae a ``solo-hashes``: viajan los hashes de
archivo y el servidor reindexa esos archivos desde el canónico tras el push.
"""

from __future__ import annotations

import os
from importlib.metadata import entry_points
from pathlib import Path
from typing import Protocol, runtime_checkable

from railspec.contracts.snapshot import DeltaIndice

GRUPO_ENTRY_POINT = "railspec.indexadores"
ENV_INDEXADOR = "RAILSPEC_INDEXADOR"


@runtime_checkable
class Indexador(Protocol):
    """Lo que el proxy pide al motor de indexado local."""

    def delta(
        self,
        worktree: Path,
        repositorio: str,
        base_commit: str,
        rutas: list[str],
        excluir: list[str],
    ) -> DeltaIndice:
        """Delta del índice entre ``base_commit`` y el árbol de trabajo, acotado a ``rutas``.

        Los embeddings se calculan aquí, en local. Los ids de símbolo salen de
        ``railspec.contracts.snapshot.id_simbolo``; una arista hacia otro repositorio
        del workspace usa el slug de ese vínculo y rellena ``repositorio_destino``.
        ``excluir`` son patrones que nunca deben indexarse (secretos, dependencias)."""
        ...

    def embedding_consulta(self, texto: str) -> bytes | None:
        """Vector int8 de 768 bytes para una búsqueda semántica, o None si no aplica.

        Lo usa ``graph_query`` cuando el contrato admite el vector de la
        consulta (contrato 1.1); ver ``ProxyLocal._preparar_consulta_grafo``."""
        ...


def cargar_indexador(nombre: str | None = None) -> Indexador | None:
    """Carga el primer indexador registrado que esté disponible en la máquina.

    Una fábrica devuelve None cuando su motor no está instalado (p. ej. falta el
    binario de codebase-memory-mcp); entonces el proxy trabaja en ``solo-hashes``."""

    nombre = nombre or os.environ.get(ENV_INDEXADOR)
    for entrada in entry_points(group=GRUPO_ENTRY_POINT):
        if nombre and entrada.name != nombre:
            continue
        indexador = entrada.load()()
        if indexador is None:
            continue
        if not isinstance(indexador, Indexador):
            raise TypeError(f"{entrada.value} no implementa railspec.local.indice.Indexador")
        return indexador
    return None
