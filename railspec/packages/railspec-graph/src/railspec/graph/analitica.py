"""Capa que codebase-memory-mcp no trae: clusters, procesos y riesgo de impacto.

Todo es determinista (mismo grafo, mismo resultado) porque el gate de código
compara resultados entre commits. Solo usa estructura: ids, nombres, rutas y
relaciones, así que vale igual en ``restringido``.
"""

from __future__ import annotations

import hashlib
import posixpath
from collections import Counter, deque

import networkx as nx
from networkx.algorithms.community import louvain_communities

from .motor import AristaMotor, Cluster, Proceso

#: Relaciones que cuentan como dependencia estructural (clusters e impacto).
RELACIONES_DEPENDENCIA = ("llama", "importa", "hereda", "implementa")
PROFUNDIDAD_PROCESO = 10
PASOS_MAX_PROCESO = 50
PASOS_MIN_PROCESO = 3


def _id(prefijo: str, *partes: str) -> str:
    return prefijo + hashlib.sha256("\0".join(partes).encode()).hexdigest()[:16]


def _nombre_cluster(rutas: list[str]) -> str:
    dirs = [posixpath.dirname(r) or "." for r in rutas]
    comun = posixpath.commonpath(dirs) if all(d != "." for d in dirs) else ""
    if comun:
        return comun
    return Counter(dirs).most_common(1)[0][0]


def calcular_clusters(simbolos: list[dict], aristas: list[AristaMotor]) -> list[Cluster]:
    """Louvain con semilla fija sobre el grafo no dirigido de dependencias."""

    ids = sorted(s["id"] for s in simbolos)
    presentes = set(ids)
    g = nx.Graph()
    g.add_nodes_from(ids)
    for a in aristas:
        if a.relacion in RELACIONES_DEPENDENCIA and a.origen in presentes and a.destino in presentes:
            if a.origen != a.destino:
                peso = g.get_edge_data(a.origen, a.destino, {"weight": 0})["weight"] + 1
                g.add_edge(a.origen, a.destino, weight=peso)
    if g.number_of_edges() == 0:
        return []
    rutas = {s["id"]: s["ruta"] for s in simbolos}
    comunidades = sorted(
        (sorted(c) for c in louvain_communities(g, weight="weight", seed=0) if len(c) >= 2),
        key=lambda c: (-len(c), c[0]),
    )
    clusters, vistos = [], Counter()
    for miembros in comunidades:
        base = _nombre_cluster([rutas[m] for m in miembros])
        vistos[base] += 1
        nombre = base if vistos[base] == 1 else f"{base} ({vistos[base]})"
        clusters.append(Cluster(id=_id("c-", *miembros), nombre=nombre, miembros=tuple(miembros)))
    return clusters


def calcular_procesos(simbolos: list[dict], aristas: list[AristaMotor]) -> list[Proceso]:
    """Un proceso por punto de entrada: función o método que llama y al que nadie llama.

    Las pruebas (origen de una arista ``prueba``) no son puntos de entrada.
    """

    por_id = {s["id"]: s for s in simbolos}
    llamadas: dict[str, list[str]] = {}
    llamados, pruebas = set(), set()
    for a in aristas:
        if a.relacion == "llama":
            llamadas.setdefault(a.origen, []).append(a.destino)
            llamados.add(a.destino)
        elif a.relacion == "prueba":
            pruebas.add(a.origen)
    entradas = sorted(
        i
        for i, s in por_id.items()
        if s["tipo"] in ("funcion", "metodo") and i in llamadas and i not in llamados and i not in pruebas
    )
    procesos = []
    for entrada in entradas:
        pasos, vistos, cola = [], {entrada}, deque([(entrada, 0)])
        while cola and len(pasos) < PASOS_MAX_PROCESO:
            actual, d = cola.popleft()
            if actual in por_id:
                pasos.append(actual)
            if d >= PROFUNDIDAD_PROCESO:
                continue
            for sig in sorted(llamadas.get(actual, [])):
                if sig not in vistos:
                    vistos.add(sig)
                    cola.append((sig, d + 1))
        if len(pasos) >= PASOS_MIN_PROCESO:
            procesos.append(
                Proceso(
                    id=_id("p-", entrada),
                    nombre=por_id[entrada]["nombre"],
                    entrada=entrada,
                    pasos=tuple(pasos),
                )
            )
    return procesos


def nivel_riesgo(afectados: int, procesos: int) -> str:
    """Riesgo de un cambio por símbolos afectados aguas arriba y procesos tocados."""

    if afectados >= 50 or procesos >= 10:
        return "critico"
    if afectados >= 20 or procesos >= 5:
        return "alto"
    if afectados >= 5 or procesos >= 2:
        return "medio"
    return "bajo"
