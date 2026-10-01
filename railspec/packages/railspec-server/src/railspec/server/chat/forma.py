"""Regla ``forma-codigo``: texto que parece código, aunque no coincida con nada leído.

Cubre lo que las huellas no ven: código que el modelo reescribe de memoria,
parafrasea con otros nombres o inventa. Es una heurística determinista sobre
tres señales: densidad de símbolos sintácticos, palabras reservadas y líneas
con forma de sentencia. Los umbrales se calibran con la suite de
``tests/test_chat_gate.py``: prosa técnica con nombres de símbolos y rutas
pasa; una o varias sentencias de código bloquean.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_SIMBOLOS = set("{}[]();=<>&|!^~*%$")
_OPERADORES = re.compile(r"->|=>|::|==|!=|<=|>=|:=|\+\+|--|&&|\|\||<<|>>|\+=|-=")
_RESERVADAS = frozenset(
    """
    def return class import lambda elif async await yield raise except finally nonlocal
    function const let var typeof instanceof extends implements interface enum
    public private protected static void final abstract override throws throw catch
    fn impl struct mut pub match func package defer chan goto
    println printf sprintf console null nil none self this new
    select insert update delete where from join
    """.split()
)
_SENTENCIAS = (
    re.compile(r"^\s*(?:def|class|function|fn|func|async\s+def|public|private|protected|static)\s+\w+"),
    re.compile(r"\b(?:return|yield|raise|throw)\s+[^\s.,;]"),
    re.compile(r"[\w\])]\s*(?:=|\+=|-=|:=)\s*[\w\"'(\[{-]"),
    re.compile(r"\w\s*\([^()]*\)\s*(?:[:{;]|=>|->)\s*(?:$|\w)"),
    re.compile(r";\s*$"),
    re.compile(r"[{}]\s*$|^\s*[{}]"),
    re.compile(r"^\s*(?:import|from|#include|using|require\()\s*[\w<\"'.]"),
    re.compile(r"\b(?:if|for|while|switch)\s*\(.*\)\s*\{?"),
    re.compile(r"\b(?:if|elif|for|while|with|try|else|except)\b[^\n]*:\s*$"),
    re.compile(r"\b(?:SELECT|INSERT|UPDATE|DELETE)\b.*\b(?:FROM|INTO|SET|WHERE)\b"),
)


@dataclass(frozen=True)
class Forma:
    densidad: float
    reservadas: int
    sentencias: int
    sangradas: int

    @property
    def es_codigo(self) -> bool:
        if self.sentencias >= 3:
            return True
        if self.sentencias >= 2 and (self.reservadas >= 2 or self.densidad >= 0.08):
            return True
        if self.densidad >= 0.15 and self.reservadas >= 2:
            return True
        return self.sangradas >= 3 and self.sentencias >= 1


def medir(texto: str) -> Forma:
    visibles = [c for c in texto if not c.isspace()]
    if not visibles:
        return Forma(0.0, 0, 0, 0)
    simbolos = sum(c in _SIMBOLOS for c in visibles) + len(_OPERADORES.findall(texto))
    palabras = re.findall(r"[A-Za-z_]+", texto)
    reservadas = len({p for p in palabras if p in _RESERVADAS})
    lineas = texto.splitlines() or [texto]
    sentencias = 0
    for linea in lineas:
        # Varias sentencias en una línea (``a = 1; b = 2``) cuentan por separado.
        for trozo in re.split(r";\s+(?=\S)", linea):
            sentencias += sum(1 for p in _SENTENCIAS if p.search(trozo))
    sangradas = sum(1 for linea in lineas if re.match(r"^(?: {2,}|\t)\S", linea))
    return Forma(round(simbolos / len(visibles), 4), reservadas, sentencias, sangradas)


def es_codigo(texto: str) -> bool:
    return medir(texto).es_codigo
