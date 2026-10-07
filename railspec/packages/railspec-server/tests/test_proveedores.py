"""Adaptadores reales (con clientes dobles), catálogo, selección sin restricción por nivel y perfiles."""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import httpx
import pytest
from apoyo_motor import JULIAN, ORG, WS_ALCANCE, construir, entrada_start
from railspec.contracts.comun import AlcanceWorkspace, Effort, GateFase, NivelCodigo, Perfil, Proveedor
from railspec.contracts.repositorio import Auditoria, Capacidades, RequisitoRol, Workspace
from railspec.contracts.tools import CodigoError
from railspec.server.estado import almacen_en_memoria
from railspec.server.motor import ErrorNegocio
from railspec.server.motor.gate import SalidaCritico
from railspec.server.motor.perfiles import perfil_por_defecto, requisito
from railspec.server.proveedores import Catalogo, ErrorProveedor, PeticionModelo, Proveedores
from railspec.server.proveedores.catalogo import (
    EntradaCatalogo,
    FuenteAnthropic,
    FuenteFoundryDeclarada,
    FuenteFoundryProyecto,
)
from railspec.server.proveedores.chat_completions import AdaptadorChatCompletions, cliente_azure_openai
from railspec.server.proveedores.claude import AdaptadorClaude, cliente_foundry
from railspec.server.proveedores.falso import ProveedorGuionado
from railspec.server.proveedores.foundry import ProveedorFoundry
from railspec.server.proveedores.seleccion import PerfilInsatisfacible


def correr(coro):
    return asyncio.run(coro)


def _peticion(**extra):
    datos = dict(
        rol="critico-profundo",
        modelo="claude-opus-5-5",
        sistema="rúbrica estable",
        contenido="material",
        esquema=SalidaCritico,
        effort=Effort.high,
    )
    datos.update(extra)
    return PeticionModelo(**datos)


# --- Claude (Messages API) -----------------------------------------------------------------


class ClienteClaude:
    def __init__(self, respuesta=None, error=None):
        self.llamadas = []
        self._respuesta, self._error = respuesta, error
        self.messages = self

    async def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error:
            raise self._error
        return self._respuesta


VACIA = SalidaCritico(hallazgos=[])


def _mensaje(valor=VACIA, stop="end_turn", **uso):
    u = dict(input_tokens=1000, output_tokens=100, cache_read_input_tokens=800, cache_creation_input_tokens=0)
    u.update(uso)
    return SimpleNamespace(parsed_output=valor, stop_reason=stop, usage=SimpleNamespace(**u))


def test_claude_arma_la_peticion_y_lee_el_uso():
    cliente = ClienteClaude(_mensaje())
    adaptador = AdaptadorClaude(Proveedor.foundry, cliente, region="eastus2")
    r = correr(adaptador.completar(_peticion(despliegue="opus-dz", region="zona-us")))
    kw = cliente.llamadas[0]
    assert kw["model"] == "opus-dz"
    assert kw["system"][0]["cache_control"] == {"type": "ephemeral"}
    assert kw["output_format"] is SalidaCritico and kw["output_config"] == {"effort": "high"}
    assert "thinking" not in kw and kw["messages"] == [{"role": "user", "content": "material"}]
    assert r.modelo == "claude-opus-5-5" and r.region == "zona-us"
    assert r.uso.tokens_entrada == 1000 and r.uso.tokens_cache_lectura == 800 and r.uso.costo_usd > 0
    # Sin región en la petición, la del adaptador.
    assert correr(adaptador.completar(_peticion())).region == "eastus2"


