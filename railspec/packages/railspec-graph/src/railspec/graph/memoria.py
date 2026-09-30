"""Motor en memoria: doble de pruebas y referencia de la semántica de ``MotorGrafo``."""

from __future__ import annotations

import copy
import math
from dataclasses import dataclass, field

from .motor import AristaMotor, Cluster, Meta, Proceso


def _sufijo(nombre: str, texto: str) -> bool:
    return nombre == texto or any(nombre.endswith(sep + texto) for sep in (".", "::", "/", "#"))


@dataclass
class _Grafo:
    meta: Meta = field(default_factory=Meta)
    simbolos: dict[str, dict] = field(default_factory=dict)
    stubs: set[str] = field(default_factory=set)
    aristas: set[AristaMotor] = field(default_factory=set)
    vectores: dict[str, list[float]] = field(default_factory=dict)
    clusters: list[Cluster] = field(default_factory=list)
    procesos: list[Proceso] = field(default_factory=list)


class MotorMemoria:
    def __init__(self) -> None:
        self._grafos: dict[str, _Grafo] = {}

    def _g(self, grafo: str) -> _Grafo:
        return self._grafos.setdefault(grafo, _Grafo())

    # --- ciclo de vida -----------------------------------------------------
    def existe(self, grafo: str) -> bool:
        return grafo in self._grafos

    def borrar(self, grafo: str) -> None:
        self._grafos.pop(grafo, None)

    def listar(self, prefijo: str) -> list[str]:
        return sorted(n for n in self._grafos if n.startswith(prefijo))

    def leer_meta(self, grafo: str) -> Meta:
        g = self._grafos.get(grafo)
        return copy.deepcopy(g.meta) if g else Meta()

    def escribir_meta(self, grafo: str, meta: Meta) -> None:
        self._g(grafo).meta = copy.deepcopy(meta)

    # --- símbolos y aristas ------------------------------------------------
    def upsert_simbolos(self, grafo: str, simbolos: list[dict]) -> None:
        g = self._g(grafo)
        for s in simbolos:
            g.simbolos[s["id"]] = dict(s)
            g.stubs.discard(s["id"])

    def borrar_simbolos(self, grafo: str, ids: list[str]) -> None:
        g = self._g(grafo)
        quitar = set(ids)
        for i in quitar:
            g.simbolos.pop(i, None)
            g.stubs.discard(i)
            g.vectores.pop(i, None)
        g.aristas = {a for a in g.aristas if a.origen not in quitar and a.destino not in quitar}

    def agregar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        g = self._g(grafo)
        for a in aristas:
            for extremo in (a.origen, a.destino):
                if extremo not in g.simbolos:
                    g.stubs.add(extremo)
            g.aristas.add(a)

    def borrar_aristas(self, grafo: str, aristas: list[AristaMotor]) -> None:
        g = self._g(grafo)
        g.aristas -= set(aristas)

    def simbolos(self, grafo: str, ids: list[str]) -> dict[str, dict]:
        g = self._grafos.get(grafo)
        if not g:
            return {}
        return {i: dict(g.simbolos[i]) for i in ids if i in g.simbolos}

    def buscar_nombre(
        self, grafo: str, texto: str, tipos: list[str], exacto: bool, limite: int
    ) -> list[dict]:
        g = self._grafos.get(grafo)
        if not g:
            return []
        t = texto.lower()
        hallados = [
            dict(s)
            for s in g.simbolos.values()
            if (not tipos or s["tipo"] in tipos)
            and (_sufijo(s["nombre"], texto) if exacto else t in s["nombre"].lower())
        ]
        return sorted(hallados, key=lambda s: (s["nombre"], s["id"]))[:limite]

    def aristas(self, grafo: str, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        g = self._grafos.get(grafo)
        if not g:
            return []
        buscados = set(ids)
        lado = (lambda a: a.origen) if direccion == "salida" else (lambda a: a.destino)
        return sorted(
            (a for a in g.aristas if lado(a) in buscados and (not relaciones or a.relacion in relaciones)),
            key=lambda a: (a.origen, a.destino, a.relacion),
        )

    def todos_simbolos(self, grafo: str) -> list[dict]:
        g = self._grafos.get(grafo)
        return [dict(s) for s in g.simbolos.values()] if g else []

    def todas_aristas(self, grafo: str) -> list[AristaMotor]:
        g = self._grafos.get(grafo)
        return sorted(g.aristas, key=lambda a: (a.origen, a.destino, a.relacion)) if g else []

    # --- vectores ----------------------------------------------------------
    def fijar_embeddings(self, grafo: str, vectores: dict[str, list[float]]) -> None:
        g = self._g(grafo)
        for i, v in vectores.items():
            if i in g.simbolos:
                g.vectores[i] = list(v)

    def knn(self, grafo: str, vector: list[float], k: int) -> list[tuple[str, float]]:
        g = self._grafos.get(grafo)
        if not g:
            return []
        norma = math.sqrt(sum(x * x for x in vector)) or 1.0
        puntos = []
        for i, v in g.vectores.items():
            nv = math.sqrt(sum(x * x for x in v)) or 1.0
            puntos.append((i, sum(a * b for a, b in zip(vector, v, strict=True)) / (norma * nv)))
        return sorted(puntos, key=lambda p: (-p[1], p[0]))[:k]

    # --- analítica ---------------------------------------------------------
    def reemplazar_analitica(self, grafo: str, clusters: list[Cluster], procesos: list[Proceso]) -> None:
        g = self._g(grafo)
        g.clusters = list(clusters)
        g.procesos = list(procesos)

    def analitica_de(self, grafo: str, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        g = self._grafos.get(grafo)
        if not g:
            return [], []
        return (
            [c for c in g.clusters if simbolo in c.miembros],
            [p for p in g.procesos if simbolo in p.pasos],
        )

    def procesos(self, grafo: str) -> list[Proceso]:
        g = self._grafos.get(grafo)
        return list(g.procesos) if g else []
