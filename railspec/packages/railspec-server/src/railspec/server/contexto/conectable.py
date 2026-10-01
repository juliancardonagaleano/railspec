"""Proveedor de gobernanza del gate armado con las herramientas de contexto conectadas.

Configuración por rol (``ProveedorContexto`` en Mongo, por organización con
override por workspace, editable desde la consola). Un rol configurado en
Mongo sustituye entero al valor por defecto del entorno (``RAILSPEC_PCE_URL``
como gobernanza de toda organización sin configuración propia).

Reglas:

- ``gobernanza`` es estricta siempre (lo impone el contrato): sin proveedor
  resuelto, o con todas sus consultas fallidas, la consulta es ``no``; con
  alguna fallida, ``parcial``. El gate escala antes de gastar tokens.
- ``documentacion`` y ``memoria`` con política ``blanda`` suman lo que
  respondan y nunca bloquean; con ``estricta``, su fallo cuenta como el de
  gobernanza.
- ``grafo-de-codigo`` lo sirve el grafo central (railspec-graph); aquí se ignora.
- ``fases`` vacía = todas. El gate ``codigo`` cuenta como fase ``implement``.
- ``presupuesto_tokens`` recorta los ítems de ese proveedor (orden estable por
  id, ~4 caracteres por token) para que el prefijo del prompt tenga tamaño acotado.
- Lo que guarda una organización no es de confianza: al usar un proveedor de
  Mongo se revalida su URL contra la allowlist de la plataforma
  (``destinos.py``) y su ``credencial_ref`` contra el namespace de la
  organización que consulta (``secretos.py``); si no pasa, esas consultas
  cuentan como fallidas y ni la clave ni la consulta salen del servidor. La
  fuente por defecto del entorno (``RAILSPEC_PCE_URL``) la fija la plataforma.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

from railspec.contracts.comun import AlcanceUnidad, AlcanceWorkspace, Fase, GateFase, GobernanzaConsultada
from railspec.contracts.orden import ItemGobernanza
from railspec.contracts.repositorio import ProveedorContexto, RolContexto

from ..motor.gobernanza import ResultadoGobernanza
from .destinos import DestinoNoPermitido, PoliticaDestinos
from .pce import ClienteContexto, ClientePce
from .secretos import ResolutorSecretos, SecretoNoDisponible

log = logging.getLogger("railspec.contexto")

TIPOS_GOBERNANZA = ("adr", "policy", "principle")
FASE_DE_GATE = {
    GateFase.spec: Fase.spec,
    GateFase.plan: Fase.plan,
    GateFase.tasks: Fase.tasks,
    GateFase.codigo: Fase.implement,
}


@dataclass(frozen=True)
class FuenteContexto:
    rol: RolContexto
    nombre: str
    url: str
    politica_fallo: str = "estricta"
    fases: tuple[Fase, ...] = ()
    presupuesto_tokens: int | None = None
    api_key: str | None = field(default=None, repr=False)
    credencial_ref: str | None = None
    #: ``entorno``: la fija la plataforma (``RAILSPEC_PCE_*``), de confianza. ``configuracion``: la guardó una
    #: organización; su URL y su credencial se validan cada vez que se usa.
    origen: Literal["entorno", "configuracion"] = "entorno"

    @property
    def estricta(self) -> bool:
        return self.rol == RolContexto.gobernanza or self.politica_fallo == "estricta"

    def aplica(self, fase: GateFase) -> bool:
        return not self.fases or FASE_DE_GATE[fase] in self.fases


def fuente_de_config(p: ProveedorContexto) -> FuenteContexto:
    return FuenteContexto(
        rol=p.rol,
        nombre=p.nombre,
        url=p.url,
        politica_fallo=p.politica_fallo,
        fases=tuple(p.fases),
        presupuesto_tokens=p.presupuesto_tokens,
        credencial_ref=p.credencial_ref,
        origen="configuracion",
    )


def _tokens(item: ItemGobernanza) -> int:
    return (len(item.id) + len(item.tipo) + len(item.titulo) + len(item.resumen) + 16) // 4


def recortar(items: list[ItemGobernanza], presupuesto: int | None) -> list[ItemGobernanza]:
    if presupuesto is None:
        return items
    salida, usados = [], 0
    for item in items:
        coste = _tokens(item)
        if usados + coste > presupuesto:
            break
        salida.append(item)
        usados += coste
    return salida


FabricaCliente = Callable[[FuenteContexto, str | None], ClienteContexto]


class ContextoConectable:
    def __init__(
        self,
        almacen: Any | None,
        defecto: list[FuenteContexto],
        resolutor: ResolutorSecretos,
        *,
        cache_s: float = 900.0,
        fabrica: FabricaCliente | None = None,
        destinos: PoliticaDestinos | None = None,
    ) -> None:
        self._almacen = almacen
        self._defecto = list(defecto)
        self._resolutor = resolutor
        #: Sin política explícita rige la del entorno (``RAILSPEC_PROVEEDORES_HOSTS``): por defecto, ninguno.
        self._destinos = destinos if destinos is not None else PoliticaDestinos.desde_entorno()
        self._fabrica = fabrica or (
            lambda f, clave: ClientePce(
                f.url,
                clave,
                cache_s=cache_s,
                destinos=None if f.origen == "entorno" else self._destinos,
            )
        )
        self._clientes: dict[tuple[str, str, str], ClienteContexto] = {}

    def fuentes(self, ws: AlcanceWorkspace) -> list[FuenteContexto]:
        leer = getattr(self._almacen, "proveedores_contexto", None)
        # Solo los de la organización consultada (el almacén ya filtra; esto es defensa en profundidad).
        propias = [fuente_de_config(p) for p in leer(ws) if p.org == ws.org] if leer else []
        roles = {f.rol for f in propias}
        return propias + [f for f in self._defecto if f.rol not in roles]

    def _cliente(self, f: FuenteContexto, org: str) -> ClienteContexto:
        if f.origen == "configuracion":
            self._destinos.validar_url(f.url)  # DestinoNoPermitido: la URL la editó una organización
        clave = f.api_key
        if f.credencial_ref:
            clave = self._resolutor.resolver(f.credencial_ref, org)  # solo el namespace de ``org``
        huella = hashlib.sha256((clave or "").encode()).hexdigest()[:16]
        k = (f.origen, f.url, huella)
        if k not in self._clientes:
            self._clientes[k] = self._fabrica(f, clave)
        return self._clientes[k]

    async def consultar(self, alcance: AlcanceUnidad, fase: GateFase, objeto: str) -> ResultadoGobernanza:
        ws = AlcanceWorkspace(org=alcance.org, workspace=alcance.workspace)
        fuentes = [f for f in self.fuentes(ws) if f.rol != RolContexto.grafo_de_codigo and f.aplica(fase)]
        gobernanza = [f for f in fuentes if f.rol == RolContexto.gobernanza]
        if not gobernanza:
            return ResultadoGobernanza(
                GobernanzaConsultada.no, detalle="rol gobernanza sin proveedor resuelto"
            )
        resultados = await asyncio.gather(*(self._una(f, objeto, alcance.org) for f in fuentes))
        pedidas = fallidas = 0
        notas: list[str] = []
        por_rol: dict[RolContexto, list[ItemGobernanza]] = {}
        for f, (items, total, fallos, nota) in zip(fuentes, resultados, strict=True):
            if f.estricta:
                pedidas += total
                fallidas += fallos
            if nota:
                notas.append(f"{f.rol.value}/{f.nombre}: {nota}")
            unicos = sorted({i.id: i for i in items}.values(), key=lambda i: i.id)
            por_rol.setdefault(f.rol, []).extend(recortar(unicos, f.presupuesto_tokens))
        if fallidas == pedidas:
            consultada = GobernanzaConsultada.no
        elif fallidas:
            consultada = GobernanzaConsultada.parcial
        else:
            consultada = GobernanzaConsultada.si
        vistos: set[str] = set()
        items: list[ItemGobernanza] = []
        for rol in [RolContexto.gobernanza, *sorted(r for r in por_rol if r != RolContexto.gobernanza)]:
            for item in por_rol.get(rol, []):
                if item.id not in vistos:
                    vistos.add(item.id)
                    items.append(item)
        return ResultadoGobernanza(consultada, items, "; ".join(notas))

    async def _una(
        self, f: FuenteContexto, objeto: str, org: str
    ) -> tuple[list[ItemGobernanza], int, int, str]:
        tipos: tuple[str | None, ...] = TIPOS_GOBERNANZA if f.rol == RolContexto.gobernanza else (None,)
        consultas = [(t, objeto) for t in tipos]
        try:
            cliente = self._cliente(f, org)
        except (SecretoNoDisponible, DestinoNoPermitido) as exc:
            return [], len(consultas), len(consultas), str(exc)
        try:
            respuestas = await cliente.buscar(consultas)
        except Exception as exc:  # un cliente roto cuenta como consultas fallidas
            log.warning("contexto %s falló: %s", f.nombre, exc)
            return [], len(consultas), len(consultas), f"{type(exc).__name__}"
        fallos = sum(1 for r in respuestas if r is None)
        items = [i for r in respuestas if r for i in r]
        nota = f"{fallos}/{len(consultas)} consultas sin respuesta" if fallos else ""
        return items, len(consultas), fallos, nota
