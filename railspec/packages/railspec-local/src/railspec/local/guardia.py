"""Guardia de las reglas de conducta: lo que los hooks del arnés consultan antes de cada tool.

Las reglas de conducta (``plantillas/reglas.md``) dicen al agente qué no hacer;
la guardia lo impide donde el arnés ofrece un hook previo a la tool. Con una
unidad en curso en el repositorio:

- Una escritura en el clon principal se rechaza: la unidad trabaja en su worktree.
- Una escritura en el worktree de una unidad solo pasa si la orden vigente la
  permite: código dentro de ``alcance.permitidos`` y fuera de ``prohibidos``;
  ``spec.md``, ``plan.md`` y ``tasks.md`` solo con la orden de redactar o
  refinar que los pide; el estado del proxy (``.railspec/``) nunca.
- Las tools que registran una decisión humana (aprobar un checkpoint, cambiar
  de modo, integrar) piden confirmación siempre.
- La shell pasa por los tres patrones del kit viejo (``sed -i``, una redirección ``>`` sobre un archivo
  existente, código inline a un intérprete): si el archivo que tocan no se podría escribir con ``Edit``,
  se rechaza igual. Cualquier otro comando lo decide el arnés (ver ``ordenes_shell``).

Sin unidades en curso la guardia no opina sobre el clon principal (el worktree
de una unidad cerrada no se edita), y lo que cae fuera del repositorio
queda a los permisos del propio arnés. La salida de emergencia es
``RAILSPEC_GUARDIA=0`` en el entorno del arnés.

Cada arnés llama a la guardia con su propio formato: ``hook_claude_code``,
``hook_codex``, ``hook_copilot`` y ``hook_opencode``. Se parecen en lo
esencial (nombre de la tool, argumentos, cwd) y difieren en cómo piden lo que
la guardia quiere: Claude Code, Copilot y OpenCode pueden pedir confirmación;
Codex no (su hook solo puede rechazar).

Se lee el estado local como JSON plano, sin validarlo contra el contrato: el
hook corre antes de cada edición y debe arrancar rápido.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from . import git, ordenes_shell
from .git import ErrorGit
from .rutas import coincide

ENV_GUARDIA = "RAILSPEC_GUARDIA"
#: Igual que ``almacen.ARCHIVO_ESTADO``; no se importa para no cargar los contratos en cada hook.
ARCHIVO_ESTADO = Path(".railspec") / "estado-local.json"
#: Artefactos del protocolo: solo los toca la orden de redactar o refinar que los pide.
ARTEFACTOS = ("spec.md", "plan.md", "tasks.md")
#: Tools del proxy que registran una decisión humana.
TOOLS_HUMANAS = ("unit_approve", "unit_set_mode", "unit_integrate")
#: La tool de shell de los cuatro arneses, sin distinguir mayúsculas: ``Bash`` (Claude Code, Codex), ``bash``.
TOOL_SHELL = "bash"
_ESCAPE = f"Si de verdad necesitas saltarte la guardia, relanza el arnés con {ENV_GUARDIA}=0."


class Decision(StrEnum):
    permitir = "allow"
    denegar = "deny"
    preguntar = "ask"


@dataclass(frozen=True)
class Veredicto:
    decision: Decision
    motivo: str = ""

    @property
    def opina(self) -> bool:
        """La guardia solo se pronuncia cuando restringe; si permite, decide el arnés."""

        return self.decision != Decision.permitir


PERMITIR = Veredicto(Decision.permitir)


@dataclass(frozen=True)
class Unidad:
    nombre: str
    worktree: Path
    estado: dict[str, Any] | None

    @property
    def activa(self) -> bool:
        if self.estado is None:
            return False
        espejo = self.estado.get("espejo_remoto") or {}
        return espejo.get("fase") != "done"

    @property
    def orden(self) -> dict[str, Any] | None:
        return (self.estado or {}).get("orden_en_curso")


def _real(ruta: Path) -> Path:
    return Path(os.path.realpath(ruta))


def _dentro(ruta: Path, carpeta: Path) -> bool:
    return ruta == carpeta or carpeta in ruta.parents


def clon_principal(desde: Path) -> Path | None:
    """Raíz del clon principal aunque ``desde`` esté en un worktree de unidad."""

    carpeta = desde if desde.is_dir() else desde.parent
    while not carpeta.exists():
        carpeta = carpeta.parent
    try:
        comun = git.texto(carpeta, "rev-parse", "--path-format=absolute", "--git-common-dir")
    except ErrorGit:
        return None
    comun_path = Path(comun)
    return _real(comun_path.parent) if comun_path.name == ".git" else None


def unidades(principal: Path) -> list[Unidad]:
    encontradas = []
    for nombre, worktree in git.worktrees(principal).items():
        ruta = worktree / ARCHIVO_ESTADO
        try:
            estado = json.loads(ruta.read_text(encoding="utf-8")) if ruta.is_file() else None
        except (OSError, json.JSONDecodeError):
            # Un estado ilegible no libera la unidad: se trata como en curso y sin orden.
            estado = {}
        encontradas.append(Unidad(nombre, _real(worktree), estado))
    return encontradas


def _rechazo(motivo: str) -> Veredicto:
    return Veredicto(Decision.denegar, f"Railspec: {motivo} {_ESCAPE}")


def apagada() -> bool:
    """La salida de emergencia (``RAILSPEC_GUARDIA=0``) está puesta en el entorno del arnés."""

    return os.environ.get(ENV_GUARDIA, "").strip().lower() in ("0", "no", "false", "off")


def evaluar_escritura(rutas: list[Path], cwd: Path) -> Veredicto:
    """Veredicto para una tool que escribe en ``rutas`` (relativas a ``cwd`` si no son absolutas)."""

    if apagada():
        return PERMITIR
    absolutas = [_real(r if r.is_absolute() else cwd / r) for r in rutas]
    principal = clon_principal(absolutas[0] if absolutas else cwd) or clon_principal(cwd)
    if principal is None:
        return PERMITIR
    todas = unidades(principal)
    activas = [u for u in todas if u.activa]
    for ruta in absolutas:
        veredicto = _evaluar_ruta(ruta, principal, todas, activas)
        if veredicto.opina:
            return veredicto
    return PERMITIR


def _evaluar_ruta(ruta: Path, principal: Path, todas: list[Unidad], activas: list[Unidad]) -> Veredicto:
    for unidad in todas:
        if _dentro(ruta, unidad.worktree):
            return _evaluar_en_unidad(ruta.relative_to(unidad.worktree).as_posix(), unidad)
    if activas and _dentro(ruta, principal):
        nombres = ", ".join(f"{u.nombre} ({u.worktree})" for u in activas)
        return _rechazo(
            f"hay una unidad en curso y el clon principal no se toca mientras tanto. "
            f"Trabaja en el worktree de la unidad: {nombres}."
        )
    return PERMITIR


def _evaluar_en_unidad(relativa: str, unidad: Unidad) -> Veredicto:
    if not unidad.activa:
        return _rechazo(f"la unidad {unidad.nombre} está cerrada; su worktree ya no se edita.")
    orden = unidad.orden
    propios = f".railspec/unidades/{unidad.nombre}/"
    if relativa.startswith(".railspec/") and not relativa.startswith(propios):
        return _rechazo(f"{relativa} es estado de Railspec; solo lo escribe el proxy.")
    if relativa.startswith(propios) and relativa.removeprefix(propios) in ARTEFACTOS:
        if orden and orden.get("tipo") in ("redactar", "refinar") and orden.get("ruta_artefacto") == relativa:
            return PERMITIR
        vigente = _describir(orden)
        return _rechazo(
            f"{relativa} es un artefacto de la unidad y solo se edita con la orden que lo pide; {vigente}."
        )
    if orden is None:
        return _rechazo(
            f"la unidad {unidad.nombre} no tiene orden vigente: pide la siguiente con unit_advance."
        )
    alcance = orden.get("alcance") or {}
    permitidos = alcance.get("permitidos") or []
    if (
        not permitidos
        or not coincide(relativa, permitidos)
        or coincide(relativa, alcance.get("prohibidos") or [])
    ):
        return _rechazo(f"{relativa} queda fuera del alcance de la orden vigente ({_describir(orden)}).")
    return PERMITIR


def _describir(orden: dict[str, Any] | None) -> str:
    if not orden:
        return "no hay orden vigente"
    tipo = orden.get("tipo", "?")
    if tipo in ("redactar", "refinar"):
        return f"la orden vigente es {tipo} {orden.get('ruta_artefacto')}"
    if tipo == "implementar":
        return f"la orden vigente es implementar el grupo {orden.get('grupo')}"
    return f"la orden vigente es {tipo}"


#: Lo que cada patrón de la guardia de Bash dice haber visto, para el motivo del rechazo.
_PATRONES_BASH = {
    1: "`sed -i` sobre un archivo",
    2: "redirección `>` que trunca un archivo existente",
    3: "código inline (`-c`, `-e` o heredoc) a un intérprete que nombra un archivo",
}


def evaluar_bash(orden: str, cwd: Path) -> Veredicto:
    """Veredicto para una orden de shell: los tres patrones del kit viejo y nada más.

    Cada patrón saca de la orden los archivos que toca, y estos se juzgan como si los escribiera
    ``Edit``: dentro del alcance de la orden vigente pasan, fuera se rechazan. A diferencia de las
    tools de edición, falla abierta: un error de la guardia no puede dejar sin shell al agente, y
    ningún análisis de una orden es fiable del todo (``tee``, ``cp``, ``find -exec``, un script
    versionado escriben sin casar con ningún patrón).
    """

    if apagada():
        return PERMITIR
    try:
        encontrados = ordenes_shell.hallazgos(orden, cwd)
        if not encontrados:
            return PERMITIR
        repos: dict[Path, tuple[Path, list[Unidad], list[Unidad]] | None] = {}
        for hallazgo in encontrados:
            ruta = Path(os.path.expanduser(hallazgo.texto))
            absoluta = _real(ruta if ruta.is_absolute() else hallazgo.carpeta / ruta)
            if hallazgo.carpeta not in repos:
                principal = clon_principal(hallazgo.carpeta)
                if principal is None:
                    repos[hallazgo.carpeta] = None
                else:
                    todas = unidades(principal)
                    repos[hallazgo.carpeta] = (principal, todas, [u for u in todas if u.activa])
            repo = repos[hallazgo.carpeta]
            if repo is None:
                continue
            veredicto = _evaluar_ruta(absoluta, *repo)
            if veredicto.opina:
                detalle = veredicto.motivo.removeprefix("Railspec: ").removesuffix(f" {_ESCAPE}")
                return _rechazo(
                    f"regla {hallazgo.regla} de la guardia de Bash ({_PATRONES_BASH[hallazgo.regla]}) sobre "
                    f"`{hallazgo.texto}`: {detalle} Edita con la tool de edición del arnés, que sí respeta "
                    "el alcance de la orden."
                )
    except Exception as exc:  # noqa: BLE001 - la guardia de Bash falla abierta, con aviso
        aviso = f"Railspec: la guardia de Bash no pudo leer la orden ({exc}); se deja decidir al arnés."
        print(aviso, file=sys.stderr)
    return PERMITIR


def evaluar_tool_humana(tool: str) -> Veredicto:
    return Veredicto(
        Decision.preguntar,
        f"Railspec: `{tool}` registra una decisión humana. Confírmala tú; el agente no decide por ti.",
    )


# --- traducción de la entrada de cada arnés -----------------------------------------------

_PATCH = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+)$", re.MULTILINE)


def rutas_de_patch(texto: str) -> list[str]:
    return [m.group(1).strip() for m in _PATCH.finditer(texto)]


def _ruta_tool(tool: str, args: dict[str, Any] | str) -> list[str] | None:
    """Rutas que escribe una tool de edición; ``None`` si la tool no escribe archivos.

    ``args`` es un objeto, salvo el parche de ``apply_patch`` en Copilot, que llega como texto.
    """

    nombre = tool.lower()
    if nombre in ("write", "edit", "multiedit", "notebookedit", "create"):
        if not isinstance(args, dict):
            return []
        # `path` es el de `create` y `edit` de Copilot.
        ruta = args.get("file_path") or args.get("filePath") or args.get("notebook_path") or args.get("path")
        return [ruta] if isinstance(ruta, str) and ruta else []
    if nombre in ("patch", "apply_patch"):
        # Codex: `{"command": parche}`; OpenCode: `{"patchText": parche}`; Copilot: el parche solo.
        if isinstance(args, str):
            texto: Any = args
        else:
            texto = args.get("patchText") or args.get("patch") or args.get("input") or args.get("command")
        return rutas_de_patch(texto) if isinstance(texto, str) else []
    return None


def _humana(tool: str) -> str | None:
    """La tool del proxy que registra una decisión humana, con el nombre que le da cada arnés."""

    # Claude Code y Codex: `mcp__railspec__unit_approve`; OpenCode: `railspec_unit_approve`;
    # Copilot: `railspec-unit_approve`.
    for humana in TOOLS_HUMANAS:
        if tool in (f"mcp__railspec__{humana}", f"railspec_{humana}", f"railspec-{humana}"):
            return humana
    return None


def evaluar_tool(tool: str, args: dict[str, Any] | str, cwd: Path) -> Veredicto:
    """Veredicto para una llamada a tool, con el nombre que le da el arnés."""

    humana = _humana(tool)
    if humana is not None:
        return evaluar_tool_humana(humana)
    if tool.lower() == TOOL_SHELL:
        orden = args.get("command") if isinstance(args, dict) else None
        return evaluar_bash(orden, cwd) if isinstance(orden, str) and orden.strip() else PERMITIR
    rutas = _ruta_tool(tool, args)
    if rutas is None:
        return PERMITIR
    if not rutas:
        return _rechazo(f"no pude leer qué archivo escribe `{tool}`.")
    return evaluar_escritura([Path(r) for r in rutas], cwd)


def _salida_pretooluse(veredicto: Veredicto) -> dict[str, Any]:
    """Formato de ``PreToolUse`` de Claude Code; Codex lo acepta igual (solo con ``deny``)."""

    return {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": veredicto.decision.value,
            "permissionDecisionReason": veredicto.motivo,
        }
    }


def hook_claude_code(entrada: dict[str, Any]) -> dict[str, Any] | None:
    """Respuesta a un ``PreToolUse`` de Claude Code; ``None`` deja decidir al arnés."""

    cwd = Path(entrada.get("cwd") or os.getcwd())
    veredicto = evaluar_tool(entrada.get("tool_name", ""), entrada.get("tool_input") or {}, cwd)
    return _salida_pretooluse(veredicto) if veredicto.opina else None


#: Lo que Codex informa como ``permission_mode`` sin aprobaciones: ``approval_policy = "never"``, que es
#: también ``--dangerously-bypass-approvals-and-sandbox``. Con cualquier otra política dice ``default``.
SIN_APROBACIONES = "bypassPermissions"


def hook_codex(entrada: dict[str, Any]) -> dict[str, Any] | None:
    """Respuesta a un ``PreToolUse`` de Codex; ``None`` deja decidir al arnés.

    El hook de Codex solo entiende ``deny``: un ``ask`` cuenta como error del hook y la tool corre.
    Las tools que registran una decisión humana ya piden confirmación por su ``approval_mode = "prompt"``
    (``.codex/config.toml``), así que el hook no opina… salvo sin aprobaciones, donde nadie puede
    confirmar y lo que se queda sin confirmar se rechaza.
    """

    cwd = Path(entrada.get("cwd") or os.getcwd())
    veredicto = evaluar_tool(entrada.get("tool_name", ""), entrada.get("tool_input") or {}, cwd)
    if veredicto.decision == Decision.preguntar:
        if apagada() or entrada.get("permission_mode") != SIN_APROBACIONES:
            return None
        veredicto = _rechazo(
            f"{veredicto.motivo.removeprefix('Railspec: ')} Codex no puede pedirte confirmación sin "
            "aprobaciones (approval_policy = never o --dangerously-bypass-approvals-and-sandbox): "
            "relanza Codex con aprobaciones activas."
        )
    return _salida_pretooluse(veredicto) if veredicto.opina else None


def _args_copilot(tool: str, args: Any) -> dict[str, Any] | str:
    """``toolArgs`` de Copilot: un objeto (en versiones anteriores, texto JSON); el parche, texto plano."""

    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return args if tool.lower() in ("apply_patch", "patch") else {}
    if isinstance(args, dict):
        return args
    return args if isinstance(args, str) and tool.lower() in ("apply_patch", "patch") else {}


def _salida_copilot(veredicto: Veredicto) -> dict[str, Any]:
    return {"permissionDecision": veredicto.decision.value, "permissionDecisionReason": veredicto.motivo}


def hook_copilot(entrada: dict[str, Any]) -> dict[str, Any] | None:
    """Respuesta a un ``preToolUse`` de GitHub Copilot CLI; ``None`` deja decidir al arnés.

    Copilot admite ``ask`` (en modo interactivo pregunta; en ``-p`` no hay a quién y rechaza).
    """

    cwd = Path(entrada.get("cwd") or os.getcwd())
    tool = entrada.get("toolName", "")
    veredicto = evaluar_tool(tool, _args_copilot(tool, entrada.get("toolArgs")), cwd)
    return _salida_copilot(veredicto) if veredicto.opina else None


def dir_worktrees(desde: Path) -> Path | None:
    """Carpeta de los worktrees de unidades del repositorio que contiene ``desde``."""

    principal = clon_principal(desde)
    if principal is None:
        return None
    from .config import ENV_WORKTREES, dir_worktrees_por_defecto

    return _real(Path(os.environ.get(ENV_WORKTREES) or dir_worktrees_por_defecto(principal)))


def hook_opencode(entrada: dict[str, Any]) -> dict[str, Any]:
    """Respuesta al plugin de OpenCode.

    - ``{"evento": "tool", "tool", "args"}`` (``tool.execute.before``) → ``{"decision", "motivo"}``.
    - ``{"evento": "config"}`` → ``{"worktrees"}``, para abrir la carpeta en ``external_directory``.

    Todas llevan ``directory``, la carpeta del proyecto de OpenCode.
    """

    cwd = Path(entrada.get("directory") or os.getcwd())
    evento = entrada.get("evento", "tool")
    if evento == "config":
        carpeta = dir_worktrees(cwd)
        return {"worktrees": str(carpeta) if carpeta else None}
    veredicto = evaluar_tool(entrada.get("tool", ""), entrada.get("args") or {}, cwd)
    return {"decision": veredicto.decision.value, "motivo": veredicto.motivo}


HOOKS = {
    "claude-code": hook_claude_code,
    "codex": hook_codex,
    "copilot": hook_copilot,
    "opencode": hook_opencode,
}


def denegar(arnes: str, motivo: str) -> dict[str, Any]:
    """Rechazo en el formato del arnés: lo que ``railspec hook`` responde si la guardia falla."""

    veredicto = Veredicto(Decision.denegar, motivo)
    if arnes == "opencode":
        return {"decision": veredicto.decision.value, "motivo": motivo}
    return _salida_copilot(veredicto) if arnes == "copilot" else _salida_pretooluse(veredicto)
