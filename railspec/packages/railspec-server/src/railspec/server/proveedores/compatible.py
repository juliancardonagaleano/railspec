"""Proveedores compatibles (contrato 1.8): endpoints con la API de Anthropic o la de OpenAI.

Cubre OpenCode Zen, MiniMax y cualquier servicio que hable uno de esos dos protocolos. Un modelo
declara su protocolo (``anthropic-messages`` u ``openai-chat``) y el adaptador llama por el SDK oficial
correspondiente, apuntado a la URL base de la suscripción.

**Salida estructurada emulada.** Ninguno de esos servicios documenta ``json_schema`` ni
``output_config.format`` (ver ``railspec/docs/proveedores.md``), así que el adaptador pide el JSON en el
prompt, lo extrae de la respuesta, lo valida contra el esquema Pydantic y, si no encaja, repite la
llamada una vez con el error. Una respuesta que sigue sin encajar es ``ErrorProveedor``: el gate
la convierte en ``escalado`` y nunca aprueba por falta de respuesta. El prompt estable (``sistema``) va
primero y la instrucción de formato después, para no romper el prefijo cacheable.

Los dos SDK se importan solo al construir el cliente; las pruebas inyectan clientes dobles con
``messages.create`` y ``chat.completions.create``.
"""

from __future__ import annotations

import json
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from railspec.contracts.comun import Proveedor
from railspec.contracts.repositorio import PrecioModelo, ProtocoloCompatible

from .base import ErrorProveedor, PeticionModelo, RespuestaModelo, T, Uso, costo_estimado

#: Cabecera de identificación: algunos servicios (OpenCode) piden un user agent propio, no el del SDK.
USER_AGENT = "railspec-server"
#: Reintentos de formato: una vez más con el error de validación a la vista del modelo.
REINTENTOS_FORMATO = 1

_RAZONAMIENTO = re.compile(r"<think(?:ing)?>.*?</think(?:ing)?>", re.DOTALL | re.IGNORECASE)
_CERCA = re.compile(r"^```[a-zA-Z0-9_-]*\s*\n(.*?)\n?```\s*$", re.DOTALL)
#: ``stop_reason`` / ``finish_reason`` que no dejan una salida utilizable.
_CORTADAS = {"max_tokens", "length", "refusal", "content_filter", "model_context_window_exceeded"}


def instruccion_formato(esquema: type[Any]) -> str:
    return (
        "Responde únicamente con un objeto JSON que cumpla este esquema JSON. Sin texto antes ni después, "
        "sin bloques de código y sin comentarios.\n"
        + json.dumps(esquema.model_json_schema(), ensure_ascii=False, separators=(",", ":"))
    )


def extraer_json(texto: str) -> str:
    """El objeto JSON de una respuesta de texto, sin razonamiento, cercas de código ni prosa alrededor."""

    limpio = _RAZONAMIENTO.sub("", texto).strip()
    cerca = _CERCA.match(limpio)
    if cerca:
        limpio = cerca.group(1).strip()
    if limpio.startswith("{"):
        return limpio
    inicio, fin = limpio.find("{"), limpio.rfind("}")
    return limpio[inicio : fin + 1] if inicio != -1 and fin > inicio else limpio


@dataclass(frozen=True)
class _Salida:
    texto: str
    uso: Uso


