"""Constructores de deltas y consultas para las pruebas del grafo."""

from __future__ import annotations

import base64
import hashlib

from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace
from railspec.contracts.snapshot import (
    Arista,
    DeltaIndice,
    Embedding,
    MotorIndice,
    Relacion,
    Simbolo,
    TipoSimbolo,
)
from railspec.contracts.tools import GraphQueryEntrada

COMMIT_1 = "1" * 40
COMMIT_2 = "2" * 40
UNIDAD = "0001-emitir-pdf"
MOTOR = MotorIndice(version="0.11.0")


def sha(texto: str) -> str:
    return hashlib.sha256(texto.encode()).hexdigest()


def simbolo(repo: str, ruta: str, tipo: str, nombre: str, linea: int = 1) -> Simbolo:
    return Simbolo(
        id=sha(f"{repo}\0{ruta}\0{tipo}\0{nombre}"),
        nombre=nombre,
        tipo=TipoSimbolo(tipo),
        ruta=ruta,
        linea_inicio=linea,
        linea_fin=linea + 5,
        sha256=sha("texto de " + nombre),
    )


def arista(o: Simbolo | str, d: Simbolo | str, rel: str = "llama") -> Arista:
    return Arista(
        origen=o if isinstance(o, str) else o.id,
        destino=d if isinstance(d, str) else d.id,
        relacion=Relacion(rel),
    )


def vector(eje: int, ruido: int = 0) -> str:
    """Vector int8 de 768 casi unitario sobre ``eje``."""

    crudo = bytearray(768)
    crudo[eje] = 120
    if ruido:
        crudo[(eje + 1) % 768] = ruido
    return base64.b64encode(bytes(crudo)).decode()


def embedding(s: Simbolo, eje: int, ruido: int = 0) -> Embedding:
    return Embedding(simbolo=s.id, vector_b64=vector(eje, ruido))


def delta(simbolos=(), aristas=(), borrados=(), aristas_borradas=(), embeddings=()) -> DeltaIndice:
    return DeltaIndice(
        motor=MOTOR,
        simbolos_upsert=list(simbolos),
        simbolos_borrados=[b if isinstance(b, str) else b.id for b in borrados],
        aristas_agregadas=list(aristas),
        aristas_borradas=list(aristas_borradas),
        embeddings=list(embeddings),
    )


def alcances(org: str, workspace: str, *repos: str) -> list[AlcanceRepositorio]:
    return [AlcanceRepositorio(org=org, workspace=workspace, repositorio=r) for r in repos]


def consulta(org: str, workspace: str, cuerpo: dict, **extra) -> GraphQueryEntrada:
    return GraphQueryEntrada.model_validate(
        {"alcance": AlcanceWorkspace(org=org, workspace=workspace).model_dump(), "consulta": cuerpo, **extra}
    )


class Api:
    """Repositorio ``api`` de ejemplo: main → emitir → render → fuente, con una prueba."""

    def __init__(self, repo: str = "api") -> None:
        self.main = simbolo(repo, "src/main.py", "funcion", "app.main")
        self.emitir = simbolo(repo, "src/servicio.py", "funcion", "servicio.emitir")
        self.render = simbolo(repo, "src/pdf.py", "funcion", "pdf.render")
        self.fuente = simbolo(repo, "src/pdf.py", "funcion", "pdf.fuente")
        self.prueba = simbolo(repo, "tests/test_servicio.py", "funcion", "test_servicio.test_emitir")
        self.todos = [self.main, self.emitir, self.render, self.fuente, self.prueba]
        self.aristas = [
            arista(self.main, self.emitir),
            arista(self.emitir, self.render),
            arista(self.render, self.fuente),
            arista(self.prueba, self.emitir),
            arista(self.prueba, self.emitir, "prueba"),
        ]

    def delta(self, **extra) -> DeltaIndice:
        return delta(
            simbolos=self.todos + list(extra.pop("simbolos", [])),
            aristas=self.aristas + list(extra.pop("aristas", [])),
            embeddings=[embedding(s, i) for i, s in enumerate(self.todos)],
            **extra,
        )
