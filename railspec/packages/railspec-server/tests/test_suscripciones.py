"""Suscripciones de modelos (contrato 1.6): claves cifradas, descubrimiento, elección y política de datos.

Sin credenciales reales: el proyecto de Foundry se simula con ``httpx.MockTransport`` (su forma de respuesta
sigue sin verificarse contra un recurso real) y Anthropic con un cliente doble. Lo que se comprueba una y otra
vez es que la clave nunca sale: ni en la API, ni en el contrato, ni en la base en claro, ni en los errores.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from apoyo_motor import JULIAN, ORG
from pydantic import ValidationError
from railspec.contracts.almacen import ConflictoVersion
from railspec.contracts.comun import Effort, NivelCodigo, Proveedor
from railspec.contracts.repositorio import (
    Auditoria,
    AutenticacionSuscripcion,
    Capacidades,
    RequisitoRol,
    SuscripcionModelo,
)
from railspec.server.consola.almacen import AlmacenConsola
from railspec.server.estado import almacen_en_memoria
from railspec.server.proveedores import PerfilInsatisfacible, Proveedores
from railspec.server.proveedores.cifrado import VARIABLE, Cifrador
from railspec.server.proveedores.falso import ProveedorGuionado
from railspec.server.proveedores.suscripciones import (
    EntradaSuscripcion,
    ErrorSuscripcion,
    ServicioSuscripciones,
    politica_foundry,
)

CLAVE = "sk-FOUNDRY-CLAVE-QUE-NO-DEBE-SALIR-0123456789"
ENDPOINT = "https://acme-eu.services.ai.azure.com"
AHORA = datetime(2026, 10, 4, 12, tzinfo=UTC)
AUDITORIA = Auditoria(creado_por=JULIAN, creado_en=AHORA, actualizado_por=JULIAN, actualizado_en=AHORA)
FOUNDRY = Proveedor.foundry
ANTHROPIC = Proveedor.anthropic


def correr(coro):
    return asyncio.run(coro)


def _cifrador() -> Cifrador:
    c = Cifrador.desde_entorno({VARIABLE: base64.b64encode(os.urandom(32)).decode()})
    assert c is not None
    return c


def _servicio(cifrador="si", **kw):
    almacen = almacen_en_memoria()
    datos = AlmacenConsola(almacen.db)
    servicio = ServicioSuscripciones(datos, _cifrador() if cifrador == "si" else cifrador, **kw)
    return servicio, datos, almacen


def _foundry(**cambios) -> EntradaSuscripcion:
    base = {
        "nombre": "Foundry UE",
        "endpoint": ENDPOINT,
        "proyecto": "railspec",
        "region": "swedencentral",
        "zona_datos": "eu",
        "clave": CLAVE,
    }
    return EntradaSuscripcion(**(base | cambios))


def _crear(servicio, id_="foundry-eu", **cambios):
    return servicio.guardar(ORG, id_, FOUNDRY, _foundry(**cambios), None, AUDITORIA)


def _volcado(almacen) -> str:
    """Todo lo que hay en la base, como texto: la clave en claro no puede estar en ningún documento."""

    return json.dumps(
        {n: list(almacen.db[n].find({})) for n in almacen.db.list_collection_names()}, default=str
    )


def _despliegues(*filas):
    return {
        "value": [
            {"name": d, "modelName": m, **({"sku": {"name": sku}} if sku else {}), "type": "ModelDeployment"}
            for d, m, sku in filas
        ]
    }


CUERPO_FOUNDRY = _despliegues(
    ("opus-dz", "claude-opus-5-5", "DataZoneStandard"),
    ("sonnet-gl", "claude-sonnet-5-5", "GlobalStandard"),
    ("haiku-st", "claude-haiku-4-5", "Standard"),
    ("raro", "claude-opus-5-5", None),
)


def _transporte(cuerpo=CUERPO_FOUNDRY, estado=200, vistas=None):
    def responder(peticion: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(peticion)
        return httpx.Response(estado, json=cuerpo)

    return httpx.MockTransport(responder)


def _descubrir(servicio, id_="foundry-eu"):
    return correr(servicio.descubrir(ORG, id_, "ana", AUDITORIA))


def _por_clave(s: SuscripcionModelo) -> dict:
    return {m.clave: m for m in s.modelos}


# --- registro y clave ---------------------------------------------------------------------------


def test_la_clave_se_guarda_cifrada_aparte_y_no_esta_en_ningun_documento():
    servicio, datos, almacen = _servicio()
    s = _crear(servicio)
    assert s.clave_configurada and s.clave_actualizada_en is not None
    cifrada = datos.clave_suscripcion(ORG, "foundry-eu")
    assert cifrada and cifrada.startswith("v1.") and CLAVE not in cifrada
    assert CLAVE not in _volcado(almacen)
    assert CLAVE not in s.model_dump_json() and CLAVE not in repr(s)
    # El contrato no tiene dónde ponerla.
    with pytest.raises(ValidationError):
        SuscripcionModelo.model_validate({**s.model_dump(), "clave": CLAVE})


def test_editar_sin_clave_conserva_la_guardada_y_se_usa_para_llamar_al_proveedor():
    usadas = []
    servicio, datos, _ = _servicio(
        fabrica=lambda s, clave: usadas.append(clave) or ProveedorGuionado(lambda p: None)
    )
    s = _crear(servicio)
    s2 = servicio.guardar(
        ORG, "foundry-eu", FOUNDRY, _foundry(nombre="Otro nombre", clave=None), s.version, AUDITORIA
    )
    assert s2.nombre == "Otro nombre" and s2.clave_configurada and s2.version == s.version + 1
    assert s2.clave_actualizada_en == s.clave_actualizada_en
    servicio.adaptador(s2)
    assert usadas == [CLAVE]


def test_cambiar_el_endpoint_obliga_a_escribir_la_clave_otra_vez():
    servicio, datos, _ = _servicio()
    s = _crear(servicio)
    nuevo = "https://otro.openai.azure.com"
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.guardar(
            ORG, "foundry-eu", FOUNDRY, _foundry(endpoint=nuevo, clave=None), s.version, AUDITORIA
        )
    assert exc.value.codigo == "clave-requerida" and "endpoint" in exc.value.detalle
    s2 = servicio.guardar(
        ORG, "foundry-eu", FOUNDRY, _foundry(endpoint=nuevo, clave="otra-clave"), s.version, AUDITORIA
    )
    assert s2.endpoint == nuevo
    assert (
        servicio._cifrador.descifrar(datos.clave_suscripcion(ORG, "foundry-eu"), ORG, "foundry-eu")
        == "otra-clave"
    )


def test_crear_exige_clave_y_clave_maestra():
    servicio, _, _ = _servicio()
    with pytest.raises(ErrorSuscripcion) as exc:
        _crear(servicio, clave=None)
    assert exc.value.codigo == "clave-requerida"
    sin_maestra, datos, _ = _servicio(cifrador=None)
    assert not sin_maestra.cifrado_disponible
    with pytest.raises(ErrorSuscripcion) as exc:
        _crear(sin_maestra)
    assert (exc.value.codigo, exc.value.estado) == ("clave-maestra-ausente", 503)
    assert VARIABLE in exc.value.detalle and datos.suscripciones(ORG) == []  # no quedó nada a medias


def test_la_identidad_del_servidor_no_lleva_clave_ni_necesita_clave_maestra():
    servicio, datos, _ = _servicio(cifrador=None)
    s = _crear(servicio, autenticacion=AutenticacionSuscripcion.identidad_servidor, clave=None)
    assert not s.clave_configurada and datos.clave_suscripcion(ORG, "foundry-eu") is None
    with pytest.raises(ErrorSuscripcion) as exc:
        _crear(servicio, "otra", autenticacion=AutenticacionSuscripcion.identidad_servidor, clave="x")
    assert exc.value.codigo == "clave-sobra"


def test_pasar_de_clave_a_identidad_del_servidor_borra_la_clave_guardada():
    servicio, datos, almacen = _servicio()
    s = _crear(servicio)
    servicio.guardar(
        ORG,
        "foundry-eu",
        FOUNDRY,
        _foundry(autenticacion=AutenticacionSuscripcion.identidad_servidor, clave=None),
        s.version,
        AUDITORIA,
    )
    assert datos.clave_suscripcion(ORG, "foundry-eu") is None


def test_duplicada_o_editada_con_version_vieja_no_toca_la_clave_de_la_existente():
    servicio, datos, _ = _servicio()
    s = _crear(servicio)
    antes = datos.clave_suscripcion(ORG, "foundry-eu")
    with pytest.raises(ConflictoVersion):
        _crear(servicio, clave="otra-clave-que-no-debe-pisar")
    with pytest.raises(ConflictoVersion):  # versión vieja
        servicio.guardar(ORG, "foundry-eu", FOUNDRY, _foundry(clave="otra"), s.version + 5, AUDITORIA)
    assert datos.clave_suscripcion(ORG, "foundry-eu") == antes


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://acme.services.ai.azure.com",
        "https://169.254.169.254",
        "https://127.0.0.1",
        "https://evil.example.com",
        "https://acme.services.ai.azure.com.evil.example.com",
        "https://usuario:clave@acme.services.ai.azure.com",
        "https://acme.services.ai.azure.com/?api-key=x",
        "https://services.ai.azure.com",
        "acme.services.ai.azure.com",
    ],
)
def test_el_endpoint_solo_puede_ser_un_recurso_de_azure_por_https(endpoint):
    servicio, datos, _ = _servicio()
    with pytest.raises(ErrorSuscripcion) as exc:
        _crear(servicio, endpoint=endpoint)
    assert exc.value.codigo == "endpoint-no-permitido" and CLAVE not in exc.value.detalle
    assert datos.suscripciones(ORG) == []


def test_el_endpoint_se_guarda_como_origen_y_los_hosts_extra_se_configuran_por_entorno():
    servicio, _, _ = _servicio()
    assert _crear(servicio, endpoint=f"{ENDPOINT}/anthropic/").endpoint == ENDPOINT
    propio = _servicio(politica=politica_foundry({"RAILSPEC_FOUNDRY_HOSTS": "*.privado.example"}))[0]
    assert _crear(propio, endpoint="https://ia.privado.example").endpoint == "https://ia.privado.example"


def test_el_proyecto_acepta_nombre_o_url_del_mismo_recurso():
    servicio, _, _ = _servicio()
    assert _crear(servicio, "a", proyecto=f"{ENDPOINT}/api/projects/mi-proyecto").proyecto == "mi-proyecto"
    for malo in (
        "https://otro.services.ai.azure.com/api/projects/p",
        f"{ENDPOINT}/otra/ruta",
        "con espacios",
        "../x",
    ):
        with pytest.raises(ErrorSuscripcion) as exc:
            _crear(servicio, "b", proyecto=malo)
        assert exc.value.codigo == "proyecto-invalido"


def test_anthropic_no_lleva_endpoint_ni_zona_ni_identidad_del_servidor():
    servicio, _, _ = _servicio()
    s = servicio.guardar(
        ORG, "directo", ANTHROPIC, EntradaSuscripcion(nombre="Directo", clave="sk-ant-x"), None, AUDITORIA
    )
    assert (s.endpoint, s.region, s.zona_datos) == (None, None, None) and s.clave_configurada
    for malo in (
        EntradaSuscripcion(nombre="x", clave="k", endpoint=ENDPOINT),
        EntradaSuscripcion(nombre="x", clave="k", zona_datos="eu"),
        EntradaSuscripcion(nombre="x", autenticacion=AutenticacionSuscripcion.identidad_servidor),
    ):
        with pytest.raises(ErrorSuscripcion):
            servicio.guardar(ORG, "malo", ANTHROPIC, malo, None, AUDITORIA)
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.guardar(ORG, "directo", FOUNDRY, _foundry(), s.version, AUDITORIA)
    assert exc.value.codigo == "proveedor-inmutable"


def test_una_suscripcion_de_otra_organizacion_no_se_ve_ni_se_descifra():
    servicio, datos, _ = _servicio()
    _crear(servicio)
    assert servicio.listar("otra-org") == [] and datos.clave_suscripcion("otra-org", "foundry-eu") is None
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.obtener("otra-org", "foundry-eu")
    assert exc.value.estado == 404


# --- descubrimiento: Foundry --------------------------------------------------------------------


def test_descubrir_foundry_manda_la_clave_y_deriva_la_region_del_sku_y_de_la_suscripcion():
    vistas = []
    servicio, _, _ = _servicio(transporte=_transporte(vistas=vistas))
    _crear(servicio)
    r = _descubrir(servicio)
    (peticion,) = vistas
    assert (
        peticion.url.path == "/api/projects/railspec/deployments"
        and peticion.url.host == "acme-eu.services.ai.azure.com"
    )
    assert peticion.headers["api-key"] == CLAVE
    assert (r.lectura.resultado, r.lectura.modelos, r.lectura.por) == ("ok", 4, "ana")
    por = _por_clave(r.suscripcion)
    assert por["opus-dz"].region == "zona-eu" and por["opus-dz"].sku == "DataZoneStandard"
    assert por["sonnet-gl"].region == "global"
    assert por["haiku-st"].region == "swedencentral"
    # Sin SKU no se puede saber la variante: región sin determinar, no «Standard» por suposición.
    assert por["raro"].region is None
    assert not any(m.seleccionado for m in r.suscripcion.modelos)
    assert all(m.origen == "descubierto" and m.visto_en for m in r.suscripcion.modelos)
    assert por["opus-dz"].capacidades.structured_outputs and Effort.max in por["opus-dz"].capacidades.efforts


def test_descubrir_sin_proyecto_dice_que_hay_que_indicarlo_o_declarar():
    servicio, _, _ = _servicio(transporte=_transporte())
    _crear(servicio, proyecto=None)
    r = _descubrir(servicio)
    assert r.lectura.resultado == "error" and r.lectura.error_codigo == "configuracion"
    assert "proyecto" in r.lectura.error_detalle and "declara" in r.lectura.error_detalle


def test_descubrir_con_la_identidad_del_servidor_no_manda_clave(monkeypatch):
    vistas = []
    servicio, _, _ = _servicio(transporte=_transporte(vistas=vistas))
    _crear(servicio, autenticacion=AutenticacionSuscripcion.identidad_servidor, clave=None)

    async def token():
        return "token-de-entra"

    monkeypatch.setattr("railspec.server.proveedores.foundry.proveedor_token_entra", lambda scope: token)
    assert _descubrir(servicio).lectura.resultado == "ok"
    assert (
        "api-key" not in vistas[0].headers and vistas[0].headers["authorization"] == "Bearer token-de-entra"
    )


@pytest.mark.parametrize(
    ("estado", "codigo"),
    [(401, "autenticacion"), (403, "permiso"), (404, "no-encontrado"), (429, "limite"), (500, "proveedor")],
)
def test_un_error_del_proveedor_se_registra_sin_filtrar_ni_perder_la_eleccion(estado, codigo):
    # El proveedor devuelve la clave en el cuerpo del error (hay APIs que repiten lo recibido).
    servicio, datos, almacen = _servicio(
        transporte=_transporte({"error": f"clave {CLAVE} rechazada"}, estado)
    )
    _crear(servicio)
    s = servicio.declarar(
        ORG,
        "foundry-eu",
        1,
        modelo="claude-opus-5-5",
        despliegue="opus",
        sku="DataZoneStandard",
        efforts=None,
        structured_outputs=None,
        contexto=None,
        auditoria=AUDITORIA,
    )
    r = _descubrir(servicio)
    assert (r.lectura.resultado, r.lectura.error_codigo) == ("error", codigo)
    assert "foundry" in r.lectura.error_detalle.lower() or "Foundry UE" in r.lectura.error_detalle
    for texto in (r.lectura.error_detalle, r.suscripcion.model_dump_json(), _volcado(almacen)):
        assert CLAVE not in texto and "https://" not in r.lectura.error_detalle
    # La elección anterior sigue ahí y la versión sube (la consola refresca).
    assert [m.clave for m in r.suscripcion.modelos if m.seleccionado] == ["opus"]
    assert r.suscripcion.version == s.version + 1
    assert datos.suscripcion(ORG, "foundry-eu").ultima_lectura.error_codigo == codigo


def test_un_error_de_red_o_un_tiempo_agotado_se_clasifican():
    def sin_red(peticion):
        raise httpx.ConnectError(f"no conecta con {peticion.url}")

    servicio, _, _ = _servicio(transporte=httpx.MockTransport(sin_red))
    _crear(servicio)
    assert _descubrir(servicio).lectura.error_codigo == "red"

    async def colgado(s, clave):
        await asyncio.sleep(3600)

    lento, _, _ = _servicio(lector=colgado, tope_lectura_s=0.05)
    _crear(lento)
    r = _descubrir(lento)
    assert r.lectura.error_codigo == "tiempo" and "no respondió a tiempo" in r.lectura.error_detalle


def test_si_falta_la_clave_maestra_descubrir_lo_dice_y_no_llama_al_proveedor():
    llamadas = []
    servicio, datos, almacen = _servicio()
    _crear(servicio)
    sin_maestra = ServicioSuscripciones(datos, None, lector=lambda s, c: llamadas.append(1))
    r = correr(sin_maestra.descubrir(ORG, "foundry-eu", "ana", AUDITORIA))
    assert r.lectura.error_codigo == "clave-maestra-ausente" and llamadas == []


def test_una_clave_cifrada_con_otra_maestra_se_explica_y_no_se_usa():
    servicio, datos, _ = _servicio()
    _crear(servicio)
    otra = ServicioSuscripciones(datos, _cifrador(), lector=lambda s, c: pytest.fail("no debe llamar"))
    r = correr(otra.descubrir(ORG, "foundry-eu", "ana", AUDITORIA))
    assert r.lectura.error_codigo == "clave-maestra-desconocida"


def test_descubrir_una_suscripcion_deshabilitada_se_rechaza():
    servicio, _, _ = _servicio(transporte=_transporte())
    _crear(servicio, habilitada=False)
    with pytest.raises(ErrorSuscripcion) as exc:
        _descubrir(servicio)
    assert exc.value.codigo == "suscripcion-deshabilitada"


# --- descubrimiento: Anthropic ------------------------------------------------------------------


def _modelo_anthropic(id_, efforts=("low", "medium", "high"), contexto=200_000):
    apoyo = lambda **kw: {"supported": True, **kw}  # noqa: E731
    return SimpleNamespace(
        id=id_,
        max_input_tokens=contexto,
        capabilities={
            "effort": {e: apoyo() for e in efforts} | {"supported": True},
            "thinking": apoyo(),
            "structured_outputs": apoyo(),
        },
    )


class ClienteAnthropicDoble:
    cierres = 0
    opciones: dict = {}
    clave = None

    def __init__(self, modelos):
        self.models = SimpleNamespace(list=lambda: self._listar(modelos))

    @staticmethod
    async def _listar(modelos):
        for m in modelos:
            yield m

    async def close(self):
        ClienteAnthropicDoble.cierres += 1


def test_descubrir_anthropic_lista_los_modelos_de_la_api_con_la_clave_de_la_suscripcion(monkeypatch):
    def cliente(clave, **opciones):
        ClienteAnthropicDoble.clave, ClienteAnthropicDoble.opciones = clave, opciones
        return ClienteAnthropicDoble(
            [_modelo_anthropic("claude-opus-5-5"), _modelo_anthropic("claude-sonnet-5-5")]
        )

    monkeypatch.setattr("railspec.server.proveedores.claude.cliente_anthropic", cliente)
    servicio, _, _ = _servicio()
    servicio.guardar(
        ORG,
        "directo",
        ANTHROPIC,
        EntradaSuscripcion(nombre="Directo", clave="sk-ant-directa"),
        None,
        AUDITORIA,
    )
    r = correr(servicio.descubrir(ORG, "directo", "ana", AUDITORIA))
    assert ClienteAnthropicDoble.clave == "sk-ant-directa"
    assert ClienteAnthropicDoble.opciones["timeout"] <= 30 and ClienteAnthropicDoble.cierres == 1
    assert [m.clave for m in r.suscripcion.modelos] == ["claude-opus-5-5", "claude-sonnet-5-5"]
    m = r.suscripcion.modelos[0]
    assert (m.despliegue, m.sku, m.region, m.origen) == (None, None, None, "descubierto")
    assert m.capacidades.structured_outputs and Effort.high in m.capacidades.efforts


def test_un_401_de_anthropic_dice_que_la_clave_fue_rechazada(monkeypatch):
    class Rechazo(Exception):
        status_code = 401

    class Mala:
        models = SimpleNamespace(list=lambda: Mala._f())

        @staticmethod
        async def _f():
            raise Rechazo(f"invalid x-api-key {CLAVE}")
            yield

        async def close(self):
            pass

    monkeypatch.setattr("railspec.server.proveedores.claude.cliente_anthropic", lambda clave, **kw: Mala())
    servicio, _, almacen = _servicio()
    servicio.guardar(
        ORG, "directo", ANTHROPIC, EntradaSuscripcion(nombre="Directo", clave=CLAVE), None, AUDITORIA
    )
    r = correr(servicio.descubrir(ORG, "directo", "ana", AUDITORIA))
    assert r.lectura.error_codigo == "autenticacion" and "rechazó la clave" in r.lectura.error_detalle
    assert CLAVE not in r.lectura.error_detalle and CLAVE not in _volcado(almacen)


# --- modelos: mezcla, declarar, elegir ----------------------------------------------------------


def _elegir(servicio, *claves, id_="foundry-eu"):
    s = servicio.obtener(ORG, id_)
    return servicio.seleccionar(ORG, id_, s.version, list(claves), AUDITORIA)


def test_seleccionar_deja_exactamente_los_pedidos_y_rechaza_lo_desconocido():
    servicio, _, _ = _servicio(transporte=_transporte())
    _crear(servicio)
    _descubrir(servicio)
    s = _elegir(servicio, "opus-dz", "haiku-st")
    assert {m.clave for m in s.modelos if m.seleccionado} == {"opus-dz", "haiku-st"}
    s = _elegir(servicio, "opus-dz")
    assert {m.clave for m in s.modelos if m.seleccionado} == {"opus-dz"}
    with pytest.raises(ErrorSuscripcion) as exc:
        _elegir(servicio, "opus-dz", "no-existe")
    assert exc.value.codigo == "modelo-desconocido" and "no-existe" in exc.value.detalle
    with pytest.raises(ConflictoVersion):  # versión vieja: otra persona cambió la suscripción
        servicio.seleccionar(ORG, "foundry-eu", 1, [], AUDITORIA)
    assert _elegir(servicio).modelos[0].seleccionado is False  # vaciar la elección vale


def test_descubrir_de_nuevo_conserva_la_eleccion_marca_ausentes_y_respeta_lo_declarado():
    cuerpo = {"actual": CUERPO_FOUNDRY}
    servicio, _, _ = _servicio(
        transporte=httpx.MockTransport(lambda p: httpx.Response(200, json=cuerpo["actual"]))
    )
    _crear(servicio)
    _descubrir(servicio)
    _elegir(servicio, "opus-dz", "haiku-st")
    s = servicio.obtener(ORG, "foundry-eu")
    servicio.declarar(
        ORG,
        "foundry-eu",
        s.version,
        modelo="gpt-5",
        despliegue="gpt-dz",
        sku="DataZoneStandard",
        efforts=[Effort.low],
        structured_outputs=True,
        contexto=100_000,
        auditoria=AUDITORIA,
    )
    # El proveedor ya no lista haiku (elegido) ni sonnet (sin elegir) y trae un modelo nuevo.
    cuerpo["actual"] = _despliegues(
        ("opus-dz", "claude-opus-5-5", "DataZoneStandard"), ("nuevo", "claude-opus-5", "DataZoneStandard")
    )
    por = _por_clave(_descubrir(servicio).suscripcion)
    assert por["opus-dz"].seleccionado and not por["opus-dz"].ausente
    assert por["haiku-st"].seleccionado and por["haiku-st"].ausente  # sigue marcado, pero ausente
    assert "sonnet-gl" not in por and "raro" not in por  # lo no elegido que desaparece, se va
    assert por["nuevo"].seleccionado is False
    assert por["gpt-dz"].origen == "declarado" and por["gpt-dz"].seleccionado
    with pytest.raises(ErrorSuscripcion) as exc:  # un ausente solo se puede desmarcar
        _elegir(servicio, "haiku-st")
    assert exc.value.codigo == "modelo-ausente"
    assert servicio.activa(ORG, "foundry-eu").entradas  # y no cuenta para elegir
    assert {e.destino for e in servicio.activa(ORG, "foundry-eu").entradas} == {"opus-dz", "gpt-dz"}


def test_declarar_a_mano_deriva_la_region_del_sku_y_retirar_solo_vale_para_lo_declarado():
    servicio, _, _ = _servicio(transporte=_transporte())
    _crear(servicio)
    s = servicio.declarar(
        ORG,
        "foundry-eu",
        1,
        modelo="claude-opus-5-5",
        despliegue="mi-opus",
        sku="GlobalStandard",
        efforts=[Effort.high],
        structured_outputs=None,
        contexto=500_000,
        auditoria=AUDITORIA,
    )
    (m,) = s.modelos
    assert (m.origen, m.seleccionado, m.region, m.sku) == ("declarado", True, "global", "GlobalStandard")
    assert m.capacidades.efforts == [Effort.high] and m.capacidades.contexto_max_tokens == 500_000
    assert m.capacidades.structured_outputs  # lo conocido del modelo se conserva
    # Redeclarar la misma clave la reemplaza (corregir el SKU), no la duplica.
    s = servicio.declarar(
        ORG,
        "foundry-eu",
        s.version,
        modelo="claude-opus-5-5",
        despliegue="mi-opus",
        sku="DataZoneStandard",
        efforts=None,
        structured_outputs=None,
        contexto=None,
        auditoria=AUDITORIA,
    )
    assert [(m.clave, m.region) for m in s.modelos] == [("mi-opus", "zona-eu")]
    for malo in ("Data Zone", "", "a-b"):
        with pytest.raises(ErrorSuscripcion) as exc:
            servicio.declarar(
                ORG,
                "foundry-eu",
                s.version,
                modelo="x",
                despliegue="y",
                sku=malo,
                efforts=None,
                structured_outputs=None,
                contexto=None,
                auditoria=AUDITORIA,
            )
        assert exc.value.codigo == "sku-invalido"
    with pytest.raises(ErrorSuscripcion):
        servicio.retirar(ORG, "foundry-eu", s.version, "no-esta", AUDITORIA)
    assert servicio.retirar(ORG, "foundry-eu", s.version, "mi-opus", AUDITORIA).modelos == []


def test_editar_la_zona_o_la_region_recalcula_la_region_de_cada_modelo():
    servicio, _, _ = _servicio(transporte=_transporte())
    s = _crear(servicio)
    _descubrir(servicio)
    s = servicio.obtener(ORG, "foundry-eu")
    s = servicio.guardar(
        ORG,
        "foundry-eu",
        FOUNDRY,
        _foundry(zona_datos="us", region="eastus2", clave=None),
        s.version,
        AUDITORIA,
    )
    por = _por_clave(s)
    assert (
        por["opus-dz"].region == "zona-us"
        and por["haiku-st"].region == "eastus2"
        and por["sonnet-gl"].region == "global"
    )
    sin_zona = servicio.guardar(
        ORG, "foundry-eu", FOUNDRY, _foundry(zona_datos=None, region=None, clave=None), s.version, AUDITORIA
    )
    por = _por_clave(sin_zona)
    assert por["opus-dz"].region is None and por["haiku-st"].region is None  # sin zona no se puede asegurar


# --- selección de proveedor por suscripción y política de datos ---------------------------------


def _con_modelos(servicio, **declarados):
    """Suscripción de Foundry con un despliegue declarado por cada ``nombre=(modelo, sku)``."""

    _crear(servicio)
    for nombre, (modelo, sku) in declarados.items():
        s = servicio.obtener(ORG, "foundry-eu")
        servicio.declarar(
            ORG,
            "foundry-eu",
            s.version,
            modelo=modelo,
            despliegue=nombre,
            sku=sku,
            efforts=None,
            structured_outputs=None,
            contexto=None,
            auditoria=AUDITORIA,
        )


def _proveedores(servicio):
    return Proveedores({}, suscripciones=servicio)


REQ = RequisitoRol(
    modelo={FOUNDRY: "claude-opus-5-5", ANTHROPIC: "claude-opus-5-5"},
    effort=Effort.high,
    structured_outputs=True,
)


def test_elegir_con_suscripcion_usa_su_despliegue_su_region_y_su_adaptador():
    doble = ProveedorGuionado(lambda p: None)
    servicio, _, _ = _servicio(fabrica=lambda s, clave: doble)
    _con_modelos(servicio, opus=("claude-opus-5-5", "DataZoneStandard"))
    e = _proveedores(servicio).elegir(
        "redactor", REQ, NivelCodigo.restringido, org=ORG, suscripcion="foundry-eu"
    )
    assert (e.modelo, e.despliegue, e.region, e.suscripcion) == (
        "claude-opus-5-5",
        "opus",
        "zona-eu",
        "foundry-eu",
    )
    assert e.proveedor is doble
    # También por el nombre del despliegue.
    por_nombre = RequisitoRol(modelo={FOUNDRY: "opus"}, structured_outputs=True)
    assert (
        _proveedores(servicio)
        .elegir("r", por_nombre, NivelCodigo.abierto, org=ORG, suscripcion="foundry-eu")
        .despliegue
        == "opus"
    )


def test_un_modelo_que_la_suscripcion_no_tiene_elegido_no_se_usa():
    servicio, _, _ = _servicio(
        fabrica=lambda s, c: ProveedorGuionado(lambda p: None), transporte=_transporte()
    )
    _crear(servicio)
    _descubrir(servicio)  # descubiertos pero ninguno elegido
    with pytest.raises(PerfilInsatisfacible) as exc:
        _proveedores(servicio).elegir("redactor", REQ, NivelCodigo.abierto, org=ORG, suscripcion="foundry-eu")
    assert "no está entre los modelos elegidos" in str(exc.value) and "Foundry UE" in str(exc.value)


@pytest.mark.parametrize("nivel", [NivelCodigo.restringido, NivelCodigo.interno])
def test_restringido_e_interno_solo_aceptan_foundry_en_la_zona_del_workspace(nivel):
    servicio, _, _ = _servicio(fabrica=lambda s, c: ProveedorGuionado(lambda p: None))
    _con_modelos(
        servicio,
        dz=("claude-opus-5-5", "DataZoneStandard"),
        st=("claude-sonnet-5-5", "Standard"),
        gl=("claude-haiku-4-5", "GlobalStandard"),
        sin=("gpt-5", "Standard"),
    )
    p = _proveedores(servicio)
    pide = lambda modelo, zona: p.elegir(  # noqa: E731
        "r", RequisitoRol(modelo={FOUNDRY: modelo}), nivel, org=ORG, zona=zona, suscripcion="foundry-eu"
    )
    assert pide("dz", "eu").region == "zona-eu"  # DataZone en la zona del workspace
    assert pide("st", "eu").region == "swedencentral"  # Standard: la región del recurso, cuya zona es eu
    assert pide("st", "swedencentral").region == "swedencentral"
    assert pide("dz", None).region == "zona-eu"  # workspace sin zona declarada: vale cualquier zona fija
    for modelo, zona, fragmento in (
        ("dz", "us", "fuera de la zona de datos"),
        ("st", "us", "fuera de la zona de datos"),
        ("gl", "eu", "fuera de la zona de datos"),  # Global nunca
        ("gl", None, "fuera de la zona de datos"),
    ):
        with pytest.raises(PerfilInsatisfacible, match=fragmento):
            pide(modelo, zona)
    # En abierto la zona no importa, ni siquiera Global.
    abierto = p.elegir(
        "r",
        RequisitoRol(modelo={FOUNDRY: "gl"}),
        NivelCodigo.abierto,
        org=ORG,
        zona="us",
        suscripcion="foundry-eu",
    )
    assert abierto.region == "global"


def test_sin_sku_o_sin_zona_la_region_queda_sin_determinar_y_no_sirve_a_restringido():
    servicio, _, _ = _servicio(
        fabrica=lambda s, c: ProveedorGuionado(lambda p: None), transporte=_transporte()
    )
    _crear(servicio, zona_datos=None)
    _descubrir(servicio)
    _elegir(servicio, "opus-dz", "raro", "haiku-st")
    p = _proveedores(servicio)
    for modelo in ("opus-dz", "raro"):  # DataZone sin zona declarada; sin SKU
        with pytest.raises(PerfilInsatisfacible, match="región desconocida"):
            p.elegir(
                "r",
                RequisitoRol(modelo={FOUNDRY: modelo}),
                NivelCodigo.restringido,
                org=ORG,
                suscripcion="foundry-eu",
            )
    # Standard sí: la región del recurso basta cuando el workspace no declara zona.
    assert (
        p.elegir(
            "r",
            RequisitoRol(modelo={FOUNDRY: "haiku-st"}),
            NivelCodigo.restringido,
            org=ORG,
            suscripcion="foundry-eu",
        ).region
        == "swedencentral"
    )


def test_anthropic_directo_solo_sirve_a_repositorios_abiertos(monkeypatch):
    doble = ProveedorGuionado(lambda p: None, proveedor=ANTHROPIC)
    servicio, _, _ = _servicio(fabrica=lambda s, c: doble)
    servicio.guardar(
        ORG, "directo", ANTHROPIC, EntradaSuscripcion(nombre="Directo", clave="k"), None, AUDITORIA
    )
    s = servicio.obtener(ORG, "directo")
    # Un modelo descubierto y elegido (lector doble).
    servicio._lector = lambda s, c: asyncio.sleep(0, result=[_entrada_anthropic("claude-opus-5-5")])
    correr(servicio.descubrir(ORG, "directo", "ana", AUDITORIA))
    servicio.seleccionar(
        ORG, "directo", servicio.obtener(ORG, "directo").version, ["claude-opus-5-5"], AUDITORIA
    )
    p = _proveedores(servicio)
    assert p.elegir("r", REQ, NivelCodigo.abierto, org=ORG, suscripcion="directo").proveedor is doble
    for nivel in (NivelCodigo.restringido, NivelCodigo.interno):
        with pytest.raises(PerfilInsatisfacible, match="solo sirve a repositorios abiertos"):
            p.elegir("r", REQ, nivel, org=ORG, suscripcion="directo")
    assert s.proveedor == ANTHROPIC


def _entrada_anthropic(modelo):
    from railspec.server.proveedores.catalogo import EntradaCatalogo

    return EntradaCatalogo(
        ANTHROPIC,
        modelo,
        None,
        "anthropic",
        None,
        Capacidades(efforts=list(Effort), structured_outputs=True, contexto_max_tokens=1_000_000),
    )


def test_el_requisito_se_comprueba_contra_las_capacidades_del_modelo_elegido():
    servicio, _, _ = _servicio(fabrica=lambda s, c: ProveedorGuionado(lambda p: None))
    _con_modelos(servicio, gpt=("gpt-4.1", "DataZoneStandard"))  # sin effort y sin gpt-5
    p = _proveedores(servicio)
    with pytest.raises(PerfilInsatisfacible, match="no admite effort high"):
        p.elegir(
            "r",
            RequisitoRol(modelo={FOUNDRY: "gpt"}, effort=Effort.high),
            NivelCodigo.abierto,
            org=ORG,
            suscripcion="foundry-eu",
        )


def test_suscripcion_inexistente_deshabilitada_o_sin_servicio_es_perfil_insatisfacible():
    servicio, _, _ = _servicio(fabrica=lambda s, c: ProveedorGuionado(lambda p: None))
    _con_modelos(servicio, opus=("claude-opus-5-5", "DataZoneStandard"))
    p = _proveedores(servicio)
    with pytest.raises(PerfilInsatisfacible, match="no existe"):
        p.elegir("r", REQ, NivelCodigo.abierto, org=ORG, suscripcion="fantasma")
    s = servicio.obtener(ORG, "foundry-eu")
    servicio.guardar(ORG, "foundry-eu", FOUNDRY, _foundry(habilitada=False, clave=None), s.version, AUDITORIA)
    with pytest.raises(PerfilInsatisfacible, match="deshabilitada"):
        p.elegir("r", REQ, NivelCodigo.abierto, org=ORG, suscripcion="foundry-eu")
    for sin in (Proveedores({}), p):
        with pytest.raises(PerfilInsatisfacible):
            sin.elegir("r", REQ, NivelCodigo.abierto, org=None if sin is p else ORG, suscripcion="foundry-eu")


def test_el_adaptador_se_reutiliza_hasta_que_la_suscripcion_cambia():
    creados = []
    servicio, _, _ = _servicio(
        fabrica=lambda s, c: creados.append(s.version) or ProveedorGuionado(lambda p: None)
    )
    _con_modelos(servicio, opus=("claude-opus-5-5", "DataZoneStandard"))
    p = _proveedores(servicio)
    for _ in range(3):
        p.elegir("r", REQ, NivelCodigo.abierto, org=ORG, suscripcion="foundry-eu")
    assert len(creados) == 1
    s = servicio.obtener(ORG, "foundry-eu")
    servicio.guardar(
        ORG, "foundry-eu", FOUNDRY, _foundry(clave="rotada"), s.version, AUDITORIA
    )  # rotar la clave
    p.elegir("r", REQ, NivelCodigo.abierto, org=ORG, suscripcion="foundry-eu")
    assert len(creados) == 2


def test_sin_suscripcion_rige_el_catalogo_del_servidor_como_antes():
    doble = ProveedorGuionado(lambda p: None)
    p = Proveedores({FOUNDRY: doble})
    e = p.elegir("r", REQ, NivelCodigo.abierto)
    assert e.proveedor is doble and e.suscripcion is None


def test_el_perfil_valida_el_contrato_de_la_suscripcion():
    s = SuscripcionModelo(
        version=1, auditoria=AUDITORIA, org=ORG, id="x", nombre="x", proveedor=FOUNDRY, endpoint=ENDPOINT
    )
    assert s.proveedor == FOUNDRY
    with pytest.raises(ValidationError, match="exige endpoint"):
        SuscripcionModelo(version=1, auditoria=AUDITORIA, org=ORG, id="x", nombre="x", proveedor=FOUNDRY)
    with pytest.raises(ValidationError, match="repetidos"):
        modelo = {"modelo": "m", "origen": "declarado", "capacidades": {"contexto_max_tokens": 1}}
        SuscripcionModelo.model_validate({**s.model_dump(), "modelos": [modelo, modelo]})
