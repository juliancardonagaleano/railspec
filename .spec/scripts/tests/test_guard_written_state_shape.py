"""Tests for `guard_written_state_shape.py` (unit 0114: CA-17, CA-18, CA-22b,
CA-22c, CA-23, D-16).

Every behavioural test runs the hook as a **subprocess** inside a throwaway repo
root built per test (`GuardRepoMixin`): the hook resolves `REPO_ROOT` by
self-location, so a copy of it under `<tmp>/.spec/scripts/` sees the temporary
tree as the repo and this suite never writes a byte into the real one. The
validator and `_common.py` travel with it -- the hook imports `read_unit_state`
from that same copy.

Every seed used below (`FLOW_STYLE_ESTADO`, `BLOCK_STYLE_ESTADO`,
`CLEAN_MANDATE_TEXT`, `PLAN_TODO_MAL`) is inline: none of them reads any
fixture tree.

What this file does **not** cover, on purpose: CA-22a (no network) lives in
`test_scripts_naming_and_strict_mode.py` by `ast` over the imports of both
hooks, as `plan.md` fixes it.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

import guard_written_state_shape as guard
import validate_mandate as vm

# `usage/` is IArk-specific instrumentation (unit 0113, not canonical kit
# andamiaje per its own `__init__.py`) and does not travel with the kit —
# inlined here instead of importing `usage.paths.default_repo_root`: same
# `Path(__file__).resolve().parents[3]` depth (this file also sits 3 levels
# under the repo root, tests/ -> scripts/ -> .spec/ -> repo root).
REAL_REPO = Path(__file__).resolve().parents[3]
REAL_SCRIPTS = REAL_REPO / ".spec" / "scripts"

#: Budget of CA-22b. If a worst case exceeds it the hook does not ship.
HOOK_TIME_BUDGET_SECONDS = 2.0

#: `_estado.yaml` "flow" style: minimal fields, no `mandato:` -- the validator
#: rejects it with `unidad-supervisado-sin-mandato`.
#: `SupervisedModeConditionTests` `.replace("modo: supervisado", ...)` needs
#: the exact literal below, hence it is a named constant, not inlined per
#: call site.
FLOW_STYLE_ESTADO = """\
id: "0000-fixture-flow-style"
titulo: "Fixture flow-style (sin mandato)"
modo: supervisado
fase: spec
estado: en-curso
"""

#: `_estado.yaml` "block" style: same fields, one per line, plus `mandato:
#: mandato.md` -- the validator accepts it (paired with `CLEAN_MANDATE_TEXT`
#: as the unit's own `mandato.md`).
BLOCK_STYLE_ESTADO = """\
id: "0000-fixture-block-style"
titulo: "Fixture block-style (con mandato)"

modo: supervisado
fase: spec
estado: en-curso

mandato: mandato.md
"""

#: A `mandato.md` body that validates clean end to end (`validate_mandate.py
#: --unidad` exit 0): every anchor `UNIT_ANCHORS` requires, `## Estado` =
#: `aprobado`, and an `## Aprobación` entry whose `hash:` the module
#: constant below computes for real (see `_clean_mandate_text`).
_MANDATE_TEMPLATE = """\
# Mandato supervisado — 0000-fixture-block-style

- id: 0000-fixture-block-style

## Objetivo y criterio de salida

Servir de unidad `modo: supervisado` que valide con
`validate_mandate.py --unidad` exit 0 en los tests de
`guard_written_state_shape.py`.

## Unidad amparada

`0000-fixture-block-style` (fixture inline del test).

## Mandato

### 2026-09-22T00:00Z
- autor: Julian Cardona Galeano
- lanza: Julian Cardona Galeano
- inicio: 2026-09-22T00:00Z
- fin: 2026-12-31T23:59Z

## Delegaciones

### Pre-decididas

(Sin pre-decisiones — la fixture no ejecuta trabajo.)

### Con criterio

