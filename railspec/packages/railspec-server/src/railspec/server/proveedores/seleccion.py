"""Selección de proveedor, modelo y despliegue por rol, contra el catálogo conectado.

Foundry es el primario. Anthropic directo solo entra con su bandera y solo
para repositorios ``abierto``. En ``restringido`` e ``interno`` el contenido se
queda en la zona de datos de Azure (política aprobada el 2026-09-30): solo
despliegues de Foundry alojados en Azure, nunca ``global``, y dentro de la
zona del workspace (``Workspace.zona_datos_azure``) si la declara.

Con catálogo, el modelo pedido tiene que estar en él y cumplir el requisito
del rol (salida estructurada, effort, contexto mínimo): si no, el perfil es
insatisfacible y el motor lo dice; nunca degrada el modelo en silencio. Sin
catálogo para un proveedor (dobles de prueba), la zona sale de la región del
adaptador.
"""

from __future__ import annotations

from dataclasses import dataclass

from railspec.contracts.comun import NivelCodigo, Proveedor
from railspec.contracts.repositorio import RequisitoRol

from .base import ProveedorModelo
from .cache import CacheNodos
from .catalogo import REGION_GLOBAL, Catalogo, EntradaCatalogo
from .suscripciones import ErrorSuscripcion, ServicioSuscripciones


class PerfilInsatisfacible(Exception):
    """Ningún proveedor configurado puede servir el rol pedido (``perfil-insatisfacible``)."""


@dataclass(frozen=True)
class Eleccion:
    proveedor: ProveedorModelo
    #: Id del catálogo; es lo que se audita y factura.
    modelo: str
    #: Nombre que se envía al proveedor (despliegue de Foundry); ``None`` = el modelo.
    despliegue: str | None = None
    #: Región o zona donde corre la inferencia, para la auditoría.
    region: str | None = None
    #: Suscripción de la organización de la que sale el modelo (``None`` = variables de entorno del servidor).
    suscripcion: str | None = None