def test_claude_convierte_fallos_en_error_proveedor():
    def correr_con(cliente):
        return correr(AdaptadorClaude(Proveedor.foundry, cliente).completar(_peticion(effort=None)))

    with pytest.raises(ErrorProveedor) as exc:
        correr_con(ClienteClaude(error=RuntimeError("429")))
    assert "RuntimeError" in str(exc.value) and exc.value.duracion_ms >= 0
    for stop in ("refusal", "max_tokens"):
        with pytest.raises(ErrorProveedor, match="cortada"):
            correr_con(ClienteClaude(_mensaje(stop=stop)))
    with pytest.raises(ErrorProveedor, match="sin salida"):
        correr_con(ClienteClaude(_mensaje(valor=None)))
    with pytest.raises(ErrorProveedor, match="esquema"):
        correr_con(ClienteClaude(_mensaje(valor={"hallazgos": "no"})))
    # Un dict válido se valida contra el esquema.
    assert correr_con(ClienteClaude(_mensaje(valor={"hallazgos": []}))).valor == SalidaCritico(hallazgos=[])


def test_clientes_reales_apuntan_al_recurso():
    c = cliente_foundry("https://acme.services.ai.azure.com/", "clave", None)
    assert str(c.base_url) == "https://acme.services.ai.azure.com/anthropic/"
    c = cliente_foundry("https://acme.services.ai.azure.com/anthropic", None, lambda: "token")
    assert str(c.base_url) == "https://acme.services.ai.azure.com/anthropic/"
    o = cliente_azure_openai("https://acme.services.ai.azure.com", "clave", None)
    assert str(o.base_url) == "https://acme.services.ai.azure.com/openai/v1/"
    with pytest.raises(ValueError):
        cliente_foundry("https://acme.services.ai.azure.com", None, None)


# --- Chat completions ----------------------------------------------------------------------


class ClienteChat:
    def __init__(self, respuesta=None, error=None):
        self.llamadas = []
        self._respuesta, self._error = respuesta, error
        self.chat = SimpleNamespace(completions=self)

    async def parse(self, **kwargs):
        self.llamadas.append(kwargs)
        if self._error:
            raise self._error
        return self._respuesta


def _completion(valor=VACIA, fin="stop", refusal=None):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=fin, message=SimpleNamespace(parsed=valor, refusal=refusal))],
        usage=SimpleNamespace(
            prompt_tokens=1200,
            completion_tokens=50,
            prompt_tokens_details=SimpleNamespace(cached_tokens=1000),
        ),
    )


def test_chat_completions_arma_la_peticion_y_lee_el_uso():
    cliente = ClienteChat(_completion())
    adaptador = AdaptadorChatCompletions(Proveedor.foundry, cliente, region="eastus2")
    r = correr(adaptador.completar(_peticion(modelo="gpt-5", despliegue="gpt5-dz", effort=Effort.medium)))
    kw = cliente.llamadas[0]
    assert kw["model"] == "gpt5-dz" and kw["response_format"] is SalidaCritico
    assert kw["reasoning_effort"] == "medium" and kw["messages"][0]["role"] == "system"
    assert r.uso.tokens_entrada == 200 and r.uso.tokens_cache_lectura == 1000 and r.uso.tokens_salida == 50
    correr(adaptador.completar(_peticion(effort=None)))
    assert "reasoning_effort" not in cliente.llamadas[1]


def test_chat_completions_fallos():
    def correr_con(cliente, **extra):
        return correr(AdaptadorChatCompletions(Proveedor.foundry, cliente).completar(_peticion(**extra)))

    with pytest.raises(ErrorProveedor, match="no existe en chat"):
        correr_con(ClienteChat(_completion()), effort=Effort.xhigh)
    with pytest.raises(ErrorProveedor, match="cortada"):
        correr_con(ClienteChat(_completion(fin="length")), effort=None)
    with pytest.raises(ErrorProveedor, match="rechazo"):
        correr_con(ClienteChat(_completion(valor=None, refusal="no")), effort=None)
    with pytest.raises(ErrorProveedor):
        correr_con(ClienteChat(error=RuntimeError("401")), effort=None)


def test_foundry_enruta_por_modelo():
    claude = ProveedorGuionado(lambda p: SalidaCritico(hallazgos=[]))
    chat = ProveedorGuionado(lambda p: SalidaCritico(hallazgos=[]))
    f = ProveedorFoundry(claude, chat, "eastus2")
    correr(f.completar(_peticion(modelo="claude-sonnet-5-5")))
    correr(f.completar(_peticion(modelo="gpt-5")))
    assert [p.modelo for p in claude.peticiones] == ["claude-sonnet-5-5"]
    assert [p.modelo for p in chat.peticiones] == ["gpt-5"]


