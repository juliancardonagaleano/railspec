"""Adversarial suite for the mandate protocol (`preflight.py` + `validate_mandate.py`).

Each scenario runs `sdd-preflight` (`.spec/scripts/preflight.py`) as a real subprocess
against a synthetic mandate/unit built fresh in an isolated temporary git repository —
never against `.spec/units/` real nor against `master` — and asserts the exact typed
stop code that `validate_mandate.CODES` reports for that friction, not merely that the
process exited non-zero. All four scenarios exercise the `validador` row of
`preflight.py` (the only point of the protocol that runs the mandate validator in
full): `supervised_conductor.py::run()` never invokes the validator's full pass, so a
typed code from `validate_mandate.CODES` is only observable through this row.

Every fixture here is a minimally-altered copy of the same base template — a
clean mandate proven to pass the validator with zero codes (see
`MANDATE_DEFAULTS`/`ESTADO_DEFAULTS` below) — so each scenario reduces to a
delta of one or two fields against a known-clean baseline instead of a
fixture built from scratch.

Discovery mechanism (how this file reaches future validation runs): this module lives
under `.spec/scripts/tests/`, the directory that any `comando_validacion` invoking
`pytest .spec/scripts/tests` (with or without `-k`) already collects by pytest's
standard test discovery — no separate registration, catalogue entry, or reference
elsewhere has to be kept in sync for a future run to pick this file up. That discovery
is forward-only: it changes what a *future* validation run collects, it does not reach
back and reopen a mandate whose own gate already ran, closed, or was approved before
this file existed.
"""

from __future__ import annotations

import shutil
import sys
from pathlib import Path

TESTS = Path(__file__).resolve().parent

sys.path.insert(0, str(TESTS))

# Reused, not reimplemented (`pol-dev-buscar-antes-de-crear`): `new_repo` builds the
# isolated temporary git repository `preflight.py` requires (`git_root_of`);
# `run_preflight` invokes the real script as a subprocess; `row_of` reads one row of
# its output table by name; `run_git` commits the synthetic seed (the git row BLOCKs
# on anything uncommitted under `--unit`); `MCP_OK` is the already-versioned MCP
# fixture that makes the `mcp` row PASS without noise. All five are plain,
# already-tested names of `test_preflight.py` — importing them keeps this suite's own
# contribution limited to the fixtures and the assertions, not a second copy of this
# plumbing.
from test_preflight import (  # noqa: E402,F401
    MCP_OK, new_repo, run_git, run_preflight, row_of,
)

# `CODES` is the closed universe of 30 typed stop codes (`pri-gob-fuente-verdad-
# unica`): every assertion below cites one of its literals, never an invented name.
from validate_mandate import CODES, Mandate  # noqa: E402,F401


# ======================================================================================
# Base fixture — anchor-complete (every entry of `validate_mandate.UNIT_ANCHORS` for
# the `unidad` form), an inline template. `Mandate.approval_hash()`
# (`validate_mandate.py:319-326`) only hashes `## Objetivo y criterio de salida` +
# `## Unidad amparada` + `## Delegaciones` — none of the three carries a placeholder
# below, so the approval hash already present stays valid across every scenario that
# formats this template. `## Objetivo y criterio de salida` and `## Unidad amparada`
# below are therefore copied verbatim from the source fixture (including its own
# `9202-fixture-aprobada-sin-retoma` unit reference, unrelated to any id used
# elsewhere in this template) instead of being reworded: any textual change to either
# section recomputes the hash and turns the stored `hash:` in `## Aprobación` stale,
# which the validator itself reports as `aprobacion-desactualizada` — confirmed while
# building this template. Nothing outside those two anchors reads that unit reference,
# so the mismatch with the `9420-mandato-adversarial` id used by the rest of this
# fixture (`## Registro de decisiones`, `_estado.yaml`) is inert, the same way the
# source fixture itself already mismatches its own directory name against this field.
# ======================================================================================

