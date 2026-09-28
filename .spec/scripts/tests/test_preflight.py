"""`preflight.py` — CA-01 … CA-06, plus the parity of `codes_from_output` (0114, G1).

Every scenario runs the preflight **as a subprocess** against a temporary git
repository seeded inline: the contract under test is the script's, not a
function's, and the zero-writes promise only means something when something
real could have been written.

The MCP row is exercised against the stdio stub (`stub_knowledge_server.py`) through
the same configuration mechanism the pilot already uses, so the four BLOCK causes are
induced, not faked. Every mandate seed is inline (no fixture tree) — see
`CLEAN_MANDATE_TEMPLATE` / `EMPTY_APPROVAL_MANDATE_TEXT` below.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
SCRIPT = SCRIPTS / "preflight.py"
VALIDATOR = SCRIPTS / "validate_mandate.py"
COMMON_SH = SCRIPTS / "_common.sh"

#: Short budget so the `timeout` case does not slow the suite down. The real default
#: (20 s, S-11) is documented in the script header and checked below.
SHORT_TIMEOUT = "3"

sys.path.insert(0, str(SCRIPTS))

import instance_lock  # noqa: E402
import preflight  # noqa: E402
import validate_mandate as vm  # noqa: E402
from _common import codes_from_output  # noqa: E402

BRANCH = "fixture"


def clean_env(extra: dict[str, str] | None = None) -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    env.pop("IARK_PREFLIGHT_MCP_CONFIG", None)
    if extra:
        env.update(extra)
    return env


def run_git(repo: Path, *args: str) -> str:
    completed = subprocess.run(["git", "-C", str(repo), *args],
                               capture_output=True, text=True, check=True)
    return completed.stdout.strip()


def new_repo(prefix: str) -> Path:
    repo = Path(tempfile.mkdtemp(prefix=prefix))
    subprocess.run(["git", "-c", f"init.defaultBranch={BRANCH}", "init", "-q",
                    str(repo)], check=True, capture_output=True)
    run_git(repo, "config", "user.email", "fixture@example.invalid")
    run_git(repo, "config", "user.name", "Fixture")
    run_git(repo, "config", "commit.gpgsign", "false")
    return repo


def run_preflight(args: list[str], env: dict[str, str] | None = None
                  ) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False,
                          env=env or clean_env())


#: No `set -e` here on purpose: `comparar_esperado` captures the validator's exit code
#: with a command substitution, and `set -e` would abort the shell on the very
#: `exit 1` the helper exists to read.
SOURCE_AND_CALL = 'source "$1"; "$2" "$3" "$4"'


def _bash_helper(name: str, first: Path, second: Path
                 ) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["bash", "-c", SOURCE_AND_CALL, "_", str(COMMON_SH), name,
         str(first), str(second)],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        env=clean_env({"VALIDATOR": str(VALIDATOR)}))


def verificar_aserciones(evidence: Path, assertions: Path
                         ) -> subprocess.CompletedProcess:
    return _bash_helper("verificar_aserciones", evidence, assertions)


def comparar_esperado(unit_dir: Path, expected: Path) -> subprocess.CompletedProcess:
    return _bash_helper("comparar_esperado", unit_dir, expected)


def row_of(stdout: str, name: str) -> str:
    for line in stdout.splitlines():
        if line.startswith(f"{name}: "):
            return line
    raise AssertionError(f"la tabla no trae la fila `{name}`:\n{stdout}")


# ======================================================================================
# Inline mandate seeds (no fixture tree). `CLEAN_MANDATE_TEMPLATE` validates with
# zero codes end to end; its `## Aprobación` hash only ever hashes `## Objetivo y
# criterio de salida` + `## Unidad amparada` + `## Delegaciones`, so the same
# precomputed hash stays valid across every variant below that leaves those three
# anchors untouched (`## Instancia en curso`, `## Estado`, `## Aprobación` itself
# vary freely without invalidating it).
# ======================================================================================

_MANDATE_TEMPLATE = """# Mandato supervisado — fixture de preflight

## Objetivo y criterio de salida

Servir de mandato de prueba para `preflight.py`.

## Unidad amparada

- unidad: `9410-fixture-preflight`
- directorio: (fixture inline de este test)
- fase de la unidad: `implement`
- avance de la unidad: `en-progreso`

## Mandato

### 2026-09-17T08:00Z

- autor: Julian Cardona Galeano
- lanza: Julian Cardona Galeano
- inicio: 2026-09-17T08:00Z
- fin: 2026-12-31T23:59Z

## Delegaciones

### Pre-decididas

| id | decisión | pre-decisión | impacto |
|---|---|---|---|
| PD-1 | nombre del archivo de mandato de la unidad | `mandato.md` | ninguno fuera de la unidad |

### Con criterio

### Reservadas

## Condiciones de parada

Las condiciones indelegables son las de `.spec/PARADAS-SUPERVISADO.md`, su única fuente.

## Paralelismo

- carriles: sdd
- tope-worktrees: 2
- dueno-stack-vivo: Julian Cardona Galeano
- presupuesto-mcp: 40 consultas a `pce-mcp` para toda la ventana del mandato
- conducta-presupuesto-agotado: parar — condición «MCP ausente o sin presupuesto» de la lista de paradas

## Registro de decisiones

## Paradas

Sin entradas.

## Punto de retoma

Sin entradas: nunca se paró.

## Estado

- estado: {estado}

## Instancia en curso

{instancia}

## Aprobación

{aprobacion}

## Revisión posterior

Vacía.
"""

_CLEAN_APROBACION = """\
### 2026-09-17T09:00Z

