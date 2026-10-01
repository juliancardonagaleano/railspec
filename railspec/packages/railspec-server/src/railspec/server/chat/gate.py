"""Gate de salida determinístico del chat: siete reglas en código, sin modelo de por medio.

Toda respuesta del agente pasa por aquí antes de llegar a una persona, y la
exportación de un insumo pasa otra vez. La seguridad no depende de que el
modelo obedezca: una inyección de prompts puede cambiar lo que el modelo
escribe, pero no lo que este módulo deja salir.

1. ``esquema``: la salida valida contra ``RespuestaChat`` (que ya excluye
   bloques de código, HTML y URL).
2. ``huella-contexto``: ningún n-grama de N tokens normalizados compartido con
   un fragmento leído en la conversación (N por nivel, de la política).
3. ``normalizacion``: lo mismo tras desofuscar (espacios y separadores fuera,
   confusibles, invisibles, texto invertido, ROT13, base64, hex, URL y
   escapes); y lo decodificado tampoco puede tener forma de código ni secretos.
4. ``forma-codigo``: densidad de símbolos, reservadas y sentencias.
5. ``secretos``: los patrones del proxy, en todo texto de la salida.
6. ``alcance``: referencias a entidades fuera del workspace o de los
   repositorios visibles se eliminan (``recorta``), no bloquean.
7. ``presupuesto-fuga``: caracteres de literales e identificadores del código
   leído citados, acumulados por conversación y por persona y día.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from pydantic import ValidationError
from railspec.contracts.chat import (
    Afirmacion,
    EvaluacionRegla,
    ReglaGate,
    RespuestaChat,
    ResultadoRegla,
    VeredictoGateSalida,
)
from railspec.contracts.referencias import (
    RefArchivo,
    RefCriterio,
    RefDecision,
    RefNodoGrafo,
    RefSimbolo,
    RefUnidad,
)

from . import forma, secretos
from .normalizacion import (
    HuellasContexto,
    Parametros,
    aplastar,
    hash_identificador,
    identificadores,
    kgramas,
    ngramas,
    tokens,
    variantes,
)

VERSION_GATE = "railspec-chat-gate/1"
_MAX_HUELLAS_REPORTADAS = 20


@dataclass(frozen=True)
class PoliticaGate:
    """La política efectiva de la conversación (la más restrictiva de sus repositorios)."""

    n_tokens: int
    fragmentos_permitidos: bool
    tope_conversacion: int
    tope_usuario_dia: int

    @property
    def parametros(self) -> Parametros:
        return Parametros(self.n_tokens)


@dataclass(frozen=True)
class Visibilidad:
    org: str
    workspace: str
    repositorios: frozenset[str]


@dataclass(frozen=True)
class Consumo:
    conversacion: int
    usuario_dia: int


@dataclass(frozen=True)
class ResultadoGate:
    veredicto: VeredictoGateSalida
    respuesta: RespuestaChat | None
    cargo_fuga: int

    @property
    def permitido(self) -> bool:
        return self.veredicto.permitido


def _cadenas(valor: Any) -> Iterator[str]:
    if isinstance(valor, str):
        yield valor
    elif isinstance(valor, dict):
        for v in valor.values():
            yield from _cadenas(v)
    elif isinstance(valor, (list, tuple)):
        for v in valor:
            yield from _cadenas(v)


def _reportables(huellas: Iterable[str]) -> list[str]:
    return sorted(hashlib.sha256(h.encode()).hexdigest() for h in huellas)[:_MAX_HUELLAS_REPORTADAS]


def _ev(regla: ReglaGate, bloquea: bool, huellas: Iterable[str] = (), eliminadas: int = 0) -> EvaluacionRegla:
    if regla == ReglaGate.alcance:
        resultado = ResultadoRegla.recorta if eliminadas else ResultadoRegla.pasa
    else:
        resultado = ResultadoRegla.bloquea if bloquea else ResultadoRegla.pasa
    return EvaluacionRegla(
        regla=regla,
        resultado=resultado,
        huellas_coincidentes=_reportables(huellas),
        referencias_eliminadas=eliminadas,
    )


_LITERAL = re.compile(
    r"`([^`\n]{1,300})`|\"([^\"\n]{1,300})\"|'([^'\n]{2,300})'|«([^»\n]{1,300})»|“([^”\n]{1,300})”"
)


def cargo_fuga(texto: str, huellas: HuellasContexto) -> int:
    """Caracteres de literales citados más identificadores del código leído que aparecen fuera de ellos."""

    cargo = 0
    resto = texto
    for m in _LITERAL.finditer(texto):
        cargo += len(next(g for g in m.groups() if g is not None))
    resto = _LITERAL.sub(" ", texto)
    vistos: set[str] = set()
    for ident in identificadores(resto):
        h = hash_identificador(ident)
        if h in huellas.identificadores and h not in vistos:
            vistos.add(h)
            cargo += len(ident)
    return cargo


def _visible(ref: Any, v: Visibilidad) -> bool:
    if isinstance(ref, (RefSimbolo, RefArchivo, RefNodoGrafo)):
        return ref.repositorio in v.repositorios
    if isinstance(ref, (RefUnidad, RefCriterio, RefDecision)):
        return ref.workspace == v.workspace
    return True  # gobernanza: la consulta del agente ya se hizo con el alcance del workspace


def recortar(respuesta: RespuestaChat, v: Visibilidad) -> tuple[RespuestaChat, int]:
    eliminadas = 0
    afirmaciones = []
    for a in respuesta.afirmaciones:
        refs = [r for r in a.referencias if _visible(r, v)]
        eliminadas += len(a.referencias) - len(refs)
        afirmaciones.append(Afirmacion(texto=a.texto, referencias=refs) if eliminadas else a)
    if not eliminadas:
        return respuesta, 0
    return RespuestaChat(
        afirmaciones=afirmaciones, preguntas_abiertas=respuesta.preguntas_abiertas
    ), eliminadas


def evaluar(
    crudo: Any,
    *,
    huellas: HuellasContexto,
    politica: PoliticaGate,
    visibilidad: Visibilidad,
    consumo: Consumo,
    ahora: datetime,
    cobrar: bool = True,
) -> ResultadoGate:
    """Evalúa las siete reglas, siempre todas, y devuelve el veredicto con la respuesta recortada."""

    p = politica.parametros
    evaluaciones: list[EvaluacionRegla] = []

    # 1. Esquema.
    respuesta: RespuestaChat | None
    try:
        respuesta = crudo if isinstance(crudo, RespuestaChat) else RespuestaChat.model_validate(crudo)
    except (ValidationError, ValueError, TypeError):
        respuesta = None
    evaluaciones.append(_ev(ReglaGate.esquema, respuesta is None))

    if respuesta is not None:
        textos = [a.texto for a in respuesta.afirmaciones] + list(respuesta.preguntas_abiertas)
    else:
        textos = list(_cadenas(crudo))
    unido = "\n".join(textos)
    todas = "\n".join(_cadenas(crudo if respuesta is None else respuesta.model_dump(mode="json")))

    # 2. Huella contra el contexto (tokens normalizados).
    coinciden = set(ngramas(tokens(unido), p.n_tokens)) & huellas.tokens
    evaluaciones.append(_ev(ReglaGate.huella_contexto, bool(coinciden), coinciden))

    # 3. Normalización: desofuscar y volver a comparar; lo decodificado tampoco puede ser código ni secreto.
    desofuscadas = variantes(unido)
    coinciden_n: set[str] = set()
    for candidato in [unido, *desofuscadas]:
        coinciden_n |= kgramas(aplastar(candidato), p.k) & huellas.caracteres
    oculto = any(
        (not politica.fragmentos_permitidos and forma.es_codigo(v)) or secretos.detectar(v)
        for v in desofuscadas
    )
    evaluaciones.append(_ev(ReglaGate.normalizacion, bool(coinciden_n) or oculto, coinciden_n))

    # 4. Forma de código (en abierto se admiten fragmentos cortos: las reglas 2 y 7 los acotan).
    evaluaciones.append(
        _ev(
            ReglaGate.forma_codigo,
            not politica.fragmentos_permitidos and any(map(forma.es_codigo, [*textos, unido])),
        )
    )

    # 5. Secretos en cualquier cadena de la salida, referencias incluidas.
    evaluaciones.append(_ev(ReglaGate.secretos, bool(secretos.detectar(todas))))

    # 6. Alcance: recorta, nunca bloquea.
    eliminadas = 0
    if respuesta is not None:
        respuesta, eliminadas = recortar(respuesta, visibilidad)
    evaluaciones.append(_ev(ReglaGate.alcance, False, eliminadas=eliminadas))

    # 7. Presupuesto de fuga.
    cargo = cargo_fuga(unido, huellas) if cobrar else 0
    excede = (
        consumo.conversacion + cargo > politica.tope_conversacion
        or consumo.usuario_dia + cargo > politica.tope_usuario_dia
    )
    evaluaciones.append(_ev(ReglaGate.presupuesto_fuga, excede))

    permitido = all(e.resultado != ResultadoRegla.bloquea for e in evaluaciones)
    veredicto = VeredictoGateSalida(
        version_gate=VERSION_GATE, permitido=permitido, reglas=evaluaciones, evaluado_en=ahora
    )
    return ResultadoGate(veredicto, respuesta if permitido else None, cargo if permitido else 0)
