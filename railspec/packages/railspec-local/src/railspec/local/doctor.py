"""``railspec doctor``: diagnóstico de solo lectura del proxy en esta máquina y este repositorio.

Responde a «¿por qué no funciona?» sin tocar nada: no escribe en el repositorio, en los worktrees ni en la
configuración del arnés, no corrige permisos, no arranca unidades y no cambia nada en el servidor (la única
llamada es ``unit.list``). Cada comprobación termina en ``ok``, ``aviso`` (algo a revisar que no impide
trabajar) o ``fallo`` (algo roto); ``railspec doctor`` sale con 1 si hay algún fallo.

Comprobaciones, por orden: ``repositorio`` (clon y ``.railspec/config.json``), ``comando`` (``railspec``
en el ``PATH`` del arnés), ``servidor`` (``RAILSPEC_URL`` responde), ``sesion`` (token local y aceptado por
el servidor), ``contrato`` (el servidor publica las tools de este proxy), ``adaptadores`` (deriva respecto
de esta versión), ``codex`` (confianza del hook de la guardia), ``indexador`` (versión de
``codebase-memory-mcp``) y ``worktrees`` (huérfanos y estados rotos).
"""

from __future__ import annotations

import asyncio
import json
import os
import shutil
import tomllib
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import ValidationError
from railspec.contracts._base import VERSION_CONTRATO
from railspec.contracts.comun import AlcanceWorkspace
from railspec.contracts.tools import TOOLS, Superficie, UnitListEntrada, UnitListSalida, nombre_mcp

from . import adaptadores, config, git, indexador_cbm
from .adaptadores import usuario
from .almacen import ARCHIVO_ESTADO, Almacen
from .cliente import ClienteServidor
from .credenciales import AlmacenCredenciales, FuenteToken, ahora_utc
from .errores import (
    ConfigInvalida,
    ErrorRailspec,
    ErrorServidor,
    RespuestaInvalida,
    ServidorRechazo,
    SinConexion,
)

Estado = Literal["ok", "aviso", "fallo"]

#: Lo que se espera a un servidor antes de darlo por caído.
TOPE_SERVIDOR_S = 20.0
#: Un token que vence dentro de este margen se avisa antes de que falle a media tarea.
AVISO_VENCIMIENTO_S = 3600
#: Un refresh token que vence dentro de este margen (7 días) se avisa: sin él ya no hay renovación.
AVISO_REFRESH_S = 7 * 24 * 3600
#: Tools que este proxy espera encontrar en el servidor: las que el contrato publica por MCP.
TOOLS_ESPERADAS = frozenset(nombre_mcp(n) for n, d in TOOLS.items() if Superficie.mcp in d.superficies)

FabricaCliente = Callable[[str, FuenteToken], ClienteServidor]


@dataclass(frozen=True)
class Comprobacion:
    nombre: str
    estado: Estado
    detalle: str
    remedio: str | None = None
    #: Líneas de detalle (una por elemento afectado).
    items: tuple[str, ...] = field(default_factory=tuple)

    def como_json(self) -> dict[str, object]:
        datos: dict[str, object] = {"nombre": self.nombre, "estado": self.estado, "detalle": self.detalle}
        if self.remedio:
            datos["remedio"] = self.remedio
        if self.items:
            datos["items"] = list(self.items)
        return datos


def _ok(nombre: str, detalle: str, items: tuple[str, ...] = ()) -> Comprobacion:
    return Comprobacion(nombre, "ok", detalle, items=items)


def _aviso(
    nombre: str, detalle: str, remedio: str | None = None, items: tuple[str, ...] = ()
) -> Comprobacion:
    return Comprobacion(nombre, "aviso", detalle, remedio, items)


def _fallo(
    nombre: str, detalle: str, remedio: str | None = None, items: tuple[str, ...] = ()
) -> Comprobacion:
    return Comprobacion(nombre, "fallo", detalle, remedio, items)


def hay_fallos(comprobaciones: list[Comprobacion]) -> bool:
    return any(c.estado == "fallo" for c in comprobaciones)


def resumen(comprobaciones: list[Comprobacion]) -> dict[str, int]:
    return {e: sum(1 for c in comprobaciones if c.estado == e) for e in ("ok", "aviso", "fallo")}


