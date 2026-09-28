"""`report_git_sync.py` and the two `_common.py` functions it rests on (unit 0114, G1).

Covers CA-11a, CA-11b, CA-11c and CA-11d for the git synchronization ritual, plus the
two parity tests that T2 owes:

* `dirty_paths()` against the **two** branches of the runner's `case`
  (`supervised-test.sh`) — not the whole `case`: the runner's other branches are
  widenings of its own, and the test pins that difference instead of hiding it.
* `resume_point()` against `check_resume` (`validate_mandate.py:755-759`), the reader
  that actually decides which resume entry is in force.

Every seed below is inline (no fixture tree): `report_git_sync.py` only reads
`## Punto de retoma` via `resume_point()` — never the validator's full anchor
set or `_estado.yaml` — so a mandate seed here only ever needs that one
section (plus `## Estado` for the precondition-splice tests).
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
SCRIPT = SCRIPTS / "report_git_sync.py"
VALIDATOR = SCRIPTS / "validate_mandate.py"
COMMON_SH = SCRIPTS / "_common.sh"

sys.path.insert(0, str(SCRIPTS))

import report_git_sync  # noqa: E402
from _common import codes_from_output, dirty_paths, resume_point  # noqa: E402

BRANCH = "fixture"


# --- temporary repositories -------------------------------------------------------

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


def commit_all(repo: Path, message: str) -> str:
    """`--allow-empty` because a fixture whose seed carries no `{{HEAD}}` marker has
    nothing to write in the second commit, and the commit is what advances `HEAD`."""
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "--allow-empty", "-m", message)
    return run_git(repo, "rev-parse", "HEAD")


def write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def substitute(path: Path, marker: str, value: str) -> None:
    path.write_text(path.read_text(encoding="utf-8").replace(marker, value),
                    encoding="utf-8")


def verificar_aserciones(evidence: Path, assertions: Path) -> subprocess.CompletedProcess:
    """The assertion dialect has one implementation: `_common.sh` (CA-11d)."""
    return subprocess.run(
        ["bash", "-c", 'source "$1"; verificar_aserciones "$2" "$3"',
         "_", str(COMMON_SH), str(evidence), str(assertions)],
        capture_output=True, text=True, check=False, cwd=str(REPO_ROOT),
        env={**_clean_env(), "VALIDATOR": str(VALIDATOR)})


def _clean_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return env


def run_report(args: list[str]) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False,
                          env=_clean_env())


def mandate_text(retoma_block: str) -> str:
    """Minimal mandate seed: `## Punto de retoma` (what `resume_point()` reads)
    plus `## Estado`, the only two sections this ritual and its precondition
    tests ever touch."""
    return (
        "# Mandato supervisado — fixture\n\n"
        "## Punto de retoma\n\n" + retoma_block + "\n"
        "## Estado\n\n- estado: aprobado\n"
    )


# --- the three CA-11d scenarios, each with its own inline assertion set ------------

COINCIDE_ASSERTIONS = """\
reporte.txt|^veredicto: coincide$
reporte.txt|^retoma: 2026-09-18T10:00Z$
reporte.txt|^rama esperada: .+$
reporte.txt|^rama observada: .+$
reporte.txt|^commit esperado: .+$
reporte.txt|^commit observado: [0-9a-f]{40}$
reporte.txt|^sucias: ninguna$
"""

DIVERGE_ASSERTIONS = """\
reporte.txt|^veredicto: diverge$
reporte.txt|^rama esperada: fixture$
reporte.txt|^rama observada: otra-rama$
reporte.txt|^commit esperado: .*0123456789abcdef0123456789abcdef01234567
reporte.txt|^commit observado: [0-9a-f]{40}$
reporte.txt|^sucias:$
reporte.txt|^  \\.spec/units/9422-sync-diverge/bitacora\\.md$
reporte.txt|^informativas:$
reporte.txt|^  ruido-fuera-de-alcance\\.txt$
"""

EMPATE_ASSERTIONS = """\
reporte.txt|^veredicto: coincide$
reporte.txt|^retoma: 2026-09-18T10:00Z$
reporte.txt|^commit observado: [0-9a-f]{40}$
reporte.txt|^sucias: ninguna$
"""


class SyncFixtureTests(unittest.TestCase):
    """CA-11d: each scenario is a real repository, and the oracle is its own
    inline assertion set applied with `verificar_aserciones`."""

    def build(self, unit: str, retoma_block: str) -> tuple[Path, Path]:
        repo = new_repo(f"0114-sync-{unit}-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = repo / ".spec" / "units" / unit / "mandato.md"
        write(mandate, mandate_text(retoma_block))
        substitute(mandate, "{{RAMA}}", BRANCH)
        head = commit_all(repo, "seed")
        substitute(mandate, "{{HEAD}}", head)
        commit_all(repo, "punto de retoma")
        return repo, mandate

    def check(self, repo: Path, stdout: str, assertions_text: str) -> None:
        evidence = repo.parent / f"{repo.name}-evidencia"
        evidence.mkdir(exist_ok=True)
        self.addCleanup(shutil.rmtree, evidence, ignore_errors=True)
        (evidence / "reporte.txt").write_text(stdout, encoding="utf-8")
        assertions = evidence / "aserciones.txt"
        assertions.write_text(assertions_text, encoding="utf-8")
        completed = verificar_aserciones(evidence, assertions)
        self.assertEqual(completed.returncode, 0,
                         completed.stderr or completed.stdout)

    def test_coincide(self) -> None:
        retoma = (
            "### 2026-09-18T10:00Z\n\n"
            "- rama: `{{RAMA}}`\n"
            "- commits: `{{HEAD}}` (fixture)\n"
            "- validacion: `python3 .spec/scripts/validate_mandate.py --unidad …` → exit 0\n"
            "- unidades-en-curso: 9421-sync-coincide, fase `implement`\n"
            "- pasos: 1. releer este mandato; 2. continuar la tarea en curso\n\n"
        )
        repo, mandate = self.build("9421-sync-coincide", retoma)
        before = run_git(repo, "status", "--porcelain")
        completed = run_report(["--mandate", str(mandate),
                                "--unit", str(mandate.parent)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.check(repo, completed.stdout, COINCIDE_ASSERTIONS)
        self.assertEqual(run_git(repo, "status", "--porcelain"), before)

    def test_diverge(self) -> None:
        retoma = (
            "### 2026-09-18T10:00Z\n\n"
            "- rama: `{{RAMA}}`\n"
            "- commits: `0123456789abcdef0123456789abcdef01234567` "
            "(hash que no está en la historia)\n"
            "- validacion: `python3 .spec/scripts/validate_mandate.py --unidad …` → exit 0\n"
            "- unidades-en-curso: 9422-sync-diverge, fase `implement`\n"
            "- pasos: 1. releer este mandato; 2. continuar la tarea en curso\n\n"
        )
        repo, mandate = self.build("9422-sync-diverge", retoma)
        run_git(repo, "checkout", "-q", "-b", "otra-rama")
        write(mandate.parent / "bitacora.md", "cambio sin commitear\n")
        write(repo / "ruido-fuera-de-alcance.txt", "ruido\n")
        completed = run_report(["--mandate", str(mandate),
                                "--unit", str(mandate.parent)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.check(repo, completed.stdout, DIVERGE_ASSERTIONS)

    def test_empate_misma_fecha_gana_la_entrada_de_mas_abajo(self) -> None:
        """Dos entradas con la **misma** fecha y `commits` distintos: gana la de
        más abajo (desempate `>=` de `resume_point()`), que trae el `HEAD` real."""
        retoma = (
            "### 2026-09-18T10:00Z\n\n"
            "- rama: `{{RAMA}}`\n"
            "- commits: `0123456789abcdef0123456789abcdef01234567` (hash inventado)\n"
            "- validacion: `python3 .spec/scripts/validate_mandate.py --unidad …` → exit 0\n"
            "- unidades-en-curso: 9423-sync-empate, fase `implement`\n"
            "- pasos: 1. releer este mandato; 2. continuar la tarea en curso\n\n"
            "### 2026-09-18T10:00Z\n\n"
            "- rama: `{{RAMA}}`\n"
            "- commits: `{{HEAD}}` (fixture)\n"
            "- validacion: `python3 .spec/scripts/validate_mandate.py --unidad …` → exit 0\n"
            "- unidades-en-curso: 9423-sync-empate, fase `implement`\n"
            "- pasos: 1. releer este mandato; 2. continuar la tarea en curso\n\n"
        )
        repo, mandate = self.build("9423-sync-empate", retoma)
        completed = run_report(["--mandate", str(mandate),
                                "--unit", str(mandate.parent)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.check(repo, completed.stdout, EMPATE_ASSERTIONS)

    def test_dry_run_da_el_mismo_reporte(self) -> None:
        """CA-11a para un ritual de solo lectura."""
        retoma = (
            "### 2026-09-18T10:00Z\n\n"
            "- rama: `{{RAMA}}`\n"
            "- commits: `{{HEAD}}` (fixture)\n"
            "- unidades-en-curso: 9421-sync-coincide, fase `implement`\n\n"
        )
        repo, mandate = self.build("9421-sync-coincide", retoma)
        before = run_git(repo, "status", "--porcelain")
        plain = run_report(["--mandate", str(mandate), "--unit", str(mandate.parent)])
        dry = run_report(["--mandate", str(mandate), "--unit", str(mandate.parent),
                          "--dry-run"])
        self.assertEqual(plain.stdout, dry.stdout)
        self.assertEqual(plain.returncode, dry.returncode)
        self.assertEqual(run_git(repo, "status", "--porcelain"), before)


class SyncPreconditionTests(unittest.TestCase):
    """CA-11c: one exit code per precondition, and the tree untouched."""

    def test_mandato_inexistente_sale_1(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="0114-sync-falta-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        completed = run_report(["--mandate", str(tmp / "no-existe.md"),
                                "--unit", str(tmp)])
        self.assertEqual(completed.returncode, 1, completed.stderr)

    def _repo_without_resume(self, body: str) -> tuple[Path, Path]:
        repo = new_repo("0114-sync-sin-retoma-")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = repo / ".spec" / "units" / "9421-sync-coincide" / "mandato.md"
        write(mandate, mandate_text(
            "### 2026-09-18T10:00Z\n\n- rama: `x`\n- commits: `x`\n\n"))
        text = mandate.read_text(encoding="utf-8")
        # Anchored to the start of a line: a real mandate's own preamble may
        # *mention* both anchors in prose, and splicing on the mention would
        # cut the wrong part.
        start = re.search(r"(?m)^## Punto de retoma$", text).start()
        end = re.search(r"(?m)^## Estado$", text).start()
        mandate.write_text(text[:start] + body + text[end:], encoding="utf-8")
        commit_all(repo, "seed sin punto de retoma")
        return repo, mandate

    def test_sin_seccion_de_punto_de_retoma_sale_6(self) -> None:
        repo, mandate = self._repo_without_resume("")
        before = run_git(repo, "status", "--porcelain")
        completed = run_report(["--mandate", str(mandate),
                                "--unit", str(mandate.parent)])
        self.assertEqual(completed.returncode, 6, completed.stdout)
        self.assertEqual(run_git(repo, "status", "--porcelain"), before)

    def test_seccion_presente_pero_sin_entradas_sale_6(self) -> None:
        """El caso del primer lanzamiento: la plantilla deja la sección vacía."""
        repo, mandate = self._repo_without_resume(
            "## Punto de retoma\n\nSin entradas: nunca se paró.\n\n")
        completed = run_report(["--mandate", str(mandate),
                                "--unit", str(mandate.parent)])
        self.assertEqual(completed.returncode, 6, completed.stdout)

    def test_la_cabecera_documenta_los_codigos_y_el_dry_run(self) -> None:
        header = report_git_sync.__doc__ or ""
        self.assertIn("--dry-run", header)
        for code in (0, 1, 2, 6):
            self.assertRegex(header, rf"(?m)^\s+{code}\s")


# --- T2: parity of `dirty_paths` ----------------------------------------------------

#: The two branches of the runner's `case` (`supervised-test.sh`), and only
#: those: `"$T"` and `"$T"/*`.
RUNNER_CASE = r'''
set -euo pipefail
cd "$1"
T="$2"
git status --porcelain -z | tr '\0' '\n' | sed -n 's/^.. //p' | while IFS= read -r p; do
  case "$p" in
    "$T"|"$T"/*) printf '%s\n' "$p" ;;
  esac
done
'''


class DirtyPathsParityTests(unittest.TestCase):
    """`dirty_paths()` classifies by tree exactly like the runner's first two `case`
    branches — no more (the prefix sibling stays out) and no less (a rename counts by
    its destination, a path with spaces is not split)."""

    def setUp(self) -> None:
        self.repo = new_repo("0114-dirty-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.tree = ".spec/units/9431-parity"
        write(self.repo / self.tree / "original.txt", "original\n")
        write(self.repo / self.tree / "queda.txt", "queda\n")
        write(self.repo / f"{self.tree}-bis" / "hermano.txt", "hermano\n")
        write(self.repo / "otro" / "ruido.txt", "ruido\n")
        commit_all(self.repo, "base")
        # A rename (staged, so `git status` reports it as `R`), a path with a space
        # and dirt outside the tree.
        run_git(self.repo, "mv", f"{self.tree}/original.txt",
                f"{self.tree}/renombrado.txt")
        write(self.repo / self.tree / "a b.txt", "con espacio\n")
        write(self.repo / f"{self.tree}-bis" / "hermano.txt", "cambiado\n")
        write(self.repo / "otro" / "ruido.txt", "cambiado\n")
        run_git(self.repo, "add", "-A")

    def runner_oracle(self, tree: str) -> list[str]:
        completed = subprocess.run(["bash", "-c", RUNNER_CASE, "_",
                                    str(self.repo), tree],
                                   capture_output=True, text=True, check=True)
        return sorted(p for p in completed.stdout.splitlines() if p)

    def test_paridad_con_las_dos_ramas_del_case_del_runner(self) -> None:
        scoped, _ = dirty_paths(self.repo, [self.tree])
        self.assertEqual(scoped, self.runner_oracle(self.tree))

    def test_el_rename_cuenta_por_su_destino_y_el_espacio_no_parte_la_ruta(self) -> None:
        scoped, _ = dirty_paths(self.repo, [self.tree])
        self.assertIn(f"{self.tree}/renombrado.txt", scoped)
        self.assertIn(f"{self.tree}/a b.txt", scoped)
        self.assertNotIn(f"{self.tree}/original.txt", scoped)

    def test_el_hermano_de_prefijo_cae_en_informativas(self) -> None:
        """`9431-parity-bis` está **fuera** para `dirty_paths()` (no es `"$T"` ni
        `"$T"/*`) y **dentro** para la tercera rama del `case` del runner, que es un
        ensanche suyo: el ensanche queda declarado, no oculto."""
        scoped, informational = dirty_paths(self.repo, [self.tree])
        sibling = f"{self.tree}-bis/hermano.txt"
        self.assertNotIn(sibling, scoped)
        self.assertIn(sibling, informational)
        self.assertIn("otro/ruido.txt", informational)

    def test_un_archivo_suelto_como_arbol_acotado_cuenta_por_igualdad(self) -> None:
        scoped, informational = dirty_paths(self.repo, [f"{self.tree}/a b.txt"])
        self.assertEqual(scoped, [f"{self.tree}/a b.txt"])
        self.assertNotIn(f"{self.tree}/a b.txt", informational)


# --- T2: parity of `resume_point` ---------------------------------------------------

class ResumePointParityTests(unittest.TestCase):
    """`resume_point()` picks the same entry `check_resume` does.

    `check_resume` does not expose its choice, but it emits `retoma-incompleta`
    **about** it. So each variant leaves exactly one entry without `rama`: the
    validator must report the code if and only if `resume_point()` comes back with an
    entry that has no `rama`.
    """

    def setUp(self) -> None:
        self.tmp = Path(tempfile.mkdtemp(prefix="0114-retoma-"))
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

    def unit_from(self, name: str, retoma_block: str) -> Path:
        """`check_resume` (`validate_mandate.py`) only evaluates `retoma-incompleta`
        when the mandate reads as `parado` — the old fixture-based version of this
        test flipped `aprobado` to `parado` after copying a fixture; this seed
        just states `parado` directly."""
        unit = self.tmp / name
        unit.mkdir()
        write(unit / "_estado.yaml",
              f"id: {name}\nmodo: supervisado\nfase: implement\nmandato: mandato.md\n")
        write(unit / "mandato.md",
              mandate_text(retoma_block).replace("- estado: aprobado", "- estado: parado"))
        return unit

    def validator_codes(self, unit: Path) -> list[str]:
        completed = subprocess.run(
            [sys.executable, str(VALIDATOR), "--unidad", str(unit)],
            capture_output=True, text=True, check=False, env=_clean_env())
        self.assertIn(completed.returncode, (0, 1), completed.stderr)
        return codes_from_output(completed.stdout)

    def assert_agree(self, unit: Path) -> None:
        mandate = (unit / "mandato.md").read_text(encoding="utf-8")
        point = resume_point(mandate)
        self.assertIsNotNone(point)
        chosen_incomplete = not (point or {}).get("rama", "").strip()
        reported = "retoma-incompleta" in self.validator_codes(unit)
        self.assertEqual(chosen_incomplete, reported,
                         f"resume_point eligió {'in' if chosen_incomplete else ''}"
                         f"completa y el validador dijo {reported}")

    #: `RESUME_FIELDS` of `validate_mandate.py`: `check_resume` reports
    #: `retoma-incompleta` when **any** of these five is missing from the
    #: entry in force, not just `rama` — every entry below carries all five
    #: except where a case deliberately drops `rama` to be "incomplete".
    _FULL_ENTRY = ("- rama: `x`\n- commits: `x`\n- validacion: `x`\n"
                   "- unidades-en-curso: `x`\n- pasos: `x`\n")
    _ENTRY_WITHOUT_RAMA = ("- commits: `x`\n- validacion: `x`\n"
                          "- unidades-en-curso: `x`\n- pasos: `x`\n")

    def test_empate_con_la_primera_entrada_incompleta(self) -> None:
        """Gana la de abajo, que sí trae `rama`: ni `resume_point()` ni el validador
        reportan nada."""
        retoma = (
            f"### 2026-09-18T10:00Z\n\n{self._ENTRY_WITHOUT_RAMA}\n"
            f"### 2026-09-18T10:00Z\n\n{self._FULL_ENTRY}\n"
        )
        unit = self.unit_from("9424-empate-primera-incompleta", retoma)
        self.assert_agree(unit)

    def test_empate_con_la_segunda_entrada_incompleta(self) -> None:
        """Gana la de abajo, que es la que perdió `rama`: los dos lo ven."""
        retoma = (
            f"### 2026-09-18T10:00Z\n\n{self._FULL_ENTRY}\n"
            f"### 2026-09-18T10:00Z\n\n{self._ENTRY_WITHOUT_RAMA}\n"
        )
        unit = self.unit_from("9425-empate-segunda-incompleta", retoma)
        self.assert_agree(unit)

    def test_entrada_unica_completa_e_incompleta(self) -> None:
        complete = self.unit_from(
            "9426-retoma-completa",
            f"### 2026-09-18T10:00Z\n\n{self._FULL_ENTRY}\n")
        self.assert_agree(complete)
        incomplete = self.unit_from(
            "9427-retoma-incompleta",
            f"### 2026-09-18T10:00Z\n\n{self._ENTRY_WITHOUT_RAMA}\n")
        self.assert_agree(incomplete)

    def test_sin_seccion_o_sin_entradas_devuelve_none(self) -> None:
        self.assertIsNone(resume_point("# Mandato\n\n## Estado\n"))
        self.assertIsNone(resume_point(
            "## Punto de retoma\n\nSin entradas.\n\n## Estado\n"))


if __name__ == "__main__":
    unittest.main()