class Proveedores:
    def __init__(
        self,
        disponibles: dict[Proveedor, ProveedorModelo],
        catalogo: Catalogo | None = None,
        zona_recurso: str | None = None,
        cache: CacheNodos | None = None,
        suscripciones: ServicioSuscripciones | None = None,
    ) -> None:
        self._disponibles = disponibles
        #: Suscripciones registradas desde la consola (1.6); ``elegir(..., suscripcion=)`` solo usa estas.
        self.suscripciones = suscripciones
        self.catalogo = catalogo
        #: Caché de nodos por hash de entradas; ``None`` = siempre llama al proveedor.
        self.cache = cache
        #: Zona de datos del recurso de Foundry (``RAILSPEC_FOUNDRY_ZONA_DATOS``).
        self.zona_recurso = zona_recurso

    @property
    def configurados(self) -> list[Proveedor]:
        return sorted(self._disponibles)

    async def refrescar(self, org: str | None = None) -> None:
        """Lee el catálogo si caducó y lo persiste en la organización. Barato si está fresco."""

        if self.catalogo is not None:
            await self.catalogo.refrescar(org)

    def elegir(
        self,
        rol: str,
        requisito: RequisitoRol,
        nivel: NivelCodigo,
        *,
        org: str | None = None,
        zona: str | None = None,
        suscripcion: str | None = None,
    ) -> Eleccion:
        """Proveedor, modelo y despliegue que sirven ``requisito`` en ``nivel``, o ``PerfilInsatisfacible``.

        Con ``suscripcion`` (la del perfil) solo valen los modelos que esa suscripción de ``org`` tiene
        elegidos; sin ella rigen las variables de entorno del servidor (el respaldo anterior a 1.6).
        """

        if suscripcion is not None:
            return self._con_suscripcion(rol, requisito, nivel, org, zona, suscripcion)
        orden = [Proveedor.foundry]
        if nivel == NivelCodigo.abierto:
            orden.append(Proveedor.anthropic)
        motivos: list[str] = []
        for p in orden:
            if p not in self._disponibles:
                motivos.append(f"{p.value} no configurado")
                continue
            if p not in requisito.modelo:
                motivos.append(f"el perfil no da modelo para {p.value}")
                continue
            eleccion, motivo = self._con(p, requisito.modelo[p], requisito, nivel, zona)
            if eleccion is not None:
                return eleccion
            motivos.append(motivo)
        raise PerfilInsatisfacible(f"rol {rol} en nivel {nivel.value}: " + "; ".join(motivos))

    def _con_suscripcion(
        self,
        rol: str,
        req: RequisitoRol,
        nivel: NivelCodigo,
        org: str | None,
        zona: str | None,
        id_: str,
    ) -> Eleccion:
        donde = f"rol {rol} en nivel {nivel.value}, suscripción {id_}: "
        if self.suscripciones is None or org is None:
            raise PerfilInsatisfacible(donde + "este servidor no tiene suscripciones de modelos")
        try:
            activa = self.suscripciones.activa(org, id_)
        except ErrorSuscripcion as exc:
            raise PerfilInsatisfacible(donde + exc.detalle) from exc
        s = activa.suscripcion
        restringe = nivel != NivelCodigo.abierto
        if s.proveedor == Proveedor.anthropic and restringe:
            raise PerfilInsatisfacible(
                donde + f"«{s.nombre}» es Anthropic directo y solo sirve a repositorios abiertos"
            )
        nombre = req.modelo.get(s.proveedor)
        if nombre is None:
            raise PerfilInsatisfacible(donde + f"el perfil no da modelo para {s.proveedor.value}")
        candidatas = [e for e in activa.entradas if nombre in (e.modelo, e.despliegue)]
        if not candidatas:
            raise PerfilInsatisfacible(
                donde
                + f"{nombre} no está entre los modelos elegidos de «{s.nombre}»: elígelo en la suscripción"
            )
        rechazos: list[str] = []
        for e in candidatas:
            falta = self._falta(e, req, restringe, zona, activa.zona_recurso)
            if falta is None:
                try:
                    adaptador = self.suscripciones.adaptador(s)
                except ErrorSuscripcion as exc:
                    raise PerfilInsatisfacible(donde + exc.detalle) from exc
                return Eleccion(adaptador, e.modelo, e.despliegue, e.region, s.id)
            rechazos.append(f"{e.destino}: {falta}")
        raise PerfilInsatisfacible(donde + f"{nombre}: " + ", ".join(rechazos))

    def _con(
        self, p: Proveedor, nombre: str, req: RequisitoRol, nivel: NivelCodigo, zona: str | None
    ) -> tuple[Eleccion | None, str]:
        adaptador = self._disponibles[p]
        restringe = nivel != NivelCodigo.abierto
        if self.catalogo is None or not self.catalogo.cubre(p):
            region = adaptador.region
            if restringe and (region is None or region == REGION_GLOBAL):
                return None, f"{p.value}: sin región en la zona de datos de Azure (región {region or '?'})"
            return Eleccion(adaptador, nombre, None, region), ""
        if not self.catalogo.leido(p):
            error = self.catalogo.error(p) or "sin leer: falta refrescar()"
            return None, f"catálogo de {p.value} no disponible ({error})"
        candidatas = self.catalogo.buscar(p, nombre)
        if not candidatas:
            return None, f"{nombre} no está en el catálogo de {p.value}"
        rechazos: list[str] = []
        for e in candidatas:
            falta = self._falta(e, req, restringe, zona, self.zona_recurso)
            if falta is None:
                return Eleccion(adaptador, e.modelo, e.despliegue, e.region), ""
            rechazos.append(f"{e.destino}: {falta}")
        return None, f"{p.value}/{nombre}: " + ", ".join(rechazos)

    @staticmethod
    def _falta(
        e: EntradaCatalogo, req: RequisitoRol, restringe: bool, zona: str | None, zona_recurso: str | None
    ) -> str | None:
        caps = e.capacidades
        if req.structured_outputs and not caps.structured_outputs:
            return "sin salida estructurada"
        if req.effort is not None and req.effort not in caps.efforts:
            return f"no admite effort {req.effort.value}"
        if req.contexto_min_tokens is not None and caps.contexto_max_tokens < req.contexto_min_tokens:
            return f"contexto {caps.contexto_max_tokens} < {req.contexto_min_tokens}"
        if restringe and not e.en_zona(zona, zona_recurso):
            donde = e.region or "región desconocida"
            return f"fuera de la zona de datos ({donde}{f', workspace {zona}' if zona else ''})"
        return None

    def validar(
        self,
        pares: list[tuple[str, RequisitoRol]],
        nivel: NivelCodigo,
        *,
        org: str | None = None,
        zona: str | None = None,
        suscripcion: str | None = None,
    ) -> list[str]:
        """Motivos por los que algún ``(rol, requisito)`` no se puede servir; vacío = perfil válido."""

        motivos: list[str] = []
        vistos: set[tuple[str, str]] = set()
        for rol, req in pares:
            clave = (rol, req.model_dump_json())
            if clave in vistos:
                continue
            vistos.add(clave)
            try:
                self.elegir(rol, req, nivel, org=org, zona=zona, suscripcion=suscripcion)
            except PerfilInsatisfacible as exc:
                motivos.append(str(exc))
        return motivos
