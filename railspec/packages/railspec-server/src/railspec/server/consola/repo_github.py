"""La GitHub App como instalación: leer un archivo de configuración del repo y proponer su cambio en un PR.

Se usa solo para los archivos de ``archivos.ARCHIVOS`` (``contexto.yaml`` y ``.railspecignore``):
configuración, no código. Ni se envía a un modelo ni se registra en el log; la auditoría guarda la ruta,
el PR y el hash. La consola nunca hace commit en la rama principal: crea una rama
``railspec/<archivo>-<hash>``, un commit y el PR hacia la rama del vínculo.

Las credenciales (``RAILSPEC_GITHUB_APP_ID`` y la clave PEM) no se confunden con el login: el token de
usuario del inicio de sesión se revoca en el callback; este código pide un token de instalación acotado a
UN repositorio y a los permisos que necesita (``contents`` y ``pull_requests``), de una hora, y no lo
guarda. Si la App no está instalada, no tiene el permiso de escritura o no hay credenciales,
``RepoGithub`` lo dice con ``ModoManual`` y la consola enseña el diff para aplicarlo a mano.
"""

from __future__ import annotations

import base64
import hashlib
import logging
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from ..api.grafo import repositorio_de_url  # noqa: F401 (la ruta lo importa de aquí)
from .config import ConfigGithubApp
from .github import API

log = logging.getLogger("railspec.consola")

#: Permisos que la App necesita en la instalación para abrir el PR.
PERMISOS_PR = {"contents": "write", "pull_requests": "write"}
PERMISOS_LECTURA = {"contents": "read"}


class ErrorRepo(Exception):
    """Fallo de GitHub que la consola no puede resolver sola (red, 5xx, respuesta inesperada)."""


class ModoManual(Exception):
    """La App no puede hacer esto en este repositorio; ``args[0]`` dice por qué en una frase."""


class ArchivoCambiado(Exception):
    """El archivo ya no es el que la persona abrió (alguien lo cambió en la rama base)."""


@dataclass(frozen=True)
class ArchivoRemoto:
    existe: bool
    contenido: str
    sha: str | None
    #: ``True`` si la instalación puede abrir PR (``contents`` y ``pull_requests`` en escritura).
    puede_proponer: bool


@dataclass(frozen=True)
class PropuestaPr:
    url: str
    numero: int
    rama: str