MANDATE_TEMPLATE = """# Mandato supervisado — fixture sintética del validador de mandatos

## Objetivo y criterio de salida

Demostrar que un mandato aprobado sin punto de retoma se reporta como retoma no
verificada.

**Criterio de salida verificable:** `validate_mandate.py --resumen --unidad
<directorio de esta fixture>` imprime `retoma sin punto verificado`.

## Unidad amparada

- unidad: `9202-fixture-aprobada-sin-retoma`
- directorio: (fixture sintética de este test, construida en un repo temporal)
- fase de la unidad: `plan`
- avance de la unidad: `en-progreso`

## Mandato

### 2026-09-17T08:00Z

- autor: Julian Cardona Galeano
- lanza: Julian Cardona Galeano
- inicio: 2026-09-17T08:00Z
- fin: 2026-09-24T08:00Z

## Delegaciones

### Pre-decididas

| id | decisión | pre-decisión | impacto |
|---|---|---|---|
| PD-1 | nombre del archivo de mandato de la unidad | `mandato.md` | ninguno fuera de la unidad |

### Con criterio

| id | decisión | criterio aplicable | impacto |
|---|---|---|---|
| CR-1 | orden de ejecución dentro del grupo | respetar el `depende de:` de `tasks.md` | ninguno fuera de la unidad |

### Reservadas

| id | decisión | por qué se reserva | impacto |
|---|---|---|---|
| RS-1 | aprobar este mandato | `pri-ia-humano-decide` | detiene el trabajo |

## Condiciones de parada

Las condiciones indelegables son las de `.spec/PARADAS-SUPERVISADO.md`, su única fuente.

## Paralelismo

- carriles: sdd
- tope-worktrees: 2
- dueno-stack-vivo: Julian Cardona Galeano
- presupuesto-mcp: 40 consultas a `pce-mcp` para toda la ventana del mandato
- conducta-presupuesto-agotado: parar — condición «MCP ausente o sin presupuesto» de la lista de paradas

## Registro de decisiones

### 9420-D1

- tipo: autonoma
- unidad: 9420-mandato-adversarial
- que-se-decidio: ejecutar las tareas del fixture en el orden declarado
- alternativas: reordenarlas por afinidad de archivo
- criterio: CR-1
- reversion: rehacer el orden en una tarea nueva de la misma unidad
- revision: {revision}
- revision-fecha: 2026-09-17
- revision-quien: Julian Cardona Galeano

## Paradas

Sin entradas.

## Punto de retoma

Sin entradas: nunca se paró, así que no hay punto verificado.

## Estado

- estado: {estado}

## Instancia en curso

Sin instancia.

## Aprobación

### 2026-09-17T09:00Z

- quien: Julian Cardona Galeano
- hash: {hash}
- fase: plan
- artefactos: spec+plan

## Revisión posterior

Vacía.
"""

ESTADO_TEMPLATE = """# Fixture sintética para la suite adversarial del validador de mandatos.
id: 9420-mandato-adversarial
titulo: "Fixture — validador de mandatos, suite adversarial"
dueño: "Julian Cardona Galeano"
carril: "sdd"
fase: {fase}
estado: {estado}
creado: "2026-09-17T00:00:00Z"
actualizado: "2026-09-17T09:00:00Z"
governance_refs: [pri-ia-humano-decide]
comando_validacion: "true"
modo: supervisado
mandato: {mandato}
riesgo: alto
gates: {{}}
validaciones_mandato: []
"""


# ======================================================================================
# Fixture builder — one synthetic unit per scenario, formatted from the two templates
# above with a 1-2 field delta (plan.md § Decisiones de diseño). `MANDATE_DEFAULTS`/
# `ESTADO_DEFAULTS` are the values of a template proven to pass the validator with
# zero codes (`test_scenario_defaults_pass_with_zero_codes` below): a call that
# overrides nothing reproduces that same clean run; every scenario below overrides
# only the field(s) plan.md names for it.
# ======================================================================================

UNIT_NAME = "9420-mandato-adversarial"


