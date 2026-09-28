"""Tests for `snapshot_evidence.sh` (unit 0114, G2 — CA-14, CA-11a/b/c).

Exercises the real script as a subprocess (it is Bash, not importable), against
a throwaway unit directory outside the repo tree so nothing here touches real
evidence or the real `.spec/units/_plan-maestro.md`.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
SCRIPT = SCRIPTS / "snapshot_evidence.sh"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


class SnapshotEvidenceTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="snapshot-evidence-")
        self.unit_dir = Path(self._tmp) / "unidad"
        self.unit_dir.mkdir()
        (self.unit_dir / "_estado.yaml").write_text("id: x\n", encoding="utf-8")
        (self.unit_dir / "bitacora.md").write_text("# bitácora\n", encoding="utf-8")
        # `mandato.md`, `tasks.md`, `paquete-aprobacion.md` deliberately
        # absent: `capturar_en()` only copies what exists.
        self.dest = Path(self._tmp) / "dest"
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    # --- CA-11a — dry-run byte-identity with the real run -------------------

    def test_dry_run_output_is_byte_identical_to_the_real_run(self) -> None:
        dry = _run(str(self.unit_dir), str(self.dest), "--dry-run")
        self.assertEqual(dry.returncode, 0)
        self.assertFalse(self.dest.exists())  # dry-run writes nothing

        real = _run(str(self.unit_dir), str(self.dest))
        self.assertEqual(real.returncode, 0)
        self.assertTrue(self.dest.is_dir())

        self.assertEqual(dry.stdout, real.stdout)
        # And matches an independent `find | sort` over what was really
        # written — not just self-consistent with its own listing.
        found = subprocess.run(
            ["find", str(self.dest), "-type", "f"],
            capture_output=True, text=True, check=True,
        ).stdout
        expected = "\n".join(sorted(found.splitlines())) + "\n"
        self.assertEqual(real.stdout, expected)

    # --- CA-11b — real run, precondition met --------------------------------

    def test_real_run_captures_only_the_files_that_exist(self) -> None:
        real = _run(str(self.unit_dir), str(self.dest))
        self.assertEqual(real.returncode, 0)
        names = {p.name for p in self.dest.iterdir()}
        self.assertIn("_estado.yaml", names)
        self.assertIn("bitacora.md", names)
        self.assertNotIn("mandato.md", names)
        self.assertNotIn("tasks.md", names)
        # Always-written companions.
        self.assertIn("plan-maestro.diff", names)
        self.assertIn("diff-vs-seed.txt", names)

    def test_seed_flag_produces_a_diff_vs_seed_with_classifications(self) -> None:
        seed = Path(self._tmp) / "seed"
        seed.mkdir()
        (seed / "_estado.yaml").write_text("id: x\n", encoding="utf-8")   # igual
        (seed / "bitacora.md").write_text("# vieja\n", encoding="utf-8")  # cambiado

        real = _run(str(self.unit_dir), str(self.dest), "--seed", str(seed))
        self.assertEqual(real.returncode, 0)
        diff_vs_seed = (self.dest / "diff-vs-seed.txt").read_text(encoding="utf-8")
        self.assertIn("igual _estado.yaml", diff_vs_seed)
        self.assertIn("cambiado bitacora.md", diff_vs_seed)

    # --- CA-11c — precondition unmet -----------------------------------------

    def test_nonexistent_unit_exits_1_and_writes_nothing(self) -> None:
        missing = Path(self._tmp) / "no-such-unit"
        result = _run(str(missing), str(self.dest))
        self.assertEqual(result.returncode, 1)
        self.assertFalse(self.dest.exists())

    def test_missing_arguments_exit_2(self) -> None:
        result = _run(str(self.unit_dir))  # dest is missing
        self.assertEqual(result.returncode, 2)

    # --- CA-14 — no second implementation of the capture ---------------------

    def test_never_names_the_freshness_marker_literally(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertNotIn("capturado-en.txt", text)

    def test_delegates_to_supervised_test_capturar_unidad(self) -> None:
        text = SCRIPT.read_text(encoding="utf-8")
        self.assertIn("capturar-unidad", text)
        # And does not shell out to `cp`/`rsync` for the artifact files
        # themselves — the only copying this script does is none.
        self.assertNotIn("cp -R", text)


if __name__ == "__main__":
    unittest.main()
