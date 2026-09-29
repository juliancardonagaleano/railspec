"""Tests for `sdd_safe_write.py` (unit 0132, T4/G4; CA-05, CA-10, CA-11).

E2E coverage of the safe-write loop. The behavioural tests run the hook as a
subprocess inside a throwaway repo built per test (`SafeWriteRepoMixin`); the
loop resolves `REPO_ROOT` by self-location, so a copy of it under
`<tmp>/.spec/scripts/` sees the temporary tree as the repo and this suite never
writes a byte into the real one. The validator and the canonical module travel
with it.

What this file covers
---------------------
- `test_codigos_set_matches_validator` (CA-09) -- the closed set of 30 codes
  the loop reads at runtime.
- `NormalizeCanonicalRuleTests` (CA-04) -- one test per canonicalization rule
  (block style, indent-2 tabs, literals, no-flow expansion, stable key order,
  idempotence, comment preservation).
- `RoundTripTests` (CA-06) -- a fixed set of inline YAML seeds (no fixture
  tree) covering every form `normalize_canonical` must preserve: block style,
  flow style, list forms (with dashes, empty), `null`/booleans, line and
  trailing comments, non-canonical (4-space) indentation, a tilde'd key
  (`dueño`), strings with `:` and with quotes, and a nested block (`gates:`).
  Each round-trips via `normalize_canonical` + atomic rewrite.
- `AtomicWriteTests` (CA-10) -- `atomic_write_text` survives a
  `KeyboardInterrupt` between the tempfile write and `os.replace`; the original
  target is left untouched.
- `LoopBehaviorTests` (CA-05) -- valid `_estado.yaml` exits 0; missing `mandato:`
  blocks; `.md` and out-of-repo paths and invalid JSON are no-ops. Includes
  E2E over the two guard-style seeds (block-style passes, flow-style blocks,
  missing-section mandato blocks).
- `LatencyTests` -- the loop stays under the 1 s budget for a typical
  `_estado.yaml`.

PyYAML is **only** used inside the test file -- the loop itself does not import
it (`pol-ia-no-embeber-conocimiento`).
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
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import sdd_safe_write
import sdd_safe_write_canonical
from sdd_safe_write import atomic_write_text

REPO = Path(__file__).resolve().parents[3]
SCRIPTS = REPO / ".spec" / "scripts"

VALID_MANDATO = "prueba"

LOOP_TIME_BUDGET_SECONDS = 1.0

VALID_BLOCK_ESTADO = (
    "id: 0000-prueba\n"
    "modo: supervisado\n"
    "fase: plan\n"
    "estado: en-progreso\n"
    f"mandato: {VALID_MANDATO}\n"
)

# CA-30: condition (e) also activates on `desatendido`, not only
# `supervisado` -- same shape, only `modo` and the added `tanda` differ.
VALID_BLOCK_ESTADO_DESATENDIDO = (
    "id: 0000-prueba\n"
    "modo: desatendido\n"
    "fase: plan\n"
    "estado: en-progreso\n"
    f"mandato: {VALID_MANDATO}\n"
    "tanda: 0000-tanda-de-prueba\n"
)

#: `_estado.yaml` "flow" style: minimal fields, no `mandato:` -- rejected with
#: `unidad-supervisado-sin-mandato`.
FLOW_STYLE_ESTADO = (
    "id: \"0000-fixture-flow-style\"\n"
    "titulo: \"Fixture flow-style (sin mandato)\"\n"
    "modo: supervisado\n"
    "fase: spec\n"
    "estado: en-curso\n"
)

#: `_estado.yaml` "block" style: same fields, plus `mandato: mandato.md`,
#: paired with a full `mandato.md` written by `_seed_unit_with_full_mandato`.
BLOCK_STYLE_ESTADO = (
    "id: \"0000-fixture-block-style\"\n"
    "titulo: \"Fixture block-style (con mandato)\"\n"
    "\n"
    "modo: supervisado\n"
    "fase: spec\n"
    "estado: en-curso\n"
    "\n"
    "mandato: mandato.md\n"
)

MINIMAL_PLAN_TEMPLATE = """# Plan de prueba minima

## Objetivo y criterio de salida

Objetivo.

## Unidades miembro

## Cadena de dependencias

