"""``graph.index``: el canónico avanza con el índice que calcula CI (contrato 1.1).

Un job de GitHub Actions corre codebase-memory-mcp en cada push a la rama
por defecto y sube el delta por lotes. Los lotes de un commit se acumulan
en un grafo de preparación (``...:i:<commit>``, dentro del espacio del
repositorio) para que sobrevivan a reinicios y a varias réplicas del
servidor; el canónico solo avanza cuando llega el último.

Reglas:

- El alcance es el del vínculo y la rama, su rama por defecto.
- Con ``commit_anterior`` el delta es incremental y exige que el canónico
  esté en ese commit; sin él es un índice completo que reemplaza al canónico.
- Reenviar un lote o un commit ya aplicado es idempotente.
- Las exclusiones del vínculo se vuelven a aplicar aquí, como en la ingesta.
- Al avanzar el canónico se retiran las superposiciones de unidades integradas
  que el nuevo commit cubre (``AlmacenGrafo.retirar_superposiciones``). Lo que
  cubre lo declara CI en ``commits_cubiertos`` (1.5): las integradas en ese
  commit o en alguno de los declarados. Sin la lista (cliente 1.4): las
  integradas en ese commit y, con un índice completo, todas las retenidas.
"""

from __future__ import annotations

import logging

from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.tools import GraphIndexEntrada, GraphIndexSalida

from .acceso import AccesoGrafo, Espacio
from .almacen import AlmacenGrafo, _arista, _props, decodificar
from .ingesta import filtrar_delta
from .motor import AristaMotor, Meta

log = logging.getLogger(__name__)

_COMPLETO = "completo"


class IndiceRechazado(ValueError):
    pass


class IndiceDesfasado(IndiceRechazado):
    """``commit_anterior`` no es el commit del canónico: falta un push intermedio o llegó tarde."""


class IndexadorCanonico:
    def __init__(self, acceso: AccesoGrafo, grafo: AlmacenGrafo) -> None:
        self._acceso = acceso
        self._grafo = grafo

    def recibir(self, entrada: GraphIndexEntrada, vinculo: VinculoRepositorio) -> GraphIndexSalida:
        alcance = entrada.alcance
        if alcance != vinculo.alcance:
            raise IndiceRechazado("el índice no es del repositorio del vínculo")
        if entrada.rama != vinculo.rama_por_defecto:
            raise IndiceRechazado(f"rama {entrada.rama}; el canónico sigue {vinculo.rama_por_defecto}")

        canon = self._acceso.espacio(alcance)
        vigente = canon.meta().commit
        if vigente == entrada.commit:
            # Un reenvío tras una caída entre avanzar el canónico y retirar las superposiciones;
            # solo lo que este commit cubre, no lo que se integró en un commit posterior.
            self._retirar(entrada, completo=False)
            return _salida(entrada, entrada.lotes, aplicado=True)
        if entrada.commit_anterior is not None and entrada.commit_anterior != vigente:
            raise IndiceDesfasado(
                f"el canónico está en {vigente}, el delta parte de {entrada.commit_anterior}"
            )

        prep = self._acceso.espacio_indexado(alcance, entrada.commit)
        estado = _Estado.leer(prep)
        estado.validar(entrada)
        if entrada.lote not in estado.recibidos:
            delta = filtrar_delta(entrada.delta, list(vinculo.exclusiones))
            prep.upsert_simbolos([_props(s) for s in delta.simbolos_upsert])
            prep.agregar_aristas([_arista(a) for a in delta.aristas_agregadas])
            prep.fijar_embeddings({e.simbolo: decodificar(e.vector_b64) for e in delta.embeddings})
            estado.recibidos.add(entrada.lote)
            estado.borrados |= set(delta.simbolos_borrados)
            estado.aristas_borradas |= {
                (a.origen, a.destino, a.relacion.value) for a in delta.aristas_borradas
            }
            estado.escribir(prep, entrada)

        if len(estado.recibidos) < entrada.lotes:
            return _salida(entrada, len(estado.recibidos), aplicado=False)

        self._aplicar(canon, prep, estado, entrada)
        prep.borrar()
        return _salida(entrada, entrada.lotes, aplicado=True)

    def _aplicar(self, canon: Espacio, prep: Espacio, estado: _Estado, entrada: GraphIndexEntrada) -> None:
        if entrada.commit_anterior is None:
            canon.borrar()
        else:
            canon.borrar_simbolos(sorted(estado.borrados))
            canon.borrar_aristas([AristaMotor(*a) for a in sorted(estado.aristas_borradas)])
        simbolos = prep.todos_simbolos()
        canon.upsert_simbolos(simbolos)
        canon.agregar_aristas(prep.todas_aristas())
        canon.fijar_embeddings(prep.leer_embeddings([s["id"] for s in simbolos]))
        canon.fijar_meta(Meta(commit=entrada.commit))
        self._grafo.recalcular_analitica(entrada.alcance)
        self._retirar(entrada, completo=entrada.commit_anterior is None)

    def _retirar(self, entrada: GraphIndexEntrada, completo: bool) -> None:
        retiradas = self._grafo.retirar_superposiciones(
            entrada.alcance, entrada.commit, completo, entrada.commits_cubiertos
        )
        if retiradas:
            a = entrada.alcance
            log.info(
                "canónico de %s/%s/%s en %s: superposiciones retiradas %s",
                a.org,
                a.workspace,
                a.repositorio,
                entrada.commit,
                retiradas,
            )


def _salida(entrada: GraphIndexEntrada, lotes_recibidos: int, aplicado: bool) -> GraphIndexSalida:
    """La respuesta habla la versión del cliente: uno 1.4 valida ``version_contrato`` contra 1.0 a 1.4."""

    return GraphIndexSalida(
        version_contrato=entrada.version_contrato,
        commit=entrada.commit,
        lotes_recibidos=lotes_recibidos,
        aplicado=aplicado,
    )


class _Estado:
    """Lotes recibidos de un commit, guardados en la meta del grafo de preparación."""

    def __init__(self, meta: Meta) -> None:
        self.base = meta.base
        self.lotes = meta.lotes
        self.recibidos = set(meta.recibidos)
        self.borrados = set(meta.borrados)
        self.aristas_borradas = {tuple(a) for a in meta.aristas_borradas}

    @classmethod
    def leer(cls, prep: Espacio) -> _Estado:
        return cls(prep.meta())

    def validar(self, entrada: GraphIndexEntrada) -> None:
        if self.lotes is None:
            return
        if self.lotes != entrada.lotes or self.base != (entrada.commit_anterior or _COMPLETO):
            raise IndiceRechazado("los lotes de un mismo commit no coinciden en total o en commit_anterior")

    def escribir(self, prep: Espacio, entrada: GraphIndexEntrada) -> None:
        prep.fijar_meta(
            Meta(
                commit=entrada.commit,
                borrados=sorted(self.borrados),
                aristas_borradas=sorted(self.aristas_borradas),
                base=entrada.commit_anterior or _COMPLETO,
                lotes=entrada.lotes,
                recibidos=sorted(self.recibidos),
            )
        )
