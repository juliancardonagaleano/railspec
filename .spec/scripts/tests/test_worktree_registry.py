"""`worktree_registry.py` — pure schema tests for the `"fusionando"` state and
the new per-unit (`seed-commit`, `parada-condicion`, `parada-causa`) /
file-level (`parada-arbol-principal`) fields it carries (worktree
orchestrator DAG/merge unit, `plan.md` § Archivos a crear / modificar and §
Riesgos y mitigaciones).

No git, no subprocess: `reconcile`/`render_board`/`classify_condition` are
already agnostic to the new state (they only ever compare against the
literal `"activo"`) — these tests confirm that by exercise, not just by
reading the source, per `plan.md`'s own mitigation for the `STATES`
exhaustiveness risk.
"""

from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import worktree_registry as wr  # noqa: E402


class StatesTests(unittest.TestCase):
    def test_fusionando_is_a_valid_state(self) -> None:
        self.assertIn("fusionando", wr.STATES)

    def test_preexisting_states_still_present(self) -> None:
        # Extending STATES must not drop any of the 3 pre-existing values
        # (the exhaustiveness risk plan.md flags: a consumer that assumed
        # only 3 values would break just as easily on a removal as on the
        # new 4th value).
        for state in ("activo", "huerfano", "retirado"):
            self.assertIn(state, wr.STATES)


class SetEntryFieldMergeTests(unittest.TestCase):
    """`set_entry` merges fields onto the existing entry (`worktree_registry.py`
    `set_entry`) — confirms the new fields ride that same merge, no total
    replacement, no migration needed (plan.md § Reutilización)."""

    def test_seed_commit_persists_across_a_later_merge(self) -> None:
        data: dict = {}
        wr.set_entry(data, "9950-seed", **{
            "estado": "activo",
            "seed-commit": "abc1234",
        })
        # A later, unrelated field write (e.g. the orchestrator recording a
        # merge-conflict stop) must not erase the seed committed earlier.
        wr.set_entry(data, "9950-seed", **{
            "parada-condicion": "3",
            "parada-causa": "conflicto de merge detectado",
        })
        entry = data["worktrees"]["9950-seed"]
        self.assertEqual(entry["seed-commit"], "abc1234")
        self.assertEqual(entry["estado"], "activo")
        self.assertEqual(entry["parada-condicion"], "3")
        self.assertEqual(entry["parada-causa"], "conflicto de merge detectado")

    def test_transition_to_fusionando_preserves_seed_commit(self) -> None:
        data: dict = {}
        wr.set_entry(data, "9951-transicion", **{
            "estado": "activo",
            "seed-commit": "deadbee",
        })
        wr.set_entry(data, "9951-transicion", estado="fusionando")
        entry = data["worktrees"]["9951-transicion"]
        self.assertEqual(entry["estado"], "fusionando")
        self.assertEqual(entry["seed-commit"], "deadbee")


class ReconcileFusionandoTests(unittest.TestCase):
    """Gate 0118/G4-style risk mitigation (plan.md § Riesgos): a `"fusionando"`
    entry (worktree finished its process but is still alive, merge pending)
    must not be marked `huerfano` while its real worktree is still there —
    `reconcile` only ever re-checks entries whose `estado == "activo"`, so a
    `"fusionando"` entry passes through untouched, exercised here rather than
    just asserted from reading the source."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="wr-reconcile-")
        self.addCleanup(self._tmp.cleanup)
        self.tmp = Path(self._tmp.name)
        self.scoped_root = self.tmp / "worktrees"
        self.scoped_root.mkdir()
        self.wt_path = self.scoped_root / "9952-fusionando"
        self.wt_path.mkdir()

    def test_fusionando_entry_with_live_worktree_is_not_touched(self) -> None:
        data: dict = {"worktrees": {
            "9952-fusionando": {
                "ruta": str(self.wt_path),
                "estado": "fusionando",
                "seed-commit": "cafebabe",
                "actualizado": "2026-09-24T00:00:00Z",
            }
        }}
        report = wr.reconcile(data, [str(self.wt_path)], self.scoped_root)
        self.assertEqual(report, [])
        entry = data["worktrees"]["9952-fusionando"]
        self.assertEqual(entry["estado"], "fusionando")
        self.assertEqual(entry["seed-commit"], "cafebabe")

    def test_fusionando_entry_is_not_marked_huerfano_even_if_worktree_vanished(self) -> None:
        # Even if the real worktree already disappeared, `reconcile` only
        # re-checks `estado == "activo"` entries: a `"fusionando"` entry is
        # left alone (its lifecycle is owned by `_drain_merges`, not by
        # reconciliation against `git worktree list`).
        data: dict = {"worktrees": {
            "9953-huerfano-candidato": {
                "ruta": str(self.scoped_root / "9953-huerfano-candidato"),
                "estado": "fusionando",
                "actualizado": "2026-09-24T00:00:00Z",
            }
        }}
        report = wr.reconcile(data, [], self.scoped_root)
        self.assertEqual(report, [])
        self.assertEqual(data["worktrees"]["9953-huerfano-candidato"]["estado"], "fusionando")


class RenderBoardWithOrchestratorStopTests(unittest.TestCase):
    """`render_board`/`classify_condition` need no special case for a stop the
    orchestrator itself originates (`parada-condicion`/`parada-causa`,
    surfaced by its caller as an ordinary `stops` row) — same grouping path
    as any other stop, confirming plan.md's claim that no logic change was
    needed in this module."""

    def test_merge_conflict_stop_groups_under_condition_3(self) -> None:
        stops = [{
            "unidad": "9954-conflicto",
            "disparador": "3. Divergencia entre plan y realidad",
            "causa": "conflicto de merge detectado, git merge --abort ejecutado",
            "timestamp": "2026-09-24T00:00:00Z",
        }]
        text = wr.render_board(stops)
        self.assertIn("## 3. Divergencia entre plan y realidad", text)
        self.assertIn(
            "| 9954-conflicto | 3. Divergencia entre plan y realidad | "
            "conflicto de merge detectado, git merge --abort ejecutado | "
            "2026-09-24T00:00:00Z |",
            text,
        )


if __name__ == "__main__":
    unittest.main()