class AdaptadorCompatible:
    """``ProveedorModelo`` sobre un cliente de mensajes de Anthropic y/o uno de chat completions de OpenAI.

    ``protocolos`` dice por modelo (clave de ``ModeloSuscripcion``) qué API habla; ``precios`` da la tarifa
    de los que la tienen (sin ella el costo sale de ``PRECIOS_USD_MTOK`` o es 0).
    """

    proveedor = Proveedor.compatible
    #: Sin región conocida: no sirve a ``restringido`` ni ``interno`` (``hosting`` es ``externo``).
    region: str | None = None

    def __init__(
        self,
        protocolos: Mapping[str, ProtocoloCompatible],
        *,
        cliente_mensajes: Any | None = None,
        cliente_chat: Any | None = None,
        precios: Mapping[str, PrecioModelo] | None = None,
    ) -> None:
        self._protocolos = dict(protocolos)
        self._mensajes = cliente_mensajes
        self._chat = cliente_chat
        self._precios = dict(precios or {})

    async def completar(self, peticion: PeticionModelo[T]) -> RespuestaModelo[T]:
        protocolo = self._protocolos.get(peticion.destino)
        if protocolo is None:
            raise ErrorProveedor(f"compatible/{peticion.destino}: la suscripción no declara su protocolo")
        inicio = time.monotonic()
        sistema = f"{peticion.sistema}\n\n{instruccion_formato(peticion.esquema)}"
        conversacion: list[tuple[str, str]] = [("user", peticion.contenido)]
        uso = Uso()
        ultimo_error = ""
        for intento in range(REINTENTOS_FORMATO + 1):
            try:
                salida = await self._llamar(protocolo, peticion, sistema, conversacion)
            except ErrorProveedor as exc:
                raise ErrorProveedor(str(exc), duracion_ms=_ms(inicio)) from exc
            except Exception as exc:  # red, cuota, 4xx/5xx del SDK: todo escala igual
                raise ErrorProveedor(
                    f"compatible/{peticion.destino}: {type(exc).__name__}: {exc}", duracion_ms=_ms(inicio)
                ) from exc
            uso = uso + salida.uso
            try:
                valor = peticion.esquema.model_validate_json(extraer_json(salida.texto))
            except ValidationError as exc:
                ultimo_error = _resumen(exc)
                if intento < REINTENTOS_FORMATO:
                    conversacion += [
                        ("assistant", salida.texto or "(vacía)"),
                        (
                            "user",
                            "Tu respuesta no cumple el esquema: "
                            f"{ultimo_error}. Responde de nuevo solo con el JSON corregido.",
                        ),
                    ]
                continue
            return RespuestaModelo(
                valor=valor,
                uso=self._con_costo(peticion, Uso(**{**uso.__dict__, "duracion_ms": _ms(inicio)})),
                proveedor=self.proveedor,
                modelo=peticion.modelo,
                region=peticion.region or self.region,
            )
        raise ErrorProveedor(
            f"compatible/{peticion.destino}: salida fuera de esquema tras {REINTENTOS_FORMATO + 1} intentos: "
            f"{ultimo_error}",
            duracion_ms=_ms(inicio),
        )

    # --- llamadas por protocolo ------------------------------------------------------------------

    async def _llamar(
        self,
        protocolo: ProtocoloCompatible,
        peticion: PeticionModelo[Any],
        sistema: str,
        conversacion: list[tuple[str, str]],
    ) -> _Salida:
        if protocolo == ProtocoloCompatible.anthropic_messages:
            return await self._llamar_mensajes(peticion, sistema, conversacion)
        return await self._llamar_chat(peticion, sistema, conversacion)

    async def _llamar_mensajes(
        self, peticion: PeticionModelo[Any], sistema: str, conversacion: list[tuple[str, str]]
    ) -> _Salida:
        if self._mensajes is None:
            raise ErrorProveedor(
                f"compatible/{peticion.destino}: la suscripción no tiene endpoint de mensajes"
            )
        r = await self._mensajes.messages.create(
            model=peticion.destino,
            max_tokens=peticion.max_tokens,
            system=sistema,
            messages=[{"role": rol, "content": texto} for rol, texto in conversacion],
        )
        motivo = str(getattr(r, "stop_reason", "") or "")
        if motivo in _CORTADAS:
            raise ErrorProveedor(f"{peticion.destino}: respuesta cortada ({motivo})")
        texto = "".join(
            getattr(b, "text", "") or "" for b in (getattr(r, "content", None) or []) if _tipo(b) == "text"
        )
        u = getattr(r, "usage", None)
        return _Salida(
            texto,
            Uso(
                tokens_entrada=_entero(u, "input_tokens"),
                tokens_salida=_entero(u, "output_tokens"),
                tokens_cache_lectura=_entero(u, "cache_read_input_tokens"),
                tokens_cache_escritura=_entero(u, "cache_creation_input_tokens"),
            ),
        )

    async def _llamar_chat(
        self, peticion: PeticionModelo[Any], sistema: str, conversacion: list[tuple[str, str]]
    ) -> _Salida:
        if self._chat is None:
            raise ErrorProveedor(f"compatible/{peticion.destino}: la suscripción no tiene endpoint de chat")
        r = await self._chat.chat.completions.create(
            model=peticion.destino,
            max_tokens=peticion.max_tokens,
            messages=[
                {"role": "system", "content": sistema},
                *({"role": rol, "content": texto} for rol, texto in conversacion),
            ],
        )
        eleccion = (getattr(r, "choices", None) or [None])[0]
        if eleccion is None:
            raise ErrorProveedor(f"{peticion.destino}: respuesta sin choices")
        motivo = str(getattr(eleccion, "finish_reason", "") or "")
        if motivo in _CORTADAS:
            raise ErrorProveedor(f"{peticion.destino}: respuesta cortada ({motivo})")
        mensaje = eleccion.message
        if getattr(mensaje, "refusal", None):
            raise ErrorProveedor(f"{peticion.destino}: rechazo del modelo")
        u = getattr(r, "usage", None)
        cache = _entero(getattr(u, "prompt_tokens_details", None), "cached_tokens")
        return _Salida(
            getattr(mensaje, "content", None) or "",
            Uso(
                # Mismo criterio que la Messages API: entrada sin lo leído de caché.
                tokens_entrada=max(_entero(u, "prompt_tokens") - cache, 0),
                tokens_salida=_entero(u, "completion_tokens"),
                tokens_cache_lectura=cache,
            ),
        )

    def _con_costo(self, peticion: PeticionModelo[Any], uso: Uso) -> Uso:
        precio = self._precios.get(peticion.destino)
        if precio is None:
            costo = costo_estimado(peticion.modelo, uso)
        else:
            costo = round(
                (
                    uso.tokens_entrada * precio.entrada
                    + uso.tokens_salida * precio.salida
                    + uso.tokens_cache_lectura * precio.cache_lectura
                )
                / 1e6,
                6,
            )
        return Uso(**{**uso.__dict__, "costo_usd": costo})