# --- Catálogo ------------------------------------------------------------------------------


def test_despliegues_declarados_y_region_por_sku():
    compacta = FuenteFoundryDeclarada(
        "opus-g=claude-opus-5-5:GlobalStandard, opus-dz=claude-opus-5-5:DataZoneStandard,"
        " gpt=gpt-5, raro=modelo-x",
        "eastus2",
        "us",
    )
    e = {x.despliegue: x for x in correr(compacta.leer())}
    assert (
        e["opus-g"].region == "global" and e["opus-dz"].region == "zona-us" and e["gpt"].region == "eastus2"
    )
    assert e["opus-dz"].capacidades.structured_outputs and Effort.max in e["opus-dz"].capacidades.efforts
    assert not e["raro"].capacidades.structured_outputs  # desconocido: hasta declararlo
    js = FuenteFoundryDeclarada(
        json.dumps(
            [
                {
                    "despliegue": "raro",
                    "modelo": "modelo-x",
                    "sku": "Standard",
                    "structured_outputs": True,
                    "efforts": ["low"],
                    "contexto": 32000,
                }
            ]
        ),
        "westeurope",
        "eu",
    )
    (x,) = correr(js.leer())
    assert x.capacidades.structured_outputs and x.capacidades.efforts == [Effort.low]
    assert x.capacidades.contexto_max_tokens == 32000 and x.region == "westeurope"
    with pytest.raises(ValueError):
        FuenteFoundryDeclarada("sin-igual", None, None).entradas()


def test_catalogo_del_proyecto_por_api_pagina_y_autentica():
    vistas = []

    def responder(request: httpx.Request) -> httpx.Response:
        vistas.append(request)
        if "pagina=2" in str(request.url):
            return httpx.Response(
                200, json={"value": [{"name": "gpt5", "modelName": "gpt-5", "sku": {"name": "Standard"}}]}
            )
        return httpx.Response(
            200,
            json={
                "value": [
                    {
                        "name": "opus",
                        "modelName": "claude-opus-5-5",
                        "modelPublisher": "Anthropic",
                        "sku": {"name": "DataZoneStandard"},
                        "type": "ModelDeployment",
                    },
                    {"name": "conexion", "type": "Connection"},
                    {"sin": "nombre"},
                ],
                "nextLink": "https://acme.services.ai.azure.com/api/projects/p/deployments?pagina=2",
            },
        )

    async def token():
        return "tok"

    fuente = FuenteFoundryProyecto(
        "https://acme.services.ai.azure.com/api/projects/p",
        None,
        "eastus2",
        "us",
        proveedor_token=token,
        transporte=httpx.MockTransport(responder),
    )
    entradas = correr(fuente.leer())
    assert [(e.despliegue, e.region) for e in entradas] == [("opus", "zona-us"), ("gpt5", "eastus2")]
    assert vistas[0].headers["authorization"] == "Bearer tok"
    assert vistas[0].url.params["api-version"] == "v1"
    con_clave = FuenteFoundryProyecto(
        "https://x/api/projects/p", "k", None, None, transporte=httpx.MockTransport(responder)
    )
    correr(con_clave.leer())
    assert vistas[-1].headers["api-key"] == "k"


def test_catalogo_de_anthropic_lee_capacidades():
    class Paginas:
        def __init__(self, modelos):
            self._m = modelos

        def __aiter__(self):
            async def gen():
                for m in self._m:
                    yield m

            return gen()

    caps = {
        "structured_outputs": {"supported": True},
        "thinking": {"supported": True},
        "effort": {
            "supported": True,
            "low": {"supported": True},
            "high": {"supported": True},
            "max": {"supported": False},
        },
    }
    cliente = SimpleNamespace(
        models=SimpleNamespace(
            list=lambda: Paginas(
                [SimpleNamespace(id="claude-opus-5-5", max_input_tokens=1_000_000, capabilities=caps)]
            )
        )
    )
    (e,) = correr(FuenteAnthropic(cliente).leer())
    assert (
        e.hosting == "anthropic" and e.region is None and e.capacidades.efforts == [Effort.low, Effort.high]
    )
    assert e.capacidades.structured_outputs and e.capacidades.contexto_max_tokens == 1_000_000