_MARCA = {"ok": "✓", "aviso": "!", "fallo": "✗"}


def formatear(comprobaciones: list[Comprobacion]) -> str:
    """Informe para leer en la terminal: una línea por comprobación, con el remedio debajo."""

    ancho = max((len(c.nombre) for c in comprobaciones), default=0)
    lineas = []
    for c in comprobaciones:
        lineas.append(f"{_MARCA[c.estado]} {c.nombre.ljust(ancho)}  {c.detalle}")
        lineas += [f"  {' ' * ancho}    - {item}" for item in c.items]
        if c.remedio:
            lineas.append(f"  {' ' * ancho}    → {c.remedio}")
    cuenta = resumen(comprobaciones)
    lineas.append("")
    lineas.append(f"{cuenta['fallo']} fallo(s), {cuenta['aviso']} aviso(s), {cuenta['ok']} bien.")
    return "\n".join(lineas)


# --- comprobaciones locales -----------------------------------------------------------------------


def _repositorio(desde: Path) -> tuple[Comprobacion, Path | None, config.ConfigRepositorio | None]:
    try:
        raiz = git.raiz_repositorio(desde)
    except ErrorRailspec:
        return (
            _fallo(
                "repositorio",
                f"{desde} no está dentro de un repositorio git.",
                "ejecuta `railspec doctor` dentro del clon, o con --repo <ruta>",
            ),
            None,
            None,
        )
    try:
        repo = config.leer_config_repositorio(raiz)
    except ConfigInvalida as exc:
        return (
            _fallo(
                "repositorio",
                str(exc),
                "`railspec instalar --org <org> --workspace <workspace> --repositorio <slug>`",
            ),
            raiz,
            None,
        )
    detalle = (
        f"{raiz}: {repo.org}/{repo.workspace}, repositorio {repo.repositorio}, "
        f"nivel {repo.nivel_codigo.value}"
    )
    return _ok("repositorio", detalle), raiz, repo


def _comando() -> Comprobacion:
    ruta = shutil.which(adaptadores.COMANDO_PROXY[0])
    if ruta is None:
        return _fallo(
            "comando",
            f"`{adaptadores.COMANDO_PROXY[0]}` no está en el PATH: el arnés no puede lanzar el proxy ni "
            "la guardia.",
            "pon el binario de la release en un PATH que vea el arnés (~/.local/bin/railspec) o instala "
            "railspec-local con pipx",
        )
    return _ok("comando", ruta)


def _sesion_local(fuente: FuenteToken | None, ahora: datetime) -> tuple[Comprobacion, bool]:
    """Qué token manda y si está en condiciones de usarse; el booleano dice si hay un token que probar."""

    if fuente is None:
        return _aviso("sesion", f"sin {config.ENV_URL} no se sabe de qué servidor mirar la sesión."), False
    sesion = fuente.sesion()
    if sesion.origen == "entorno":
        return _ok("sesion", f"token de {config.ENV_TOKEN}"), True
    if sesion.problema:
        return _fallo("sesion", sesion.problema, "`railspec login`"), False
    credencial = sesion.credencial
    if credencial is None:
        return (
            _fallo(
                "sesion",
                f"no hay sesión iniciada para este servidor ni {config.ENV_TOKEN} exportada.",
                "`railspec login`",
            ),
            False,
        )
    quien = credencial.login or "una persona sin login"
    if sesion.vencida:
        vencio = f"{credencial.expira_en:%Y-%m-%d %H:%M} UTC"
        if sesion.renovable:
            # ``doctor`` solo lee (su fuente no tiene renovador): no la renueva ni la prueba contra el
            # servidor; el proxy lo hace en su primera petición y ``railspec whoami`` lo hace ahora.
            return (
                _ok(
                    "sesion",
                    f"la sesión de {quien} venció el {vencio}; el proxy la renueva con su refresh token en "
                    "la próxima llamada (doctor solo lee: `railspec whoami` la renueva ahora)",
                ),
                False,
            )
        causa = (
            "no tiene refresh token con el que renovarse (inicia sesión otra vez para que se guarde)"
            if credencial.refresh_token is None
            else "su refresh token también venció"
        )
        return _fallo(
            "sesion", f"la sesión de {quien} venció el {vencio} y {causa}.", "`railspec login`"
        ), False
    if credencial.expira_en is None:
        return _ok("sesion", f"sesión de {quien} (railspec login, no vence)"), True
    vence = f"{credencial.expira_en:%Y-%m-%d %H:%M} UTC"
    if credencial.renovable(ahora):
        if (
            credencial.refresh_expira_en is not None
            and (credencial.refresh_expira_en - ahora).total_seconds() < AVISO_REFRESH_S
        ):
            return (
                _aviso(
                    "sesion",
                    f"el refresh token de {quien} vence el {credencial.refresh_expira_en:%Y-%m-%d} UTC: "
                    "después no podrá renovarse.",
                    "`railspec login`",
                ),
                True,
            )
        return _ok("sesion", f"sesión de {quien} (railspec login, vence {vence}; se renueva sola)"), True
    if (credencial.expira_en - ahora).total_seconds() < AVISO_VENCIMIENTO_S:
        return _aviso(
            "sesion", f"la sesión de {quien} vence el {vence} y no se renueva.", "`railspec login`"
        ), True
    return _ok(
        "sesion", f"sesión de {quien} (railspec login, vence {vence}; sin refresh token, no se renueva)"
    ), True