Vigente:

## Registro de revisiones

## Mandato

### 2026-09-22

- inicio: 2026-09-22T00:00:00Z
- fin: 2026-09-22T23:59:59Z
- quien: julian

## Delegaciones

### Pre-decididas

### Con criterio

### Reservadas

## Condiciones de parada

Segun `.spec/PARADAS-SUPERVISADO.md`.

## Paralelismo

- carriles: 1
- tope-worktrees: 1
- dueno-stack-vivo: julian
- presupuesto-mcp: bajo
- conducta-presupuesto-agotado: parar

## Registro de decisiones

## Paradas

## Punto de retoma

## Estado

borrador

## Instancia en curso

## Aprobación

### 2026-09-22

- autor: julian
- hash: PLACEHOLDER_HASH

## Revisión posterior
"""

#: A `mandato.md` body that validates clean end to end
#: (`validate_mandate.py --unidad` exit 0), paired with `BLOCK_STYLE_ESTADO`.
#: Every anchor `UNIT_ANCHORS` requires, `## Estado` = `aprobado`, and an
#: `## Aprobación` entry whose `hash:` `_seed_unit_with_full_mandato` computes
#: for real against the throwaway's own copy of `validate_mandate.py`.
MINIMAL_MANDATO_TEMPLATE = """# Mandato supervisado — 0000-fixture-block-style

- id: 0000-fixture-block-style

## Objetivo y criterio de salida

Servir de unidad `modo: supervisado` que valide con
`validate_mandate.py --unidad` exit 0 en los tests de `sdd_safe_write.py`.

## Unidad amparada

`0000-fixture-block-style` (fixture inline del test).

## Mandato

### 2026-09-22T00:00Z
- autor: julian
- lanza: julian
- inicio: 2026-09-22T00:00Z
- fin: 2026-12-31T23:59Z

## Delegaciones

### Pre-decididas

### Con criterio

### Reservadas

## Condiciones de parada

Ver `.spec/PARADAS-SUPERVISADO.md`.

## Paralelismo

- carriles: `sdd`
- tope-worktrees: 1
- dueno-stack-vivo: julian
- presupuesto-mcp: 0
- conducta-presupuesto-agotado: parar

## Registro de decisiones

## Paradas

## Punto de retoma

## Estado

aprobado

## Instancia en curso

Sin instancia.

## Aprobación

### 2026-09-22T00:00Z
- quien: julian
- hash: PLACEHOLDER_HASH
- fase: implement
- artefactos: spec