class FuenteContada:
    proveedor = Proveedor.foundry

    def __init__(self, entradas, fallar=False):
        self.entradas, self.fallar, self.lecturas = entradas, fallar, 0

    async def leer(self):
        self.lecturas += 1
        if self.fallar:
            raise RuntimeError("Foundry caído")
        return list(self.entradas)


def _entrada(despliegue, modelo="claude-opus-5-5", region="zona-us", hosting="azure", efforts=None):
    return EntradaCatalogo(
        Proveedor.foundry if hosting == "azure" else Proveedor.anthropic,
        modelo,
        despliegue,
        hosting,
        region,
        Capacidades(
            efforts=efforts if efforts is not None else list(Effort),
            structured_outputs=True,
            contexto_max_tokens=1_000_000,
        ),
    )


def test_catalogo_cachea_persiste_y_sobrevive_a_un_fallo():
    almacen = almacen_en_memoria()
    reloj = [0.0]
    fuente = FuenteContada([_entrada("opus-dz")])
    cat = Catalogo([fuente], almacen, ttl_s=60, monotono=lambda: reloj[0])
    correr(cat.refrescar("acme"))
    correr(cat.refrescar("acme"))
    assert fuente.lecturas == 1
    (guardado,) = almacen.catalogo("acme")
    assert guardado.despliegue == "opus-dz" and guardado.org == "acme" and guardado.leido_en.tzinfo
    correr(cat.refrescar("otra"))
    assert len(almacen.catalogo("otra")) == 1 and fuente.lecturas == 1
    # Caduca y la fuente falla: rige la última lectura.
    reloj[0] = 120
    fuente.fallar = True
    correr(cat.refrescar("acme"))
    assert cat.buscar(Proveedor.foundry, "opus-dz") and "caído" in cat.error(Proveedor.foundry)
    # Tras un reinicio con la fuente caída: la copia guardada en Mongo.
    nuevo = Catalogo([FuenteContada([], fallar=True)], almacen, ttl_s=60)
    correr(nuevo.refrescar("acme"))
    assert [e.despliegue for e in nuevo.entradas()] == ["opus-dz"]


# --- Selección: el nivel de código no restringe proveedor, modelo ni región ---


def _proveedores(entradas, *, anthropic=False):
    disponibles = {Proveedor.foundry: ProveedorGuionado(lambda p: None, region="eastus2")}
    fuentes = [FuenteContada([e for e in entradas if e.proveedor == Proveedor.foundry])]
    if anthropic:
        disponibles[Proveedor.anthropic] = ProveedorGuionado(
            lambda p: None, Proveedor.anthropic, region="global"
        )

        class FuenteA(FuenteContada):
            proveedor = Proveedor.anthropic

        fuentes.append(FuenteA([e for e in entradas if e.proveedor == Proveedor.anthropic]))
    return Proveedores(disponibles, Catalogo(fuentes))


REQ = RequisitoRol(
    modelo={Proveedor.foundry: "claude-opus-5-5", Proveedor.anthropic: "claude-opus-5-5"},
    effort=Effort.high,
    structured_outputs=True,
)


def test_la_region_del_despliegue_se_audita_pero_no_restringe_a_ningun_repositorio():
    p = _proveedores([_entrada("opus-g", region="global")])
    with pytest.raises(PerfilInsatisfacible, match="sin leer"):
        p.elegir("critico-profundo", REQ)
    correr(p.refrescar("acme"))
    # Un despliegue Global sirve igual: ya no hay comprobación de región ni de zona.
    e = p.elegir("critico-profundo", REQ)
    assert (e.modelo, e.despliegue, e.region) == ("claude-opus-5-5", "opus-g", "global")

    p = _proveedores([_entrada("opus-g", region="global"), _entrada("opus-dz", region="zona-us")])
    correr(p.refrescar())
    assert p.elegir("critico-profundo", REQ).despliegue in {"opus-g", "opus-dz"}  # ambos sirven
    # Por nombre de despliegue también.
    req = REQ.model_copy(update={"modelo": {Proveedor.foundry: "opus-dz"}})
    e = p.elegir("x", req)
    assert (e.modelo, e.region) == ("claude-opus-5-5", "zona-us")


