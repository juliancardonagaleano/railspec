"""CA-17: comparación mecánica entre `docs/inventario-extraccion.yaml` (CA-03)
y `installer/kit_manifest.yaml` — todo archivo marcado `viaja` está en el
manifiesto (por alguna entrada cuyo `source` lo cubre, directo o vía una
entrada de directorio que lo contiene); ningún `no-viaja` está en él.

El inventario solo cubre `.spec/scripts/*.py`/`*.sh` de nivel superior (su
propio alcance declarado, ver `docs/inventario-extraccion.yaml`); este test
no exige ni prohíbe que el manifiesto tenga entradas fuera de ese alcance
(`.agents/`, `.spec/perfiles.yaml`, etc.) — decidible por un tercero sin
reinterpretar la clasificación.
"""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
INVENTORY_PATH = REPO_ROOT / "docs" / "inventario-extraccion.yaml"
MANIFEST_PATH = REPO_ROOT / "installer" / "kit_manifest.yaml"
SCRIPTS_PREFIX = ".spec/scripts/"


def _read_yaml_body(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    body_lines = [line for line in text.splitlines() if not line.lstrip().startswith("#")]
    return yaml.safe_load("\n".join(body_lines))


def _inventory_entries() -> list[dict]:
    data = _read_yaml_body(INVENTORY_PATH)
    return data["entradas"]


def _manifest_sources() -> list[str]:
    data = _read_yaml_body(MANIFEST_PATH)
    return [e["source"] for e in data["entries"]]


def _manifest_covers(manifest_sources: list[str], scripts_relative_path: str) -> bool:
    """True if some manifest entry's `source` is exactly this path, or a
    directory entry that contains it."""
    for source in manifest_sources:
        if source == scripts_relative_path:
            return True
        if not source.endswith("/") and (
            scripts_relative_path == source or scripts_relative_path.startswith(source + "/")
        ):
            return True
        if scripts_relative_path.startswith(source.rstrip("/") + "/"):
            return True
    return False


def test_todo_viaja_esta_en_el_manifiesto() -> None:
    inventory = _inventory_entries()
    manifest_sources = _manifest_sources()
    faltantes = []
    for entry in inventory:
        if entry["categoria"] != "viaja":
            continue
        scripts_relative_path = SCRIPTS_PREFIX + entry["archivo"]
        if not _manifest_covers(manifest_sources, scripts_relative_path):
            faltantes.append(scripts_relative_path)
    assert not faltantes, f"marcados `viaja` sin cubrir en el manifiesto: {faltantes}"


def test_ningun_no_viaja_esta_en_el_manifiesto() -> None:
    inventory = _inventory_entries()
    manifest_sources = _manifest_sources()
    presentes = []
    for entry in inventory:
        if entry["categoria"] != "no-viaja":
            continue
        scripts_relative_path = SCRIPTS_PREFIX + entry["archivo"]
        if _manifest_covers(manifest_sources, scripts_relative_path):
            presentes.append(scripts_relative_path)
    assert not presentes, f"marcados `no-viaja` presentes en el manifiesto: {presentes}"


def test_el_inventario_tiene_al_menos_una_entrada_de_cada_categoria() -> None:
    """Guard: si el inventario alguna vez queda vacío de una categoría, los
    dos tests de arriba pasarían trivialmente sin verificar nada real."""
    inventory = _inventory_entries()
    categorias = {e["categoria"] for e in inventory}
    assert categorias == {"viaja", "no-viaja"}