async def _con_tope(coro: Awaitable[object], tope_s: float) -> object:
    return await asyncio.wait_for(coro, tope_s)


@dataclass
class _Servidor:
    """Lo que se averiguó hablando con el servidor."""

    comprobacion: Comprobacion
    sesion: Comprobacion | None = None
    contrato: Comprobacion | None = None


async def _servidor(
    url: str,
    cliente: ClienteServidor,
    repo: config.ConfigRepositorio | None,
    hay_token: bool,
    tope_s: float,
) -> _Servidor:
    """``servidor`` (responde), ``sesion`` (lo acepta) y ``contrato`` (publica lo que este proxy usa)."""

    try:
        tools = await _con_tope(cliente.herramientas(), tope_s)
    except (SinConexion, TimeoutError) as exc:
        motivo = "no respondió a tiempo" if isinstance(exc, TimeoutError) else str(exc)
        return _Servidor(
            _fallo(
                "servidor",
                f"{url}: {motivo}",
                f"comprueba {config.ENV_URL} y que el servidor esté desplegado",
            )
        )
    publicadas = None if tools is None else set(tools)  # type: ignore[arg-type]
    servidor = _Servidor(
        _ok("servidor", f"{url} responde" + (f" ({len(publicadas)} tools)" if publicadas is not None else ""))
    )

    # La única llamada al servidor: unit.list, de lectura. Valida token, rol y forma de la respuesta.
    invalida: RespuestaInvalida | None = None
    if hay_token and repo is None:
        servidor.sesion = _aviso(
            "sesion", "sin la configuración del repositorio no se probó el token contra el servidor."
        )
    elif hay_token and repo is not None:
        entrada = UnitListEntrada(
            alcance=AlcanceWorkspace(org=repo.org, workspace=repo.workspace),
            repositorio=repo.repositorio,
            limite=1,
        )
        try:
            await _con_tope(cliente.llamar("unit.list", entrada, UnitListSalida), tope_s)
            servidor.sesion = _ok("sesion", "el servidor acepta el token")
        except ServidorRechazo as exc:
            servidor.sesion = _fallo("sesion", str(exc))
        except ErrorServidor as exc:
            servidor.sesion = _fallo(
                "sesion",
                f"el servidor acepta el token pero rechaza `unit_list`: {exc}",
                "revisa tu rol en el workspace",
            )
        except RespuestaInvalida as exc:
            invalida = exc
        except (SinConexion, TimeoutError) as exc:
            motivo = "no respondió a tiempo" if isinstance(exc, TimeoutError) else str(exc)
            servidor.sesion = _aviso("sesion", f"no se pudo probar el token: {motivo}")
    servidor.contrato = _contrato(publicadas, invalida)
    return servidor


