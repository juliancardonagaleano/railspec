"""CA-02 — Verificador de nomenclatura para U-0005.

Lee la convención declarada en `.spec/README.md` (anchor `# nomenclatura:`)
y la aplica sobre una lista configurable de directorios. **No** hardcodea la
convención: el anchor del README es la fuente única.

Por extensión:
- `.py` → snake_case
- `.sh` → kebab-case
- `.md` → kebab-case o single-word lowercase (sin `_` ni mayúsculas)
- `.yaml` → snake_case
- `.spec/units/<NNNN-slug>/` → NNNN-kebab-case-slug

Anomalías documentadas en la tabla "Excepciones vigentes" del README §
nomenclatura se omiten de la salida (load-bearing renames pospuestos).
"""

from __future__ import annotations

import re
from pathlib import Path

README_PATH = Path(__file__).resolve().parents[3] / ".spec" / "README.md"
NOMENCLATURA_ANCHOR = "# nomenclatura:"

DEFAULT_SCOPES: list[str] = [
    "scripts",
    "installer",
    ".spec/scripts",
    ".agents",
    ".claude",
    ".spec/units",
]


# --- Regex --------------------------------------------------------------------

_SNAKE_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(_[a-z0-9]+)*$")
_KEBAB_CASE_RE = re.compile(r"^[a-z][a-z0-9]*(-[a-z0-9]+)*$")
_SINGLE_WORD_LOWER_RE = re.compile(r"^[a-z][a-z0-9]*$")
_UNIT_SLUG_RE = re.compile(r"^[0-9]{4}-[a-z0-9]+(-[a-z0-9]+)*$")


# --- Public API ----------------------------------------------------------------


def parse_nomenclature(readme_text: str) -> dict[str, object]:
    """Parsea la sección `# nomenclatura:` del README y devuelve la convención.

    Devuelve un dict con:
    - ``extensions``: dict de extensión → ``"snake_case"`` | ``"kebab_case"`` |
      ``"kebab_or_single_word"``.
    - ``exceptions``: set de stems (sin extensión) que la convención excluye
      (load-bearing renames documentados).
    """
    lines = readme_text.splitlines()
    in_section = False
    extensions: dict[str, str] = {}
    exceptions: set[str] = set()
    in_exceptions_table = False

    for line in lines:
        if line.startswith("# "):
            in_section = (line == NOMENCLATURA_ANCHOR)
            in_exceptions_table = False
            continue
        if not in_section:
            continue
        stripped = line.strip()
        if stripped.startswith("| Extensión") or stripped.startswith("|---"):
            continue
        if stripped.startswith("### Excepciones vigentes"):
            in_exceptions_table = True
            continue
        if stripped.startswith("### ") or stripped.startswith("## "):
            in_exceptions_table = False
            continue
        if not stripped.startswith("|"):
            continue
        raw_cells = [c.strip() for c in stripped.strip("|").split("|")]
        cells = [c.strip("`").strip() for c in raw_cells]
        if len(cells) < 2:
            continue
        if not in_exceptions_table:
            ext_cell = cells[0]
            conv_cell = cells[1]
            if ext_cell in (".py", ".sh", ".md", ".yaml"):
                if "snake_case" in conv_cell:
                    extensions[ext_cell] = "snake_case"
                elif "kebab-case" in conv_cell and "single-word" not in conv_cell:
                    extensions[ext_cell] = "kebab_case"
                elif "kebab-case" in conv_cell:
                    extensions[ext_cell] = "kebab_or_single_word"
        else:
            name_cell = raw_cells[0]
            for name in re.findall(r"`([^`]+)`", name_cell):
                stem, _, _ext = name.rpartition(".")
                if stem:
                    exceptions.add(stem)

    return {"extensions": extensions, "exceptions": exceptions}


def expected_nomenclature_section_present(readme_text: str) -> bool:
    """``True`` si el anchor está exactamente una vez en el README."""
    matches = [ln for ln in readme_text.splitlines() if ln.startswith("# nomenclatura")]
    return len(matches) == 1


def _stem_is_snake(stem: str) -> bool:
    return bool(_SNAKE_CASE_RE.match(stem))


def _stem_is_kebab(stem: str) -> bool:
    return bool(_KEBAB_CASE_RE.match(stem))


def _stem_is_single_word_lower(stem: str) -> bool:
    return bool(_SINGLE_WORD_LOWER_RE.match(stem))


def _stem_is_kebab_or_single_word(stem: str) -> bool:
    return _stem_is_kebab(stem) or _stem_is_single_word_lower(stem)


def _stem_is_unit_slug(name: str) -> bool:
    return bool(_UNIT_SLUG_RE.match(name))


def _classify(stem: str, ext: str, rule: str) -> bool:
    if rule == "snake_case":
        return _stem_is_snake(stem)
    if rule == "kebab_case":
        return _stem_is_kebab(stem)
    if rule == "kebab_or_single_word":
        return _stem_is_kebab_or_single_word(stem)
    return True  # rule desconocida: no falla


def find_outliers(
    repo_root: Path,
    scopes: list[str] | None = None,
    nomenclature: dict[str, object] | None = None,
) -> list[tuple[str, str, str]]:
    """Recorre cada `scopes[i]` bajo `repo_root` y devuelve los outliers.

    Cada outlier es ``(ruta_relativa, nombre_archivo, regla_violada)`` donde
    ``ruta_relativa`` es relativa a ``repo_root``. La convención se obtiene
    del README (pasada en `nomenclature` o leída aquí si es ``None``).

    Filtros estructurales que el verificador **no** reporta (no son outliers
    relevantes para la convención):

    - ``__init__.py`` (marcador de paquete Python; sin stem propio).
    - ``SKILL.md``/``AGENTS.md`` — el nombre del archivo va por convención
      Claude Code; lo que importa es el nombre del directorio.
    - Archivos con stem vacío por ``_`` (e.g. ``_common.py``,
      ``_estado.yaml``): convención Python "private/internal".
    - Cache de pytest`` (``<root>/.pytest_cache/``).
    """
    if nomenclature is None:
        nomenclature = parse_nomenclature(README_PATH.read_text(encoding="utf-8"))
    extensions: dict[str, str] = nomenclature["extensions"]  # type: ignore[assignment]

    out: list[tuple[str, str, str]] = []
    for scope in scopes or DEFAULT_SCOPES:
        base = repo_root / scope
        if not base.is_dir():
            continue
        for path in sorted(base.rglob("*")):
            if not path.is_file():
                continue
            if any(part in {".pytest_cache", "__pycache__"} for part in path.parts):
                continue
            rel = path.relative_to(repo_root).as_posix()
            name = path.name
            if name in {"__init__.py", "SKILL.md", "AGENTS.md", "AGENT.md"}:
                continue
            stem, dot, ext = name.rpartition(".")
            if not dot:
                continue
            if not stem or stem.startswith("_"):
                continue
            if scope == ".spec/units" and path.parent == base:
                if not _stem_is_unit_slug(name):
                    out.append((rel, name, "unit_slug (NNNN-kebab-case-slug)"))
                continue
            rule = extensions.get(f".{ext}")
            if rule is None:
                continue
            if not _classify(stem, f".{ext}", rule):
                out.append((rel, name, rule))
    return out


def run(
    repo_root: Path | None = None,
    scopes: list[str] | None = None,
) -> int:
    """Punto de entrada CLI: imprime outliers (uno por línea) y retorna 0."""
    root = repo_root or README_PATH.parents[1]
    outliers = find_outliers(root, scopes)
    for rel, name, rule in outliers:
        print(f"{rel}:{name} ({rule})")
    return 0