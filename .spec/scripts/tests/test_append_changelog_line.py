"""Tests for `append_changelog_line.py` (unit 0114, G2 — CA-13, CA-11a/b/c).

Uses `--index` (a testing-only override; the real ritual always targets
`.spec/units/_plan-maestro.md`) so the suite never writes to the repo's real
changelog index.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SCRIPTS))

import append_changelog_line as acl  # noqa: E402

SCRIPT = SCRIPTS / "append_changelog_line.py"


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True, text=True, check=False,
    )


class AppendChangelogLineTests(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.mkdtemp(prefix="append-changelog-line-")
        self.index = Path(self._tmp) / "_plan-maestro.md"
        self.index.write_text(
            "# Índice\n\n## Otra sección\n\ncontenido ajeno\n\n"
            "## Changelog\n\n"
            "- 2026-09-01 · autor · primera entrada.\n"
            "- 2026-09-02 · autor · segunda entrada,\n"
            "  con una continuación indentada.\n",
            encoding="utf-8",
        )
        self.addCleanup(shutil.rmtree, self._tmp, ignore_errors=True)

    def _text(self) -> str:
        return self.index.read_text(encoding="utf-8")

    # --- CA-11a — dry-run byte-identity -------------------------------------

    def test_dry_run_is_byte_identical_to_what_a_real_run_appends(self) -> None:
        line = "- 2026-09-19 · test · tercera entrada."
        before = self._text()

        dry = _run("--line", line, "--index", str(self.index), "--dry-run")
        self.assertEqual(dry.returncode, acl.EXIT_OK)
        self.assertEqual(self._text(), before)  # writes nothing

        real = _run("--line", line, "--index", str(self.index))
        self.assertEqual(real.returncode, acl.EXIT_OK)
        after = self._text()
        self.assertEqual(after, before + line + "\n")
        self.assertEqual(dry.stdout.rstrip("\n"), line)
        self.assertEqual(real.stdout.rstrip("\n"), line)

    # --- CA-11b — real run, precondition met --------------------------------

    def test_real_run_appends_only_the_one_line_and_exits_zero(self) -> None:
        line = "- 2026-09-19 · test · tercera entrada."
        before = self._text()
        rc = acl.main(["--line", line, "--index", str(self.index)])
        self.assertEqual(rc, acl.EXIT_OK)
        after = self._text()
        # Diff limited to the appended line (CA-13): nothing before it moved.
        self.assertTrue(after.startswith(before))
        self.assertEqual(after[len(before):], line + "\n")

    # --- CA-13 — idempotent on a second identical run -----------------------

    def test_second_run_with_the_same_line_is_idempotent(self) -> None:
        line = "- 2026-09-19 · test · tercera entrada."
        rc1 = acl.main(["--line", line, "--index", str(self.index)])
        after_first = self._text()
        rc2 = acl.main(["--line", line, "--index", str(self.index)])
        after_second = self._text()
        self.assertEqual((rc1, rc2), (acl.EXIT_OK, acl.EXIT_OK))
        self.assertEqual(after_first, after_second)
        self.assertEqual(after_second.count(line), 1)

    def test_idempotence_is_byte_equality_after_strip(self) -> None:
        # Leading/trailing whitespace on the candidate must not defeat the
        # duplicate check (the spec's "igualdad byte a byte tras strip").
        acl.main(["--line", "- 2026-09-19 · test · cuarta entrada.",
                  "--index", str(self.index)])
        before = self._text()
        rc = acl.main(["--line", "  - 2026-09-19 · test · cuarta entrada.  \n",
                        "--index", str(self.index)])
        self.assertEqual(rc, acl.EXIT_OK)
        self.assertEqual(self._text(), before)

    # --- CA-11c — precondition unmet -----------------------------------------

    def test_no_changelog_section_exits_1_and_writes_nothing(self) -> None:
        no_section = Path(self._tmp) / "sin-changelog.md"
        no_section.write_text("# Índice\n\nsin sección de changelog\n", encoding="utf-8")
        before = no_section.read_text(encoding="utf-8")
        rc = acl.main(["--line", "- x", "--index", str(no_section)])
        self.assertEqual(rc, acl.EXIT_NO_SECTION)
        self.assertEqual(no_section.read_text(encoding="utf-8"), before)

    def test_nonexistent_index_exits_1(self) -> None:
        rc = acl.main(["--line", "- x", "--index", str(Path(self._tmp) / "no-existe.md")])
        self.assertEqual(rc, acl.EXIT_NO_SECTION)

    # --- --line-file ----------------------------------------------------------

    def test_line_file_reads_the_line_from_a_file(self) -> None:
        line_file = Path(self._tmp) / "linea.txt"
        line_file.write_text("- 2026-09-19 · test · desde archivo.\n", encoding="utf-8")
        rc = acl.main(["--line-file", str(line_file), "--index", str(self.index)])
        self.assertEqual(rc, acl.EXIT_OK)
        self.assertIn("- 2026-09-19 · test · desde archivo.\n", self._text())

    # --- section at end of file vs. mid-file --------------------------------

    def test_appends_before_the_next_heading_when_changelog_is_not_last(self) -> None:
        mid = Path(self._tmp) / "mid.md"
        mid.write_text(
            "## Changelog\n\n- primera\n\n## Siguiente sección\n\notro contenido\n",
            encoding="utf-8",
        )
        rc = acl.main(["--line", "- segunda", "--index", str(mid)])
        self.assertEqual(rc, acl.EXIT_OK)
        text = mid.read_text(encoding="utf-8")
        self.assertLess(text.index("- segunda"), text.index("## Siguiente sección"))


if __name__ == "__main__":
    unittest.main()