## Revisión posterior
"""


class SafeWriteRepoMixin:
    """A throwaway repo root with the loop, the validator, the canonical
    module and `_common.py`. Modelled on `GuardRepoMixin` of
    `test_guard_written_state_shape.py` -- the loop and the guard share
    the same self-location idiom."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="sdd-safe-write-")).resolve()
        self.repo = self._tmp / "repo"
        self.scripts = self.repo / ".spec" / "scripts"
        self.scripts.mkdir(parents=True)
        for name in (
            "sdd_safe_write.py",
            "sdd_safe_write_canonical.py",
            "validate_mandate.py",
            "_common.py",
        ):
            shutil.copy(SCRIPTS / name, self.scripts / name)
        self.loop = self.scripts / "sdd_safe_write.py"
        self.usage_dir = self.repo / ".spec" / ".usage"
        self.log = self.usage_dir / "sdd-safe-write.log"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def make_unit(self, rel_dir: str, estado_text: str | None = None) -> Path:
        unit = self.repo / rel_dir
        unit.mkdir(parents=True, exist_ok=True)
        (unit / "_estado.yaml").write_text(
            estado_text if estado_text is not None else VALID_BLOCK_ESTADO,
            encoding="utf-8",
        )
        return unit

    def seed_plan(self, plan_id: str = "prueba") -> Path:
        """Build a minimal valid `plan.md` in the throwaway repo.

        The validator in the throwaway resolves `PLANS` from its own
        `parents[2]`, so `plan_id` becomes a sibling plan directory under
        `<throwaway>/.spec/planes/<plan_id>/`. The plan carries all anchors
        required by `PLAN_ANCHORS`, an empty `## Unidades miembro`, an empty
        `Vigente:` chain, a valid mandate entry, an `## Estado: borrador`
        (single-line) so `retoma-incompleta` does not fire in strictest reading,
        a `## Paralelismo` with the five mandatory fields and a `tope-worktrees`
        under the cap, and a `## Aprobación` entry whose `hash` matches the
        live `approval_hash()` of the just-written plan. The live hash is
        computed by running the throwaway's copy of `validate_mandate.py
        --hash --plan <plan_id>` once, then the placeholder is substituted in.
        The `PARADAS-SUPERVISADO.md` reference is copied verbatim so
        `check_stop_conditions` finds the literal token it looks for.
        """
        plan_dir = self.repo / ".spec" / "planes" / plan_id
        plan_dir.mkdir(parents=True, exist_ok=True)
        (self.repo / ".spec" / "PARADAS-SUPERVISADO.md").write_bytes(
            (REPO / ".spec" / "PARADAS-SUPERVISADO.md").read_bytes()
        )
        plan_text = MINIMAL_PLAN_TEMPLATE.format(plan_id=plan_id)
        (plan_dir / "plan.md").write_text(plan_text, encoding="utf-8")
        hash_proc = subprocess.run(
            [
                sys.executable,
                str(self.scripts / "validate_mandate.py"),
                "--hash",
                "--plan",
                plan_id,
            ],
            cwd=str(self.repo),
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            hash_proc.returncode,
            0,
            hash_proc.stdout + hash_proc.stderr,
        )
        live_hash = hash_proc.stdout.strip()
        (plan_dir / "plan.md").write_text(
            plan_text.replace("PLACEHOLDER_HASH", live_hash),
            encoding="utf-8",
        )
        return plan_dir

    def _seed_unit_with_full_mandato(self, estado_text: str) -> Path:
        """A unit whose `_estado.yaml` is `estado_text` (expected to declare
        `mandato: mandato.md`) paired with a `mandato.md` that validates
        clean -- the hash under `## Aprobación` is computed for real against
        the throwaway's own `validate_mandate.py`, the same pattern
        `seed_plan` uses for the plan form."""
        unit = self.make_unit(".spec/units/0000-unidad/", estado_text=estado_text)
        (unit / "mandato.md").write_text(
            MINIMAL_MANDATO_TEMPLATE.replace("PLACEHOLDER_HASH", "0" * 64),
            encoding="utf-8",
        )
        hash_proc = subprocess.run(
            [sys.executable, str(self.scripts / "validate_mandate.py"),
             "--hash", "--unidad", str(unit)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(hash_proc.returncode, 0, hash_proc.stdout + hash_proc.stderr)
        live_hash = hash_proc.stdout.strip()
        (unit / "mandato.md").write_text(
            MINIMAL_MANDATO_TEMPLATE.replace("PLACEHOLDER_HASH", live_hash),
            encoding="utf-8",
        )
        return unit

    def run_loop(
        self,
        file_path: Path,
        *,
        tool: str = "Write",
        extra_payload: dict | None = None,
    ) -> subprocess.CompletedProcess:
        payload = {
            "session_id": "sesion-de-prueba",
            "cwd": str(self.repo),
            "hook_event_name": "PostToolUse",
            "tool_name": tool,
            "tool_input": {"file_path": str(file_path)},
            "tool_response": {"success": True},
        }
        if extra_payload:
            payload.update(extra_payload)
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        env.pop("SDD_SAFE_WRITE_MAX_RETRIES", None)
        return subprocess.run(
            [sys.executable, str(self.loop)],
            input=json.dumps(payload),
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )

    def log_lines(self) -> list[str]:
        if not self.log.is_file():
            return []
        return [
            line
            for line in self.log.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def decision(self, result: subprocess.CompletedProcess) -> dict | None:
        self.assertEqual(result.returncode, 0, result.stderr)
        if not result.stdout.strip():
            return None
        return json.loads(result.stdout)


class CanonicalModuleTests(unittest.TestCase):
    """CA-09 / CA-13: the closed set of codes and the partition table."""

    def test_codigos_set_matches_validator(self) -> None:
        codes = sdd_safe_write_canonical.list_validator_codes()
        self.assertEqual(len(codes), sdd_safe_write_canonical.EXPECTED_CODE_COUNT)
        self.assertEqual(len(codes), 30)
        self.assertEqual(
            sdd_safe_write_canonical.FIXABLE_BY_FORMAT,
            frozenset(),
        )
        self.assertEqual(
            sdd_safe_write_canonical.CODE_CODE_POINT,
            frozenset(),
        )


class NormalizeCanonicalRuleTests(unittest.TestCase):
    """CA-04: one test per rule of `normalize_canonical`."""

    def _norm(self, sample: str) -> str:
        return sdd_safe_write_canonical.normalize_canonical(sample)

    def test_expands_non_empty_flow_mapping_to_block(self) -> None:
        sample = "gates:\n  plan:\n    criticos: [{lente: L1, subagente: a}]\n"
        out = self._norm(sample)
        self.assertNotIn("{lente", out)
        self.assertIn("criticos:", out)
        self.assertIn("- lente: L1", out)
        self.assertIn("subagente: a", out)

    def test_expands_flow_list_value_to_block_list(self) -> None:
        sample = "keys: [a, b, c]\n"
        out = self._norm(sample)
        self.assertEqual(out, "keys:\n  - a\n  - b\n  - c\n")

    def test_keeps_empty_flow_collections(self) -> None:
        sample = "hallazgos: []\nempty_map: {}\n"
        self.assertEqual(self._norm(sample), sample)

    def test_expands_list_item_flow_mapping(self) -> None:
        sample = "criticos:\n  - {lente: \"L1\", subagente: sdd-critico}\n"
        out = self._norm(sample)
        self.assertNotIn("{", out)
        self.assertIn("- lente: \"L1\"", out)
        self.assertIn("  subagente: sdd-critico", out)

    def test_leading_tabs_become_two_spaces_each(self) -> None:
        sample = "a:\n\tb:\n\t\tc: 1\n"
        out = self._norm(sample)
        self.assertEqual(out, "a:\n  b:\n    c: 1\n")

    def test_existing_space_indent_is_not_reindented(self) -> None:
        sample = "a:\n  b:\n      c: 1\n"
        self.assertEqual(self._norm(sample), sample)

    def test_bare_literal_true_false_null_become_canonical(self) -> None:
        sample = "a: True\nb: False\nc: ~\nd: NULL\ne: TRUE\nf: Null\n"
        out = self._norm(sample)
        self.assertEqual(out, "a: true\nb: false\nc: null\nd: null\ne: true\nf: null\n")

    def test_quoted_literals_untouched(self) -> None:
        sample = "a: \"True\"\nb: '~'\n"
        self.assertEqual(self._norm(sample), sample)

    def test_key_order_is_preserved_not_sorted(self) -> None:
        sample = "zeta: 1\nalpha: 2\nmid: 3\n"
        self.assertEqual(self._norm(sample), sample)

    def test_normalize_is_idempotent(self) -> None:
        sample = (
            "gates:\n"
            "  plan:\n"
            "    criticos: [{lente: L1, subagente: a}]\n"
            "    flags: [True, False, ~]\n"
            "    refutador: []\n"
            "\tweird: True\n"
            "# trailing comment\n"
        )
        once = self._norm(sample)
        twice = self._norm(once)
        self.assertEqual(twice, once)

    def test_preserves_comments(self) -> None:
        sample = (
            "# comentario de cabecera\n"
            "id: 0000-prueba\n"
            "\n"
            "modo: supervisado  # inline\n"
            f"mandato: {VALID_MANDATO}\n"
        )
        self.assertEqual(self._norm(sample), sample)

    def test_block_scalar_passthrough_keeps_body(self) -> None:
        sample = "body: |\n  line one\n  line two\nnext: 1\n"
        self.assertEqual(self._norm(sample), sample)

    def test_block_scalar_indentation_indicator_passthrough(self) -> None:
        """Regression (gate alta-4): `|2` / `|2-` / `>-1` open a block scalar
        so the body is not rewritten (`True` must stay `True`)."""
        for opener in ("|2", "|2-", ">-1", "|+", "|"):
            with self.subTest(opener=opener):
                sample = f"body: {opener}\n   keep: True\nnext: 1\n"
                self.assertEqual(self._norm(sample), sample)

    def test_list_item_flow_expands_recursively(self) -> None:
        """Regression (gate media): first pair of `- {k: <flow>, …}` and
        `- key: {…}` / `- key: […]` must not leave residual flow."""
        cases = [
            (
                "criticos:\n  - {cfg: {a: 1}, lente: L1}\n",
                "criticos:\n  - cfg:\n      a: 1\n    lente: L1\n",
            ),
            ("criticos:\n  - cfg: {a: 1}\n", "criticos:\n  - cfg:\n      a: 1\n"),
            ("criticos:\n  - keys: [a, b]\n", "criticos:\n  - keys:\n      - a\n      - b\n"),
        ]
        import yaml

        for sample, expected in cases:
            with self.subTest(sample=sample.strip()):
                out = self._norm(sample)
                self.assertEqual(out, expected)
                self.assertEqual(yaml.safe_load(out), yaml.safe_load(sample))
                self.assertEqual(self._norm(out), out)

    def test_flow_expansion_round_trips_yaml_semantics(self) -> None:
        import yaml

        sample = "a: [1, 2]\nb: {x: True, y: ~}\n"
        out = self._norm(sample)
        self.assertEqual(
            yaml.safe_load(out),
            yaml.safe_load(sample),
        )


#: CA-06 fixed seed set (plan.md § Decisiones de diseño): the minimum set of
#: forms `normalize_canonical` must preserve, each round-tripped through
#: `yaml.safe_load` equality. Replaces the old per-file walk of a retired
#: fixture tree (D-11) with an explicit, named catalogue so no form silently
#: drops out of coverage.
ROUND_TRIP_SEEDS: dict[str, str] = {
    "block_style": (
        "id: 0000-prueba\n"
        "modo: supervisado\n"
        "fase: plan\n"
        f"mandato: {VALID_MANDATO}\n"
    ),
    "flow_style": "gates:\n  plan:\n    criticos: [{lente: L1, subagente: a}]\n",
    "lists_with_dashes": (
        "gobernanza:\n"
        "  - pol-dev-nomenclatura-english-only\n"
        "  - pri-gob-fuente-verdad-unica\n"
    ),
    "empty_lists": "hallazgos: []\ncriticos: []\n",
    "null_and_booleans": "a: True\nb: False\nc: ~\nactivo: true\ncerrado: null\n",
    "line_and_trailing_comments": (
        "# comentario de cabecera\n"
        "id: 0000-prueba\n"
        "\n"
        "modo: supervisado  # inline trailing comment\n"
        f"mandato: {VALID_MANDATO}\n"
    ),
    "non_canonical_4_space_indent": "a:\n    b:\n        c: 1\n",
    "tilde_key": "dueño: \"Julian Cardona Galeano\"\ncarril: sdd\n",
    "string_with_colon_and_quotes": (
        "titulo: \"Fixture: caso con dos puntos\"\n"
        "detalle: 'texto con \"comillas\" internas'\n"
    ),
    "nested_block": (
        "gates:\n"
        "  plan:\n"
        "    veredicto: aprobado\n"
        "    criticos:\n"
        "      - lente: L1\n"
        "        subagente: a\n"
    ),
}


class RoundTripTests(unittest.TestCase):
    """CA-06: for every inline seed of `ROUND_TRIP_SEEDS`, applying
    `normalize_canonical` then re-parsing with `yaml.safe_load` must yield the
    same value as the original. PyYAML is used here in the test only."""

    def setUp(self) -> None:
        import yaml

        self._yaml = yaml

    def test_each_seed_round_trips(self) -> None:
        for name, text in ROUND_TRIP_SEEDS.items():
            with self.subTest(seed=name):
                normalized = sdd_safe_write_canonical.normalize_canonical(text)
                with tempfile.TemporaryDirectory(
                    prefix="sdd-safe-write-roundtrip-"
                ) as tmp_str:
                    target = Path(tmp_str) / "_estado.yaml"
                    target.write_text(text, encoding="utf-8")
                    atomic_write_text(target, normalized)
                    rewritten = target.read_text(encoding="utf-8")
                self.assertEqual(
                    self._yaml.safe_load(rewritten),
                    self._yaml.safe_load(text),
                )


class AtomicWriteTests(unittest.TestCase):
    """CA-10: atomic rewrite via `tempfile + os.replace`. A crash between the
    tempfile write and the replace leaves the original `target` untouched."""

    def setUp(self) -> None:
        self._tmp = Path(tempfile.mkdtemp(prefix="sdd-safe-write-atomic-"))
        self.target = self._tmp / "_estado.yaml"

    def tearDown(self) -> None:
        shutil.rmtree(self._tmp, ignore_errors=True)

    def test_atomic_write_under_keyboard_interrupt(self) -> None:
        original_bytes = (
            b"id: 0000-prueba\n"
            b"modo: supervisado\n"
            b"fase: plan\n"
            b"estado: en-progreso\n"
            b"mandato: plan-ejemplo\n"
        )
        self.target.write_bytes(original_bytes)
        with mock.patch(
            "sdd_safe_write.os.replace",
            side_effect=KeyboardInterrupt,
        ), self.assertRaises(KeyboardInterrupt):
            atomic_write_text(self.target, "new bytes")
        self.assertEqual(self.target.read_bytes(), original_bytes)
        residual = sorted(self._tmp.glob(f".*{self.target.name}.*"))
        for tmp in residual:
            self.assertFalse(
                tmp.exists(),
                f"tempfile residual no limpiado: {tmp}",
            )

    def test_atomic_write_succeeds_normally(self) -> None:
        original_bytes = b"id: 0000\nmodo: supervisado\n"
        self.target.write_bytes(original_bytes)
        atomic_write_text(self.target, "new bytes")
        self.assertEqual(self.target.read_bytes(), b"new bytes")
        residual = sorted(self._tmp.glob(f".*{self.target.name}.*"))
        self.assertEqual(residual, [])


class LoopBehaviorTests(SafeWriteRepoMixin, unittest.TestCase):
    """CA-05 / CA-11: the loop behaviour end-to-end. Valid `_estado.yaml`
    passes (no decision), invalid one blocks with a decision, non-`.yaml`
    basenames and out-of-repo paths and invalid JSON are silent no-ops."""

    def test_block_style_estado_passes(self) -> None:
        self.seed_plan()
        unit = self.make_unit(".spec/units/0000-unidad/")
        validator_check = subprocess.run(
            [
                sys.executable,
                str(self.scripts / "validate_mandate.py"),
                "--unidad",
                str(unit),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            validator_check.returncode,
            0,
            validator_check.stdout + validator_check.stderr,
        )
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIn(decision, (None, {}))
        lines = self.log_lines()
        self.assertTrue(
            any(" ok " in line for line in lines),
            f"expected 'ok' in log lines: {lines}",
        )

    def test_desatendido_estado_also_passes(self) -> None:
        """CA-30: the loop acts on `modo: desatendido` the same way it acts
        on `supervisado` -- condition (e) is not `supervisado`-only."""
        self.seed_plan()
        unit = self.make_unit(
            ".spec/units/0000-unidad/",
            estado_text=VALID_BLOCK_ESTADO_DESATENDIDO,
        )
        validator_check = subprocess.run(
            [
                sys.executable,
                str(self.scripts / "validate_mandate.py"),
                "--unidad",
                str(unit),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            validator_check.returncode,
            0,
            validator_check.stdout + validator_check.stderr,
        )
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIn(decision, (None, {}))
        lines = self.log_lines()
        self.assertTrue(
            any(" ok " in line for line in lines),
            f"expected 'ok' in log lines: {lines}",
        )

    def test_semantic_failure_blocks(self) -> None:
        self.seed_plan()
        unit = self.make_unit(
            ".spec/units/0000-unidad/",
            estado_text=(
                "id: 0000-prueba\n"
                "modo: supervisado\n"
                "fase: plan\n"
                "estado: en-progreso\n"
            ),
        )
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["decision"], "block")
        self.assertIn(
            "unidad-supervisado-sin-mandato",
            decision["reason"],
        )
        lines = self.log_lines()
        self.assertTrue(
            any("block" in line and "códigos:" in line for line in lines),
            f"expected 'block códigos:' in log lines: {lines}",
        )

    def test_md_file_is_noop(self) -> None:
        unit_dir = self.repo / ".spec" / "units" / "0000-unidad"
        unit_dir.mkdir(parents=True, exist_ok=True)
        plan = unit_dir / "plan.md"
        original = "# cabecera\n## Objetivo\ntexto\n"
        plan.write_text(original, encoding="utf-8")
        original_bytes = plan.read_bytes()
        original_mtime = plan.stat().st_mtime_ns
        result = self.run_loop(plan)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(plan.read_bytes(), original_bytes)
        self.assertEqual(plan.stat().st_mtime_ns, original_mtime)

    def test_outside_repo_is_noop(self) -> None:
        outside = self._tmp / "_estado.yaml"
        original = "id: foo\n"
        outside.write_text(original, encoding="utf-8")
        original_bytes = outside.read_bytes()
        result = self.run_loop(outside)
        self.assertEqual(result.returncode, 0)
        self.assertEqual(result.stdout.strip(), "")
        self.assertEqual(outside.read_bytes(), original_bytes)

    def test_invalid_json_is_noop(self) -> None:
        env = dict(os.environ)
        env.pop("SDD_GUARD_EXEMPT_TREE", None)
        env.pop("SDD_SAFE_WRITE_MAX_RETRIES", None)
        result = subprocess.run(
            [sys.executable, str(self.loop)],
            input="esto no es JSON",
            capture_output=True,
            text=True,
            check=False,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "")

    def test_guard_block_fixture_passes_loop(self) -> None:
        """CA-05: the block-style seed (valid mandato) exits 0."""
        unit = self._seed_unit_with_full_mandato(BLOCK_STYLE_ESTADO)
        validator_check = subprocess.run(
            [sys.executable, str(self.scripts / "validate_mandate.py"),
             "--unidad", str(unit)],
            capture_output=True, text=True, check=False,
        )
        self.assertEqual(
            validator_check.returncode, 0,
            validator_check.stdout + validator_check.stderr,
        )
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIn(decision, (None, {}))

    def test_guard_flow_fixture_blocks_missing_mandato(self) -> None:
        """CA-05: the flow-style seed (no `mandato:`) blocks."""
        self.seed_plan()
        unit = self.make_unit(".spec/units/0000-unidad/", estado_text=FLOW_STYLE_ESTADO)
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["decision"], "block")
        self.assertIn("unidad-supervisado-sin-mandato", decision["reason"])

    def test_missing_mandato_section_blocks(self) -> None:
        """CA-05 / seccion-ausente: block-style estado with a mandato file
        that lacks required anchors blocks with `seccion-ausente`."""
        unit = self._seed_unit_with_full_mandato(BLOCK_STYLE_ESTADO)
        (unit / "mandato.md").write_text("# Mandato\n\nSolo cabecera.\n", encoding="utf-8")
        result = self.run_loop(unit / "_estado.yaml")
        decision = self.decision(result)
        self.assertIsNotNone(decision)
        assert decision is not None
        self.assertEqual(decision["decision"], "block")
        self.assertIn("seccion-ausente", decision["reason"])


class LatencyTests(SafeWriteRepoMixin, unittest.TestCase):
    """Budget decision from `plan.md #7`: the loop stays under 1 s for a
    typical `_estado.yaml`."""

    def test_normal_loop_under_one_second(self) -> None:
        self.seed_plan()
        unit = self.make_unit(".spec/units/0000-unidad/")
        validator_check = subprocess.run(
            [
                sys.executable,
                str(self.scripts / "validate_mandate.py"),
                "--unidad",
                str(unit),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(
            validator_check.returncode,
            0,
            validator_check.stdout + validator_check.stderr,
        )
        target = unit / "_estado.yaml"
        start = time.monotonic()
        result = self.run_loop(target)
        elapsed = time.monotonic() - start
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertLess(
            elapsed,
            LOOP_TIME_BUDGET_SECONDS,
            f"loop sobre _estado.yaml canonico: {elapsed:.3f}s",
        )


class SelfLocationTests(unittest.TestCase):
    """Sanity check: `REPO_ROOT` resolves to the same path the hook uses
    (`parents[2]`). Guards against accidental relocation of the loop."""

    def test_repo_root_matches_parents_2_of_self(self) -> None:
        self.assertEqual(sdd_safe_write.REPO_ROOT, SCRIPTS.parent.parent)


if __name__ == "__main__":
    unittest.main()
