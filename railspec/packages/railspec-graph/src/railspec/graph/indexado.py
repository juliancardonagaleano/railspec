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
  ``RAILSPEC_GRAFO_INDEXADO_HORAS`` (24); ``0`` lo desactiva. Las superposiciones
  retenidas que ningún índice cubrió caen a los ``RAILSPEC_GRAFO_RETENIDAS_DIAS`` (30)
  de retenerse. La respuesta de ``graph.index`` no cambia y un fallo del barrido solo se registra.
- El canónico recuerda los commits que cubren sus últimos índices (``Meta.cubiertos``: la unión del
  commit y de ``commits_cubiertos`` de cada uno; un índice completo reemplaza la lista), para que
  ``retener_superposicion`` retire al momento la de una unidad integrada en un commit que el canónico
  ya pasó.
- Con ``resumen`` (1.10, solo en el último lote) se reconcilia el contenido: tras aplicar el índice se
  compara, ruta por ruta, el resumen de CI con el del canónico, sin las rutas que el vínculo excluye en
  ninguno de los dos lados. El resultado queda en la meta del canónico y ``graph.query`` lo avisa. Una
  divergencia nunca hace fallar el índice: se aplica, se registra y se avisa. Sin resumen no se compara
  (``contenido_verificado`` queda en ``None``, sin arrastrar el del índice anterior).
