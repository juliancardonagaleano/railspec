"""Tests for `.spec/scripts/_common.py` — the shared no-PyYAML parsing helpers.

Covers `block_list_of_dicts()` (block-style list-of-dicts primitive consumed
by `validate_mode_conversion.py` and `validate_gate_budget.py`) and a minimal
regression check that `field()`/`field_list()` keep working.
"""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SCRIPTS))

import _common  # noqa: E402


class BlockListOfDictsTests(unittest.TestCase):
    def test_returns_empty_list_when_key_is_absent(self) -> None:
        text = "otro_campo: valor\n"
        self.assertEqual(_common.block_list_of_dicts(text, "modo_conversion"), [])

    def test_returns_empty_list_for_inline_empty_list(self) -> None:
        text = "modo_conversion: []\n"
        self.assertEqual(_common.block_list_of_dicts(text, "modo_conversion"), [])

    def test_returns_empty_list_for_key_with_no_items(self) -> None:
        text = "modo_conversion:\notro_campo: valor\n"
        self.assertEqual(_common.block_list_of_dicts(text, "modo_conversion"), [])

    def test_returns_empty_list_for_scalar_value(self) -> None:
        text = "modo_conversion: interactivo\n"
        self.assertEqual(_common.block_list_of_dicts(text, "modo_conversion"), [])

    def test_parses_single_item(self) -> None:
        text = "modo_conversion:\n  - desde: interactivo\n    hacia: supervisado\n"
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [{"desde": "interactivo", "hacia": "supervisado"}],
        )

    def test_parses_two_items_with_continuations(self) -> None:
        text = (
            "modo_conversion:\n"
            "  - desde: interactivo\n"
            "    hacia: supervisado\n"
            "    tras: spec\n"
            "  - desde: supervisado\n"
            "    hacia: semi-autonomo\n"
        )
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [
                {"desde": "interactivo", "hacia": "supervisado", "tras": "spec"},
                {"desde": "supervisado", "hacia": "semi-autonomo"},
            ],
        )

    def test_unquotes_value(self) -> None:
        text = 'modo_conversion:\n  - desde: interactivo\n    en: "2026-09-25T10:00:00Z"\n'
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [{"desde": "interactivo", "en": "2026-09-25T10:00:00Z"}],
        )

    def test_strips_trailing_comment(self) -> None:
        text = "modo_conversion:\n  - desde: interactivo\n    por: julian   # decisión en checkpoint\n"
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [{"desde": "interactivo", "por": "julian"}],
        )

    def test_full_example_with_comment_and_multiple_fields(self) -> None:
        text = (
            "modo_conversion:\n"
            "  - desde: interactivo\n"
            "    hacia: supervisado\n"
            '    en: "2026-09-25T10:00:00Z"\n'
            "    por: julian   # decisión en checkpoint\n"
            "    tras: spec\n"
            "  - desde: supervisado\n"
            "    hacia: semi-autonomo\n"
        )
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [
                {
                    "desde": "interactivo",
                    "hacia": "supervisado",
                    "en": "2026-09-25T10:00:00Z",
                    "por": "julian",
                    "tras": "spec",
                },
                {"desde": "supervisado", "hacia": "semi-autonomo"},
            ],
        )

    def test_stops_at_next_top_level_key(self) -> None:
        text = (
            "modo_conversion:\n"
            "  - desde: interactivo\n"
            "    hacia: supervisado\n"
            "otro_campo: valor\n"
        )
        self.assertEqual(
            _common.block_list_of_dicts(text, "modo_conversion"),
            [{"desde": "interactivo", "hacia": "supervisado"}],
        )


class TopLevelBlockTests(unittest.TestCase):
    def test_returns_empty_string_when_key_is_absent(self) -> None:
        text = "otro_campo: valor\n"
        self.assertEqual(_common.top_level_block(text, "gates"), "")

    def test_returns_empty_string_for_scalar_value(self) -> None:
        text = "gates: valor\n"
        self.assertEqual(_common.top_level_block(text, "gates"), "")

    def test_returns_empty_string_for_inline_empty_dict(self) -> None:
        text = "gates: {}\n"
        self.assertEqual(_common.top_level_block(text, "gates"), "")

    def test_returns_indented_body_up_to_next_top_level_key(self) -> None:
        text = "gates:\n  spec:\n    veredicto: refinado\notro_campo: valor\n"
        self.assertEqual(
            _common.top_level_block(text, "gates"),
            "  spec:\n    veredicto: refinado",
        )

    def test_body_is_taken_to_end_of_file_when_no_next_key_follows(self) -> None:
        text = "gates:\n  spec:\n    veredicto: refinado\n"
        self.assertEqual(
            _common.top_level_block(text, "gates"),
            "  spec:\n    veredicto: refinado",
        )


class ChildBlocksTests(unittest.TestCase):
    def test_returns_empty_list_for_blank_block(self) -> None:
        self.assertEqual(_common.child_blocks(""), [])
        self.assertEqual(_common.child_blocks("\n\n"), [])

    def test_two_children(self) -> None:
        block = "  spec:\n    veredicto: refinado\n  plan:\n    veredicto: pendiente\n"
        self.assertEqual(
            _common.child_blocks(block),
            [
                ("spec", "    veredicto: refinado"),
                ("plan", "    veredicto: pendiente"),
            ],
        )

    def test_child_with_list_body(self) -> None:
        block = (
            "  spec:\n"
            "    criticos:\n"
            "      - {modelo: sonnet}\n"
            "      - {modelo: opus}\n"
        )
        self.assertEqual(
            _common.child_blocks(block),
            [
                (
                    "spec",
                    "    criticos:\n      - {modelo: sonnet}\n      - {modelo: opus}",
                )
            ],
        )


class RegressionFieldTests(unittest.TestCase):
    def test_field_returns_scalar_value(self) -> None:
        text = "fase: implementar\n"
        self.assertEqual(_common.field(text, "fase"), "implementar")

    def test_field_returns_empty_string_when_absent(self) -> None:
        self.assertEqual(_common.field("otro: valor\n", "fase"), "")

    def test_field_list_parses_block_bullets(self) -> None:
        text = "campo:\n  - a\n  - b\n"
        self.assertEqual(_common.field_list(text, "campo"), ["a", "b"])

    def test_field_list_parses_inline_list(self) -> None:
        text = 'campo: ["a", "b"]\n'
        self.assertEqual(_common.field_list(text, "campo"), ["a", "b"])


if __name__ == "__main__":
    unittest.main()
