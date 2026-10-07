"""``GraphStore`` y ``VectorStore`` de Railspec sobre cualquier ``MotorGrafo``.

- El grafo canónico de un repositorio avanza por deltas del indexador
  (codebase-memory-mcp en local o en CI: los embeddings nunca se calculan en
  el servidor). Tras cada delta canónico se recalculan clusters y procesos.
- La superposición de una unidad es un grafo aparte, dentro del espacio del
  repositorio, que se reconstruye con cada snapshot: el delta del snapshot
  siempre es base..árbol de trabajo, no incremental. Sus borrados quedan
  como lápidas que ocultan el canónico.
- Una consulta con ``unidad`` ve canónico + superposición; sin ella, solo
  el canónico.
- Al integrar una unidad su superposición se retiene (marcada con el commit
  integrado y con el instante) hasta que ``graph.index`` lleva el canónico a ese
  commit; sin commit, o con un commit que el canónico ya cubrió, se descarta al
  momento. Una retenida que ningún índice cubre caduca a los
  ``RAILSPEC_GRAFO_RETENIDAS_DIAS`` (30; ``0`` = nunca), con aviso en ``graph.query``
  desde la mitad del plazo, y ``retenidas`` las lista.
- La comparación base contra snapshot (``impacto``) parte de lo que la
  superposición toca y recorre el canónico aguas arriba; el gate de código y
  el verbo ``impact`` de ``graph.query`` (1.4) la usan.
- Las trazas ``CA-NN`` enlazan cada criterio de una unidad con los símbolos
  que cambiaron al completar sus tareas; viven en un grafo aparte por
  repositorio que sobrevive a reindexados e integraciones (verbo ``trace``).
- Lo abandonado se barre con ``limpiar_huerfanos``: la superposición de una
  unidad sin actividad (ni snapshot nuevo) desde hace N días y la preparación
  de un índice sin lotes nuevos desde hace N horas; las retenidas (``integrado``)
  tienen su propio plazo, contado desde que se retuvieron.
- Cada consulta dice qué tan al día está el canónico de los repositorios que toca
  (``GraphQuerySalida.frescura`` y ``avisos``): si nunca se indexó, el commit, el instante
  del último índice aplicado (``Meta.actualizado`` del canónico) y si superó
  ``RAILSPEC_GRAFO_FRESCURA_HORAS`` (72; ``0`` = no avisa). Desde 1.10 también si el contenido
  del canónico coincide con el resumen que CI calculó sobre el repositorio
  (``contenido_verificado``, ``divergencias_total`` y ``rutas_divergentes``; lo calcula
  ``IndexadorCanonico`` al aplicar el índice).
- Grafo y vectores comparten grafo físico (el embedding es propiedad del
  nodo), así que borrar un repositorio por cualquiera de los dos borra ambos.
"""

from __future__ import annotations

import base64
import logging
import math
from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Protocol

from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.referencias import FrescuraGrafo, RefCriterio, RefNodoGrafo, RefSimbolo
from railspec.contracts.snapshot import DeltaIndice, Embedding, Relacion, TipoSimbolo
from railspec.contracts.tools import (
    ConsultaRelated,
    ConsultaResolve,
    ConsultaSearch,
    ConsultaTraverse,
    GraphQueryEntrada,
    GraphQuerySalida,
    ResultadoGrafo,
)

from .acceso import AccesoGrafo, Espacio
from .analitica import RELACIONES_DEPENDENCIA, calcular_clusters, calcular_procesos, nivel_riesgo
from .motor import AristaMotor, Cluster, Meta, Proceso, Traza
from .plazos import RETENIDAS_DIAS, VAR_RETENIDAS_DIAS, plazo

log = logging.getLogger(__name__)

#: Variable que fija tras cuántas horas sin índice nuevo un canónico se avisa como desactualizado
#: (``0`` = nunca avisa); se lee al construir ``AlmacenGrafo``, y una inválida impide arrancar.
VAR_FRESCURA_HORAS = "RAILSPEC_GRAFO_FRESCURA_HORAS"
FRESCURA_HORAS = 72.0


class CodificadorConsulta(Protocol):
    """Embebe el texto de una consulta (no código) con el mismo modelo del indexador local.

    Desde el contrato 1.1 el proxy manda el vector ya calculado
    (``ConsultaSearch.vector_b64``); el codificador solo se usa si falta.
    """

    def codificar(self, texto: str) -> str:
        """Devuelve el vector int8 en base64, como ``Embedding.vector_b64``."""


def decodificar(vector_b64: str) -> list[float]:
    crudo = base64.b64decode(vector_b64, validate=True)
    return [(b - 256 if b > 127 else b) / 127.0 for b in crudo]


def _props(s) -> dict:
    return {
        "id": s.id,
        "nombre": s.nombre,
        "tipo": s.tipo.value,
        "ruta": s.ruta,
        "linea_inicio": s.linea_inicio,
        "linea_fin": s.linea_fin,
        "sha256": s.sha256,
    }


def _arista(a) -> AristaMotor:
    return AristaMotor(a.origen, a.destino, a.relacion.value)


