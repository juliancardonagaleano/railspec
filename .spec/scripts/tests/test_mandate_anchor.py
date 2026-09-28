"""`mandate_anchor.py` — the sixth ritual, added by the additivity-anchor correction
(unit 0114, G4).

Covers CA-29 (the `write`/`resolve` contract itself), the CA-30 discriminant
(launch creates, resume never does — the skill-level oracle lives in the pilot's
`E00-feliz`, T19), CA-31 (Paso 9 of `validate-supervised.sh`, exercised in
`ValidateSupervisedBaseTests`) and D-17's decisive check: `write` never changes
`--hash`.

Reuses, rather than reimplementing (`pol-dev-buscar-antes-de-crear`):

* `test_report_git_sync.run_git` / `new_repo` / `commit_all` / `write` — the same
  temporary-repository helpers every 0114 git-backed ritual test already uses.

Every seed below is inline (no fixture tree): `mandate_anchor.py` only needs
`resolve_unit`/`resolve_plan` to resolve the mandate file (a real `mandato:`
field pointing at an existing file) — it never runs the validator's full
`validate_content`, so a mandate body does not need every anchor `PLAN_ANCHORS`/
`UNIT_ANCHORS` require to exercise `write`/`resolve`.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
SCRIPT = SCRIPTS / "mandate_anchor.py"
VALIDATOR = SCRIPTS / "validate_mandate.py"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import mandate_anchor  # noqa: E402
import test_report_git_sync as sync_test  # noqa: E402
from _common import codes_from_output, normalize, sections  # noqa: E402

run_git = sync_test.run_git
new_repo = sync_test.new_repo
commit_all = sync_test.commit_all
write_file = sync_test.write

FULL_HEX_A = "a" * 40
FULL_HEX_B = "b" * 40

#: A `mandato.md` body with no `## Ancla de aditividad` section at all — the
#: "absent" case (D-17) and the base every anchor variant below starts from.
FREE_MANDATO_BODY = (
    "# Mandato supervisado — {unit_id}\n\n"
    "## Objetivo y criterio de salida\n\n"
    "Objetivo de prueba.\n\n"
    "## Aprobación\n\n"
    "### 2026-09-22T00:00Z\n"
    "- quien: julian\n"
    "- hash: 0000000000000000000000000000000000000000000000000000000000000000\n\n"
    "## Revisión posterior\n\n"
    "(Sin revisiones.)\n"
)

#: `_estado.yaml` bodies for the two mandate forms `resolve_unit`/`resolve_plan`
#: need to resolve: a unit whose `mandato:` points at its own `mandato.md`.
FREE_UNIT_ESTADO = (
    "id: {unit_id}\n"
    "modo: supervisado\n"
    "fase: implement\n"
    "mandato: mandato.md\n"
)

#: A minimal, self-contained plan mandate — `resolve_plan` treats an existing
#: directory as a fixture root, so no `.spec/planes/` nesting is required.
FREE_PLAN_BODY = (
    "# Plan supervisado — {plan_id}\n\n"
    "## Objetivo y criterio de salida\n\n"
    "Objetivo de prueba.\n\n"
    "## Aprobación\n\n"
    "### 2026-09-22T00:00Z\n"
    "- quien: julian\n"
    "- hash: 0000000000000000000000000000000000000000000000000000000000000000\n\n"
    "## Revisión posterior\n\n"
    "(Sin revisiones.)\n"
)

#: `## Ancla de aditividad` variants, appended to `FREE_MANDATO_BODY` — one
#: valid form and the three CA-29 invalid shapes.
ANCHOR_VARIANTS = {
    "valida": f"## Ancla de aditividad\n\ncommit: {FULL_HEX_A}\n",
    "invalida-sin-linea": "## Ancla de aditividad\n\nPendiente de asignar.\n",
    "invalida-valor": "## Ancla de aditividad\n\ncommit: no-es-un-hash\n",
    "invalida-repetida": f"## Ancla de aditividad\n\ncommit: {FULL_HEX_A}\ncommit: {FULL_HEX_B}\n",
}


def run(args: list[str], cwd: Path | None = None) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, str(SCRIPT), *args],
                          capture_output=True, text=True, check=False,
                          cwd=str(cwd) if cwd else None)


def build_repo(case: str, unit_id: str) -> tuple[Path, Path]:
    """A fresh git repo with an inline unit whose `mandato.md` carries the
    `ANCHOR_VARIANTS[case]` section, committed once."""
    repo = new_repo(f"0114-anchor-{case}-")
    unit_dir = repo / ".spec" / "units" / unit_id
    unit_dir.mkdir(parents=True)
    write_file(unit_dir / "_estado.yaml", FREE_UNIT_ESTADO.format(unit_id=unit_id))
    write_file(
        unit_dir / "mandato.md",
        FREE_MANDATO_BODY.format(unit_id=unit_id) + "\n" + ANCHOR_VARIANTS[case],
    )
    commit_all(repo, "seed")
    return repo, unit_dir


def copy_unit_into_repo(_source: Path | None, unit_id: str) -> tuple[Path, Path]:
    """A fresh repo holding an inline unit at `.spec/units/<unit_id>/`, with no
    `## Ancla de aditividad` section (the "absent" case), committed once.
    `_source` is accepted and ignored — kept so call sites read the same as
    the version this replaces."""
    repo = new_repo(f"0114-anchor-unit-{unit_id}-")
    unit_dir = repo / ".spec" / "units" / unit_id
    unit_dir.mkdir(parents=True)
    write_file(unit_dir / "_estado.yaml", FREE_UNIT_ESTADO.format(unit_id=unit_id))
    write_file(unit_dir / "mandato.md", FREE_MANDATO_BODY.format(unit_id=unit_id))
    commit_all(repo, "seed")
    return repo, unit_dir


def copy_plan_into_repo(_source: Path | None, plan_id: str) -> tuple[Path, Path]:
    """A fresh repo holding an inline plan mandate at a top-level directory
    (`resolve_plan` treats an existing directory as a fixture root)."""
    repo = new_repo(f"0114-anchor-plan-{plan_id}-")
    plan_dir = repo / plan_id
    plan_dir.mkdir(parents=True)
    write_file(plan_dir / "plan.md", FREE_PLAN_BODY.format(plan_id=plan_id))
    commit_all(repo, "seed")
    return repo, plan_dir


# ======================================================================================
# Pure functions: `read_anchor` / `render_anchor` (no subprocess, no git needed)
# ======================================================================================

class ReadAnchorTests(unittest.TestCase):
    """CA-29's definition of "valid": exactly one `commit: <40 hex>` line. Every
    other shape — including the ones the plan calls out by name — is `None`, the same
    bucket as "the section does not exist at all" (D-18)."""

    def test_ausente_es_none(self) -> None:
        self.assertIsNone(mandate_anchor.read_anchor("# M\n\n## Otra\n\ncosa\n"))

    def test_valida_devuelve_el_commit(self) -> None:
        text = f"## Ancla de aditividad\n\ncommit: {FULL_HEX_A}\n"
        self.assertEqual(mandate_anchor.read_anchor(text), FULL_HEX_A)

    def test_sin_linea_commit_es_none(self) -> None:
        text = "## Ancla de aditividad\n\nPendiente de asignar.\n"
        self.assertIsNone(mandate_anchor.read_anchor(text))

    def test_valor_que_no_son_40_hex_es_none(self) -> None:
        text = "## Ancla de aditividad\n\ncommit: no-es-un-hash\n"
        self.assertIsNone(mandate_anchor.read_anchor(text))

    def test_linea_repetida_es_none(self) -> None:
        text = (f"## Ancla de aditividad\n\ncommit: {FULL_HEX_A}\n"
               f"commit: {FULL_HEX_B}\n")
        self.assertIsNone(mandate_anchor.read_anchor(text))

    def test_seccion_de_otro_mandato_no_confunde_el_regex(self) -> None:
        """Un `commit:` fuera de la sección (otra sección, prosa) no cuenta."""
        text = (f"## Otra sección\n\ncommit: {FULL_HEX_A}\n\n"
               "## Ancla de aditividad\n\nPendiente de asignar.\n")
        self.assertIsNone(mandate_anchor.read_anchor(text))


class RenderAnchorTests(unittest.TestCase):
    """`render_anchor` is what both the real write and `--dry-run` (CA-11a) build
    their output from — testing it directly pins the exact bytes."""

    def test_crea_como_ultima_seccion_cuando_esta_ausente(self) -> None:
        text = "# M\n\n## Revisión posterior\n\nVacía.\n"
        out = mandate_anchor.render_anchor(text, FULL_HEX_A)
        self.assertEqual(
            out,
            "# M\n\n## Revisión posterior\n\nVacía.\n\n"
            f"## Ancla de aditividad\n\ncommit: {FULL_HEX_A}\n")

    def test_repara_en_el_mismo_lugar_sin_mover_ni_duplicar_el_encabezado(self) -> None:
        text = ("# M\n\n## Ancla de aditividad\n\nPendiente de asignar.\n\n"
               "## Otra\n\nresto intacto\n")
        out = mandate_anchor.render_anchor(text, FULL_HEX_B)
        self.assertEqual(
            out,
            f"# M\n\n## Ancla de aditividad\n\ncommit: {FULL_HEX_B}\n\n"
            "## Otra\n\nresto intacto\n")
        self.assertEqual(out.count("## Ancla de aditividad"), 1)

    def test_no_toca_ninguna_otra_seccion(self) -> None:
        """Comparado por `normalize()` (la misma que usa `approval_hash()`), no por
        igualdad byte a byte: insertar una sección nueva al final añade la línea en
        blanco de separación que ya usa todo el árbol antes del nuevo encabezado, y
        esa línea en blanco queda —por cómo corta `sections()`— dentro del cuerpo
        parseado de la sección **anterior**. `normalize()` la descarta igual que
        descarta cualquier otra línea en blanco, así que no es un cambio de
        contenido para nada que lea el cuerpo con ella (`approval_hash()` incluido)."""
        text = "# M\n\n## Uno\n\na\n\n## Dos\n\nb\n\n## Revisión posterior\n\nc\n"
        out = mandate_anchor.render_anchor(text, FULL_HEX_A)
        before = sections(text)
        after = sections(out)
        for anchor in before:
            self.assertEqual(normalize(before[anchor]), normalize(after[anchor]),
                             anchor)


# ======================================================================================
# CLI over the four `mandate-anchor/` fixtures (sección válida + tres inválidas)
# ======================================================================================

class FixtureCliTests(unittest.TestCase):

    def test_seccion_valida_resolve_imprime_el_commit_existente(self) -> None:
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        completed = run(["resolve", "--unidad", str(unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), FULL_HEX_A)

    def test_seccion_valida_write_sin_force_no_toca_el_archivo(self) -> None:
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        before = mandate.read_bytes()
        completed = run(["write", "--unidad", str(unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(mandate.read_bytes(), before)
        self.assertEqual(run_git(repo, "status", "--porcelain"), "")

    def test_seccion_valida_write_force_la_reemplaza(self) -> None:
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        head = run_git(repo, "rev-parse", "HEAD")
        completed = run(["write", "--unidad", str(unit_dir), "--force"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(mandate_anchor.read_anchor(
            mandate.read_text(encoding="utf-8")), head)
        self.assertNotEqual(head, FULL_HEX_A, "el fixture ya traía otro commit")

    @staticmethod
    def _heading_count(text: str) -> int:
        """Counts **heading lines**, not substring occurrences: the fixtures' own
        introductory blockquote mentions `` `## Ancla de aditividad` `` in prose (same
        trap `test_report_git_sync.py` documents for its own fixtures), so a naive
        `text.count(...)` would over-count."""
        return sum(1 for line in text.splitlines()
                  if line == mandate_anchor.ANCHOR_SECTION)

    def _assert_repairs_to_valid(self, case: str, unit_id: str) -> None:
        repo, unit_dir = build_repo(case, unit_id)
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"

        resolved = run(["resolve", "--unidad", str(unit_dir)])
        self.assertEqual(resolved.returncode, 0, resolved.stderr)
        self.assertEqual(resolved.stdout, "", "una sección inválida no imprime nada")

        completed = run(["write", "--unidad", str(unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        text = mandate.read_text(encoding="utf-8")
        self.assertEqual(self._heading_count(text), 1)
        commit = mandate_anchor.read_anchor(text)
        self.assertIsNotNone(commit, "write debe dejar la sección válida")
        self.assertEqual(run(["resolve", "--unidad", str(unit_dir)]).stdout.strip(),
                         commit)

    def test_sin_linea_commit_se_repara(self) -> None:
        self._assert_repairs_to_valid("invalida-sin-linea", "9442-ancla-sin-linea")

    def test_valor_invalido_se_repara(self) -> None:
        self._assert_repairs_to_valid("invalida-valor", "9443-ancla-valor-invalido")

    def test_linea_repetida_se_repara(self) -> None:
        self._assert_repairs_to_valid("invalida-repetida", "9444-ancla-repetida")


# ======================================================================================
# Sección ausente + el discriminante de CA-30 (lanzamiento crea, retoma no)
# ======================================================================================

class AbsentSectionTests(unittest.TestCase):
    """`copy_unit_into_repo` seeds a `mandato.md` with no `## Ancla de
    aditividad` section at all (D-17, T15b): the "absent" case, inline."""

    def setUp(self) -> None:
        self.repo, self.unit_dir = copy_unit_into_repo(
            None, "9450-ancla-ausente")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.mandate = self.unit_dir / "mandato.md"

    def test_resolve_no_imprime_nada_antes_de_crearla(self) -> None:
        completed = run(["resolve", "--unidad", str(self.unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_write_por_defecto_usa_head(self) -> None:
        head = run_git(self.repo, "rev-parse", "HEAD")
        completed = run(["write", "--unidad", str(self.unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        commit = mandate_anchor.read_anchor(
            self.mandate.read_text(encoding="utf-8"))
        self.assertEqual(commit, head)

    def test_write_con_commit_explicito_normaliza_a_40_hex(self) -> None:
        short = run_git(self.repo, "rev-parse", "--short", "HEAD")
        full = run_git(self.repo, "rev-parse", "HEAD")
        completed = run(["write", "--unidad", str(self.unit_dir), "--commit", short])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        commit = mandate_anchor.read_anchor(
            self.mandate.read_text(encoding="utf-8"))
        self.assertEqual(commit, full)
        self.assertEqual(len(commit), 40)

    def test_queda_como_ultima_seccion(self) -> None:
        run(["write", "--unidad", str(self.unit_dir)])
        text = self.mandate.read_text(encoding="utf-8")
        self.assertTrue(text.rstrip("\n").endswith(
            mandate_anchor.read_anchor(text)))
        last_heading = [line for line in text.splitlines() if line.startswith("## ")][-1]
        self.assertEqual(last_heading, mandate_anchor.ANCHOR_SECTION)

    def test_ca30_lanzamiento_crea_retoma_no(self) -> None:
        """El caso discriminante de D-23: invocar `write` (simula el lanzamiento) crea
        la sección; **no** invocarlo (simula la retoma) la deja igual de ausente — a
        diferencia de repetir `write` sobre un mandato que ya la tiene, que por CA-29
        es idempotente en los dos casos y no distinguiría nada."""
        # "Launch": write is invoked.
        launch_repo, launch_unit = copy_unit_into_repo(
            None, "9451-ancla-lanzamiento")
        self.addCleanup(shutil.rmtree, launch_repo, ignore_errors=True)
        run(["write", "--unidad", str(launch_unit)])
        self.assertIsNotNone(mandate_anchor.read_anchor(
            (launch_unit / "mandato.md").read_text(encoding="utf-8")))

        # "Resume": write is never invoked.
        resume_repo, resume_unit = copy_unit_into_repo(
            None, "9452-ancla-retoma")
        self.addCleanup(shutil.rmtree, resume_repo, ignore_errors=True)
        self.assertIsNone(mandate_anchor.read_anchor(
            (resume_unit / "mandato.md").read_text(encoding="utf-8")))


# ======================================================================================
# CA-11a: dry-run byte-identical to the real write
# ======================================================================================

class DryRunTests(unittest.TestCase):

    def test_dry_run_ausente_es_byte_identico_a_lo_escrito(self) -> None:
        repo, unit_dir = copy_unit_into_repo(None, "9453-ancla-dry-run")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        before = mandate.read_bytes()

        dry = run(["write", "--unidad", str(unit_dir), "--dry-run"])
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(mandate.read_bytes(), before, "el dry-run no escribe")

        real = run(["write", "--unidad", str(unit_dir)])
        self.assertEqual(real.returncode, 0, real.stderr)
        section = sections(mandate.read_text(encoding="utf-8"))[
            mandate_anchor.ANCHOR_SECTION]
        written = "\n".join(line for line in section.splitlines() if line.strip())
        expected = f"{mandate_anchor.ANCHOR_SECTION}\n\n{written}"
        self.assertEqual(dry.stdout.strip("\n"), expected)

    def test_seccion_valida_sin_force_dry_run_no_imprime_nada(self) -> None:
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        completed = run(["write", "--unidad", str(unit_dir), "--dry-run"])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout, "")

    def test_seccion_valida_force_dry_run_tiene_oraculo_igual_que_ausente(self) -> None:
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        head = run_git(repo, "rev-parse", "HEAD")

        dry = run(["write", "--unidad", str(unit_dir), "--force", "--dry-run"])
        self.assertEqual(dry.returncode, 0, dry.stderr)
        self.assertEqual(mandate_anchor.read_anchor(
            mandate.read_text(encoding="utf-8")), FULL_HEX_A, "el dry-run no escribe")

        real = run(["write", "--unidad", str(unit_dir), "--force"])
        self.assertEqual(real.returncode, 0, real.stderr)
        self.assertEqual(dry.stdout.strip("\n"),
                         f"{mandate_anchor.ANCHOR_SECTION}\n\ncommit: {head}")


# ======================================================================================
# CA-11b/CA-11c: idempotency and preconditions
# ======================================================================================

class PreconditionTests(unittest.TestCase):

    def test_segunda_escritura_es_idempotente(self) -> None:
        repo, unit_dir = copy_unit_into_repo(None, "9454-ancla-idempo")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        run(["write", "--unidad", str(unit_dir)])
        before = mandate.read_bytes()
        completed = run(["write", "--unidad", str(unit_dir)])
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(mandate.read_bytes(), before)

    def test_mandato_inexistente_sale_1_en_los_dos_subcomandos(self) -> None:
        tmp = Path(tempfile.mkdtemp(prefix="0114-anchor-falta-"))
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        for subcommand in ("write", "resolve"):
            with self.subTest(subcommand=subcommand):
                completed = run([subcommand, "--unidad", str(tmp / "no-existe")])
                self.assertEqual(completed.returncode, 1, completed.stderr)

    def test_commit_invalido_sale_4_sin_escribir(self) -> None:
        repo, unit_dir = copy_unit_into_repo(None, "9455-ancla-commit-malo")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        mandate = unit_dir / "mandato.md"
        before = mandate.read_bytes()
        completed = run(["write", "--unidad", str(unit_dir),
                         "--commit", "esto-no-es-un-sha"])
        self.assertEqual(completed.returncode, 4, completed.stdout)
        self.assertEqual(mandate.read_bytes(), before)
        self.assertEqual(run_git(repo, "status", "--porcelain"), "")

    def test_commit_invalido_sobre_seccion_ya_valida_ni_se_evalua(self) -> None:
        """Idempotencia primero (D-29/CA-11b): si la sección ya es válida y no hay
        `--force`, el script no necesita resolver `--commit` — ni siquiera uno
        inválido rompe el no-op."""
        repo, unit_dir = build_repo("valida", "9441-ancla-valida")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        completed = run(["write", "--unidad", str(unit_dir),
                         "--commit", "esto-no-es-un-sha"])
        self.assertEqual(completed.returncode, 0, completed.stderr)


# ======================================================================================
# D-17: `write` never changes `--hash` (the decisive check for a signed mandate)
# ======================================================================================

class HashPreservationTests(unittest.TestCase):

    def _hash(self, args: list[str]) -> str:
        completed = subprocess.run([sys.executable, str(VALIDATOR), *args, "--hash"],
                                   capture_output=True, text=True, check=True)
        return completed.stdout.strip()

    def _codes(self, args: list[str]) -> list[str]:
        completed = subprocess.run([sys.executable, str(VALIDATOR), *args],
                                   capture_output=True, text=True, check=False)
        return codes_from_output(completed.stdout)

    def test_forma_unidad_hash_y_codigos_intactos(self) -> None:
        repo, unit_dir = copy_unit_into_repo(None, "9456-ancla-hash-unidad")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        args = ["--unidad", str(unit_dir)]
        hash_before = self._hash(args)
        codes_before = self._codes(args)

        completed = run(["write"] + args)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        hash_after = self._hash(args)
        codes_after = self._codes(args)
        self.assertEqual(hash_before, hash_after)
        self.assertEqual(codes_before, codes_after)

    def test_forma_plan_hash_y_codigos_intactos(self) -> None:
        repo, plan_dir = copy_plan_into_repo(
            None, "plan-parado-retoma-incompleta")
        self.addCleanup(shutil.rmtree, repo, ignore_errors=True)
        args = ["--plan", str(plan_dir)]
        hash_before = self._hash(args)
        codes_before = self._codes(args)

        completed = run(["write"] + args)
        self.assertEqual(completed.returncode, 0, completed.stderr)

        hash_after = self._hash(args)
        codes_after = self._codes(args)
        self.assertEqual(hash_before, hash_after)
        self.assertEqual(codes_before, codes_after)


# ======================================================================================
# CA-31: `validate-supervised.sh` Paso 9 (D-19) — additivity of `_plan-maestro.md`
# ======================================================================================

class ValidateSupervisedBaseTests(unittest.TestCase):
    """`ROOT` of `validate-supervised.sh` is computed from the script's own location
    and the script `cd`s into it — it always operates on whatever repo the copy lives
    in (D-19). Older revisions of this test diffed two frozen commits of the real repo,
    but the real `.spec/units/_tanda-*.md` files those commits captured have since been
    deleted by ordinary repo evolution (tandas that closed and were cleaned up) — a
    genuine passage-of-time rot unrelated to `modo` vocabulary, discovered while
    reworking this suite. A throwaway repo with its own two-commit history does not
    rot: it seeds exactly the additive/non-additive cases Paso 9 discriminates."""

    def setUp(self) -> None:
        self.repo = new_repo("0114-validate-supervised-")
        self.addCleanup(shutil.rmtree, self.repo, ignore_errors=True)
        self.scripts = self.repo / ".spec" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("validate-supervised.sh", "_common.sh", "validate_mandate.py", "_common.py"):
            shutil.copy(SCRIPTS / name, self.scripts / name)
        self.script = self.scripts / "validate-supervised.sh"
        units = self.repo / ".spec" / "units"
        units.mkdir(parents=True)
        self.index = units / "_plan-maestro.md"
        # Deliberately not `- entrada 1` (a markdown bullet): the diff marker
        # `-` prefixed to a line that already starts with `-` produces `--`,
        # which the script's own `grep -E '^-[^-]'` (excluding the `---`
        # file-header line of a unified diff) then also excludes — a
        # pre-existing quirk of the unmodified script, not something this
        # seed exists to exercise.
        self.index.write_text("# Índice\n\nentrada 1\n", encoding="utf-8")
        commit_all(self.repo, "seed: índice con una entrada")
        self.base_commit = run_git(self.repo, "rev-parse", "HEAD")

    def _run(self) -> subprocess.CompletedProcess:
        env = {**__import__("os").environ, "VALIDAR_BASE": self.base_commit,
              "PYTHONDONTWRITEBYTECODE": "1"}
        return subprocess.run(["bash", str(self.script)],
                              capture_output=True, text=True, check=False,
                              cwd=str(self.repo), env=env)

    def test_pure_addition_does_not_fail_at_paso_9(self) -> None:
        self.index.write_text(
            self.index.read_text(encoding="utf-8") + "entrada 2\n", encoding="utf-8"
        )
        completed = self._run()
        self.assertNotIn("FAIL — Paso 9", completed.stderr)
        self.assertIn("=== Paso 9 —", completed.stdout)

    def test_rewritten_line_fails_at_paso_9(self) -> None:
        self.index.write_text("# Índice\n\nentrada 1 editada\n", encoding="utf-8")
        completed = self._run()
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("FAIL — Paso 9", completed.stderr)

    def test_missing_validar_base_fails_explicitly(self) -> None:
        env = {**__import__("os").environ, "PYTHONDONTWRITEBYTECODE": "1"}
        env.pop("VALIDAR_BASE", None)
        completed = subprocess.run(["bash", str(self.script)],
                                   capture_output=True, text=True, check=False,
                                   cwd=str(self.repo), env=env)
        self.assertEqual(completed.returncode, 1, completed.stdout)
        self.assertIn("FAIL — Paso 9", completed.stderr)
        self.assertIn("VALIDAR_BASE", completed.stderr)


# ======================================================================================
# Header contract (CA-10)
# ======================================================================================

class HeaderTests(unittest.TestCase):

    def test_la_cabecera_documenta_los_dos_subcomandos_y_sus_codigos(self) -> None:
        header = mandate_anchor.__doc__ or ""
        for token in ("write", "resolve", "--commit", "--force", "--dry-run"):
            self.assertIn(token, header)
        for code in (0, 1, 2, 4):
            self.assertRegex(header, rf"(?m)^\s+{code}\s")


if __name__ == "__main__":
    unittest.main()
