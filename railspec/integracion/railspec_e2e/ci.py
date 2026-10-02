"""El reindexado de CI del e2e: ``railspec/deploy/ci/reindexar.py`` de verdad contra el servidor.

GitHub Actions no existe aquí, así que dos piezas se sustituyen por locales:

- ``EmisorOidc``: un emisor OIDC mínimo (JWKS por HTTP en loopback). El servidor lo toma
  como ``RAILSPEC_OIDC_EMISOR`` y valida con él el token del job, como haría con el de GitHub.
- ``CiLocal``: el job ``railspec-reindexar``. Hace checkout del commit empujado en un clon
  aparte y ejecuta ``reindexar()`` con el indexador real (``codebase-memory-mcp``) y
  ``POST /v1/tools/graph.index`` real.
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from railspec.contracts.comun import AlcanceRepositorio
from railspec.contracts.tools import GraphIndexSalida

from . import ORG, REPO, WS

AUDIENCIA = "e2e-audiencia-larga-y-no-adivinable"
REPOSITORIO_GH = f"{ORG}/{REPO}"
REINDEXAR = Path(__file__).resolve().parents[2] / "deploy" / "ci" / "reindexar.py"


class EmisorOidc:
    """Emisor OIDC local: sirve su JWKS y firma los tokens del job de CI."""

    def __init__(self, puerto: int = 0) -> None:
        self._clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self._clave.public_key()))
        jwks = json.dumps({"keys": [{**jwk, "kid": "e2e", "use": "sig", "alg": "RS256"}]}).encode()

        class Servicio(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - API de http.server
                ok = self.path == "/.well-known/jwks"
                self.send_response(200 if ok else 404)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(jwks if ok else b"{}")

            def log_message(self, *args):
                pass

        self._http = ThreadingHTTPServer(("127.0.0.1", puerto), Servicio)
        self.url = f"http://127.0.0.1:{self._http.server_address[1]}"
        threading.Thread(target=self._http.serve_forever, daemon=True).start()

    def token(self, repositorio: str = REPOSITORIO_GH) -> str:
        ahora = int(time.time())
        reclamos = {
            "iss": self.url,
            "aud": AUDIENCIA,
            "iat": ahora,
            "nbf": ahora,
            "exp": ahora + 300,
            "repository": repositorio,
            "ref": "refs/heads/main",
            "workflow_ref": f"{repositorio}/.github/workflows/railspec-reindexar.yml@refs/heads/main",
            "run_id": "1",
        }
        return jwt.encode(reclamos, self._clave, algorithm="RS256", headers={"kid": "e2e"})

    def cerrar(self) -> None:
        self._http.shutdown()
        self._http.server_close()


def _cargar_reindexar():
    spec = importlib.util.spec_from_file_location("railspec_e2e_reindexar", REINDEXAR)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = modulo  # los @dataclass del script resuelven sus anotaciones por aquí
    spec.loader.exec_module(modulo)
    return modulo


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class CiLocal:
    """Un job ``railspec-reindexar`` por commit empujado a la rama por defecto."""

    def __init__(self, remoto: Path, trabajo: Path, servidor: str, emisor: EmisorOidc) -> None:
        self._remoto, self._checkout = remoto, trabajo / "ci"
        self._servidor, self._emisor = servidor, emisor
        self._reindexar = _cargar_reindexar()

    def reindexar(self, commit: str, anterior: str | None = None) -> GraphIndexSalida:
        """``anterior`` None = índice completo; con commit, delta desde él (``github.event.before``)."""

        from railspec.local import indexador_cbm, secretos

        if not self._checkout.exists():
            subprocess.run(
                ["git", "clone", "-q", str(self._remoto), str(self._checkout)],
                check=True,
                capture_output=True,
            )
        _git(self._checkout, "fetch", "-q", "origin")
        _git(self._checkout, "checkout", "-q", "--detach", commit)
        return self._reindexar.reindexar(
            self._checkout,
            AlcanceRepositorio(org=ORG, workspace=WS, repositorio=REPO),
            "main",
            commit,
            anterior,
            self._servidor,
            self._emisor.token,
            indexador_cbm.crear(),
            secretos.exclusiones(self._checkout),
            espera_s=0.1,
        )
