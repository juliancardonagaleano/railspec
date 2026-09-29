"""`supervised_parallel.py` / `worktree_registry.py` — CA-17..CA-22 (unit 0118, G4).

Reuses, rather than reimplementing, the temporary-git-repository helpers every
0114/0118 git-backed test already shares (`test_report_git_sync.new_repo` /
`run_git` / `commit_all` / `write`) and the synthetic-`claude`-stub pattern of
`test_supervised_conductor.py` (a small chmod'd Python script substituted via
`--claude-bin`).

What is stubbed vs. real, by design (see the task brief this group came
from):

* `claude -p` is ALWAYS a stub here — never a real `claude -p` invocation
  (out of scope for an automated test).
* `git worktree add`/`remove`/`list` are ALWAYS real, run against throwaway
  temporary git repositories created fresh per test and torn down in
  `tearDown`/`addCleanup` — never against this repository's own checkout or
  its `master` branch.
* `worktree_registry.py`'s file writes (`worktrees.json`, `paradas.md`) are
  ALWAYS real filesystem writes under a temporary directory.
* The end-to-end tests copy this repo's REAL `.spec/scripts/` into the
  fixture repo (so `supervised_parallel.py` launches the REAL
  `supervised_conductor.py`, unmodified, as its own subprocess inside each
  worktree — CA-17's actual reuse requirement) and run the REAL
  `supervised_parallel.py`/`supervised_conductor.py`/`validate_mandate.py` as
  subprocesses, exactly as a real invocation would.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import textwrap
import unittest
import unittest.mock as mock
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
PARALLEL_SCRIPT = SCRIPTS / "supervised_parallel.py"

sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import supervised_parallel as hop  # noqa: E402
import test_report_git_sync as sync_test  # noqa: E402
import worktree_registry as wr  # noqa: E402

run_git = sync_test.run_git
new_repo = sync_test.new_repo
commit_all = sync_test.commit_all
write_file = sync_test.write


# ======================================================================================
# Part 1 — pure functions, no git, no subprocess (chain parsing, cap reading)
# ======================================================================================

class ChainStageGroupsTests(unittest.TestCase):
    """CA-17/CA-18: sequential stages (`→`/`->`) vs. same-stage siblings (`/`)."""

    def test_sequential_and_parallel_stages(self) -> None:
        body = "Vigente: `0113 → 0114/0115 → 0116`\n"
        groups = hop.chain_stage_groups(body)
        self.assertEqual(groups, [["0113"], ["0114", "0115"], ["0116"]])

    def test_single_stage_all_parallel(self) -> None:
        body = "Vigente: `9910/9911`\n"
        self.assertEqual(hop.chain_stage_groups(body), [["9910", "9911"]])

    def test_no_chain_line_returns_empty(self) -> None:
        self.assertEqual(hop.chain_stage_groups("Sin cadena declarada.\n"), [])


class _FakeMandate:
    """Minimal stand-in exposing only what `parallelism_cap` reads (`.body`)."""

    def __init__(self, sections: dict[str, str]) -> None:
        self._sections = sections

    def body(self, anchor: str) -> str:
        return self._sections.get(anchor, "")


class EligibleUnitsFailClosedTests(unittest.TestCase):
    """Gate 0118/G4, hallazgo L2 media: a chain token that never resolves to a real
    unit directory must NOT make its stage look "done" by omission — the dependent
    unit of a later stage stays ineligible until the predecessor is real and `done`,
    not just until the tokens that happened to resolve are."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hop-eligible-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.units_root = self.tmp / "units"
        self.units_root.mkdir()

    def _write_unit(self, slug: str, fase: str) -> None:
        d = self.units_root / slug
        d.mkdir()
        (d / "_estado.yaml").write_text(f"id: {slug}\nfase: {fase}\n", encoding="utf-8")

    def _mandate(self, chain: str, members: list[str]) -> _FakeFullMandate:
        return _FakeFullMandate(self.units_root, chain, members)

    def test_unresolved_predecessor_token_blocks_dependent_unit(self) -> None:
        # Stage 1 cites `9920-ghost` (never created on disk) and `9921-real` (done).
        # Stage 2's `9922-dependent` must NOT be eligible: stage 1 is unverifiable,
        # not "done", even though the one predecessor that DID resolve is done.
        self._write_unit("9921-real", "done")
        self._write_unit("9922-dependent", "plan")
        mandate = self._mandate("9920/9921 → 9922", ["9921-real", "9922-dependent"])
        self.assertEqual(hop.eligible_units(mandate, {"worktrees": {}}), [])

    def test_fully_resolved_done_predecessor_stage_makes_dependent_eligible(self) -> None:
        # Control case: same shape, but every stage-1 token resolves and is done ->
        # the dependent unit IS eligible. Confirms the fix did not just always block.
        self._write_unit("9921-real", "done")
        self._write_unit("9923-real2", "done")
        self._write_unit("9922-dependent", "plan")
        mandate = self._mandate("9921/9923 → 9922", ["9921-real", "9923-real2", "9922-dependent"])
        self.assertEqual(hop.eligible_units(mandate, {"worktrees": {}}), ["9922-dependent"])


class _FakeFullMandate:
    """Minimal stand-in exposing what `eligible_units`/`refresh_board` read:
    `.body`, `.units_root`, `.members()`, `.path`, `.reference`."""

    def __init__(self, units_root: Path, chain: str, members: list[str],
                path: Path | None = None, reference: str = "fixture-plan") -> None:
        self.units_root = units_root
        self._chain = chain
        self._members = members
        self.path = path or (units_root.parent / "plan.md")
        self.reference = reference
        self.form = "plan"

    def body(self, anchor: str) -> str:
        if anchor == "## Cadena de dependencias":
            return f"Vigente: `{self._chain}`\n"
        return ""

    def members(self) -> list[str]:
        return list(self._members)


class ParallelismCapTests(unittest.TestCase):
    def test_reads_tope_worktrees_field(self) -> None:
        m = _FakeMandate({"## Paralelismo": "- tope-worktrees: 2\n- carriles: `sdd`\n"})
        self.assertEqual(hop.parallelism_cap(m, None), 2)

    def test_falls_back_to_default_when_missing(self) -> None:
        m = _FakeMandate({"## Paralelismo": "- carriles: `sdd`\n"})
        self.assertEqual(hop.parallelism_cap(m, None), hop.DEFAULT_CAP)

    def test_override_wins_over_declared_field(self) -> None:
        m = _FakeMandate({"## Paralelismo": "- tope-worktrees: 2\n"})
        self.assertEqual(hop.parallelism_cap(m, 1), 1)


# ======================================================================================
# Part 2 — CA-21/CA-22 (T19): the guard every subprocess call goes through.
# ======================================================================================

