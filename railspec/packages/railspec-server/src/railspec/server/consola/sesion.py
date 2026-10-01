"""Sesiones de la consola y tokens ``rsc1`` (R2: el actor siempre sale del token).

Un único formato firmado con HMAC-SHA256:
``rsc1.<carga en base64url>.<firma en base64url>``. La carga lleva el tipo
(``t``) y la audiencia (``aud``):

- ``sesion`` (aud ``consola``): vive en la cookie HttpOnly ``railspec_sesion``
  y solo vale para ``/consola/api``. Lleva los equipos de GitHub leídos al
  iniciar sesión, que la consola usa para resolver roles asignados a equipos (R3).
- ``api`` (aud ``v1``): lo pide la SPA con ``POST /consola/api/auth/token`` (con
  la cookie) y vale como ``Authorization: Bearer`` en ``/v1/*`` (tools y chat),
  canal ``consola``. Vida corta; la SPA lo renueva. No vale en ``/consola/api``.
- ``oauth`` (aud ``oauth``): el ``state`` firmado del flujo de GitHub.

Cada tipo se firma con su propia subclave (HKDF-SHA256 del secreto): ``abrir``
exige el tipo esperado y un token de un tipo no se abre como otro aunque se
reescriba su claim.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from railspec.contracts.comun import Actor, AlcanceWorkspace, Canal, TipoActor

from ..api.identidad import TokenInvalido
from .config import validar_secreto

log = logging.getLogger("railspec.consola")

PREFIJO = "rsc1"
COOKIE = "railspec_sesion"
COOKIE_ESTADO = "railspec_oauth"
#: Tipo de token → audiencia (claim ``aud``) en la que vale.
AUDIENCIAS = {"sesion": "consola", "api": "v1", "oauth": "oauth"}


def _b64(datos: bytes) -> str:
    return base64.urlsafe_b64encode(datos).decode().rstrip("=")


def _desb64(texto: str) -> bytes:
    return base64.urlsafe_b64decode(texto + "=" * (-len(texto) % 4))


@dataclass(frozen=True)
class Sesion:
    login: str
    github_id: int
    expira: datetime
    equipos: frozenset[int] = field(default_factory=frozenset)
    tipo: str = "sesion"

    def actor(self, canal: Canal = Canal.consola) -> Actor:
        return Actor(tipo=TipoActor.humano, canal=canal, github_id=self.github_id, login=self.login)


class Firmador:
    def __init__(self, secreto: str | None, reloj: Any = None) -> None:
        # Un secreto explícito débil nunca se acepta; la ausencia (clave efímera) la
        # veta ``montar_consola`` cuando hay https o GitHub App.
        validar_secreto(secreto, exigido=False)
        self.efimero = not secreto
        if not secreto:
            log.warning(
                "sin RAILSPEC_CONSOLA_SECRETO: clave de sesión efímera (no apta para varias réplicas)"
            )
            secreto = secrets.token_urlsafe(32)
        # HKDF (RFC 5869) con SHA-256: una subclave por tipo de token. Extract con sal fija
        # y Expand de un bloque (32 bytes) con el tipo como ``info``.
        prk = hmac.new(b"railspec-consola/rsc1", secreto.encode(), hashlib.sha256).digest()
        self._claves = {
            tipo: hmac.new(prk, f"railspec-consola/rsc1/{tipo}".encode() + b"\x01", hashlib.sha256).digest()
            for tipo in AUDIENCIAS
        }
        self._reloj = reloj or (lambda: datetime.now(UTC))

    def _firma(self, tipo: str, texto: str) -> str:
        return _b64(hmac.new(self._claves[tipo], texto.encode("utf-8", "replace"), hashlib.sha256).digest())

    def firmar(self, tipo: str, datos: dict[str, Any]) -> str:
        """Firma ``datos`` como token de ``tipo`` (pone ``t`` y ``aud``)."""

        if tipo not in AUDIENCIAS:
            raise ValueError(f"tipo de token de consola desconocido: {tipo!r}")
        completos = {**datos, "t": tipo, "aud": AUDIENCIAS[tipo]}
        carga = _b64(json.dumps(completos, separators=(",", ":"), sort_keys=True).encode())
        return f"{PREFIJO}.{carga}.{self._firma(tipo, f'{PREFIJO}.{tipo}.{carga}')}"

    def abrir(self, token: str, tipo: str) -> dict[str, Any]:
        """Verifica firma (con la subclave de ``tipo``), tipo, audiencia y vigencia."""

        if tipo not in AUDIENCIAS:
            raise ValueError(f"tipo de token de consola desconocido: {tipo!r}")
        partes = token.split(".")
        if len(partes) != 3 or partes[0] != PREFIJO:
            raise TokenInvalido("token de consola mal formado")
        # Bytes, no texto: compare_digest lanza TypeError con un ``str`` no ASCII.
        esperada = self._firma(tipo, f"{partes[0]}.{tipo}.{partes[1]}").encode()
        if not hmac.compare_digest(esperada, partes[2].encode("utf-8", "replace")):
            raise TokenInvalido("firma de token de consola inválida")
        try:
            datos = json.loads(_desb64(partes[1]))
            if not isinstance(datos, dict):
                raise ValueError("la carga no es un objeto")
            caduca = float(datos.get("exp", 0))
        except (ValueError, TypeError) as exc:
            raise TokenInvalido("carga de token de consola ilegible") from exc
        if datos.get("t") != tipo or datos.get("aud") != AUDIENCIAS[tipo]:
            raise TokenInvalido(f"se esperaba un token de {tipo}")
        if not caduca > self._reloj().timestamp():
            raise TokenInvalido("token de consola expirado")
        return datos

    # --- sesiones -----------------------------------------------------------------

    def emitir(self, login: str, github_id: int, equipos: frozenset[int], vida: timedelta, tipo: str) -> str:
        exp = self._reloj() + vida
        return self.firmar(
            tipo, {"l": login, "g": github_id, "e": sorted(equipos), "exp": int(exp.timestamp())}
        )

    def sesion(self, token: str, tipo: str) -> Sesion:
        datos = self.abrir(token, tipo)
        try:
            return Sesion(
                login=str(datos["l"]),
                github_id=int(datos["g"]),
                expira=datetime.fromtimestamp(int(datos["exp"]), UTC),
                equipos=frozenset(int(e) for e in datos.get("e", [])),
                tipo=tipo,
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise TokenInvalido("carga de token de consola incompleta") from exc

    def ahora(self) -> datetime:
        return self._reloj()


class IdentidadConConsola:
    """Identidad del servidor que además acepta tokens ``rsc1`` de tipo ``api``.

    Envuelve la identidad compuesta (GitHub u OIDC de Actions): todo lo demás
    se le delega sin cambios. El token ``api`` es la credencial de ``/v1`` y del
    chat (canal ``consola``); no vale como credencial del arnés (``/mcp``).
    """

    def __init__(self, base: Any, firmador: Firmador) -> None:
        self.base = base
        self.firmador = firmador
        self.nombre = getattr(base, "nombre", "github")

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        if token.startswith(PREFIJO + "."):
            if canal != Canal.consola:
                raise TokenInvalido("los tokens de la consola solo valen en el canal consola")
            return self.firmador.sesion(token, "api").actor(Canal(canal))
        return self.base.actor_desde_token(token, canal)

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return self.base.workspaces_visibles(actor, org)

    def expira(self, token: str) -> datetime | None:
        if token.startswith(PREFIJO + "."):
            return self.firmador.sesion(token, "api").expira
        return self.base.expira(token)
