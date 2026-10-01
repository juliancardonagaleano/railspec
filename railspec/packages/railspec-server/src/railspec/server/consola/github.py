"""Inicio de sesión con la GitHub App (flujo web de OAuth para usuarios de la App).

El token de usuario de GitHub solo se usa dentro del callback (leer el
usuario y sus equipos); la consola nunca lo guarda y, al terminar, lo revoca en
GitHub (``DELETE /applications/{client_id}/token``, mejor esfuerzo): un token
que ya no sirve a la consola tampoco debe seguir sirviendo a nadie más.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote, urlencode

from ..api.identidad import equipos_de_usuario
from .config import ConfigGithubApp

AUTORIZAR = "https://github.com/login/oauth/authorize"
TOKEN = "https://github.com/login/oauth/access_token"
API = "https://api.github.com"

log = logging.getLogger("railspec.consola")

#: Login de GitHub: alfanumérico y guiones, sin guion inicial, hasta 39 caracteres.
PATRON_LOGIN = r"[A-Za-z0-9][A-Za-z0-9-]{0,38}"
_LOGIN = re.compile(PATRON_LOGIN)


class ErrorGithub(Exception):
    pass


@dataclass(frozen=True)
class UsuarioGithub:
    login: str
    github_id: int
    equipos: frozenset[int]


class ClienteGithub:
    """``cliente``: un ``httpx.Client`` (las pruebas inyectan un transporte simulado)."""

    def __init__(self, app: ConfigGithubApp | None, cliente: Any | None = None) -> None:
        self.app = app
        self._cliente = cliente

    def _http(self):
        if self._cliente is None:
            import httpx

            self._cliente = httpx.Client(timeout=10)
        return self._cliente

    def url_autorizar(self, redireccion: str, estado: str) -> str:
        if self.app is None:
            raise ErrorGithub("la GitHub App no está configurada")
        return f"{AUTORIZAR}?" + urlencode(
            {"client_id": self.app.client_id, "redirect_uri": redireccion, "state": estado}
        )

    def usuario_desde_codigo(self, codigo: str, redireccion: str) -> UsuarioGithub:
        if self.app is None:
            raise ErrorGithub("la GitHub App no está configurada")
        r = self._http().post(
            TOKEN,
            data={
                "client_id": self.app.client_id,
                "client_secret": self.app.client_secret,
                "code": codigo,
                "redirect_uri": redireccion,
            },
            headers={"Accept": "application/json"},
        )
        datos = r.json() if r.status_code == 200 else {}
        token = datos.get("access_token")
        if not token:
            raise ErrorGithub(f"GitHub no entregó token ({datos.get('error') or r.status_code})")
        try:
            cabeceras = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
            u = self._http().get(f"{API}/user", headers=cabeceras)
            if u.status_code != 200:
                raise ErrorGithub(f"GitHub rechazó el token de usuario ({u.status_code})")
            usuario = u.json()
            return UsuarioGithub(usuario["login"], int(usuario["id"]), self._equipos(cabeceras))
        finally:
            self._revocar(token)

    def _revocar(self, token: str) -> None:
        """Revoca el token de usuario en GitHub. Mejor esfuerzo: si falla, el login no se rompe."""

        if self.app is None:
            return
        try:
            r = self._http().request(
                "DELETE",
                f"{API}/applications/{self.app.client_id}/token",
                json={"access_token": token},
                auth=(self.app.client_id, self.app.client_secret),
                headers={"Accept": "application/vnd.github+json"},
            )
            if r.status_code not in (204, 404):  # 404: ya no existía
                log.warning("GitHub no revocó el token de usuario (HTTP %s)", r.status_code)
        except Exception as exc:  # red caída, cliente inyectado, etc.: nunca rompe el login
            log.warning("no se pudo revocar el token de usuario de GitHub (%s)", type(exc).__name__)

    def _equipos(self, cabeceras: dict[str, str]) -> frozenset[int]:
        """Equipos del usuario (permiso de la App: Members, lectura). Sin permiso, ninguno."""

        return equipos_de_usuario(self._http(), cabeceras)

    def id_de_login(self, login: str) -> int | None:
        """``GET /users/{login}`` (público). None si no existe o si no es un login de GitHub.

        El login se valida antes de armar la ruta: ``../orgs/x`` no debe llegar a
        otro endpoint de ``api.github.com`` (httpx normaliza los ``..``).
        """

        if not _LOGIN.fullmatch(login):
            return None
        r = self._http().get(
            f"{API}/users/{quote(login, safe='')}", headers={"Accept": "application/vnd.github+json"}
        )
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ErrorGithub(f"GitHub respondió {r.status_code} al buscar {login}")
        return int(r.json()["id"])
