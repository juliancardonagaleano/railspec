"""Servicios conocidos de proveedores compatibles (contrato 1.8) y lectura de su listado de modelos.

Un servicio fija las URL base y de qué protocolo habla cada modelo: OpenCode Zen enruta por modelo
(Claude y algunos Qwen por mensajes de Anthropic, los abiertos por chat completions) y su ``/models`` no lo
dice. Tablas según su documentación (``opencode.ai/docs/zen``, ``platform.minimax.io/docs``, 2026-10-06):
un id que no esté en ellas no se ofrece al descubrir, y un administrador lo puede declarar a mano con su
protocolo.

Se dejan fuera a propósito:

* Los modelos que Zen documenta como recolectores de datos (``*-free``, ``big-pickle``): ver
  ``railspec/docs/proveedores.md``. Se pueden declarar a mano.
* Los que hablan Responses (GPT, Grok, Muse), la API de Gemini o la de Jev: el adaptador solo habla
  ``anthropic-messages`` y ``openai-chat``.
* OpenCode Go: su página lo describe como pensado para agentes de código, vigila el tráfico y pide
  cabeceras de sesión; no es un servicio para un servidor. Quien lo quiera lo registra como personalizado.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from railspec.contracts.comun import Proveedor
from railspec.contracts.repositorio import Capacidades, ProtocoloCompatible

from .catalogo import EntradaCatalogo, capacidades_conocidas
from .compatible import USER_AGENT

#: Contexto de un modelo que la API no declara: el mismo valor conservador de ``capacidades_conocidas``.
CONTEXTO_DESCONOCIDO = 8_192

_ZEN_CHAT = frozenset(
    "qwen3.8-max deepseek-v4.1-flash deepseek-v4-pro deepseek-v4-flash deepseek-v4-flash-vision-exp "
    "minimax-m3 minimax-m2.7 minimax-m2.5 glm-5.3-flash glm-5.3 glm-5.2 glm-5.1 glm-5 "
    "kimi-k2.5 kimi-k2.6 kimi-k2.7-code kimi-k3".split()
)
_ZEN_MENSAJES = frozenset("qwen3.8-flash qwen3.7-max qwen3.7-plus qwen3.6-plus qwen3.5-plus".split())
_ENDPOINT_ZEN = "https://opencode.ai/zen/v1"


def _protocolo_zen(modelo: str) -> ProtocoloCompatible | None:
    if modelo.startswith("claude-") or modelo in _ZEN_MENSAJES:
        return ProtocoloCompatible.anthropic_messages
    return ProtocoloCompatible.openai_chat if modelo in _ZEN_CHAT else None


def _recolector_zen(modelo: str) -> bool:
    """Los que Zen documenta como recolectores de datos (gratis y de prueba)."""

    return modelo.endswith("-free") or modelo == "big-pickle" or "contributor" in modelo


def _capacidades_zen(modelo: str) -> Capacidades:
    if modelo.startswith("claude-"):
        return _emulada(capacidades_conocidas(modelo).contexto_max_tokens)
    return _emulada(CONTEXTO_DESCONOCIDO)


def _protocolo_minimax(modelo: str) -> ProtocoloCompatible | None:
    # Messages de Anthropic es la API que MiniMax recomienda; chat completions queda para declarar a mano.
    return ProtocoloCompatible.anthropic_messages if modelo.lower().startswith("minimax-") else None


def _capacidades_minimax(modelo: str) -> Capacidades:
    m = modelo.lower()
    if m.startswith("minimax-m3"):
        return _emulada(1_000_000)
    return _emulada(204_800 if m.startswith("minimax-m2") else CONTEXTO_DESCONOCIDO)


def _emulada(contexto: int) -> Capacidades:
    """``structured_outputs`` verdadero: el adaptador compatible lo emula (JSON validado); sin efforts."""

    return Capacidades(efforts=[], thinking=False, structured_outputs=True, contexto_max_tokens=contexto)


@dataclass(frozen=True)
class ServicioCompatible:
    id: str
    nombre: str
    endpoint: str | None
    endpoint_mensajes: str | None
    #: Retención y entrenamiento que declara el servicio: se muestra al elegirlo.
    nota: str
    protocolo_de: Callable[[str], ProtocoloCompatible | None]
    capacidades_de: Callable[[str], Capacidades]
    excluye: Callable[[str], bool] = lambda _: False

    def a_vista(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "nombre": self.nombre,
            "endpoint": self.endpoint,
            "endpoint_mensajes": self.endpoint_mensajes,
            "nota": self.nota,
        }


SERVICIOS: dict[str, ServicioCompatible] = {
    s.id: s
    for s in (
        ServicioCompatible(
            "opencode-zen",
            "OpenCode Zen",
            _ENDPOINT_ZEN,
            "https://opencode.ai/zen",
            "Pasarela alojada en EE. UU.; OpenAI y Anthropic retienen 30 días. "
            "Tiene otras condiciones de retención y región que Foundry: úsalo a conciencia.",
            _protocolo_zen,
            _capacidades_zen,
            _recolector_zen,
        ),
        ServicioCompatible(
            "minimax",
            "MiniMax",
            "https://api.minimax.io/v1",
            "https://api.minimax.io/anthropic",
            "Sus términos permiten usar entradas y salidas para mejorar el servicio. "
            "Tiene otras condiciones de retención y región que Foundry: úsalo a conciencia.",
            _protocolo_minimax,
            _capacidades_minimax,
        ),
    )
}

#: Hosts de los servicios conocidos (siempre permitidos); los personalizados piden RAILSPEC_COMPATIBLES_HOSTS.
HOSTS_CONOCIDOS = ("opencode.ai", "api.minimax.io")


def capacidades_de(servicio: ServicioCompatible | None, modelo: str) -> Capacidades:
    return servicio.capacidades_de(modelo) if servicio is not None else _emulada(CONTEXTO_DESCONOCIDO)


def protocolo_por_defecto(endpoint: str | None) -> ProtocoloCompatible:
    return ProtocoloCompatible.openai_chat if endpoint else ProtocoloCompatible.anthropic_messages


async def leer_modelos(
    servicio: ServicioCompatible | None,
    endpoint: str | None,
    endpoint_mensajes: str | None,
    clave: str,
    *,
    transporte: Any | None = None,
    tope_s: float = 30.0,
) -> list[EntradaCatalogo]:
    """``GET <base>/models`` (OpenAI) o ``<base>/v1/models`` (Anthropic) y los ids que el servicio soporta."""

    import httpx

    if endpoint:
        url, cabeceras = endpoint.rstrip("/") + "/models", {"Authorization": f"Bearer {clave}"}
    else:
        assert endpoint_mensajes is not None
        url = endpoint_mensajes.rstrip("/") + "/v1/models"
        cabeceras = {
            "x-api-key": clave,
            "anthropic-version": "2023-06-01",
            "Authorization": f"Bearer {clave}",
        }
    async with httpx.AsyncClient(
        timeout=tope_s, follow_redirects=False, transport=transporte, headers={"User-Agent": USER_AGENT}
    ) as cliente:
        r = await cliente.get(url, headers=cabeceras)
    r.raise_for_status()
    cuerpo = r.json()
    filas = cuerpo.get("data") if isinstance(cuerpo, dict) else None
    if not isinstance(filas, list):
        raise ValueError("respuesta de modelos sin lista data")
    ids = sorted({str(f["id"]) for f in filas if isinstance(f, dict) and isinstance(f.get("id"), str)})
    entradas = []
    for id_ in ids:
        if servicio is not None and servicio.excluye(id_):
            continue
        protocolo = servicio.protocolo_de(id_) if servicio is not None else protocolo_por_defecto(endpoint)
        if protocolo is None or not _hay_endpoint(protocolo, endpoint, endpoint_mensajes):
            continue
        caps = capacidades_de(servicio, id_)
        entradas.append(
            EntradaCatalogo(Proveedor.compatible, id_, None, "externo", None, caps, None, protocolo)
        )
    return entradas


def _hay_endpoint(
    protocolo: ProtocoloCompatible, endpoint: str | None, endpoint_mensajes: str | None
) -> bool:
    return bool(endpoint if protocolo == ProtocoloCompatible.openai_chat else endpoint_mensajes)
