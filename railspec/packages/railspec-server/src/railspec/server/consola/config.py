"""Configuración de la consola web desde variables de entorno.

Todas son opcionales: sin GitHub App la consola solo admite tokens de
desarrollo o ``Authorization: Bearer``; sin secreto de sesión el servidor
genera uno efímero (las sesiones no sobreviven a un reinicio ni se comparten
entre réplicas, así que en AKS hay que fijarlo).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class ConfigGithubApp:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class ConfigConsola:
    #: Clave HMAC de las sesiones y tokens ``rsc1``. Vacía = efímera.
    secreto_sesion: str | None = None
    #: URL pública del servidor (``https://railspec.example.com``): base de la
    #: redirección de OAuth y de la cookie ``Secure``.
    url_publica: str | None = None
    github_app: ConfigGithubApp | None = None
    #: github_id de quienes administran la plataforma (crean organizaciones).
    administradores: frozenset[int] = frozenset()
    #: Carpeta con la SPA compilada (``dist`` de railspec-console). None = no se sirve.
    carpeta_spa: str | None = None
    horas_sesion: int = 8
    minutos_token: int = 60

    @property
    def cookie_segura(self) -> bool:
        return bool(self.url_publica and self.url_publica.startswith("https://"))

    @classmethod
    def desde_entorno(cls, entorno: Mapping[str, str] | None = None) -> ConfigConsola:
        env = os.environ if entorno is None else entorno
        app = None
        if env.get("RAILSPEC_GITHUB_APP_CLIENT_ID"):
            secreto = env.get("RAILSPEC_GITHUB_APP_CLIENT_SECRET")
            if not secreto:
                raise ValueError("RAILSPEC_GITHUB_APP_CLIENT_ID exige RAILSPEC_GITHUB_APP_CLIENT_SECRET")
            app = ConfigGithubApp(env["RAILSPEC_GITHUB_APP_CLIENT_ID"], secreto)
        admins = set()
        for crudo in filter(None, (p.strip() for p in env.get("RAILSPEC_CONSOLA_ADMINS", "").split(","))):
            if not crudo.isdigit():
                raise ValueError("RAILSPEC_CONSOLA_ADMINS espera github_id numéricos separados por coma")
            admins.add(int(crudo))
        url = (env.get("RAILSPEC_CONSOLA_URL") or "").rstrip("/") or None
        if app is not None and url is None:
            raise ValueError("la GitHub App exige RAILSPEC_CONSOLA_URL (base de la redirección de OAuth)")
        return cls(
            secreto_sesion=env.get("RAILSPEC_CONSOLA_SECRETO") or None,
            url_publica=url,
            github_app=app,
            administradores=frozenset(admins),
            carpeta_spa=env.get("RAILSPEC_CONSOLA_DIR") or None,
            horas_sesion=int(env.get("RAILSPEC_CONSOLA_SESION_HORAS") or 8),
        )
