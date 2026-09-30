"""Comando ``railspec``.

- ``railspec mcp``: servidor MCP por stdio para el arnés (lo lanza el arnés).
- ``railspec instalar``: configura el repositorio y los adaptadores de arnés.
- ``railspec insumo pull <id>``: trae un insumo del chat de la consola.
- ``railspec estado`` y ``railspec sync``: estado local y envío de la cola.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from railspec.contracts.comun import Arnes, NivelCodigo

from . import __version__, adaptadores, config, git
from .almacen import EXCLUIR_DE_GIT
from .cliente import ClienteServidor
from .errores import ConfigInvalida, ErrorRailspec
from .indice import cargar_indexador
from .proxy import ProxyLocal


def crear_proxy(raiz: Path) -> ProxyLocal:
    cfg = config.cargar(raiz)
    if not cfg.url:
        raise ConfigInvalida(f"Falta {config.ENV_URL}: el endpoint MCP de railspec-server.")
    from .transporte_mcp import TransporteMcpHttp

    return ProxyLocal(cfg, ClienteServidor(TransporteMcpHttp(cfg.url, cfg.token)), cargar_indexador())


def _raiz(desde: str | None) -> Path:
    return git.raiz_repositorio(Path(desde or ".").resolve())


def _imprimir(datos: Any) -> None:
    print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .servidor_mcp import servir

    raiz = _raiz(args.repo)
    servir(lambda: crear_proxy(raiz))
    return 0


def _cmd_instalar(args: argparse.Namespace) -> int:
    raiz = _raiz(args.repo)
    arneses = [Arnes(a) for a in (args.arnes or [])]
    if args.verificar:
        existente = config.leer_config_repositorio(raiz)
        deriva = {a.value: adaptadores.verificar(raiz, a) for a in arneses or [existente.arnes] if a}
        _imprimir({"config": str(raiz / config.ARCHIVO_CONFIG), "deriva": deriva})
        return 1 if any(deriva.values()) else 0
    ruta_config = raiz / config.ARCHIVO_CONFIG
    if ruta_config.is_file() and not (args.org or args.workspace or args.repositorio or args.nivel):
        repo = config.leer_config_repositorio(raiz)
    else:
        previo = config.leer_config_repositorio(raiz) if ruta_config.is_file() else None
        org = args.org or (previo.org if previo else None)
        workspace = args.workspace or (previo.workspace if previo else None)
        repositorio = args.repositorio or (previo.repositorio if previo else None)
        if not (org and workspace and repositorio):
            raise ConfigInvalida("La primera instalación necesita --org, --workspace y --repositorio.")
        repo = config.ConfigRepositorio(
            org=org,
            workspace=workspace,
            repositorio=repositorio,
            nivel_codigo=NivelCodigo(args.nivel)
            if args.nivel
            else (previo.nivel_codigo if previo else NivelCodigo.restringido),
            arnes=arneses[0] if arneses else (previo.arnes if previo else None),
        )
        config.escribir_config_repositorio(raiz, repo)
    git.excluir_localmente(raiz, EXCLUIR_DE_GIT)
    cambios = {a.value: adaptadores.instalar(raiz, a) for a in arneses}
    _imprimir(
        {
            "config": str(ruta_config),
            "nivel_codigo": repo.nivel_codigo.value,
            "cambios": cambios,
            "siguiente": f"exporta {config.ENV_URL} y {config.ENV_TOKEN} y reinicia el arnés",
        }
    )
    return 0


def _cmd_insumo(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    _imprimir(asyncio.run(proxy.traer_insumo(UUID(args.id), args.unidad)))
    return 0


def _cmd_estado(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    _imprimir(asyncio.run(proxy.estado(args.unidad)))
    return 0


def _cmd_sync(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    resultado = asyncio.run(proxy.sincronizar(args.unidad))
    _imprimir(resultado)
    return 0 if not resultado["pendientes"] else 2


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="railspec", description="Proxy local de Railspec.")
    p.add_argument("--version", action="version", version=f"railspec-local {__version__}")
    p.add_argument("--repo", help="Ruta dentro del repositorio (por defecto, el directorio actual).")
    sub = p.add_subparsers(dest="comando", required=True)

    sub.add_parser("mcp", help="Servidor MCP por stdio para el arnés.").set_defaults(fn=_cmd_mcp)

    inst = sub.add_parser("instalar", help="Configura el repositorio y los adaptadores de arnés.")
    inst.add_argument("--org")
    inst.add_argument("--workspace")
    inst.add_argument("--repositorio", help="Slug del vínculo del repositorio en el workspace.")
    inst.add_argument("--nivel", choices=[n.value for n in NivelCodigo])
    inst.add_argument(
        "--arnes", action="append", choices=[a.value for a in adaptadores.ADAPTADORES], help="Repetible."
    )
    inst.add_argument("--verificar", action="store_true", help="Solo informa deriva; no escribe.")
    inst.set_defaults(fn=_cmd_instalar)

    ins = sub.add_parser("insumo", help="Insumos exportados desde el chat de la consola.")
    ins_sub = ins.add_subparsers(dest="accion", required=True)
    pull = ins_sub.add_parser("pull", help="Escribe el insumo en .railspec/insumos/.")
    pull.add_argument("id")
    pull.add_argument("--unidad")
    pull.set_defaults(fn=_cmd_insumo)

    est = sub.add_parser("estado", help="Estado remoto y local de una unidad.")
    est.add_argument("--unidad")
    est.set_defaults(fn=_cmd_estado)

    syn = sub.add_parser("sync", help="Envía la cola de reportes pendientes.")
    syn.add_argument("--unidad")
    syn.set_defaults(fn=_cmd_sync)
    return p


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except ErrorRailspec as exc:
        print(f"railspec: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