(Sin decisiones con criterio.)

### Reservadas

(Sin reservadas — la fixture no ejecuta trabajo.)

## Condiciones de parada

Ver `.spec/PARADAS-SUPERVISADO.md` — lista única de condiciones de parada
indelegables del modo supervisado. Este mandato no reproduce esa lista; toda
parada se resuelve contra ella.

## Paralelismo

- carriles: `sdd` (solo el test)
- tope-worktrees: 1 (la fixture no requiere paralelismo)
- dueno-stack-vivo: Julian Cardona Galeano
- presupuesto-mcp: 0 (no se invoca MCP de gobernanza)
- conducta-presupuesto-agotado: parar — condición "MCP de gobernanza ausente o
  sin presupuesto" de `.spec/PARADAS-SUPERVISADO.md`

## Registro de decisiones

(Sin decisiones registradas — la fixture es estática.)

## Paradas

(Sin paradas a la fecha.)

## Punto de retoma

(Sin punto de retoma — la unidad no está `parado` ni requiere retomar.)

## Estado

aprobado

## Instancia en curso

Sin instancia.

## Aprobación

### 2026-09-22T00:00Z
- quien: Julian Cardona Galeano
- hash: {hash}
- fase: implement
- artefactos: spec

## Revisión posterior

(Sin revisiones.)
"""

#: Minimal, intentionally broken plan mandate: none of `PLAN_ANCHORS` is
#: present, so the validator rejects it with a batch of `seccion-ausente`.
PLAN_TODO_MAL = "# Plan de prueba, intencionalmente incompleto (sin ninguna ancla).\n"


def _clean_mandate_text() -> str:
    """`_MANDATE_TEMPLATE` with its own correct `## Aprobación` hash.

    `Mandate.approval_hash()` only hashes three fixed anchor bodies (`##
    Objetivo y criterio de salida`, `## Unidad amparada`, `## Delegaciones`),
    which this template holds constant, so the hash is the same text every
    time -- computed once, here, against a throwaway copy, never read from a
    fixture."""
    with tempfile.TemporaryDirectory() as tmp_dir:
        probe = Path(tmp_dir) / "mandato.md"
        probe.write_text(_MANDATE_TEMPLATE.format(hash="0" * 64), encoding="utf-8")
        real_hash = vm.Mandate(probe, "unidad", "mandato.md", Path(tmp_dir)).approval_hash()
    return _MANDATE_TEMPLATE.format(hash=real_hash)


CLEAN_MANDATE_TEXT = _clean_mandate_text()


class GuardRepoMixin:
    """A throwaway repo root with the hook, the validator and `_common.py`."""

    def setUp(self) -> None:  # noqa: D401
        self._tmp = Path(tempfile.mkdtemp(prefix="guard-post-write-")).resolve()
        self.repo = self._tmp / "repo"
        self.scripts = self.repo / ".spec" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in ("guard_written_state_shape.py", "validate_mandate.py", "_common.py"):
            shutil.copy(REAL_SCRIPTS / name, self.scripts / name)
        self.hook = self.scripts / "guard_written_state_shape.py"
        self.usage_dir = self.repo / ".spec" / ".usage"
        self.rejections = self.usage_dir / "guard-rejections-post-write.jsonl"
        self.pre_bash_rejections = self.usage_dir / "guard-rejections-pre-bash.jsonl"
        self.failures = self.usage_dir / "guard-failures.log"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    # -- building the tree under test ------------------------------------------------

    def make_unit(self, rel_dir: str, *, estado: str, mandate: bool = True) -> Path:
        """A unit directory at `rel_dir` with `_estado.yaml` (literal text)
        and, by default, a `mandato.md` that validates clean."""
        unit = self.repo / rel_dir
        unit.mkdir(parents=True, exist_ok=True)
        (unit / "_estado.yaml").write_text(estado, encoding="utf-8")
        if mandate:
            (unit / "mandato.md").write_text(CLEAN_MANDATE_TEXT, encoding="utf-8")
        return unit

    def make_plan(self, plan_id: str, content: str) -> Path:
        plan_dir = self.repo / ".spec" / "planes" / plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        plan_file = plan_dir / "plan.md"
        plan_file.write_text(content, encoding="utf-8")
        return plan_file

    # -- running the hook --------------------------------------------------------------

    def run_hook(self, file_path: Path, *, exempt_tree: str | None = None,
                 tool: str = "Write") -> subprocess.CompletedProcess:
        payload = {
            "session_id": "sesion-de-prueba",
            "cwd": str(self.repo),
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(file_path)},
            "tool_response": {"success": True},
        }
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        if exempt_tree is not None:
            env["SDD_GUARD_EXEMPT_TREE"] = exempt_tree
        return subprocess.run(
            [sys.executable, str(self.hook)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    # -- assertions --------------------------------------------------------------------

    def decision(self, result: subprocess.CompletedProcess) -> dict | None:
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)

    def rejection_lines(self) -> list[dict]:
        if not self.rejections.is_file():
            return []
        return [json.loads(line) for line in self.rejections.read_text(encoding="utf-8").splitlines() if line.strip()]

    def failure_lines(self) -> list[str]:
        if not self.failures.is_file():
            return []
        return [line for line in self.failures.read_text(encoding="utf-8").splitlines() if line.strip()]

    def assertNoDecision(self, result: subprocess.CompletedProcess) -> None:  # noqa: N802 — mimics unittest's own assert* naming convention
        self.assertIsNone(self.decision(result))
        self.assertEqual(self.rejection_lines(), [])


class ShapeRejectionTests(GuardRepoMixin, unittest.TestCase):
    """CA-17: "the shape" is whatever `validate_mandate.py` accepts or rejects."""

    def test_flow_style_estado_is_blocked_with_the_literal_code_and_location(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        decision = self.decision(self.run_hook(unit / "_estado.yaml"))
        self.assertIsNotNone(decision)
        self.assertEqual(decision["decision"], "block")
        reason = decision["reason"]
        # The literal code of the validator, with its location -- not a second
        # taxonomy of this hook's own.
        self.assertIn("unidad-supervisado-sin-mandato", reason)
        self.assertIn(str(unit / "_estado.yaml"), reason)
        self.assertIn("códigos: unidad-supervisado-sin-mandato", reason)
        self.assertIn(".spec/units/0000-unidad/_estado.yaml", reason)

        # CA-23: one line in the post-write log, with its codes.
        lines = self.rejection_lines()
        self.assertEqual(len(lines), 1)
        self.assertEqual(lines[0]["path"], ".spec/units/0000-unidad/_estado.yaml")
        self.assertEqual(lines[0]["tool"], "Write")
        self.assertEqual(lines[0]["codes"], ["unidad-supervisado-sin-mandato"])
        self.assertEqual(lines[0]["rule"], "validate_mandate --unidad")
        self.assertIn("session_id", lines[0])
        self.assertIn("ts", lines[0])
        # The pre-Bash log is a different file and stays untouched (CA-23).
        self.assertFalse(self.pre_bash_rejections.exists())

    def test_the_same_content_in_block_style_is_not_rejected(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=BLOCK_STYLE_ESTADO)
        self.assertNoDecision(self.run_hook(unit / "_estado.yaml"))

    def test_file_is_not_restored_after_a_block(self) -> None:
        """S-5: reporting is the point; undoing the write would be a hidden write."""
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        before = (unit / "_estado.yaml").read_bytes()
        self.decision(self.run_hook(unit / "_estado.yaml"))
        self.assertEqual((unit / "_estado.yaml").read_bytes(), before)


class MandateRejectionTests(GuardRepoMixin, unittest.TestCase):
    """CA-18: a broken anchor or mandate shape in `mandato.md`/`plan.md`."""

    def test_mandato_md_with_a_missing_anchor_is_blocked(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=BLOCK_STYLE_ESTADO)
        text = (unit / "mandato.md").read_text(encoding="utf-8")
        (unit / "mandato.md").write_text(
            text.replace("## Paradas", "## Paradas-renombrada"), encoding="utf-8"
        )
        decision = self.decision(self.run_hook(unit / "mandato.md", tool="Edit"))
        self.assertIsNotNone(decision)
        self.assertIn("seccion-ausente", decision["reason"])
        self.assertEqual(self.rejection_lines()[0]["codes"], ["seccion-ausente"])

    def test_mandato_md_with_a_duplicated_anchor_is_blocked(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=BLOCK_STYLE_ESTADO)
        text = (unit / "mandato.md").read_text(encoding="utf-8")
        (unit / "mandato.md").write_text(
            text.replace("## Paradas", "## Paradas\n\n## Paradas", 1), encoding="utf-8"
        )
        decision = self.decision(self.run_hook(unit / "mandato.md", tool="Edit"))
        self.assertIsNotNone(decision)
        self.assertIn("seccion-duplicada", decision["reason"])
        self.assertEqual(self.rejection_lines()[0]["codes"], ["seccion-duplicada"])

    def test_a_valid_mandato_md_is_not_rejected(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=BLOCK_STYLE_ESTADO)
        self.assertNoDecision(self.run_hook(unit / "mandato.md", tool="Edit"))

    def test_plan_md_is_not_watched_post_u0009(self) -> None:
        # U-0009 retira el archivo histórico de WATCHED_BASENAMES; el guard no
        # observa ``plan.md`` por construcción (los planes son validados por
        # `sdd-supervisado` al inicio de la tanda, no por el guard de escritura).
        # Por construcción, ``make_plan`` aún escribe el archivo, pero el hook
        # debe retornar ``decision=None`` (no-op) porque el basename no está
        # en WATCHED_BASENAMES.
        plan_file = self.make_plan("plan-de-prueba", PLAN_TODO_MAL)
        decision = self.decision(self.run_hook(plan_file, tool="Edit"))
        self.assertIsNone(
            decision,
            "post-U-0009 el guard no observa `plan.md`; el hook debe ser no-op",
        )


class TreeConditionTests(GuardRepoMixin, unittest.TestCase):
    """Condition (b): templates and the fixture trees hold states and mandates
    that are invalid **on purpose** -- editing one is not a rejection."""

    def _write_invalid_estado(self, rel_dir: str) -> Path:
        target_dir = self.repo / rel_dir
        target_dir.mkdir(parents=True, exist_ok=True)
        (target_dir / "_estado.yaml").write_text(FLOW_STYLE_ESTADO, encoding="utf-8")
        return target_dir / "_estado.yaml"

    def test_templates_tree_is_excluded(self) -> None:
        target = self.repo / ".spec" / "_plantillas"
        target.mkdir(parents=True)
        (target / "mandato.md").write_text("# plantilla sin anclas\n", encoding="utf-8")
        self.assertNoDecision(self.run_hook(target / "mandato.md"))

    def test_fixtures_tree_is_excluded(self) -> None:
        self.assertNoDecision(
            self.run_hook(self._write_invalid_estado(".spec/_fixtures/alguna-fixture/unidad-cualquiera"))
        )

    def test_usage_tests_fixtures_tree_is_excluded(self) -> None:
        self.assertNoDecision(
            self.run_hook(self._write_invalid_estado(".spec/scripts/usage/tests/fixtures/repo_root"))
        )

    def test_a_watched_basename_outside_both_trees_is_ignored(self) -> None:
        loose = self.repo / "_estado.yaml"
        loose.write_text(FLOW_STYLE_ESTADO, encoding="utf-8")
        self.assertNoDecision(self.run_hook(loose))

    def test_a_file_outside_the_repo_is_ignored(self) -> None:
        outside = self._tmp / "_estado.yaml"
        outside.write_text(FLOW_STYLE_ESTADO, encoding="utf-8")
        self.assertNoDecision(self.run_hook(outside))


class SupervisedModeConditionTests(GuardRepoMixin, unittest.TestCase):
    """Condition (c): `_estado.yaml` is only watched in `supervisado`/
    `desatendido` units."""

    def test_estado_of_a_unit_that_is_not_supervised_is_ignored(self) -> None:
        unit = self.make_unit(
            ".spec/units/0000-unidad/",
            estado=FLOW_STYLE_ESTADO.replace("modo: supervisado", "modo: semi-autonomo"),
        )
        self.assertNoDecision(self.run_hook(unit / "_estado.yaml"))

    def test_estado_of_a_desatendido_unit_is_also_watched(self) -> None:
        """CA-30: condition (c) is not `supervisado`-only -- a `desatendido`
        unit's `_estado.yaml` is watched the same way, so its malformed
        shape (no `mandato:`) still blocks."""
        unit = self.make_unit(
            ".spec/units/0000-unidad/",
            estado=FLOW_STYLE_ESTADO.replace("modo: supervisado", "modo: desatendido"),
        )
        self.assertIsNotNone(self.decision(self.run_hook(unit / "_estado.yaml")))


class PilotExemptionTests(GuardRepoMixin, unittest.TestCase):
    """D-16: the pilot's test unit produces malformed states on purpose."""

    def test_pilot_prefix_is_exempt_without_any_variable(self) -> None:
        unit = self.make_unit(".spec/units/9109-prueba-supervisado/", estado=FLOW_STYLE_ESTADO)
        self.assertNoDecision(self.run_hook(unit / "_estado.yaml"))

    def test_the_same_content_under_another_unit_is_blocked(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        self.assertIsNotNone(self.decision(self.run_hook(unit / "_estado.yaml")))

    def test_exempt_tree_variable_covers_a_test_unit_outside_the_prefix(self) -> None:
        unit = self.make_unit(".spec/units/9500-otra-unidad-de-prueba/", estado=FLOW_STYLE_ESTADO)
        result = self.run_hook(
            unit / "_estado.yaml", exempt_tree=".spec/units/9500-otra-unidad-de-prueba"
        )
        self.assertNoDecision(result)
        self.assertEqual(self.failure_lines(), [])

    def test_empty_exempt_tree_still_blocks_and_leaves_a_trace(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        self.assertIsNotNone(self.decision(self.run_hook(unit / "_estado.yaml", exempt_tree="")))
        self.assertTrue(any("exempt-tree-ignored" in line for line in self.failure_lines()))

    def test_exempt_tree_outside_units_still_blocks_and_leaves_a_trace(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        self.assertIsNotNone(
            self.decision(self.run_hook(unit / "_estado.yaml", exempt_tree=".spec"))
        )
        self.assertTrue(any("exempt-tree-ignored" in line for line in self.failure_lines()))


class FailOpenTests(GuardRepoMixin, unittest.TestCase):
    """CA-22c: a failure of the hook's own never blocks the tool."""

    def test_an_unreadable_target_logs_and_does_not_block(self) -> None:
        unit = self.make_unit(".spec/units/0000-unidad/", estado=FLOW_STYLE_ESTADO)
        target = unit / "_estado.yaml"
        target.chmod(0o000)
        try:
            if os.access(target, os.R_OK):  # running as root: the probe is meaningless
                self.skipTest("el proceso puede leer un archivo con modo 000 (¿root?)")
            result = self.run_hook(target)
            self.assertNoDecision(result)
            self.assertTrue(self.failure_lines())
            self.assertFalse(self.pre_bash_rejections.exists())
        finally:
            target.chmod(0o644)

    def test_a_payload_that_is_not_json_does_not_block(self) -> None:
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        result = subprocess.run(
            [sys.executable, str(self.hook)],
            input="esto no es JSON",
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")
        self.assertTrue(self.failure_lines())


class RejectionsLogPathTests(unittest.TestCase):
    """The path of the post-write log is the contract K6 reads (CA-23)."""

    def test_rejections_log_is_the_literal_expected_path(self) -> None:
        self.assertEqual(
            guard.REJECTIONS_LOG,
            REAL_REPO / ".spec/.usage/guard-rejections-post-write.jsonl",
        )

    def test_failures_log_is_separate_from_both_rejection_logs(self) -> None:
        self.assertNotEqual(guard.FAILURES_LOG, guard.REJECTIONS_LOG)
        self.assertEqual(guard.FAILURES_LOG.parent, guard.REJECTIONS_LOG.parent)


class WorstCaseBudgetTests(GuardRepoMixin, unittest.TestCase):
    """CA-22b: one worst case per branch of the validator this hook runs.

    Both copy the **largest real file** of its kind into the throwaway repo, so
    the size under measurement is real while nothing is written into the real
    tree. The budget is the hook end to end (interpreter start-up, the import of
    `validate_mandate`, the validator subprocess and the log append).
    """

    def _largest(self, root: Path, *names: str) -> Path:
        candidates = [
            path
            for name in names
            for path in (REAL_REPO / root).glob(f"*/{name}")
            if path.is_file()
        ]
        self.assertTrue(candidates, f"no hay candidatos bajo {root} para {names}")
        return max(candidates, key=lambda p: p.stat().st_size)

    def _measure(self, target: Path) -> float:
        start = time.monotonic()
        result = self.run_hook(target, tool="Edit")
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        return elapsed

    @unittest.skip(
        "Needs the largest real `.spec/units/*/_estado.yaml`|`mandato.md` of "
        "this repo to measure a worst-case performance budget against. A "
        "freshly-founded kit repo has no real backlog depth to draw that "
        "worst case from yet (unlike the source repo, with years of unit "
        "history) — not a genericity gap, just no data to measure. Revisit "
        "once the kit accumulates its own real `.spec/units/` history."
    )
    def test_unit_branch_stays_within_the_budget(self) -> None:
        source = self._largest(Path(".spec/units"), "_estado.yaml", "mandato.md")
        unit = self.repo / ".spec" / "units" / "0000-unidad"
        unit.mkdir(parents=True)
        shutil.copy(source, unit / source.name)
        if source.name == "_estado.yaml":
            # Condition (c) must hold or the hook returns before doing any work.
            text = (unit / "_estado.yaml").read_text(encoding="utf-8")
            if "modo: supervisado" not in text:
                (unit / "_estado.yaml").write_text(text + "\nmodo: supervisado\n", encoding="utf-8")
        elapsed = self._measure(unit / source.name)
        self.assertLess(
            elapsed,
            HOOK_TIME_BUDGET_SECONDS,
            f"rama --unidad sobre {source} ({source.stat().st_size} B): {elapsed:.3f}s",
        )

    @unittest.skip(
        "Needs the largest real `.spec/planes/*/plan.md` of "
        "this repo to measure a worst-case performance budget against. A "
        "freshly-founded kit repo has no `.spec/planes/` backlog depth to draw "
        "that worst case from yet — not a genericity gap, just no data to "
        "measure. Revisit once the kit accumulates its own real plans."
    )
    def test_plan_branch_stays_within_the_budget(self) -> None:
        source = self._largest(Path(".spec/planes"), "plan.md")
        plan_file = self.make_plan("plan-de-prueba", source.read_text(encoding="utf-8"))
        elapsed = self._measure(plan_file)
        self.assertLess(
            elapsed,
            HOOK_TIME_BUDGET_SECONDS,
            f"rama --plan sobre {source} ({source.stat().st_size} B): {elapsed:.3f}s",
        )


if __name__ == "__main__":
    unittest.main()
