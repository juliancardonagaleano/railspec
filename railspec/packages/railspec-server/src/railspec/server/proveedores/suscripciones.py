"""Suscripciones de modelos: conexiones de una organización a Foundry o a Anthropic, con claves cifradas.

Hasta el contrato 1.5 el servidor tenía un único Foundry y un único Anthropic, fijados por variables de
entorno (``RAILSPEC_FOUNDRY_*``, ``RAILSPEC_ANTHROPIC_*``). Una ``SuscripcionModelo`` (1.6) es una conexión
registrada desde la consola por un ``org-admin``: varias por organización, cada una con su endpoint, su
región y zona de datos, y su clave. Con ella:

1. **Descubrir**: lee de la API real del proveedor los modelos disponibles (despliegues del proyecto de
   Foundry; ``/v1/models`` de Anthropic) con tope de tiempo y errores clasificados y saneados.
2. **Elegir**: el administrador marca cuáles quedan como modelos disponibles; un despliegue que la API no
   lista se declara a mano (``declarar``), con su SKU.
3. **Asociar**: un ``PerfilConfig`` apunta a una suscripción y solo puede usar sus modelos elegidos
   (``Proveedores.elegir(..., suscripcion=...)``).

Reglas que no se negocian:

* La clave se guarda cifrada (``cifrado.py``, clave maestra del entorno) en una colección aparte y **ninguna
  API ni log la devuelve**: el contrato solo dice si hay una (``clave_configurada``). Cambiar el endpoint
  obliga a escribir la clave otra vez, para que no se pueda redirigir una clave que quien edita no ve.
* La región de cada modelo se deriva de la suscripción y del SKU del despliegue: ``Global*`` → ``global``,
  ``DataZone*`` → ``zona-<zona de la suscripción>``, el resto → la región de la suscripción. Sin SKU
  conocido (o sin zona), la región queda sin determinar. La región se audita; no restringe qué repositorios
  puede servir el modelo (el nivel de código solo gobierna qué material viaja).
* El endpoint de Foundry tiene que ser https y de un dominio de Azure conocido (o de
  ``RAILSPEC_FOUNDRY_HOSTS``): una suscripción no es una salida hacia cualquier host.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from collections.abc import Awaitable, Callable, Iterable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any
from urllib.parse import urlsplit

from railspec.contracts.comun import Effort, Proveedor
from railspec.contracts.repositorio import (
    Auditoria,
    AutenticacionSuscripcion,
    LecturaSuscripcion,
    ModeloSuscripcion,
    PrecioModelo,
    ProtocoloCompatible,
    SuscripcionModelo,
)

from .base import ProveedorModelo
from .catalogo import (
    EntradaCatalogo,
    ErrorCatalogo,
    FuenteAnthropic,
    FuenteFoundryProyecto,
    _estado_http,
    capacidades_conocidas,
    region_de_sku,
)
from .cifrado import Cifrador, ClaveMaestraAusente, ErrorCifrado
from .servicios_compatibles import HOSTS_CONOCIDOS, SERVICIOS, capacidades_de, leer_modelos

if TYPE_CHECKING:
    from ..contexto.destinos import PoliticaDestinos

log = logging.getLogger("railspec.suscripciones")

#: Dominios de los endpoints de Foundry (Azure AI Foundry / Azure OpenAI). ``RAILSPEC_FOUNDRY_HOSTS``
#: añade otros (nubes soberanas, Private Link con dominio propio).
HOSTS_FOUNDRY = (
    "*.services.ai.azure.com",
    "*.openai.azure.com",
    "*.cognitiveservices.azure.com",
)
#: Tope de una lectura de modelos (los errores de red se reportan antes).
TOPE_LECTURA_S = 45.0

_SKU = re.compile(r"^[A-Za-z0-9]{1,60}$")
_PROYECTO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,99}$")
_AUTENTICACION = {"ClientAuthenticationError", "CredentialUnavailableError"}


class ErrorSuscripcion(Exception):
    """Fallo de negocio con ``estado`` HTTP, ``codigo`` estable y un ``detalle`` que se puede mostrar."""

    def __init__(self, codigo: str, detalle: str, estado: int = 422) -> None:
        super().__init__(detalle)
        self.codigo = codigo
        self.detalle = detalle
        self.estado = estado


@dataclass(frozen=True)
class EntradaSuscripcion:
    """Lo que el administrador escribe en el formulario. ``clave`` ``None`` = conservar la que hay."""

    nombre: str
    autenticacion: AutenticacionSuscripcion = AutenticacionSuscripcion.api_key
    endpoint: str | None = None
    proyecto: str | None = None
    region: str | None = None
    zona_datos: str | None = None
    habilitada: bool = True
    clave: str | None = None
    #: Solo ``compatible``: servicio conocido (``opencode-zen``, ``minimax``) o ``None`` = personalizado.
    servicio: str | None = None
    endpoint_mensajes: str | None = None


@dataclass(frozen=True)
class Descubrimiento:
    suscripcion: SuscripcionModelo
    lectura: LecturaSuscripcion


@dataclass(frozen=True)
class SuscripcionActiva:
    """Lo que la selección necesita de una suscripción: sus modelos elegibles y cómo llamarla."""

    suscripcion: SuscripcionModelo
    entradas: list[EntradaCatalogo]


def politica_foundry(entorno: Mapping[str, str] | None = None) -> PoliticaDestinos:
    # Import perezoso: ``contexto`` arrastra el motor, que importa este paquete.
    from ..contexto.destinos import PoliticaDestinos

    env = os.environ if entorno is None else entorno
    extras = [h.lower() for h in re.split(r"[,\s]+", env.get("RAILSPEC_FOUNDRY_HOSTS", "")) if h]
    return PoliticaDestinos.desde_entorno({"RAILSPEC_PROVEEDORES_HOSTS": " ".join((*HOSTS_FOUNDRY, *extras))})


def politica_compatibles(entorno: Mapping[str, str] | None = None) -> PoliticaDestinos:
    """Hosts de los servicios conocidos más ``RAILSPEC_COMPATIBLES_HOSTS`` (endpoints personalizados)."""

    from ..contexto.destinos import PoliticaDestinos

    env = os.environ if entorno is None else entorno
    extras = [h.lower() for h in re.split(r"[,\s]+", env.get("RAILSPEC_COMPATIBLES_HOSTS", "")) if h]
    return PoliticaDestinos.desde_entorno(
        {"RAILSPEC_PROVEEDORES_HOSTS": " ".join((*HOSTS_CONOCIDOS, *extras))}
    )


def normalizar_base(endpoint: str, politica: PoliticaDestinos) -> str:
    """``https://host[:puerto][/ruta]`` sin barra final, o ``ErrorSuscripcion`` si no está permitido."""

    from ..contexto.destinos import DestinoNoPermitido

    texto = endpoint.strip()
    try:
        politica.validar_url(texto)
    except DestinoNoPermitido as exc:
        raise ErrorSuscripcion("endpoint-no-permitido", f"endpoint: {exc}") from exc
    partes = urlsplit(texto)
    if partes.query:
        raise ErrorSuscripcion("endpoint-no-permitido", "endpoint: no puede llevar parámetros de consulta")
    puerto = f":{partes.port}" if partes.port not in (None, 443) else ""
    return f"https://{(partes.hostname or '').lower()}{puerto}{partes.path.rstrip('/')}"


