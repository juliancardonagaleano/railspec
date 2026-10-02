"""Entorno de la integración: Mongo y FalkorDB reales, railspec-server y el proxy por stdio.

- Con ``RAILSPEC_E2E_URL`` las pruebas usan ese servidor (p. ej. el del
  ``docker-compose.yml``) y la base ``RAILSPEC_E2E_MONGO_DB``.
- Sin ella, levantan ``railspec_e2e.servidor`` en un subproceso contra una base
  de Mongo nueva por corrida y la borran al terminar.

Mongo y FalkorDB se buscan en ``RAILSPEC_E2E_MONGO_URI`` y
``RAILSPEC_E2E_FALKORDB_URL`` (por defecto, los puertos del compose en
localhost). Si no responden, las pruebas se saltan con el motivo.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import time
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

import pytest

RAIZ = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(RAIZ))

from railspec_e2e import GITHUB_ID, LOGIN, TOKEN  # noqa: E402

MONGO_URI = os.environ.get("RAILSPEC_E2E_MONGO_URI", "mongodb://127.0.0.1:27017")
FALKORDB_URL = os.environ.get("RAILSPEC_E2E_FALKORDB_URL", "redis://127.0.0.1:6379")


@dataclass
class Entorno:
    url: str
    token: str
    mongo_uri: str
    mongo_db: str
    falkordb_url: str
    #: Emisor OIDC local que el servidor del subproceso acepta para ``graph.index``; ``None``
    #: con un servidor externo, que no lo conoce.
    oidc: object | None = None

    def ci(self, remoto: Path, trabajo: Path):
        """El job de reindexado de CI contra este servidor, o ``None`` si no acepta su OIDC."""

        if self.oidc is None:
            return None
        from railspec_e2e.ci import CiLocal

        return CiLocal(remoto, trabajo, self.url.removesuffix("/").removesuffix("/mcp"), self.oidc)

    def mongo(self):
        import pymongo

        return pymongo.MongoClient(self.mongo_uri, serverSelectionTimeoutMS=3000)[self.mongo_db]

    def falkordb(self):
        import falkordb

        u = urlparse(self.falkordb_url)
        return falkordb.FalkorDB(host=u.hostname, port=u.port or 6379)


def _responde(url: str) -> bool:
    u = urlparse(url)
    try:
        with socket.create_connection((u.hostname, u.port), timeout=2):
            return True
    except OSError:
        return False


def _puerto_libre() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _esperar_salud(base: str, proceso: subprocess.Popen | None, log: Path | None) -> None:
    limite = time.monotonic() + 60
    while time.monotonic() < limite:
        if proceso is not None and proceso.poll() is not None:
            raise RuntimeError(f"railspec-server terminó al arrancar:\n{log.read_text() if log else ''}")
        try:
            with urllib.request.urlopen(f"{base}/healthz", timeout=2) as r:
                if r.status == 200:
                    return
        except OSError:
            time.sleep(0.3)
    raise RuntimeError(f"railspec-server no respondió en {base}/healthz")


@pytest.fixture(scope="session")
def entorno(tmp_path_factory) -> Entorno:
    externo = os.environ.get("RAILSPEC_E2E_URL")
    if externo:
        base = externo.rstrip("/").removesuffix("/mcp")
        _esperar_salud(base, None, None)
        yield Entorno(
            url=f"{base}/mcp/",
            token=os.environ.get("RAILSPEC_E2E_TOKEN", TOKEN),
            mongo_uri=MONGO_URI,
            mongo_db=os.environ.get("RAILSPEC_E2E_MONGO_DB", "railspec_e2e"),
            falkordb_url=FALKORDB_URL,
        )
        return

    for nombre, url in (("Mongo", MONGO_URI), ("FalkorDB", FALKORDB_URL)):
        if not _responde(url):
            pytest.skip(
                f"{nombre} no responde en {url}: "
                "docker compose -f railspec/integracion/docker-compose.yml up -d mongo falkordb"
            )
    from railspec_e2e.ci import AUDIENCIA, REPOSITORIO_GH, EmisorOidc

    emisor = EmisorOidc()
    base_datos = f"railspec_e2e_{uuid.uuid4().hex[:8]}"
    puerto = _puerto_libre()
    log = tmp_path_factory.mktemp("servidor") / "railspec-server.log"
    env = {
        **os.environ,
        "RAILSPEC_MONGO_URI": MONGO_URI,
        "RAILSPEC_MONGO_DB": base_datos,
        "RAILSPEC_FALKORDB_URL": FALKORDB_URL,
        "RAILSPEC_TOKENS_DESARROLLO": f"{TOKEN}={LOGIN}:{GITHUB_ID}",
        "RAILSPEC_PERMITIR_DESARROLLO": "1",
        "RAILSPEC_HOST": "127.0.0.1",
        "RAILSPEC_PUERTO": str(puerto),
        "RAILSPEC_OIDC_AUDIENCIA": AUDIENCIA,
        "RAILSPEC_OIDC_EMISOR": emisor.url,
        "RAILSPEC_OIDC_REPOSITORIOS": REPOSITORIO_GH,
    }
    for variable in ("RAILSPEC_FOUNDRY_ENDPOINT", "RAILSPEC_PCE_URL", "RAILSPEC_ANTHROPIC_HABILITADO"):
        env.pop(variable, None)
    with log.open("w") as salida:
        proceso = subprocess.Popen(
            [sys.executable, "-m", "railspec_e2e.servidor"],
            cwd=RAIZ,
            env=env,
            stdout=salida,
            stderr=subprocess.STDOUT,
        )
    base = f"http://127.0.0.1:{puerto}"
    entorno = Entorno(f"{base}/mcp/", TOKEN, MONGO_URI, base_datos, FALKORDB_URL, oidc=emisor)
    try:
        _esperar_salud(base, proceso, log)
        yield entorno
    finally:
        proceso.terminate()
        try:
            proceso.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proceso.kill()
        entorno.mongo().client.drop_database(base_datos)
        emisor.cerrar()