class Vista:
    """Canónico de un repositorio más, opcionalmente, la superposición de una unidad."""

    def __init__(self, canon: Espacio, sup: Espacio | None) -> None:
        self.canon = canon
        self.alcance = canon.alcance
        self.meta_canon = canon.meta()
        self.commit = self.meta_canon.commit
        self.sup = sup if sup is not None and sup.existe() else None
        meta = self.sup.meta() if self.sup else Meta()
        self.meta_sup = meta
        if self.sup and meta.commit:
            self.commit = meta.commit
        self.borrados = set(meta.borrados)
        self.aristas_borradas = {AristaMotor(*a) for a in meta.aristas_borradas}
        self._procesos: list[Proceso] | None = None

    def _canon_vivos(self, ids: list[str]) -> list[str]:
        if not self.sup:
            return ids
        propios = self.sup.simbolos(ids)
        return [i for i in ids if i not in self.borrados and i not in propios]

    def simbolos(self, ids: list[str]) -> dict[str, dict]:
        propios = self.sup.simbolos(ids) if self.sup else {}
        resto = [i for i in ids if i not in propios and i not in self.borrados]
        return {**self.canon.simbolos(resto), **propios}

    def buscar_nombre(self, texto: str, tipos: list[str], exacto: bool, limite: int) -> list[dict]:
        propios = self.sup.buscar_nombre(texto, tipos, exacto, limite) if self.sup else []
        canon = self.canon.buscar_nombre(texto, tipos, exacto, limite + len(self.borrados) + len(propios))
        vivos = set(self._canon_vivos([s["id"] for s in canon]))
        ids_propios = {s["id"] for s in propios}
        todos = propios + [s for s in canon if s["id"] in vivos and s["id"] not in ids_propios]
        return sorted(todos, key=lambda s: (s["nombre"], s["id"]))[:limite]

    def aristas(self, ids: list[str], relaciones: list[str], direccion: str) -> list[AristaMotor]:
        canon = [
            a
            for a in self.canon.aristas(ids, relaciones, direccion)
            if a not in self.aristas_borradas
            and a.origen not in self.borrados
            and a.destino not in self.borrados
        ]
        propias = self.sup.aristas(ids, relaciones, direccion) if self.sup else []
        return sorted(set(canon) | set(propias), key=lambda a: (a.origen, a.destino, a.relacion))

    def knn(self, vector: list[float], k: int) -> list[tuple[str, float]]:
        propios = self.sup.knn(vector, k) if self.sup else []
        canon = self.canon.knn(vector, k + len(self.borrados))
        vivos = set(self._canon_vivos([i for i, _ in canon]))
        todos = propios + [(i, p) for i, p in canon if i in vivos]
        return sorted(todos, key=lambda p: (-p[1], p[0]))[:k]

    def procesos(self) -> list[Proceso]:
        if self._procesos is None:
            self._procesos = self.canon.procesos()
        return self._procesos

    def analitica_de(self, simbolo: str) -> tuple[list[Cluster], list[Proceso]]:
        return self.canon.analitica_de(simbolo)

    def ref(self, s: dict) -> RefSimbolo:
        return RefSimbolo(
            repositorio=self.alcance.repositorio,
            commit=self.commit,
            simbolo=s["id"],
            nombre=s["nombre"],
            tipo_simbolo=TipoSimbolo(s["tipo"]),
            ruta=s["ruta"],
        )

    def ref_nodo(self, clase: str, id_: str, nombre: str) -> RefNodoGrafo:
        return RefNodoGrafo(
            repositorio=self.alcance.repositorio, commit=self.commit, clase=clase, id=id_, nombre=nombre[:300]
        )


@dataclass
class Impacto:
    """Comparación base contra snapshot de una unidad.

    ``tocados``: ids que la superposición cambia (upsert o lápida), resueltos
    en ``refs_tocados`` cuando se conocen (un borrado se resuelve contra el
    canónico). ``afectados``: aguas arriba, sin los tocados. El riesgo cuenta
    afectados y procesos tocados por unos u otros, así que un cambio sin nada
    aguas arriba puede ser de riesgo alto si toca muchos procesos.
    """

    tocados: list[str] = field(default_factory=list)
    refs_tocados: list[RefSimbolo] = field(default_factory=list)
    afectados: list[ResultadoGrafo] = field(default_factory=list)
    procesos: list[str] = field(default_factory=list)
    riesgo: str = "bajo"

    def resultados(self) -> list[ResultadoGrafo]:
        propios = [ResultadoGrafo(ref=r, distancia=0, riesgo=self.riesgo) for r in self.refs_tocados]
        return propios + self.afectados


@dataclass
class Huerfanos:
    """Lo que ``limpiar_huerfanos`` borró: unidades (superposiciones, y aparte las retenidas que
    ningún índice cubrió en su plazo) y commits (preparaciones)."""

    superposiciones: list[str] = field(default_factory=list)
    indexados: list[str] = field(default_factory=list)
    retenidas: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return bool(self.superposiciones or self.indexados or self.retenidas)


@dataclass(frozen=True)
class Retenida:
    """Una superposición retenida de ``AlmacenGrafo.retenidas``: la de ``unidad``, integrada en ``integrado``.

    ``desde`` es cuándo se retuvo (``None`` si se retuvo antes de que se guardara; el primer barrido
    lo sella) y ``vence`` cuándo caduca (``None`` si no hay plazo o falta ``desde``)."""

    unidad: str
    integrado: str
    desde: datetime | None
    vence: datetime | None


@dataclass(frozen=True)
class Alcanzado:
    distancia: int
    relacion: str


