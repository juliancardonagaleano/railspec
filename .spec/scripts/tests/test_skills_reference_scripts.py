"""CA-07 (`sdd-preflight` canonical + mirror + manifest) and CA-15 (the five
supervised-mode skills reference the six rituals by path, never describe them by
hand again) — unit 0114, G4.

Also covers D-12 (no numbered heading of `sdd-supervisado` changes) and the
`pol-dev-patron-retrieval` enforcement CA-15 extends to `sdd-preflight` (zero
`CONSULTAR` citations there — its only MCP contact is a connectivity probe inside
`preflight.py`, not a knowledge query, D-10).
"""

from __future__ import annotations

import re
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[1]
REPO_ROOT = SCRIPTS.parents[1]
AGENTS_SKILLS = REPO_ROOT / ".agents" / "skills"
CLAUDE_SKILLS = REPO_ROOT / ".claude" / "skills"
MANIFEST = CLAUDE_SKILLS / ".claude-skills-manifest.yaml"
PATTERNS_FIXTURE = REPO_ROOT / ".spec" / "_fixtures" / "patrones-prohibidos.txt"

#: D-12: the numbered headings of `sdd-supervisado/SKILL.md` as of this unit's own
#: rewrite (CA-29 legitimately dropped `### 1.4`'s ADR-consultation clause and
#: collapsed `## 11 Evidencia de piloto` + `## 12 Changelog` into a single `## 11
#: Changelog` — this baseline is reset here, once, for that deliberate change; from
#: now on it is the anti-drift floor for any *accidental* renumbering). Declared
#: inline, not read from a fixture.
FROZEN_HEADINGS_SDD_SUPERVISADO = """\
## 0. Identidad de la sesión
## 1. Lanzamiento
### 1.1 Lock de instancia única
### 1.2 Quién puede lanzar
### 1.3 Validador de aprobación
### 1.4 Marcado de las unidades
## 2. Flujo por unidad, dirigido por estado
## 3. Cadena de un plan
## 4. Decisiones bajo delegación
## 5. Paralelismo
## 6. Paradas y punto de retoma
### 6.A Visibilidad agregada (por defecto)
### 6.B Notificación diferenciada por severidad
### 6.C Reanudación determinista (re-fase, no reinicio)
## 7. Vencimiento
## 8. Retoma
## 9. Validador en el bucle
## 10. Cierre
## 11. Changelog\
"""

#: The five skills this unit's ritual-by-path substitution touches (spec.md CA-15).
FIVE_SKILLS = ["sdd-supervisado", "sdd-preflight", "sdd-implementar", "sdd-gate",
              "sdd-retomar"]

#: CA-15's own literal floor — the suite fails if any of these four is ever
#: removed from the fixture (ampliar la lista es editar el fixture; vaciarla no
#: debe pasar en silencio).
MINIMUM_PATTERNS = [
    r"validate_mandate\.py .*--(plan|unidad|hash|resumen)",
    r"^\s*-\s*(sesion|lanzador|inicio):",
    r"anexar al changelog de",
    r"capturado-en\.txt",
]


def _load_ritual_patterns() -> list[str]:
    patterns: list[str] = []
    for line in PATTERNS_FIXTURE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        cls, _, value = line.partition("|")
        if cls == "ritual":
            patterns.append(value)
    return patterns


def _skill_text(name: str) -> str:
    return (AGENTS_SKILLS / name / "SKILL.md").read_text(encoding="utf-8")


# ======================================================================================
# CA-07 — `sdd-preflight`: canonical + identical mirror + listed in the manifest
# ======================================================================================

class PreflightSkillMirrorTests(unittest.TestCase):

    def test_existe_la_canonica_y_su_espejo_es_byte_identico(self) -> None:
        canonical = AGENTS_SKILLS / "sdd-preflight" / "SKILL.md"
        mirror = CLAUDE_SKILLS / "sdd-preflight" / "SKILL.md"
        self.assertTrue(canonical.is_file(), canonical)
        self.assertTrue(mirror.is_file(), mirror)
        self.assertEqual(canonical.read_bytes(), mirror.read_bytes())

    def test_el_manifiesto_lo_lista(self) -> None:
        manifest = MANIFEST.read_text(encoding="utf-8")
        self.assertIn('"sdd-preflight/SKILL.md"', manifest)

    def test_las_cinco_skills_tocadas_tienen_canonica_y_espejo(self) -> None:
        for name in FIVE_SKILLS:
            with self.subTest(name=name):
                canonical = AGENTS_SKILLS / name / "SKILL.md"
                mirror = CLAUDE_SKILLS / name / "SKILL.md"
                self.assertTrue(canonical.is_file())
                self.assertTrue(mirror.is_file())
                self.assertEqual(canonical.read_bytes(), mirror.read_bytes())


