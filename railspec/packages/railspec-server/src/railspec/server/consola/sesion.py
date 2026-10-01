"""Sesiones de la consola y tokens ``rsc1`` (R2: el actor siempre sale del token).

Un único formato firmado con HMAC-SHA256, sin estado en el servidor:
``rsc1.<carga en base64url>.<firma en base64url>``. La carga lleva el tipo:

- ``sesion``: vive en la cookie HttpOnly ``railspec_sesion`` y solo vale para
  ``/consola/api``. Lleva los equipos de GitHub leídos al iniciar sesión, que
  la consola usa para resolver roles asignados a equipos (R3).
- ``api``: lo pide la SPA con ``POST /consola/api/auth/token`` y vale como
  ``Authorization: Bearer`` en ``/v1/*`` (tools y chat), canal ``consola``.
  Vida corta; la SPA lo renueva.

Cerrar sesión borra la cookie; un token ya emitido vale hasta que expira.
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

from ..api.identidad import TokenInvalido, con_equipos

log = logging.getLogger("railspec.consola")

PREFIJO = "rsc1"
COOKIE = "railspec_sesion"
COOKIE_ESTADO = "railspec_oauth"


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
        if not secreto:
            log.warning(
                "sin RAILSPEC_CONSOLA_SECRETO: clave de sesión efímera (no apta para varias réplicas)"
            )
            secreto = secrets.token_urlsafe(32)
        self._clave = hashlib.sha256(("railspec-consola:" + secreto).encode()).digest()
        self._reloj = reloj or (lambda: datetime.now(UTC))

    def _firma(self, texto: str) -> str:
        return _b64(hmac.new(self._clave, texto.encode(), hashlib.sha256).digest())

    def firmar(self, datos: dict[str, Any]) -> str:
        carga = _b64(json.dumps(datos, separators=(",", ":"), sort_keys=True).encode())
        return f"{PREFIJO}.{carga}.{self._firma(f'{PREFIJO}.{carga}')}"

    def abrir(self, token: str) -> dict[str, Any]:
        partes = token.split(".")
        if len(partes) != 3 or partes[0] != PREFIJO:
            raise TokenInvalido("token de consola mal formado")
        if not hmac.compare_digest(self._firma(f"{partes[0]}.{partes[1]}"), partes[2]):
            raise TokenInvalido("firma de token de consola inválida")
        try:
            datos = json.loads(_desb64(partes[1]))
        except ValueError as exc:
            raise TokenInvalido("carga de token de consola ilegible") from exc
        if not isinstance(datos, dict) or float(datos.get("exp", 0)) <= self._reloj().timestamp():
            raise TokenInvalido("token de consola expirado")
        return datos

    # --- sesiones -----------------------------------------------------------------

    def emitir(self, login: str, github_id: int, equipos: frozenset[int], vida: timedelta, tipo: str) -> str:
        exp = self._reloj() + vida
        return self.firmar(
            {"t": tipo, "l": login, "g": github_id, "e": sorted(equipos), "exp": int(exp.timestamp())}
        )

    def sesion(self, token: str, tipo: str) -> Sesion:
        datos = self.abrir(token)
        if datos.get("t") != tipo:
            raise TokenInvalido(f"se esperaba un token de {tipo}")
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
    se le delega sin cambios. El actor de un token ``rsc1`` lleva los equipos
    firmados en el token (``ActorConEquipos``); un token vencido o con la carga
    alterada no llega a ser actor.
    """

    def __init__(self, base: Any, firmador: Firmador) -> None:
        self.base = base
        self.firmador = firmador
        self.nombre = getattr(base, "nombre", "github")

    def actor_desde_token(self, token: str, canal: str) -> Actor:
        if token.startswith(PREFIJO + "."):
            sesion = self.firmador.sesion(token, "api")
            # Los equipos viajan firmados en el token (leídos en el login): el autorizador de /v1 y MCP
            # los usa para los roles por equipo, igual que la consola.
            return con_equipos(sesion.actor(Canal(canal)), sesion.equipos)
        return self.base.actor_desde_token(token, canal)

    def workspaces_visibles(self, actor: Actor, org: str) -> list[AlcanceWorkspace]:
        return self.base.workspaces_visibles(actor, org)

    def expira(self, token: str) -> datetime | None:
        if token.startswith(PREFIJO + "."):
            return self.firmador.sesion(token, "api").expira
        return self.base.expira(token)
