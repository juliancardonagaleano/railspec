"""Comando ``railspec``.

- ``railspec mcp``: servidor MCP por stdio para el arnés (lo lanza el arnés).
- ``railspec instalar``: configura el repositorio y los adaptadores de arnés.
- ``railspec desinstalar``: quita los adaptadores (y, si se pide, la configuración).
- ``railspec insumo pull <id>``: trae un insumo del chat de la consola.
- ``railspec exportar`` e ``railspec importar``: una unidad a o desde un paquete en
  disco; ``importar`` también convierte a demanda unidades del kit SDD (``.spec/units/``).
- ``railspec estado`` y ``railspec sync``: estado local y envío de la cola.
- ``railspec hook <arnés>``: guardia de las reglas de conducta (la llaman los hooks del arnés).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from railspec.contracts.comun import Arnes, NivelCodigo

from . import __version__, adaptadores, config, git, guardia
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


def _dir_worktrees(raiz: Path) -> Path:
    return Path(os.environ.get(config.ENV_WORKTREES) or config.dir_worktrees_por_defecto(raiz))


#: Configuración por máquina que el adaptador de Claude Code escribe: nunca se versiona.
EXCLUIR_ADAPTADORES = ["/.claude/settings.local.json"]


def _cmd_instalar(args: argparse.Namespace) -> int:
    raiz = _raiz(args.repo)
    arneses = [Arnes(a) for a in (args.arnes or [])]
    worktrees = _dir_worktrees(raiz)
    if args.verificar:
        existente = config.leer_config_repositorio(raiz)
        deriva = {
            a.value: adaptadores.verificar(raiz, a, worktrees) for a in arneses or [existente.arnes] if a
        }
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
    git.excluir_localmente(
        raiz, EXCLUIR_DE_GIT + (EXCLUIR_ADAPTADORES if Arnes.claude_code in arneses else [])
    )
    cambios = {a.value: adaptadores.instalar(raiz, a, worktrees) for a in arneses}
    salida: dict[str, Any] = {
        "config": str(ruta_config),
        "nivel_codigo": repo.nivel_codigo.value,
        "cambios": cambios,
        "siguiente": f"exporta {config.ENV_URL} y {config.ENV_TOKEN} y reinicia el arnés",
    }
    avisos = _avisos(arneses, worktrees)
    if avisos:
        salida["avisos"] = avisos
    _imprimir(salida)
    return 0


def _avisos(arneses: list[Arnes], worktrees: Path) -> list[str]:
    avisos = []
    if arneses and shutil.which(adaptadores.COMANDO_PROXY[0]) is None:
        avisos.append(
            f"`{adaptadores.COMANDO_PROXY[0]}` no está en el PATH: el arnés no podrá lanzar el proxy. "
            "Pon el binario de la release en un PATH que vea el arnés (~/.local/bin/railspec) o instala "
            "railspec-local con pipx; ver railspec/docs/proxy-local.md."
        )
    if Arnes.codex in arneses:
        avisos.append(
            "Codex solo carga .codex/config.toml (y con él el servidor railspec) en proyectos de confianza: "
            "acepta «Trust this folder» la primera vez. Para que el sandbox escriba en los worktrees, "
            f"lánzalo con `codex --add-dir {worktrees}`. Arranca una unidad con `$railspec <petición>`."
        )
        avisos.append(
            "Codex no ejecuta el hook de la guardia (.codex/hooks.json) hasta que lo confíes: abre /hooks, "
            "revisa el hook de Railspec y confíalo (otra vez cada vez que `instalar` lo cambie). Sin eso "
            "la guardia no corre y Codex solo avisa de que el hook necesita revisión."
        )
    if Arnes.copilot in arneses:
        avisos.append(
            "Copilot solo carga los servidores MCP de .mcp.json y los hooks de .github/hooks (la guardia) "
            f"en carpetas de confianza: acéptala la primera vez. Lánzalo con `copilot --add-dir {worktrees}` "
            "para trabajar en los worktrees. "
            "Las tools del bucle quedan aprobadas al invocar /railspec; unit_approve, unit_set_mode y "
            "unit_integrate preguntan siempre."
        )
    return avisos


def _cmd_desinstalar(args: argparse.Namespace) -> int:
    raiz = _raiz(args.repo)
    arneses = [Arnes(a) for a in args.arnes] if args.arnes else adaptadores.instalados(raiz)
    worktrees = _dir_worktrees(raiz)
    quedan = [a for a in adaptadores.instalados(raiz) if a not in arneses]
    cambios = {a.value: adaptadores.desinstalar(raiz, a, worktrees, quedan) for a in arneses}
    salida: dict[str, Any] = {"cambios": cambios}
    ruta_config = raiz / config.ARCHIVO_CONFIG
    if args.config and ruta_config.is_file():
        ruta_config.unlink()
        if ruta_config.parent.is_dir() and not any(ruta_config.parent.iterdir()):
            ruta_config.parent.rmdir()
        salida["config"] = f"{ruta_config} borrado"
    unidades = git.worktrees(raiz)
    if unidades:
        # Las unidades y su estado local no son del adaptador: se dejan donde están.
        salida["unidades_en_local"] = {u: str(r) for u, r in sorted(unidades.items())}
    _imprimir(salida)
    return 0


def _cmd_insumo(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    _imprimir(asyncio.run(proxy.traer_insumo(UUID(args.id), args.unidad)))
    return 0


def _cmd_exportar(args: argparse.Namespace) -> int:
    from . import portabilidad

    proxy = crear_proxy(_raiz(args.repo))
    destino = Path(args.destino or f"{args.unidad}.railspec-unidad").resolve()
    _imprimir(asyncio.run(portabilidad.exportar(proxy, args.unidad, destino)))
    return 0


def _cmd_importar(args: argparse.Namespace) -> int:
    from . import portabilidad

    raiz = _raiz(args.repo)
    convertir = args.solo_convertir is not None
    repositorio = None if convertir else config.leer_config_repositorio(raiz).repositorio
    conversiones = [portabilidad.leer_origen(Path(r).resolve(), repositorio) for r in args.rutas]
    if convertir:
        # Sin servidor: deja los paquetes en disco para revisarlos antes de importar.
        destino = Path(args.solo_convertir).resolve()
        escritos = [
            {
                "paquete": str(portabilidad.escribir_paquete(c, destino / c.paquete.origen.id_original)),
                "fase_retomar": c.paquete.fase_retomar.value,
                "avisos": c.avisos,
            }
            for c in conversiones
        ]
        _imprimir({"paquetes": escritos})
        return 0
    proxy = crear_proxy(raiz)
    resultados = [asyncio.run(portabilidad.importar(proxy, c)) for c in conversiones]
    _imprimir(resultados[0] if len(resultados) == 1 else resultados)
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


def _cmd_hook(args: argparse.Namespace) -> int:
    """Lee la entrada del hook por stdin y responde por stdout; ante un fallo, rechaza.

    Siempre sale con 0: la decisión viaja en el JSON, que es lo que el arnés lee.
    """

    try:
        respuesta = guardia.HOOKS[args.arnes_hook](json.loads(sys.stdin.read() or "{}"))
    except Exception as exc:  # noqa: BLE001 - la guardia falla cerrada, con salida de emergencia
        motivo = (
            f"Railspec: la guardia falló ({exc}). Relanza el arnés con {guardia.ENV_GUARDIA}=0 para saltarla."
        )
        respuesta = guardia.denegar(args.arnes_hook, motivo)
    if respuesta is not None:
        print(json.dumps(respuesta, ensure_ascii=False))
    return 0


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

    des = sub.add_parser(
        "desinstalar", help="Quita los adaptadores de arnés; las unidades y sus worktrees se conservan."
    )
    des.add_argument(
        "--arnes",
        action="append",
        choices=[a.value for a in adaptadores.ADAPTADORES],
        help="Repetible. Por defecto, todos los que estén instalados.",
    )
    des.add_argument("--config", action="store_true", help=f"Borra también {config.ARCHIVO_CONFIG}.")
    des.set_defaults(fn=_cmd_desinstalar)

    ins = sub.add_parser("insumo", help="Insumos exportados desde el chat de la consola.")
    ins_sub = ins.add_subparsers(dest="accion", required=True)
    pull = ins_sub.add_parser("pull", help="Escribe el insumo en .railspec/insumos/.")
    pull.add_argument("id")
    pull.add_argument("--unidad")
    pull.set_defaults(fn=_cmd_insumo)

    exp = sub.add_parser("exportar", help="Escribe una unidad como paquete railspec.unidad/v1 en disco.")
    exp.add_argument("--unidad", required=True)
    exp.add_argument("--destino", help="Carpeta nueva (por defecto, <unidad>.railspec-unidad).")
    exp.set_defaults(fn=_cmd_exportar)

    imp = sub.add_parser(
        "importar",
        help="Registra en el servidor una unidad del kit SDD (.spec/units/<id>) o un paquete exportado.",
    )
    imp.add_argument("rutas", nargs="+", help="Carpetas de unidad del kit o de paquete.")
    imp.add_argument(
        "--solo-convertir",
        metavar="DESTINO",
        help="No contacta el servidor: escribe los paquetes en DESTINO.",
    )
    imp.set_defaults(fn=_cmd_importar)

    est = sub.add_parser("estado", help="Estado remoto y local de una unidad.")
    est.add_argument("--unidad")
    est.set_defaults(fn=_cmd_estado)

    syn = sub.add_parser("sync", help="Envía la cola de reportes pendientes.")
    syn.add_argument("--unidad")
    syn.set_defaults(fn=_cmd_sync)

    hook = sub.add_parser("hook", help="Guardia de las reglas de conducta; la llaman los hooks del arnés.")
    hook.add_argument("arnes_hook", metavar="arnes", choices=sorted(guardia.HOOKS))
    hook.set_defaults(fn=_cmd_hook)
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
