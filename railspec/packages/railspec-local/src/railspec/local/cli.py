"""Comando ``railspec``.

- ``railspec mcp``: servidor MCP por stdio para el arnés (lo lanza el arnés).
- ``railspec instalar``: configura el repositorio y los adaptadores de arnés; con ``--alcance usuario``,
  los adaptadores en la configuración del usuario (Claude Code y OpenCode) para todos los repositorios.
- ``railspec desinstalar``: quita los adaptadores (y, si se pide, la configuración).
- ``railspec doctor``: diagnóstico de solo lectura; sale con 1 si algo falla.
- ``railspec login``, ``logout`` y ``whoami``: sesión del desarrollador con GitHub (device flow) en
  vez de exportar ``RAILSPEC_TOKEN`` a mano.
- ``railspec insumo pull <id>``: trae un insumo del chat de la consola.
- ``railspec exportar`` e ``railspec importar``: una unidad a o desde un paquete en
  disco; ``importar`` también convierte a demanda unidades del kit SDD (``.spec/units/``).
- ``railspec estado`` y ``railspec sync``: estado local y envío de la cola.
- ``railspec mandato``: redacta (``proponer``), consulta (``estado``, ``listar``) o revoca un mandato de
  los modos supervisado y desatendido. Aprobarlo no está aquí: lo hace una persona en la consola web.
- ``railspec hook <arnés>``: guardia de las reglas de conducta (la llaman los hooks del arnés).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import sys
from contextlib import suppress
from datetime import timedelta
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import TypeAdapter, ValidationError
from railspec.contracts.comun import Arnes, NivelCodigo, Slug
from railspec.contracts.mandato import EstadoMandato

from . import (
    __version__,
    adaptadores,
    config,
    credenciales,
    dispositivo,
    doctor,
    git,
    guardia,
    plataforma,
    renovacion,
)
from .adaptadores import usuario
from .almacen import EXCLUIR_DE_GIT
from .cliente import ClienteServidor
from .errores import ConfigInvalida, CredencialesInvalidas, ErrorRailspec, LoginFallido
from .indice import cargar_indexador
from .proxy import ProxyLocal


def crear_cliente(url: str, fuente: credenciales.FuenteToken) -> ClienteServidor:
    from .transporte_mcp import TransporteMcpHttp

    return ClienteServidor(TransporteMcpHttp(url, fuente))


def crear_proxy(raiz: Path) -> ProxyLocal:
    cfg = config.cargar(raiz)
    if not cfg.url:
        raise ConfigInvalida(f"Falta {config.ENV_URL}: el endpoint MCP de railspec-server.")
    # RAILSPEC_TOKEN manda; sin él, la sesión de `railspec login`, releída en cada petición.
    fuente = renovacion.fuente_con_renovacion(cfg.url, cfg.token, credenciales.AlmacenCredenciales())
    return ProxyLocal(cfg, crear_cliente(cfg.url, fuente), cargar_indexador())


def _raiz(desde: str | None) -> Path:
    ruta = Path(desde or ".").resolve()
    try:
        return git.raiz_repositorio(ruta)
    except git.ErrorGit:
        raise ConfigInvalida(
            f"{ruta} no está dentro de un repositorio git: ejecuta railspec en el clon donde trabajas "
            "(o pasa --repo <ruta>)."
        ) from None


def _imprimir(datos: Any) -> None:
    print(json.dumps(datos, indent=2, ensure_ascii=False, default=str))


def _cmd_mcp(args: argparse.Namespace) -> int:
    from .servidor_mcp import servir

    # El repositorio se busca al primer uso, no al arrancar: instalado con `--alcance usuario`, el arnés
    # lanza este servidor en cualquier carpeta y fuera de un repositorio de Railspec debe conectar igual
    # (la tool responde qué falta) en vez de caerse al arrancar.
    servir(lambda: crear_proxy(_raiz(args.repo)))
    return 0


def _dir_worktrees(raiz: Path) -> Path:
    return Path(os.environ.get(config.ENV_WORKTREES) or config.dir_worktrees_por_defecto(raiz))


#: Configuración por máquina que el adaptador de Claude Code escribe: nunca se versiona.
EXCLUIR_ADAPTADORES = ["/.claude/settings.local.json"]


def _cmd_instalar(args: argparse.Namespace) -> int:
    if args.alcance == ALCANCE_USUARIO:
        return _instalar_usuario(args)
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
        "siguiente": (
            f"exporta {config.ENV_URL}, inicia sesión con `railspec login` (o exporta {config.ENV_TOKEN}) "
            "y reinicia el arnés"
        ),
    }
    avisos = _avisos(arneses, worktrees)
    if avisos:
        salida["avisos"] = avisos
    _imprimir(salida)
    return 0


#: Dónde vale lo que instala `railspec instalar`: este repositorio o la configuración del usuario.
ALCANCE_REPOSITORIO = "repositorio"
ALCANCE_USUARIO = "usuario"


def _aviso_comando() -> str | None:
    if shutil.which(adaptadores.COMANDO_PROXY[0]) is not None:
        return None
    return (
        f"`{adaptadores.COMANDO_PROXY[0]}` no está en el PATH: el arnés no podrá lanzar el proxy. "
        "Pon el binario de la release en un PATH que vea el arnés (~/.local/bin/railspec) o instala "
        "railspec-local con pipx; ver railspec/docs/proxy-local.md."
    )


def _arneses_de_usuario(args: argparse.Namespace) -> list[Arnes]:
    """Valida ``--arnes`` para el alcance de usuario antes de escribir nada."""

    arneses = [Arnes(a) for a in (args.arnes or [])]
    for arnes in arneses:
        usuario.comprobar(arnes)
    return arneses


def _instalar_usuario(args: argparse.Namespace) -> int:
    propios = [
        f
        for f, v in (
            ("--org", args.org),
            ("--workspace", args.workspace),
            ("--repositorio", args.repositorio),
            ("--nivel", args.nivel),
        )
        if v
    ]
    if propios:
        raise ConfigInvalida(
            f"{', '.join(propios)} son del repositorio, no del usuario: escribe esa configuración en cada "
            "clon con `railspec instalar --org … --workspace … --repositorio …` (sin --alcance ni --arnes)."
        )
    arneses = _arneses_de_usuario(args)
    if not arneses:
        raise ConfigInvalida(
            "Con --alcance usuario indica el arnés: "
            f"{', '.join(f'--arnes {a.value}' for a in usuario.ARNESES)}."
        )
    worktrees = os.environ.get(config.ENV_WORKTREES)
    carpeta = Path(worktrees) if worktrees else None
    if args.verificar:
        deriva = {a.value: usuario.verificar(a, carpeta) for a in arneses}
        _imprimir({"alcance": ALCANCE_USUARIO, "deriva": deriva})
        return 1 if any(deriva.values()) else 0
    cambios = {a.value: usuario.instalar(a, carpeta) for a in arneses}
    salida: dict[str, Any] = {
        "alcance": ALCANCE_USUARIO,
        "cambios": cambios,
        "siguiente": (
            "en cada repositorio, `railspec instalar --org … --workspace … --repositorio …` (sin --arnes) "
            f"escribe su .railspec/config.json; exporta {config.ENV_URL}, inicia sesión con `railspec login` "
            "y reinicia el arnés"
        ),
    }
    avisos = [a for a in (_aviso_comando(),) if a]
    if Arnes.claude_code in arneses and carpeta is None:
        avisos.append(
            "Claude Code pedirá permiso la primera vez que edite en la carpeta de worktrees de cada "
            "repositorio (<repo>.railspec). Para abrirla de una vez, fija "
            f"{config.ENV_WORKTREES} a una carpeta común y repite este comando."
        )
    if avisos:
        salida["avisos"] = avisos
    _imprimir(salida)
    return 0


def _avisos(arneses: list[Arnes], worktrees: Path) -> list[str]:
    avisos = []
    if arneses and (aviso := _aviso_comando()):
        avisos.append(aviso)
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
            "Las tools del bucle quedan aprobadas al invocar /railspec; unit_approve, unit_set_mode, "
            "unit_integrate y mandate_revoke preguntan siempre."
        )
    return avisos


def _desinstalar_usuario(args: argparse.Namespace) -> int:
    if args.config:
        raise ConfigInvalida(
            "--config borra el .railspec/config.json del repositorio; con --alcance usuario no hay "
            "configuración de repositorio que borrar."
        )
    arneses = _arneses_de_usuario(args) or usuario.instalados()
    worktrees = os.environ.get(config.ENV_WORKTREES)
    cambios = {a.value: usuario.desinstalar(a, Path(worktrees) if worktrees else None) for a in arneses}
    _imprimir({"alcance": ALCANCE_USUARIO, "cambios": cambios})
    return 0


def _cmd_desinstalar(args: argparse.Namespace) -> int:
    if args.alcance == ALCANCE_USUARIO:
        return _desinstalar_usuario(args)
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


def crear_flujo(client_id: str | None) -> dispositivo.FlujoDispositivo:
    return dispositivo.FlujoDispositivo(client_id)


def _aviso(texto: str) -> None:
    print(f"railspec: {texto}", file=sys.stderr)


def _url_servidor() -> str:
    url = os.environ.get(config.ENV_URL)
    if not url:
        raise ConfigInvalida(
            f"Falta {config.ENV_URL}: el endpoint MCP de railspec-server. La sesión se guarda por servidor."
        )
    return url


def _iso(momento: Any) -> str | None:
    return momento.isoformat() if momento is not None else None


def _cmd_login(args: argparse.Namespace) -> int:
    url = _url_servidor()
    almacen = credenciales.AlmacenCredenciales()
    ruta = almacen.ruta  # si no hay dónde guardar la sesión, falla ya y no tras autorizar en GitHub
    try:
        previa = almacen.leer(url)
    except CredencialesInvalidas:
        previa = None  # un archivo dañado no impide iniciar sesión: `guardar` lo reescribe
    client_id = args.client_id or os.environ.get(config.ENV_GITHUB_CLIENT_ID)
    if not client_id:
        # El servidor lo publica (no es secreto): es la fuente por defecto, y gana a la sesión guardada
        # por si el administrador cambió de GitHub App. Sin respuesta, vale el de la sesión.
        print(f"Consultando la GitHub App de {renovacion.url_base(url)}…", file=sys.stderr)
        client_id = renovacion.descubrir_client_id(url) or (previa and previa.client_id)
    if not client_id:
        raise ConfigInvalida(
            "No se pudo averiguar el client id de la GitHub App de Railspec: el servidor no lo publica "
            "(¿versión anterior, sin la GitHub App o dormido? reintenta) y no hay otro. Pásalo con "
            f"--client-id o exporta {config.ENV_GITHUB_CLIENT_ID} (no es secreto; lo publica quien "
            "administra el servidor). Se recuerda en la sesión guardada: las veces siguientes basta "
            "`railspec login`."
        )
    if os.environ.get(config.ENV_TOKEN):
        _aviso(
            f"{config.ENV_TOKEN} está exportada y tiene prioridad sobre la sesión que vas a guardar: "
            "quítala (unset) para usar la de `railspec login`."
        )
    try:
        with crear_flujo(client_id) as flujo:
            codigo = flujo.solicitar_codigo()
            print(
                f"\nAbre {codigo.verification_uri} e introduce el código:\n\n    {codigo.user_code}\n\n"
                f"Esperando la autorización (el código vence en {max(codigo.expires_in // 60, 1)} min; "
                "Ctrl+C cancela)…",
                file=sys.stderr,
            )
            token = flujo.esperar_token(codigo)
            persona = flujo.persona(token.access_token)
    except KeyboardInterrupt:
        raise LoginFallido("Cancelado: no se inició sesión.") from None
    ahora = credenciales.ahora_utc()
    credencial = credenciales.Credencial(
        access_token=token.access_token,
        client_id=client_id,
        login=persona.login if persona else None,
        github_id=persona.github_id if persona else None,
        obtenido_en=ahora,
        expira_en=ahora + timedelta(seconds=token.expires_in) if token.expires_in else None,
        refresh_token=token.refresh_token,
        refresh_expira_en=(
            ahora + timedelta(seconds=token.refresh_expires_in)
            if token.refresh_token and token.refresh_expires_in
            else None
        ),
    )
    almacen.guardar(url, credencial)
    salida: dict[str, Any] = {
        "servidor": url,
        "login": credencial.login,
        "github_id": credencial.github_id,
        "expira_en": _iso(credencial.expira_en),
        "renovable": credencial.refresh_token is not None,
        "credenciales": str(ruta),
        "siguiente": "el proxy usa esta sesión en su próxima llamada; no hace falta reiniciar el arnés",
    }
    if persona is None:
        salida["aviso"] = "GitHub no dijo de quién es el token; se guardó igual."
    if credencial.expira_en is not None and credencial.refresh_token is not None:
        hasta = (
            f" hasta el {credencial.refresh_expira_en:%Y-%m-%d} UTC" if credencial.refresh_expira_en else ""
        )
        salida["renovacion"] = (
            "Este token vence, pero el proxy lo renueva solo (a través del servidor) mientras lo uses"
            f"{hasta}: pasado ese plazo sin renovarla, ejecuta `railspec login` otra vez."
        )
    elif credencial.expira_en is not None:
        salida["aviso_vencimiento"] = (
            "Este token vence y GitHub no entregó un refresh token con el que renovarlo: cuando venza, "
            "ejecuta `railspec login` otra vez."
        )
    _imprimir(salida)
    return 0


def _cmd_logout(args: argparse.Namespace) -> int:
    url = _url_servidor()
    almacen = credenciales.AlmacenCredenciales()
    cerrada = almacen.borrar(url)
    salida: dict[str, Any] = {"servidor": url, "cerrada": cerrada}
    if cerrada:
        salida["aviso"] = (
            "Se borró la sesión de este equipo; el token sigue vigente en GitHub hasta que venza. Para "
            "revocarlo ya, quita la GitHub App de Railspec en https://github.com/settings/apps/authorizations."
        )
    if os.environ.get(config.ENV_TOKEN):
        salida["entorno"] = f"{config.ENV_TOKEN} sigue exportada: el proxy seguirá usándola."
    _imprimir(salida)
    return 0


def _cmd_whoami(args: argparse.Namespace) -> int:
    url = _url_servidor()
    almacen = credenciales.AlmacenCredenciales()
    fuente = renovacion.fuente_con_renovacion(url, os.environ.get(config.ENV_TOKEN), almacen)
    fuente.token()  # lo que haría el proxy ahora: con la sesión vencida y un refresh token vigente, renueva
    sesion = fuente.sesion()
    salida: dict[str, Any] = {
        "servidor": url,
        "origen": {"entorno": config.ENV_TOKEN, "credenciales": "railspec login"}.get(sesion.origen),
    }
    credencial = sesion.credencial
    if sesion.origen == "entorno":
        with suppress(CredencialesInvalidas):
            guardada = almacen.leer(url)
            if guardada is not None:
                quien = guardada.login or "otra persona"
                salida["ignorada"] = f"la sesión guardada de {quien}: {config.ENV_TOKEN} tiene prioridad"
    if credencial is not None:
        salida |= {
            "login": credencial.login,
            "github_id": credencial.github_id,
            "client_id": credencial.client_id,
            "obtenido_en": _iso(credencial.obtenido_en),
            "expira_en": _iso(credencial.expira_en),
            "vencida": sesion.vencida,
            "renovable": credencial.refresh_token is not None,
            "refresh_expira_en": _iso(credencial.refresh_expira_en),
        }
        if fuente.renovada:
            salida["renovada"] = "la sesión estaba vencida y se renovó con su refresh token"
    if sesion.problema:
        salida["problema"] = sesion.problema
    valida = sesion.token is not None
    if args.comprobar and sesion.token is not None:
        with crear_flujo(None) as flujo:
            persona = flujo.comprobar(sesion.token)
        salida["github"] = (
            {"valido": True, "login": persona.login, "github_id": persona.github_id}
            if persona
            else {"valido": False}
        )
        valida = persona is not None
    if not valida:
        salida["siguiente"] = (
            sesion.nota()
            if sesion.token is None or sesion.origen == "entorno"
            else "ejecuta `railspec login`"
        )
    _imprimir(salida)
    return 0 if valida else 1


def _cmd_insumo(args: argparse.Namespace) -> int:
    try:
        insumo = UUID(args.id)
    except ValueError:
        raise ConfigInvalida(
            f"«{args.id}» no es un id de insumo (un UUID, como el que muestra el chat de la consola)."
        ) from None
    proxy = crear_proxy(_raiz(args.repo))
    _imprimir(asyncio.run(proxy.traer_insumo(insumo, args.unidad)))
    return 0


def _cmd_indice(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))

    def progreso(hechos: int, total: int) -> None:
        print(
            f"\rVectores: {hechos}/{total}", end="" if hechos < total else "\n", file=sys.stderr, flush=True
        )

    _imprimir(
        asyncio.run(proxy.indexar_codigo(args.unidad, args.vectores, progreso if args.vectores else None))
    )
    return 0


def _cmd_modelo(args: argparse.Namespace) -> int:
    from . import codificador

    if args.accion == "instalar":
        destino = codificador.instalar(args.nombre, lambda m: print(m, file=sys.stderr))
        _imprimir({"instalado": args.nombre, "directorio": str(destino)})
        return 0
    try:
        local = codificador.cargar()
        estado: dict[str, object] = {
            "directorio": str(codificador.directorio_de()),
            "instalado": local is not None,
        }
        if local is not None:
            estado.update(nombre=local.nombre, dimensiones=local.dimensiones)
    except codificador.CodificadorNoDisponible as exc:
        estado = {"directorio": str(codificador.directorio_de()), "instalado": True, "problema": str(exc)}
    _imprimir(estado)
    return 0


def _cmd_evaluar_busqueda(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    _imprimir(asyncio.run(proxy.evaluar_busqueda(Path(args.casos), args.unidad)))
    return 0


def _cmd_buscar(args: argparse.Namespace) -> int:
    proxy = crear_proxy(_raiz(args.repo))
    resultado = asyncio.run(
        proxy.buscar_codigo(
            " ".join(args.texto), args.limite, args.tipo or None, args.ruta, args.unidad, args.modo
        )
    )
    _imprimir(resultado)
    return 0 if resultado["resultados"] else 1


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


def _leer_contenido_mandato(ruta: Path) -> Any:
    """El contenido de un mandato desde un archivo JSON o YAML (YAML solo si pyyaml está instalado)."""

    try:
        texto = ruta.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigInvalida(f"No se pudo leer {ruta}: {exc.strerror or exc}.") from exc
    if ruta.suffix.lower() in (".yaml", ".yml"):
        try:
            import yaml
        except ImportError:
            raise ConfigInvalida(
                f"{ruta.name} es YAML y pyyaml no está instalado: conviértelo a JSON (.json)."
            ) from None
        try:
            return yaml.safe_load(texto)
        except yaml.YAMLError as exc:
            raise ConfigInvalida(f"{ruta} no es YAML válido: {exc}") from exc
    try:
        return json.loads(texto)
    except json.JSONDecodeError as exc:
        raise ConfigInvalida(f"{ruta} no es JSON válido: {exc}") from exc


def _id_mandato(texto: str) -> str:
    try:
        return TypeAdapter(Slug).validate_python(texto)
    except ValidationError:
        raise ConfigInvalida(
            f"«{texto}» no es un id de mandato válido (un slug: minúsculas, números y guiones, como el "
            "`plan` de las unidades)."
        ) from None


def _cmd_mandato(args: argparse.Namespace) -> int:
    """Aprobar no existe aquí a propósito: es un acto de una persona en la consola web."""

    if args.accion == "proponer":
        ruta = Path(args.archivo)
        mandato = _id_mandato(args.id or ruta.stem)
        contenido = _leer_contenido_mandato(ruta)
        proxy = crear_proxy(_raiz(args.repo))
        _imprimir(asyncio.run(proxy.proponer_mandato(mandato, contenido, args.version_vista)))
        return 0
    proxy = crear_proxy(_raiz(args.repo))
    if args.accion == "estado" and args.id:
        _imprimir(asyncio.run(proxy.ver_mandato(_id_mandato(args.id))))
    elif args.accion in ("estado", "listar"):
        estados = [EstadoMandato(e) for e in getattr(args, "estado", None) or []]
        _imprimir(asyncio.run(proxy.listar_mandatos(estados, getattr(args, "limite", 50))))
    else:
        _imprimir(asyncio.run(proxy.revocar_mandato(_id_mandato(args.id), args.motivo, args.version_vista)))
    return 0


def _cmd_doctor(args: argparse.Namespace) -> int:
    desde = Path(args.repo or ".").resolve()
    comprobaciones = asyncio.run(doctor.diagnosticar(desde, crear_cliente))
    if args.json:
        _imprimir(
            {
                "ok": not doctor.hay_fallos(comprobaciones),
                "resumen": doctor.resumen(comprobaciones),
                "comprobaciones": [c.como_json() for c in comprobaciones],
            }
        )
    else:
        print(doctor.formatear(comprobaciones))
    return 1 if doctor.hay_fallos(comprobaciones) else 0


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
    inst.add_argument(
        "--alcance",
        choices=[ALCANCE_REPOSITORIO, ALCANCE_USUARIO],
        default=ALCANCE_REPOSITORIO,
        help=(
            "repositorio (por defecto): este clon. usuario: la configuración del usuario del arnés, para "
            "todos los repositorios (solo claude-code y opencode)."
        ),
    )
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
    des.add_argument(
        "--alcance",
        choices=[ALCANCE_REPOSITORIO, ALCANCE_USUARIO],
        default=ALCANCE_REPOSITORIO,
        help="Dónde quitarlos: este repositorio (por defecto) o la configuración del usuario.",
    )
    des.set_defaults(fn=_cmd_desinstalar)

    doc = sub.add_parser(
        "doctor",
        help="Diagnóstico de solo lectura (servidor, sesión, contrato, adaptadores, indexador, worktrees).",
    )
    doc.add_argument("--json", action="store_true", help="Salida en JSON en vez de texto.")
    doc.set_defaults(fn=_cmd_doctor)

    login = sub.add_parser(
        "login", help="Inicia sesión con GitHub (device flow) y guarda tu token de usuario en este equipo."
    )
    login.add_argument(
        "--client-id",
        help=(
            f"Client id de la GitHub App de Railspec (por defecto {config.ENV_GITHUB_CLIENT_ID}); "
            "si no hay ninguno, el que publica el servidor)."
        ),
    )
    login.set_defaults(fn=_cmd_login)

    sub.add_parser("logout", help="Borra la sesión guardada para el servidor de RAILSPEC_URL.").set_defaults(
        fn=_cmd_logout
    )

    who = sub.add_parser("whoami", help="Quién eres para el proxy y de dónde sale el token.")
    who.add_argument("--comprobar", action="store_true", help="Pregunta a GitHub si el token sigue valiendo.")
    who.set_defaults(fn=_cmd_whoami)

    ins = sub.add_parser("insumo", help="Insumos exportados desde el chat de la consola.")
    ins_sub = ins.add_subparsers(dest="accion", required=True)
    pull = ins_sub.add_parser("pull", help="Escribe el insumo en .railspec/insumos/.")
    pull.add_argument("id")
    pull.add_argument("--unidad")
    pull.set_defaults(fn=_cmd_insumo)

    idx = sub.add_parser(
        "indice", help="Construye el índice de texto local que usa `railspec buscar` y code_search."
    )
    idx.add_argument("--unidad")
    idx.add_argument(
        "--vectores",
        action="store_true",
        help="Calcula también los embeddings con el modelo local (minutos; se puede interrumpir).",
    )
    idx.set_defaults(fn=_cmd_indice)

    mod = sub.add_parser("modelo", help="Modelo local de embeddings para la búsqueda semántica (opcional).")
    mod_sub = mod.add_subparsers(dest="accion", required=True)
    inst = mod_sub.add_parser("instalar", help="Descarga el modelo (hash verificado) a tu carpeta de datos.")
    inst.add_argument("nombre", nargs="?", default="jina-v2-base-code")
    inst.set_defaults(fn=_cmd_modelo)
    mod_sub.add_parser("estado", help="Dónde está el modelo y si se puede usar.").set_defaults(fn=_cmd_modelo)

    ev = sub.add_parser(
        "evaluar-busqueda",
        help='Compara texto, semántico e híbrido con tus consultas: [{"consulta": …, "esperados": […]}].',
    )
    ev.add_argument(
        "casos", help="Archivo JSON con las consultas y los nombres de símbolo que deberían salir."
    )
    ev.add_argument("--unidad")
    ev.set_defaults(fn=_cmd_evaluar_busqueda)

    bus = sub.add_parser("buscar", help="Busca texto en el código del clon (índice local, sin red).")
    bus.add_argument("texto", nargs="+")
    bus.add_argument("--limite", type=int, default=20)
    bus.add_argument("--tipo", action="append", help="Tipo de símbolo; repetible.")
    bus.add_argument("--ruta", help="Prefijo de ruta.")
    bus.add_argument("--unidad")
    bus.add_argument(
        "--modo",
        choices=("auto", "texto", "semantico", "hibrido"),
        default="auto",
        help="Cómo ordenar (auto).",
    )
    bus.set_defaults(fn=_cmd_buscar)

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

    man = sub.add_parser(
        "mandato",
        help="Mandato de supervisado y desatendido: redactar, consultar o revocar (aprobar: en la consola).",
    )
    man_sub = man.add_subparsers(dest="accion", required=True)
    prop = man_sub.add_parser(
        "proponer",
        help="Redacta un borrador desde un archivo JSON o YAML; lo aprueba una persona en la consola.",
    )
    prop.add_argument("archivo", help="Contenido del mandato (.json; .yaml/.yml si pyyaml está instalado).")
    prop.add_argument(
        "--id", help="Id del mandato (el `plan` de las unidades); por defecto, el nombre del archivo."
    )
    prop.add_argument(
        "--version-vista", type=int, help="Versión que conoces, para editar un mandato que ya existe."
    )
    mest = man_sub.add_parser(
        "estado", help="Un mandato con su vigencia, unidades y decisiones; sin id, los lista."
    )
    mest.add_argument("id", nargs="?")
    mlis = man_sub.add_parser("listar", help="Los mandatos del workspace.")
    mlis.add_argument(
        "--estado", action="append", choices=[e.value for e in EstadoMandato], help="Repetible."
    )
    mlis.add_argument("--limite", type=int, default=50)
    mrev = man_sub.add_parser("revocar", help="Cierra un mandato para siempre y detiene sus unidades.")
    mrev.add_argument("id")
    mrev.add_argument("--motivo", required=True)
    mrev.add_argument("--version-vista", type=int, help="Por defecto, la versión vigente.")
    man.set_defaults(fn=_cmd_mandato)

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
    plataforma.forzar_utf8()
    args = parser().parse_args(argv)
    try:
        return args.fn(args)
    except ErrorRailspec as exc:
        print(f"railspec: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
