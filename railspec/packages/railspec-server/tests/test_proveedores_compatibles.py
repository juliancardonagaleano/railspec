"""Proveedores compatibles (contrato 1.8): adaptador con JSON validado, servicios, suscripciones y política.

Sin red ni credenciales: los clientes de los SDK son dobles y el listado usa ``httpx.MockTransport``.
Lo que se comprueba es lo que no se puede dar por hecho de esas APIs: que la salida estructurada se emula, que
un JSON malo escala y nunca aprueba, que sirven a cualquier repositorio (el nivel no los restringe) y que la
clave nunca sale.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import httpx
import pytest
from apoyo_motor import ORG
from pydantic import ValidationError
from railspec.contracts.comun import Effort, Proveedor
from railspec.contracts.repositorio import (
    Capacidades,
    ModeloSuscripcion,
    PrecioModelo,
    ProtocoloCompatible,
    RequisitoRol,
    SuscripcionModelo,
)
from railspec.server.motor.gate import SalidaRefutador
from railspec.server.proveedores import ErrorProveedor, PeticionModelo, Proveedores
from railspec.server.proveedores.compatible import (
    USER_AGENT,
    AdaptadorCompatible,
    cliente_chat,
    cliente_mensajes,
    extraer_json,
    instruccion_formato,
)
from railspec.server.proveedores.falso import ProveedorGuionado
from railspec.server.proveedores.seleccion import PerfilInsatisfacible
from railspec.server.proveedores.servicios_compatibles import SERVICIOS, leer_modelos
from railspec.server.proveedores.suscripciones import (
    EntradaSuscripcion,
    ErrorSuscripcion,
    clasificar_lectura,
    hosting_de,
    politica_compatibles,
)
from test_consola import CSRF
from test_consola_suscripciones import RUTA, _montaje, _todo
from test_suscripciones import AUDITORIA, CLAVE, _servicio, _volcado, correr

COMPATIBLE = Proveedor.compatible
MENSAJES = ProtocoloCompatible.anthropic_messages
CHAT = ProtocoloCompatible.openai_chat
VALIDO = '{"refutado": true, "motivo": "no aplica"}'


def _peticion(modelo="minimax-m3", **extra):
    datos = dict(
        rol="refutador",
        modelo=modelo,
        sistema="rúbrica estable",
        contenido="material",
        esquema=SalidaRefutador,
    )
    return PeticionModelo(**(datos | extra))


# --- clientes dobles -----------------------------------------------------------------------------


class ClienteMensajes:
    def __init__(self, *respuestas):
        self.llamadas = []
        self._respuestas = list(respuestas)
        self.messages = self

    async def create(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self._respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _mensaje(texto=VALIDO, stop="end_turn", bloques=None, **uso):
    u = dict(input_tokens=1000, output_tokens=100, cache_read_input_tokens=800, cache_creation_input_tokens=0)
    u.update(uso)
    contenido = bloques if bloques is not None else [SimpleNamespace(type="text", text=texto)]
    return SimpleNamespace(content=contenido, stop_reason=stop, usage=SimpleNamespace(**u))


class ClienteChat:
    def __init__(self, *respuestas):
        self.llamadas = []
        self._respuestas = list(respuestas)
        self.chat = SimpleNamespace(completions=self)

    async def create(self, **kwargs):
        self.llamadas.append(kwargs)
        r = self._respuestas.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def _chat(texto=VALIDO, fin="stop", refusal=None, cache=200, entrada=1000, salida=100):
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=fin, message=SimpleNamespace(content=texto, refusal=refusal))],
        usage=SimpleNamespace(
            prompt_tokens=entrada,
            completion_tokens=salida,
            prompt_tokens_details=SimpleNamespace(cached_tokens=cache),
        ),
    )


def _adaptador(mensajes=None, chat=None, **kw):
    return AdaptadorCompatible(
        {"minimax-m3": MENSAJES, "glm-5": CHAT, "claude-opus-5-5": MENSAJES},
        cliente_mensajes=mensajes,
        cliente_chat=chat,
        **kw,
    )


# --- extraer el JSON de una respuesta de texto ---------------------------------------------------


@pytest.mark.parametrize(
    "texto",
    [
        VALIDO,
        f"```json\n{VALIDO}\n```",
        f"```\n{VALIDO}\n```",
        f"Claro, aquí está:\n{VALIDO}\nEspero que sirva.",
        f"<think>\nvoy a pensar {{con llaves}}\n</think>\n{VALIDO}",
        f"<thinking>x</thinking>  {VALIDO}  ",
    ],
)
def test_extraer_json_quita_razonamiento_cercas_y_prosa(texto):
    assert json.loads(extraer_json(texto)) == {"refutado": True, "motivo": "no aplica"}


def test_la_instruccion_de_formato_lleva_el_esquema_del_nodo():
    texto = instruccion_formato(SalidaRefutador)
    assert "JSON" in texto and '"refutado"' in texto and '"motivo"' in texto


# --- adaptador: mensajes de Anthropic ------------------------------------------------------------


def test_mensajes_manda_el_modelo_el_sistema_con_el_esquema_y_devuelve_el_valor_validado():
    cliente = ClienteMensajes(_mensaje())
    r = correr(_adaptador(mensajes=cliente).completar(_peticion(max_tokens=4000)))
    assert r.valor == SalidaRefutador(refutado=True, motivo="no aplica")
    assert (r.proveedor, r.modelo, r.region) == (COMPATIBLE, "minimax-m3", None)
    (llamada,) = cliente.llamadas
    assert llamada["model"] == "minimax-m3" and llamada["max_tokens"] == 4000
    # El sistema estable va primero y la instrucción de formato después; sin cache_control ni effort ni parse.
    assert llamada["system"].startswith("rúbrica estable\n\n") and '"refutado"' in llamada["system"]
    assert llamada["messages"] == [{"role": "user", "content": "material"}]
    assert not {"output_config", "output_format", "tools", "tool_choice", "thinking"} & set(llamada)
    assert (r.uso.tokens_entrada, r.uso.tokens_salida, r.uso.tokens_cache_lectura) == (1000, 100, 800)


def test_mensajes_ignora_los_bloques_de_razonamiento_y_une_los_de_texto():
    bloques = [
        SimpleNamespace(type="thinking", thinking='{"refutado": false, "motivo": "esto no"}'),
        SimpleNamespace(type="text", text='{"refutado": true,'),
        SimpleNamespace(type="text", text=' "motivo": "ok"}'),
    ]
    r = correr(_adaptador(mensajes=ClienteMensajes(_mensaje(bloques=bloques))).completar(_peticion()))
    assert r.valor == SalidaRefutador(refutado=True, motivo="ok")


def test_un_json_que_no_encaja_se_repite_una_vez_con_el_error_y_se_suman_los_usos():
    cliente = ClienteMensajes(
        _mensaje('{"refutado": "quizá"}', input_tokens=500, output_tokens=50),
        _mensaje(input_tokens=700, output_tokens=60),
    )
    r = correr(_adaptador(mensajes=cliente).completar(_peticion()))
    assert r.valor.refutado is True and len(cliente.llamadas) == 2
    reintento = cliente.llamadas[1]["messages"]
    assert [m["role"] for m in reintento] == ["user", "assistant", "user"]
    assert reintento[1]["content"] == '{"refutado": "quizá"}'
    # El error le dice al modelo qué campo falló, sin repetir el valor recibido.
    assert "refutado" in reintento[2]["content"] and "quizá" not in reintento[2]["content"]
    assert (r.uso.tokens_entrada, r.uso.tokens_salida) == (1200, 110)


def test_dos_respuestas_fuera_de_esquema_son_error_del_proveedor_y_nunca_un_valor():
    cliente = ClienteMensajes(_mensaje("no es JSON"), _mensaje('{"refutado": true}'))
    with pytest.raises(ErrorProveedor, match="fuera de esquema tras 2 intentos") as exc:
        correr(_adaptador(mensajes=cliente).completar(_peticion()))
    assert exc.value.duracion_ms >= 0 and len(cliente.llamadas) == 2


@pytest.mark.parametrize("stop", ["max_tokens", "refusal", "model_context_window_exceeded"])
def test_una_respuesta_cortada_o_rechazada_no_se_repite_ni_se_acepta(stop):
    cliente = ClienteMensajes(_mensaje("{", stop=stop))
    with pytest.raises(ErrorProveedor, match="respuesta cortada"):
        correr(_adaptador(mensajes=cliente).completar(_peticion()))
    assert len(cliente.llamadas) == 1


def test_un_error_de_red_o_cuota_del_sdk_es_error_del_proveedor_con_la_duracion():
    cliente = ClienteMensajes(RuntimeError("429 cuota"))
    with pytest.raises(ErrorProveedor, match="compatible/minimax-m3: RuntimeError: 429 cuota") as exc:
        correr(_adaptador(mensajes=cliente).completar(_peticion()))
    assert exc.value.duracion_ms >= 0


def test_un_modelo_sin_protocolo_o_sin_el_endpoint_de_su_protocolo_no_llama_a_nadie():
    cliente = ClienteMensajes(_mensaje())
    with pytest.raises(ErrorProveedor, match="no declara su protocolo"):
        correr(_adaptador(mensajes=cliente).completar(_peticion(modelo="otro")))
    with pytest.raises(ErrorProveedor, match="no tiene endpoint de mensajes"):
        correr(_adaptador(chat=ClienteChat()).completar(_peticion()))
    with pytest.raises(ErrorProveedor, match="no tiene endpoint de chat"):
        correr(_adaptador(mensajes=cliente).completar(_peticion(modelo="glm-5")))
    assert cliente.llamadas == []


# --- adaptador: chat completions de OpenAI --------------------------------------------------------


def test_chat_manda_sistema_y_usuario_sin_response_format_y_resta_la_cache_de_la_entrada():
    cliente = ClienteChat(_chat(cache=200, entrada=1000, salida=100))
    r = correr(_adaptador(chat=cliente).completar(_peticion(modelo="glm-5", max_tokens=3000)))
    assert r.valor.motivo == "no aplica"
    (llamada,) = cliente.llamadas
    assert llamada["model"] == "glm-5" and llamada["max_tokens"] == 3000
    sistema, usuario = llamada["messages"]
    assert sistema["role"] == "system" and sistema["content"].startswith("rúbrica estable\n\n")
    assert usuario == {"role": "user", "content": "material"}
    assert not {"response_format", "reasoning_effort", "tools"} & set(llamada)
    assert (r.uso.tokens_entrada, r.uso.tokens_salida, r.uso.tokens_cache_lectura) == (800, 100, 200)


def test_chat_quita_el_razonamiento_que_minimax_deja_en_el_contenido():
    cliente = ClienteChat(_chat(f"<think>\n{{}} pienso\n</think>\n{VALIDO}"))
    assert correr(_adaptador(chat=cliente).completar(_peticion(modelo="glm-5"))).valor.refutado is True


@pytest.mark.parametrize("fin", ["length", "content_filter"])
def test_chat_cortado_por_longitud_o_filtro_no_se_acepta(fin):
    with pytest.raises(ErrorProveedor, match=f"respuesta cortada \\({fin}\\)"):
        correr(_adaptador(chat=ClienteChat(_chat("{", fin=fin))).completar(_peticion(modelo="glm-5")))


def test_chat_rechazo_del_modelo_y_respuesta_sin_choices_son_error():
    with pytest.raises(ErrorProveedor, match="rechazo del modelo"):
        correr(_adaptador(chat=ClienteChat(_chat(refusal="no puedo"))).completar(_peticion(modelo="glm-5")))
    vacia = SimpleNamespace(choices=[], usage=None)
    with pytest.raises(ErrorProveedor, match="sin choices"):
        correr(_adaptador(chat=ClienteChat(vacia)).completar(_peticion(modelo="glm-5")))


def test_chat_reintenta_una_vez_con_el_error_en_la_conversacion():
    cliente = ClienteChat(_chat("```json\n{}\n```"), _chat())
    r = correr(_adaptador(chat=cliente).completar(_peticion(modelo="glm-5")))
    assert r.valor.refutado is True
    assert [m["role"] for m in cliente.llamadas[1]["messages"]] == ["system", "user", "assistant", "user"]


# --- costo ---------------------------------------------------------------------------------------


def test_el_costo_sale_de_la_tarifa_de_la_suscripcion_y_sin_ella_es_cero_salvo_claude():
    tarifa = {"minimax-m3": PrecioModelo(entrada=0.30, salida=1.20, cache_lectura=0.06)}
    r = correr(_adaptador(mensajes=ClienteMensajes(_mensaje()), precios=tarifa).completar(_peticion()))
    # 1000 de entrada, 100 de salida y 800 de caché: (1000*0.30 + 100*1.20 + 800*0.06) / 1e6
    assert r.uso.costo_usd == pytest.approx(0.000468)
    sin = correr(_adaptador(mensajes=ClienteMensajes(_mensaje())).completar(_peticion()))
    assert sin.uso.costo_usd == 0.0
    claude = correr(
        _adaptador(mensajes=ClienteMensajes(_mensaje())).completar(_peticion(modelo="claude-opus-5-5"))
    )
    assert claude.uso.costo_usd > 0


# --- clientes reales (solo construcción: no hay red) -----------------------------------------------


def test_los_clientes_se_construyen_con_la_url_base_la_clave_y_un_user_agent_propio():
    mensajes = cliente_mensajes("https://api.minimax.io/anthropic", "k-1")
    assert str(mensajes.base_url).startswith("https://api.minimax.io/anthropic")
    assert mensajes.default_headers["User-Agent"] == USER_AGENT
    chat = cliente_chat("https://opencode.ai/zen/v1", "k-2")
    assert str(chat.base_url) == "https://opencode.ai/zen/v1/"
    assert chat.default_headers["User-Agent"] == USER_AGENT


# --- descubrimiento ------------------------------------------------------------------------------

ZEN = {
    "object": "list",
    "data": [
        {"id": i, "object": "model", "created": 1, "owned_by": "opencode"}
        for i in (
            "claude-sonnet-5-5",
            "minimax-m3",
            "glm-5.3",
            "kimi-k3",
            "qwen3.8-flash",
            "qwen3.8-max",
            "gpt-5.5",
            "grok-4.7",
            "gemini-3.1-pro",
            "jev-1.13",
            "big-pickle",
            "mimo-v2.5-free",
            "muse-spark-1.3-contributor-free",
            "modelo-nuevo-que-no-conozco",
        )
    ],
}
MINIMAX = {
    "object": "list",
    "data": [
        {"id": i, "object": "model", "created": 1, "owned_by": "minimax"}
        for i in ("MiniMax-M3", "MiniMax-M2.7")
    ],
}


def _lectura(servicio, endpoint, mensajes, cuerpo, estado=200, vistas=None):
    def responder(p: httpx.Request) -> httpx.Response:
        if vistas is not None:
            vistas.append(p)
        return httpx.Response(estado, json=cuerpo)

    return correr(
        leer_modelos(
            SERVICIOS.get(servicio or ""),
            endpoint,
            mensajes,
            CLAVE,
            transporte=httpx.MockTransport(responder),
        )
    )


def test_zen_ofrece_solo_lo_que_el_adaptador_habla_y_no_recolecta_datos():
    vistas = []
    entradas = _lectura(
        "opencode-zen", "https://opencode.ai/zen/v1", "https://opencode.ai/zen", ZEN, vistas=vistas
    )
    protocolos = {e.modelo: e.protocolo for e in entradas}
    assert protocolos == {
        "claude-sonnet-5-5": MENSAJES,
        "qwen3.8-flash": MENSAJES,
        "minimax-m3": CHAT,
        "glm-5.3": CHAT,
        "kimi-k3": CHAT,
        "qwen3.8-max": CHAT,
    }
    # Fuera: Responses (gpt, grok), Gemini, Jev, los que recolectan datos y lo que no está en la tabla.
    (p,) = vistas
    assert str(p.url) == "https://opencode.ai/zen/v1/models"
    assert p.headers["Authorization"] == f"Bearer {CLAVE}" and p.headers["User-Agent"] == USER_AGENT
    assert {e.hosting for e in entradas} == {"externo"} and {e.proveedor for e in entradas} == {COMPATIBLE}
    assert all(
        e.region is None and e.capacidades.structured_outputs and not e.capacidades.efforts for e in entradas
    )
    claude = next(e for e in entradas if e.modelo == "claude-sonnet-5-5")
    assert claude.capacidades.contexto_max_tokens >= 200_000
    assert next(e for e in entradas if e.modelo == "glm-5.3").capacidades.contexto_max_tokens == 8192


def test_minimax_usa_mensajes_de_anthropic_y_sabe_su_contexto():
    entradas = _lectura("minimax", "https://api.minimax.io/v1", "https://api.minimax.io/anthropic", MINIMAX)
    assert {e.modelo: (e.protocolo, e.capacidades.contexto_max_tokens) for e in entradas} == {
        "MiniMax-M2.7": (MENSAJES, 204_800),
        "MiniMax-M3": (MENSAJES, 1_000_000),
    }


def test_un_servicio_personalizado_descubre_con_el_protocolo_de_su_endpoint():
    vistas = []
    solo_chat = _lectura(None, "https://api.ejemplo.com/v1/", None, ZEN, vistas=vistas)
    assert str(vistas[0].url) == "https://api.ejemplo.com/v1/models"
    assert len(solo_chat) == len(ZEN["data"]) and {e.protocolo for e in solo_chat} == {CHAT}
    vistas.clear()
    solo_mensajes = _lectura(None, None, "https://api.ejemplo.com/anthropic", ZEN, vistas=vistas)
    assert str(vistas[0].url) == "https://api.ejemplo.com/anthropic/v1/models"
    assert vistas[0].headers["x-api-key"] == CLAVE and {e.protocolo for e in solo_mensajes} == {MENSAJES}


def test_una_respuesta_sin_lista_de_modelos_es_error_de_forma_y_un_http_error_trae_su_estado():
    with pytest.raises(ValueError):
        _lectura("minimax", "https://api.minimax.io/v1", None, {"models": []})
    with pytest.raises(httpx.HTTPStatusError) as exc:
        _lectura("minimax", "https://api.minimax.io/v1", None, {}, estado=401)
    s = SuscripcionModelo.model_validate(_compatible_doc())
    error = clasificar_lectura(exc.value, s)
    assert (
        error.codigo == "autenticacion"
        and CLAVE not in error.detalle
        and "api.minimax.io" not in error.detalle
    )


# --- suscripciones -------------------------------------------------------------------------------

KEY = "sk-COMPATIBLE-QUE-NO-DEBE-SALIR-0123456789"


def _compatible_doc(**cambios):
    base = {
        "version": 1,
        "auditoria": AUDITORIA.model_dump(),
        "org": ORG,
        "id": "minimax",
        "nombre": "MiniMax",
        "proveedor": "compatible",
        "servicio": "minimax",
        "endpoint": "https://api.minimax.io/v1",
        "endpoint_mensajes": "https://api.minimax.io/anthropic",
        "clave_configurada": True,
    }
    return base | cambios


def _entrada(**cambios) -> EntradaSuscripcion:
    return EntradaSuscripcion(**({"nombre": "MiniMax", "servicio": "minimax", "clave": KEY} | cambios))


def _guardar(svc, id_="minimax", **cambios):
    return svc.guardar(ORG, id_, COMPATIBLE, _entrada(**cambios), None, AUDITORIA)


def test_un_servicio_conocido_fija_sus_endpoints_y_la_clave_se_guarda_cifrada():
    servicio, datos, almacen = _servicio()
    s = _guardar(servicio)
    assert (s.servicio, s.endpoint, s.endpoint_mensajes) == (
        "minimax",
        "https://api.minimax.io/v1",
        "https://api.minimax.io/anthropic",
    )
    assert (s.proyecto, s.region, s.zona_datos) == (None, None, None) and hosting_de(s) == "externo"
    assert s.clave_configurada and KEY not in _volcado(almacen) and KEY not in s.model_dump_json()
    assert datos.clave_suscripcion(ORG, "minimax").startswith("v1.")


@pytest.mark.parametrize(
    ("cambios", "codigo"),
    [
        ({"servicio": "inventado"}, "servicio-desconocido"),
        ({"endpoint": "https://api.minimax.io/v1"}, "campos-invalidos"),
        ({"endpoint_mensajes": "https://api.minimax.io/anthropic"}, "campos-invalidos"),
        ({"proyecto": "p"}, "campos-invalidos"),
        ({"region": "eu"}, "campos-invalidos"),
        ({"zona_datos": "eu"}, "campos-invalidos"),
        ({"autenticacion": "identidad-servidor", "clave": None}, "autenticacion-invalida"),
        ({"clave": None}, "clave-requerida"),
    ],
)
def test_una_suscripcion_compatible_mal_formada_se_rechaza_con_su_codigo(cambios, codigo):
    servicio, _, _ = _servicio()
    with pytest.raises(ErrorSuscripcion) as exc:
        _guardar(servicio, **cambios)
    assert exc.value.codigo == codigo and KEY not in exc.value.detalle
    assert servicio.listar(ORG) == []


def test_una_suscripcion_personalizada_solo_acepta_hosts_permitidos_por_el_operador():
    propios = {"servicio": None, "endpoint": "https://llm.ejemplo.com/v1/"}
    cerrado, _, _ = _servicio()
    with pytest.raises(ErrorSuscripcion, match="no está permitido") as exc:
        _guardar(cerrado, **propios)
    assert exc.value.codigo == "endpoint-no-permitido"
    abierto, _, _ = _servicio(
        politica_personalizados=politica_compatibles({"RAILSPEC_COMPATIBLES_HOSTS": "llm.ejemplo.com"})
    )
    s = _guardar(abierto, **propios, endpoint_mensajes="https://llm.ejemplo.com/anthropic")
    # Conserva la ruta, sin barra final; sin servicio conocido.
    assert (s.servicio, s.endpoint, s.endpoint_mensajes) == (
        None,
        "https://llm.ejemplo.com/v1",
        "https://llm.ejemplo.com/anthropic",
    )
    # Los servicios conocidos siempre valen, y un endpoint con http, IP privada o consulta no.
    for malo in ("http://llm.ejemplo.com/v1", "https://10.0.0.5/v1", "https://llm.ejemplo.com/v1?x=1"):
        with pytest.raises(ErrorSuscripcion) as exc:
            _guardar(abierto, id_="malo", servicio=None, endpoint=malo)
        assert exc.value.codigo == "endpoint-no-permitido"
    with pytest.raises(ErrorSuscripcion, match="al menos un endpoint"):
        _guardar(abierto, id_="vacio", servicio=None)
    assert politica_compatibles({}).host_permitido("opencode.ai")


def test_los_proveedores_que_no_son_compatibles_rechazan_servicio_y_endpoint_de_mensajes():
    servicio, _, _ = _servicio()
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.guardar(
            ORG,
            "a",
            Proveedor.anthropic,
            EntradaSuscripcion(nombre="A", clave="k", servicio="minimax"),
            None,
            AUDITORIA,
        )
    assert exc.value.codigo == "campos-invalidos"


def test_cambiar_de_servicio_o_de_endpoint_obliga_a_escribir_la_clave_otra_vez():
    servicio, datos, _ = _servicio(
        politica_personalizados=politica_compatibles({"RAILSPEC_COMPATIBLES_HOSTS": "llm.ejemplo.com"})
    )
    s = _guardar(servicio)
    cifrada = datos.clave_suscripcion(ORG, "minimax")
    # Editar sin tocar el destino conserva la clave guardada.
    editada = servicio.guardar(
        ORG, "minimax", COMPATIBLE, _entrada(nombre="Otro", clave=None), s.version, AUDITORIA
    )
    assert editada.nombre == "Otro" and datos.clave_suscripcion(ORG, "minimax") == cifrada
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.guardar(
            ORG,
            "minimax",
            COMPATIBLE,
            _entrada(servicio="opencode-zen", clave=None),
            editada.version,
            AUDITORIA,
        )
    assert exc.value.codigo == "clave-requerida"


def _descubierta(servicio, cuerpo=MINIMAX):
    servicio._transporte = httpx.MockTransport(lambda p: httpx.Response(200, json=cuerpo))
    _guardar(servicio)
    return correr(servicio.descubrir(ORG, "minimax", "ana", AUDITORIA)).suscripcion


def test_descubrir_guarda_el_protocolo_de_cada_modelo_y_el_contrato_lo_exige():
    servicio, _, almacen = _servicio()
    s = _descubierta(servicio)
    assert {m.modelo: m.protocolo for m in s.modelos} == {"MiniMax-M2.7": MENSAJES, "MiniMax-M3": MENSAJES}
    assert all(m.origen == "descubierto" and not m.seleccionado and m.region is None for m in s.modelos)
    assert KEY not in _volcado(almacen)
    doc = _compatible_doc()
    modelo = {"modelo": "m", "capacidades": {"contexto_max_tokens": 1}, "origen": "declarado"}
    with pytest.raises(ValidationError, match="exige su protocolo"):
        SuscripcionModelo.model_validate({**doc, "modelos": [modelo]})
    with pytest.raises(ValidationError, match="no tiene endpoint para openai-chat"):
        SuscripcionModelo.model_validate(
            {**doc, "endpoint": None, "servicio": None, "modelos": [{**modelo, "protocolo": "openai-chat"}]}
        )
    with pytest.raises(ValidationError, match="endpoint o endpoint_mensajes"):
        SuscripcionModelo.model_validate({**doc, "endpoint": None, "endpoint_mensajes": None})
    with pytest.raises(ValidationError, match="solo de los proveedores compatibles"):
        SuscripcionModelo.model_validate(
            {**doc, "proveedor": "anthropic", "endpoint": None, "endpoint_mensajes": None, "servicio": None}
            | {"modelos": [{**modelo, "protocolo": "openai-chat"}]}
        )


def test_declarar_un_modelo_compatible_con_protocolo_y_tarifa_lo_deja_elegido_y_sobrevive_a_descubrir():
    servicio, _, _ = _servicio()
    s = _descubierta(servicio)
    s = servicio.declarar_compatible(
        ORG,
        "minimax",
        s.version,
        modelo="MiniMax-M3",
        protocolo=CHAT,
        efforts=None,
        structured_outputs=None,
        contexto=500_000,
        precio=PrecioModelo(entrada=0.30, salida=1.20),
        auditoria=AUDITORIA,
    )
    m3 = next(m for m in s.modelos if m.clave == "MiniMax-M3")
    assert (m3.origen, m3.seleccionado, m3.protocolo) == ("declarado", True, CHAT)
    assert m3.capacidades.contexto_max_tokens == 500_000 and m3.capacidades.structured_outputs
    assert m3.precio_usd_mtok == PrecioModelo(entrada=0.30, salida=1.20, cache_lectura=0.0)
    # Descubrir otra vez no pisa lo declarado.
    de_nuevo = correr(servicio.descubrir(ORG, "minimax", "ana", AUDITORIA)).suscripcion
    assert next(m for m in de_nuevo.modelos if m.clave == "MiniMax-M3").precio_usd_mtok is not None
    # Un modelo de un servicio desconocido para la tabla se declara con su protocolo.
    s = servicio.declarar_compatible(
        ORG,
        "minimax",
        de_nuevo.version,
        modelo="otro",
        protocolo=MENSAJES,
        efforts=None,
        structured_outputs=None,
        contexto=None,
        precio=None,
        auditoria=AUDITORIA,
    )
    assert next(m for m in s.modelos if m.clave == "otro").capacidades.contexto_max_tokens == 8192
    # Retirar solo vale para lo declarado y el protocolo necesita su endpoint.
    assert [
        m.clave
        for m in servicio.retirar(ORG, "minimax", s.version, "otro", AUDITORIA).modelos
        if m.clave == "otro"
    ] == []


def test_declarar_compatible_exige_el_endpoint_del_protocolo_y_una_suscripcion_compatible():
    servicio, _, _ = _servicio(
        politica_personalizados=politica_compatibles({"RAILSPEC_COMPATIBLES_HOSTS": "llm.ejemplo.com"})
    )
    s = _guardar(servicio, servicio=None, endpoint="https://llm.ejemplo.com/v1")
    comun = dict(efforts=None, structured_outputs=None, contexto=None, precio=None, auditoria=AUDITORIA)
    with pytest.raises(ErrorSuscripcion, match="no tiene endpoint para anthropic-messages"):
        servicio.declarar_compatible(ORG, "minimax", s.version, modelo="m", protocolo=MENSAJES, **comun)
    servicio.guardar(
        ORG, "directo", Proveedor.anthropic, EntradaSuscripcion(nombre="D", clave="k"), None, AUDITORIA
    )
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.declarar_compatible(ORG, "directo", 1, modelo="m", protocolo=CHAT, **comun)
    assert exc.value.codigo == "solo-compatible"


def test_quitar_el_endpoint_de_un_protocolo_en_uso_se_rechaza_hasta_retirar_el_modelo():
    servicio, _, _ = _servicio(
        politica_personalizados=politica_compatibles({"RAILSPEC_COMPATIBLES_HOSTS": "llm.ejemplo.com"})
    )
    s = _guardar(
        servicio,
        servicio=None,
        endpoint="https://llm.ejemplo.com/v1",
        endpoint_mensajes="https://llm.ejemplo.com/a",
    )
    s = servicio.declarar_compatible(
        ORG,
        "minimax",
        s.version,
        modelo="m",
        protocolo=MENSAJES,
        efforts=None,
        structured_outputs=None,
        contexto=None,
        precio=None,
        auditoria=AUDITORIA,
    )
    with pytest.raises(ErrorSuscripcion) as exc:
        servicio.guardar(
            ORG,
            "minimax",
            COMPATIBLE,
            _entrada(servicio=None, endpoint="https://llm.ejemplo.com/v1", clave=KEY),
            s.version,
            AUDITORIA,
        )
    assert exc.value.codigo == "endpoint-requerido" and "m usa anthropic-messages" in exc.value.detalle


# --- selección y política de datos -----------------------------------------------------------------


def _con_modelo_elegido(servicio, **kw):
    s = _descubierta(servicio)
    servicio.seleccionar(ORG, "minimax", s.version, ["MiniMax-M3"], AUDITORIA)
    return Proveedores({}, suscripciones=servicio)


SIN_EFFORT = RequisitoRol(modelo={COMPATIBLE: "MiniMax-M3"}, structured_outputs=True)


def test_un_proveedor_compatible_sirve_a_cualquier_repositorio_con_su_adaptador_y_sin_region():
    doble = ProveedorGuionado(lambda p: None, proveedor=COMPATIBLE)
    servicio, _, _ = _servicio(fabrica=lambda s, c: doble)
    p = _con_modelo_elegido(servicio)
    # Ya no hay nivel ni zona que comprobar: el repositorio restringido también lo usa si el perfil lo elige.
    e = p.elegir("refutador", SIN_EFFORT, org=ORG, suscripcion="minimax")
    assert (e.proveedor, e.modelo, e.despliegue, e.region, e.suscripcion) == (
        doble,
        "MiniMax-M3",
        None,
        None,
        "minimax",
    )
    assert p.validar([("refutador", SIN_EFFORT)], org=ORG, suscripcion="minimax") == []


def test_los_modelos_compatibles_no_admiten_effort_y_un_perfil_que_lo_pide_es_insatisfacible():
    servicio, _, _ = _servicio(fabrica=lambda s, c: ProveedorGuionado(lambda p: None, proveedor=COMPATIBLE))
    p = _con_modelo_elegido(servicio)
    con_effort = RequisitoRol(modelo={COMPATIBLE: "MiniMax-M3"}, effort=Effort.high, structured_outputs=True)
    with pytest.raises(PerfilInsatisfacible, match="no admite effort high"):
        p.elegir("refutador", con_effort, org=ORG, suscripcion="minimax")
    # Y un modelo que no se eligió no se usa, aunque la suscripción lo descubra.
    con_otro = RequisitoRol(modelo={COMPATIBLE: "MiniMax-M2.7"}, structured_outputs=True)
    with pytest.raises(PerfilInsatisfacible, match="no está entre los modelos elegidos"):
        p.elegir("refutador", con_otro, org=ORG, suscripcion="minimax")


def test_la_fabrica_real_arma_el_adaptador_con_los_protocolos_las_tarifas_y_los_endpoints_de_la_suscripcion():
    servicio, _, _ = _servicio()
    s = _descubierta(servicio)
    s = servicio.declarar_compatible(
        ORG,
        "minimax",
        s.version,
        modelo="MiniMax-M3",
        protocolo=MENSAJES,
        efforts=None,
        structured_outputs=None,
        contexto=None,
        precio=PrecioModelo(entrada=1, salida=2),
        auditoria=AUDITORIA,
    )
    adaptador = servicio.adaptador(s)
    assert isinstance(adaptador, AdaptadorCompatible) and adaptador.proveedor == COMPATIBLE
    assert adaptador._protocolos == {"MiniMax-M2.7": MENSAJES, "MiniMax-M3": MENSAJES}
    assert adaptador._precios == {"MiniMax-M3": PrecioModelo(entrada=1, salida=2)}
    assert str(adaptador._mensajes.base_url).startswith("https://api.minimax.io/anthropic")
    assert str(adaptador._chat.base_url) == "https://api.minimax.io/v1/"
    assert servicio.adaptador(s) is adaptador  # se reutiliza mientras la versión no cambie


def test_el_catalogo_marca_externo_el_hosting_de_las_suscripciones_compatibles():
    servicio, _, _ = _servicio()
    s = _descubierta(servicio)
    activa = servicio.activa(ORG, "minimax")
    assert hosting_de(s) == "externo" and activa.entradas == []  # ninguno elegido todavía
    servicio.seleccionar(ORG, "minimax", s.version, ["MiniMax-M3"], AUDITORIA)
    (e,) = servicio.activa(ORG, "minimax").entradas
    assert (e.hosting, e.protocolo, e.region) == ("externo", MENSAJES, None)


def test_un_modelo_del_catalogo_con_hosting_externo_es_un_contrato_valido():
    from railspec.contracts.repositorio import ModeloCatalogo

    m = ModeloCatalogo(
        org=ORG,
        proveedor=COMPATIBLE,
        modelo="MiniMax-M3",
        hosting="externo",
        capacidades=Capacidades(structured_outputs=True, contexto_max_tokens=1),
        leido_en=AUDITORIA.creado_en,
    )
    assert m.version_contrato == "1.9"
    with pytest.raises(ValidationError):
        ModeloCatalogo.model_validate({**m.model_dump(), "hosting": "otro"})


def test_un_modelo_de_suscripcion_acepta_protocolo_y_tarifa_y_rechaza_tarifas_negativas():
    caps = Capacidades(structured_outputs=True, contexto_max_tokens=1)
    m = ModeloSuscripcion(
        modelo="m",
        capacidades=caps,
        origen="declarado",
        protocolo=CHAT,
        precio_usd_mtok=PrecioModelo(entrada=0, salida=0),
    )
    assert m.clave == "m"
    with pytest.raises(ValidationError):
        PrecioModelo(entrada=-1, salida=1)


# --- API de la consola ---------------------------------------------------------------------------


def test_la_consola_registra_descubre_declara_y_asocia_un_perfil_compatible():
    m = _montaje(transporte=httpx.MockTransport(lambda p: httpx.Response(200, json=MINIMAX)))

    async def caso():
        async with m.cliente("tk-julian") as c:
            r = await c.put(
                f"{RUTA}/minimax",
                json={"proveedor": "compatible", "nombre": "MiniMax", "servicio": "minimax", "clave": KEY},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            s = r.json()
            assert (s["servicio"], s["endpoint_mensajes"], s["clave_configurada"]) == (
                "minimax",
                "https://api.minimax.io/anthropic",
                True,
            )
            assert KEY not in r.text and "clave" not in s
            lista = (await c.get(RUTA)).json()
            assert {x["id"] for x in lista["servicios_compatibles"]} == {"opencode-zen", "minimax"}
            r = await c.post(f"{RUTA}/minimax/descubrir", headers=CSRF)
            assert r.status_code == 200, r.text
            modelos = {x["clave"]: x for x in r.json()["suscripcion"]["modelos"]}
            assert modelos["MiniMax-M3"]["protocolo"] == "anthropic-messages"
            assert (
                modelos["MiniMax-M3"]["hosting"] == "externo" and "restringible" not in modelos["MiniMax-M3"]
            )
            version = r.json()["suscripcion"]["version"]
            r = await c.put(
                f"{RUTA}/minimax/modelos",
                json={"seleccionados": ["MiniMax-M3"], "version": version},
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            version = r.json()["version"]
            # Declarar exige el protocolo y acepta la tarifa.
            r = await c.post(
                f"{RUTA}/minimax/modelos", json={"modelo": "x", "version": version}, headers=CSRF
            )
            assert r.status_code == 422 and "protocolo" in r.text
            r = await c.post(
                f"{RUTA}/minimax/modelos",
                json={
                    "modelo": "x",
                    "protocolo": "openai-chat",
                    "precio_usd_mtok": {"entrada": 1, "salida": 2},
                    "version": version,
                },
                headers=CSRF,
            )
            assert r.status_code == 200, r.text
            x = next(m for m in r.json()["modelos"] if m["clave"] == "x")
            assert (x["protocolo"], x["precio_usd_mtok"]["salida"], x["seleccionado"]) == (
                "openai-chat",
                2,
                True,
            )
            # El perfil usa la suscripción sin effort; ya no avisa de nivel alguno.
            perfil = {
                "suscripcion": "minimax",
                "roles": {"refutador": {"modelo": {"compatible": "MiniMax-M3"}, "structured_outputs": True}},
                "gate": {"bajo": {"criticos": 1, "iteraciones": 1, "adversarial": False}},
                "exploradores": {"bajo": 1},
            }
            r = await c.put(f"/consola/api/orgs/{ORG}/perfiles/ligero", json=perfil, headers=CSRF)
            assert r.status_code == 200, r.text
            assert r.json()["avisos"] == []
            con_effort = {**perfil, "version": 1}
            con_effort["roles"] = {
                "refutador": {
                    "modelo": {"compatible": "MiniMax-M3"},
                    "effort": "high",
                    "structured_outputs": True,
                }
            }
            r = await c.put(f"/consola/api/orgs/{ORG}/perfiles/ligero", json=con_effort, headers=CSRF)
            assert r.status_code == 422 and "no admite effort high" in r.json()["detalle"]

    correr(caso())
    assert KEY not in _todo(m)


def test_la_consola_rechaza_un_servicio_desconocido_y_un_endpoint_con_servicio_conocido():
    m = _montaje()

    async def caso():
        async with m.cliente("tk-julian") as c:
            base = {"proveedor": "compatible", "nombre": "X", "clave": KEY}
            r = await c.put(f"{RUTA}/x", json={**base, "servicio": "inventado"}, headers=CSRF)
            assert r.status_code == 422 and r.json()["codigo"] == "servicio-desconocido"
            r = await c.put(
                f"{RUTA}/x",
                json={**base, "servicio": "minimax", "endpoint": "https://evil.example/v1"},
                headers=CSRF,
            )
            assert r.status_code == 422 and r.json()["codigo"] == "campos-invalidos"
            r = await c.put(f"{RUTA}/x", json={**base, "endpoint": "https://evil.example/v1"}, headers=CSRF)
            assert r.status_code == 422 and r.json()["codigo"] == "endpoint-no-permitido"
            assert (await c.get(RUTA)).json()["suscripciones"] == []

    correr(caso())
    assert KEY not in _todo(m)
