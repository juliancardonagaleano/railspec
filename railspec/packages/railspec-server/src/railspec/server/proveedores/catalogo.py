"""Catálogo de modelos: leído por API de cada proveedor, cacheado y persistido por organización.

Cada proveedor aporta una ``FuenteCatalogo``. El catálogo se lee una vez por
``ttl_s`` para todo el servidor (el proveedor responde lo mismo a todas las
organizaciones) y se guarda en la colección ``catalogo`` de cada organización
que lo usa, con su fecha de lectura. Si una fuente falla, rige la última copia
leída o, tras un reinicio, la guardada en Mongo: el catálogo nunca se inventa.

Región de un despliegue de Foundry, que decide la política de zona de datos:

- SKU ``Global*``: ``global``. La inferencia puede correr en cualquier región
  de Azure; nunca sirve a ``restringido`` ni ``interno``.
- SKU ``DataZone*``: ``zona-<zona>`` con la zona del recurso (``us``, ``eu``).
- El resto (``Standard``, ``Provisioned*``): la región del recurso.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol

from railspec.contracts.comun import Effort, Proveedor
from railspec.contracts.repositorio import Capacidades, ModeloCatalogo

log = logging.getLogger("railspec.catalogo")

REGION_GLOBAL = "global"
_TODOS = [Effort.low, Effort.medium, Effort.high, Effort.xhigh, Effort.max]


@dataclass(frozen=True)
class EntradaCatalogo:
    proveedor: Proveedor
    modelo: str
    despliegue: str | None
    hosting: str  # "azure" | "anthropic"
    region: str | None
    capacidades: Capacidades

    def a_contrato(self, org: str, leido_en: datetime) -> ModeloCatalogo:
        return ModeloCatalogo(
            org=org,
            proveedor=self.proveedor,
            modelo=self.modelo,
            despliegue=self.despliegue,
            hosting=self.hosting,  # type: ignore[arg-type]
            region=self.region,
            capacidades=self.capacidades,
            leido_en=leido_en,
        )

    @classmethod
    def de_contrato(cls, m: ModeloCatalogo) -> EntradaCatalogo:
        return cls(m.proveedor, m.modelo, m.despliegue, m.hosting, m.region, m.capacidades)

    @property
    def destino(self) -> str:
        return self.despliegue or self.modelo

    def zona(self, zona_recurso: str | None) -> str | None:
        if self.region is None or self.region == REGION_GLOBAL:
            return None
        if self.region.startswith("zona-"):
            return self.region.removeprefix("zona-")
        return zona_recurso

    def en_zona(self, zona_workspace: str | None, zona_recurso: str | None) -> bool:
        """¿Sirve a ``restringido``/``interno``? Azure, nunca ``global`` y dentro de la zona del workspace."""

        if self.hosting != "azure" or self.region is None or self.region == REGION_GLOBAL:
            return False
        if not zona_workspace:
            return True
        return zona_workspace.lower() in {self.region.lower(), (self.zona(zona_recurso) or "").lower()}


def capacidades_conocidas(modelo: str) -> Capacidades:
    """Capacidades de modelos conocidos cuando la API del proveedor no las da (Foundry).

    Conservador a propósito: un modelo desconocido queda sin salida estructurada
    y no satisface ningún rol del gate hasta que se declare (``RAILSPEC_FOUNDRY_DESPLIEGUES``).
    """

    m = modelo.lower()
    if m.startswith("claude-haiku-4-5"):
        return Capacidades(efforts=[], thinking=True, structured_outputs=True, contexto_max_tokens=200_000)
    if m.startswith(("claude-opus-4-6", "claude-sonnet-4-6")):
        efforts = [Effort.low, Effort.medium, Effort.high, Effort.max]
        return Capacidades(
            efforts=efforts, thinking=True, structured_outputs=True, contexto_max_tokens=1_000_000
        )
    if m.startswith(("claude-opus-", "claude-sonnet-5", "claude-fable-", "claude-mythos-")):
        return Capacidades(
            efforts=_TODOS, thinking=True, structured_outputs=True, contexto_max_tokens=1_000_000
        )
    if m.startswith(("gpt-5", "o3", "o4")):
        efforts = [Effort.low, Effort.medium, Effort.high]
        return Capacidades(
            efforts=efforts, thinking=True, structured_outputs=True, contexto_max_tokens=200_000
        )
    if m.startswith(("gpt-4.1", "gpt-4o")):
        return Capacidades(efforts=[], structured_outputs=True, contexto_max_tokens=128_000)
    return Capacidades(efforts=[], structured_outputs=False, contexto_max_tokens=8_192)


def region_de_sku(sku: str | None, region_recurso: str | None, zona_recurso: str | None) -> str | None:
    s = (sku or "standard").lower()
    if s.startswith("global"):
        return REGION_GLOBAL
    if s.startswith("datazone"):
        return f"zona-{zona_recurso}" if zona_recurso else None
    return region_recurso


class FuenteCatalogo(Protocol):
    proveedor: Proveedor

    async def leer(self) -> list[EntradaCatalogo]: ...


# --- Foundry --------------------------------------------------------------------------------


def _capacidades_declaradas(base: Capacidades, d: dict[str, Any]) -> Capacidades:
    cambios: dict[str, Any] = {}
    if "efforts" in d:
        cambios["efforts"] = [Effort(e) for e in d["efforts"]]
    if "structured_outputs" in d:
        cambios["structured_outputs"] = bool(d["structured_outputs"])
    if "contexto" in d:
        cambios["contexto_max_tokens"] = int(d["contexto"])
    return base.model_copy(update=cambios)


@dataclass
class FuenteFoundryDeclarada:
    """Despliegues declarados en ``RAILSPEC_FOUNDRY_DESPLIEGUES``.

    Dos formas: ``despliegue=modelo[:SKU]`` separados por coma, o una lista JSON
    de objetos ``{"despliegue", "modelo", "sku", "efforts", "structured_outputs",
    "contexto"}`` cuando hace falta declarar capacidades.
    """

    crudo: str
    region: str | None
    zona: str | None
    proveedor: Proveedor = Proveedor.foundry

    def entradas(self) -> list[EntradaCatalogo]:
        texto = self.crudo.strip()
        filas: list[dict[str, Any]] = []
        if texto.startswith("["):
            filas = json.loads(texto)
        else:
            for par in filter(None, (p.strip() for p in texto.split(","))):
                despliegue, _, resto = par.partition("=")
                modelo, _, sku = resto.partition(":")
                if not despliegue or not modelo:
                    raise ValueError("RAILSPEC_FOUNDRY_DESPLIEGUES espera despliegue=modelo[:SKU]")
                filas.append({"despliegue": despliegue.strip(), "modelo": modelo.strip(), "sku": sku.strip()})
        return [
            EntradaCatalogo(
                Proveedor.foundry,
                f["modelo"],
                f["despliegue"],
                "azure",
                region_de_sku(f.get("sku") or None, self.region, self.zona),
                _capacidades_declaradas(capacidades_conocidas(f["modelo"]), f),
            )
            for f in filas
        ]

    async def leer(self) -> list[EntradaCatalogo]:
        return self.entradas()


@dataclass
class FuenteFoundryProyecto:
    """Despliegues del proyecto de Foundry: ``GET {proyecto}/deployments?api-version=...``.

    La forma de la respuesta (``value[].name``, ``modelName``, ``modelPublisher``,
    ``sku.name``, ``nextLink``) sigue la API de proyectos de Foundry; no está
    verificada contra un recurso real, así que el parseo descarta lo que no
    reconoce. Con API key manda ``api-key``; sin ella, un token de Entra ID.
    """

    proyecto: str
    api_key: str | None
    region: str | None
    zona: str | None
    api_version: str = "v1"
    proveedor_token: Callable[[], Awaitable[str]] | None = None
    transporte: Any | None = None
    proveedor: Proveedor = Proveedor.foundry

    async def leer(self) -> list[EntradaCatalogo]:
        import httpx

        cabeceras = {"api-key": self.api_key} if self.api_key else {}
        if not self.api_key:
            if self.proveedor_token is None:
                raise ValueError("catálogo de Foundry sin API key ni token de Entra ID")
            cabeceras["Authorization"] = f"Bearer {await self.proveedor_token()}"
        url: str | None = f"{self.proyecto.rstrip('/')}/deployments"
        params: dict[str, str] | None = {"api-version": self.api_version}
        entradas: list[EntradaCatalogo] = []
        async with httpx.AsyncClient(transport=self.transporte, timeout=30.0) as http:
            paginas = 0
            while url and paginas < 50:
                r = await http.get(url, params=params, headers=cabeceras)
                r.raise_for_status()
                datos = r.json()
                for d in datos.get("value", []) if isinstance(datos, dict) else []:
                    e = self._entrada(d)
                    if e is not None:
                        entradas.append(e)
                url, params, paginas = datos.get("nextLink"), None, paginas + 1
        return entradas

    def _entrada(self, d: Any) -> EntradaCatalogo | None:
        if not isinstance(d, dict) or not d.get("name") or not d.get("modelName"):
            return None
        tipo = str(d.get("type") or "ModelDeployment")
        if tipo != "ModelDeployment":
            return None
        sku = (d.get("sku") or {}).get("name") if isinstance(d.get("sku"), dict) else None
        modelo = str(d["modelName"])
        return EntradaCatalogo(
            Proveedor.foundry,
            modelo,
            str(d["name"]),
            "azure",
            region_de_sku(sku, self.region, self.zona),
            capacidades_conocidas(modelo),
        )


# --- Anthropic ------------------------------------------------------------------------------


@dataclass
class FuenteAnthropic:
    """``GET /v1/models`` con el SDK (pagina solo); capacidades del objeto ``capabilities``."""

    cliente: Any
    proveedor: Proveedor = Proveedor.anthropic

    async def leer(self) -> list[EntradaCatalogo]:
        entradas = []
        async for m in self.cliente.models.list():
            caps = getattr(m, "capabilities", None) or {}
            efforts = [e for e in _TODOS if _soportado(caps, "effort", e.value)]
            entradas.append(
                EntradaCatalogo(
                    Proveedor.anthropic,
                    m.id,
                    None,
                    "anthropic",
                    None,
                    Capacidades(
                        efforts=efforts,
                        thinking=_soportado(caps, "thinking"),
                        structured_outputs=_soportado(caps, "structured_outputs"),
                        contexto_max_tokens=int(getattr(m, "max_input_tokens", 0) or 1),
                    ),
                )
            )
        return entradas


def _soportado(caps: Any, *ruta: str) -> bool:
    nodo = caps
    for clave in ruta:
        if not isinstance(nodo, dict) or clave not in nodo:
            return False
        nodo = nodo[clave]
    return isinstance(nodo, dict) and bool(nodo.get("supported"))


# --- Catálogo -------------------------------------------------------------------------------


@dataclass
class _Lectura:
    entradas: list[EntradaCatalogo] = field(default_factory=list)
    leido_en: datetime | None = None
    error: str | None = None
    #: Monotónico de la última lectura (válida o fallida): el TTL vale para ambas.
    marca: float = float("-inf")


class Catalogo:
    def __init__(
        self,
        fuentes: Iterable[FuenteCatalogo],
        almacen: Any | None = None,
        *,
        ttl_s: float = 3600.0,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        monotono: Callable[[], float] = time.monotonic,
    ) -> None:
        self._fuentes = {f.proveedor: f for f in fuentes}
        self._almacen = almacen
        self._ttl = ttl_s
        self._reloj = reloj
        self._monotono = monotono
        self._lecturas: dict[Proveedor, _Lectura] = {}
        #: (org, proveedor) -> fecha de la lectura ya persistida en esa organización.
        self._persistido: dict[tuple[str, Proveedor], datetime | None] = {}
        self._cerrojo = asyncio.Lock()

    @property
    def proveedores(self) -> set[Proveedor]:
        return set(self._fuentes)

    def cubre(self, proveedor: Proveedor) -> bool:
        return proveedor in self._fuentes

    async def refrescar(self, org: str | None = None, *, forzar: bool = False) -> None:
        async with self._cerrojo:
            for proveedor, fuente in self._fuentes.items():
                lectura = self._lecturas.get(proveedor)
                if forzar or lectura is None or self._monotono() - lectura.marca >= self._ttl:
                    self._lecturas[proveedor] = await self._leer(fuente, lectura, org)
                if org is not None:
                    self._persistir(org, proveedor)

    async def _leer(self, fuente: FuenteCatalogo, previa: _Lectura | None, org: str | None) -> _Lectura:
        try:
            entradas = sorted(await fuente.leer(), key=lambda e: (e.modelo, e.destino))
            return _Lectura(entradas, self._reloj(), None, self._monotono())
        except Exception as exc:  # red, auth, forma inesperada
            error = f"{type(exc).__name__}: {exc}"
            log.warning("catálogo de %s no se pudo leer: %s", fuente.proveedor.value, error)
            if previa is not None and previa.leido_en is not None:
                return _Lectura(previa.entradas, previa.leido_en, error, self._monotono())
            guardadas = self._guardadas(fuente.proveedor, org)
            if guardadas:
                return _Lectura(guardadas, None, error, self._monotono())
            return _Lectura([], None, error, self._monotono())

    def _guardadas(self, proveedor: Proveedor, org: str | None) -> list[EntradaCatalogo]:
        if self._almacen is None or org is None:
            return []
        return [
            EntradaCatalogo.de_contrato(m) for m in self._almacen.catalogo(org) if m.proveedor == proveedor
        ]

    def _persistir(self, org: str, proveedor: Proveedor) -> None:
        lectura = self._lecturas.get(proveedor)
        if self._almacen is None or lectura is None or lectura.leido_en is None:
            return
        if self._persistido.get((org, proveedor)) == lectura.leido_en:
            return
        self._almacen.guardar_catalogo(
            org, proveedor, [e.a_contrato(org, lectura.leido_en) for e in lectura.entradas]
        )
        self._persistido[(org, proveedor)] = lectura.leido_en

    def leido(self, proveedor: Proveedor) -> bool:
        lectura = self._lecturas.get(proveedor)
        return lectura is not None and (lectura.leido_en is not None or bool(lectura.entradas))

    def error(self, proveedor: Proveedor) -> str | None:
        lectura = self._lecturas.get(proveedor)
        return lectura.error if lectura else None

    def entradas(self, proveedor: Proveedor | None = None) -> list[EntradaCatalogo]:
        return [
            e
            for p, lectura in sorted(self._lecturas.items())
            if proveedor in (None, p)
            for e in lectura.entradas
        ]

    def buscar(self, proveedor: Proveedor, nombre: str) -> list[EntradaCatalogo]:
        """Entradas cuyo modelo o despliegue se llama ``nombre``, en orden estable."""

        return [e for e in self.entradas(proveedor) if nombre in (e.modelo, e.despliegue)]