class RepoGithub:
    """``cliente``: un ``httpx.Client`` (las pruebas inyectan un transporte simulado)."""

    def __init__(self, app: ConfigGithubApp | None, cliente: Any | None = None, reloj=time.time) -> None:
        self.app = app
        self._cliente = cliente
        self._reloj = reloj

    @property
    def disponible(self) -> bool:
        return self.app is not None and self.app.puede_instalar

    def _http(self):
        if self._cliente is None:
            import httpx

            self._cliente = httpx.Client(timeout=15)
        return self._cliente

    # --- credenciales -----------------------------------------------------------------------------

    def _jwt(self) -> str:
        import jwt

        assert self.app is not None
        ahora = int(self._reloj())
        return jwt.encode(
            {"iat": ahora - 60, "exp": ahora + 540, "iss": self.app.app_id},
            self.app.clave_privada,
            algorithm="RS256",
        )

    def _como_app(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._jwt()}", "Accept": "application/vnd.github+json"}

    def _instalacion(self, repo: str) -> tuple[int, dict[str, str]]:
        r = self._http().get(f"{API}/repos/{quote(repo, safe='/')}/installation", headers=self._como_app())
        if r.status_code == 404:
            raise ModoManual("la GitHub App de Railspec no está instalada en ese repositorio")
        if r.status_code != 200:
            raise ErrorRepo(f"GitHub respondió {r.status_code} al buscar la instalación")
        datos = r.json()
        return int(datos["id"]), dict(datos.get("permissions") or {})

    def _token(self, instalacion: int, repo: str, permisos: dict[str, str]) -> str:
        r = self._http().post(
            f"{API}/app/installations/{instalacion}/access_tokens",
            headers=self._como_app(),
            json={"repositories": [repo.split("/", 1)[1]], "permissions": permisos},
        )
        if r.status_code in (403, 422):
            raise ModoManual(
                f"la instalación de la App no concede {', '.join(f'{k}:{v}' for k, v in permisos.items())}"
            )
        if r.status_code != 201:
            raise ErrorRepo(f"GitHub respondió {r.status_code} al pedir el token de instalación")
        return str(r.json()["token"])

    def _cabeceras(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}

    # --- lectura -------------------------------------------------------------------------------------

    def leer(self, repo: str, ruta: str, rama: str) -> ArchivoRemoto:
        """El archivo en la punta de ``rama`` y si la instalación puede además proponer cambios."""

        if not self.disponible:
            raise ModoManual("el servidor no tiene las credenciales de la GitHub App como instalación")
        instalacion, concedidos = self._instalacion(repo)
        puede = all(concedidos.get(k) == v for k, v in PERMISOS_PR.items())
        token = self._token(instalacion, repo, PERMISOS_LECTURA)
        r = self._http().get(
            f"{API}/repos/{quote(repo, safe='/')}/contents/{quote(ruta)}",
            params={"ref": rama},
            headers=self._cabeceras(token),
        )
        if r.status_code == 404:
            return ArchivoRemoto(False, "", None, puede)
        if r.status_code != 200:
            raise ErrorRepo(f"GitHub respondió {r.status_code} al leer {ruta}")
        datos = r.json()
        if not isinstance(datos, dict) or datos.get("type") != "file" or datos.get("encoding") != "base64":
            raise ErrorRepo(f"{ruta} no es un archivo de texto que se pueda leer")
        try:
            texto = base64.b64decode(datos["content"]).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise ErrorRepo(f"{ruta} no es UTF-8") from exc
        return ArchivoRemoto(True, texto, str(datos["sha"]), puede)

    # --- propuesta -----------------------------------------------------------------------------------

    def proponer(
        self,
        repo: str,
        ruta: str,
        rama_base: str,
        contenido: str,
        sha_base: str | None,
        *,
        titulo: str,
        cuerpo: str,
        mensaje_commit: str,
    ) -> PropuestaPr:
        """Crea la rama, el commit y el PR. ``sha_base`` es el blob que se vio (``None``: no existía)."""

        if not self.disponible:
            raise ModoManual("el servidor no tiene las credenciales de la GitHub App como instalación")
        instalacion, concedidos = self._instalacion(repo)
        if not all(concedidos.get(k) == v for k, v in PERMISOS_PR.items()):
            raise ModoManual(
                "la instalación de la App no tiene contenido:escritura y pull requests:escritura"
            )
        token = self._token(instalacion, repo, PERMISOS_PR)
        cab = self._cabeceras(token)
        base = f"{API}/repos/{quote(repo, safe='/')}"
        http = self._http()

        actual = http.get(f"{base}/contents/{quote(ruta)}", params={"ref": rama_base}, headers=cab)
        if actual.status_code == 404:
            sha_actual = None
        elif actual.status_code == 200:
            sha_actual = str(actual.json().get("sha"))
        else:
            raise ErrorRepo(f"GitHub respondió {actual.status_code} al comprobar {ruta}")
        if sha_actual != sha_base:
            raise ArchivoCambiado(f"{ruta} cambió en {rama_base} desde que lo abriste")

        punta = http.get(f"{base}/git/ref/heads/{quote(rama_base)}", headers=cab)
        if punta.status_code == 404:
            raise ErrorRepo(f"la rama {rama_base} no existe en {repo} (revisa la rama del vínculo)")
        if punta.status_code != 200:
            raise ErrorRepo(f"GitHub respondió {punta.status_code} al leer la rama {rama_base}")
        sello = hashlib.sha256(f"{ruta}\0{contenido}".encode()).hexdigest()[:10]
        rama = f"railspec/{ruta.lstrip('.').replace('.', '-')}-{sello}"
        creada = http.post(
            f"{base}/git/refs",
            headers=cab,
            json={"ref": f"refs/heads/{rama}", "sha": punta.json()["object"]["sha"]},
        )
        if creada.status_code == 422:
            raise ArchivoCambiado(f"ya hay una propuesta con este contenido ({rama}); revisa los PR abiertos")
        if creada.status_code != 201:
            raise ErrorRepo(f"GitHub respondió {creada.status_code} al crear la rama")
        try:
            carga: dict[str, Any] = {
                "message": mensaje_commit,
                "content": base64.b64encode(contenido.encode()).decode(),
                "branch": rama,
            }
            if sha_actual is not None:
                carga["sha"] = sha_actual
            c = http.put(f"{base}/contents/{quote(ruta)}", headers=cab, json=carga)
            if c.status_code not in (200, 201):
                raise ErrorRepo(f"GitHub respondió {c.status_code} al guardar {ruta}")
            pr = http.post(
                f"{base}/pulls",
                headers=cab,
                json={"title": titulo, "head": rama, "base": rama_base, "body": cuerpo},
            )
            if pr.status_code != 201:
                raise ErrorRepo(f"GitHub respondió {pr.status_code} al abrir el PR")
        except Exception:
            self._borrar_rama(base, cab, rama)
            raise
        datos = pr.json()
        return PropuestaPr(str(datos["html_url"]), int(datos["number"]), rama)

    def _borrar_rama(self, base: str, cab: dict[str, str], rama: str) -> None:
        """No dejar una rama huérfana si el commit o el PR fallaron (mejor esfuerzo)."""

        try:
            self._http().request("DELETE", f"{base}/git/refs/heads/{quote(rama, safe='/')}", headers=cab)
        except Exception as exc:  # red caída: la rama sobrante no rompe nada
            log.warning("no se pudo borrar la rama %s (%s)", rama, type(exc).__name__)