"""

from __future__ import annotations

import logging
from datetime import datetime

from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.resumen import divergencias, resumir
from railspec.contracts.tools import (
    MAX_COMMITS_CUBIERTOS,
    GraphIndexEntrada,
    GraphIndexSalida,
    ResumenArchivo,
    ResumenIndice,
)

from .acceso import AccesoGrafo, Espacio
from .almacen import AlmacenGrafo, _arista, _props, decodificar
from .ingesta import excluido, filtrar_delta
from .motor import AristaMotor, Meta
from .plazos import RETENIDAS_DIAS, VAR_RETENIDAS_DIAS
from .plazos import plazo as _plazo

log = logging.getLogger(__name__)

_COMPLETO = "completo"
#: Variables que fijan cuándo caduca lo abandonado (``0`` = nunca); se leen al construir el indexador.
VAR_SUPERPOSICION_DIAS = "RAILSPEC_GRAFO_SUPERPOSICION_DIAS"
VAR_INDEXADO_HORAS = "RAILSPEC_GRAFO_INDEXADO_HORAS"
SUPERPOSICION_DIAS = 30.0
INDEXADO_HORAS = 24.0
#: Cuántas rutas divergentes guarda la meta del canónico (las primeras, en orden alfabético).
MAX_RUTAS_DIVERGENTES = 20


class IndiceRechazado(ValueError):
    pass


class IndiceDesfasado(IndiceRechazado):
    """``commit_anterior`` no es el commit del canónico: falta un push intermedio o llegó tarde."""


class IndexadorCanonico:
    """``superposicion_dias``, ``indexado_horas`` y ``retenidas_dias`` (``0`` = nunca caduca) sustituyen
    a las variables de entorno; sin ellos se leen aquí, al construir, y una variable inválida impide
    arrancar."""

    def __init__(
        self,
        acceso: AccesoGrafo,
        grafo: AlmacenGrafo,
        *,
        superposicion_dias: float | None = None,
        indexado_horas: float | None = None,
        retenidas_dias: float | None = None,
    ) -> None:
        self._acceso = acceso
        self._grafo = grafo
        self._superposicion = _plazo(superposicion_dias, VAR_SUPERPOSICION_DIAS, SUPERPOSICION_DIAS, "days")
        self._indexado = _plazo(indexado_horas, VAR_INDEXADO_HORAS, INDEXADO_HORAS, "hours")
        self._retenidas = _plazo(retenidas_dias, VAR_RETENIDAS_DIAS, RETENIDAS_DIAS, "days")

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
            escribir = True
        else:
            escribir = False
        if entrada.resumen is not None and estado.resumen is None:
            # El resumen viaja en el último lote, que no tiene por qué ser el que completa el commit
            # (los lotes pueden llegar desordenados): se guarda con la preparación hasta aplicarlo.
            estado.resumen = [(a.ruta, a.simbolos, a.huella) for a in entrada.resumen.archivos]
            escribir = True
        if escribir:
            estado.escribir(prep, entrada, self._grafo.ahora())

        if len(estado.recibidos) < entrada.lotes:
            if primero:
                self._limpiar(alcance)  # un repositorio cuyo índice nunca completa no llega al otro barrido
            return _salida(entrada, len(estado.recibidos), aplicado=False)

        self._aplicar(canon, prep, estado, entrada, vinculo)
        prep.borrar()
        self._limpiar(alcance)
        return _salida(entrada, entrada.lotes, aplicado=True)

    def _aplicar(
        self,
        canon: Espacio,
        prep: Espacio,
        estado: _Estado,
        entrada: GraphIndexEntrada,
        vinculo: VinculoRepositorio,
    ) -> None:
        completo = entrada.commit_anterior is None
        previos = [] if completo else canon.meta().cubiertos
        if completo:
            canon.borrar()
        else:
            canon.borrar_simbolos(sorted(estado.borrados))
            canon.borrar_aristas([AristaMotor(*a) for a in sorted(estado.aristas_borradas)])
        simbolos = prep.todos_simbolos()
        canon.upsert_simbolos(simbolos)
        canon.agregar_aristas(prep.todas_aristas())
        canon.fijar_embeddings(prep.leer_embeddings([s["id"] for s in simbolos]))
        verificado, difieren = self._verificar(canon, estado.resumen, vinculo, entrada)
        # ``actualizado`` es el instante del índice: ``graph.query`` lo devuelve como frescura.
        canon.fijar_meta(
            Meta(
                commit=entrada.commit,
                actualizado=self._grafo.ahora(),
                cubiertos=_cubiertos(entrada, previos, completo),
                contenido_verificado=verificado,
                divergencias_total=len(difieren),
                rutas_divergentes=difieren[:MAX_RUTAS_DIVERGENTES],
            )
        )
        self._grafo.recalcular_analitica(entrada.alcance)
        self._retirar(entrada, completo=completo)

    def _verificar(
        self,
        canon: Espacio,
        resumen: list[tuple[str, int, str]] | None,
        vinculo: VinculoRepositorio,
        entrada: GraphIndexEntrada,
    ) -> tuple[bool | None, list[str]]:
        """Compara el resumen de CI con lo que quedó en el canónico: ``(verificado, rutas que difieren)``.

        Sin resumen no se compara (``None``). Las rutas que el vínculo excluye se quitan de los dos lados:
        el servidor ya filtró el delta, así que el resumen de CI las trae y el canónico no. Nunca falla el
        índice: una divergencia se registra y se avisa; si la comparación misma falla, queda sin comparar."""

        if resumen is None:
            return None, []
        a = entrada.alcance
        try:
            patrones = list(vinculo.exclusiones)
            esperado = ResumenIndice(
                archivos=[
                    ResumenArchivo(ruta=r, simbolos=n, huella=h)
                    for r, n, h in resumen
                    if not excluido(r, patrones)
                ]
            )
            encontrado = resumir(
                (s["id"], s["ruta"], s["sha256"])
                for s in canon.todos_simbolos()
                if not excluido(s["ruta"], patrones)
            )
            difieren = divergencias(esperado, encontrado)
        except Exception:
            log.exception(
                "no se pudo comparar el contenido de %s/%s/%s con el resumen de CI",
                a.org,
                a.workspace,
                a.repositorio,
            )
            return None, []
        if difieren:
            log.warning(
                "canónico de %s/%s/%s en %s: el contenido difiere del resumen de CI en %d ruta(s), p. ej. %s",
                a.org,
                a.workspace,
                a.repositorio,
                entrada.commit,
                len(difieren),
                difieren[:5],
            )
        return not difieren, difieren

    def _limpiar(self, alcance: AlcanceRepositorio) -> None:
        """Barre lo abandonado del repositorio. Nunca falla el índice: ya se aplicó o quedó guardado."""

        if self._superposicion is None and self._indexado is None and self._retenidas is None:
            return
        try:
            borrado = self._grafo.limpiar_huerfanos(
                alcance, self._superposicion, self._indexado, self._retenidas
            )
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
                "grafo de %s/%s/%s: abandonado y borrado, superposiciones %s, retenidas que ningún índice "
                "cubrió %s, índices sin completar %s",
                alcance.org,
                alcance.workspace,
                alcance.repositorio,
                borrado.superposiciones,
                borrado.retenidas,
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


def _cubiertos(entrada: GraphIndexEntrada, previos: list[str], completo: bool) -> list[str]:
    """Commits que cubre el canónico tras este índice, los más recientes primero y a lo sumo
    ``MAX_COMMITS_CUBIERTOS``: el commit y los que declara CI, y los de índices anteriores salvo que este
    sea completo, que reemplaza la lista."""

    nuevos = list(dict.fromkeys([entrada.commit, *(entrada.commits_cubiertos or [])]))
    if completo:
        return nuevos[:MAX_COMMITS_CUBIERTOS]
    ya = set(nuevos)
    return (nuevos + [c for c in previos if c not in ya])[:MAX_COMMITS_CUBIERTOS]


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
        self.resumen = meta.resumen

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
                resumen=self.resumen,
            )
        )
