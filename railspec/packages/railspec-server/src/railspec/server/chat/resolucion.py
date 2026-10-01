"""Insumos del chat dentro de una unidad: ``unit.start`` los acepta y cada orden los lleva resueltos.

- ``existe``: ``unit.start`` rechaza (``no-encontrado``) un insumo que no es del workspace.
- ``resolver``: ``contexto.insumos`` de cada orden lleva objetivo, hallazgos,
  restricciones y cada referencia con su marca de obsolescencia frente al
  ``base_commit`` de la unidad. Una referencia de código es obsoleta si su
  archivo cambió entre el commit del insumo y el de la unidad (comparando el
  clon canónico); sin clon, si los commits difieren. Las referencias a
  repositorios que la unidad no toca, y las que no son de código, no se marcan.

El texto de cada referencia lo resuelve el arnés en local (``railspec insumo
pull``); aquí no viaja código.
"""

from __future__ import annotations

from typing import Any

from railspec.contracts.comun import AlcanceRepositorio, AlcanceWorkspace
from railspec.contracts.estado import EstadoUnidad
from railspec.contracts.insumo import Insumo, InsumoResuelto, ReferenciaResuelta
from railspec.contracts.referencias import RefArchivo, RefNodoGrafo, RefSimbolo

from .almacen import AlmacenChat
from .codigo import FuenteCodigo


class ResolutorInsumos:
    def __init__(self, chat: AlmacenChat, almacen: Any, fuente: FuenteCodigo | None = None) -> None:
        self.chat = chat
        self.almacen = almacen
        self.fuente = fuente

    def existe(self, alcance: AlcanceWorkspace, id_: Any) -> bool:
        return self.chat.obtener_insumo(alcance, id_) is not None

    def _obsoleta(self, ref: Any, bases: dict[str, str], alcance: AlcanceWorkspace) -> bool:
        if not isinstance(ref, (RefSimbolo, RefArchivo, RefNodoGrafo)):
            return False
        base = bases.get(ref.repositorio)
        if base is None or base == ref.commit:
            return False
        if isinstance(ref, RefNodoGrafo) or self.fuente is None:
            return True
        vinculo = self.almacen.vinculo(
            AlcanceRepositorio(org=alcance.org, workspace=alcance.workspace, repositorio=ref.repositorio)
        )
        if vinculo is None:
            return True
        antes = self.fuente.leer(vinculo, ref.commit, ref.ruta)
        return antes is None or antes != self.fuente.leer(vinculo, base, ref.ruta)

    def resolver(self, estado: EstadoUnidad) -> list[InsumoResuelto]:
        alcance = AlcanceWorkspace(org=estado.unidad.org, workspace=estado.unidad.workspace)
        bases = {r.repositorio: r.base_commit for r in estado.repositorios}
        resueltos = []
        for id_ in estado.insumos:
            insumo: Insumo | None = self.chat.obtener_insumo(alcance, id_)
            if insumo is None:  # borrado tras arrancar la unidad: la orden sigue sin él
                continue
            referencias = [
                ReferenciaResuelta(referencia=r, obsoleta=self._obsoleta(r, bases, alcance))
                for a in insumo.hallazgos
                for r in a.referencias
            ]
            resueltos.append(
                InsumoResuelto(
                    id=insumo.id,
                    sha256=insumo.sha256,
                    objetivo=insumo.objetivo,
                    hallazgos=insumo.hallazgos,
                    restricciones=insumo.restricciones,
                    referencias=referencias,
                )
            )
        return resueltos
