"""Ingesta de snapshots del proxy local en el grafo, con la política del vínculo.

El contrato ya impide que un snapshot ``restringido`` lleve diff o
fragmentos; aquí se añade lo que depende del vínculo del servidor:

- el snapshot debe ser del repositorio y del workspace del vínculo;
- no puede declarar un nivel más abierto que el del vínculo;
- las exclusiones del vínculo se aplican otra vez en el servidor (defensa
  en profundidad): esos símbolos no entran al grafo;
- con los criterios de las tareas completadas, enlaza cada ``CA-NN`` con los
  símbolos que el snapshot cambió respecto del anterior (trazabilidad).

El grafo nunca guarda texto de código, en ningún nivel: ni el diff ni los
fragmentos del snapshot pasan de aquí. Si el nivel lo permite, persistirlos
(cifrados y con TTL) es cosa de la colección ``snapshots`` en Mongo.
"""

from __future__ import annotations

import fnmatch
import posixpath
from collections.abc import Iterable

from railspec.contracts.comun import AlcanceRepositorio, NivelCodigo
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.snapshot import DeltaIndice, ModoDelta, Snapshot

from .almacen import AlmacenGrafo

_ORDEN_NIVEL = {NivelCodigo.restringido: 0, NivelCodigo.interno: 1, NivelCodigo.abierto: 2}


class SnapshotRechazado(ValueError):
    pass


def excluido(ruta: str, patrones: list[str]) -> bool:
    for patron in patrones:
        p = patron.rstrip("/")
        if (
            fnmatch.fnmatch(ruta, p)
            or ruta.startswith(p + "/")
            or fnmatch.fnmatch(posixpath.basename(ruta), p)
        ):
            return True
    return False


def filtrar_delta(delta: DeltaIndice, patrones: list[str]) -> DeltaIndice:
    if not patrones:
        return delta
    fuera = {s.id for s in delta.simbolos_upsert if excluido(s.ruta, patrones)}
    if not fuera:
        return delta
    return delta.model_copy(
        update={
            "simbolos_upsert": [s for s in delta.simbolos_upsert if s.id not in fuera],
            "aristas_agregadas": [
                a for a in delta.aristas_agregadas if a.origen not in fuera and a.destino not in fuera
            ],
            "embeddings": [e for e in delta.embeddings if e.simbolo not in fuera],
        }
    )


def cambiados(delta: DeltaIndice, previas: dict[str, str]) -> list[str]:
    """Ids que el delta cambia respecto de la superposición anterior (``firmas_superposicion``).

    El delta de un snapshot es siempre base..árbol de trabajo, así que lo que
    ya estaba igual en el snapshot anterior no es obra de este reporte.
    """

    nuevos = {s.id for s in delta.simbolos_upsert if previas.get(s.id) != s.sha256}
    borrados = {i for i in delta.simbolos_borrados if previas.get(i) != ""}
    return sorted(nuevos | borrados)


def ingerir_snapshot(
    snapshot: Snapshot,
    vinculo: VinculoRepositorio,
    grafo: AlmacenGrafo,
    criterios: Iterable[str] = (),
) -> bool:
    """Aplica el delta del snapshot a la superposición de su unidad.

    Con ``criterios`` (los ``CA-NN`` de las tareas que el reporte completa),
    enlaza cada uno con los símbolos que este snapshot cambia respecto del
    anterior de la misma unidad. Devuelve False si no había nada que aplicar
    (``solo-hashes``).
    """

    alcance = AlcanceRepositorio(
        org=snapshot.unidad.org, workspace=snapshot.unidad.workspace, repositorio=snapshot.repositorio
    )
    if alcance != vinculo.alcance:
        raise SnapshotRechazado(
            f"snapshot de {alcance.org}/{alcance.workspace}/{alcance.repositorio} con vínculo "
            f"{vinculo.alcance.org}/{vinculo.alcance.workspace}/{vinculo.alcance.repositorio}"
        )
    if _ORDEN_NIVEL[snapshot.nivel_codigo] > _ORDEN_NIVEL[vinculo.nivel_codigo]:
        raise SnapshotRechazado(
            f"snapshot {snapshot.nivel_codigo.value} sobre un vínculo {vinculo.nivel_codigo.value}"
        )
    if snapshot.modo_delta == ModoDelta.solo_hashes or snapshot.delta_indice is None:
        return False
    unidad = snapshot.unidad.unidad
    delta = filtrar_delta(snapshot.delta_indice, list(vinculo.exclusiones))
    criterios = sorted(set(criterios))
    tocados = cambiados(delta, grafo.firmas_superposicion(alcance, unidad)) if criterios else []
    grafo.aplicar_delta(alcance, snapshot.base_commit, delta, unidad)
    if tocados:
        grafo.enlazar_criterios(alcance, unidad, criterios, tocados)
    return True