def _contrato(publicadas: set[str] | None, invalida: RespuestaInvalida | None) -> Comprobacion:
    habla = f"este proxy habla el contrato {VERSION_CONTRATO}"
    if invalida is not None:
        return _fallo(
            "contrato",
            f"{habla} y la respuesta del servidor no lo cumple: {invalida}",
            "actualiza railspec-local o el servidor para que hablen la misma versión mayor",
        )
    if publicadas is None:
        return _aviso("contrato", f"{habla}; el transporte no lista las tools del servidor, sin comprobar.")
    faltan = sorted(TOOLS_ESPERADAS - publicadas)
    if faltan:
        return _fallo(
            "contrato",
            f"{habla} y el servidor no publica {', '.join(faltan)}: habla un contrato anterior.",
            "actualiza el servidor, o instala una railspec-local que hable su contrato",
        )
    sobran = sorted(publicadas - TOOLS_ESPERADAS)
    if sobran:
        return _aviso(
            "contrato",
            f"{habla}; el servidor publica además {', '.join(sobran)} (¿contrato más nuevo?). La versión "
            "que se negocia se fija al arrancar una unidad.",
            "actualiza railspec-local",
        )
    return _ok(
        "contrato",
        f"{habla} y el servidor publica las {len(TOOLS_ESPERADAS)} tools que usa. La versión que se negocia "
        "se fija al arrancar una unidad.",
    )


def _adaptadores(raiz: Path | None, home: Path | None, entorno: Mapping[str, str]) -> Comprobacion:
    deriva: list[str] = []
    presentes: list[str] = []
    worktrees = _worktrees_usuario(entorno)
    if raiz is not None:
        dir_worktrees = _dir_worktrees(raiz, entorno)
        for arnes in adaptadores.instalados(raiz):
            presentes.append(f"{arnes.value} (repositorio)")
            deriva += [
                f"{arnes.value} (repositorio): {ruta}"
                for ruta in adaptadores.verificar(raiz, arnes, dir_worktrees)
            ]
    try:
        for arnes in usuario.instalados(home, entorno):
            presentes.append(f"{arnes.value} (usuario)")
            deriva += [
                f"{arnes.value} (usuario): {ruta}"
                for ruta in usuario.verificar(arnes, worktrees, home, entorno)
            ]
    except ErrorRailspec as exc:
        return _aviso("adaptadores", f"no se pudo mirar la configuración del usuario: {exc}")
    if not presentes:
        return _aviso(
            "adaptadores",
            "ningún arnés tiene el adaptador de Railspec: el arnés no verá el servidor ni la guardia.",
            "`railspec instalar --arnes claude-code` (o opencode, codex, copilot); con "
            "`--alcance usuario` vale para todos los repositorios",
        )
    if deriva:
        return _fallo(
            "adaptadores",
            f"{', '.join(presentes)}: hay piezas que faltan o no coinciden con esta versión del proxy.",
            "repite `railspec instalar --arnes <arnés>` (con `--alcance usuario` si es el del usuario)",
            tuple(deriva),
        )
    return _ok("adaptadores", ", ".join(presentes) + ": al día con esta versión")


def _worktrees_usuario(entorno: Mapping[str, str]) -> Path | None:
    valor = entorno.get(config.ENV_WORKTREES)
    return Path(valor) if valor else None


def _dir_worktrees(raiz: Path, entorno: Mapping[str, str]) -> Path:
    return Path(entorno.get(config.ENV_WORKTREES) or config.dir_worktrees_por_defecto(raiz))


# --- Codex ----------------------------------------------------------------------------------------


def _config_codex(entorno: Mapping[str, str], home: Path | None) -> Path:
    base = entorno.get("CODEX_HOME")
    return (Path(base) if base else usuario.carpeta_personal(home) / ".codex") / "config.toml"


def _indice_hook_codex(raiz: Path) -> int | None:
    """Posición de la entrada de Railspec en ``hooks.PreToolUse`` de ``.codex/hooks.json`` (o ``None``)."""

    archivo = raiz / ".codex" / "hooks.json"
    try:
        datos = json.loads(archivo.read_text(encoding="utf-8"))
        lista = datos["hooks"]["PreToolUse"]
    except (OSError, ValueError, KeyError, TypeError):
        return None
    for i, entrada in enumerate(lista if isinstance(lista, list) else []):
        hooks = entrada.get("hooks") if isinstance(entrada, dict) else None
        if any(
            isinstance(h, dict) and str(h.get("command", "")).startswith("railspec hook ")
            for h in hooks or []
        ):
            return i
    return None