def recorrer(
    vistas: list[Vista], inicios: list[str], relaciones: list[str], direccion: str, profundidad: int
) -> dict[str, Alcanzado]:
    """BFS sobre la unión de las vistas. Los ids son globales, así que las
    aristas hacia stubs de otro repositorio visible se siguen solas; las que
    llevan a un repositorio no visible se cortan al resolver."""

    lado = "entrada" if direccion == "upstream" else "salida"
    alcanzados: dict[str, Alcanzado] = {}
    vistos = set(inicios)
    nivel = sorted(inicios)
    # Un nivel por vuelta y una consulta por vista y nivel (no una por símbolo): con un motor remoto el
    # viaje de red domina. El orden de descubrimiento es el mismo que si se recorriera nodo a nodo.
    for d in range(profundidad):
        if not nivel:
            break
        por_nodo: dict[str, list] = {i: [] for i in nivel}
        for v in vistas:
            for a in v.aristas(nivel, relaciones, lado):
                por_nodo[a.destino if lado == "entrada" else a.origen].append(a)
        siguiente: list[str] = []
        for actual in nivel:
            for a in por_nodo[actual]:
                sig = a.origen if lado == "entrada" else a.destino
                if sig not in vistos:
                    vistos.add(sig)
                    alcanzados[sig] = Alcanzado(d + 1, a.relacion)
                    siguiente.append(sig)
        nivel = siguiente
    return alcanzados


def resolver(vistas: list[Vista], ids: list[str]) -> dict[str, tuple[Vista, dict]]:
    hallados: dict[str, tuple[Vista, dict]] = {}
    pendientes = list(ids)
    for v in vistas:
        if not pendientes:
            break
        for i, s in v.simbolos(pendientes).items():
            hallados[i] = (v, s)
        pendientes = [i for i in pendientes if i not in hallados]
    return hallados


