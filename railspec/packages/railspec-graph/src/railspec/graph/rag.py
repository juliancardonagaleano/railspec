"""RAG sobre el grafo: recupera referencias, nunca texto.

Similitud por vectores en cada repositorio visible del workspace, luego
expansión por el grafo (vecinos estructurales y procesos del acierto). El
resultado son referencias tipadas: el arnés las resuelve contra su clon en
local y el chat, con ``code.read`` al vuelo; aquí no hay texto que filtrar.
"""

from __future__ import annotations

from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace
from railspec.contracts.snapshot import Relacion
from railspec.contracts.tools import ConsultaSearch, GraphQueryEntrada, ResultadoGrafo

from .almacen import AlmacenGrafo, decodificar, resolver
from .analitica import RELACIONES_DEPENDENCIA

#: Peso de un vecino respecto al acierto que lo trajo.
FACTOR_VECINO = 0.5


class RecuperadorContexto:
    def __init__(self, grafo: AlmacenGrafo) -> None:
        self._grafo = grafo

    def recuperar(
        self,
        alcance: AlcanceWorkspace,
        visibles: list[AlcanceRepositorio],
        vector_b64: str,
        k: int = 10,
        unidad: str | None = None,
        repositorios: list[str] | None = None,
        expandir: bool = True,
    ) -> list[ResultadoGrafo]:
        consulta = GraphQueryEntrada(
            alcance=alcance,
            repositorios=repositorios or [],
            unidad=unidad,
            consulta=ConsultaSearch(texto="rag", semantica=True),
        )
        vistas = self._grafo.vistas(consulta, visibles)
        vector = decodificar(vector_b64)
        aciertos: list[tuple[float, str]] = []
        for v in vistas:
            aciertos += [(p, i) for i, p in v.knn(vector, k)]
        aciertos = sorted(aciertos, key=lambda a: (-a[0], a[1]))[:k]
        puntos: dict[str, tuple[float, int, str | None]] = {i: (p, 0, None) for p, i in aciertos}
        if expandir:
            for p, i in aciertos:
                for v in vistas:
                    for lado in ("entrada", "salida"):
                        for a in v.aristas([i], list(RELACIONES_DEPENDENCIA), lado):
                            otro = a.origen if lado == "entrada" else a.destino
                            candidato = (p * FACTOR_VECINO, 1, a.relacion)
                            if otro not in puntos or puntos[otro][0] < candidato[0]:
                                puntos[otro] = candidato
        hallados = resolver(vistas, list(puntos))
        orden = sorted((i for i in puntos if i in hallados), key=lambda i: (-puntos[i][0], puntos[i][1], i))
        resultados = []
        for i in orden[: k * 3]:
            v, s = hallados[i]
            p, d, rel = puntos[i]
            resultados.append(
                ResultadoGrafo(
                    ref=v.ref(s), puntuacion=round(p, 6), distancia=d, relacion=Relacion(rel) if rel else None
                )
            )
        return resultados