def _codex(raiz: Path, entorno: Mapping[str, str], home: Path | None) -> Comprobacion | None:
    """Confianza del hook de la guardia en Codex, o ``None`` si el repositorio no usa Codex.

    Codex no ejecuta un hook de proyecto hasta que el humano lo confía (``/hooks``) y, sin confiarlo, la
    guardia no corre y no hay ningún error: la tool pasa. La confianza queda por máquina en
    ``config.toml`` del usuario, bajo ``hooks.state."<ruta>/.codex/hooks.json:pre_tool_use:<grupo>:<hook>"``,
    ligada al contenido del hook. Aquí se comprueba que haya una entrada con ``trusted_hash``; no se puede
    saber si el hash sigue siendo el del hook actual."""

    indice = _indice_hook_codex(raiz)
    if indice is None:
        return None
    try:
        ruta_config = _config_codex(entorno, home)
        estado = tomllib.loads(ruta_config.read_text(encoding="utf-8")).get("hooks", {}).get("state", {})
    except FileNotFoundError:
        estado = {}
    except (OSError, tomllib.TOMLDecodeError, ErrorRailspec) as exc:
        return _aviso("codex", f"no se pudo leer la configuración de Codex: {exc}")
    if not isinstance(estado, dict):
        estado = {}
    # La ruta como la da el SO y con «/» (en un TOML la barra invertida escapa); en Windows, también con ella.
    claves = set()
    for r in {str(raiz), os.path.realpath(raiz), raiz.as_posix()}:
        claves.add(f"{r}/.codex/hooks.json:pre_tool_use:{indice}:0")
        if "\\" in r:
            claves.add(f"{r}\\.codex\\hooks.json:pre_tool_use:{indice}:0")
    confiado = any(isinstance(estado.get(c), dict) and estado[c].get("trusted_hash") for c in claves)
    if not confiado:
        return _fallo(
            "codex",
            "el hook de la guardia no está confiado: Codex no lo ejecuta y las ediciones pasan sin revisar.",
            "abre Codex en este repositorio, entra en /hooks, revisa el hook de Railspec y confíalo "
            "(otra vez cada vez que `railspec instalar` lo cambie)",
        )
    return _ok(
        "codex",
        "hay confianza guardada para el hook de la guardia. No se puede comprobar que su hash siga siendo el "
        "del hook actual: si `railspec instalar` lo cambió, Codex lo pide de nuevo en /hooks.",
    )


# --- indexador ------------------------------------------------------------------------------------


def _indexador() -> Comprobacion:
    diagnostico = indexador_cbm.diagnosticar()
    if diagnostico.binario is None:
        return _aviso(
            "indexador",
            f"{indexador_cbm.BINARIO} no está instalado: los snapshots viajan solo con hashes (es opcional).",
            f"`pip install {indexador_cbm.BINARIO}=={indexador_cbm.VERSION_FIJA}`",
        )
    if not diagnostico.compatible:
        return _fallo("indexador", diagnostico.problema or "versión no compatible")
    return _ok("indexador", f"{diagnostico.binario} {diagnostico.version} (la versión fijada)")


# --- worktrees ------------------------------------------------------------------------------------