class GuardedSubprocessTests(unittest.TestCase):
    def test_blocks_docker_compose(self) -> None:
        with self.assertRaises(hop.ParallelError) as ctx:
            hop.guarded_subprocess(["docker-compose", "up", "-d"])
        self.assertEqual(ctx.exception.code, hop.EXIT_INTERNAL)

    def test_blocks_docker_compose_with_space(self) -> None:
        with self.assertRaises(hop.ParallelError):
            hop.guarded_subprocess(["docker", "compose", "restart"])

    def test_blocks_pce_reindex(self) -> None:
        with self.assertRaises(hop.ParallelError):
            hop.guarded_subprocess(["python3", "reindex_knowledge_router.py"])

    def test_blocks_pce_popen_too(self) -> None:
        with self.assertRaises(hop.ParallelError):
            hop.guarded_popen(["python3", "rebuild-graph.py"])

    def test_allows_an_ordinary_git_command(self) -> None:
        result = hop.guarded_subprocess(["git", "--version"], capture_output=True, text=True, check=True)
        self.assertEqual(result.returncode, 0)

    def test_worktree_registry_shares_the_same_guard(self) -> None:
        """Gate 0118/G4, hallazgo L3 media: `worktree_registry.git_worktree_list`
        must route through the SAME choke-point as `supervised_parallel.py`, not a
        second copy of the forbidden-pattern list — asserted structurally (same
        underlying `_common.guarded_subprocess`), not just by behavior, so a future
        divergence of the two lists fails this test even before it could matter."""
        import _common
        self.assertIs(wr.guarded_subprocess, _common.guarded_subprocess)
        with self.assertRaises(_common.GuardedCommandError):
            wr.guarded_subprocess(["docker-compose", "up", "-d"])


# ======================================================================================
# Part 3 — `worktree_registry.py`: board rendering and atomic persistence (pure
# filesystem, temp dir, no git).
# ======================================================================================

class ClassifyConditionTests(unittest.TestCase):
    def test_matches_by_leading_number(self) -> None:
        self.assertEqual(wr.classify_condition("12. algo raro"), (12, "No reconstruir la PCE con corridas en vuelo"))

    def test_matches_by_literal_name(self) -> None:
        self.assertEqual(
            wr.classify_condition("Gate escalado en fase codigo"),
            (7, "Gate escalado"),
        )

    def test_unmatched_falls_back_to_untypified(self) -> None:
        self.assertEqual(wr.classify_condition("algo que no está en la lista"), wr.UNTYPIFIED)


class RenderBoardTests(unittest.TestCase):
    def test_groups_by_condition_with_one_table_per_group(self) -> None:
        stops = [
            {"unidad": "9910-a", "disparador": "10. Único dueño del stack vivo",
             "causa": "intento de redeploy", "timestamp": "2026-09-21T10:00Z"},
            {"unidad": "9911-b", "disparador": "12. No reconstruir la PCE con corridas en vuelo",
             "causa": "reindex pedido", "timestamp": "2026-09-21T11:00Z"},
        ]
        text = wr.render_board(stops)
        self.assertIn("## 10. Único dueño del stack vivo", text)
        self.assertIn("## 12. No reconstruir la PCE con corridas en vuelo", text)
        self.assertIn("| 9910-a | 10. Único dueño del stack vivo | intento de redeploy | 2026-09-21T10:00Z |", text)
        self.assertNotIn("## 1. Cambio destructivo", text)  # empty groups are skipped

    def test_empty_stops_says_so(self) -> None:
        self.assertIn("Sin paradas activas.", wr.render_board([]))


class LatestStopForUnitTests(unittest.TestCase):
    """Gate 0118/G4, hallazgo L1 alta (sostenido por el refutador): the real parse
    path from a mandate's actual `## Paradas` text to a stop dict — not just
    `render_board`/`classify_condition` fed a hand-built `stops` list."""

    MANDATE_TEXT = """# Mandato

## Paradas

### 2026-09-21T08:00:00Z
- unidad: `9930-a`
- disparador: 10. Único dueño del stack vivo
- causa: intento de redeploy detectado
- desbloqueo-quien:

### 2026-09-21T09:30:00Z
- unidad: `9930-a`
- disparador: 7. Gate escalado
- causa: hallazgo alta sin resolver
- desbloqueo-quien: Julian Cardona Galeano
- desbloqueo-fecha: 2026-09-21T10:00:00Z
- desbloqueo-que: reabrió con nueva evidencia

### 2026-09-21T09:00:00Z
- unidad: `9931-b`
- disparador: 5. Tarea reservada a presencia humana
- causa: otra unidad, no debe aparecer para 9930-a
- desbloqueo-quien:

## Punto de retoma
"""

    def test_returns_latest_unresolved_entry_for_the_unit(self) -> None:
        stop = wr.latest_stop_for_unit(self.MANDATE_TEXT, "9930-a")
        self.assertIsNotNone(stop)
        self.assertEqual(stop["disparador"], "7. Gate escalado")
        self.assertEqual(stop["causa"], "hallazgo alta sin resolver")
        self.assertEqual(stop["fecha"], "2026-09-21T09:30:00Z")
        # Resolved (`desbloqueo-quien` set) -- `refresh_board` itself is what filters
        # a resolved stop out; `latest_stop_for_unit` just reports the latest entry,
        # resolved or not, unfiltered -- confirmed here so that contract is explicit.
        self.assertEqual(stop["desbloqueo-quien"], "Julian Cardona Galeano")

    def test_filters_by_unit_and_ignores_other_units_entries(self) -> None:
        stop = wr.latest_stop_for_unit(self.MANDATE_TEXT, "9931-b")
        self.assertIsNotNone(stop)
        self.assertEqual(stop["disparador"], "5. Tarea reservada a presencia humana")

    def test_unit_with_no_entries_returns_none(self) -> None:
        self.assertIsNone(wr.latest_stop_for_unit(self.MANDATE_TEXT, "9999-nadie"))


class RefreshBoardRealContentTests(unittest.TestCase):
    """Gate 0118/G4, hallazgo L1 alta (sostenido): the E2E test only asserted
    `board.is_file()` -- this exercises the real path a stop takes end to end:
    `refresh_board` reads the WORKTREE's own copy of the mandate (never the main
    tree's), calls `latest_stop_for_unit` for real, and only an UNRESOLVED stop
    (empty `desbloqueo-quien`) reaches `paradas.md` -- verified by content, not
    existence."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hop-board-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.units_root = self.tmp / "units"
        self.units_root.mkdir()
        (self.units_root / "9930-a").mkdir()
        (self.units_root / "9930-a" / "_estado.yaml").write_text(
            "id: 9930-a\nfase: implement\n", encoding="utf-8")

    def _write_mandate(self, text: str) -> Path:
        path = self.tmp / "plan.md"
        path.write_text(text, encoding="utf-8")
        return path

    def test_unresolved_real_stop_reaches_the_board(self) -> None:
        mandate_text = """# Mandato

## Paradas

### 2026-09-21T08:00:00Z
- unidad: `9930-a`
- disparador: 10. Único dueño del stack vivo
- causa: intento de redeploy detectado
- desbloqueo-quien:
"""
        mandate_path = self._write_mandate(mandate_text)
        mandate = _FakeFullMandate(self.units_root, "9930-a", ["9930-a"], path=mandate_path)
        config = hop.ParallelConfig(plan="fixture-plan", repo_root=self.tmp)
        board = self.tmp / "paradas.md"

        hop.refresh_board(config, mandate, {"worktrees": {}}, board)

        text = board.read_text(encoding="utf-8")
        self.assertIn("## 10. Único dueño del stack vivo", text)
        self.assertIn(
            "| 9930-a | 10. Único dueño del stack vivo | intento de redeploy detectado | "
            "2026-09-21T08:00:00Z |",
            text,
        )

    def test_resolved_real_stop_does_not_reach_the_board(self) -> None:
        mandate_text = """# Mandato