class AlmacenGrafo:
    """Implementa ``railspec.contracts.almacen.GraphStore``."""

    def __init__(
        self,
        acceso: AccesoGrafo,
        codificador: CodificadorConsulta | None = None,
        *,
        reloj: Callable[[], datetime] | None = None,
        frescura_horas: float | None = None,
        retenidas_dias: float | None = None,
    ) -> None:
        """``frescura_horas`` (``0`` = no avisa) sustituye a ``RAILSPEC_GRAFO_FRESCURA_HORAS``; sin él se
        lee aquí, al construir, y un valor inválido impide arrancar. Igual ``retenidas_dias``
        (``0`` = sin plazo, sin aviso) con ``RAILSPEC_GRAFO_RETENIDAS_DIAS``: aquí solo sirve para avisar
        y listar; el borrado lo hace ``limpiar_huerfanos`` con el plazo que le pasa ``IndexadorCanonico``."""

        self._acceso = acceso
        self._codificador = codificador
        self._reloj = reloj or (lambda: datetime.now(UTC))
        self._frescura = plazo(frescura_horas, VAR_FRESCURA_HORAS, FRESCURA_HORAS, "hours")
        self._retenidas = plazo(retenidas_dias, VAR_RETENIDAS_DIAS, RETENIDAS_DIAS, "days")

    def ahora(self) -> datetime:
        """El instante con que se sella la actividad de una superposición o de un índice (UTC)."""

        return self._reloj()

    # --- escritura ---------------------------------------------------------
    def aplicar_delta(
        self, alcance: AlcanceRepositorio, commit: str, delta: DeltaIndice, unidad: str | None
    ) -> None:
        if unidad is None:
            self._aplicar_canonico(alcance, commit, delta)
        else:
            self._reconstruir_superposicion(alcance, commit, delta, unidad)

    def _aplicar_canonico(self, alcance: AlcanceRepositorio, commit: str, delta: DeltaIndice) -> None:
        e = self._acceso.espacio(alcance)
        e.borrar_simbolos(list(delta.simbolos_borrados))
        e.borrar_aristas([_arista(a) for a in delta.aristas_borradas])
        e.upsert_simbolos([_props(s) for s in delta.simbolos_upsert])
        e.agregar_aristas([_arista(a) for a in delta.aristas_agregadas])
        e.fijar_embeddings({emb.simbolo: decodificar(emb.vector_b64) for emb in delta.embeddings})
        # Los commits cubiertos por índices anteriores siguen cubiertos; el contenido no se compara.
        e.fijar_meta(Meta(commit=commit, actualizado=self.ahora(), cubiertos=e.meta().cubiertos))
        self.recalcular_analitica(alcance)

    def _reconstruir_superposicion(
        self, alcance: AlcanceRepositorio, commit: str, delta: DeltaIndice, unidad: str
    ) -> None:
        e = self._acceso.espacio(alcance, unidad)
        e.borrar()
        e.upsert_simbolos([_props(s) for s in delta.simbolos_upsert])
        e.agregar_aristas([_arista(a) for a in delta.aristas_agregadas])
        e.fijar_embeddings({emb.simbolo: decodificar(emb.vector_b64) for emb in delta.embeddings})
        e.fijar_meta(
            Meta(
                commit=commit,
                unidad=unidad,
                borrados=list(delta.simbolos_borrados),
                aristas_borradas=[(a.origen, a.destino, a.relacion.value) for a in delta.aristas_borradas],
                actualizado=self.ahora(),
            )
        )

    def ingerir(self, snapshot, vinculo, criterios=()) -> bool:
        """``ingerir_snapshot`` sobre este almacén: política del vínculo y trazas ``CA-NN``."""

        from .ingesta import ingerir_snapshot

        return ingerir_snapshot(snapshot, vinculo, self, criterios)

    def recalcular_analitica(self, alcance: AlcanceRepositorio) -> None:
        e = self._acceso.espacio(alcance)
        simbolos, aristas = e.todos_simbolos(), e.todas_aristas()
        e.reemplazar_analitica(calcular_clusters(simbolos, aristas), calcular_procesos(simbolos, aristas))

    def descartar_superposicion(self, alcance: AlcanceRepositorio, unidad: str, hasta: str) -> None:
        """La unidad se integró en ``hasta``; el canónico la cubre cuando el indexador llegue a ese commit."""

        self._acceso.espacio(alcance, unidad).borrar()

    def retener_superposicion(self, alcance: AlcanceRepositorio, unidad: str, integrado: str) -> bool:
        """La unidad se integró en ``integrado``: su superposición sigue visible a las consultas
        con ``unidad`` hasta que el canónico alcance ese commit.

        Devuelve si queda retenida. Si el canónico ya está en ``integrado``, o ya lo cubrió un índice
        anterior (``Meta.cubiertos``), la superposición sobra y se borra al momento; si no tiene
        superposición no hay nada que retener. La retira ``retirar_superposiciones`` cuando
        ``IndexadorCanonico`` aplica un índice que lo cubre, o ``limpiar_huerfanos`` si ninguno lo
        cubre en ``RAILSPEC_GRAFO_RETENIDAS_DIAS``. Sella ``retenido_en``, de donde cuenta ese plazo."""

        sup = self._acceso.espacio(alcance, unidad)
        if not sup.existe():
            return False
        canon = self._acceso.espacio(alcance).meta()
        if canon.commit == integrado or integrado in canon.cubiertos:
            sup.borrar()
            return False
        meta = sup.meta()
        if meta.integrado != integrado or meta.retenido_en is None:
            meta.retenido_en = self.ahora()
        meta.integrado = integrado
        sup.fijar_meta(meta)
        return True

    def retenidas(self, alcance: AlcanceRepositorio) -> list[Retenida]:
        """Las superposiciones retenidas del repositorio (las de unidades ya integradas que ningún índice
        ha cubierto), por unidad: lo que hay que mirar si una quedó varada. Solo lee."""

        salida = []
        for unidad in self._acceso.superposiciones(alcance):
            meta = self._acceso.espacio(alcance, unidad).meta()
            if meta.integrado is None:
                continue
            desde = _utc(meta.retenido_en)
            vence = desde + self._retenidas if desde is not None and self._retenidas is not None else None
            salida.append(Retenida(unidad, meta.integrado, desde, vence))
        return salida

    def barrer_retenidas(self) -> list[tuple[AlcanceRepositorio, list[str]]]:
        """Retira las retenidas vencidas de todos los repositorios, sin esperar a que llegue un índice.

        ``limpiar_huerfanos`` solo corre al aplicar un ``graph.index``: un repositorio que deja de
        recibirlos conservaría sus retenidas para siempre. Esto recorre los repositorios que tienen
        superposiciones (``AccesoGrafo.repositorios_con_superposiciones``) y aplica el mismo plazo
        (``RAILSPEC_GRAFO_RETENIDAS_DIAS``, el de ``retenidas``); sin plazo (``0``) no hace nada. Solo
        toca retenidas, no las superposiciones en curso ni las preparaciones: esas siguen su propio
        plazo al llegar un índice. Un repositorio que falla no impide barrer los demás. Devuelve, por
        repositorio, las unidades retiradas (los que no retiraron nada no salen)."""

        if self._retenidas is None:
            return []
        salida = []
        for alcance in self._acceso.repositorios_con_superposiciones():
            try:
                borrado = self.limpiar_huerfanos(alcance, None, None, self._retenidas)
            except Exception:
                log.exception(
                    "no se pudieron barrer las retenidas de %s/%s/%s",
                    alcance.org,
                    alcance.workspace,
                    alcance.repositorio,
                )
                continue
            if borrado.retenidas:
                log.info(
                    "grafo de %s/%s/%s: retenidas que ningún índice cubrió retiradas por el barrido %s",
                    alcance.org,
                    alcance.workspace,
                    alcance.repositorio,
                    borrado.retenidas,
                )
                salida.append((alcance, borrado.retenidas))
        return salida

    def retirar_superposiciones(
        self,
        alcance: AlcanceRepositorio,
        aplicado: str,
        completo: bool,
        cubiertos: Collection[str] | None = None,
    ) -> list[str]:
        """El canónico acaba de avanzar a ``aplicado``: borra las retenidas que ese commit cubre.

        El servidor no tiene git, así que no puede ordenar un commit de otro; lo que cubre un
        índice lo declara CI. Con ``cubiertos`` (contrato 1.5: los commits de la rama que el índice
        incorpora) se retiran las integradas en ``aplicado`` o en alguno de ellos, y ``completo`` no
        amplía nada: una integrada en un commit posterior espera a su propio índice. Sin
        ``cubiertos`` (cliente 1.4) vale la regla de 1.4: las integradas en ``aplicado`` y, con un
        índice ``completo`` (CI perdió la cadena de deltas), todas las retenidas. Las superposiciones
        sin ``integrado`` son de unidades en curso y no se tocan. Devuelve las unidades retiradas."""

        cubre = None if cubiertos is None else {aplicado, *cubiertos}
        retiradas = []
        for unidad in self._acceso.superposiciones(alcance):
            sup = self._acceso.espacio(alcance, unidad)
            integrado = sup.meta().integrado
            if integrado is None:
                continue
            if (integrado in cubre) if cubre is not None else (completo or integrado == aplicado):
                sup.borrar()
                retiradas.append(unidad)
        return retiradas

    def limpiar_huerfanos(
        self,
        alcance: AlcanceRepositorio,
        superposicion: timedelta | None,
        indexado: timedelta | None,
        retenida: timedelta | None = None,
    ) -> Huerfanos:
        """Borra lo que nadie va a terminar: superposiciones y grafos de preparación abandonados.

        - Superposición de una unidad sin ``integrado`` y sin actividad desde hace ``superposicion``
          (cada snapshot de ``unit.report`` la reconstruye y renueva su sello). Una unidad en curso nunca
          cae por el plazo de las retenidas.
        - Superposición retenida (con ``integrado``) que lleva ``retenida`` sin que ningún índice cubra su
          commit, contado desde ``retenido_en``. Lo normal es que salga antes por
          ``retirar_superposiciones``; esto recoge las que quedaron varadas (el commit nunca llegó al
          canónico). Se reporta aparte, en ``Huerfanos.retenidas``.
        - Preparación de ``graph.index`` sin lotes nuevos desde hace ``indexado`` (reenviar un lote ya
          recibido no cuenta). Un índice que nunca completa no la borra por sí solo.

        ``None`` deja ese lado sin barrer. Un grafo sin sello (anterior a esta versión) se sella ahora y se
        juzga en el siguiente barrido, así que nada se borra por tiempo que no hayamos visto pasar. No
        toca el canónico ni las trazas ``CA-NN``. El barrido no toma lock: si una unidad vuelve a la vida
        justo mientras se borra su superposición, el próximo snapshot la reconstruye completa; si un lote
        llega tras ``indexado`` de silencio justo en ese instante, CI repite el índice."""

        ahora = self.ahora()
        borrado = Huerfanos()
        if superposicion is not None or retenida is not None:
            for unidad in self._acceso.superposiciones(alcance):
                e = self._acceso.espacio(alcance, unidad)
                meta = e.meta()
                if meta.integrado is None:
                    if superposicion is not None and self._vencido(e, meta, ahora, superposicion):
                        e.borrar()
                        borrado.superposiciones.append(unidad)
                elif retenida is not None and self._retencion_vencida(e, meta, ahora, retenida):
                    e.borrar()
                    borrado.retenidas.append(unidad)
        if indexado is not None:
            for commit in self._acceso.indexados(alcance):
                e = self._acceso.espacio_indexado(alcance, commit)
                if self._vencido(e, e.meta(), ahora, indexado):
                    e.borrar()
                    borrado.indexados.append(commit)
        return borrado

    @staticmethod
    def _vencido(e: Espacio, meta: Meta, ahora: datetime, plazo: timedelta) -> bool:
        if meta.actualizado is None:
            e.sellar(ahora)
            return False
        return ahora - meta.actualizado >= plazo

    @staticmethod
    def _retencion_vencida(e: Espacio, meta: Meta, ahora: datetime, plazo: timedelta) -> bool:
        desde = _utc(meta.retenido_en)
        if desde is None:
            # Retenida antes de que se guardara cuándo: el plazo cuenta desde este barrido. Reescribe la
            # meta (no hay un sellado atómico de este campo) solo si la superposición sigue ahí.
            if e.existe():
                meta.retenido_en = ahora
                e.fijar_meta(meta)
            return False
        return ahora - desde >= plazo

    def borrar_repositorio(self, alcance: AlcanceRepositorio) -> None:
        self._acceso.borrar_repositorio(alcance)

    # --- lectura -----------------------------------------------------------
    def vistas(self, consulta: GraphQueryEntrada, visibles: list[AlcanceRepositorio]) -> list[Vista]:
        return self._vistas(
            consulta, self._acceso.visibles(consulta.alcance, visibles, list(consulta.repositorios))
        )

    def _vistas(self, consulta: GraphQueryEntrada, repos: list[AlcanceRepositorio]) -> list[Vista]:
        vistas = []
        for r in repos:
            sup = self._acceso.espacio(r, consulta.unidad) if consulta.unidad else None
            v = Vista(self._acceso.espacio(r), sup)
            if v.commit is not None:
                vistas.append(v)
        return vistas

    def frescura(
        self, repos: list[AlcanceRepositorio], vistas: list[Vista]
    ) -> tuple[dict[str, FrescuraGrafo], list[str]]:
        """Frescura del canónico de cada repositorio consultado y los avisos que de ella se siguen.

        Un repositorio sin índice canónico no está en ``vistas`` (o está solo por la superposición de
        la unidad) y una consulta sobre él devuelve vacío: el aviso lo dice, para que un vacío no se
        lea como "no existe". ``desactualizado`` compara el instante del último índice con el plazo
        configurado; un índice sin instante (anterior a que se guardara) no se marca. Si el último índice
        trajo el resumen de CI y el contenido del canónico no coincide, lo dice (``contenido_verificado``,
        ``divergencias_total``, ``rutas_divergentes``) y avisa. Y si la superposición de la unidad consultada
        lleva retenida más de la mitad del plazo (``RAILSPEC_GRAFO_RETENIDAS_DIAS``), avisa de que la
        retirarán por tiempo."""

        por_repo = {v.alcance.repositorio: v for v in vistas}
        ahora = self.ahora()
        frescura: dict[str, FrescuraGrafo] = {}
        avisos: list[str] = []
        for r in repos:
            slug = r.repositorio
            v = por_repo.get(slug)
            meta = v.meta_canon if v else Meta()
            if meta.commit is None:
                frescura[slug] = FrescuraGrafo(indexado=False)
                avisos.append(
                    f"{slug}: sin índice canónico (nunca se indexó o CI no ha subido graph.index); "
                    + (
                        "solo se ve la superposición de la unidad."
                        if v
                        else "la consulta no devuelve nada de este repositorio, lo que no prueba que "
                        "el código no exista."
                    )
                )
                avisos.extend(self._aviso_retenida(slug, v, ahora))
                continue
            en = _utc(meta.actualizado)
            viejo = en is not None and self._frescura is not None and ahora - en > self._frescura
            frescura[slug] = FrescuraGrafo(
                indexado=True,
                commit=meta.commit,
                indexado_en=en,
                desactualizado=bool(viejo),
                contenido_verificado=meta.contenido_verificado,
                divergencias_total=meta.divergencias_total,
                rutas_divergentes=list(meta.rutas_divergentes),
            )
            if viejo:
                avisos.append(
                    f"{slug}: el índice canónico ({meta.commit[:12]}) se aplicó hace "
                    f"{_horas(ahora - en)}, más que el plazo de {_horas(self._frescura)} "
                    f"({VAR_FRESCURA_HORAS}): puede no reflejar el código actual."
                )
            if meta.contenido_verificado is False:
                avisos.append(_aviso_divergencia(slug, meta))
            avisos.extend(self._aviso_retenida(slug, v, ahora))
        return frescura, avisos

    def _aviso_retenida(self, slug: str, v: Vista | None, ahora: datetime) -> list[str]:
        """Aviso de la superposición retenida de la unidad consultada si lleva más de la mitad del plazo."""

        if v is None or v.sup is None or self._retenidas is None or v.meta_sup.integrado is None:
            return []
        desde = _utc(v.meta_sup.retenido_en)
        if desde is None or ahora - desde < self._retenidas / 2:
            return []
        resta = desde + self._retenidas - ahora
        cuando = (
            f"faltan {_dias(resta)} para retirarla"
            if resta > timedelta(0)
            else "ya superó el plazo y la retira el próximo barrido"
        )
        return [
            f"{slug}: la superposición de la unidad {v.sup.unidad} sigue retenida: se integró en "
            f"{v.meta_sup.integrado[:12]} y ningún índice canónico lo cubre; {cuando} "
            f"({VAR_RETENIDAS_DIAS}). Un índice que cubra ese commit la retira antes."
        ]

    def consultar(self, consulta: GraphQueryEntrada, visibles: list[AlcanceRepositorio]) -> GraphQuerySalida:
        repos = self._acceso.visibles(consulta.alcance, visibles, list(consulta.repositorios))
        vistas = self._vistas(consulta, repos)
        q = consulta.consulta
        if isinstance(q, ConsultaResolve):
            resultados = self._resolve(vistas, q, consulta.limite)
        elif isinstance(q, ConsultaSearch):
            resultados = self._search(vistas, q, consulta.limite)
        elif isinstance(q, ConsultaTraverse):
            resultados = self._traverse(vistas, q)
        elif isinstance(q, ConsultaRelated):
            resultados = self._related(vistas, q)
        elif q.verbo == "impact":  # 1.4; por verbo para no exigir el contrato nuevo al importar
            resultados = self._impacto(vistas, q.profundidad).resultados()
        elif q.verbo == "trace":
            resultados = self._trace(vistas, consulta.unidad, q.criterio, q.simbolo)
        else:  # pragma: no cover - la unión del contrato es cerrada
            raise TypeError(type(q))
        frescura, avisos = self.frescura(repos, vistas)
        return GraphQuerySalida(
            resultados=resultados[: consulta.limite],
            commits={v.alcance.repositorio: v.commit for v in vistas},
            truncado=len(resultados) > consulta.limite,
            frescura=frescura,
            avisos=avisos,
        )

    def _resolve(self, vistas: list[Vista], q: ConsultaResolve, limite: int) -> list[ResultadoGrafo]:
        return [
            ResultadoGrafo(ref=v.ref(s), puntuacion=1.0)
            for v in vistas
            for s in v.buscar_nombre(q.nombre, [], True, limite + 1)
        ]

    def _search(self, vistas: list[Vista], q: ConsultaSearch, limite: int) -> list[ResultadoGrafo]:
        tipos = [t.value for t in q.tipos]
        puntos: dict[str, tuple[float, Vista, dict]] = {}
        for v in vistas:
            for s in v.buscar_nombre(q.texto, tipos, False, limite + 1):
                puntos[s["id"]] = (len(q.texto) / max(len(s["nombre"]), len(q.texto)), v, s)
        vector_b64 = q.vector_b64
        if q.semantica and vector_b64 is None and self._codificador is not None:
            vector_b64 = self._codificador.codificar(q.texto)
        if q.semantica and vector_b64 is not None:
            vector = decodificar(vector_b64)
            for v in vistas:
                cercanos = v.knn(vector, limite + 1)
                hallados = v.simbolos([i for i, _ in cercanos])
                for i, p in cercanos:
                    s = hallados.get(i)
                    if s is None or (tipos and s["tipo"] not in tipos):
                        continue
                    if i not in puntos or puntos[i][0] < p:
                        puntos[i] = (p, v, s)
        orden = sorted(puntos.items(), key=lambda kv: (-kv[1][0], kv[1][2]["nombre"], kv[0]))
        return [ResultadoGrafo(ref=v.ref(s), puntuacion=round(p, 6)) for _, (p, v, s) in orden]

    def _traverse(self, vistas: list[Vista], q: ConsultaTraverse) -> list[ResultadoGrafo]:
        relaciones = [r.value for r in q.relaciones]
        alcanzados = recorrer(vistas, [q.simbolo], relaciones, q.direccion, q.profundidad)
        hallados = resolver(vistas, list(alcanzados))
        riesgo = None
        if q.direccion == "upstream":
            riesgo = nivel_riesgo(len(hallados), len(procesos_tocados(vistas, [q.simbolo, *hallados])))
        orden = sorted(
            hallados.items(), key=lambda kv: (alcanzados[kv[0]].distancia, kv[1][1]["nombre"], kv[0])
        )
        return [
            ResultadoGrafo(
                ref=v.ref(s),
                relacion=Relacion(alcanzados[i].relacion),
                distancia=alcanzados[i].distancia,
                riesgo=riesgo,
            )
            for i, (v, s) in orden
        ]

    def _related(self, vistas: list[Vista], q: ConsultaRelated) -> list[ResultadoGrafo]:
        propio = resolver(vistas, [q.simbolo])
        if not propio:
            return []
        v, _ = propio[q.simbolo]
        clusters, procesos = v.analitica_de(q.simbolo)
        resultados = [ResultadoGrafo(ref=v.ref_nodo("cluster", c.id, c.nombre)) for c in clusters]
        resultados += [ResultadoGrafo(ref=v.ref_nodo("proceso", p.id, p.nombre)) for p in procesos]
        vecinos: dict[str, str] = {}
        for vista in vistas:
            for direccion in ("entrada", "salida"):
                for a in vista.aristas([q.simbolo], [], direccion):
                    otro = a.origen if direccion == "entrada" else a.destino
                    vecinos.setdefault(otro, a.relacion)
        companeros = sorted({m for c in clusters for m in c.miembros} - set(vecinos) - {q.simbolo})
        hallados = resolver(vistas, sorted(vecinos) + companeros)
        for i in sorted(vecinos, key=lambda i: (hallados[i][1]["nombre"], i) if i in hallados else ("", i)):
            if i in hallados:
                vi, s = hallados[i]
                resultados.append(ResultadoGrafo(ref=vi.ref(s), relacion=Relacion(vecinos[i]), distancia=1))
        for i in companeros:
            if i in hallados:
                vi, s = hallados[i]
                resultados.append(ResultadoGrafo(ref=vi.ref(s)))
        return resultados

    # --- gate de código: comparación base contra snapshot ------------------
    def impacto(
        self, consulta: GraphQueryEntrada, visibles: list[AlcanceRepositorio], profundidad: int = 3
    ) -> Impacto:
        """Lo que la superposición de ``consulta.unidad`` toca y afecta aguas arriba.

        ``consulta.consulta`` se ignora; ``consulta.unidad`` es obligatoria.
        """

        if consulta.unidad is None:
            raise ValueError("el impacto necesita unidad")
        return self._impacto(self.vistas(consulta, visibles), profundidad)

    def impacto_superposicion(
        self, consulta: GraphQueryEntrada, visibles: list[AlcanceRepositorio], profundidad: int = 3
    ) -> tuple[list[str], list[ResultadoGrafo]]:
        """Forma anterior de ``impacto``: (ids tocados, afectados aguas arriba)."""

        i = self.impacto(consulta, visibles, profundidad)
        return i.tocados, i.afectados

    def _impacto(self, vistas: list[Vista], profundidad: int) -> Impacto:
        tocados: set[str] = set()
        refs: list[RefSimbolo] = []
        for v in vistas:
            if not v.sup:
                continue
            propios = v.sup.todos_simbolos()
            borrados = v.canon.simbolos(sorted(v.borrados))
            tocados |= {s["id"] for s in propios} | v.borrados
            refs += [v.ref(s) for s in propios] + [v.ref(s) for s in borrados.values()]
        if not tocados:
            return Impacto()
        alcanzados = recorrer(vistas, sorted(tocados), list(RELACIONES_DEPENDENCIA), "upstream", profundidad)
        hallados = resolver(vistas, [i for i in alcanzados if i not in tocados])
        procesos = procesos_tocados(vistas, [*tocados, *hallados])
        riesgo = nivel_riesgo(len(hallados), len(procesos))
        orden = sorted(
            hallados.items(), key=lambda kv: (alcanzados[kv[0]].distancia, kv[1][1]["nombre"], kv[0])
        )
        return Impacto(
            tocados=sorted(tocados),
            refs_tocados=sorted(refs, key=lambda r: (r.repositorio, r.nombre, r.simbolo)),
            afectados=[
                ResultadoGrafo(
                    ref=v.ref(s),
                    relacion=Relacion(alcanzados[i].relacion),
                    distancia=alcanzados[i].distancia,
                    riesgo=riesgo,
                )
                for i, (v, s) in orden
            ],
            procesos=sorted(procesos),
            riesgo=riesgo,
        )

    # --- trazabilidad CA-NN --------------------------------------------------
    def firmas_superposicion(self, alcance: AlcanceRepositorio, unidad: str) -> dict[str, str]:
        """id → sha256 de los símbolos de la superposición vigente (lápidas con sha vacío)."""

        e = self._acceso.espacio(alcance, unidad)
        if not e.existe():
            return {}
        firmas = {s["id"]: s["sha256"] for s in e.todos_simbolos()}
        return {**{i: "" for i in e.meta().borrados}, **firmas}

    def enlazar_criterios(
        self, alcance: AlcanceRepositorio, unidad: str, criterios: list[str], simbolos: list[str]
    ) -> None:
        """Enlaza cada criterio con cada símbolo. Acumula: un reporte posterior no borra los anteriores."""

        self._acceso.espacio(alcance, unidad)  # valida la unidad
        self._acceso.espacio_trazas(alcance).agregar_trazas(
            [Traza(unidad, c, s) for c in sorted(set(criterios)) for s in sorted(set(simbolos))]
        )

    def trazas(self, alcance: AlcanceRepositorio, unidad: str | None = None) -> list[Traza]:
        return self._acceso.espacio_trazas(alcance).trazas(unidad=unidad)

    def _trace(
        self, vistas: list[Vista], unidad: str | None, criterio: str | None, simbolo: str | None
    ) -> list[ResultadoGrafo]:
        if criterio is not None:
            ids = sorted(
                {
                    t.simbolo
                    for v in vistas
                    for t in self._acceso.espacio_trazas(v.alcance).trazas(unidad, criterio)
                }
            )
            hallados = resolver(vistas, ids)
            orden = sorted(hallados.items(), key=lambda kv: (kv[1][1]["ruta"], kv[1][1]["nombre"], kv[0]))
            return [ResultadoGrafo(ref=v.ref(s)) for _, (v, s) in orden]
        pares = sorted(
            {
                (t.unidad, t.criterio)
                for v in vistas
                for t in self._acceso.espacio_trazas(v.alcance).trazas(unidad, None, simbolo)
            }
        )
        workspace = vistas[0].alcance.workspace if vistas else None
        return [ResultadoGrafo(ref=RefCriterio(workspace=workspace, unidad=u, criterio=c)) for u, c in pares]


