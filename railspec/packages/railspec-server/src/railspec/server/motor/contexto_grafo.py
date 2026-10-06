"""Rebanada del grafo remoto para el contexto de spec, plan y tasks.

La orden de redactar o refinar lleva en ``ContextoArmado.grafo`` los símbolos del código existente
que se parecen a lo que pide la unidad, para que el arnés escriba el artefacto sobre el código real y
no a ciegas. Se arma con la misma ``GraphStore.consultar`` que sirve ``graph.query``, sobre los
repositorios de la unidad y con el canónico (la superposición de la unidad aún no existe antes de
implementar).

Reglas:

- La consulta sale del título, el pedido y los criterios de la unidad: nombres entre comillas
  invertidas (``resolve``, exacto) y palabras con contenido (``search``, subcadena del nombre). El grafo
  busca por nombre de símbolo, no por significado: un título en español rara vez coincide con
  identificadores en inglés, y por eso un resultado vacío se dice como "no sé", nunca como "no hay".
- Todo está acotado: pocos términos, pocos resultados por término y un tope de nodos y de caracteres.
  Solo viajan símbolo, nombre, tipo y ruta; el grafo no guarda texto de código.
- Nunca falla la orden. Sin grafo configurado (Render, hoy), con el canónico sin indexar o
  desactualizado, o con un grafo que no responde, el contexto sale sin rebanada y con
  ``grafo_avisos`` que lo cuentan; la frescura del canónico (``grafo_frescura``) sale tal como la
  devolvió el grafo.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass, field
from typing import Any

from railspec.contracts.comun import AlcanceRepositorio, AlcanceUnidad, AlcanceWorkspace
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.orden import NodoContexto
from railspec.contracts.referencias import FrescuraGrafo
from railspec.contracts.tools import ConsultaResolve, ConsultaSearch, GraphQueryEntrada

log = logging.getLogger("railspec.motor")

#: Consultas por orden (cada una es una ida al grafo) y resultados que se piden a cada una.
MAX_TERMINOS = 6
POR_TERMINO = 8
#: Tope de lo que entra en ``ContextoArmado.grafo``: nodos y caracteres de nombre + ruta + motivo.
MAX_NODOS = 20
MAX_CARACTERES = 6000

_ENTRE_COMILLAS = re.compile(r"`([A-Za-z_][\w.:/#$-]{2,120})`")
_PALABRA = re.compile(r"[^\W\d]\w{2,}", re.UNICODE)
#: Palabras que casi nunca son un identificador y solo harían ruido como subcadena.
_VACIAS = frozenset(
    """
    para como donde desde hasta sobre entre esta este esto estos estas cada todo todos toda todas
    puede pueden debe deben cuando tiene tienen hacer hacen unidad usuario usuarios sistema nuevo
    nueva nuevos nuevas debería deberia quiero necesito cualquier algún alguna algunos algunas
    mientras también tambien según segun pero porque sino sin con por los las del
    una uno unos unas que the and for with from that this when should must have into which there
    their then than also will shall can not are was were been being its our your criterio
    criterios spec plan tasks tareas tarea
    """.split()
)


@dataclass
class Rebanada:
    """Lo que el grafo aporta al contexto de una orden."""

    nodos: list[NodoContexto] = field(default_factory=list)
    frescura: dict[str, FrescuraGrafo] = field(default_factory=dict)
    avisos: list[str] = field(default_factory=list)


def terminos(*textos: str) -> list[tuple[str, bool]]:
    """Hasta ``MAX_TERMINOS`` consultas ``(término, exacto)`` de los textos, en orden de aparición.

    Primero los nombres entre comillas invertidas (``exacto``: se resuelven por nombre), luego las
    palabras con contenido de cada texto en el orden dado. Sin repetidos (sin distinguir mayúsculas)."""

    vistos: set[str] = set()
    salida: list[tuple[str, bool]] = []

    def agregar(termino: str, exacto: bool) -> None:
        clave = termino.lower()
        if clave not in vistos and len(salida) < MAX_TERMINOS:
            vistos.add(clave)
            salida.append((termino, exacto))

    for texto in textos:
        for nombre in _ENTRE_COMILLAS.findall(texto):
            agregar(nombre, True)
    for texto in textos:
        sin_comillas = _ENTRE_COMILLAS.sub(" ", texto)
        for palabra in _PALABRA.findall(sin_comillas):
            if palabra.lower() not in _VACIAS:
                agregar(palabra, False)
    return salida


def _frase(termino_s: list[str]) -> str:
    return ", ".join(f"«{t}»" for t in termino_s)


def _consultar(grafo: Any, entrada: GraphQueryEntrada, visibles: list[AlcanceRepositorio], pedidos):
    """Una consulta por término (síncrono: ``railspec-graph`` habla con el motor en el hilo que lo llama)."""

    return [
        (termino, grafo.consultar(entrada.model_copy(update={"consulta": consulta}), visibles))
        for termino, consulta in pedidos
    ]


async def rebanada(grafo: Any | None, alcance: AlcanceUnidad, estado: EstadoUnidad, *textos: str) -> Rebanada:
    """``Rebanada`` del grafo para la unidad; ``grafo`` es el ``GraphStore`` del motor (o ``None``)."""

    if grafo is None:
        return Rebanada(
            avisos=[
                "Este servidor no tiene grafo de código configurado: el contexto no incluye símbolos "
                "del repositorio. Busca en el clon local."
            ]
        )
    pedidos = [
        (t, ConsultaResolve(nombre=t) if exacto else ConsultaSearch(texto=t))
        for t, exacto in terminos(*textos)
    ]
    if not pedidos:
        return Rebanada(
            avisos=[
                "El título y el pedido de la unidad no traen nombres ni palabras con las que buscar en "
                "el grafo: el contexto no incluye símbolos del repositorio. Busca en el clon local."
            ]
        )
    visibles = [
        AlcanceRepositorio(org=alcance.org, workspace=alcance.workspace, repositorio=r.repositorio)
        for r in estado.repositorios
    ]
    roles = {r.repositorio: r.rol for r in estado.repositorios}
    entrada = GraphQueryEntrada(
        alcance=AlcanceWorkspace(org=alcance.org, workspace=alcance.workspace),
        repositorios=[r.repositorio for r in estado.repositorios],
        consulta=pedidos[0][1],
        limite=POR_TERMINO,
    )
    try:
        respuestas = await asyncio.to_thread(_consultar, grafo, entrada, visibles, pedidos)
    except Exception as exc:  # el grafo informa, no decide: un fallo suyo no frena la orden
        log.warning("grafo no disponible para el contexto de %s: %s", alcance.unidad, exc)
        return Rebanada(
            avisos=[
                "El grafo de código no respondió al armar el contexto: no incluye símbolos del "
                f"repositorio ({type(exc).__name__}). Busca en el clon local."
            ]
        )

    frescura: dict[str, FrescuraGrafo] = {}
    avisos: list[str] = []
    por_simbolo: dict[tuple[str, str], tuple[Any, float, list[str]]] = {}
    for termino, salida in respuestas:
        frescura.update(salida.frescura)
        avisos.extend(a for a in salida.avisos if a not in avisos)
        for r in salida.resultados:
            ref = r.ref
            if ref.tipo != "simbolo" or ref.repositorio not in roles:
                continue
            clave = (ref.repositorio, ref.simbolo)
            _, mejor, motivos = por_simbolo.get(clave, (ref, 0.0, []))
            por_simbolo[clave] = (
                ref,
                max(mejor, r.puntuacion or 0.0),
                motivos if termino in motivos else [*motivos, termino],
            )
    orden = sorted(
        por_simbolo.values(), key=lambda v: (-len(v[2]), -v[1], v[0].ruta, v[0].nombre, v[0].simbolo)
    )
    nodos: list[NodoContexto] = []
    caracteres = 0
    for ref, _, motivos in orden:
        motivo = f"coincide con {_frase(motivos)}"[:300]
        coste = len(ref.nombre) + len(ref.ruta) + len(motivo)
        if len(nodos) >= MAX_NODOS or caracteres + coste > MAX_CARACTERES:
            break
        caracteres += coste
        nodos.append(
            NodoContexto(
                repositorio=ref.repositorio,
                rol=roles[ref.repositorio],
                simbolo=ref.simbolo,
                nombre=ref.nombre,
                tipo=ref.tipo_simbolo,
                ruta=ref.ruta,
                motivo=motivo,
            )
        )
    if not nodos and all(f.indexado for f in frescura.values()):
        avisos.append(
            f"El grafo no encontró símbolos que coincidan con {_frase([t for t, _ in pedidos])}. "
            "La búsqueda es por nombre de símbolo, no por significado: no implica que no haya código "
            "relacionado. Busca en el clon local."
        )
    return Rebanada(nodos=nodos, frescura=frescura, avisos=[a[:1000] for a in avisos])
