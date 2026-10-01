"""Configuración de la consola web desde variables de entorno.

Todas son opcionales en desarrollo: sin GitHub App la consola solo admite
tokens de desarrollo o ``Authorization: Bearer``; sin secreto de sesión el
servidor genera uno efímero (las sesiones no sobreviven a un reinicio ni se
comparten entre réplicas). Con URL pública https o con GitHub App el secreto
es obligatorio y debe tener al menos ``MIN_SECRETO`` caracteres: el estado de
OAuth que entrega ``/auth/github/inicio`` es texto conocido más su MAC, así que
una clave débil se rompe sin conexión y permitiría forjar sesiones.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

#: Largo mínimo del secreto de sesión (``openssl rand -base64 48`` da 64).
MIN_SECRETO = 32


def validar_secreto(secreto: str | None, *, exigido: bool) -> None:
    """Un secreto explícito debe ser fuerte; uno ausente solo se admite si no ``exigido``."""

    if not secreto:
        if exigido:
            raise ValueError(
                "RAILSPEC_CONSOLA_SECRETO es obligatorio con URL pública https o GitHub App "
                f"(mínimo {MIN_SECRETO} caracteres, p. ej. `openssl rand -base64 48`): sin él cada "
                "réplica firma con una clave distinta y las sesiones no se comparten"
            )
        return
    if len(secreto) < MIN_SECRETO:
        raise ValueError(
            f"RAILSPEC_CONSOLA_SECRETO debe tener al menos {MIN_SECRETO} caracteres "
            f"(tiene {len(secreto)}): una clave débil permite forjar sesiones"
        )


@dataclass(frozen=True)
class ConfigGithubApp:
    client_id: str
    client_secret: str


@dataclass(frozen=True)
class ConfigConsola:
    #: Clave HMAC de las sesiones y tokens ``rsc1`` (mínimo ``MIN_SECRETO``). Vacía = efímera,
    #: solo admitida sin URL https ni GitHub App.
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

    @property
    def exige_secreto(self) -> bool:
        """Con https o GitHub App la clave de sesión no puede ser efímera ni débil."""

        return self.cookie_segura or self.github_app is not None

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
        config = cls(
            secreto_sesion=env.get("RAILSPEC_CONSOLA_SECRETO") or None,
            url_publica=url,
            github_app=app,
            administradores=frozenset(admins),
            carpeta_spa=env.get("RAILSPEC_CONSOLA_DIR") or None,
            horas_sesion=int(env.get("RAILSPEC_CONSOLA_SESION_HORAS") or 8),
        )
        validar_secreto(config.secreto_sesion, exigido=config.exige_secreto)
        return config
