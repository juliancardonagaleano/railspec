"""Sesión del desarrollador: el token de usuario de la GitHub App de Railspec.

``railspec login`` obtiene el token con el device flow de GitHub y lo guarda aquí, por usuario y por
servidor. El proxy lo usa cuando no hay ``RAILSPEC_TOKEN`` (que tiene prioridad) y lo relee en cada
petición: un ``railspec login`` hecho con el arnés abierto vale sin reiniciarlo.

El archivo vive fuera de cualquier repositorio (``~/.config/railspec/credenciales.json``), con modo 0600 en
una carpeta 0700, y se reemplaza de forma atómica. No se guarda el refresh token: renovar el token de
usuario de una GitHub App exige su client secret, que no puede viajar a las máquinas de los desarrolladores;
un token vencido se resuelve volviendo a ejecutar ``railspec login``.
"""

from __future__ import annotations

import json
import os
import stat
import tempfile
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit, urlunsplit

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError

from . import config
from .errores import CredencialesInvalidas

VERSION_ARCHIVO = 1

#: Un token que vence dentro de este margen ya no se manda: llegaría vencido al servidor.
MARGEN_VENCIMIENTO_S = 60


def ahora_utc() -> datetime:
    return datetime.now(UTC)


def clave_servidor(url: str) -> str:
    """Identifica al servidor sin que importen la mayúscula del host, la barra final ni el query."""

    partes = urlsplit(url.strip())
    return urlunsplit((partes.scheme.lower(), partes.netloc.lower(), partes.path.rstrip("/"), "", ""))


def ruta_por_defecto(entorno: Mapping[str, str] | None = None) -> Path:
    env = os.environ if entorno is None else entorno
    if env.get(config.ENV_CREDENCIALES):
        return Path(env[config.ENV_CREDENCIALES]).expanduser()
    base = Path(env.get("XDG_CONFIG_HOME") or "")
    if not base.is_absolute():  # la especificación XDG manda ignorar una ruta relativa
        try:
            base = Path.home() / ".config"
        except RuntimeError as exc:  # sin HOME ni entrada en passwd (contenedor con uid arbitrario)
            raise CredencialesInvalidas(
                "No se pudo determinar tu carpeta personal para guardar la sesión: fija "
                f"{config.ENV_CREDENCIALES} con una ruta absoluta."
            ) from exc
    return base / "railspec" / "credenciales.json"


class Credencial(BaseModel):
    """Sesión de una persona en un servidor. Lo que no se usa (refresh token, scopes) no se guarda."""

    model_config = ConfigDict(extra="ignore", frozen=True)

    access_token: str = Field(min_length=1, repr=False)
    client_id: str = Field(
        min_length=1, description="De la GitHub App que lo emitió: ``login`` lo reutiliza."
    )
    login: str | None = None
    github_id: int | None = None
    obtenido_en: AwareDatetime
    expira_en: AwareDatetime | None = Field(default=None, description="Vacío: el token no vence.")

    def vencida(self, ahora: datetime) -> bool:
        return (
            self.expira_en is not None and self.expira_en - timedelta(seconds=MARGEN_VENCIMIENTO_S) <= ahora
        )


def _resumen(exc: Exception) -> str:
    """Qué falló, sin eco del contenido: el archivo guarda tokens y un mensaje no puede repetirlos."""

    if isinstance(exc, json.JSONDecodeError):
        return f"JSON inválido en la línea {exc.lineno}"
    if isinstance(exc, ValidationError):
        return "; ".join(
            f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}"
            for e in exc.errors(include_input=False, include_url=False, include_context=False)
        )
    if isinstance(exc, UnicodeDecodeError):
        return "no es UTF-8"
    return str(exc)