def _utc(instante: datetime | None) -> datetime | None:
    """El instante con zona (UTC si venía sin ella)."""

    if instante is not None and instante.tzinfo is None:
        return instante.replace(tzinfo=UTC)
    return instante


def _dias(delta: timedelta) -> str:
    dias = math.ceil(delta.total_seconds() / 86400)
    return "menos de un día" if dias <= 1 else f"{dias} días"


#: Cuántas rutas divergentes nombra el aviso (``rutas_divergentes`` lleva hasta 20).
_RUTAS_EN_AVISO = 5


def _aviso_divergencia(slug: str, meta: Meta) -> str:
    rutas = meta.rutas_divergentes[:_RUTAS_EN_AVISO]
    mas = meta.divergencias_total - len(rutas)
    nombradas = ", ".join(rutas) + (f" y {mas} más" if mas > 0 else "")
    return (
        f"{slug}: el contenido del índice canónico ({(meta.commit or '')[:12]}) no coincide con el que "
        f"calculó CI sobre el repositorio en {meta.divergencias_total} ruta(s): {nombradas}. Los símbolos "
        "de esas rutas pueden faltar, sobrar o estar desfasados; un índice `completo` "
        "(`railspec-reindexar`) lo corrige."
    )


def _horas(delta: timedelta) -> str:
    horas = delta.total_seconds() / 3600
    return f"{horas:.0f} h" if horas >= 10 else f"{horas:.1f} h"


def procesos_tocados(vistas: list[Vista], ids: list[str]) -> set[str]:
    buscados = set(ids)
    return {p.id for v in vistas for p in v.procesos() if buscados.intersection(p.pasos)}


class AlmacenVectores:
    """Implementa ``railspec.contracts.almacen.VectorStore`` sobre el grafo canónico."""

    def __init__(self, acceso: AccesoGrafo) -> None:
        self._acceso = acceso

    def upsert(self, alcance: AlcanceRepositorio, commit: str, embeddings: list[Embedding]) -> None:
        e = self._acceso.espacio(alcance)
        vigente = e.meta().commit
        if vigente is not None and vigente != commit:
            raise ValueError(f"embeddings de {commit} sobre un grafo en {vigente}")
        e.fijar_embeddings({emb.simbolo: decodificar(emb.vector_b64) for emb in embeddings})

    def buscar(self, alcance: AlcanceRepositorio, vector_b64: str, k: int) -> list[tuple[str, float]]:
        return self._acceso.espacio(alcance).knn(decodificar(vector_b64), k)

    def borrar_repositorio(self, alcance: AlcanceRepositorio) -> None:
        self._acceso.borrar_repositorio(alcance)
