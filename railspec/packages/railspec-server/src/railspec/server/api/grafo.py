"""Manejador de ``graph.query`` sobre el grafo central (``railspec-graph``).

Los repositorios visibles son los vinculados al workspace; el registro ya
comprobó el rol ``lector``. ``AlmacenGrafo`` es síncrono: corre en un hilo.
"""

from __future__ import annotations

import asyncio
from typing import Any

from railspec.contracts.comun import Actor
from railspec.contracts.tools import CodigoError, GraphQueryEntrada, GraphQuerySalida

from ..motor.motor import ErrorNegocio


def manejador_graph_query(grafo: Any, almacen: Any):
    async def graph_query(entrada: GraphQueryEntrada, actor: Actor) -> GraphQuerySalida:
        a = entrada.alcance
        visibles = [v.alcance for v in almacen.vinculos(a.org, a.workspace)]
        try:
            return await asyncio.to_thread(grafo.consultar, entrada, visibles)
        except PermissionError as exc:  # FueraDeWorkspace, RepositorioNoVisible
            raise ErrorNegocio(CodigoError.fuera_de_alcance, str(exc)) from exc

    return graph_query
