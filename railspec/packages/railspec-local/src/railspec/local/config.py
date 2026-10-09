"""Configuración del proxy local.

Dos fuentes, nunca mezcladas:

- ``.railspec/config.json`` en la raíz del repositorio (versionable, sin
  secretos): a qué organización, workspace y repositorio pertenece el clon,
  el nivel de código y el arnés.
- Variables de entorno para la conexión: ``RAILSPEC_URL`` (endpoint MCP del
  servidor) y ``RAILSPEC_TOKEN`` (token de usuario de la GitHub App de
  Railspec). Sin ``RAILSPEC_TOKEN`` el proxy usa la sesión que guardó
  ``railspec login`` (``credenciales.py``); el token no vive en este módulo
  ni en el repositorio.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

from pydantic import Field, ValidationError
from railspec.contracts._base import Contrato
from railspec.contracts.comun import AlcanceRepositorio, Arnes, NivelCodigo, Slug

from .errores import ConfigInvalida

ARCHIVO_CONFIG = Path(".railspec") / "config.json"
ENV_URL = "RAILSPEC_URL"
ENV_TOKEN = "RAILSPEC_TOKEN"
#: Client id (público) de la GitHub App de Railspec, para ``railspec login``.
ENV_GITHUB_CLIENT_ID = "RAILSPEC_GITHUB_CLIENT_ID"
#: Ruta del archivo de credenciales por usuario; por defecto ``~/.config/railspec/credenciales.json``.
ENV_CREDENCIALES = "RAILSPEC_CREDENCIALES"
ENV_WORKTREES = "RAILSPEC_WORKTREES"

#: Tope por defecto del comando de validación.
VALIDACION_TIMEOUT_S = 30 * 60


class ConfigRepositorio(Contrato):
    """Contenido de ``.railspec/config.json``."""

    org: Slug
    workspace: Slug
    repositorio: Slug
    nivel_codigo: NivelCodigo = Field(
        default=NivelCodigo.restringido,
        description=(
            "Nivel del vínculo del repositorio. El proxy nunca envía más de lo que este nivel permite; "
            "si falta, rige restringido."
        ),
    )
    arnes: Arnes | None = None
    validacion_timeout_s: int = Field(default=VALIDACION_TIMEOUT_S, ge=1, le=6 * 3600)

    @property
    def alcance(self) -> AlcanceRepositorio:
        return AlcanceRepositorio(org=self.org, workspace=self.workspace, repositorio=self.repositorio)


class Config(Contrato):
    raiz: str = Field(description="Raíz del clon principal del desarrollador.")
    repo: ConfigRepositorio
    url: str | None = None
    token: str | None = Field(default=None, repr=False)
    dir_worktrees: str

    @property
    def raiz_path(self) -> Path:
        return Path(self.raiz)

    @property
    def url_consola(self) -> str | None:
        return url_consola(self.url)


def url_consola(url: str | None) -> str | None:
    """La consola web del servidor (``<servidor>/consola/``), si el endpoint MCP deja derivarla.

    ``RAILSPEC_URL`` apunta al endpoint MCP (``https://railspec.example/mcp``) y la consola vive en el mismo
    origen. Si la URL no termina en ``/mcp`` no se adivina: ``None``."""

    if not url:
        return None
    partes = urlsplit(url)
    ruta = partes.path.rstrip("/")
    if not (partes.scheme and partes.netloc and ruta.endswith("/mcp")):
        return None
    return f"{partes.scheme}://{partes.netloc}{ruta.removesuffix('/mcp')}/consola/"


def dir_worktrees_por_defecto(raiz: Path) -> Path:
    """Hermano del clon (``<padre>/<nombre>.railspec``) para que las herramientas
    del repositorio (pytest, linters) no recorran los worktrees de las unidades."""

    return raiz.parent / f"{raiz.name}.railspec"


def leer_config_repositorio(raiz: Path) -> ConfigRepositorio:
    ruta = raiz / ARCHIVO_CONFIG
    if not ruta.is_file():
        raise ConfigInvalida(
            f"No existe {ARCHIVO_CONFIG.as_posix()} en {raiz}. "
            "Ejecuta `railspec instalar` en la raíz del repositorio."
        )
    try:
        return ConfigRepositorio.model_validate(json.loads(ruta.read_text(encoding="utf-8")))
    except (json.JSONDecodeError, ValidationError) as exc:
        raise ConfigInvalida(f"{ruta} no es válido: {exc}") from exc


def escribir_config_repositorio(raiz: Path, config: ConfigRepositorio) -> Path:
    ruta = raiz / ARCHIVO_CONFIG
    ruta.parent.mkdir(parents=True, exist_ok=True)
    datos = config.model_dump(mode="json", exclude_defaults=False)
    ruta.write_text(json.dumps(datos, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return ruta


def cargar(raiz: Path, entorno: dict[str, str] | None = None) -> Config:
    env = os.environ if entorno is None else entorno
    repo = leer_config_repositorio(raiz)
    return Config(
        raiz=str(raiz),
        repo=repo,
        url=env.get(ENV_URL) or None,
        token=env.get(ENV_TOKEN) or None,
        dir_worktrees=env.get(ENV_WORKTREES) or str(dir_worktrees_por_defecto(raiz)),
    )
