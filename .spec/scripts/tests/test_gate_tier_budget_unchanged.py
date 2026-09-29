"""Golden-file regression for CA-13 (unit 0116, G3): no gate of any tier may
*silently* reduce, relative to the table `.spec/README.md` § "Gates de
validación" publishes, its critics-per-iteration nor its max-iterations.

This does **not** hardcode the numbers from memory of an earlier session: it
parses `.spec/README.md`'s own table (the "tabla vigente", literal wording of
`tasks.md`) and pins it to the values it holds today —

    | bajo  | 1 | 1 | no  |
    | medio | 1 | 1 | no  |
    | alto  | 4 | 2 | sí  |

— so that if `.spec/README.md` is ever silently edited to a lower number,
this test fails naming exactly which cell moved (CA-13's own wording: "no
reduce ... respecto a la tabla vigente de `.spec/README.md`"). These specific
golden values were themselves lowered twice, deliberately: `0117-D4`/`0117-D5`
(2026-09-20, Julian Cardona Galeano — see `.spec/planes/
plan-ejemplo/plan.md § Registro de decisiones`) bajaron
`medio` de 2 críticos/2 iteraciones a 1/1 (panel consolidado en un solo
crítico multi-lente) y `alto` de 3 a 2 iteraciones (mismo trigger de
convergencia "solo si quedó ambiguo" que ya regía `medio`). `0117-D6`
(2026-09-20, misma sesión, decisión directa de Julian pidiendo reformular el
uso de los gates de críticos) bajó además `alto` de 4 críticos/iteración
(uno por lente) a 2 (cada uno cubre la mitad de los lentes activos), y
sacó el panel por completo de `plan`/`tasks` (quedan solo con la capa
determinista — fuera de esta tabla, que describe únicamente `spec`/`codigo`).
CA-13 protege contra una reducción *sin* esa clase de decisión explícita y
registrada — no contra la reducción en sí. `.spec/README.md` en sí sigue
fuera de alcance de esta unidad (plan.md § Decisiones de diseño — `RS-2`
reservado a Julian para dónde vive el archivo, no para su contenido) — este
test solo fija su contenido vigente, nunca lo edita.
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent
README = REPO_ROOT / ".spec" / "README.md"

EXPECTED = {
    "bajo": {"criticos": "1", "iteraciones": "1", "adversarial": "no"},
    "medio": {"criticos": "1", "iteraciones": "1", "adversarial": "no"},
    "alto": {"criticos": "2", "iteraciones": "2", "adversarial": "sí"},
}


def _gates_table_rows() -> dict[str, dict[str, str]]:
    text = README.read_text(encoding="utf-8")
    # Locate the "## Gates de validación" section, then its first pipe table.
    m = re.search(r"^## Gates de validación\n(.*?)(?=^## |\Z)", text,
                  re.MULTILINE | re.DOTALL)
    if m is None:
        raise AssertionError("`.spec/README.md` no tiene sección "
                              "`## Gates de validación`")
    section = m.group(1)
    table_lines = [ln for ln in section.splitlines() if ln.strip().startswith("|")]
    if len(table_lines) < 2:
        raise AssertionError("`## Gates de validación` no tiene una tabla de tiers")

    def split_row(line: str) -> list[str]:
        inner = line.strip().strip("|")
        return [c.strip() for c in inner.split("|")]

    header = [c.lower() for c in split_row(table_lines[0])]
    rows = {}
    for line in table_lines[2:]:
        cells = split_row(line)
        if len(cells) != len(header):
            continue
        tier = cells[0].strip("`").lower()
        rows[tier] = {
            "criticos": cells[header.index("críticos por iteración")],
            "iteraciones": cells[header.index("máx. iteraciones")],
            "adversarial": cells[header.index("verificación adversarial")],
        }
    return rows


class GateTierBudgetUnchangedTests(unittest.TestCase):
    def test_readme_gates_table_matches_the_pinned_golden_values(self) -> None:
        rows = _gates_table_rows()
        self.assertEqual(set(rows.keys()), set(EXPECTED.keys()))
        for tier, expected in EXPECTED.items():
            with self.subTest(tier=tier):
                self.assertEqual(rows[tier], expected)

    def test_no_tier_is_below_its_golden_iteration_count(self) -> None:
        """Literal CA-13 direction: a *reduction* is what fails, not any
        drift — if a future edit ever raised a tier's budget this test alone
        would not catch it (that is not what CA-13 forbids)."""
        rows = _gates_table_rows()
        for tier, expected in EXPECTED.items():
            with self.subTest(tier=tier):
                self.assertGreaterEqual(
                    int(rows[tier]["iteraciones"]), int(expected["iteraciones"])
                )
                self.assertGreaterEqual(
                    int(rows[tier]["criticos"]), int(expected["criticos"])
                )


if __name__ == "__main__":
    unittest.main()
