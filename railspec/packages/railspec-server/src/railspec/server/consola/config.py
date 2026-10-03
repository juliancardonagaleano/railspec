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
from typing import Any

#: Largo mínimo del secreto de sesión (``openssl rand -base64 48`` da 64).
MIN_SECRETO = 32
#: Vida por defecto de la sesión y tope configurable (horas).
HORAS_SESION = 4
MAX_HORAS_SESION = 24
#: Peticiones por minuto y por IP en ``/consola/api/auth/*`` (0 = sin límite).
LIMITE_AUTH_MINUTO = 60
#: Con https las cookies llevan este prefijo: el navegador las rechaza si no son ``Secure``.
#: (``__Host-`` exigiría ``Path=/``, y las cookies de la consola van en ``/consola``.)
PREFIJO_SEGURO = "__Secure-"


def _horas_sesion(crudo: str | None) -> int:
    if not crudo:
        return HORAS_SESION
    try:
        horas = int(crudo)
    except ValueError:
        horas = 0
    if not 1 <= horas <= MAX_HORAS_SESION:
        raise ValueError(
            f"RAILSPEC_CONSOLA_SESION_HORAS debe ser un entero de 1 a {MAX_HORAS_SESION} "
            "(los equipos de GitHub de la cookie quedan congelados durante toda la sesión)"
        )
    return horas


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


def _limite_auth(crudo: str | None) -> int:
    if not crudo:
        return LIMITE_AUTH_MINUTO
    try:
        limite = int(crudo)
    except ValueError:
        limite = -1
    if limite < 0:
        raise ValueError("RAILSPEC_CONSOLA_AUTH_LIMITE debe ser un entero >= 0 (peticiones por minuto y IP)")
    return limite


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
    #: Vida de la sesión. También es cuánto tiempo quedan congelados los equipos de GitHub que
    #: lleva la cookie (se leen al iniciar sesión): por eso es corta y tiene tope.
    horas_sesion: int = HORAS_SESION
    minutos_token: int = 60
    #: Eventos en vivo (SSE): conexiones abiertas a la vez por persona y en total, por réplica.
    #: Al exceder el tope, la ruta responde 429 con ``Retry-After``.
    sse_max_por_usuario: int = 5
    sse_max_global: int = 200
    #: Cada cuántos segundos un flujo SSE vuelve a comprobar que la persona sigue con rol ``lector``.
    sse_revalidar_s: float = 30.0
    #: Peticiones por minuto y por IP en ``/consola/api/auth/*`` (0 = sin límite).
    limite_auth_minuto: int = LIMITE_AUTH_MINUTO

    @property
    def cookie_segura(self) -> bool:
        return bool(self.url_publica and self.url_publica.startswith("https://"))

    def nombre_cookie(self, base: str) -> str:
        """Nombre real de una cookie: con URL https lleva el prefijo ``__Secure-``."""

        return PREFIJO_SEGURO + base if self.cookie_segura else base

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
        # En Render, RENDER_EXTERNAL_URL (la fija la plataforma) hace de URL pública cuando no hay una propia.
        url = (env.get("RAILSPEC_CONSOLA_URL") or env.get("RENDER_EXTERNAL_URL") or "").rstrip("/") or None
        if app is not None and url is None:
            raise ValueError("la GitHub App exige RAILSPEC_CONSOLA_URL (base de la redirección de OAuth)")
        config = cls(
            secreto_sesion=env.get("RAILSPEC_CONSOLA_SECRETO") or None,
            url_publica=url,
            github_app=app,
            administradores=frozenset(admins),
            carpeta_spa=env.get("RAILSPEC_CONSOLA_DIR") or None,
            horas_sesion=_horas_sesion(env.get("RAILSPEC_CONSOLA_SESION_HORAS")),
            limite_auth_minuto=_limite_auth(env.get("RAILSPEC_CONSOLA_AUTH_LIMITE")),
            sse_max_por_usuario=_positivo(env, "RAILSPEC_CONSOLA_SSE_MAX_USUARIO", 5, int),
            sse_max_global=_positivo(env, "RAILSPEC_CONSOLA_SSE_MAX_GLOBAL", 200, int),
            sse_revalidar_s=_positivo(env, "RAILSPEC_CONSOLA_SSE_REVALIDAR_S", 30.0, float),
        )
        validar_secreto(config.secreto_sesion, exigido=config.exige_secreto)
        return config


def _positivo(env: Mapping[str, str], nombre: str, defecto: Any, tipo: type) -> Any:
    crudo = env.get(nombre)
    if not crudo:
        return defecto
    try:
        valor = tipo(crudo)
    except ValueError as exc:
        raise ValueError(f"{nombre} espera un número, no {crudo!r}") from exc
    if valor <= 0:
        raise ValueError(f"{nombre} debe ser mayor que cero")
    return valor
