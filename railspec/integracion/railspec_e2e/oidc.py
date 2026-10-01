"""Emisor OIDC local que imita a GitHub Actions, para el reindexado del canónico.

Sirve por HTTP las dos caras del OIDC de Actions:

- ``GET /.well-known/jwks``: el JWKS que lee ``VerificadorOidcActions`` del
  servidor (el real, con ``PyJWKClient``), igual que con
  ``https://token.actions.githubusercontent.com``.
- ``GET /token?api-version=2.0&audience=...``: lo que el runner expone en
  ``ACTIONS_ID_TOKEN_REQUEST_URL``; exige ``ACTIONS_ID_TOKEN_REQUEST_TOKEN``
  como bearer y devuelve ``{"value": <jwt>}``, como ``TokenOidc`` de
  ``reindexar.py`` espera.

Los tokens llevan los reclamos de un push a la rama ``rama`` del repositorio
``owner/repo`` desde el workflow ``railspec-reindexar.yml``. La clave RSA se
genera en cada instancia: nada de esto vale fuera de la prueba.
"""

from __future__ import annotations

import json
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlparse

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

WORKFLOW = ".github/workflows/railspec-reindexar.yml"


class EmisorOidc:
    def __init__(self, repositorio: str, rama: str = "main", kid: str = "railspec-e2e-1") -> None:
        self.repositorio = repositorio
        self.rama = rama
        self.kid = kid
        self.clave_runner = secrets.token_urlsafe(24)
        #: Reclamos de cada token emitido, en orden (la prueba comprueba que se usó OIDC).
        self.emitidos: list[dict[str, Any]] = []
        #: Veces que el servidor leyó el JWKS.
        self.lecturas_jwks = 0
        self._clave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
        self._http = ThreadingHTTPServer(("127.0.0.1", 0), self._manejador())
        self._hilo = threading.Thread(target=self._http.serve_forever, daemon=True)

    @property
    def emisor(self) -> str:
        """``iss`` de los tokens y base del JWKS (``RAILSPEC_OIDC_EMISOR`` del servidor)."""

        return f"http://127.0.0.1:{self._http.server_address[1]}"

    def entorno_runner(self) -> dict[str, str]:
        """Las variables que GitHub inyecta en un job con ``permissions: id-token: write``."""

        return {
            "ACTIONS_ID_TOKEN_REQUEST_URL": f"{self.emisor}/token?api-version=2.0",
            "ACTIONS_ID_TOKEN_REQUEST_TOKEN": self.clave_runner,
        }

    def jwks(self) -> dict[str, Any]:
        jwk = jwt.algorithms.RSAAlgorithm.to_jwk(self._clave.public_key(), as_dict=True)
        return {"keys": [{**jwk, "kid": self.kid, "use": "sig", "alg": "RS256"}]}

    def token(self, audiencia: str, **reclamos: Any) -> str:
        ahora = int(time.time())
        ref = f"refs/heads/{self.rama}"
        workflow_ref = f"{self.repositorio}/{WORKFLOW}@{ref}"
        datos = {
            "iss": self.emisor,
            "aud": audiencia,
            "sub": f"repo:{self.repositorio}:ref:{ref}",
            "iat": ahora,
            "nbf": ahora,
            "exp": ahora + 300,
            "repository": self.repositorio,
            "repository_owner": self.repositorio.split("/")[0],
            "ref": ref,
            "ref_type": "branch",
            "event_name": "push",
            "workflow": "railspec-reindexar",
            "workflow_ref": workflow_ref,
            "job_workflow_ref": workflow_ref,
            "run_id": str(4000 + len(self.emitidos)),
            "run_attempt": "1",
        }
        datos.update(reclamos)
        self.emitidos.append(datos)
        return jwt.encode(datos, self._clave, algorithm="RS256", headers={"kid": self.kid})

    def _manejador(self) -> type[BaseHTTPRequestHandler]:
        emisor = self

        class Manejador(BaseHTTPRequestHandler):
            def _json(self, estado: int, cuerpo: dict[str, Any]) -> None:
                datos = json.dumps(cuerpo).encode()
                self.send_response(estado)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(datos)))
                self.end_headers()
                self.wfile.write(datos)

            def do_GET(self) -> None:  # noqa: N802 - nombre de BaseHTTPRequestHandler
                url = urlparse(self.path)
                if url.path == "/.well-known/jwks":
                    emisor.lecturas_jwks += 1
                    return self._json(200, emisor.jwks())
                if url.path == "/token":
                    if self.headers.get("Authorization") != f"Bearer {emisor.clave_runner}":
                        return self._json(401, {"message": "bad credentials"})
                    audiencia = parse_qs(url.query).get("audience", [""])[0]
                    return self._json(200, {"count": 1, "value": emisor.token(audiencia)})
                return self._json(404, {"message": "not found"})

            def log_message(self, *args: Any) -> None:
                pass

        return Manejador

    def __enter__(self) -> EmisorOidc:
        self._hilo.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        self._http.shutdown()
        self._http.server_close()