# ======================================================================================
# CA-15 — the fixture keeps its four minimum patterns
# ======================================================================================

class RitualPatternFixtureFloorTests(unittest.TestCase):

    def test_el_fixture_conserva_los_cuatro_patrones_minimos(self) -> None:
        patterns = _load_ritual_patterns()
        for minimum in MINIMUM_PATTERNS:
            self.assertIn(minimum, patterns,
                         f"falta el patrón mínimo de CA-15: {minimum!r}")


# ======================================================================================
# CA-15 — none of the five skills describes a ritual by hand any more
# ======================================================================================

class SkillsReferenceScriptsByPathTests(unittest.TestCase):

    def test_ninguna_de_las_cinco_skills_casa_un_patron_de_ritual(self) -> None:
        compiled = [re.compile(p, re.MULTILINE) for p in _load_ritual_patterns()]
        for skill in FIVE_SKILLS:
            with self.subTest(skill=skill):
                text = _skill_text(skill)
                for pattern in compiled:
                    match = pattern.search(text)
                    self.assertIsNone(
                        match,
                        f"{skill}/SKILL.md casa el patrón prohibido "
                        f"{pattern.pattern!r} en «{match.group(0) if match else ''}»")

    def test_las_skills_citan_los_scripts_por_ruta(self) -> None:
        """Positivo, no solo negativo: cada skill que menciona un ritual lo hace
        citando `.spec/scripts/<script>`."""
        expectations = {
            "sdd-supervisado": ["instance_lock.py", "record_mandate_validation.py",
                                "report_git_sync.py", "mandate_anchor.py"],
            "sdd-preflight": ["preflight.py"],
            "sdd-implementar": ["record_mandate_validation.py"],
            "sdd-retomar": ["report_git_sync.py"],
        }
        for skill, scripts in expectations.items():
            text = _skill_text(skill)
            for script in scripts:
                with self.subTest(skill=skill, script=script):
                    self.assertIn(f".spec/scripts/{script}", text)


# ======================================================================================
# `pol-dev-patron-retrieval` enforcement (CA-15): `sdd-preflight` has zero
# `CONSULTAR` citations; the skills that already had the step keep it.
# ======================================================================================

class RetrievalPatternEnforcementTests(unittest.TestCase):

    def test_sdd_preflight_no_tiene_ninguna_cita_consultar(self) -> None:
        text = _skill_text("sdd-preflight")
        self.assertEqual(len(re.findall(r"CONSULTAR", text)), 0)

    # The old ADR-consultation test is retired (not renamed): CA-29 removes every
    # reference to the ADR that introduced this mode from live surfaces, including
    # the `**CONSULTAR** resolve_entity` step this test pinned inside
    # `sdd-supervisado/SKILL.md` — the skill no longer makes that query at all.

    def test_sdd_gate_conserva_sus_dos_consultas(self) -> None:
        text = _skill_text("sdd-gate")
        self.assertEqual(len(re.findall(r"\*\*CONSULTAR\*\*", text)), 2)


# ======================================================================================
# D-12 — no numbered heading of `sdd-supervisado` changes number or title
# ======================================================================================

class HeadingsUnchangedTests(unittest.TestCase):

    @staticmethod
    def _numbered_headings(text: str) -> str:
        return "\n".join(line for line in text.splitlines()
                         if re.match(r"^#{2,3} \d", line))

    def test_los_encabezados_numerados_de_sdd_supervisado_son_los_mismos_que_en_head(
        self) -> None:
        frozen = FROZEN_HEADINGS_SDD_SUPERVISADO.strip("\n")
        current = self._numbered_headings(_skill_text("sdd-supervisado")).strip("\n")
        self.assertEqual(frozen, current)


if __name__ == "__main__":
    unittest.main()