def _tipo(bloque: Any) -> str:
    return str(bloque.get("type") if isinstance(bloque, dict) else getattr(bloque, "type", ""))


def _resumen(exc: ValidationError) -> str:
    """Los primeros errores del esquema, sin el valor recibido (puede traer texto del repositorio)."""

    partes = [f"{'.'.join(str(p) for p in e['loc']) or 'raíz'}: {e['msg']}" for e in exc.errors()[:3]]
    return "; ".join(partes)


def _ms(inicio: float) -> int:
    return int((time.monotonic() - inicio) * 1000)


def _entero(objeto: Any, campo: str) -> int:
    return int(getattr(objeto, campo, 0) or 0) if objeto is not None else 0


# --- clientes ----------------------------------------------------------------------------------


def cliente_mensajes(endpoint_mensajes: str, api_key: str, **opciones: Any) -> Any:
    """``AsyncAnthropic`` sobre la URL base de mensajes de la suscripción."""

    from anthropic import AsyncAnthropic

    return AsyncAnthropic(
        api_key=api_key,
        base_url=endpoint_mensajes,
        default_headers={"User-Agent": USER_AGENT},
        **{"timeout": 300.0, **opciones},
    )


def cliente_chat(endpoint: str, api_key: str, **opciones: Any) -> Any:
    """``AsyncOpenAI`` sobre la URL base de chat completions de la suscripción."""

    from openai import AsyncOpenAI

    return AsyncOpenAI(
        api_key=api_key,
        base_url=endpoint.rstrip("/") + "/",
        default_headers={"User-Agent": USER_AGENT},
        **{"timeout": 300.0, **opciones},
    )