class AlmacenCredenciales:
    """Archivo JSON por usuario con una credencial por servidor."""

    def __init__(
        self,
        ruta: Path | None = None,
        entorno: Mapping[str, str] | None = None,
        *,
        cerrar_permisos: bool = True,
    ) -> None:
        # La ruta se resuelve al usarla: quien usa RAILSPEC_TOKEN no necesita una carpeta personal.
        self._ruta = ruta
        self._entorno = entorno
        # ``False`` para quien solo mira (``railspec doctor``): un archivo abierto se informa, no se corrige.
        self._cerrar_permisos = cerrar_permisos

    @property
    def ruta(self) -> Path:
        if self._ruta is None:
            self._ruta = ruta_por_defecto(self._entorno)
        return self._ruta

    def leer(self, url: str) -> Credencial | None:
        return self._leer_todo().get(clave_servidor(url))

    def guardar(self, url: str, credencial: Credencial) -> None:
        try:
            todo = self._leer_todo()
        except CredencialesInvalidas:
            todo = {}  # un archivo dañado no puede impedir iniciar sesión: se reescribe
        todo[clave_servidor(url)] = credencial
        self._escribir(todo)

    def borrar(self, url: str) -> bool:
        todo = self._leer_todo()
        if todo.pop(clave_servidor(url), None) is None:
            return False
        if todo:
            self._escribir(todo)
        else:
            self.ruta.unlink(missing_ok=True)
        return True

    def _leer_todo(self) -> dict[str, Credencial]:
        try:
            info = self.ruta.stat()
        except FileNotFoundError:
            return {}
        except OSError as exc:
            raise CredencialesInvalidas(f"No se puede leer {self.ruta}: {exc.strerror or exc}") from exc
        if not stat.S_ISREG(info.st_mode):
            raise CredencialesInvalidas(f"{self.ruta} no es un archivo normal.")
        modo = stat.S_IMODE(info.st_mode)
        if modo & 0o077 and not self._cerrar_permisos:
            raise CredencialesInvalidas(
                f"{self.ruta} lo pueden leer otros usuarios (modo {modo:04o}). Ciérralo con "
                f"`chmod 600 {self.ruta}`; cualquier otro comando de railspec lo hace solo."
            )
        if modo & 0o077:
            # Como ssh con las claves: una credencial que otros pueden leer se cierra antes de usarla.
            try:
                self.ruta.chmod(0o600)
            except OSError as exc:
                raise CredencialesInvalidas(
                    f"{self.ruta} lo pueden leer otros usuarios (modo {modo:04o}) y no se pudo restringir: "
                    f"{exc.strerror or exc}. Corrige los permisos (chmod 600) y reintenta."
                ) from exc
        try:
            crudo = json.loads(self.ruta.read_text(encoding="utf-8"))
            if not isinstance(crudo, dict) or not isinstance(crudo.get("servidores"), dict):
                raise ValueError("falta el objeto «servidores»")
            if crudo.get("version") != VERSION_ARCHIVO:
                raise ValueError(f"versión {crudo.get('version')!r} no soportada por esta railspec")
            return {k: Credencial.model_validate(v) for k, v in crudo["servidores"].items()}
        except (ValueError, UnicodeDecodeError) as exc:  # JSONDecodeError y ValidationError son ValueError
            raise CredencialesInvalidas(
                f"{self.ruta} no es válido ({_resumen(exc)}). Bórralo y vuelve a ejecutar `railspec login`."
            ) from exc

    def _escribir(self, todo: dict[str, Credencial]) -> None:
        carpeta = self.ruta.parent
        carpeta.mkdir(mode=0o700, parents=True, exist_ok=True)
        datos = {
            "version": VERSION_ARCHIVO,
            "servidores": {k: c.model_dump(mode="json") for k, c in sorted(todo.items())},
        }
        # mkstemp crea el temporal con 0600: el token nunca existe en disco con más permisos.
        fd, temporal = tempfile.mkstemp(dir=carpeta, prefix=".credenciales-", suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                f.write(json.dumps(datos, indent=2, ensure_ascii=False) + "\n")
            os.replace(temporal, self.ruta)
        except BaseException:
            with suppress(OSError):
                os.unlink(temporal)
            raise


Origen = Literal["entorno", "credenciales", "ninguna"]


@dataclass(frozen=True)
class Sesion:
    """Qué token manda el proxy ahora y por qué."""

    origen: Origen
    token: str | None = field(default=None, repr=False)
    credencial: Credencial | None = None
    vencida: bool = False
    problema: str | None = None

    def nota(self) -> str:
        """Qué hacer cuando el servidor rechaza este token (cierra el mensaje de ``ServidorRechazo``)."""

        if self.origen == "entorno":
            return (
                "Se usa el token de RAILSPEC_TOKEN, que tiene prioridad sobre la sesión de `railspec login`: "
                "corrígelo, o quita la variable y ejecuta `railspec login`. Tiene que ser un token de "
                "usuario de la GitHub App de Railspec (un token rsc1 de la consola no vale en el arnés)."
            )
        if self.problema:
            return f"{self.problema} Ejecuta `railspec login` para iniciar sesión otra vez."
        if self.credencial is None:
            return "No hay sesión iniciada: ejecuta `railspec login` (o exporta RAILSPEC_TOKEN)."
        quien = f" como {self.credencial.login}" if self.credencial.login else ""
        if self.vencida:
            vencio = f"{self.credencial.expira_en:%Y-%m-%d %H:%M} UTC"
            return (
                f"La sesión de `railspec login`{quien} venció el {vencio}: ejecuta `railspec login` otra vez."
            )
        return (
            f"El servidor no acepta la sesión de `railspec login`{quien}: ejecuta `railspec login` otra vez. "
            "Si se repite, el client id con el que iniciaste sesión puede ser de otra GitHub App que la del "
            "servidor."
        )


class FuenteToken:
    """De dónde sale el token en cada petición: ``RAILSPEC_TOKEN`` o, si no está, la sesión guardada."""

    def __init__(
        self,
        url: str | None,
        token_entorno: str | None = None,
        almacen: AlmacenCredenciales | None = None,
        ahora: Callable[[], datetime] = ahora_utc,
    ) -> None:
        self._url = url
        self._entorno = token_entorno or None
        self._almacen = almacen
        self._ahora = ahora

    def sesion(self) -> Sesion:
        if self._entorno:
            return Sesion("entorno", token=self._entorno)
        if self._url is None or self._almacen is None:
            return Sesion("ninguna")
        try:
            credencial = self._almacen.leer(self._url)
        except CredencialesInvalidas as exc:
            return Sesion("ninguna", problema=str(exc))
        if credencial is None:
            return Sesion("ninguna")
        if credencial.vencida(self._ahora()):
            return Sesion("credenciales", credencial=credencial, vencida=True)
        return Sesion("credenciales", token=credencial.access_token, credencial=credencial)

    def token(self) -> str | None:
        """Nunca lanza: se llama dentro de cada petición HTTP, donde un error mataría la sesión MCP."""

        return self.sesion().token

    def nota(self) -> str:
        return self.sesion().nota()