## Paradas

### 2026-09-21T08:00:00Z
- unidad: `9930-a`
- disparador: 10. Único dueño del stack vivo
- causa: intento de redeploy detectado
- desbloqueo-quien: Julian Cardona Galeano
- desbloqueo-fecha: 2026-09-21T09:00:00Z
- desbloqueo-que: confirmado seguro, reanudó
"""
        mandate_path = self._write_mandate(mandate_text)
        mandate = _FakeFullMandate(self.units_root, "9930-a", ["9930-a"], path=mandate_path)
        config = hop.ParallelConfig(plan="fixture-plan", repo_root=self.tmp)
        board = self.tmp / "paradas.md"

        hop.refresh_board(config, mandate, {"worktrees": {}}, board)

        text = board.read_text(encoding="utf-8")
        self.assertIn("Sin paradas activas.", text)
        self.assertNotIn("9930-a", text)


class RegistryPersistenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wr-test-")
        self.tmp = Path(self._tmp.name)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_roundtrip_and_no_leftover_tmp_files(self) -> None:
        path = self.tmp / "worktrees.json"
        data = wr.load(path)
        wr.set_entry(data, "9910-a", ruta="/x", rama="", fase="plan", pid="123",
                     sesion="s1", estado="activo")
        wr.save(path, data)

        reloaded = wr.load(path)
        self.assertEqual(reloaded["worktrees"]["9910-a"]["estado"], "activo")
        self.assertEqual(reloaded["worktrees"]["9910-a"]["pid"], "123")
        leftovers = [p for p in self.tmp.iterdir() if p.name != "worktrees.json"]
        self.assertEqual(leftovers, [], f"quedaron archivos temporales: {leftovers}")

    def test_write_board_is_atomic_and_readable(self) -> None:
        path = self.tmp / "paradas.md"
        wr.write_board(path, [{"unidad": "9910-a", "disparador": "7. Gate escalado",
                               "causa": "c", "timestamp": "t"}])
        text = path.read_text(encoding="utf-8")
        self.assertIn("## 7. Gate escalado", text)
        leftovers = [p for p in self.tmp.iterdir() if p.name != "paradas.md"]
        self.assertEqual(leftovers, [])

    def test_load_of_missing_file_is_empty_registry(self) -> None:
        data = wr.load(self.tmp / "does-not-exist.json")
        self.assertEqual(data["worktrees"], {})


# ======================================================================================
# Part 4 — reconciliation against REAL `git worktree list` (real repo, real
# worktrees, temporary directories only, always torn down).
# ======================================================================================

class ReconciliationRealGitTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = new_repo("0118-wr-reconcile-")
        self.addCleanup(self._cleanup_repo)
        write_file(self.repo / "README.md", "fixture\n")
        commit_all(self.repo, "seed")
        self._worktrees: list[Path] = []

    def _cleanup_repo(self) -> None:
        for path in self._worktrees:
            subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", str(path)],
                           capture_output=True, text=True, check=False)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "prune"],
                       capture_output=True, text=True, check=False)
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _add_worktree(self, path: Path) -> None:
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "--detach", str(path), "HEAD"],
                       capture_output=True, text=True, check=True)
        self._worktrees.append(path)

    def test_entry_without_real_worktree_becomes_orphan_not_deleted(self) -> None:
        scoped_root = self.repo / "runtime" / "worktrees"
        wt_path = scoped_root / "9910-a"
        self._add_worktree(wt_path)
        data = {"worktrees": {"9910-a": {"ruta": str(wt_path), "estado": "activo"}}}

        # Retire the worktree for real, out from under the registry.
        subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", str(wt_path)],
                       capture_output=True, text=True, check=True)
        self._worktrees.remove(wt_path)

        real = wr.git_worktree_list(self.repo)
        report = wr.reconcile(data, real, scoped_root)
        self.assertEqual(data["worktrees"]["9910-a"]["estado"], "huerfano")
        self.assertTrue(any("huerfano" in line for line in report))
        # Never deleted on its own (plan.md § Decisiones de diseño).
        self.assertIn("9910-a", data["worktrees"])

    def test_real_worktree_without_entry_is_added_as_orphan_and_scoped(self) -> None:
        scoped_root = self.repo / "runtime" / "worktrees"
        inside = scoped_root / "9911-b"
        outside = self.repo.parent / f"outside-{self.repo.name}"
        self._add_worktree(inside)
        self._add_worktree(outside)

        data = {"worktrees": {}}
        real = wr.git_worktree_list(self.repo)
        report = wr.reconcile(data, real, scoped_root)

        self.assertIn("9911-b", data["worktrees"])
        self.assertEqual(data["worktrees"]["9911-b"]["estado"], "huerfano")
        # The worktree outside this mandate's own root is not this registry's
        # business -- never added.
        self.assertEqual(len(data["worktrees"]), 1)
        self.assertTrue(any("9911-b" in line and "añadido" in line for line in report))


# ======================================================================================
# Part 5 — end to end: real git worktrees, real `supervised_parallel.py`, real
# `supervised_conductor.py` (unmodified, launched as this script's own
# subprocess), a stubbed `claude`.
# ======================================================================================

PLAN_TEMPLATE = """# Mandato supervised — fixture de prueba (0118, G4)

## Estado

- estado: aprobado

## Instancia en curso

Sin instancia.

## Objetivo y criterio de salida

Fixture de prueba, unidad 0118 G4.

## Unidades miembro

{miembros}

## Cadena de dependencias

Vigente: `{cadena}`

## Paralelismo

- carriles: `sdd`
- tope-worktrees: {tope}
- dueno-stack-vivo: Test
- presupuesto-mcp: n/a
- conducta-presupuesto-agotado: parar

## Paradas

Sin paradas.

## Punto de retoma

Sin entradas.

## Aprobación

Vacía.
"""

UNIT_ESTADO_TEMPLATE = """id: {slug}
titulo: "Fixture ({slug})"
dueño: "Test"
carril: "sdd"
fase: {fase}
estado: en-progreso
creado: "2026-09-20T00:00:00Z"
actualizado: "2026-09-20T00:00:00Z"
governance_refs: []
comando_validacion: "true"
modo: supervisado
mandato: {plan_id}
riesgo: bajo
gates: {{}}
validaciones_mandato: []
"""

STUB_SOURCE = textwrap.dedent("""\
    #!/usr/bin/env python3
    # Synthetic `claude` stand-in (unit 0118, G4 tests): sleeps briefly (so the
    # parent's cap-enforcement has time to observe a real "at capacity" cycle),
    # then flips its OWN unit's `fase` to `done` in one step -- `HOP_UNIT_DIR`
    # is what `supervised_parallel.py` always sets for the conductor child it
    # launches, precisely so several parallel stub instances (one per
    # worktree, same process environment otherwise) never collide on a single
    # shared file.
    import os
    import time
    import sys

    unit_dir = os.environ["HOP_UNIT_DIR"]
    sleep_s = float(os.environ.get("HOP_STUB_SLEEP", "1.2"))
    time.sleep(sleep_s)
    estado_path = os.path.join(unit_dir, "_estado.yaml")
    with open(estado_path, "r", encoding="utf-8") as fh:
        lines = fh.read().splitlines()
    out = []
    for line in lines:
        out.append("fase: done" if line.startswith("fase:") else line)
    with open(estado_path, "w", encoding="utf-8") as fh:
        fh.write("\\n".join(out) + "\\n")
    sys.exit(0)
