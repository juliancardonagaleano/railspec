"""``railspec-server`` con dobles de modelo y gobernanza, para el entorno de integración.

Es el servidor real (``ensamblar``: Mongo, FalkorDB, registro de tools, MCP y
HTTP); solo cambian dos piezas externas que exigen credenciales:

- El proveedor de modelo es ``ProveedorGuionado``: cada crítico del gate
  responde sin hallazgos. Así el recorrido es determinista y no llama a Foundry.
- La gobernanza es ``GobernanzaFija`` (consultada, sin ítems) en vez de PCE.

Al arrancar siembra la configuración mínima del workspace: el vínculo del
repositorio en nivel ``restringido`` y el rol ``desarrollador`` del usuario de
desarrollo. Sin ellas, con Mongo real, el autorizador rechaza toda tool.
``RAILSPEC_E2E_REPO`` cambia el slug del repositorio vinculado (y el
``owner/repo`` de su URL), para que una prueba tenga su propio grafo canónico.

    python -m railspec_e2e.servidor
"""

from __future__ import annotations

import logging
import os
import uuid
from datetime import UTC, datetime

from railspec.contracts.comun import Actor, AlcanceRepositorio, Canal, NivelCodigo, Proveedor, TipoActor
from railspec.contracts.repositorio import (
    AsignacionRol,
    Auditoria,
    Rol,
    SujetoUsuario,
    VinculoRepositorio,
    politica_chat_por_defecto,
)
from railspec.server.app import ensamblar
from railspec.server.config import Configuracion
from railspec.server.motor.gate import SalidaCritico
from railspec.server.motor.gobernanza import GobernanzaFija
from railspec.server.proveedores import Proveedores
from railspec.server.proveedores.falso import ProveedorGuionado

from . import GITHUB_ID, LOGIN, ORG, REPO, WS

log = logging.getLogger("railspec.e2e")


def critico_sin_hallazgos(peticion):
    if peticion.esquema is SalidaCritico:
        return SalidaCritico(hallazgos=[])
    return AssertionError(f"el entorno e2e no guioniza {peticion.esquema.__name__}")


def semilla(nivel: NivelCodigo = NivelCodigo.restringido, repositorio: str = REPO) -> list:
    ahora = datetime.now(UTC)
    admin = Actor(tipo=TipoActor.humano, canal=Canal.consola, github_id=GITHUB_ID, login=LOGIN)
    auditoria = Auditoria(creado_por=admin, creado_en=ahora, actualizado_por=admin, actualizado_en=ahora)
    return [
        VinculoRepositorio(
            version=1,
            auditoria=auditoria,
            alcance=AlcanceRepositorio(org=ORG, workspace=WS, repositorio=repositorio),
            url=f"https://github.com/{ORG}/{repositorio}",
            rol="primario",
            nivel_codigo=nivel,
            chat_contexto_codigo=politica_chat_por_defecto(nivel),
        ),
        AsignacionRol(
            version=1,
            auditoria=auditoria,
            # Id estable: arrancar dos veces no duplica la asignación.
            id=uuid.UUID(
                int=uuid.uuid5(uuid.NAMESPACE_URL, f"railspec-e2e:{ORG}:{WS}:{GITHUB_ID}").int, version=4
            ),
            org=ORG,
            workspace=WS,
            rol=Rol.desarrollador,
            sujeto=SujetoUsuario(github_id=GITHUB_ID),
        ),
    ]


def construir(config: Configuracion, repositorio: str = REPO):
    proveedores = Proveedores({Proveedor.foundry: ProveedorGuionado(critico_sin_hallazgos)})
    motor, app = ensamblar(config, proveedores=proveedores, gobernanza=GobernanzaFija())
    motor.n.almacen.guardar_configuracion(semilla(repositorio=repositorio))
    return motor, app


def main() -> None:
    import uvicorn

    logging.basicConfig(level=logging.INFO)
    config = Configuracion.desde_entorno()
    _, app = construir(config, os.environ.get("RAILSPEC_E2E_REPO") or REPO)
    uvicorn.run(app, host=config.host, port=config.puerto)


if __name__ == "__main__":
    main()