def test_elegir_ya_no_recibe_nivel_ni_zona():
    import inspect

    for metodo in (Proveedores.elegir, Proveedores.validar):
        assert {"nivel", "zona"}.isdisjoint(inspect.signature(metodo).parameters)
    assert "zona_recurso" not in inspect.signature(Proveedores).parameters


def test_requisitos_del_rol_contra_el_catalogo():
    p = _proveedores([_entrada("gpt", modelo="gpt-5", efforts=[Effort.low, Effort.medium, Effort.high])])
    correr(p.refrescar())
    with pytest.raises(PerfilInsatisfacible, match="no está en el catálogo"):
        p.elegir("r", REQ)
    req = RequisitoRol(modelo={Proveedor.foundry: "gpt-5"}, effort=Effort.xhigh, structured_outputs=True)
    with pytest.raises(PerfilInsatisfacible, match="no admite effort xhigh"):
        p.elegir("r", req)
    req = req.model_copy(update={"effort": Effort.high, "contexto_min_tokens": 2_000_000})
    with pytest.raises(PerfilInsatisfacible, match="contexto"):
        p.elegir("r", req)


def test_cae_a_anthropic_directo_para_cualquier_repositorio():
    p = _proveedores([_entrada(None, hosting="anthropic", region=None)], anthropic=True)
    correr(p.refrescar())
    e = p.elegir("r", REQ)
    assert e.proveedor.proveedor == Proveedor.anthropic and e.despliegue is None
    # Foundry primero: con el modelo en su catálogo no se llega a Anthropic.
    p = _proveedores([_entrada("opus-dz"), _entrada(None, hosting="anthropic", region=None)], anthropic=True)
    correr(p.refrescar())
    assert p.elegir("r", REQ).proveedor.proveedor == Proveedor.foundry
    # Sin Anthropic configurado, el motivo lo dice.
    solo = _proveedores([_entrada("otro", modelo="gpt-5")])
    correr(solo.refrescar())
    with pytest.raises(PerfilInsatisfacible, match="anthropic no configurado"):
        solo.elegir("r", REQ)


def test_sin_catalogo_la_region_auditada_sale_del_adaptador():
    global_ = Proveedores({Proveedor.foundry: ProveedorGuionado(lambda p: None, region="global")})
    assert global_.elegir("r", REQ).region == "global"
    sin_region = Proveedores({Proveedor.foundry: ProveedorGuionado(lambda p: None, region=None)})
    assert sin_region.elegir("r", REQ).region is None
    en_zona = Proveedores({Proveedor.foundry: ProveedorGuionado(lambda p: None, region="eastus2")})
    assert en_zona.elegir("r", REQ).region == "eastus2"
    # Anthropic directo, sin catálogo: lo sirve cuando Foundry no está configurado.
    directo = Proveedores(
        {Proveedor.anthropic: ProveedorGuionado(lambda p: None, Proveedor.anthropic, "global")}
    )
    assert directo.elegir("r", REQ).proveedor.proveedor == Proveedor.anthropic


def test_claves_de_perfil_por_etapa_y_nivel():
    perfil = perfil_por_defecto(WS_ALCANCE, Perfil.estandar)
    gpt = RequisitoRol(modelo={Proveedor.foundry: "gpt-5"}, effort=Effort.high, structured_outputs=True)
    plan = RequisitoRol(modelo={Proveedor.foundry: "plan"}, structured_outputs=True)
    exacta = RequisitoRol(modelo={Proveedor.foundry: "exacta"}, structured_outputs=True)
    perfil = perfil.model_copy(
        update={
            "roles": {
                **perfil.roles,
                "critico-profundo:restringido": gpt,
                "critico-profundo@plan": plan,
                "critico-profundo@codigo:restringido": exacta,
            }
        }
    )
    r = "critico-profundo"
    assert requisito(perfil, r) == perfil.roles[r]
    assert requisito(perfil, r, GateFase.spec, NivelCodigo.restringido) == gpt
    assert requisito(perfil, r, GateFase.plan, NivelCodigo.restringido) == plan
    assert requisito(perfil, r, GateFase.codigo, NivelCodigo.restringido) == exacta
    assert requisito(perfil, r, GateFase.spec, NivelCodigo.abierto) == perfil.roles[r]
    assert requisito(perfil, "refutador", GateFase.spec) == perfil.roles["refutador"]