def normalizar_endpoint(endpoint: str, politica: PoliticaDestinos) -> str:
    """El origen (``https://host``) del endpoint, o ``ErrorSuscripcion`` si no es un destino permitido."""

    from ..contexto.destinos import DestinoNoPermitido

    try:
        politica.validar_url(endpoint.strip())
    except DestinoNoPermitido as exc:
        raise ErrorSuscripcion("endpoint-no-permitido", f"endpoint: {exc}") from exc
    partes = urlsplit(endpoint.strip())
    if partes.query:
        raise ErrorSuscripcion("endpoint-no-permitido", "endpoint: no puede llevar parámetros de consulta")
    puerto = f":{partes.port}" if partes.port not in (None, 443) else ""
    return f"https://{(partes.hostname or '').lower()}{puerto}"


def normalizar_proyecto(proyecto: str | None, endpoint: str) -> str | None:
    """Nombre del proyecto: acepta el nombre o la URL ``<endpoint>/api/projects/<nombre>``."""

    texto = (proyecto or "").strip()
    if not texto:
        return None
    if texto.startswith("https://"):
        partes = urlsplit(texto)
        if (partes.hostname or "").lower() != (urlsplit(endpoint).hostname or "").lower():
            raise ErrorSuscripcion(
                "proyecto-invalido", "proyecto: la URL tiene que ser del mismo recurso que el endpoint"
            )
        m = re.fullmatch(r"/api/projects/([^/]+)/?", partes.path)
        if m is None:
            raise ErrorSuscripcion(
                "proyecto-invalido", "proyecto: la URL debe ser <endpoint>/api/projects/<nombre>"
            )
        texto = m.group(1)
    if _PROYECTO.fullmatch(texto) is None:
        raise ErrorSuscripcion(
            "proyecto-invalido", "proyecto: nombre o URL de proyecto de Foundry no válidos"
        )
    return texto