- quien: Julian Cardona Galeano
- hash: {hash}
- fase: implement
- artefactos: spec+plan
"""

EMPTY_INSTANCIA = "Sin instancia."
BUSY_INSTANCIA = (
    "- sesion: https://claude.ai/code/session_00-fixture-otra-instancia\n"
    "- lanzador: Julian Cardona Galeano\n"
    "- inicio: 2026-09-17T13:55Z\n"
    "- pid: 999999"
)


def _base_hash() -> str:
    with tempfile.TemporaryDirectory() as tmp_dir:
        probe = Path(tmp_dir) / "mandato.md"
        probe.write_text(
            _MANDATE_TEMPLATE.format(estado="aprobado", instancia=EMPTY_INSTANCIA,
                                     aprobacion=_CLEAN_APROBACION.format(hash="0" * 64)),
            encoding="utf-8",
        )
        return vm.Mandate(probe, "unidad", "mandato.md", Path(tmp_dir)).approval_hash()


_HASH = _base_hash()

#: Validates end to end with zero codes.
CLEAN_MANDATE_TEXT = _MANDATE_TEMPLATE.format(
    estado="aprobado", instancia=EMPTY_INSTANCIA,
    aprobacion=_CLEAN_APROBACION.format(hash=_HASH))

#: Same three hashed anchors, lock section busy — for `LockRowTests`.
BUSY_LOCK_MANDATE_TEXT = _MANDATE_TEMPLATE.format(
    estado="aprobado", instancia=BUSY_INSTANCIA,
    aprobacion=_CLEAN_APROBACION.format(hash=_HASH))

#: Same three hashed anchors, `## Aprobación` empty — reports exactly
#: `{aprobacion-ausente}` (`fase: spec` in `_estado.yaml` below keeps
#: `implementacion-sin-aprobacion` from also firing).
EMPTY_APPROVAL_MANDATE_TEXT = _MANDATE_TEMPLATE.format(
    estado="borrador", instancia=EMPTY_INSTANCIA, aprobacion="Sin entradas.")


def estado_yaml(unit_id: str, *, fase: str = "implement", modo: str = "supervisado") -> str:
    return (f"id: {unit_id}\nmodo: {modo}\nfase: {fase}\nestado: en-progreso\n"
           "mandato: mandato.md\n")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def seed_unit(repo: Path, rel_dir: str, *, unit_id: str,
             mandate_text: str = CLEAN_MANDATE_TEXT, fase: str = "implement") -> Path:
    unit = repo / rel_dir
    write(unit / "_estado.yaml", estado_yaml(unit_id, fase=fase))
    write(unit / "mandato.md", mandate_text)
    write(unit / "bitacora.md", "# bitácora\n\n## 2026-09-17T09:00Z · seed\n")
    return unit


#: MCP stdio configs — the stub server's four modes.
def mcp_config(tmp_dir: Path, mode: str) -> Path:
    path = tmp_dir / f"mcp-{mode}.json"
    path.write_text(json.dumps({"mcpServers": {"pce-mcp": {
        "type": "stdio", "command": "python3",
        "args": [".spec/scripts/tests/stub_knowledge_server.py", "--mode", mode],
    }}}), encoding="utf-8")
    return path


#: Module-level, already-clean MCP config (mode `ok`), for other test modules that
#: import this one for its shared plumbing (`pol-dev-buscar-antes-de-crear`) —
#: `test_mandate_adversarial.py` in particular.
_MCP_OK_DIR = Path(tempfile.mkdtemp(prefix="0114-preflight-mcp-ok-"))
MCP_OK = mcp_config(_MCP_OK_DIR, "ok")


def mcp_absent_config(tmp_dir: Path) -> Path:
    path = tmp_dir / "mcp-absent.json"
    path.write_text(json.dumps({"mcpServers": {"pce-mcp": {"command": "false"}}}),
                    encoding="utf-8")
    return path


class PreflightCase(unittest.TestCase):
    """Base: a temporary repository, plus the CA-05a check."""

    def new_seeded_repo(self, prefix: str) -> Path:
        repo = new_repo(prefix)
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        return repo

    def mcp_dir(self) -> Path:
        d = Path(tempfile.mkdtemp(prefix="0114-preflight-mcp-cfg-"))
        self.addCleanup(shutil.rmtree, d, ignore_errors=True)
        return d

    def assert_leaves_no_trace(self, repo: Path, args: list[str],
                               env: dict[str, str] | None = None
                               ) -> subprocess.CompletedProcess:
        """CA-05a: `git status --porcelain` is identical before and after."""
        before = run_git(repo, "status", "--porcelain")
        completed = run_preflight(args, env)
        self.assertEqual(run_git(repo, "status", "--porcelain"), before,
                         "el preflight escribió algo en el repo")
        return completed

    def check_assertions(self, assertions_text: str, repo: Path, stdout: str) -> None:
        evidence = repo.parent / f"{repo.name}-evidencia"
        evidence.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, evidence, ignore_errors=True)
        (evidence / "tabla.txt").write_text(stdout, encoding="utf-8")
        assertions = evidence / "aserciones.txt"
        assertions.write_text(assertions_text, encoding="utf-8")
        completed = verificar_aserciones(evidence, assertions)
        self.assertEqual(completed.returncode, 0,
                         (completed.stderr or completed.stdout) + "\n" + stdout)


# --- CA-01 — lock row ---------------------------------------------------------------

LOCK_OCUPADO_ASSERTIONS = """\
tabla.txt|^lock: BLOCK ocupado sesion=.*session_00-fixture-otra-instancia
tabla.txt|^lock: BLOCK .*inicio=2026-09-17T13:55Z$
tabla.txt|^  regla: BLOCK si .*Instancia en curso
"""


class LockRowTests(PreflightCase):

    def test_lock_ocupado_bloquea_citando_sesion_e_inicio(self) -> None:
        """CA-01."""
        repo = self.new_seeded_repo("0114-preflight-lock-")
        unit = seed_unit(repo, ".spec/units/9109-prueba-supervisado",
                         unit_id="9109-prueba-supervisado",
                         mandate_text=BUSY_LOCK_MANDATE_TEXT)
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed lock ocupado")

        completed = self.assert_leaves_no_trace(
            repo, ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
                   "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.assertNotEqual(completed.returncode, 0)
        self.check_assertions(LOCK_OCUPADO_ASSERTIONS, repo, completed.stdout)

    def test_el_preflight_solo_usa_la_operacion_de_solo_lectura_del_lock(self) -> None:
        """CA-01: cero apariciones de los dos identificadores de escritura del script
        de lock en `preflight.py` — la lectura más estricta, incluidos comentarios."""
        source = SCRIPT.read_text(encoding="utf-8")
        for forbidden in ("acquire", "release"):
            self.assertNotIn(forbidden, source)
        self.assertIn("inspect", source)


# --- CA-02 — MCP row ----------------------------------------------------------------

class McpRowTests(PreflightCase):

    def setUp(self) -> None:
        self.repo = self.new_seeded_repo("0114-preflight-mcp-")
        self.unit = seed_unit(self.repo, ".spec/units/9413-preflight-git-limpio",
                              unit_id="9413-preflight-git-limpio")
        run_git(self.repo, "add", "-A")
        run_git(self.repo, "commit", "-q", "-m", "seed")
        self.mandate = self.unit / "mandato.md"
        self._mcp_dir = self.mcp_dir()

    def probe(self, config: Path, env: dict[str, str] | None = None
              ) -> subprocess.CompletedProcess:
        return self.assert_leaves_no_trace(
            self.repo,
            ["--mandate", str(self.mandate), "--unit", str(self.unit),
             "--mcp-config", str(config), "--mcp-timeout", SHORT_TIMEOUT],
            env)

    def test_pass_nombra_la_entidad_resuelta_y_el_preflight_sale_0(self) -> None:
        completed = self.probe(mcp_config(self._mcp_dir, "ok"))
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn(f"PASS resolvió {preflight.KNOWN_ENTITY_ID}",
                      row_of(completed.stdout, "mcp"))

    def test_las_cuatro_causas_de_block_se_inducen_de_verdad(self) -> None:
        casos = [(mcp_absent_config(self._mcp_dir), "ausente"),
                 (mcp_config(self._mcp_dir, "unauthorized"), "401"),
                 (mcp_config(self._mcp_dir, "budget"), "presupuesto agotado"),
                 (mcp_config(self._mcp_dir, "hang"), "timeout")]
        for config, cause in casos:
            with self.subTest(causa=cause):
                completed = self.probe(config)
                self.assertNotEqual(completed.returncode, 0, completed.stdout)
                self.assertIn(f"BLOCK {cause} ", row_of(completed.stdout, "mcp"))

    def test_401_tambien_cuando_el_servidor_muere_antes_de_initialize(self) -> None:
        """D-5, primera fila: el proceso termina sin responder `initialize` y su
        stderr trae la marca. No tiene fixture versionado propio — los cuatro modos
        del stub declarados cubren las cuatro causas; esta entrada observada es el
        segundo camino hacia la misma causa `401`."""
        config = self.repo / "mcp-exit-401.json"
        config.write_text(json.dumps({"mcpServers": {"pce-mcp": {
            "command": "python3",
            "args": [".spec/scripts/tests/stub_knowledge_server.py",
                     "--mode", "exit-401"]}}}), encoding="utf-8")
        completed = self.probe(config)
        self.assertIn("BLOCK 401 ", row_of(completed.stdout, "mcp"))

    def test_el_mapeo_de_causas_es_cerrado_y_tiene_exactamente_cuatro(self) -> None:
        """CA-02: las cuatro cadenas viven en un mapeo cerrado del script."""
        self.assertEqual(set(preflight.MCP_CAUSES.values()),
                         {"ausente", "401", "presupuesto agotado", "timeout"})

    def test_el_veredicto_no_mira_los_campos_de_antidrift(self) -> None:
        """CA-02 / `pol-gob-indice-agenticos-c3`: cero apariciones de los dos campos
        de antidrift en el script. El stub los devuelve a propósito y el PASS de
        arriba se produce igual."""
        source = SCRIPT.read_text(encoding="utf-8")
        for field in ("sourceDigest", "pointerDigest"):
            self.assertNotIn(field, source)

    def test_una_notificacion_intermedia_no_es_jsonrpc_malformado(self) -> None:
        """El modo `ok` del stub emite `notifications/message` antes de cada
        respuesta: tomar el primer marco como respuesta daría `preflight-error` con el
        MCP sano."""
        completed = self.probe(mcp_config(self._mcp_dir, "ok"))
        self.assertNotIn("preflight-error", completed.stdout)

    # --- D-3: resolved configuration and its origin ---------------------------------

    def test_la_fila_declara_contra_que_configuracion_resolvio(self) -> None:
        ok_config = mcp_config(self._mcp_dir, "ok")
        flag = self.probe(ok_config)
        self.assertIn(f"(config: {ok_config}, origen: flag)", row_of(flag.stdout, "mcp"))

        env_run = self.assert_leaves_no_trace(
            self.repo,
            ["--mandate", str(self.mandate), "--unit", str(self.unit),
             "--mcp-timeout", SHORT_TIMEOUT],
            clean_env({"IARK_PREFLIGHT_MCP_CONFIG": str(ok_config)}))
        self.assertIn(f"(config: {ok_config}, origen: env)",
                      row_of(env_run.stdout, "mcp"))

    def test_precedencia_flag_env_default(self) -> None:
        """D-3, un caso por origen. El origen `default` se comprueba en la resolución,
        no arrancando el servidor real de `.mcp.json`."""
        ok_config = mcp_config(self._mcp_dir, "ok")
        config_401 = mcp_config(self._mcp_dir, "unauthorized")
        previous = os.environ.pop("IARK_PREFLIGHT_MCP_CONFIG", None)
        self.addCleanup(lambda: os.environ.__setitem__(
            "IARK_PREFLIGHT_MCP_CONFIG", previous) if previous else None)
        self.assertEqual(preflight.resolve_mcp_config(None),
                         (REPO_ROOT / ".mcp.json", "default"))
        os.environ["IARK_PREFLIGHT_MCP_CONFIG"] = str(ok_config)
        self.assertEqual(preflight.resolve_mcp_config(None), (ok_config, "env"))
        self.assertEqual(preflight.resolve_mcp_config(str(config_401)),
                         (Path(config_401), "flag"))
        os.environ.pop("IARK_PREFLIGHT_MCP_CONFIG", None)

    def test_la_cabecera_documenta_el_tiempo_maximo_y_el_origen(self) -> None:
        header = preflight.__doc__ or ""
        self.assertIn("20 s", header)
        self.assertIn("IARK_PREFLIGHT_MCP_CONFIG", header)
        self.assertIn("not representative of the session", header)
        self.assertEqual(preflight.DEFAULT_MCP_TIMEOUT, 20.0)


# --- CA-03 — git row -----------------------------------------------------------------

GIT_LIMPIO_ASSERTIONS = """\
# CA-03, cuarto caso: todo limpio -> PASS con lista informativa vacía.
tabla.txt|^git: PASS nada sin commitear bajo el alcance$
tabla.txt|^  sucias: ninguna$
tabla.txt|^  informativas: ninguna$
tabla.txt|^  alcance: \\.spec/units/9413-preflight-git-limpio, \\.spec/units/9413-preflight-git-limpio/mandato\\.md$
"""

GIT_SUCIO_ASSERTIONS = """\
# CA-03, primer caso: el árbol de la unidad del alcance está sucio -> BLOCK, con la
# ruta literal, y nada en la lista informativa.
tabla.txt|^git: BLOCK 1 ruta\\(s\\) sin commitear bajo el alcance$
tabla.txt|^    \\.spec/units/9414-preflight-git-sucio/bitacora\\.md$
tabla.txt|^  informativas: ninguna$
tabla.txt|^resultado: BLOCK \\(git\\)$
"""

GIT_OTRAS_SUCIAS_ASSERTIONS = """\
# CA-03, segundo caso: solo otras unidades sucias -> PASS con lista informativa no
# vacía. El ruido de fuera del alcance no cambia el veredicto.
tabla.txt|^git: PASS nada sin commitear bajo el alcance$
tabla.txt|^  sucias: ninguna$
tabla.txt|^    \\.spec/units/9416-preflight-fuera-de-alcance/bitacora\\.md$
"""

GIT_DOS_MIEMBROS_ASSERTIONS = """\
# CA-03, tercer caso: plan con dos unidades miembro; solo la que está FUERA del
# alcance recibido está sucia -> PASS, con esa ruta en la lista informativa.
tabla.txt|^git: PASS nada sin commitear bajo el alcance$
tabla.txt|^  sucias: ninguna$
tabla.txt|^    \\.spec/planes/9417-preflight-dos-miembros/units/9419-miembro-fuera-de-alcance/_estado\\.yaml$
tabla.txt|^  alcance: \\.spec/planes/9417-preflight-dos-miembros/units/9418-miembro-en-alcance, \\.spec/planes/9417-preflight-dos-miembros/plan-maestro\\.md$
"""


class GitRowTests(PreflightCase):

    def test_arbol_de_la_unidad_sucio_bloquea(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-git-sucio-")
        unit_rel = ".spec/units/9414-preflight-git-sucio"
        unit = seed_unit(repo, unit_rel, unit_id="9414-preflight-git-sucio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        write(unit / "bitacora.md",
              (unit / "bitacora.md").read_text(encoding="utf-8") + "\nsin commitear\n")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.check_assertions(GIT_SUCIO_ASSERTIONS, repo, completed.stdout)
        self.assertEqual(completed.returncode, 1, completed.stdout)

    def test_solo_otras_unidades_sucias_pasa_con_lista_informativa(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-git-otras-")
        unit_rel = ".spec/units/9415-preflight-git-otras"
        unit = seed_unit(repo, unit_rel, unit_id="9415-preflight-git-otras")
        otra = seed_unit(repo, ".spec/units/9416-preflight-fuera-de-alcance",
                         unit_id="9416-preflight-fuera-de-alcance")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        write(otra / "bitacora.md",
              (otra / "bitacora.md").read_text(encoding="utf-8") + "\nsin commitear\n")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.check_assertions(GIT_OTRAS_SUCIAS_ASSERTIONS, repo, completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_plan_de_dos_miembros_con_el_de_fuera_del_alcance_sucio(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-git-dos-miembros-")
        plan_rel = ".spec/planes/9417-preflight-dos-miembros"
        plan_dir = repo / plan_rel
        write(plan_dir / "plan-maestro.md", CLEAN_MANDATE_TEXT)
        en_alcance = seed_unit(repo, f"{plan_rel}/units/9418-miembro-en-alcance",
                               unit_id="9418-miembro-en-alcance")
        fuera = seed_unit(repo, f"{plan_rel}/units/9419-miembro-fuera-de-alcance",
                          unit_id="9419-miembro-fuera-de-alcance")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        write(fuera / "_estado.yaml",
              (fuera / "_estado.yaml").read_text(encoding="utf-8") + "\nsucio: true\n")
        completed = run_preflight(
            ["--mandate", str(plan_dir / "plan-maestro.md"), "--unit", str(en_alcance),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.check_assertions(GIT_DOS_MIEMBROS_ASSERTIONS, repo, completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def test_todo_limpio_pasa_con_lista_informativa_vacia(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-git-limpio-")
        unit_rel = ".spec/units/9413-preflight-git-limpio"
        unit = seed_unit(repo, unit_rel, unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.check_assertions(GIT_LIMPIO_ASSERTIONS, repo, completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stdout)

    def _seed_git_limpio(self, prefix: str) -> tuple[Path, Path]:
        repo = self.new_seeded_repo(prefix)
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        return repo, unit

    def test_sin_unit_es_preflight_error(self) -> None:
        """CA-03: el preflight nunca infiere el alcance."""
        repo, unit = self._seed_git_limpio("0114-preflight-sin-unit-")
        completed = run_preflight(["--mandate", str(unit / "mandato.md")])
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("preflight-error:", completed.stdout)

    def test_la_regla_se_imprime_junto_a_la_fila(self) -> None:
        repo, unit = self._seed_git_limpio("0114-preflight-regla-")
        completed = run_preflight(["--mandate", str(unit / "mandato.md"),
                                   "--unit", str(unit),
                                   "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        lines = completed.stdout.splitlines()
        index = lines.index(row_of(completed.stdout, "git"))
        self.assertTrue(lines[index + 1].startswith("  regla: "), lines[index + 1])
        self.assertIn("si y solo si", lines[index + 1])


# --- CA-04 — validator row ------------------------------------------------------------

class ValidatorRowTests(PreflightCase):

    def scenario(self, unit_id: str, mandate_text: str, expected_codes: list[str]) -> None:
        repo = self.new_seeded_repo(f"0114-preflight-{unit_id}-")
        unit_dir = seed_unit(repo, f".spec/units/{unit_id}", unit_id=unit_id,
                             mandate_text=mandate_text,
                             fase="spec" if not expected_codes else "spec")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        state_before = (unit_dir / "_estado.yaml").read_bytes()

        completed = self.assert_leaves_no_trace(
            repo, ["--mandate", str(unit_dir / "mandato.md"),
                   "--unit", str(unit_dir),
                   "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        printed = [line.strip() for line in completed.stdout.splitlines()
                   if line.strip().startswith("codigos: ")]
        self.assertEqual(len(printed), 1, completed.stdout)
        codes = printed[0][len("codigos: "):].strip()
        wanted = sorted(set(expected_codes))
        self.assertEqual(codes, " ".join(wanted) if wanted else "ninguno")
        self.assertEqual((unit_dir / "_estado.yaml").read_bytes(), state_before,
                         "`validaciones_mandato` no puede ganar entradas (CA-04)")

    def test_mandato_con_codigos(self) -> None:
        self.scenario("9412-preflight-validador-con-codigos",
                      EMPTY_APPROVAL_MANDATE_TEXT, ["aprobacion-ausente"])

    def test_mandato_sin_codigos(self) -> None:
        self.scenario("9411-preflight-validador-limpio", CLEAN_MANDATE_TEXT, [])

    def test_plan_en_plan_md_no_se_valida_como_unidad(self) -> None:
        # Every real plan is `plan.md`, not `plan-maestro.md`: keying the branch off
        # the file name alone validated the plan directory as an isolated unit, which
        # has no `_estado.yaml` of its own and so always reported
        # `unidad-supervisado-sin-mandato`.
        repo = self.new_seeded_repo("0114-preflight-plan-md-")
        plan_rel = ".spec/planes/9420-preflight-plan-md"
        plan_dir = repo / plan_rel
        write(plan_dir / "plan.md", CLEAN_MANDATE_TEXT)
        miembro = seed_unit(repo, f"{plan_rel}/units/9421-miembro",
                            unit_id="9421-miembro")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")

        completed = run_preflight(
            ["--mandate", str(plan_dir / "plan.md"), "--unit", str(miembro),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        printed = [line.strip() for line in completed.stdout.splitlines()
                   if line.strip().startswith("codigos: ")]
        self.assertEqual(printed, ["codigos: ninguno"], completed.stdout)
        self.assertEqual(completed.returncode, 0, completed.stdout)


# --- CA-05b — zero writes outside the repository --------------------------------------

class ZeroWriteTests(PreflightCase):

    def test_no_escribe_en_home_tmpdir_xdg_ni_deja_bytecode(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-cero-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        sandbox = Path(tempfile.mkdtemp(prefix="0114-preflight-jaula-"))
        self.addCleanup(shutil.rmtree, sandbox, ignore_errors=True)
        empties = {}
        for name in ("HOME", "TMPDIR", "XDG_CACHE_HOME"):
            directory = sandbox / name.lower()
            directory.mkdir()
            empties[name] = directory

        before = sorted(p.relative_to(REPO_ROOT).as_posix()
                        for p in (REPO_ROOT / ".spec" / "scripts").rglob("*"))
        env = clean_env({name: str(path) for name, path in empties.items()})
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))], env)
        self.assertEqual(completed.returncode, 0, completed.stdout)

        for name, path in empties.items():
            self.assertEqual(list(path.iterdir()), [], f"{name} dejó de estar vacío")
        after = sorted(p.relative_to(REPO_ROOT).as_posix()
                       for p in (REPO_ROOT / ".spec" / "scripts").rglob("*"))
        self.assertEqual(before, after, "apareció o desapareció algo en .spec/scripts")


# --- CA-06 — exit codes and the `preflight-error` table --------------------------------

class ExitCodeTests(PreflightCase):

    def test_exit_0_si_y_solo_si_todas_las_filas_son_pass(self) -> None:
        repo = self.new_seeded_repo("0114-preflight-exit0-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        args = ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
                "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))]
        completed = run_preflight(args)
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("resultado: PASS", completed.stdout)

        write(unit / "bitacora.md", "sucio\n")
        blocked = run_preflight(args)
        self.assertEqual(blocked.returncode, 1, blocked.stdout)

    def test_la_tabla_lista_todas_las_filas_sin_detenerse_en_el_primer_block(self
                                                                            ) -> None:
        """CA-06: dos filas BLOCK y las cuatro presentes."""
        repo = self.new_seeded_repo("0114-preflight-tabla-")
        unit = seed_unit(repo, ".spec/units/9414-preflight-git-sucio",
                         unit_id="9414-preflight-git-sucio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        write(unit / "bitacora.md", "sucio\n")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_absent_config(self.mcp_dir())),
             "--mcp-timeout", SHORT_TIMEOUT])
        self.assertEqual(completed.returncode, 1, completed.stdout)
        for name in preflight.ROWS:
            self.assertTrue(row_of(completed.stdout, name))
        self.assertIn("BLOCK ausente", row_of(completed.stdout, "mcp"))
        self.assertIn("BLOCK", row_of(completed.stdout, "git"))
        self.assertIn("resultado: BLOCK (mcp, git)", completed.stdout)

    def test_fallo_interno_con_una_fila_ya_evaluada_la_marca_no_autoritativa(self
                                                                             ) -> None:
        """CA-06, caso (i): configuración MCP `type: http` (D-6) con el lock libre."""
        repo = self.new_seeded_repo("0114-preflight-http-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        config = repo / "mcp-http.json"
        config.write_text(json.dumps(
            {"mcpServers": {"pce-mcp": {"type": "http",
                                        "url": "http://localhost:8000/mcp"}}}),
            encoding="utf-8")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(config)])
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("lock: PASS (no autoritativo: preflight-error)",
                      completed.stdout)
        for name in ("mcp", "git", "validador"):
            self.assertIn(f"{name}: no evaluada", completed.stdout)
        self.assertRegex(completed.stdout, r"(?m)^preflight-error: .+$")
        self.assertIn("stdio", completed.stdout)

    def test_fallo_interno_sin_ninguna_fila_evaluada(self) -> None:
        """CA-06, caso (ii): ruta de mandato inexistente con `--unit` válido."""
        repo = self.new_seeded_repo("0114-preflight-sin-mandato-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        completed = run_preflight(
            ["--mandate", str(repo / "no-existe.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.assertEqual(completed.returncode, 2, completed.stdout)
        for name in preflight.ROWS:
            self.assertIn(f"{name}: no evaluada", completed.stdout)
        self.assertNotIn("no autoritativo", completed.stdout)
        self.assertRegex(completed.stdout, r"(?m)^preflight-error: .+$")

    def test_los_tres_codigos_estan_en_la_cabecera(self) -> None:
        header = preflight.__doc__ or ""
        for code in (0, 1, 2):
            self.assertRegex(header, rf"(?m)^\s+{code}\s")
        self.assertIn("preflight-error", header)
        self.assertEqual(
            (preflight.EXIT_PASS, preflight.EXIT_BLOCK,
             preflight.EXIT_PREFLIGHT_ERROR), (0, 1, 2))


# --- Self-heal (G2, CA-01 … CA-10) -----------------------------------------------------
# Everything below exercises `--self-heal`. CA-07 (absent the flag, byte-identical to
# before) is covered two ways: every test class above this point keeps passing
# unmodified against its own inline seed, and `SelfHealBackwardCompatTests` below adds
# an explicit check that no self-heal vocabulary leaks into a plain run.

class PidAliveTests(unittest.TestCase):
    """The primitive CA-01's evidence is built on."""

    def test_pid_vivo(self) -> None:
        self.assertTrue(preflight.pid_alive(os.getpid()))

    def test_pid_muerto(self) -> None:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        self.assertFalse(preflight.pid_alive(proc.pid))