def _base_mandate_hash() -> str:
    """`Mandate.approval_hash()` only hashes `## Objetivo y criterio de salida` +
    `## Unidad amparada` + `## Delegaciones`; no scenario below overrides any field
    inside those three, so this is the same constant hash for every call to
    `build_unit` — computed once here (against a throwaway copy) instead of hand-
    maintained as a literal that silently goes stale the next time the template's
    prose changes."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp_dir:
        probe = Path(tmp_dir) / "mandato.md"
        probe.write_text(
            MANDATE_TEMPLATE.format(revision="x", estado="x", hash="0" * 64),
            encoding="utf-8",
        )
        return Mandate(probe, "unidad", "mandato.md", Path(tmp_dir)).approval_hash()


MANDATE_DEFAULTS = {
    "revision": "pendiente",
    "estado": "aprobado",
    "hash": _base_mandate_hash(),
}

ESTADO_DEFAULTS = {
    "fase": "plan",
    "estado": "en-progreso",
    "mandato": "mandato.md",
}


def build_unit(repo: Path, *, mandate: dict[str, str] | None = None,
               estado: dict[str, str] | None = None,
               unit_name: str = UNIT_NAME) -> Path:
    """Writes `mandato.md` + `_estado.yaml` under `repo/.spec/units/<unit_name>/`,
    formatting the two base templates with `MANDATE_DEFAULTS`/`ESTADO_DEFAULTS`
    overridden by `mandate`/`estado`, commits the seed (the `git` row of `preflight.py`
    BLOCKs on anything uncommitted under `--unit`) and returns the unit directory."""
    mandate_fields = {**MANDATE_DEFAULTS, **(mandate or {})}
    estado_fields = {**ESTADO_DEFAULTS, **(estado or {})}
    unit_dir = repo / ".spec" / "units" / unit_name
    unit_dir.mkdir(parents=True)
    (unit_dir / "mandato.md").write_text(
        MANDATE_TEMPLATE.format(**mandate_fields), encoding="utf-8")
    (unit_dir / "_estado.yaml").write_text(
        ESTADO_TEMPLATE.format(**estado_fields), encoding="utf-8")
    run_git(repo, "add", "-A")
    run_git(repo, "commit", "-q", "-m", "seed")
    return unit_dir


def codes_of(stdout: str) -> list[str]:
    """The payload of the `codigos: …` line among the `validador` row's extra lines
    (`preflight.py`'s own table format — `f"  codigos: {{' '.join(codes)}}"`, not
    `validate_mandate.py`'s `códigos:` line with the accent). Same parse
    `test_preflight.py::ValidatorRowTests.scenario` already does on this same line."""
    for line in stdout.splitlines():
        stripped = line.strip()
        if stripped.startswith("codigos: "):
            payload = stripped[len("codigos: "):].strip()
            return [] if payload == "ninguno" else payload.split()
    raise AssertionError(f"la fila `validador` no trae línea `codigos:`:\n{stdout}")


def run_scenario(prefix: str, *, mandate: dict[str, str] | None = None,
                 estado: dict[str, str] | None = None) -> list[str]:
    """Builds the scenario's unit in a fresh temporary repo and runs `sdd-preflight`
    (`preflight.py`) against it for real (CA-02), with the `mcp` row pinned to the
    already-versioned `mcp-ok.json` fixture so `validador` is the only row that can
    BLOCK (plan.md § Reutilización). Asserts the `validador` row itself BLOCKed and
    the overall exit is `1` (BLOCK by rule), never `2` (`preflight-error` — an
    internal failure of the preflight itself, not a typed code) before handing back
    the codes it reported, so a caller's `assertIn` is checked against a run that
    actually exercised the validator, not a bare `returncode != 0` (CA-04)."""
    repo = new_repo(prefix)
    try:
        unit_dir = build_unit(repo, mandate=mandate, estado=estado)
        completed = run_preflight(
            ["--mandate", str(unit_dir / "mandato.md"), "--unit", str(unit_dir),
             "--mcp-config", str(MCP_OK)])
        assert completed.returncode == 1, completed.stdout
        assert row_of(completed.stdout, "validador").startswith("validador: BLOCK"), \
            completed.stdout
        return codes_of(completed.stdout)
    finally:
        shutil.rmtree(repo, ignore_errors=True)


# --- T2 — Escenario 1: fase mal escrita en `_estado.yaml` ------------------------------

def test_mandato_estado_inconsistente() -> None:
    """CA-02..CA-05. Origen: `bitacora.md` de esta unidad, entrada
    `2026-09-24T00:30:00Z` (mapeo escenario 1 → `mandato-estado-inconsistente`) y
    `validate_mandate.py:451-500` (`check_mandate_state`, regla (i)): `## Estado:
    cerrado` del mandato con la unidad sin cerrar (`_estado.yaml > fase: plan`, sin
    tocar; `estado: en-progreso` heredado del fixture base) es la única manifestación
    tipificada de una `fase:` desactualizada/mal escrita — no existe en los 30 códigos
    un chequeo de enum sobre `fase:` en sí misma (confirmado por lectura completa de
    `CODES`). `revision` sube de `pendiente` a `aceptada` respecto del fixture base
    (delta de 2 campos, no 1) únicamente para que `check_decisions` no dispare también
    `cierre-con-pendientes` como ruido: con `## Estado: cerrado` ya resuelto,
    `strictest_reading("cerrado")` es verdadero por igualdad, así que una decisión
    `autonoma`/`pendiente` habría producido un segundo código ajeno a lo que este caso
    demuestra.
    """
    codes = run_scenario("mandato-adversarial-t2-",
                         mandate={"estado": "cerrado", "revision": "aceptada"})
    assert "mandato-estado-inconsistente" in CODES
    assert "mandato-estado-inconsistente" in codes, codes


# --- T3 retirado -------------------------------------------------------------------
# Este escenario cubría `aprobacion-sin-precondicion-adr`, código que CA-29 retira de
# `validate_mandate.py` junto con toda referencia al ADR que introdujo el modo
# supervisado (dominio `ia` rechazado por `AGENTS.md`). Sin código que emitir, el
# escenario no tiene objeto: se elimina, no se sustituye.


# --- T4 — Escenario 3: `sdd-preflight` con un path que no corresponde a un mandato real

def test_unidad_mandato_inexistente() -> None:
    """CA-02..CA-05. Origen: `validate_mandate.py:399-403` (`resolve_unit`, rama que
    reporta `unidad-mandato-inexistente`) y `bitacora.md` de esta unidad, entrada
    `2026-09-24T00:30:00Z` (mapeo escenario 3 → este código). `--mandate`/`--unit`
    apuntan a un directorio y a un `mandato.md` **reales** — nunca a un archivo
    inexistente, que en cambio da `preflight-error` (exit 2, ya cubierto por
    `test_preflight.py::ExitCodeTests::
    test_fallo_interno_sin_ninguna_fila_evaluada`, y no es uno de los 30 códigos que
    CA-04 exige) — pero `_estado.yaml > mandato:` referencia un nombre de archivo
    (`mandato-inexistente.md`) que no existe en ese directorio, nunca el
    `mandato.md` real que sí se escribió.
    """
    codes = run_scenario("mandato-adversarial-t4-",
                         estado={"mandato": "mandato-inexistente.md"})
    assert "unidad-mandato-inexistente" in CODES
    assert "unidad-mandato-inexistente" in codes, codes


# --- T5 — Escenario 4: cierre prematuro con decisiones autónomas pendientes -----------

def test_cierre_con_pendientes() -> None:
    """CA-02..CA-05. Origen: `validate_mandate.py:699-756` (`check_decisions`, CA-14)
    y `bitacora.md` de esta unidad, entrada `2026-09-24T00:30:00Z` (mapeo escenario 4
    → este código, sobre `## Registro de decisiones` — nunca sobre `## Punto de
    retoma > unidades-en-curso`, que lee un chequeo distinto, `retoma-incompleta`,
    CA-17). El fixture deja `revision: pendiente` del fixture base sin tocar (la
    decisión `### 9420-D1` ya es `tipo: autonoma`) y sube `## Estado` del mandato a
    `cerrado`, la condición que `check_decisions` exige por igualdad estricta
    (`strictest_reading("cerrado")`). `_estado.yaml` pasa a `fase: done` +
    `estado: completado` (unidad genuinamente cerrada) para que `check_mandate_state`
    no dispare también `mandato-estado-inconsistente` como ruido: con la unidad
    cerrada, la condición `state == "cerrado" and not closed` de su regla (i) es
    falsa.
    """
    codes = run_scenario("mandato-adversarial-t5-",
                         mandate={"estado": "cerrado"},
                         estado={"fase": "done", "estado": "completado"})
    assert "cierre-con-pendientes" in CODES
    assert "cierre-con-pendientes" in codes, codes