def region_modelo(proveedor: Proveedor, region: str | None, zona: str | None, sku: str | None) -> str | None:
    """Región de un modelo según su suscripción: ``None`` si no se sabe (dato de auditoría)."""

    if proveedor != Proveedor.foundry or not sku:
        return None
    return region_de_sku(sku, region, zona)


def hosting_de(s: SuscripcionModelo) -> str:
    """``azure`` (Foundry), ``anthropic`` o ``externo`` (compatible): dato informativo del catálogo."""

    return {Proveedor.foundry: "azure", Proveedor.anthropic: "anthropic"}.get(s.proveedor, "externo")


def clasificar_lectura(exc: BaseException, s: SuscripcionModelo) -> ErrorCatalogo:
    """Un fallo de lectura como ``ErrorCatalogo`` que nombra la suscripción sin filtrar URLs ni claves."""

    if isinstance(exc, ErrorCatalogo):
        return exc
    if isinstance(exc, ErrorCifrado | ErrorSuscripcion):
        return ErrorCatalogo(exc.codigo, exc.detalle)
    n = f"«{s.nombre}»"
    estado = _estado_http(exc)
    nombre = type(exc).__name__
    sugerencia = (
        " Si la API de proyecto no acepta la clave, usa la identidad del servidor o declara a mano."
        if s.proveedor == Proveedor.foundry and s.autenticacion == AutenticacionSuscripcion.api_key
        else ""
    )
    if estado == 401:
        quien = (
            "la identidad del servidor"
            if s.autenticacion == AutenticacionSuscripcion.identidad_servidor
            else "la clave"
        )
        return ErrorCatalogo(
            "autenticacion",
            f"{n}: el proveedor rechazó {quien} (HTTP 401): revísala o escribe una nueva.{sugerencia}",
        )
    if estado == 403:
        return ErrorCatalogo(
            "permiso", f"{n}: el proveedor denegó listar los modelos (HTTP 403).{sugerencia}"
        )
    if estado == 404:
        return ErrorCatalogo(
            "no-encontrado",
            f"{n}: el proveedor no encontró el recurso (HTTP 404): revisa endpoint y proyecto.",
        )
    if estado == 429:
        return ErrorCatalogo(
            "limite", f"{n}: el proveedor limitó las consultas (HTTP 429): reintenta en unos minutos."
        )
    if estado is not None:
        return ErrorCatalogo(
            "proveedor", f"{n}: el proveedor respondió con un error (HTTP {estado}): reintenta más tarde."
        )
    if isinstance(exc, TimeoutError) or "Timeout" in nombre:
        return ErrorCatalogo("tiempo", f"{n}: el proveedor no respondió a tiempo: reintenta en unos minutos.")
    if nombre in _AUTENTICACION:
        return ErrorCatalogo(
            "autenticacion",
            f"{n}: el servidor no pudo autenticarse con Entra ID: revisa la identidad del servidor.",
        )
    if isinstance(exc, OSError) or any(t in nombre for t in ("Connect", "Transport", "Network")):
        return ErrorCatalogo(
            "red", f"{n}: no se pudo conectar con el proveedor: revisa el endpoint y la red del servidor."
        )
    if isinstance(exc, ValueError):
        return ErrorCatalogo(
            "forma", f"{n}: la respuesta del proveedor no tiene la forma esperada ({nombre})."
        )
    return ErrorCatalogo(
        "interno", f"{n}: error inesperado al leer los modelos ({nombre}); el detalle está en el log."
    )


Lector = Callable[[SuscripcionModelo, str | None], Awaitable[list[EntradaCatalogo]]]
Fabrica = Callable[[SuscripcionModelo, str | None], ProveedorModelo]