class SelfHealFlagValidationTests(PreflightCase):

    def test_self_heal_sin_session_es_preflight_error(self) -> None:
        repo = self.new_seeded_repo("0159-selfheal-sin-session-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")), "--self-heal"])
        self.assertEqual(completed.returncode, 2, completed.stdout)
        self.assertIn("preflight-error:", completed.stdout)
        self.assertIn("--session", completed.stdout)


class SelfHealBackwardCompatTests(PreflightCase):
    """CA-07 — absent `--self-heal`, no self-heal vocabulary leaks in."""

    def test_sin_self_heal_no_aparecen_tokens_de_self_heal(self) -> None:
        repo = self.new_seeded_repo("0159-compat-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        completed = self.assert_leaves_no_trace(
            repo, ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
                   "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        for token in ("self-heal", "DEGRADED"):
            self.assertNotIn(token, completed.stdout)


# --- CA-01 / CA-02 — lock reap + bitácora entry ----------------------------------------

class LockReapTests(PreflightCase):

    SESSION = "sesion-9420-reap"
    UNIT = ".spec/units/9413-preflight-git-limpio"

    @staticmethod
    def _dead_pid() -> int:
        proc = subprocess.Popen([sys.executable, "-c", "pass"])
        proc.wait()
        return proc.pid

    def _seed_with_lock(self, prefix: str, body_lines: list[str]) -> tuple[Path, Path, Path]:
        repo = self.new_seeded_repo(prefix)
        unit = seed_unit(repo, self.UNIT, unit_id="9413-preflight-git-limpio")
        mandate = unit / "mandato.md"
        text = mandate.read_text(encoding="utf-8")
        mandate.write_text(preflight._rewrite_lock_section(text, body_lines),
                           encoding="utf-8")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "lock ocupado (fixture)")
        return repo, unit, mandate

    def test_reap_libera_lock_propio_con_pid_muerto_y_anexa_bitacora(self) -> None:
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-propio-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: DEGRADED", row_of(completed.stdout, "lock"))
        self.assertIn("resultado: DEGRADED", completed.stdout)

        text = mandate.read_text(encoding="utf-8")
        self.assertNotIn(f"sesion: {self.SESSION}", text)
        self.assertIn("Sin instancia.", text)

        bitacora = (unit / "bitacora.md").read_text(encoding="utf-8")
        self.assertIn("self-heal:reap", bitacora)
        self.assertIn(f"sesion={self.SESSION}", bitacora)
        self.assertIn(f"pid={pid}", bitacora)
        self.assertIn("evidencia=pid-muerto", bitacora)

    def test_no_reap_si_la_sesion_es_ajena(self) -> None:
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-ajena-",
            ["- sesion: otra-sesion", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: BLOCK", row_of(completed.stdout, "lock"))
        self.assertIn("sesion: otra-sesion", mandate.read_text(encoding="utf-8"))

    def test_no_reap_con_pid_vivo(self) -> None:
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-vivo-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {os.getpid()}"])
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: BLOCK", row_of(completed.stdout, "lock"))

    def test_no_reap_sin_pid_registrado(self) -> None:
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-sin-pid-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z"])
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: BLOCK", row_of(completed.stdout, "lock"))

    def test_sin_self_heal_no_reapea_aunque_el_pid_este_muerto(self) -> None:
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-sin-flag-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: BLOCK", row_of(completed.stdout, "lock"))
        self.assertNotIn("DEGRADED", completed.stdout)
        self.assertIn(f"sesion: {self.SESSION}", mandate.read_text(encoding="utf-8"))

    def test_reap_cuyo_escritura_falla_reporta_self_heal_fallo(self) -> None:
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-fallo-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        mandate.chmod(0o444)
        try:
            if os.access(mandate, os.W_OK):
                self.skipTest("el proceso puede escribir un archivo en modo 444 (¿root?)")
            completed = run_preflight(
                ["--mandate", str(mandate), "--unit", str(unit),
                 "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
                 "--self-heal", "--session", self.SESSION])
            self.assertEqual(completed.returncode, 1, completed.stdout)
            self.assertIn("self-heal-fallo: reap-lock", completed.stdout)
            self.assertNotIn("DEGRADED", completed.stdout)
        finally:
            mandate.chmod(0o644)

    def test_reap_cuyo_commit_falla_reporta_self_heal_fallo_reap_commit(self) -> None:
        """Hallazgo 1 (gate 0159): fuerza el fallo del `git commit` dentro de
        `_commit_reap_changes` — mismo patrón de hook `pre-commit` que
        `SelfHealGitTests.test_fallo_a_mitad_de_la_secuencia_reporta_self_heal_fallo_y_vuelve`.
        La escritura del reap (mandato + bitácora) ya sucedió antes de intentar el
        commit, así que el lock queda genuinamente libre en el archivo en disco
        aunque el commit no se haya completado (docstring de `_commit_reap_changes`)."""
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-commit-fallo-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)

        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("self-heal-fallo: reap-commit (", completed.stdout)
        self.assertNotIn("DEGRADED", completed.stdout)

        text = mandate.read_text(encoding="utf-8")
        self.assertNotIn(f"sesion: {self.SESSION}", text)
        self.assertIn("Sin instancia.", text)

    def test_reap_mas_archivo_sucio_independiente_en_la_misma_invocacion(self) -> None:
        """Hallazgo 2 (gate 0159): reap exitoso (lock con pid muerto + sesión
        propia) combinado, en la MISMA invocación, con un archivo sucio
        independiente dentro del mismo alcance — el escenario exacto que motivó
        crear `_commit_reap_changes()` (su propio docstring). El commit del reap
        debe sobrevivir en la rama original tras el `checkout` de vuelta que hace
        la remediación de CA-03 sobre el archivo sucio independiente."""
        pid = self._dead_pid()
        repo, unit, mandate = self._seed_with_lock(
            "0159-reap-mas-sucio-",
            [f"- sesion: {self.SESSION}", "- lanzador: Fixture",
             "- inicio: 2026-09-24T00:00Z", f"- pid: {pid}"])
        original_branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")

        estado = unit / "_estado.yaml"
        estado.write_text(estado.read_text(encoding="utf-8") + "\nsucio: true\n",
                          encoding="utf-8")

        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("lock: DEGRADED", row_of(completed.stdout, "lock"))
        self.assertIn("git: DEGRADED", row_of(completed.stdout, "git"))
        self.assertIn("resultado: DEGRADED", completed.stdout)

        self.assertEqual(run_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                         original_branch)
        self.assertEqual(run_git(repo, "status", "--porcelain"), "")

        text_after = mandate.read_text(encoding="utf-8")
        self.assertNotIn(f"sesion: {self.SESSION}", text_after)
        self.assertIn("Sin instancia.", text_after)
        log_original = run_git(repo, "log", "--oneline", "-n", "5")
        self.assertIn("self-heal: reap de lock huérfano", log_original)