# --- Motor: validación al arrancar y auditoría ---------------------------------------------


def _con_catalogo(motor, entradas, region_adaptador="eastus2"):
    adaptador = motor.n.proveedores._disponibles[Proveedor.foundry]
    adaptador.region = region_adaptador
    motor.n.proveedores = Proveedores(
        {Proveedor.foundry: adaptador},
        Catalogo([FuenteContada(entradas)], motor.n.almacen),
    )
    return adaptador


def test_unit_start_rechaza_un_perfil_que_el_catalogo_no_sirve():
    async def caso():
        motor, _ = construir()
        _con_catalogo(motor, [_entrada("gpt", "gpt-5", "eastus2")])  # sin los modelos del perfil
        with pytest.raises(ErrorNegocio) as exc:
            await motor.start(entrada_start(), JULIAN)
        assert exc.value.codigo == CodigoError.perfil_insatisfacible
        assert "no está en el catálogo" in exc.value.detalle
        assert motor.n.almacen.db.unidades.count_documents({}) == 0  # nada creado
        # El catálogo quedó persistido para la organización.
        assert {m.despliegue for m in motor.n.almacen.catalogo(ORG)} == {"gpt"}

    correr(caso())


def test_unit_start_en_restringido_acepta_despliegues_global():
    """El nivel restringido (el de un vínculo sin configurar) ya no exige zona de datos ni región fija."""

    async def caso():
        motor, _ = construir()
        _con_catalogo(
            motor, [_entrada("opus-g", region="global"), _entrada("sonnet-g", "claude-sonnet-5-5", "global")]
        )
        out = await motor.start(entrada_start(), JULIAN)
        assert out.estado.nivel_efectivo == NivelCodigo.restringido
        assert {m.despliegue for m in motor.n.almacen.catalogo(ORG)} == {"opus-g", "sonnet-g"}

    correr(caso())


def test_sin_proveedores_unit_start_rechaza():
    async def caso():
        motor, _ = construir()
        motor.n.proveedores = Proveedores({})
        with pytest.raises(ErrorNegocio) as exc:
            await motor.start(entrada_start(), JULIAN)
        assert exc.value.codigo == CodigoError.perfil_insatisfacible

    correr(caso())


def _workspace(zona):
    ahora = datetime(2026, 9, 30, tzinfo=UTC)
    return Workspace(
        version=1,
        auditoria=Auditoria(creado_por=JULIAN, creado_en=ahora, actualizado_por=JULIAN, actualizado_en=ahora),
        alcance=AlcanceWorkspace(org=ORG, workspace=WS_ALCANCE.workspace),
        nombre="Certificados",
        zona_datos_azure=zona,
    )


