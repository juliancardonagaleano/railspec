"""``code.read``: fragmentos del clon canónico leídos al vuelo, solo para el agente del chat.

El servidor mantiene un clon de solo lectura por repositorio vinculado (lo
actualiza el despliegue; ver ``railspec/docs/chat.md``). Esta tool lee de él
el archivo en el commit pedido, o en el canónico vigente, sin guardarlo. Lo
que devuelve está marcado ``codigo_interno`` en el contrato: el servicio del
chat calcula sus huellas para el gate de salida y lo audita, y el texto nunca
sale del servidor salvo hacia el modelo permitido por la política.

Nunca se leen rutas excluidas: las mismas exclusiones por defecto del proxy
(claves, ``.env``, dependencias) más las del vínculo.
"""

from __future__ import annotations

import asyncio
import hashlib
import subprocess
from fnmatch import fnmatch
from pathlib import Path
from typing import Any, Protocol

from railspec.contracts.comun import Actor, AlcanceRepositorio
from railspec.contracts.repositorio import VinculoRepositorio
from railspec.contracts.tools import CodeReadEntrada, CodeReadSalida, CodigoError, FragmentoLeido

from ..api.grafo import repositorio_de_url
from ..motor.motor import ErrorNegocio

#: Copia de ``EXCLUSIONES_POR_DEFECTO`` de railspec-local (los paquetes no se importan entre sí).
EXCLUSIONES_POR_DEFECTO: tuple[str, ...] = (
    ".env", ".env.*", "*.pem", "*.key", "*.p12", "*.pfx", "*.jks", "*.keystore", "id_rsa*", "id_ed25519*",
    "*.tfstate", "*.tfstate.*", ".npmrc", ".pypirc", ".netrc", ".git-credentials", "secrets.*",
    "node_modules/", ".venv/", "venv/", "__pycache__/", "dist/", "build/", "target/", ".railspec/",
    ".codebase-memory/",
)  # fmt: skip

MAX_LINEAS = 400
MAX_CARACTERES = 40_000


def excluida(ruta: str, patrones: tuple[str, ...] | list[str]) -> bool:
    partes = ruta.split("/")
    for p in patrones:
        if p.endswith("/"):
            if p.rstrip("/") in partes[:-1]:
                return True
        elif fnmatch(ruta, p) or fnmatch(partes[-1], p):
            return True
    return False


class FuenteCodigo(Protocol):
    """Lectura del clon canónico de un repositorio vinculado."""

    def commit_canonico(self, vinculo: VinculoRepositorio) -> str | None: ...

    def leer(self, vinculo: VinculoRepositorio, commit: str, ruta: str) -> str | None: ...


class ClonesGit:
    """Clones de solo lectura en ``raiz/<owner>/<repo>`` (``git show <commit>:<ruta>``)."""

    def __init__(self, raiz: str | Path) -> None:
        self.raiz = Path(raiz)

    def _clon(self, vinculo: VinculoRepositorio) -> Path | None:
        nombre = repositorio_de_url(vinculo.url)
        if nombre is None:
            return None
        raiz = self.raiz.resolve()
        clon = (raiz / nombre).resolve()
        # Defensa en profundidad: ni ``..`` ni un enlace simbólico sacan la lectura de la carpeta de clones.
        if clon == raiz or not clon.is_relative_to(raiz):
            return None
        return clon if clon.is_dir() else None

    def _git(self, clon: Path, *args: str) -> str | None:
        r = subprocess.run(["git", "-C", str(clon), *args], capture_output=True, timeout=20, check=False)
        return r.stdout.decode("utf-8", errors="replace") if r.returncode == 0 else None

    def commit_canonico(self, vinculo: VinculoRepositorio) -> str | None:
        clon = self._clon(vinculo)
        if clon is None:
            return None
        for ref in (
            f"refs/remotes/origin/{vinculo.rama_por_defecto}",
            f"refs/heads/{vinculo.rama_por_defecto}",
        ):
            salida = self._git(clon, "rev-parse", "--verify", "--quiet", f"{ref}^{{commit}}")
            if salida:
                return salida.strip()
        return None

    def leer(self, vinculo: VinculoRepositorio, commit: str, ruta: str) -> str | None:
        clon = self._clon(vinculo)
        return None if clon is None else self._git(clon, "show", f"{commit}:{ruta}")


class FuenteEnMemoria:
    """Doble de pruebas: ``{(repositorio, commit): {ruta: texto}}`` y el canónico por repositorio."""

    def __init__(self, archivos: dict[tuple[str, str], dict[str, str]], canonicos: dict[str, str]) -> None:
        self.archivos = archivos
        self.canonicos = canonicos

    def commit_canonico(self, vinculo: VinculoRepositorio) -> str | None:
        return self.canonicos.get(vinculo.alcance.repositorio)

    def leer(self, vinculo: VinculoRepositorio, commit: str, ruta: str) -> str | None:
        return self.archivos.get((vinculo.alcance.repositorio, commit), {}).get(ruta)


def manejador_code_read(fuente: FuenteCodigo, almacen: Any):
    def leer(entrada: CodeReadEntrada) -> CodeReadSalida:
        vinculo = almacen.vinculo(entrada.alcance)
        if vinculo is None:
            a = entrada.alcance
            raise ErrorNegocio(
                CodigoError.no_encontrado, f"{a.org}/{a.workspace}/{a.repositorio} sin vínculo"
            )
        if not vinculo.chat_contexto_codigo.permitido:
            raise ErrorNegocio(
                CodigoError.fuera_de_alcance, "el vínculo no permite código como contexto del chat"
            )
        if excluida(entrada.ruta, [*EXCLUSIONES_POR_DEFECTO, *vinculo.exclusiones]):
            raise ErrorNegocio(CodigoError.fuera_de_alcance, f"{entrada.ruta} está excluida")
        commit = entrada.commit or fuente.commit_canonico(vinculo)
        if commit is None:
            raise ErrorNegocio(CodigoError.no_encontrado, "sin clon canónico para ese repositorio")
        texto = fuente.leer(vinculo, commit, entrada.ruta)
        if texto is None:
            raise ErrorNegocio(CodigoError.no_encontrado, f"{entrada.ruta} no existe en {commit[:12]}")
        lineas = texto.splitlines(keepends=True)
        inicio = entrada.linea_inicio or 1
        fin = min(entrada.linea_fin or len(lineas), len(lineas), inicio + MAX_LINEAS - 1)
        if not lineas or inicio > len(lineas):
            raise ErrorNegocio(CodigoError.no_encontrado, f"{entrada.ruta} no tiene la línea {inicio}")
        trozo = "".join(lineas[inicio - 1 : fin])[:MAX_CARACTERES]
        return CodeReadSalida(
            commit=commit,
            fragmentos=[
                FragmentoLeido(
                    ruta=entrada.ruta,
                    linea_inicio=inicio,
                    linea_fin=max(inicio, fin),
                    sha256=hashlib.sha256(trozo.encode()).hexdigest(),
                    texto=trozo,
                )
            ],
        )

    async def code_read(entrada: CodeReadEntrada, actor: Actor) -> CodeReadSalida:
        return await asyncio.to_thread(leer, entrada)

    return code_read


def alcance_repo(org: str, workspace: str, repositorio: str) -> AlcanceRepositorio:
    return AlcanceRepositorio(org=org, workspace=workspace, repositorio=repositorio)