# --- Hallazgo 3 (gate 0159) — anti-drift vs instance_lock.py ---------------------------

class LockAntiDriftTests(unittest.TestCase):
    """`preflight.LOCK_EMPTY_BODY`/`_rewrite_lock_section` duplicate, on purpose
    (module docstring, § Zero writes: the reap never invokes `instance_lock.py`'s
    own write subcommands), the literal and algorithm of
    `instance_lock.EMPTY_BODY`/`replace_section`. Without this test a future edit
    to `instance_lock.py` would diverge silently instead of breaking the build."""

    def test_el_literal_de_cuerpo_vacio_coincide_con_instance_lock(self) -> None:
        self.assertEqual(preflight.LOCK_EMPTY_BODY, instance_lock.EMPTY_BODY)

    def test_el_algoritmo_de_reescritura_coincide_con_replace_section(self) -> None:
        text = ("# Mandato\n\n## Instancia en curso\n\n"
                "- sesion: alguien\n- lanzador: Fixture\n"
                "- inicio: 2026-09-24T00:00Z\n- pid: 12345\n\n"
                "## Otra sección\n\ncontenido\n")
        body = ["- sesion: nueva", "- lanzador: Otro",
                "- inicio: 2026-09-24T01:00Z"]
        self.assertEqual(preflight._rewrite_lock_section(text, body),
                         instance_lock.replace_section(text, body))