class ServicioSuscripciones:
    """Casos de uso de las suscripciones sobre el almacén de la consola (``AlmacenConsola``).

    ``lector`` y ``fabrica`` son las costuras de prueba: por defecto leen y llaman a los proveedores reales.
    """

    def __init__(
        self,
        datos: Any,
        cifrador: Cifrador | None,
        *,
        politica: PoliticaDestinos | None = None,
        politica_personalizados: PoliticaDestinos | None = None,
        reloj: Callable[[], datetime] = lambda: datetime.now(UTC),
        tope_lectura_s: float = TOPE_LECTURA_S,
        lector: Lector | None = None,
        fabrica: Fabrica | None = None,
        transporte: Any | None = None,
    ) -> None:
        self._datos = datos
        self._cifrador = cifrador
        self._politica = politica or politica_foundry()
        self._politica_compatibles = politica_personalizados or politica_compatibles()
        self._reloj = reloj
        self._tope = tope_lectura_s
        self._lector = lector or self._leer_proveedor
        self._fabrica = fabrica or self._crear_adaptador
        self._transporte = transporte
        #: (org, id) -> (versión, adaptador): una escritura sube la versión y el adaptador viejo se descarta.
        self._adaptadores: dict[tuple[str, str], tuple[int, ProveedorModelo]] = {}

    # --- cifrado ---------------------------------------------------------------------------------

    @property
    def cifrado_disponible(self) -> bool:
        return self._cifrador is not None

    def _exigir_cifrador(self) -> Cifrador:
        if self._cifrador is None:
            falta = ClaveMaestraAusente()
            raise ErrorSuscripcion(falta.codigo, falta.detalle, 503)
        return self._cifrador

    def _clave(self, s: SuscripcionModelo) -> str | None:
        """La clave en claro (``None`` con identidad del servidor). Solo para llamar al proveedor."""

        if s.autenticacion == AutenticacionSuscripcion.identidad_servidor:
            return None
        cifrador = self._exigir_cifrador()
        cifrada = self._datos.clave_suscripcion(s.org, s.id)
        if not cifrada:
            raise ErrorCifrado("clave-ausente", "la suscripción no tiene clave guardada: escribe una.")
        return cifrador.descifrar(cifrada, s.org, s.id)

    # --- lectura ---------------------------------------------------------------------------------

    def listar(self, org: str) -> list[SuscripcionModelo]:
        return self._datos.suscripciones(org)

    def obtener(self, org: str, id_: str) -> SuscripcionModelo:
        s = self._datos.suscripcion(org, id_)
        if s is None:
            raise ErrorSuscripcion("no-encontrada", f"no existe la suscripción {id_}", 404)
        return s

    # --- escritura -------------------------------------------------------------------------------

    def guardar(
        self,
        org: str,
        id_: str,
        proveedor: Proveedor,
        entrada: EntradaSuscripcion,
        version: int | None,
        auditoria: Auditoria,
    ) -> SuscripcionModelo:
        """Crea (``version`` ``None``) o edita una suscripción; la clave, si viene, se cifra aparte."""

        previo = self._datos.suscripcion(org, id_)
        if version is not None and previo is None:
            raise ErrorSuscripcion("no-encontrada", f"no existe la suscripción {id_}", 404)
        if previo is not None and previo.proveedor != proveedor:
            raise ErrorSuscripcion(
                "proveedor-inmutable", "el proveedor de una suscripción no se cambia: crea otra"
            )
        clave = (entrada.clave or "").strip() or None
        endpoint = endpoint_mensajes = servicio = proyecto = region = zona = None
        if proveedor != Proveedor.compatible and (entrada.servicio or entrada.endpoint_mensajes):
            raise ErrorSuscripcion(
                "campos-invalidos", "servicio y endpoint de mensajes son solo de los proveedores compatibles"
            )
        if proveedor == Proveedor.foundry:
            if not entrada.endpoint:
                raise ErrorSuscripcion(
                    "endpoint-requerido", "una suscripción de Foundry exige el endpoint del recurso"
                )
            endpoint = normalizar_endpoint(entrada.endpoint, self._politica)
            proyecto = normalizar_proyecto(entrada.proyecto, endpoint)
            region = (entrada.region or "").strip().lower() or None
            zona = (entrada.zona_datos or "").strip().lower() or None
        elif entrada.autenticacion != AutenticacionSuscripcion.api_key:
            raise ErrorSuscripcion(
                "autenticacion-invalida",
                "Anthropic y los proveedores compatibles solo se autentican con una clave de API",
            )
        elif proveedor == Proveedor.compatible:
            if entrada.proyecto or entrada.region or entrada.zona_datos:
                raise ErrorSuscripcion(
                    "campos-invalidos", "un proveedor compatible no lleva proyecto, región ni zona de datos"
                )
            endpoint, endpoint_mensajes, servicio = self._destinos_compatible(entrada)
            for m in previo.modelos if previo else []:
                destino = endpoint if m.protocolo == ProtocoloCompatible.openai_chat else endpoint_mensajes
                if destino is None:
                    raise ErrorSuscripcion(
                        "endpoint-requerido",
                        f"el modelo {m.clave} usa {m.protocolo.value if m.protocolo else '?'}: conserva el "
                        "endpoint de ese protocolo o retira el modelo antes",
                    )
        elif entrada.endpoint or entrada.proyecto or entrada.region or entrada.zona_datos:
            raise ErrorSuscripcion(
                "campos-invalidos",
                "Anthropic usa el endpoint del proveedor: sin endpoint, proyecto, región ni zona",
            )
        con_clave = entrada.autenticacion == AutenticacionSuscripcion.api_key
        if not con_clave and clave:
            raise ErrorSuscripcion("clave-sobra", "la identidad del servidor no lleva clave")
        ya_tiene = previo is not None and previo.clave_configurada
        # Cambiar adónde va la clave obliga a escribirla otra vez: quien edita no la ve ni puede redirigirla.
        destino_cambia = previo is not None and (previo.endpoint, previo.endpoint_mensajes) != (
            endpoint,
            endpoint_mensajes,
        )
        if con_clave and clave is None and (not ya_tiene or destino_cambia):
            raise ErrorSuscripcion(
                "clave-requerida",
                "cambiaste el endpoint: la clave guardada no se reutiliza con otro destino: escríbela"
                if ya_tiene
                else "escribe la clave de la suscripción",
            )
        cifrador = self._exigir_cifrador() if clave is not None else None
        nueva = SuscripcionModelo(
            version=(version or 0) + 1,
            auditoria=auditoria,
            org=org,
            id=id_,
            nombre=entrada.nombre.strip(),
            proveedor=proveedor,
            endpoint=endpoint,
            endpoint_mensajes=endpoint_mensajes,
            servicio=servicio,
            proyecto=proyecto,
            region=region,
            zona_datos=zona,
            autenticacion=entrada.autenticacion,
            clave_configurada=con_clave,
            clave_actualizada_en=self._reloj()
            if clave is not None
            else (previo.clave_actualizada_en if previo and con_clave else None),
            habilitada=entrada.habilitada,
            modelos=[
                m.model_copy(update={"region": region_modelo(proveedor, region, zona, m.sku)})
                for m in (previo.modelos if previo else [])
            ],
            ultima_lectura=previo.ultima_lectura if previo else None,
        )
        # Primero el documento: un ``ConflictoVersion`` no toca la clave guardada de otra suscripción.
        self._datos.guardar_suscripcion(nueva, version)
        if cifrador is not None and clave is not None:
            self._datos.guardar_clave_suscripcion(org, id_, cifrador.cifrar(clave, org, id_))
        elif not con_clave:
            self._datos.borrar_clave_suscripcion(org, id_)
        self._adaptadores.pop((org, id_), None)
        return nueva

    def _destinos_compatible(self, entrada: EntradaSuscripcion) -> tuple[str | None, str | None, str | None]:
        """``(endpoint, endpoint_mensajes, servicio)``: los del servicio conocido o los personalizados."""

        if entrada.servicio:
            conocido = SERVICIOS.get(entrada.servicio)
            if conocido is None:
                raise ErrorSuscripcion(
                    "servicio-desconocido",
                    f"servicio {entrada.servicio}: elige {', '.join(sorted(SERVICIOS))} o personalizado",
                )
            if entrada.endpoint or entrada.endpoint_mensajes:
                raise ErrorSuscripcion(
                    "campos-invalidos", f"{conocido.nombre} fija sus endpoints: no los escribas"
                )
            return conocido.endpoint, conocido.endpoint_mensajes, conocido.id
        if not entrada.endpoint and not entrada.endpoint_mensajes:
            raise ErrorSuscripcion(
                "endpoint-requerido", "una suscripción personalizada exige al menos un endpoint"
            )
        endpoint = normalizar_base(entrada.endpoint, self._politica_compatibles) if entrada.endpoint else None
        mensajes = (
            normalizar_base(entrada.endpoint_mensajes, self._politica_compatibles)
            if entrada.endpoint_mensajes
            else None
        )
        return endpoint, mensajes, None

    def borrar(self, org: str, id_: str) -> SuscripcionModelo:
        s = self.obtener(org, id_)
        usan = self._datos.perfiles_con_suscripcion(org, id_)
        if usan:
            donde = ", ".join(
                sorted({f"{p.nombre.value}{f' ({p.workspace})' if p.workspace else ''}" for p in usan})
            )
            raise ErrorSuscripcion(
                "suscripcion-en-uso",
                f"la usan estos perfiles: {donde}. Cámbialos de suscripción antes de borrarla.",
                409,
            )
        self._datos.borrar_suscripcion(org, id_)
        self._adaptadores.pop((org, id_), None)
        return s

    # --- modelos: descubrir, declarar, elegir ----------------------------------------------------

    async def descubrir(self, org: str, id_: str, por: str | None, auditoria: Auditoria) -> Descubrimiento:
        """Lee los modelos del proveedor y los mezcla con la elección; un fallo conserva todo."""

        s = self.obtener(org, id_)
        if not s.habilitada:
            raise ErrorSuscripcion(
                "suscripcion-deshabilitada",
                "la suscripción está deshabilitada: habilítala para descubrir modelos",
                409,
            )
        ahora = self._reloj()
        try:
            clave = self._clave(s)
            leidas = await asyncio.wait_for(self._lector(s, clave), self._tope)
        except Exception as exc:
            error = clasificar_lectura(exc, s)
            # Solo la clase y el código HTTP: ni el texto de la excepción (puede traer URLs) ni la clave.
            log.warning(
                "descubrimiento de %s/%s falló: %s (HTTP %s)", org, id_, type(exc).__name__, _estado_http(exc)
            )
            lectura = LecturaSuscripcion(
                en=ahora,
                por=por,
                resultado="error",
                modelos=len(s.modelos),
                error_codigo=error.codigo,
                error_detalle=error.detalle[:500],
            )
            return Descubrimiento(self._con_lectura(s, s.modelos, lectura, auditoria), lectura)
        modelos = self._mezclar(s, leidas, ahora)
        lectura = LecturaSuscripcion(en=ahora, por=por, resultado="ok", modelos=len(modelos))
        return Descubrimiento(self._con_lectura(s, modelos, lectura, auditoria), lectura)

    def _con_lectura(
        self,
        s: SuscripcionModelo,
        modelos: list[ModeloSuscripcion],
        lectura: LecturaSuscripcion,
        auditoria: Auditoria,
    ) -> SuscripcionModelo:
        nueva = _copiar(
            s, modelos=modelos, ultima_lectura=lectura, version=s.version + 1, auditoria=auditoria
        )
        self._datos.guardar_suscripcion(nueva, s.version)
        return nueva

    def _mezclar(
        self, s: SuscripcionModelo, leidas: Iterable[EntradaCatalogo], ahora: datetime
    ) -> list[ModeloSuscripcion]:
        """Lo leído gana; lo declarado se conserva; lo elegido que ya no aparece queda ``ausente``."""

        por_clave = {e.destino: e for e in leidas}
        resultado: list[ModeloSuscripcion] = []
        for previo in s.modelos:
            e = por_clave.pop(previo.clave, None)
            if previo.origen == "declarado":
                resultado.append(previo.model_copy(update={"visto_en": ahora if e else previo.visto_en}))
            elif e is not None:
                resultado.append(self._modelo(s, e, previo.seleccionado, ahora))
            elif previo.seleccionado:
                resultado.append(previo.model_copy(update={"ausente": True}))
        resultado += [self._modelo(s, e, False, ahora) for e in por_clave.values()]
        return sorted(resultado, key=lambda m: (m.modelo, m.clave))

    def _modelo(
        self, s: SuscripcionModelo, e: EntradaCatalogo, seleccionado: bool, ahora: datetime
    ) -> ModeloSuscripcion:
        return ModeloSuscripcion(
            modelo=e.modelo,
            despliegue=e.despliegue,
            sku=e.sku,
            region=region_modelo(s.proveedor, s.region, s.zona_datos, e.sku),
            capacidades=e.capacidades,
            origen="descubierto",
            seleccionado=seleccionado,
            visto_en=ahora,
            protocolo=e.protocolo,
        )

    def seleccionar(
        self, org: str, id_: str, version: int, claves: list[str], auditoria: Auditoria
    ) -> SuscripcionModelo:
        """Deja como modelos disponibles exactamente ``claves`` (despliegue o, sin él, id del modelo)."""

        s = self.obtener(org, id_)
        existentes = {m.clave: m for m in s.modelos}
        pedidas = set(claves)
        desconocidas = sorted(pedidas - set(existentes))
        if desconocidas:
            raise ErrorSuscripcion(
                "modelo-desconocido", f"no están en la suscripción: {', '.join(desconocidas)}"
            )
        ausentes = sorted(c for c in pedidas if existentes[c].ausente)
        if ausentes:
            raise ErrorSuscripcion(
                "modelo-ausente",
                f"ya no aparecen en el proveedor: {', '.join(ausentes)}. Descubre de nuevo o declara.",
            )
        modelos = [m.model_copy(update={"seleccionado": m.clave in pedidas}) for m in s.modelos]
        nueva = _copiar(s, modelos=modelos, version=version + 1, auditoria=auditoria)
        self._datos.guardar_suscripcion(nueva, version)
        return nueva

    def declarar(
        self,
        org: str,
        id_: str,
        version: int,
        *,
        modelo: str,
        despliegue: str,
        sku: str,
        efforts: list[Effort] | None,
        structured_outputs: bool | None,
        contexto: int | None,
        auditoria: Auditoria,
    ) -> SuscripcionModelo:
        """Declara a mano un despliegue de Foundry (o corrige uno descubierto) y lo deja elegido."""

        s = self.obtener(org, id_)
        if s.proveedor != Proveedor.foundry:
            raise ErrorSuscripcion(
                "solo-foundry",
                "solo se declaran despliegues en suscripciones de Foundry; en las compatibles se declara "
                "el modelo con su protocolo",
            )
        if _SKU.fullmatch(sku or "") is None:
            raise ErrorSuscripcion(
                "sku-invalido", "sku: letras y números (Standard, GlobalStandard, DataZoneStandard...)"
            )
        caps = capacidades_conocidas(modelo)
        cambios: dict[str, Any] = {}
        if efforts is not None:
            cambios["efforts"] = efforts
        if structured_outputs is not None:
            cambios["structured_outputs"] = structured_outputs
        if contexto is not None:
            cambios["contexto_max_tokens"] = contexto
        nuevo = ModeloSuscripcion(
            modelo=modelo.strip(),
            despliegue=despliegue.strip(),
            sku=sku,
            region=None,
            capacidades=caps.model_copy(update=cambios),
            origen="declarado",
            seleccionado=True,
        )
        nuevo = nuevo.model_copy(update={"region": region_modelo(s.proveedor, s.region, s.zona_datos, sku)})
        modelos = [m for m in s.modelos if m.clave != nuevo.clave] + [nuevo]
        nueva = _copiar(
            s,
            modelos=sorted(modelos, key=lambda m: (m.modelo, m.clave)),
            version=version + 1,
            auditoria=auditoria,
        )
        self._datos.guardar_suscripcion(nueva, version)
        return nueva

    def declarar_compatible(
        self,
        org: str,
        id_: str,
        version: int,
        *,
        modelo: str,
        protocolo: ProtocoloCompatible,
        efforts: list[Effort] | None,
        structured_outputs: bool | None,
        contexto: int | None,
        precio: PrecioModelo | None,
        auditoria: Auditoria,
    ) -> SuscripcionModelo:
        """Declara a mano un modelo de una suscripción compatible (o corrige uno) y lo deja elegido."""

        s = self.obtener(org, id_)
        if s.proveedor != Proveedor.compatible:
            raise ErrorSuscripcion("solo-compatible", "solo se declaran modelos en suscripciones compatibles")
        destino = s.endpoint if protocolo == ProtocoloCompatible.openai_chat else s.endpoint_mensajes
        if destino is None:
            raise ErrorSuscripcion(
                "endpoint-requerido", f"la suscripción no tiene endpoint para {protocolo.value}"
            )
        caps = capacidades_de(SERVICIOS.get(s.servicio or ""), modelo.strip())
        cambios: dict[str, Any] = {}
        if efforts is not None:
            cambios["efforts"] = efforts
        if structured_outputs is not None:
            cambios["structured_outputs"] = structured_outputs
        if contexto is not None:
            cambios["contexto_max_tokens"] = contexto
        nuevo = ModeloSuscripcion(
            modelo=modelo.strip(),
            capacidades=caps.model_copy(update=cambios),
            origen="declarado",
            seleccionado=True,
            protocolo=protocolo,
            precio_usd_mtok=precio,
        )
        modelos = [m for m in s.modelos if m.clave != nuevo.clave] + [nuevo]
        nueva = _copiar(
            s,
            modelos=sorted(modelos, key=lambda m: (m.modelo, m.clave)),
            version=version + 1,
            auditoria=auditoria,
        )
        self._datos.guardar_suscripcion(nueva, version)
        return nueva

    def retirar(
        self, org: str, id_: str, version: int, clave: str, auditoria: Auditoria
    ) -> SuscripcionModelo:
        s = self.obtener(org, id_)
        previo = next((m for m in s.modelos if m.clave == clave), None)
        if previo is None or previo.origen != "declarado":
            raise ErrorSuscripcion(
                "modelo-desconocido", "solo se retiran los despliegues declarados a mano", 404
            )
        nueva = _copiar(
            s, modelos=[m for m in s.modelos if m.clave != clave], version=version + 1, auditoria=auditoria
        )
        self._datos.guardar_suscripcion(nueva, version)
        return nueva

    # --- para la selección de modelos (Proveedores.elegir) ---------------------------------------

    def activa(self, org: str, id_: str) -> SuscripcionActiva:
        """La suscripción habilitada con sus modelos elegidos; ``ErrorSuscripcion`` si no se puede usar."""

        s = self._datos.suscripcion(org, id_)
        if s is None:
            raise ErrorSuscripcion("no-encontrada", f"la suscripción {id_} no existe en la organización", 404)
        if not s.habilitada:
            raise ErrorSuscripcion(
                "suscripcion-deshabilitada", f"la suscripción «{s.nombre}» está deshabilitada", 409
            )
        entradas = [
            EntradaCatalogo(
                s.proveedor,
                m.modelo,
                m.despliegue,
                hosting_de(s),
                m.region,
                m.capacidades,
                m.sku,
                m.protocolo,
            )
            for m in s.modelos
            if m.seleccionado and not m.ausente
        ]
        return SuscripcionActiva(s, entradas)

    def adaptador(self, s: SuscripcionModelo) -> ProveedorModelo:
        """El cliente del proveedor para ``s``; se reutiliza mientras su versión no cambie."""

        guardado = self._adaptadores.get((s.org, s.id))
        if guardado is not None and guardado[0] == s.version:
            return guardado[1]
        try:
            adaptador = self._fabrica(s, self._clave(s))
        except ErrorCifrado as exc:
            raise ErrorSuscripcion(exc.codigo, exc.detalle, 503) from exc
        self._adaptadores[(s.org, s.id)] = (s.version, adaptador)
        return adaptador

    # --- proveedores reales ----------------------------------------------------------------------

    def _crear_adaptador(self, s: SuscripcionModelo, clave: str | None) -> ProveedorModelo:
        from .claude import AdaptadorClaude, cliente_anthropic
        from .compatible import AdaptadorCompatible, cliente_chat, cliente_mensajes
        from .foundry import proveedor_foundry

        if s.proveedor == Proveedor.compatible:
            assert clave is not None
            return AdaptadorCompatible(
                {m.clave: m.protocolo for m in s.modelos if m.protocolo is not None},
                cliente_mensajes=cliente_mensajes(s.endpoint_mensajes, clave)
                if s.endpoint_mensajes
                else None,
                cliente_chat=cliente_chat(s.endpoint, clave) if s.endpoint else None,
                precios={m.clave: m.precio_usd_mtok for m in s.modelos if m.precio_usd_mtok is not None},
            )
        if s.proveedor == Proveedor.anthropic:
            assert clave is not None
            return AdaptadorClaude(Proveedor.anthropic, cliente_anthropic(clave), region="global")
        assert s.endpoint is not None
        return proveedor_foundry(s.endpoint, clave, s.region)

    async def _leer_proveedor(self, s: SuscripcionModelo, clave: str | None) -> list[EntradaCatalogo]:
        if s.proveedor == Proveedor.compatible:
            assert clave is not None
            return await leer_modelos(
                SERVICIOS.get(s.servicio or ""),
                s.endpoint,
                s.endpoint_mensajes,
                clave,
                transporte=self._transporte,
            )
        if s.proveedor == Proveedor.anthropic:
            from .claude import cliente_anthropic

            assert clave is not None
            cliente = cliente_anthropic(clave, timeout=30.0, max_retries=1)
            try:
                return await FuenteAnthropic(cliente).leer()
            finally:
                await _cerrar(cliente)
        if not s.proyecto:
            raise ErrorCatalogo(
                "configuracion",
                f"«{s.nombre}» no tiene proyecto de Foundry: indícalo para descubrir los despliegues o "
                "declara los despliegues a mano.",
            )
        assert s.endpoint is not None
        token = None
        if clave is None:
            from .claude import SCOPE_FOUNDRY_ANTHROPIC
            from .foundry import proveedor_token_entra

            token = proveedor_token_entra(SCOPE_FOUNDRY_ANTHROPIC)
        fuente = FuenteFoundryProyecto(
            f"{s.endpoint}/api/projects/{s.proyecto}",
            clave,
            s.region,
            s.zona_datos,
            proveedor_token=token,
            transporte=self._transporte,
            estricta=True,
        )
        return await fuente.leer()


def _copiar(s: SuscripcionModelo, **cambios: Any) -> SuscripcionModelo:
    """Copia con cambios **validando** el resultado (``model_copy`` no lo hace)."""

    return SuscripcionModelo.model_validate({**s.model_dump(), **cambios})


async def _cerrar(cliente: Any) -> None:
    cerrar = getattr(cliente, "close", None)
    if cerrar is None:
        return
    try:
        resultado = cerrar()
        if asyncio.iscoroutine(resultado):
            await resultado
    except Exception:  # cerrar es de cortesía: nunca tumba la lectura
        log.debug("no se pudo cerrar el cliente de lectura", exc_info=True)