def _estado_json(ruta: Path) -> dict | None:
    try:
        datos = json.loads(ruta.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return datos if isinstance(datos, dict) else None


def _worktrees(raiz: Path, repo: config.ConfigRepositorio | None, entorno: Mapping[str, str]) -> Comprobacion:
    try:
        registrados = git.worktrees(raiz)
    except ErrorRailspec as exc:
        return _fallo("worktrees", f"no se pudo listar los worktrees: {exc}")
    huerfanos: list[str] = []
    rotos: list[str] = []
    for unidad, ruta in sorted(registrados.items()):
        if not ruta.is_dir():
            huerfanos.append(
                f"{unidad}: git registra {ruta} y la carpeta no existe (se limpia con `git worktree prune`)"
            )
        elif not (ruta / ARCHIVO_ESTADO).is_file():
            huerfanos.append(
                f"{unidad}: {ruta} no tiene {ARCHIVO_ESTADO}; el proxy no puede atenderla "
                f"(si sobra, `git worktree remove {ruta}`)"
            )
        else:
            try:
                Almacen(ruta).leer()
            except (ValidationError, ValueError, OSError) as exc:
                rotos.append(f"{unidad}: el estado local de {ruta} no se puede leer ({type(exc).__name__})")
    # Carpetas de unidades de este repositorio que git ya no conoce.
    carpeta = _dir_worktrees(raiz, entorno)
    conocidas = {os.path.realpath(r) for r in registrados.values()}
    if repo is not None and carpeta.is_dir():
        for hija in sorted(carpeta.iterdir()):
            estado = _estado_json(hija / ARCHIVO_ESTADO) if hija.is_dir() else None
            if (
                estado
                and estado.get("repositorio") == repo.repositorio
                and os.path.realpath(hija) not in conocidas
            ):
                huerfanos.append(
                    f"{hija.name}: {hija} tiene estado de Railspec y git no la conoce (bórrala si sobra)"
                )
    if rotos:
        return _fallo(
            "worktrees",
            f"{len(rotos)} unidad(es) con el estado local roto.",
            "restaura el estado o quita el worktree y vuelve a importar la unidad",
            tuple(rotos + huerfanos),
        )
    if huerfanos:
        return _aviso(
            "worktrees",
            f"{len(huerfanos)} worktree(s) huérfano(s).",
            "nada se borra solo: limpia a mano lo que sobre",
            tuple(huerfanos),
        )
    return _ok("worktrees", f"{len(registrados)} unidad(es) local(es), sin huérfanos")


# --- orquestación ---------------------------------------------------------------------------------


async def diagnosticar(
    desde: Path,
    crear_cliente: FabricaCliente,
    entorno: Mapping[str, str] | None = None,
    home: Path | None = None,
    ahora: Callable[[], datetime] = ahora_utc,
    tope_servidor_s: float = TOPE_SERVIDOR_S,
) -> list[Comprobacion]:
    """Corre todas las comprobaciones; nunca lanza por un fallo que pueda contarse como tal."""

    env = os.environ if entorno is None else entorno
    resultado: list[Comprobacion] = []

    comprobacion, raiz, repo = _repositorio(desde)
    resultado.append(comprobacion)
    resultado.append(_comando())

    url = env.get(config.ENV_URL) or None
    fuente = (
        FuenteToken(
            url, env.get(config.ENV_TOKEN), AlmacenCredenciales(entorno=env, cerrar_permisos=False), ahora
        )
        if url
        else None
    )
    sesion, hay_token = _sesion_local(fuente, ahora())
    servidor: _Servidor | None = None
    if url and fuente is not None:
        cliente = crear_cliente(url, fuente)
        try:
            servidor = await _servidor(url, cliente, repo, hay_token, tope_servidor_s)
        finally:
            cerrar = getattr(cliente.transporte, "cerrar", None)
            if cerrar is not None:
                await cerrar()
        resultado.append(servidor.comprobacion)
    else:
        resultado.append(
            _fallo(
                "servidor",
                f"Falta {config.ENV_URL}: el endpoint MCP de railspec-server.",
                f"`export {config.ENV_URL}=https://<servidor>/mcp`",
            )
        )
    # Con servidor, la prueba en vivo del token se suma a lo que dice el archivo local.
    if servidor is not None and servidor.sesion is not None:
        if servidor.sesion.estado == "ok":
            sesion = replace(sesion, detalle=f"{sesion.detalle}; {servidor.sesion.detalle}")
        else:
            sesion = servidor.sesion
    resultado.append(sesion)
    if servidor is not None and servidor.contrato is not None:
        resultado.append(servidor.contrato)

    resultado.append(_adaptadores(raiz, home, env))
    if raiz is not None:
        codex = _codex(raiz, env, home)
        if codex is not None:
            resultado.append(codex)
    resultado.append(_indexador())
    if raiz is not None:
        resultado.append(_worktrees(raiz, repo, env))
    return resultado
