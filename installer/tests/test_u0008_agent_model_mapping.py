"""Unit 0008 — CA-01..06, CA-10: mapping `agent.<rol>.model` en opencode.jsonc.

Verifica que `sdd-kit/opencode.jsonc` declare los 18 entries de la sección
`agent` (9 roles base + 9 variantes `*-low|high|max|medium|xhigh`
materializadas en `.claude/agents/`), cada uno con:

- `_sdd_kit: true` como discriminador de fusión con marcador.
- `model` apuntando a la familia `minimax/*` nativa del harness.
- Split M3 vs M2.7-highspeed según capacidad del rol, replicando
  `.spec/perfiles.yaml` (críticos profundos / especificadores /
  planificadores / refutador → M3; resto → M2.7-highspeed).

Verifica además la ausencia de `provider`/`default_agent`/`model` global
(CA-10 — P1+P2 del spec).
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPENCODE_JSONC = REPO_ROOT / "opencode.jsonc"


def _load_opencode() -> dict:
    return json.loads(OPENCODE_JSONC.read_text(encoding="utf-8"))


# ----------------------- CA-01: opencode.jsonc parsea --------------------------


def test_u0008_ca01_opencode_jsonc_parses_as_strict_json() -> None:
    """El archivo debe ser JSON estricto (sin `//` comments — DD-2)."""
    json.loads(OPENCODE_JSONC.read_text(encoding="utf-8"))


# ----------------------- CA-02: 9 roles base -----------------------------------


BASE_ROLES = [
    "sdd-critico-cumplimiento",
    "sdd-critico-estructural",
    "sdd-critico-profundo",
    "sdd-especificar-redactor",
    "sdd-explorador",
    "sdd-implementador",
    "sdd-planificar-redactor",
    "sdd-refutador",
    "sdd-tareas-redactor",
]


@pytest.mark.parametrize("rol", BASE_ROLES)
def test_u0008_ca02_base_rol_declared(rol: str) -> None:
    data = _load_opencode()
    assert rol in data["agent"], f"rol base faltante: {rol}"


# ----------------------- CA-03: 9 variantes ------------------------------------


VARIANT_ROLES = [
    "sdd-critico-profundo-low",
    "sdd-critico-profundo-max",
    "sdd-especificar-redactor-medium",
    "sdd-especificar-redactor-xhigh",
    "sdd-implementador-high",
    "sdd-implementador-low",
    "sdd-implementador-max",
    "sdd-implementador-xhigh",
    "sdd-refutador-max",
]


@pytest.mark.parametrize("rol", VARIANT_ROLES)
def test_u0008_ca03_variant_rol_declared(rol: str) -> None:
    data = _load_opencode()
    assert rol in data["agent"], f"variante faltante: {rol}"


# ----------------------- CA-04: familia minimax/* ------------------------------


ALL_ROLES = BASE_ROLES + VARIANT_ROLES


@pytest.mark.parametrize("rol", ALL_ROLES)
def test_u0008_ca04_model_is_minimax_family(rol: str) -> None:
    data = _load_opencode()
    model = data["agent"][rol]["model"]
    assert model.startswith("minimax/"), (
        f"{rol} → modelo {model!r} no es de la familia minimax/*"
    )


# ----------------------- CA-05: split M3 vs M2.7-highspeed ---------------------


M3_ROLES = {
    "sdd-critico-profundo",
    "sdd-critico-profundo-low",
    "sdd-critico-profundo-max",
    "sdd-especificar-redactor",
    "sdd-especificar-redactor-medium",
    "sdd-especificar-redactor-xhigh",
    "sdd-planificar-redactor",
    "sdd-refutador",
    "sdd-refutador-max",
}

HIGH_SPEED_ROLES = {
    "sdd-critico-cumplimiento",
    "sdd-critico-estructural",
    "sdd-explorador",
    "sdd-implementador",
    "sdd-implementador-high",
    "sdd-implementador-low",
    "sdd-implementador-max",
    "sdd-implementador-xhigh",
    "sdd-tareas-redactor",
}


@pytest.mark.parametrize("rol", sorted(M3_ROLES))
def test_u0008_ca05_deep_reasoning_role_uses_m3(rol: str) -> None:
    data = _load_opencode()
    assert data["agent"][rol]["model"] == "minimax/MiniMax-M3", (
        f"{rol} debería usar minimax/MiniMax-M3"
    )


@pytest.mark.parametrize("rol", sorted(HIGH_SPEED_ROLES))
def test_u0008_ca05_other_role_uses_m27_highspeed(rol: str) -> None:
    data = _load_opencode()
    assert data["agent"][rol]["model"] == "minimax/MiniMax-M2.7-highspeed", (
        f"{rol} debería usar minimax/MiniMax-M2.7-highspeed"
    )


def test_u0008_ca05_partition_is_complete() -> None:
    """Los 18 roles cubre exactamente la unión M3 ∪ M2.7-highspeed."""
    assert M3_ROLES | HIGH_SPEED_ROLES == set(ALL_ROLES)
    assert M3_ROLES & HIGH_SPEED_ROLES == set()


# ----------------------- CA-06: _sdd_kit: true por entry ----------------------


@pytest.mark.parametrize("rol", ALL_ROLES)
def test_u0008_ca06_agent_entry_has_sdd_kit_marker(rol: str) -> None:
    data = _load_opencode()
    entry = data["agent"][rol]
    assert entry.get("_sdd_kit") is True, (
        f"{rol} no tiene _sdd_kit: true — discriminador ausente"
    )


# ----------------------- CA-10: sin provider/default_agent/model global --------


def test_u0008_ca10_no_global_provider_default_agent_or_model() -> None:
    data = _load_opencode()
    assert "provider" not in data, "el kit no debe declarar provider global (P1)"
    assert "default_agent" not in data, "el kit no debe declarar default_agent (P2)"
    assert "model" not in data, "el kit no debe declarar model global (P2)"