# --- CA-03 / CA-04 — seed branch + auto-commit -----------------------------------------

class SelfHealGitTests(PreflightCase):

    SESSION = "sesion-git-9421"
    UNIT = ".spec/units/9413-preflight-git-limpio"

    def _dirty_repo(self, prefix: str) -> tuple[Path, Path, Path]:
        repo = self.new_seeded_repo(prefix)
        unit = seed_unit(repo, self.UNIT, unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        mandate = unit / "mandato.md"
        target = unit / "bitacora.md"
        target.write_text(target.read_text(encoding="utf-8") + "\nsucio\n",
                          encoding="utf-8")
        return repo, unit, mandate

    def test_commit_hacia_rama_seed_deja_el_arbol_limpio(self) -> None:
        repo, unit, mandate = self._dirty_repo("0159-selfheal-git-")
        original_branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertEqual(completed.returncode, 0, completed.stdout)
        self.assertIn("git: DEGRADED", row_of(completed.stdout, "git"))
        self.assertIn("resultado: DEGRADED", completed.stdout)
        self.assertRegex(
            completed.stdout,
            rf"rama seed: self-heal/{self.SESSION}-\d{{8}}T\d{{6}}Z")
        self.assertIn("accion: commit", completed.stdout)

        self.assertEqual(run_git(repo, "status", "--porcelain"), "")
        self.assertEqual(run_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                         original_branch)
        self.assertIn("self-heal/",
                      run_git(repo, "branch", "--list", "self-heal/*"))

    def test_sin_self_heal_arbol_sucio_sigue_bloqueando_sin_tocar_nada(self) -> None:
        repo, unit, mandate = self._dirty_repo("0159-selfheal-git-sinflag-")
        before = run_git(repo, "status", "--porcelain")
        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok"))])
        self.assertNotEqual(completed.returncode, 0, completed.stdout)
        self.assertEqual(run_git(repo, "status", "--porcelain"), before)
        self.assertNotIn("DEGRADED", completed.stdout)

    def test_fallo_a_mitad_de_la_secuencia_reporta_self_heal_fallo_y_vuelve(self) -> None:
        repo, unit, mandate = self._dirty_repo("0159-selfheal-git-fallo-")
        hooks = repo / ".git" / "hooks"
        hooks.mkdir(parents=True, exist_ok=True)
        hook = hooks / "pre-commit"
        hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
        original_branch = run_git(repo, "rev-parse", "--abbrev-ref", "HEAD")

        completed = run_preflight(
            ["--mandate", str(mandate), "--unit", str(unit),
             "--mcp-config", str(mcp_config(self.mcp_dir(), "ok")),
             "--self-heal", "--session", self.SESSION])
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("self-heal-fallo: git-commit", completed.stdout)
        self.assertNotIn("DEGRADED", completed.stdout)
        self.assertEqual(run_git(repo, "rev-parse", "--abbrev-ref", "HEAD"),
                         original_branch)

    def test_colision_de_nombre_de_rama_es_preflight_error(self) -> None:
        """plan.md § Decisiones: una colisión de rama seed nunca reintenta con
        sufijos — es un `preflight-error` explícito, nada se ha tocado todavía."""
        repo = self.new_seeded_repo("0159-selfheal-git-colision-")
        seed_unit(repo, self.UNIT, unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        fixed_name = "self-heal/colision-fixture"
        run_git(repo, "branch", fixed_name)
        original = preflight._seed_branch_name
        preflight._seed_branch_name = lambda session: fixed_name
        try:
            with self.assertRaises(preflight.PreflightError):
                preflight._self_heal_git(repo, [], "sesion-colision")
        finally:
            preflight._seed_branch_name = original


# --- CA-05 / CA-06 — MCP retry with backoff ---------------------------------------------

class RetryProbeMcpTests(unittest.TestCase):
    """Direct calls with an injected `probe`/`sleep` (plan.md § Reutilización): no
    new MCP stub mode needed."""

    def test_reintenta_hasta_conectar_y_registra_los_delays(self) -> None:
        calls = {"n": 0}

        def probe(config, timeout, entity):
            calls["n"] += 1
            return "no-response" if calls["n"] < 3 else ""

        sleeps: list[float] = []
        observed, attempts, delays = preflight.retry_probe_mcp(
            Path("/dev/null"), 1.0, "entidad", attempts=5,
            sleep=sleeps.append, probe=probe)
        self.assertEqual(observed, "")
        self.assertEqual(attempts, 3)
        self.assertEqual(delays, sleeps)
        self.assertEqual(len(delays), 2)
        self.assertEqual(delays[0], preflight.SELF_HEAL_RETRY_BASE_SECONDS)

    def test_agotamiento_reporta_la_causa_del_ultimo_intento(self) -> None:
        calls = {"n": 0}

        def probe(config, timeout, entity):
            calls["n"] += 1
            return "no-response"

        observed, attempts, delays = preflight.retry_probe_mcp(
            Path("/dev/null"), 1.0, "entidad", attempts=3,
            sleep=lambda s: None, probe=probe)
        self.assertEqual(observed, "no-response")
        self.assertEqual(attempts, 3)
        self.assertEqual(calls["n"], 3)
        self.assertEqual(len(delays), 2)             # never sleeps after the last try

    def test_backoff_esta_acotado_por_el_tope(self) -> None:
        _, _, delays = preflight.retry_probe_mcp(
            Path("/dev/null"), 1.0, "entidad", attempts=6,
            sleep=lambda s: None, probe=lambda *a: "no-response")
        self.assertTrue(all(d <= preflight.SELF_HEAL_RETRY_MAX_DELAY_SECONDS
                            for d in delays))
        self.assertEqual(delays, sorted(delays))


class RowMcpSelfHealTests(unittest.TestCase):
    """`row_mcp` under `--self-heal` — `retry_probe_mcp` is resolved by name at
    call time, so it can be substituted here without touching the subprocess
    path (module-level lookup, not a bound default)."""

    def test_degradado_cuando_el_reintento_logra_conectar(self) -> None:
        original = preflight.retry_probe_mcp
        preflight.retry_probe_mcp = lambda *a, **k: ("", 2, [2.0])
        try:
            row = preflight.row_mcp(Path("/dev/null"), "flag", 1.0, "entidad",
                                    self_heal=True, mcp_retries=3)
        finally:
            preflight.retry_probe_mcp = original
        self.assertEqual(row.verdict, "DEGRADED")
        self.assertIn("tras 2 intento(s)", row.detail)
        self.assertIn("intentos: 2/3", row.extra)

    def test_pass_sin_calificar_cuando_el_primer_intento_ya_conecta(self) -> None:
        original = preflight.retry_probe_mcp
        preflight.retry_probe_mcp = lambda *a, **k: ("", 1, [])
        try:
            row = preflight.row_mcp(Path("/dev/null"), "flag", 1.0, "entidad",
                                    self_heal=True, mcp_retries=3)
        finally:
            preflight.retry_probe_mcp = original
        self.assertEqual(row.verdict, "PASS")

    def test_agotamiento_sigue_block_nunca_degraded(self) -> None:
        original = preflight.retry_probe_mcp
        preflight.retry_probe_mcp = lambda *a, **k: ("no-response", 3, [2.0, 4.0])
        try:
            row = preflight.row_mcp(Path("/dev/null"), "flag", 1.0, "entidad",
                                    self_heal=True, mcp_retries=3)
        finally:
            preflight.retry_probe_mcp = original
        self.assertEqual(row.verdict, "BLOCK")
        self.assertIn("agotados 3 intento(s)", row.detail)


class SelfHealMcpEndToEndTests(PreflightCase):
    """One subprocess-level check that the exhaustion path really reaches
    `main()` unchanged: still `BLOCK`, never `DEGRADED`, exit 1."""

    def test_agotamiento_de_principio_a_fin_sigue_block(self) -> None:
        repo = self.new_seeded_repo("0159-mcp-agotamiento-")
        unit = seed_unit(repo, ".spec/units/9413-preflight-git-limpio",
                         unit_id="9413-preflight-git-limpio")
        run_git(repo, "add", "-A")
        run_git(repo, "commit", "-q", "-m", "seed")
        completed = run_preflight(
            ["--mandate", str(unit / "mandato.md"), "--unit", str(unit),
             "--mcp-config", str(mcp_absent_config(self.mcp_dir())),
             "--mcp-timeout", SHORT_TIMEOUT,
             "--self-heal", "--session", "sesion-mcp-9422",
             "--self-heal-mcp-retries", "2"])
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("mcp: BLOCK", row_of(completed.stdout, "mcp"))
        self.assertNotIn("DEGRADED", completed.stdout)
        self.assertIn("agotados 2 intento(s)", completed.stdout)
        self.assertIn("intentos: 2/2", completed.stdout)


# --- T2 — parity of `codes_from_output` -----------------------------------------------

BASH_PIPELINE = (
    "sed -n 's/^códigos: //p' | tr ' ' '\\n' | sed '/^$/d' | sort -u"
)


class CodesFromOutputParityTests(unittest.TestCase):
    """`codes_from_output()` is the single Python implementation of the parse that
    `comparar_esperado` does in Bash (`_common.sh:140-144`)."""

    SAMPLES = [
        "PASS — mandato sin incumplimientos (mandato.md).",
        "FAIL — 1 incumplimiento(s):\n  aprobacion-ausente  x\ncódigos: aprobacion-ausente",
        "códigos: b a c",
        "códigos: a  b",                       # double space
        "códigos: a b a",                      # duplicate
        "códigos: \n",                          # no codes at all
        "AVISO algo\ncódigos: retoma-incompleta paradas-sin-referencia",
    ]

    def bash_oracle(self, sample: str) -> list[str]:
        completed = subprocess.run(["bash", "-c", BASH_PIPELINE], input=sample + "\n",
                                   capture_output=True, text=True, check=True)
        return [line for line in completed.stdout.splitlines() if line]

    def test_paridad_con_la_tuberia_de_comparar_esperado(self) -> None:
        for sample in self.SAMPLES:
            with self.subTest(sample=sample[:40]):
                self.assertEqual(codes_from_output(sample), self.bash_oracle(sample))

    def test_paridad_sobre_la_salida_real_del_validador(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="0114-codes-parity-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        unit = seed_unit(tmp, "unidad-aislada", unit_id="9201-fixture-unidad-aislada",
                         mandate_text=EMPTY_APPROVAL_MANDATE_TEXT, fase="spec")
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), "--unidad", str(unit)],
            capture_output=True, text=True, check=False, env=clean_env())
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        self.assertEqual(codes_from_output(completed.stdout),
                         self.bash_oracle(completed.stdout))
        self.assertEqual(codes_from_output(completed.stdout), ["aprobacion-ausente"])


if __name__ == "__main__":
    unittest.main()