""")


def make_stub(tmp: Path) -> Path:
    stub = tmp / "claude-stub.py"
    stub.write_text(STUB_SOURCE, encoding="utf-8")
    stub.chmod(stub.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return stub


def build_fixture_repo(tmp: Path, plan_id: str, slugs: list[str], *, chain: str, cap: int) -> Path:
    """A real git repo, real `.spec/planes/<plan_id>/plan.md`, real
    `.spec/units/<slug>/_estado.yaml` per member, and a REAL copy of this
    repository's own `.spec/scripts/` -- so every script the fixture launches
    (`supervised_parallel.py`, `supervised_conductor.py`, `validate_mandate.py`,
    `instance_lock.py`, `worktree_registry.py`) is the genuine, unmodified
    article, each with its own `ROOT` correctly recomputed from ITS OWN file
    location inside this fixture (`validate_mandate.py`'s `ROOT =
    Path(__file__).resolve().parents[2]`) -- required for the `plan`-id
    resolution branch to find this fixture's own `.spec/planes/...`, not the
    real repo's."""
    import shutil

    repo = new_repo(f"0118-parallel-{plan_id}-")
    shutil.copytree(SCRIPTS, repo / ".spec" / "scripts",
                    ignore=shutil.ignore_patterns("__pycache__", "tests"))
    miembros = "\n".join(f"- `{slug}`" for slug in slugs)
    write_file(repo / ".spec" / "planes" / plan_id / "plan.md",
              PLAN_TEMPLATE.format(miembros=miembros, cadena=chain, tope=cap))
    for slug in slugs:
        write_file(repo / ".spec" / "units" / slug / "_estado.yaml",
                  UNIT_ESTADO_TEMPLATE.format(slug=slug, fase="plan", plan_id=plan_id))
    commit_all(repo, "seed")
    return repo


class ParallelEndToEndTestCase(unittest.TestCase):
    """Every worktree this test creates lives under a fresh `tempfile.mkdtemp`
    fixture repo (never this checkout's own `master`) and is removed in
    `tearDown`, even on failure (`addCleanup`)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hop-e2e-")
        self.tmp = Path(self._tmp.name)
        self.stub = make_stub(self.tmp)

    def tearDown(self) -> None:
        repo = getattr(self, "repo", None)
        if repo is not None:
            subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                           capture_output=True, text=True, check=False)
            result = subprocess.run(["git", "-C", str(repo), "worktree", "list", "--porcelain"],
                                    capture_output=True, text=True, check=False)
            for line in result.stdout.splitlines():
                if line.startswith("worktree "):
                    wt_path = line[len("worktree "):]
                    if wt_path.rstrip("/") == str(repo).rstrip("/"):
                        continue
                    subprocess.run(["git", "-C", str(repo), "worktree", "remove", "--force", wt_path],
                                   capture_output=True, text=True, check=False)
            subprocess.run(["git", "-C", str(repo), "worktree", "prune"],
                           capture_output=True, text=True, check=False)
        self._tmp.cleanup()

    def _run_parallel(self, plan_id: str, *, extra_args: list[str], env: dict[str, str] | None = None,
                      timeout: int = 90) -> subprocess.CompletedProcess:
        parallel_script = self.repo / ".spec" / "scripts" / "supervised_parallel.py"
        args = [sys.executable, str(parallel_script), "run", "--plan", plan_id,
               "--repo-root", str(self.repo), "--claude-bin", str(self.stub),
               "--poll-seconds", "0.2", "--max-ciclos", "400", *extra_args]
        run_env = dict(os.environ)
        if env:
            run_env.update(env)
        return subprocess.run(args, capture_output=True, text=True, check=False,
                              env=run_env, timeout=timeout)

    def test_cap_respected_and_extra_unit_serialized(self) -> None:
        """CA-17/CA-18: two units of the SAME chain stage (both eligible at
        once), `tope-worktrees: 1` -- only one worktree at a time; the other
        is serialized, logged as such, and picked up once the slot frees."""
        plan_id = "e2e-cap"
        slugs = ["9910-unidad-a", "9911-unidad-b"]
        self.repo = build_fixture_repo(self.tmp, plan_id, slugs, chain="/".join(slugs), cap=1)
        evidencia = self.tmp / "evidencia"

        result = self._run_parallel(
            plan_id, extra_args=["--evidencia-dir", str(evidencia)],
            env={"HOP_STUB_SLEEP": "1.5"},
        )
        self.assertEqual(result.returncode, hop.EXIT_OK, result.stderr)

        log_text = (evidencia / "parallel.log").read_text(encoding="utf-8")
        self.assertIn("tope-worktrees=1 alcanzado -- serializadas:", log_text,
                      f"no se observó serialización en el log:\n{log_text}")

        registry = wr.load(wr.registry_path(self.repo / ".spec" / "scripts", plan_id))
        for slug in slugs:
            entry = registry["worktrees"][slug]
            self.assertEqual(entry["estado"], "retirado", entry)
            self.assertEqual(entry["fase"], "done", entry)
            # `supervised_conductor.py::mandate_closed`, for a PLAN-member unit
            # (`--unidad` resolving `mandato: <plan-id>`), checks the WHOLE
            # plan's own `## Estado`, not the one member's `fase` -- by
            # design, one member reaching `done` never closes the plan on its
            # own. So the conductor keeps going for a second step, finds no
            # further advance (`avanzo: no`), and stops with `motivo: retoma
            # sin punto verificado ... no avanzó` (rc=1) -- a real, typified
            # stop, not an error. What matters for CA-17/CA-18 here is that
            # the worktree's own file really reached `fase: done` before that
            # stop (asserted above) and that `supervised_parallel.py` recorded
            # it and did not try to relaunch this unit again.
            self.assertEqual(entry["ultimo-rc"], "1", entry)

        # No worktree left mounted (both retired and removed).
        leftover = subprocess.run(["git", "-C", str(self.repo), "worktree", "list", "--porcelain"],
                                  capture_output=True, text=True, check=True)
        for slug in slugs:
            self.assertNotIn(slug, leftover.stdout)

        board = wr.board_path(self.repo / ".spec" / "scripts", plan_id)
        self.assertTrue(board.is_file())

    def test_lock_busy_on_main_tree_stops_without_launching(self) -> None:
        """CA-13/CA-17: the parent only ever `inspect`s the main tree's lock --
        occupied there means no NEW worktree launches this run."""
        plan_id = "e2e-lock-busy"
        slugs = ["9912-unidad-c"]
        self.repo = build_fixture_repo(self.tmp, plan_id, slugs, chain=slugs[0], cap=1)
        instance_lock = self.repo / ".spec" / "scripts" / "instance_lock.py"
        mandate_path = self.repo / ".spec" / "planes" / plan_id / "plan.md"
        acquire = subprocess.run(
            [sys.executable, str(instance_lock), "acquire", str(mandate_path),
             "--session", "otra-sesion", "--launcher", "Otro"],
            capture_output=True, text=True, check=False)
        self.assertEqual(acquire.returncode, 0, acquire.stderr)

        evidencia = self.tmp / "evidencia"
        result = self._run_parallel(plan_id, extra_args=["--evidencia-dir", str(evidencia)])
        self.assertEqual(result.returncode, hop.EXIT_LOCK_BUSY_AT_START, result.stderr)

        registry = wr.load(wr.registry_path(self.repo / ".spec" / "scripts", plan_id))
        self.assertEqual(registry["worktrees"], {})
        leftover = subprocess.run(["git", "-C", str(self.repo), "worktree", "list", "--porcelain"],
                                  capture_output=True, text=True, check=True)
        self.assertNotIn(slugs[0], leftover.stdout)


# ======================================================================================
# Part 6 — lock re-check granularity (gate 0118/G4 codigo, iteración 2, hallazgo L1
# media): the lock must be re-consulted before EACH individual launch, not once per
# cycle -- in-process, `hop.run()` driven directly with `_launch`/`eligible_units`/
# `lock_status`/`vm` mocked out, since reproducing a lock that turns busy BETWEEN two
# launches of the very same cycle deterministically through real subprocesses would be
# flaky by construction (a timing race is exactly what is being tested).
# ======================================================================================

class LockRecheckPerLaunchTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repo = new_repo("0118-lockcheck-")
        self.addCleanup(self._cleanup)
        write_file(self.repo / "README.md", "fixture\n")
        commit_all(self.repo, "seed")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_lock_gone_busy_between_two_launches_of_the_same_cycle_stops_the_second(self) -> None:
        mandate = _FakeFullMandate(self.repo / "units", "u1/u2", ["u1", "u2"],
                                   path=self.repo / "plan.md")
        config = hop.ParallelConfig(
            plan="fixture-plan", repo_root=self.repo,
            evidencia_dir=self.repo / "evidencia", tope_worktrees=2,
            max_cycles=1, poll_seconds=0.01,
        )

        lock_calls = {"n": 0}

        def fake_lock_status(_path):
            # Call 1 is `run()`'s pre-loop check (CA-13, unchanged -- must read
            # free so the run actually reaches the loop); call 2 is u1's per-launch
            # check (also free -- u1 launches); call 3+ (u2's per-launch check, and
            # any later re-check) reads busy -- exactly the "turned busy mid-cycle"
            # race a per-cycle-only check would have missed.
            lock_calls["n"] += 1
            return ("libre", 0) if lock_calls["n"] <= 2 else ("ocupado", 0)

        launched: list[str] = []

        class _FakeProc:
            def poll(self):
                return None

        def fake_launch(config, mandate, slug, wtroot, data, running, reg_path, evidencia_dir):
            launched.append(slug)
            running[slug] = _FakeProc()

        with mock.patch.object(hop, "vm") as fake_vm, \
             mock.patch.object(hop, "lock_status", side_effect=fake_lock_status), \
             mock.patch.object(hop, "eligible_units", return_value=["u1", "u2"]), \
             mock.patch.object(hop, "_launch", side_effect=fake_launch):
            fake_vm.resolve_plan.return_value = mandate

            rc = hop.run(config)

        self.assertEqual(rc, hop.EXIT_OK)
        # u1 launched (lock was free); u2 did NOT (lock had turned busy) -- proof the
        # re-check happens before EACH launch, not once for the whole cycle.
        self.assertEqual(launched, ["u1"])
        self.assertGreaterEqual(lock_calls["n"], 2)


# ======================================================================================
# Part 7 — worktree-orchestrator DAG/merge unit: token-level DAG, waves, seed-commit,
# serialized merge, CA-10 hybrid re-gate, conflict handling, and the isolation/cap
# guarantees a `"fusionando"` unit must preserve.
# ======================================================================================

class DependencyDagTests(unittest.TestCase):
    """CA-01/CA-02/CA-03/CA-04/CA-05: token-level DAG, generalizing
    `chain_stage_groups` without touching it (that function stays the default source)."""

    def test_default_reproduces_stage_union_exactly(self) -> None:
        body = "Vigente: `0113 → 0114/0115 → 0116`\n"
        dag = hop.dependency_dag(body)
        self.assertEqual(dag, {
            "0113": set(),
            "0114": {"0113"},
            "0115": {"0113"},
            "0116": {"0113", "0114", "0115"},
        })

    def test_override_replaces_default_predecessors_not_adds(self) -> None:
        body = "Vigente: `0113 → 0114/0115 → 0116(<-0113,0114)`\n"
        dag = hop.dependency_dag(body)
        self.assertEqual(dag["0116"], {"0113", "0114"})  # NOT 0115

    def test_override_citing_unresolved_id_is_fail_closed(self) -> None:
        body = "Vigente: `0113 → 0116(<-9999)`\n"
        dag = hop.dependency_dag(body)
        (pred,) = dag["0116"]
        self.assertTrue(pred.startswith(hop._UNRESOLVED_PREFIX))
        self.assertIn("9999", pred)

    def test_override_citing_a_same_or_later_stage_id_is_also_fail_closed(self) -> None:
        # `0116` may only cite ids from an EARLIER stage of the SAME chain -- citing
        # `0117` (its own successor) must never create a real edge.
        body = "Vigente: `0113 → 0116(<-0117) → 0117`\n"
        dag = hop.dependency_dag(body)
        (pred,) = dag["0116"]
        self.assertTrue(pred.startswith(hop._UNRESOLVED_PREFIX))

    def test_no_chain_line_returns_empty_dag(self) -> None:
        self.assertEqual(hop.dependency_dag("Sin cadena declarada.\n"), {})


class DagWavesTests(unittest.TestCase):
    """CA-02/CA-04: waves generalize stages -- an override can move an id into an
    EARLIER wave than the plain chain would place it."""

    def test_waves_match_stages_for_the_default_linear_case(self) -> None:
        body = "Vigente: `0113 → 0114/0115 → 0116`\n"
        dag = hop.dependency_dag(body)
        self.assertEqual(hop.dag_waves(dag), [["0113"], ["0114", "0115"], ["0116"]])

    def test_override_moves_a_later_stage_id_into_an_earlier_wave(self) -> None:
        body = "Vigente: `0113 → 0114 → 0115 → 0116(<-0113)`\n"
        dag = hop.dependency_dag(body)
        self.assertEqual(hop.dag_waves(dag), [["0113"], ["0114", "0116"], ["0115"]])


class EligibleUnitsNonLinearTests(unittest.TestCase):
    """CA-04/CA-05 at the `eligible_units` level: a non-linear override unlocks a
    dependent strictly earlier than the plain chain would (its real predecessor set
    is smaller), and an unresolved override keeps it blocked forever."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hop-nonlinear-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.units_root = self.tmp / "units"
        self.units_root.mkdir()

    def _write_unit(self, slug: str, fase: str) -> None:
        d = self.units_root / slug
        d.mkdir()
        (d / "_estado.yaml").write_text(f"id: {slug}\nfase: {fase}\n", encoding="utf-8")

    def test_override_unlocks_dependent_without_waiting_on_the_untouched_stage(self) -> None:
        self._write_unit("9925-a", "done")
        self._write_unit("9926-b", "plan")     # NOT done -- default chain would block 9928
        self._write_unit("9927-c", "plan")
        self._write_unit("9928-d", "plan")
        mandate = _FakeFullMandate(
            self.units_root, "9925/9926 → 9927 → 9928(<-9925)",
            ["9925-a", "9926-b", "9927-c", "9928-d"])
        result = hop.eligible_units(mandate, {"worktrees": {}})
        # 9928 only needs 9925 (done) per its override -- eligible even though 9926
        # (its DEFAULT stage-mate) is not done and 9927 is not even launched.
        self.assertIn("9928-d", result)
        self.assertNotIn("9927-c", result)  # 9927 still needs the default stage fully done

    def test_unresolved_override_keeps_dependent_ineligible_forever(self) -> None:
        self._write_unit("9925-a", "done")
        self._write_unit("9928-d", "plan")
        mandate = _FakeFullMandate(self.units_root, "9925 → 9928(<-9999)", ["9925-a", "9928-d"])
        self.assertEqual(hop.eligible_units(mandate, {"worktrees": {}}), [])


class SeedCommitCaptureTests(unittest.TestCase):
    """CA-06/CA-07, isolated from the conductor child: `_launch` captures the main
    tree's real HEAD only on an actual worktree creation, never on reuse.
    `create_worktree`/`guarded_popen` are mocked out (no real `git worktree add`, no
    real conductor child) and `hop.SCRIPTS` is patched to a path under the fixture
    repo, so this test never writes into this checkout's own `.supervised-runtime/`."""

    def setUp(self) -> None:
        self.repo = new_repo("hop-seed-")
        self.addCleanup(self._cleanup)
        write_file(self.repo / "README.md", "seed\n")
        commit_all(self.repo, "seed")
        self.units_root = self.repo / "units"
        (self.units_root / "9940-a").mkdir(parents=True)
        (self.units_root / "9940-a" / "_estado.yaml").write_text(
            "id: 9940-a\nfase: plan\n", encoding="utf-8")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    class _FakeProc:
        pid = 4242

        def poll(self):
            return None

    def _launch_isolated(self, mandate, config, wtroot, data, reg_path, evidencia_dir):
        with mock.patch.object(hop, "SCRIPTS", self.repo / "fake-scripts"), \
             mock.patch.object(hop, "create_worktree") as fake_create, \
             mock.patch.object(hop, "guarded_popen", return_value=self._FakeProc()):
            running: dict = {}
            hop._launch(config, mandate, "9940-a", wtroot, data, running, reg_path, evidencia_dir)
        return fake_create

    def test_seed_commit_matches_main_tree_head_at_creation(self) -> None:
        mandate = _FakeFullMandate(self.units_root, "9940-a", ["9940-a"], reference="fixture-seed")
        config = hop.ParallelConfig(plan="fixture-seed", repo_root=self.repo)
        wtroot = self.repo / "worktrees"
        data: dict = {"worktrees": {}}
        expected_head = run_git(self.repo, "rev-parse", "HEAD")

        fake_create = self._launch_isolated(mandate, config, wtroot, data,
                                            self.repo / "worktrees.json", self.repo / "evidencia")

        self.assertTrue(fake_create.called)
        self.assertEqual(data["worktrees"]["9940-a"]["seed-commit"], expected_head)
        # CA-07: verifiable against a real `git rev-parse HEAD` of the main tree.
        self.assertEqual(data["worktrees"]["9940-a"]["seed-commit"], run_git(self.repo, "rev-parse", "HEAD"))

    def test_seed_commit_not_re_seeded_on_reuse(self) -> None:
        mandate = _FakeFullMandate(self.units_root, "9940-a", ["9940-a"], reference="fixture-seed")
        config = hop.ParallelConfig(plan="fixture-seed", repo_root=self.repo)
        wtroot = self.repo / "worktrees"
        data: dict = {"worktrees": {}}
        reg_path = self.repo / "worktrees.json"
        evidencia_dir = self.repo / "evidencia"
        first_head = run_git(self.repo, "rev-parse", "HEAD")

        self._launch_isolated(mandate, config, wtroot, data, reg_path, evidencia_dir)
        self.assertEqual(data["worktrees"]["9940-a"]["seed-commit"], first_head)

        # The main tree advances, and the worktree path now exists (simulating an
        # already-mounted worktree) -- a re-seed would be observably different.
        (wtroot / "9940-a").mkdir(parents=True)
        write_file(self.repo / "advance.txt", "advance\n")
        commit_all(self.repo, "advance")
        second_head = run_git(self.repo, "rev-parse", "HEAD")
        self.assertNotEqual(second_head, first_head)

        fake_create2 = self._launch_isolated(mandate, config, wtroot, data, reg_path, evidencia_dir)

        fake_create2.assert_not_called()
        self.assertEqual(data["worktrees"]["9940-a"]["seed-commit"], first_head)


class FusionandoCapAndRelaunchTests(unittest.TestCase):
    """CA-11: a `"fusionando"` unit occupies neither a NEW `eligible_units` launch
    slot (never relaunched -- it already finished, only its merge is pending) nor the
    active-worktree cap (`active_count` counts only `estado == "activo"`, unaffected
    by this unit's extended life)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="hop-fusionando-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.units_root = self.tmp / "units"
        self.units_root.mkdir()

    def _write_unit(self, slug: str, fase: str) -> None:
        d = self.units_root / slug
        d.mkdir()
        (d / "_estado.yaml").write_text(f"id: {slug}\nfase: {fase}\n", encoding="utf-8")

    def test_fusionando_unit_is_never_relaunched(self) -> None:
        self._write_unit("9970-x", "plan")
        mandate = _FakeFullMandate(self.units_root, "9970-x", ["9970-x"])
        registry = {"worktrees": {"9970-x": {"estado": "fusionando"}}}
        self.assertEqual(hop.eligible_units(mandate, registry), [])

    def test_cap_math_ignores_fusionando_entries(self) -> None:
        registry = {"worktrees": {
            "9970-x": {"estado": "fusionando"},
            "9971-y": {"estado": "activo"},
        }}
        # Calls the real production function -- not a reimplementation of its
        # formula -- so a change to `_active_worktree_count` itself is what
        # this test actually exercises.
        self.assertEqual(hop._active_worktree_count(registry), 1)  # not 2


class ParadaArbolPrincipalBlocksLaunchTests(unittest.TestCase):
    """CA-08/T9: `run()` reads `parada-arbol-principal` from `worktrees.json` before
    launching anything new -- present means no NEW worktree launches this cycle, read
    only, never auto-cleared by `run()` itself."""

    def setUp(self) -> None:
        self.repo = new_repo("hop-parada-arbol-")
        self.addCleanup(self._cleanup)
        write_file(self.repo / "README.md", "fixture\n")
        commit_all(self.repo, "seed")

    def _cleanup(self) -> None:
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def test_no_new_launch_while_parada_arbol_principal_is_present(self) -> None:
        mandate = _FakeFullMandate(self.repo / "units", "u1", ["u1"], path=self.repo / "plan.md")
        config = hop.ParallelConfig(
            plan="fixture-plan", repo_root=self.repo, evidencia_dir=self.repo / "evidencia",
            tope_worktrees=2, max_cycles=1, poll_seconds=0.01,
        )
        fake_scripts = self.repo / "fake-scripts"
        reg_path = wr.registry_path(fake_scripts, mandate.reference)
        data = wr.load(reg_path)
        data["parada-arbol-principal"] = {"codigo": "UNTYPIFIED", "causa": "x", "detectado-en": "t"}
        wr.save(reg_path, data)

        launched: list[str] = []

        def fake_launch(config, mandate, slug, wtroot, data, running, reg_path, evidencia_dir):
            launched.append(slug)

        with mock.patch.object(hop, "SCRIPTS", fake_scripts), \
             mock.patch.object(hop, "vm") as fake_vm, \
             mock.patch.object(hop, "lock_status", return_value=("libre", 0)), \
             mock.patch.object(hop, "eligible_units", return_value=["u1"]), \
             mock.patch.object(hop, "_launch", side_effect=fake_launch), \
             mock.patch.object(hop, "_drain_merges"):
            fake_vm.resolve_plan.return_value = mandate
            rc = hop.run(config)

        self.assertEqual(rc, hop.EXIT_OK)
        self.assertEqual(launched, [])  # never launched, despite an "eligible" unit


class DrainMergesTests(unittest.TestCase):
    """CA-08/CA-09/CA-10/CA-12/CA-13: `_drain_merges` against REAL git worktrees of a
    disposable temporary repository -- never this checkout's own. `lock_status` is
    mocked free throughout (its own per-attempt granularity is
    `LockRecheckPerLaunchTests`'s job); everything else -- `git merge`,
    `git merge --abort`, `git diff`, `git show` -- runs for real."""

    def setUp(self) -> None:
        self.repo = new_repo("hop-drain-")
        self.addCleanup(self._cleanup)
        write_file(self.repo / "README.md", "seed\n")
        commit_all(self.repo, "seed")
        self._worktrees: list[Path] = []
        self.units_root = self.repo / "units"
        self.units_root.mkdir()
        self.evidencia_dir = self.repo / "evidencia"

    def _cleanup(self) -> None:
        for path in self._worktrees:
            subprocess.run(["git", "-C", str(self.repo), "worktree", "remove", "--force", str(path)],
                           capture_output=True, text=True, check=False)
        subprocess.run(["git", "-C", str(self.repo), "worktree", "prune"],
                       capture_output=True, text=True, check=False)
        import shutil
        shutil.rmtree(self.repo, ignore_errors=True)

    def _write_unit(self, slug: str, fase: str = "implement") -> None:
        d = self.units_root / slug
        d.mkdir(parents=True, exist_ok=True)
        (d / "_estado.yaml").write_text(f"id: {slug}\nfase: {fase}\n", encoding="utf-8")

    def _add_worktree_with_commit(self, slug: str, file_name: str, content: str) -> tuple[Path, str]:
        """Real `git worktree add --detach`, one commit inside it -- returns `(path,
        seed_commit)`. `seed_commit` is the repo's HEAD BEFORE this worktree's own
        commit (CA-06's seed)."""
        seed = run_git(self.repo, "rev-parse", "HEAD")
        path = self.repo / "worktrees" / slug
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "--detach", str(path), "HEAD"],
                       capture_output=True, text=True, check=True)
        self._worktrees.append(path)
        write_file(path / file_name, content)
        run_git(path, "add", "-A")
        run_git(path, "commit", "-q", "-m", f"work: {slug}")
        return path, seed

    def _mandate(self, chain: str, members: list[str]) -> _FakeFullMandate:
        return _FakeFullMandate(self.units_root, chain, members,
                                path=self.repo / "plan.md", reference="fixture-drain")

    def _drain(self, mandate, data: dict) -> None:
        config = hop.ParallelConfig(plan="fixture-drain", repo_root=self.repo)
        with mock.patch.object(hop, "lock_status", return_value=("libre", 0)):
            hop._drain_merges(config, mandate, data, self.repo / "worktrees.json", self.evidencia_dir)

    # -- CA-09: topological order, not real finish order ---------------------------

    def test_merge_lands_in_topological_order_even_when_finish_order_is_inverted(self) -> None:
        self._write_unit("9951-a")
        self._write_unit("9950-b")
        # Created in INVERTED order against the dependency, on purpose.
        path_b, seed_b = self._add_worktree_with_commit("9950-b", "b.txt", "b\n")
        path_a, seed_a = self._add_worktree_with_commit("9951-a", "a.txt", "a\n")
        mandate = self._mandate("9951-a → 9950-b", ["9951-a", "9950-b"])
        data = {"worktrees": {
            "9950-b": {"estado": "fusionando", "ruta": str(path_b), "seed-commit": seed_b},
            "9951-a": {"estado": "fusionando", "ruta": str(path_a), "seed-commit": seed_a},
        }}

        self._drain(mandate, data)

        self.assertEqual(data["worktrees"]["9951-a"]["estado"], "retirado")
        self.assertEqual(data["worktrees"]["9951-a"]["fase"], "done")
        self.assertEqual(data["worktrees"]["9950-b"]["estado"], "retirado")
        self.assertEqual(data["worktrees"]["9950-b"]["fase"], "done")

        log = run_git(self.repo, "log", "--reverse", "--format=%s").splitlines()
        self.assertLess(log.index("merge: 9951-a"), log.index("merge: 9950-b"),
                        f"orden de merge no topológico:\n{log}")

    def test_dependent_defers_until_its_predecessor_actually_merged(self) -> None:
        """CA-08, exercised at the `_drain_merges` level: a dependent whose
        predecessor has NOT merged yet (still `"activo"`, not even `"fusionando"`)
        must stay `"fusionando"` this cycle -- no premature merge, no touch of the
        worktree's own internal files (CA-13)."""
        self._write_unit("9980-a")
        self._write_unit("9981-b")
        path_b, seed_b = self._add_worktree_with_commit("9981-b", "b.txt", "b\n")
        marker = path_b / "_estado.yaml"
        marker.write_text("id: 9981-b\nfase: done\n", encoding="utf-8")
        before = marker.read_bytes()

        mandate = self._mandate("9980-a → 9981-b", ["9980-a", "9981-b"])
        data = {"worktrees": {
            "9980-a": {"estado": "activo"},
            "9981-b": {"estado": "fusionando", "ruta": str(path_b), "seed-commit": seed_b},
        }}

        self._drain(mandate, data)

        self.assertEqual(data["worktrees"]["9981-b"]["estado"], "fusionando")
        self.assertTrue(path_b.is_dir())
        self.assertEqual(marker.read_bytes(), before)  # CA-13: untouched

    # -- CA-10: hybrid re-gate rule --------------------------------------------------

    def test_cheap_confirmation_when_merged_content_stays_identical(self) -> None:
        self._write_unit("9960-c")
        path_c, seed_c = self._add_worktree_with_commit("9960-c", "c.txt", "c\n")
        mandate = self._mandate("9960-c", ["9960-c"])
        data = {"worktrees": {
            "9960-c": {"estado": "fusionando", "ruta": str(path_c), "seed-commit": seed_c},
        }}

        with mock.patch.object(hop, "_rerun_code_gate") as fake_regate:
            self._drain(mandate, data)
        fake_regate.assert_not_called()

        self.assertEqual(data["worktrees"]["9960-c"]["estado"], "retirado")
        self.assertEqual(data["worktrees"]["9960-c"]["fase"], "done")

    def test_full_regate_runs_when_merge_alters_gate_evaluated_content(self) -> None:
        write_file(self.repo / "shared.txt", "line1\nline2\nline3\n")
        commit_all(self.repo, "shared file")
        self._write_unit("9961-d")
        path_d, seed_d = self._add_worktree_with_commit(
            "9961-d", "shared.txt", "line1\nline2\nline3-edited-by-d\n")
        # A change already landed on the main tree (simulating a sibling merged
        # earlier), touching a DIFFERENT line of the SAME file -- git auto-merges
        # cleanly, but the merged content is no longer byte-identical to what the
        # gate evaluated for 9961-d.
        write_file(self.repo / "shared.txt", "line1-edited-by-sibling\nline2\nline3\n")
        commit_all(self.repo, "sibling edit")

        mandate = self._mandate("9961-d", ["9961-d"])
        data = {"worktrees": {
            "9961-d": {"estado": "fusionando", "ruta": str(path_d), "seed-commit": seed_d},
        }}

        with mock.patch.object(hop, "_rerun_code_gate", return_value=(True, "")) as fake_regate:
            self._drain(mandate, data)
        fake_regate.assert_called_once()

        self.assertEqual(data["worktrees"]["9961-d"]["estado"], "retirado")
        self.assertEqual(data["worktrees"]["9961-d"]["fase"], "done")
        merged = (self.repo / "shared.txt").read_text(encoding="utf-8")
        self.assertIn("line1-edited-by-sibling", merged)
        self.assertIn("line3-edited-by-d", merged)

    def test_full_regate_timeout_or_escalation_leaves_the_unit_fusionando(self) -> None:
        write_file(self.repo / "shared.txt", "line1\nline2\nline3\n")
        commit_all(self.repo, "shared file")
        self._write_unit("9962-e")
        path_e, seed_e = self._add_worktree_with_commit(
            "9962-e", "shared.txt", "line1\nline2\nline3-edited-by-e\n")
        write_file(self.repo / "shared.txt", "line1-edited-by-sibling\nline2\nline3\n")
        commit_all(self.repo, "sibling edit")

        mandate = self._mandate("9962-e", ["9962-e"])
        data = {"worktrees": {
            "9962-e": {"estado": "fusionando", "ruta": str(path_e), "seed-commit": seed_e},
        }}

        with mock.patch.object(hop, "_rerun_code_gate",
                               return_value=(False, "timeout sin veredicto (1800s excedidos)")):
            self._drain(mandate, data)

        entry = data["worktrees"]["9962-e"]
        self.assertEqual(entry["estado"], "fusionando")  # never retired
        self.assertNotEqual(entry.get("fase"), "done")
        self.assertEqual(entry["parada-condicion"], "7")
        self.assertIn("timeout", entry["parada-causa"])
        # The merge itself already landed on the main tree (it was clean) -- only the
        # gate confirmation is missing; the worktree stays mounted (CA-10, not
        # counted as a satisfied predecessor yet).
        self.assertTrue(path_e.is_dir())

    # -- CA-12: real conflict -> typified stop, no auto-resolution -------------------

    def test_merge_conflict_stops_typified_without_auto_resolution(self) -> None:
        write_file(self.repo / "x.txt", "original\n")
        commit_all(self.repo, "x seed")
        self._write_unit("9963-f")
        path_f = self.repo / "worktrees" / "9963-f"
        subprocess.run(["git", "-C", str(self.repo), "worktree", "add", "--detach", str(path_f), "HEAD"],
                       capture_output=True, text=True, check=True)
        self._worktrees.append(path_f)
        seed_f = run_git(self.repo, "rev-parse", "HEAD")

        # Main tree changes x.txt AFTER the worktree branched off.
        write_file(self.repo / "x.txt", "main-change\n")
        commit_all(self.repo, "main change")

        # Worktree changes the SAME line differently -- a real conflict.
        write_file(path_f / "x.txt", "worktree-change\n")
        run_git(path_f, "add", "-A")
        run_git(path_f, "commit", "-q", "-m", "work: 9963-f")

        mandate = self._mandate("9963-f", ["9963-f"])
        data = {"worktrees": {
            "9963-f": {"estado": "fusionando", "ruta": str(path_f), "seed-commit": seed_f},
        }}

        self._drain(mandate, data)

        entry = data["worktrees"]["9963-f"]
        self.assertEqual(entry["estado"], "fusionando")
        self.assertNotEqual(entry.get("fase"), "done")
        self.assertEqual(entry["parada-condicion"], "3")
        self.assertIn("conflicto", entry["parada-causa"])
        # `git merge --abort` left the main tree clean -- no MERGE_HEAD lingering.
        self.assertFalse(hop._merge_head_present(self.repo))
        # The worktree is left alive -- resolution is a human's job (CA-12's "No
        # incluye"), not auto-cleaned.
        self.assertTrue(path_f.is_dir())

        # No silent retry on a second cycle while the marker stands.
        self._drain(mandate, data)
        self.assertEqual(data["worktrees"]["9963-f"]["parada-condicion"], "3")
        self.assertEqual(data["worktrees"]["9963-f"]["estado"], "fusionando")

    # -- Mandate-level orphan `MERGE_HEAD` health-check -------------------------------

    def test_orphaned_merge_head_sets_mandate_level_stop_and_skips_the_cycle(self) -> None:
        write_file(self.repo / "x.txt", "original\n")
        commit_all(self.repo, "x seed")
        path_g, seed_g = self._add_worktree_with_commit("9990-g", "x.txt", "worktree-change\n")
        worktree_head = run_git(path_g, "rev-parse", "HEAD")
        # Main tree diverges on the SAME line after the worktree branched -- a real
        # conflict, attempted DIRECTLY (not via `_drain_merges`) and left UNRESOLVED,
        # simulating a crash mid-`git merge` in a previous cycle.
        write_file(self.repo / "x.txt", "main-change\n")
        commit_all(self.repo, "main change")
        subprocess.run(["git", "-C", str(self.repo), "merge", "--no-ff", worktree_head],
                       capture_output=True, text=True, check=False)
        self.assertTrue(hop._merge_head_present(self.repo))  # sanity: conflict left MERGE_HEAD set

        self._write_unit("9990-g")
        mandate = self._mandate("9990-g", ["9990-g"])
        data = {"worktrees": {
            "9990-g": {"estado": "fusionando", "ruta": str(path_g), "seed-commit": seed_g},
        }}

        try:
            self._drain(mandate, data)

            self.assertIn("parada-arbol-principal", data)
            self.assertEqual(data["parada-arbol-principal"]["codigo"], "UNTYPIFIED")
            # Never resolved on its own -- no `git merge --abort` from this path.
            self.assertTrue(hop._merge_head_present(self.repo))
            # The fusionando unit was never even attempted this cycle.
            self.assertEqual(data["worktrees"]["9990-g"]["estado"], "fusionando")
            self.assertNotEqual(data["worktrees"]["9990-g"].get("fase"), "done")
        finally:
            # Manual cleanup so `_cleanup`'s worktree teardown doesn't choke on the
            # pending merge left on the main tree.
            subprocess.run(["git", "-C", str(self.repo), "merge", "--abort"],
                           capture_output=True, text=True, check=False)


if __name__ == "__main__":
    unittest.main()