def test_la_auditoria_lleva_despliegue_region_y_las_llamadas_fallidas():
    async def caso():
        llamadas = []

        def guion(p):
            llamadas.append(p)
            if len(llamadas) == 1:
                return ErrorProveedor("503 de Foundry", duracion_ms=7)
            return SalidaCritico(hallazgos=[])

        motor, _ = construir(guion)
        motor.n.almacen.guardar_configuracion([_workspace("us")])
        entradas = [
            _entrada("opus-dz", region="zona-us"),
            _entrada("sonnet-dz", "claude-sonnet-5-5", "zona-us"),
        ]
        _con_catalogo(motor, entradas)
        from railspec.contracts.estado import TipoCheckpoint
        from test_motor_gate import es_checkpoint, hasta

        alcance = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        await hasta(motor, alcance, es_checkpoint(TipoCheckpoint.gate_escalado))
        assert llamadas[0].despliegue in {"opus-dz", "sonnet-dz"} and llamadas[0].region == "zona-us"
        registros = list(motor.n.almacen.db.auditoria.find({"evento": "llamada-modelo"}))
        assert registros and all(r["region"] == "zona-us" for r in registros)
        fallida = next(r for r in registros if r["detalle"]["resultado"] == "error")
        assert "503" in fallida["detalle"]["error"] and fallida["detalle"]["despliegue"] in {
            "opus-dz",
            "sonnet-dz",
        }
        assert fallida["modelo"].startswith("claude-")
        tele = list(motor.n.almacen.db.telemetria.find({}))
        assert any(t["duracion_ms"] == 7 and t["tokens_entrada"] == 0 for t in tele)

    correr(caso())


def test_configuracion_y_ensamblado_de_proveedores_reales():
    from railspec.server.app import _contexto, _proveedores
    from railspec.server.config import Configuracion
    from railspec.server.contexto import ContextoConectable

    env = {
        "RAILSPEC_FOUNDRY_ENDPOINT": "https://acme.services.ai.azure.com/",
        "RAILSPEC_FOUNDRY_API_KEY": "k",
        "RAILSPEC_FOUNDRY_REGION": "EastUS2",
        "RAILSPEC_FOUNDRY_ZONA_DATOS": "us",
        "RAILSPEC_FOUNDRY_DESPLIEGUES": "opus-dz=claude-opus-5-5:DataZoneStandard",
        "RAILSPEC_CATALOGO_TTL_S": "60",
        "RAILSPEC_PCE_URL": "http://pce:8000/mcp",
    }
    c = Configuracion.desde_entorno(env)
    assert c.foundry.region == "eastus2" and c.foundry.zona_datos == "us" and c.catalogo_ttl_s == 60
    assert c.foundry.proyecto is None and c.contexto_cache_s == 900
    almacen = almacen_en_memoria()
    p = _proveedores(c, almacen)
    assert p.configurados == [Proveedor.foundry]
    correr(p.refrescar(ORG))
    e = p.elegir("critico-profundo", REQ, org=ORG)
    assert (e.despliegue, e.region) == ("opus-dz", "zona-us")
    assert [m.despliegue for m in almacen.catalogo(ORG)] == ["opus-dz"]
    assert isinstance(_contexto(c, almacen), ContextoConectable)
    # Sin despliegues declarados ni proyecto, el catálogo de Foundry queda vacío: nada se
    # satisface (no se adivina el SKU).
    sin = _proveedores(
        Configuracion.desde_entorno(
            {
                "RAILSPEC_FOUNDRY_ENDPOINT": "https://a",
                "RAILSPEC_FOUNDRY_API_KEY": "k",
                "RAILSPEC_FOUNDRY_REGION": "eastus2",
            }
        ),
        almacen,
    )
    correr(sin.refrescar(ORG))
    with pytest.raises(PerfilInsatisfacible, match="no está en el catálogo"):
        sin.elegir("r", REQ)


def test_humo_sin_llamada(monkeypatch, capsys):
    from railspec.server import humo

    monkeypatch.setenv("RAILSPEC_FOUNDRY_ENDPOINT", "https://acme.services.ai.azure.com")
    monkeypatch.setenv("RAILSPEC_FOUNDRY_API_KEY", "k")
    monkeypatch.setenv("RAILSPEC_FOUNDRY_REGION", "eastus2")
    monkeypatch.setenv("RAILSPEC_FOUNDRY_ZONA_DATOS", "us")
    monkeypatch.setenv(
        "RAILSPEC_FOUNDRY_DESPLIEGUES", "opus=claude-opus-5-5:DataZoneStandard,sonnet=claude-sonnet-5-5"
    )
    assert humo.main(["--sin-llamada", "--nivel", "interno"]) == 0
    assert "perfil estandar satisfacible" in capsys.readouterr().out
    # Global ya no se rechaza en restringido; lo que falta en el catálogo sí.
    monkeypatch.setenv(
        "RAILSPEC_FOUNDRY_DESPLIEGUES",
        "opus=claude-opus-5-5:GlobalStandard,sonnet=claude-sonnet-5-5:GlobalStandard",
    )
    assert humo.main(["--sin-llamada", "--nivel", "restringido"]) == 0
    monkeypatch.setenv("RAILSPEC_FOUNDRY_DESPLIEGUES", "opus=claude-opus-5-5:GlobalStandard")
    assert humo.main(["--sin-llamada", "--nivel", "restringido"]) == 1


