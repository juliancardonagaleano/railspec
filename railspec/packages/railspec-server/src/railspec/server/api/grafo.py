"""Manejadores de ``graph.query`` y ``graph.index`` sobre el grafo central (``railspec-graph``).

- ``graph.query``: los repositorios visibles son los vinculados al workspace;
  el registro ya comprobó el rol ``lector``.
- ``graph.index``: solo actores de servicio (OIDC de GitHub Actions; el
  registro lo exige). Aquí se comprueba el alcance fino: el token viene del
  repositorio del vínculo y de un workflow corrido en su rama por defecto.
  Los lotes van al grafo de preparación del commit y el canónico avanza al
  llegar el último (``IndexadorCanonico``).

``railspec-graph`` es síncrono: corre en un hilo.
"""

from __future__ import annotations

import asyncio
import re
from typing import Any

from railspec.contracts.comun import Actor
from railspec.contracts.tools import (
    CodigoError,
    GraphIndexEntrada,
    GraphIndexSalida,
    GraphQueryEntrada,
    GraphQuerySalida,
)

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


#: Única forma admitida de URL de repositorio: ``https://github.com/<owner>/<repo>`` (con ``.git`` o ``/``
#: finales opcionales). Sin userinfo, puerto, consulta, fragmento ni segmentos de más.
_URL_GITHUB = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9][A-Za-z0-9-]{0,38})/(?P<repo>[A-Za-z0-9._-]{1,100}?)(?:\.git)?/?"
)


def repositorio_de_url(url: str) -> str | None:
    """``https://github.com/acme/api(.git)`` → ``acme/api``; None si no tiene exactamente esa forma.

    El resultado se usa como ruta bajo la carpeta de clones, así que ``.``, ``..`` y
    nombres de solo puntos no valen aunque el patrón de caracteres los admita.
    """

    m = _URL_GITHUB.fullmatch(url)
    if m is None:
        return None
    repo = m["repo"]
    if set(repo) == {"."} or repo == ".git":
        return None
    return f"{m['owner']}/{repo}"


def manejador_graph_index(indexador: Any, almacen: Any):
    from railspec.graph import IndiceDesfasado, IndiceRechazado

    async def graph_index(entrada: GraphIndexEntrada, actor: Actor) -> GraphIndexSalida:
        if actor.oidc is None:
            raise ErrorNegocio(CodigoError.fuera_de_alcance, "graph.index exige un token OIDC de Actions")
        vinculo = almacen.vinculo(entrada.alcance)
        if vinculo is None:
            a = entrada.alcance
            raise ErrorNegocio(
                CodigoError.no_encontrado, f"{a.org}/{a.workspace}/{a.repositorio} sin vínculo"
            )
        esperado = repositorio_de_url(vinculo.url)
        if esperado is None or actor.oidc.repositorio.lower() != esperado.lower():
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                f"el token es de {actor.oidc.repositorio}; el vínculo es de {esperado or vinculo.url}",
            )
        ref = actor.oidc.workflow.rpartition("@")[2]
        if ref != f"refs/heads/{vinculo.rama_por_defecto}" or entrada.rama != vinculo.rama_por_defecto:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance,
                f"el canónico sigue {vinculo.rama_por_defecto}; el workflow corrió en {ref}",
            )
        try:
            return await asyncio.to_thread(indexador.recibir, entrada, vinculo)
        except IndiceDesfasado as exc:
            raise ErrorNegocio(CodigoError.base_commit_distinto, str(exc)) from exc
        except IndiceRechazado as exc:
            raise ErrorNegocio(CodigoError.snapshot_invalido, str(exc)) from exc

    return graph_index
