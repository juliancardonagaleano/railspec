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
- Lo abandonado se borra (``AlmacenGrafo.limpiar_huerfanos``) tras cada índice
  aplicado y al llegar el primer lote de un commit nuevo, así que un índice
  que nunca completa no deja su preparación para siempre: las superposiciones
  de unidades sin actividad desde hace ``RAILSPEC_GRAFO_SUPERPOSICION_DIAS``
  (30) y las preparaciones sin lotes nuevos desde hace
  ``RAILSPEC_GRAFO_INDEXADO_HORAS`` (24); ``0`` lo desactiva. La respuesta de
  ``graph.index`` no cambia y un fallo del barrido solo se registra.
"""

from __future__ import annotations

import logging
import math
import os
from datetime import datetime, timedelta

from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.tools import GraphIndexEntrada, GraphIndexSalida

from .acceso import AccesoGrafo, Espacio
from .almacen import AlmacenGrafo, _arista, _props, decodificar
from .ingesta import filtrar_delta
from .motor import AristaMotor, Meta

log = logging.getLogger(__name__)

_COMPLETO = "completo"
#: Variables que fijan cuándo caduca lo abandonado (``0`` = nunca); se leen al construir el indexador.
VAR_SUPERPOSICION_DIAS = "RAILSPEC_GRAFO_SUPERPOSICION_DIAS"
VAR_INDEXADO_HORAS = "RAILSPEC_GRAFO_INDEXADO_HORAS"
SUPERPOSICION_DIAS = 30.0
INDEXADO_HORAS = 24.0


class IndiceRechazado(ValueError):
    pass


class IndiceDesfasado(IndiceRechazado):
    """``commit_anterior`` no es el commit del canónico: falta un push intermedio o llegó tarde."""


def _plazo(valor: float | None, variable: str, defecto: float, unidad: str) -> timedelta | None:
    """``valor`` explícito, si no la variable de entorno, si no el defecto; ``0`` = sin caducidad."""

    if valor is None:
        crudo = (os.environ.get(variable) or "").strip()
        try:
            valor = float(crudo) if crudo else defecto
        except ValueError:
            raise ValueError(f"{variable} debe ser un número de {unidad}, no {crudo!r}") from None
    if not math.isfinite(valor) or valor < 0:
        raise ValueError(f"{variable} debe ser un número de {unidad} mayor o igual que 0, no {valor!r}")
    if valor == 0:
        return None
    try:
        return timedelta(**{unidad: valor})
    except OverflowError:
        raise ValueError(f"{variable} es demasiado grande: {valor!r} {unidad}") from None


class IndexadorCanonico:
    """``superposicion_dias`` e ``indexado_horas`` (``0`` = nunca caduca) sustituyen a las variables de
    entorno; sin ellos se leen aquí, al construir, y una variable inválida impide arrancar."""

    def __init__(
        self,
        acceso: AccesoGrafo,
        grafo: AlmacenGrafo,
        *,
        superposicion_dias: float | None = None,
        indexado_horas: float | None = None,
    ) -> None:
        self._acceso = acceso
        self._grafo = grafo
        self._superposicion = _plazo(superposicion_dias, VAR_SUPERPOSICION_DIAS, SUPERPOSICION_DIAS, "days")
        self._indexado = _plazo(indexado_horas, VAR_INDEXADO_HORAS, INDEXADO_HORAS, "hours")

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
            self._limpiar(alcance)
            return _salida(entrada, entrada.lotes, aplicado=True)
        if entrada.commit_anterior is not None and entrada.commit_anterior != vigente:
            raise IndiceDesfasado(
                f"el canónico está en {vigente}, el delta parte de {entrada.commit_anterior}"
            )

        prep = self._acceso.espacio_indexado(alcance, entrada.commit)
        estado = _Estado.leer(prep)
        estado.validar(entrada)
        primero = estado.lotes is None  # esta preparación acaba de nacer
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
            estado.escribir(prep, entrada, self._grafo.ahora())

        if len(estado.recibidos) < entrada.lotes:
            if primero:
                self._limpiar(alcance)  # un repositorio cuyo índice nunca completa no llega al otro barrido
            return _salida(entrada, len(estado.recibidos), aplicado=False)

        self._aplicar(canon, prep, estado, entrada)
        prep.borrar()
        self._limpiar(alcance)
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

    def _limpiar(self, alcance: AlcanceRepositorio) -> None:
        """Barre lo abandonado del repositorio. Nunca falla el índice: ya se aplicó o quedó guardado."""

        if self._superposicion is None and self._indexado is None:
            return
        try:
            borrado = self._grafo.limpiar_huerfanos(alcance, self._superposicion, self._indexado)
        except Exception:
            log.exception(
                "no se pudo limpiar lo abandonado de %s/%s/%s",
                alcance.org,
                alcance.workspace,
                alcance.repositorio,
            )
            return
        if borrado:
            log.info(
                "grafo de %s/%s/%s: abandonado y borrado, superposiciones %s, índices sin completar %s",
                alcance.org,
                alcance.workspace,
                alcance.repositorio,
                borrado.superposiciones,
                borrado.indexados,
            )

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

    def escribir(self, prep: Espacio, entrada: GraphIndexEntrada, ahora: datetime) -> None:
        prep.fijar_meta(
            Meta(
                commit=entrada.commit,
                borrados=sorted(self.borrados),
                aristas_borradas=sorted(self.aristas_borradas),
                base=entrada.commit_anterior or _COMPLETO,
                lotes=entrada.lotes,
                recibidos=sorted(self.recibidos),
                actualizado=ahora,
            )
        )
