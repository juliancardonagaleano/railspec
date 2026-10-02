"""Convivencia del kit SDD con Railspec en un mismo repositorio.

Instala el kit y `railspec instalar` en repositorios temporales, en los dos órdenes,
y comprueba que ninguno pisa al otro: lo que cada uno escribe sobrevive a que el otro
(re)instale, lo ajeno a los dos también, y ni `verificar` del kit ni `railspec instalar
--verificar` ven deriva por lo que puso el otro. La guía `railspec/docs/migracion-kit.md`
describe estos mismos comportamientos.

Necesita `railspec-local` instalado (`pip install -e railspec/packages/railspec-local`);
sin él, el módulo se salta. Nunca instala sobre este repositorio.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import io
import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(REPO_ROOT))

railspec_cli = pytest.importorskip(
    "railspec.local.cli", reason="railspec-local no está instalado: no hay con qué probar la convivencia"
)

from installer.convivencia import digest_de_instalacion  # noqa: E402
from installer.installer import run_install  # noqa: E402
from installer.verifier import run_verify  # noqa: E402

ARNESES = ["claude-code", "opencode", "codex", "copilot"]
SCRIPT_DRIFT = REPO_ROOT / "installer" / "scripts" / "check_install_drift.py"

#: Lo que el repositorio ya tenía antes de instalar nada, ajeno al kit y a Railspec.
PROSA_PROPIA = "# Mi repositorio\n\nReglas propias del equipo.\n"
MCP_PROPIO = {"mcpServers": {"otro": {"type": "stdio", "command": "otro-mcp"}}}
AJUSTES_PROPIOS = {
    "permissions": {"allow": ["Bash(ls:*)"]},
    "hooks": {"PreToolUse": [{"matcher": "Bash", "hooks": [{"type": "command", "command": "echo ajeno"}]}]},
}
OPENCODE_PROPIO = {"theme": "tokyonight", "mcp": {"otro": {"type": "local", "command": ["otro-mcp"]}}}


def _git_init(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", "-q", str(path)], check=True)


def _escribir_json(ruta: Path, datos: dict) -> None:
    ruta.parent.mkdir(parents=True, exist_ok=True)
    ruta.write_text(json.dumps(datos, indent=2) + "\n", encoding="utf-8")


def _leer_json(ruta: Path) -> dict:
    return json.loads(ruta.read_text(encoding="utf-8"))


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """Repositorio con configuración propia de cada archivo que el kit y Railspec comparten."""
    destino = tmp_path / "repo"
    _git_init(destino)
    (destino / "CLAUDE.md").write_text(PROSA_PROPIA, encoding="utf-8")
    _escribir_json(destino / ".mcp.json", MCP_PROPIO)
    _escribir_json(destino / ".claude" / "settings.json", AJUSTES_PROPIOS)
    _escribir_json(destino / "opencode.jsonc", OPENCODE_PROPIO)
    return destino


def _silencio():
    return contextlib.redirect_stdout(io.StringIO())


def instalar_kit(destino: Path) -> int:
    with _silencio(), contextlib.redirect_stderr(io.StringIO()):
        return run_install(destino)


def verificar_kit(destino: Path) -> tuple[int, str]:
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        return run_verify(destino), salida.getvalue()


def instalar_railspec(destino: Path, arneses: list[str] = ARNESES) -> None:
    argv = ["--repo", str(destino), "instalar", "--org", "acme", "--workspace", "ws", "--repositorio", "r"]
    for arnes in arneses:
        argv += ["--arnes", arnes]
    with _silencio():
        assert railspec_cli.main(argv) == 0


def deriva_railspec(destino: Path, arneses: list[str] = ARNESES) -> dict[str, list[str]]:
    argv = ["--repo", str(destino), "instalar", "--verificar"]
    for arnes in arneses:
        argv += ["--arnes", arnes]
    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        railspec_cli.main(argv)
    return json.loads(salida.getvalue())["deriva"]


def sin_deriva(destino: Path, arneses: list[str] = ARNESES) -> bool:
    return not any(deriva_railspec(destino, arneses).values())


def comandos_de_hooks(ajustes: dict) -> list[str]:
    return [h["command"] for e in ajustes["hooks"]["PreToolUse"] for h in e["hooks"]]


def afirmar_estado_final(destino: Path) -> None:
    """Todo lo del kit, todo lo de Railspec y todo lo ajeno a los dos, a la vez."""
    mcp = _leer_json(destino / ".mcp.json")["mcpServers"]
    assert mcp["pce-mcp"]["_sdd_kit"] is True, "falta la entrada del kit en .mcp.json"
    assert mcp["railspec"]["command"] == "railspec", "falta la entrada de Railspec en .mcp.json"
    assert mcp["otro"] == MCP_PROPIO["mcpServers"]["otro"], "se perdió un servidor ajeno en .mcp.json"

    ajustes = _leer_json(destino / ".claude" / "settings.json")
    comandos = comandos_de_hooks(ajustes)
    assert any("guard_generated_paths.py" in c for c in comandos), "falta el hook del kit"
    assert "railspec hook claude-code" in comandos, "falta el hook de Railspec"
    assert "echo ajeno" in comandos, "se perdió un hook ajeno"
    assert "Bash(ls:*)" in ajustes["permissions"]["allow"], "se perdió un permiso ajeno"
    assert "mcp__railspec__unit_start" in ajustes["permissions"]["allow"], "faltan los permisos de Railspec"

    opencode = _leer_json(destino / "opencode.jsonc")
    assert opencode["mcp"]["pce-mcp"]["_sdd_kit"] is True, "falta la entrada del kit en opencode.jsonc"
    assert opencode["mcp"]["railspec"]["command"] == ["railspec", "mcp"]
    assert opencode["mcp"]["otro"] == OPENCODE_PROPIO["mcp"]["otro"]
    assert opencode["theme"] == "tokyonight"
    assert any(k.startswith("sdd-") for k in opencode["agent"]), "faltan los agentes del kit en opencode.jsonc"

    agentes = (destino / "AGENTS.md").read_text(encoding="utf-8")
    assert "Contrato Agéntico del kit" in agentes, "falta la prosa del kit en AGENTS.md"
    assert "railspec:inicio" in agentes and "railspec:fin" in agentes, "falta el bloque de Railspec en AGENTS.md"
    claude = (destino / "CLAUDE.md").read_text(encoding="utf-8")
    assert "Reglas propias del equipo." in claude and "railspec:inicio" in claude

    for ruta in (
        ".claude/commands/sdd.md",
        ".claude/skills/sdd-orquestar/SKILL.md",
        ".claude/commands/railspec.md",
        ".claude/skills/railspec-bucle/SKILL.md",
        ".agents/skills/sdd-orquestar/SKILL.md",
        ".agents/skills/railspec/SKILL.md",
        ".opencode/commands/railspec.md",
        ".github/hooks/railspec.json",
    ):
        assert (destino / ruta).is_file(), f"falta {ruta}"


# --- los dos órdenes ---------------------------------------------------------------------


def test_kit_y_luego_railspec_no_se_pisan(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)

    afirmar_estado_final(repo)
    assert sin_deriva(repo)
    codigo, salida = verificar_kit(repo)
    assert codigo == 0, f"el kit ve deriva por lo que puso Railspec:\n{salida}"


def test_railspec_y_luego_kit_no_se_pisan(repo: Path) -> None:
    instalar_railspec(repo)
    assert instalar_kit(repo) == 0

    afirmar_estado_final(repo)
    assert sin_deriva(repo), deriva_railspec(repo)
    codigo, salida = verificar_kit(repo)
    assert codigo == 0, salida


def test_reinstalar_el_kit_conserva_lo_de_railspec(repo: Path) -> None:
    """Actualizar el kit (`--install` otra vez) no borra nada de Railspec, y es idempotente."""
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)

    assert instalar_kit(repo) == 0

    afirmar_estado_final(repo)
    assert sin_deriva(repo)
    assert verificar_kit(repo)[0] == 0
    # La fusión del kit deja sus entradas al final de cada lista: tras la primera reinstalación,
    # las siguientes no cambian ni un byte.
    estable = {r: (repo / r).read_bytes() for r in (".mcp.json", ".claude/settings.json", "opencode.jsonc", "AGENTS.md")}
    assert instalar_kit(repo) == 0
    assert {r: (repo / r).read_bytes() for r in estable} == estable, "reinstalar el kit cambió archivos compartidos"


def test_reinstalar_railspec_conserva_lo_del_kit(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)
    antes = {r: (repo / r).read_bytes() for r in (".mcp.json", ".claude/settings.json", "opencode.jsonc", "AGENTS.md")}

    instalar_railspec(repo)

    assert {r: (repo / r).read_bytes() for r in antes} == antes, "reinstalar Railspec cambió archivos compartidos"
    assert verificar_kit(repo)[0] == 0


def test_el_kit_no_poda_el_comando_ni_la_skill_de_railspec_en_claude_code(repo: Path) -> None:
    """Sin Codex en el repo, `.agents/skills/` no tiene copia de las skills de Railspec: el espejo
    `.claude/` del kit no puede tomarlas por huérfanas."""
    instalar_railspec(repo, ["claude-code"])
    assert instalar_kit(repo) == 0
    assert instalar_kit(repo) == 0

    assert (repo / ".claude" / "commands" / "railspec.md").is_file()
    assert (repo / ".claude" / "skills" / "railspec-bucle" / "SKILL.md").is_file()
    assert sin_deriva(repo, ["claude-code"])


def test_el_kit_no_espeja_las_skills_de_codex_y_copilot_de_railspec(repo: Path) -> None:
    """`.agents/skills/railspec*` es de Codex: el kit no las copia a `.claude/skills/`."""
    instalar_railspec(repo, ["codex"])
    assert instalar_kit(repo) == 0

    assert not (repo / ".claude" / "skills" / "railspec").exists()
    assert not (repo / ".claude" / "skills" / "railspec-bucle").exists()
    assert (repo / ".claude" / "skills" / "sdd-orquestar" / "SKILL.md").is_file()


# --- el gate de pre-push del kit ---------------------------------------------------------------


def _confirmar(destino: Path, mensaje: str) -> str:
    subprocess.run(["git", "-C", str(destino), "add", "-A"], check=True)
    subprocess.run(
        ["git", "-C", str(destino), "-c", "user.name=t", "-c", "user.email=t@t", "commit", "-q", "--no-verify", "-m", mensaje],
        check=True,
    )
    return subprocess.run(
        ["git", "-C", str(destino), "rev-parse", "HEAD"], capture_output=True, text=True, check=True
    ).stdout.strip()


@pytest.mark.parametrize(("arnes", "pasa"), [("claude-code", True), ("opencode", True), ("copilot", True), ("codex", False)])
def test_el_gate_de_prepush_del_kit_solo_rechaza_el_adaptador_de_codex(repo: Path, arnes: str, pasa: bool) -> None:
    """La guía avisa de esto: `.agents/skills/` es ruta del protocolo del kit y Codex escribe ahí."""
    assert instalar_kit(repo) == 0
    base = _confirmar(repo, "kit")
    instalar_railspec(repo, [arnes])
    _confirmar(repo, "railspec")

    resultado = subprocess.run(
        ["bash", ".spec/scripts/pre-push-gate.sh", "--check", base, "HEAD"], cwd=repo, capture_output=True, text=True
    )

    if pasa:
        assert resultado.returncode == 0, resultado.stdout + resultado.stderr
        assert "sin cambios a rutas del protocolo" in resultado.stdout
    else:
        assert resultado.returncode != 0 and "BLOCK" in resultado.stderr


# --- desinstalar Railspec -----------------------------------------------------------------


def test_desinstalar_railspec_deja_el_kit_como_estaba(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    kit_solo = {r: (repo / r).read_bytes() for r in ("AGENTS.md", ".claude/commands/sdd.md")}
    instalar_railspec(repo)

    with _silencio():
        assert railspec_cli.main(["--repo", str(repo), "desinstalar"]) == 0

    assert verificar_kit(repo)[0] == 0
    assert {r: (repo / r).read_bytes() for r in kit_solo} == kit_solo
    mcp = _leer_json(repo / ".mcp.json")["mcpServers"]
    assert "railspec" not in mcp and mcp["pce-mcp"]["_sdd_kit"] is True and "otro" in mcp
    assert "railspec hook claude-code" not in comandos_de_hooks(_leer_json(repo / ".claude" / "settings.json"))
    assert not (repo / ".claude" / "commands" / "railspec.md").exists()


# --- quitar el kit, como dice la guía ---------------------------------------------------------


def _script_de_desinstalacion() -> str:
    guia = (REPO_ROOT / "railspec" / "docs" / "migracion-kit.md").read_text(encoding="utf-8")
    encontrado = re.search(r"<!-- prueba:desinstalar-kit -->\n```python\n(.*?)\n```", guia, re.DOTALL)
    assert encontrado, "la guía ya no trae el procedimiento de desinstalación marcado"
    return encontrado.group(1)


def test_quitar_el_kit_con_el_procedimiento_de_la_guia_deja_a_railspec_como_estaba(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)
    propio = repo / ".spec" / "perfiles.yaml"
    propio.write_text(propio.read_text(encoding="utf-8") + "# cambio mío\n", encoding="utf-8")
    hook = Path(subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, check=True
    ).stdout.strip())
    assert (hook / "hooks" / "pre-push").is_file() and (hook / "sdd-kit-install-record.yaml").is_file()

    resultado = subprocess.run(
        [sys.executable, "-"], input=_script_de_desinstalacion(), cwd=repo, capture_output=True, text=True
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    assert ".spec/perfiles.yaml" in resultado.stdout, "tenía cambios míos y debía conservarse"
    assert propio.is_file()
    for ruta in (
        ".spec/scripts", ".spec/_plantillas", ".agents/skills/sdd-orquestar", ".agents/agents", ".agents/commands",
        ".claude/agents", ".claude/commands/sdd.md", ".claude/skills/sdd-orquestar", ".claude/output-styles",
        "scripts", "installer",
    ):
        assert not (repo / ruta).exists(), f"quedó {ruta}"
    assert not (hook / "hooks" / "pre-push").exists() and not (hook / "sdd-kit-install-record.yaml").exists()

    # Railspec, y lo ajeno a los dos, siguen en pie.
    assert sin_deriva(repo)
    mcp = _leer_json(repo / ".mcp.json")["mcpServers"]
    assert set(mcp) == {"otro", "railspec"}
    ajustes = _leer_json(repo / ".claude" / "settings.json")
    assert set(comandos_de_hooks(ajustes)) == {"echo ajeno", "railspec hook claude-code"}
    assert "Bash(ls:*)" in ajustes["permissions"]["allow"]
    opencode = _leer_json(repo / "opencode.jsonc")
    assert set(opencode["mcp"]) == {"otro", "railspec"} and "agent" not in opencode and opencode["theme"] == "tokyonight"
    agentes = (repo / "AGENTS.md").read_text(encoding="utf-8")
    assert agentes.startswith("<!-- railspec:inicio") and "Contrato Agéntico" not in agentes
    assert (repo / ".claude" / "commands" / "railspec.md").is_file()
    assert (repo / ".claude" / "skills" / "railspec-bucle" / "SKILL.md").is_file()


def test_quitar_el_kit_sin_railspec_borra_los_json_y_agents_que_solo_eran_del_kit(tmp_path: Path) -> None:
    destino = tmp_path / "solo-kit"
    _git_init(destino)
    assert instalar_kit(destino) == 0

    resultado = subprocess.run(
        [sys.executable, "-"], input=_script_de_desinstalacion(), cwd=destino, capture_output=True, text=True
    )

    assert resultado.returncode == 0, resultado.stdout + resultado.stderr
    for ruta in (".mcp.json", "opencode.jsonc", ".claude/settings.json", "AGENTS.md", ".agents", ".claude", "scripts"):
        assert not (destino / ruta).exists(), f"quedó {ruta}"


# --- la deriva real del kit se sigue viendo ------------------------------------------------


def test_editar_la_parte_del_kit_sigue_siendo_deriva(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)

    mcp = _leer_json(repo / ".mcp.json")
    mcp["mcpServers"]["pce-mcp"]["command"] = "otro-comando"
    _escribir_json(repo / ".mcp.json", mcp)
    agentes = repo / "AGENTS.md"
    agentes.write_text(agentes.read_text(encoding="utf-8").replace("Contrato Agéntico", "Contrato editado", 1))

    codigo, salida = verificar_kit(repo)
    assert codigo == 1
    assert "drift: .mcp.json" in salida and "drift: AGENTS.md" in salida


def test_borrar_la_entrada_del_kit_sigue_siendo_deriva(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)

    mcp = _leer_json(repo / ".mcp.json")
    del mcp["mcpServers"]["pce-mcp"]
    _escribir_json(repo / ".mcp.json", mcp)

    codigo, salida = verificar_kit(repo)
    assert codigo == 1 and "drift: .mcp.json" in salida


def test_un_registro_anterior_con_el_digest_del_archivo_entero_sigue_valiendo(repo: Path) -> None:
    """Los registros de instalación previos guardan el sha256 del archivo entero."""
    assert instalar_kit(repo) == 0
    registro = Path(subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--absolute-git-dir"], capture_output=True, text=True, check=True
    ).stdout.strip()) / "sdd-kit-install-record.yaml"
    lineas = []
    for linea in registro.read_text(encoding="utf-8").splitlines():
        for ruta in (".mcp.json", "opencode.jsonc", ".claude/settings.json", "AGENTS.md"):
            if linea.startswith(f'  "{ruta}":'):
                linea = f'  "{ruta}": "{hashlib.sha256((repo / ruta).read_bytes()).hexdigest()}"'
        lineas.append(linea)
    registro.write_text("\n".join(lineas) + "\n", encoding="utf-8")

    assert verificar_kit(repo)[0] == 0


# --- un JSON ilegible no se pisa ------------------------------------------------------------


def test_un_json_compartido_ilegible_aborta_sin_escribir(repo: Path) -> None:
    (repo / ".mcp.json").write_text("{ esto no es json", encoding="utf-8")

    assert instalar_kit(repo) == 2

    assert (repo / ".mcp.json").read_text(encoding="utf-8") == "{ esto no es json"
    assert not (repo / ".spec").exists(), "el instalador escribió antes de abortar"


# --- el script de pre-push y el instalador miden lo mismo ----------------------------------


def _cargar_script_drift():
    spec = importlib.util.spec_from_file_location("check_install_drift", SCRIPT_DRIFT)
    modulo = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(modulo)
    return modulo


def test_el_script_de_deriva_mide_lo_mismo_que_el_instalador(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)
    script = _cargar_script_drift()

    for ruta in (".mcp.json", "opencode.jsonc", ".claude/settings.json", "AGENTS.md", "CLAUDE.md"):
        datos = (repo / ruta).read_bytes()
        propio = hashlib.sha256(script._contenido_del_kit(ruta, datos)).hexdigest()
        assert propio == digest_de_instalacion(ruta, datos), ruta


def test_el_script_de_deriva_acepta_lo_de_railspec_y_rechaza_la_deriva_real(repo: Path) -> None:
    assert instalar_kit(repo) == 0
    instalar_railspec(repo)

    def correr() -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(repo / "installer" / "scripts" / "check_install_drift.py")],
            cwd=repo, capture_output=True, text=True,
        )

    assert correr().returncode == 0, correr().stdout
    mcp = _leer_json(repo / ".mcp.json")
    mcp["mcpServers"]["pce-mcp"]["command"] = "otro-comando"
    _escribir_json(repo / ".mcp.json", mcp)
    resultado = correr()
    assert resultado.returncode == 1 and ".mcp.json" in resultado.stdout


# --- importar unidades del kit --------------------------------------------------------------


def _unidad_del_kit(carpeta: Path, modo: str) -> Path:
    carpeta.mkdir(parents=True)
    (carpeta / "_estado.yaml").write_text(
        f"id: '0042'\ntitulo: Cola de reintentos\nfase: spec\nmodo: {modo}\nriesgo: medio\n", encoding="utf-8"
    )
    (carpeta / "spec.md").write_text("# Spec\n\n## Problema\n\nLos reintentos se pierden.\n", encoding="utf-8")
    return carpeta


@pytest.mark.parametrize("modo", ["supervisado", "desatendido"])
def test_importar_una_unidad_supervisada_o_desatendida_entra_interactiva(repo: Path, tmp_path: Path, modo: str) -> None:
    unidad = _unidad_del_kit(repo / ".spec" / "units" / "0042-cola", modo)
    destino = tmp_path / "paquetes"

    salida = io.StringIO()
    with contextlib.redirect_stdout(salida):
        codigo = railspec_cli.main(["--repo", str(repo), "importar", str(unidad), "--solo-convertir", str(destino)])

    assert codigo == 0
    paquete = json.loads((destino / "0042" / "unidad.json").read_text(encoding="utf-8"))
    assert "modo" not in paquete, "el modo con mandato no viaja: la unidad entra interactiva"
    assert paquete["origen"]["tipo"] == "sdd-kit" and paquete["riesgo"] == "medio"
    assert any(modo in aviso for aviso in json.loads(salida.getvalue())["paquetes"][0]["avisos"])