# --- Caché de nodos por hash de entradas ---------------------------------------------------


def test_cache_de_nodos_por_hash_caduca_y_distingue_entradas():
    from datetime import timedelta

    from railspec.server.proveedores.base import RespuestaModelo, Uso
    from railspec.server.proveedores.cache import CacheNodos, clave_nodo

    # mongomock aplica el índice TTL con el reloj real.
    ahora = [datetime.now(UTC)]
    almacen = almacen_en_memoria()
    cache = CacheNodos(almacen, ttl_s=60, reloj=lambda: ahora[0])
    p = _peticion()
    r = RespuestaModelo(
        valor=SalidaCritico(hallazgos=[]),
        uso=Uso(tokens_entrada=10, tokens_salida=3),
        proveedor=Proveedor.foundry,
        modelo="claude-opus-5-5",
        region="zona-us",
    )
    assert cache.obtener(ORG, Proveedor.foundry, p) is None
    cache.guardar(ORG, p, r)
    guardada = cache.obtener(ORG, Proveedor.foundry, p)
    assert guardada.valor == r.valor and guardada.uso.tokens_entrada == 10 and guardada.region == "zona-us"
    # Otra organización, otro contenido u otro effort no comparten entrada.
    assert cache.obtener("otra-org", Proveedor.foundry, p) is None
    assert cache.obtener(ORG, Proveedor.foundry, _peticion(contenido="otro")) is None
    assert clave_nodo(Proveedor.foundry, p) != clave_nodo(Proveedor.foundry, _peticion(effort=Effort.low))
    assert clave_nodo(Proveedor.foundry, p) != clave_nodo(Proveedor.foundry, _peticion(despliegue="opus-dz"))
    # Lo enviado nunca se guarda.
    doc = almacen.db.cache_nodos.find_one({})
    assert "material" not in json.dumps(doc, default=str)
    ahora[0] += timedelta(seconds=61)
    assert cache.obtener(ORG, Proveedor.foundry, p) is None
    assert CacheNodos(almacen, ttl_s=0).obtener(ORG, Proveedor.foundry, p) is None


def test_un_gate_repetido_sale_de_la_cache_sin_auditoria_ni_consumo():
    async def caso():
        from railspec.contracts.estado import TipoCheckpoint
        from railspec.server.proveedores.cache import CacheNodos
        from test_motor_gate import es_checkpoint, hasta

        motor, proveedor = construir()
        motor.n.proveedores.cache = CacheNodos(motor.n.almacen)
        primera = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        await hasta(motor, primera, es_checkpoint(TipoCheckpoint.aprobar_spec))
        pagadas = len(proveedor.peticiones)
        auditadas = motor.n.almacen.db.auditoria.count_documents({"evento": "llamada-modelo"})
        assert pagadas and auditadas == pagadas

        # Otra unidad con el mismo material y los mismos criterios: mismas entradas.
        segunda = (await motor.start(entrada_start(), JULIAN)).estado.unidad
        await hasta(motor, segunda, es_checkpoint(TipoCheckpoint.aprobar_spec))
        assert len(proveedor.peticiones) == pagadas
        assert motor.n.almacen.db.auditoria.count_documents({"evento": "llamada-modelo"}) == auditadas
        estado = motor.n.almacen.obtener_estado(segunda)
        assert estado.consumo.tokens == 0 and estado.gates[GateFase.spec].veredicto.value == "aprobado"
        assert motor.n.almacen.obtener_estado(primera).consumo.tokens > 0

    correr(caso())
