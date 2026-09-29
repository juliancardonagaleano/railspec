#!/usr/bin/env python3
"""Supervised mandate validator.

No agents, no network, no external dependencies: the system's `python3` and stdlib.
Checks **exactly** the closed list of codes below (`CODES`).

Usage
-----
    validate_mandate.py --plan <id|ruta>     # goal-based plan + `_estado.yaml` of its members
    validate_mandate.py --unidad <ruta>      # isolated unit: its `mandato.md` + its `_estado.yaml`
    validate_mandate.py --codigos            # prints the closed list of codes
    validate_mandate.py --hash    --plan|--unidad <x>   # sha256 for the `## Aprobación` entry
    validate_mandate.py --resumen --plan|--unidad <x>   # fields that `sdd-retomar` transcribes

`--plan` accepts an id (resolved to `.spec/planes/<id>/plan.md`, members
under `.spec/units/`) or the path of an existing directory (fixture:
`<dir>/plan.md`, members under `<dir>/units/`).

Emission rules (CA-21)
-----------------------
* The six mandate-reference **resolution** codes are evaluated always and only in
  `--unidad` form (it is the unit that carries the reference). If the reference
  does not resolve, **only** that code is reported and content checks are skipped.
* The 24 **content** codes require a resolved mandate and accumulate **without
  short-circuiting**: the reported set is their union, with no precedence or
  suppression.
* **Warnings** are printed separately and do not count toward the exit code or the
  reported set.

Input format the parser consumes (plan.md § Decisiones de diseño, S-22): `## Ancla`
sections and, in registry sections, one entry per `### <clave>` block followed by
`- campo: valor` bullets. Delegations are table rows `| PD-n | … |` / `| CR-n | … |`
/ `| RS-n | … |` under their three `###`.

Exit 0 if there are no errors; 1 if there are (naming each code and its location).
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from _common import (  # noqa: E402
    FAILURES,
    WARNINGS,
    add_failure,
    add_warning,
    bullets,
    codes_from,
    date_key,
    entries,
    field,
    field_list,
    iso_date,
    normalize,
    reset,
    sections,
    sha256_text,
    units,
)

ROOT = Path(__file__).resolve().parents[2]      # repo root
PLANS = ROOT / ".spec" / "planes"
UNITS = ROOT / ".spec" / "units"

# --- Closed list of codes. Single source: `--codigos` prints it and
# `validate-supervised.sh` compares it against its own inline list.
CODES = [
    "seccion-ausente",
    "seccion-duplicada",
    "mandato-estado-inconsistente",
    "unidad-supervisado-sin-mandato",
    "unidad-mandato-vacio",
    "unidad-plan-inexistente",
    "unidad-mandato-inexistente",
    "mandato-forma-invalida",
    "unidad-mandato-doble",
    "plan-unidad-sin-referencia-inversa",
    "cadena-unidad-inexistente",
    "revision-sin-fecha",
    "mandato-sin-fin",
    "mandato-entrada-sin-fecha",
    "aprobacion-ausente",
    "aprobacion-desactualizada",
    "implementacion-sin-aprobacion",
    "aprobacion-sin-contexto",
    "parada-abierta-sin-estado-parado",
    "desbloqueo-no-autor",
    "decision-campo-ausente",
    "decision-id-duplicado",
    "decision-fuera-de-delegacion",
    "decision-sobre-reservada",
    "cierre-con-pendientes",
    "revertida-sin-tarea",
    "paradas-sin-referencia",
    "retoma-incompleta",
    "paralelismo-sin-declarar",
    "paralelismo-excede-tope",
]

# --- Mandatory anchors (CA-01 / CA-02). Transcribed from the spec; step 1 of the
# script cross-checks them against the templates and against `anclas-{plan,unidad}.txt`.
PLAN_ANCHORS = [
    "## Objetivo y criterio de salida",
    "## Unidades miembro",
    "## Cadena de dependencias",
    "## Registro de revisiones",
    "## Mandato",
    "## Delegaciones",
    "### Pre-decididas",
    "### Con criterio",
    "### Reservadas",
    "## Condiciones de parada",
    "## Paralelismo",
    "## Registro de decisiones",
    "## Paradas",
    "## Punto de retoma",
    "## Estado",
    "## Instancia en curso",
    "## Aprobación",
    "## Revisión posterior",
]
UNIT_ANCHORS = [
    "## Objetivo y criterio de salida",
    "## Unidad amparada",
    "## Mandato",
    "## Delegaciones",
    "### Pre-decididas",
    "### Con criterio",
    "### Reservadas",
    "## Condiciones de parada",
    "## Paralelismo",
    "## Registro de decisiones",
    "## Paradas",
    "## Punto de retoma",
    "## Estado",
    "## Instancia en curso",
    "## Aprobación",
    "## Revisión posterior",
]

MANDATE_STATES = ["borrador", "aprobado", "parado", "cerrado"]
DECISION_FIELDS = [
    "tipo",
    "unidad",
    "que-se-decidio",
    "alternativas",
    "criterio",
    "reversion",
    "revision",
    "revision-fecha",
    "revision-quien",
]
PARALLELISM_FIELDS = [
    "carriles",
    "tope-worktrees",
    "dueno-stack-vivo",
    "presupuesto-mcp",
    "conducta-presupuesto-agotado",
]
RESUME_FIELDS = ["rama", "commits", "validacion", "unidades-en-curso", "pasos"]
MAX_CAP = 4                                      # D-11 (plan) y H14/S-15 (unidad aislada)
STOPS_REF = "PARADAS-SUPERVISADO.md"             # CA-15
# Chain of `## Cadena de dependencias` (CA-07): the connector separates the members —
# `/` among them, which is how the first plan writes a parallel branch (`0108c/0102`).
CHAIN_CONNECTOR_RE = re.compile(r"→|->|/")
# A token is an id only when it matches **whole**: `(core-)?NNNN[a-z]?`, optionally
# followed by the slug of its directory (`9401-fixture-miembro`), which is how a plan
# may cite a member by its full directory name. Any other word — prose, `ADR-0044`,
# a label — is not an id.
CHAIN_TOKEN_RE = re.compile(r"^((?:core-)?\d{4}[a-z]?)(?:-[a-z0-9][a-z0-9-]*)?$")
ISO_IN_TEXT_RE = re.compile(
    r"\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2})?(?:Z|[+-]\d{2}:?\d{2})?)?"
)
PLAN_ID_RE = re.compile(r"^[a-z0-9]+(-[a-z0-9]+)*$")


# ======================================================================================
# Mandate model
# ======================================================================================

class Mandate:
    """An already-resolved supervised mandate: its text, its form, and its unit root."""

    def __init__(self, path: Path, form: str, reference: str, units_root: Path):
        self.path = path
        self.form = form                   # "plan" | "unidad"
        self.reference = reference         # plan id or file path
        self.units_root = units_root
        self.text = path.read_text(encoding="utf-8")
        self.sec = sections(self.text)
        self.where = str(path)

    def body(self, anchor: str) -> str:
        return self.sec.get(anchor, "")

    # --- Mandate state (closed vocabulary, CA-01d) ---
    def state_resolution(self) -> tuple[str, str]:
        """`(state, problem)` for `## Estado`.

        Only the canonical form resolves: the bullet `- estado: <valor>`, or a
        section whose whole body is a single line of the closed vocabulary. Anything
        else — an empty section, prose naming two of the four terms, a value outside
        the vocabulary — leaves the state **unresolved** and returns the reason.
        Scanning the prose for the first matching word (what this used to do) turned
        the rules keyed on the state off in silence.
        """
        body = self.body("## Estado")
        declared = bullets(body).get("estado", "").strip().strip("`").lower()
        if declared:
            if declared in MANDATE_STATES:
                return declared, ""
            return "", (f"`## Estado` declara «{declared}», fuera del vocabulario "
                        f"cerrado ({', '.join(MANDATE_STATES)})")

        lines = [line.strip() for line in body.splitlines() if line.strip()]
        if not lines:
            return "", "`## Estado` no declara ningún valor"
        if len(lines) == 1:
            single = lines[0].strip("`").strip(".").lower()
            if single in MANDATE_STATES:
                return single, ""
        found = sorted({w for w in re.findall(r"[a-záéíóú]+", body.lower())
                        if w in MANDATE_STATES})
        if len(found) == 1:
            return "", (f"`## Estado` no usa la forma canónica `- estado: "
                        f"{found[0]}`: el valor está enterrado en prosa")
        return "", ("`## Estado` no resuelve a un único valor del vocabulario "
                    f"cerrado (encontrados: {', '.join(found) or 'ninguno'})")

    @property
    def state(self) -> str:
        """The **declared** state, or `""` when `## Estado` does not resolve.

        An unresolved state is never replaced here by an assumed one: assuming
        `borrador` made `plan-unidad-sin-referencia-inversa` (CA-04) degrade from error
        to warning, so an ambiguous `## Estado` bought a plan a false PASS. Checks that
        get **stricter** when the state takes a given value read `strictest_reading()`
        instead; checks that get stricter when it does **not** (an open stop without
        `parado`, a member without back reference on a plan that is not `borrador`)
        compare against this property, and `""` never matches — which is the strict
        reading for them too.
        """
        return self.state_resolution()[0]

    def strictest_reading(self, state: str) -> bool:
        """Does the mandate count as `state` for a check that gets **stricter** when it
        does (`retoma-incompleta` on `parado`, `cierre-con-pendientes` on `cerrado`)?

        A resolved state answers by equality. An unresolved one answers `True` — the
        strictest applicable reading — but **only in `plan` form**, which is where
        nothing else reports it: `check_mandate_state` cannot emit
        `mandato-estado-inconsistente` there (CA-21 declares it «solo unidad aislada»)
        and the closed list of 30 admits no second code, so without this the rules keyed
        on the state would switch off in silence. In `unidad` form the unresolved state
        is already reported as its own failure, so it is not read as anything else.
        """
        return self.state == state or (not self.state and self.form == "plan")

    def state_as(self, state: str) -> str:
        """Wording of a message keyed on `state`, so the report never claims a value the
        section does not declare: either it `es <state>` or it does not resolve and is
        **read as** `<state>` by the strictest reading."""
        return f"es `{state}`" if self.state else f"no resuelve y se lee como `{state}`"

    # --- `## Mandato` entries ---
    @property
    def mandate_entries(self) -> list[tuple[str, dict[str, str]]]:
        result = []
        for key, fields, _ in entries(self.body("## Mandato")):
            result.append((key, fields))
        return result

    def current_entry(self, until: str = "") -> dict[str, str] | None:
        """`## Mandato` entry with the highest `inicio` (≤ `until` if bounded).

        Both sides are normalized to the same granularity before comparing: a
        day-only `until` covers the **whole** day, so a renewal starting at
        `2026-09-18T09:00Z` is in force for an event dated `2026-09-18`. Comparing
        the two strings raw excluded that renewal and pointed at the previous one.
        """
        limit = date_key(until, end_of_day=True) if until else ""
        best, best_key = None, ""
        for key, fields in self.mandate_entries:
            start = date_key(fields.get("inicio", "")) or date_key(key)
            if not start:
                continue
            if limit and start > limit:
                continue
            if start >= best_key:
                best, best_key = fields, start
        return best

    # --- Delegations (CA-12, D-13) ---
    def delegations(self) -> tuple[set[str], set[str]]:
        """(delegated ids = PD-n ∪ CR-n, reserved ids = RS-n)."""
        delegated: set[str] = set()
        reserved: set[str] = set()
        for key, _, raw in entries(self.body("## Delegaciones")):
            ids = {m.group(1) for m in re.finditer(r"^\|\s*((?:PD|CR|RS)-\d+)\s*\|",
                                                   raw, re.MULTILINE)}
            if key.lower().startswith("reservadas"):
                reserved |= ids
            else:
                delegated |= ids
        return delegated, reserved

    # --- Approval hash (S-24) ---
    def approval_hash(self) -> str:
        anchors = ["## Objetivo y criterio de salida"]
        if self.form == "plan":
            anchors += ["## Unidades miembro", "## Cadena de dependencias"]
        else:
            anchors += ["## Unidad amparada"]
        anchors += ["## Delegaciones"]
        return sha256_text("\n".join(normalize(self.body(a)) for a in anchors))

    # --- Members (plan only) ---
    def members(self) -> list[str]:
        ids = []
        for m in re.finditer(r"^[ \t]*-[ \t]+`?([A-Za-z0-9][\w.-]*)`?",
                             self.body("## Unidades miembro"), re.MULTILINE):
            ids.append(m.group(1))
        return ids


# ======================================================================================
# Reference-resolution phase (CA-04) — `--unidad` form only
# ======================================================================================

def resolve_unit(unit_dir: Path) -> Mandate | None:
    """Resolves `mandato:` from a unit's `_estado.yaml`. Emits one of the six
    resolution codes and returns `None` if it does not resolve (fixed order from
    plan.md)."""
    state_file = unit_dir / "_estado.yaml"
    if not state_file.is_file():
        add_failure("unidad-supervisado-sin-mandato", str(unit_dir),
              "la unidad no tiene _estado.yaml")
        return None
    text = state_file.read_text(encoding="utf-8")
    where = str(state_file)

    list_ = field_list(text, "mandato")
    if list_ is not None and len(list_) > 1:
        add_failure("unidad-mandato-doble", where, f"`mandato` es una lista de {len(list_)}")
        return None

    if not re.search(r"^\s*mandato:", text, re.MULTILINE):
        if field(text, "modo") != "supervisado":
            print(f"AVISO — {where} tampoco declara `modo: supervisado`", file=sys.stderr)
        add_failure("unidad-supervisado-sin-mandato", where, "no declara el campo `mandato`")
        return None

    value = field(text, "mandato")
    if list_ is not None and len(list_) == 1:
        value = list_[0]
    if not value.strip():
        # U-0009 / DD-3: ``mandato: ""`` es válido en ``modo: desatendido`` (la
        # unidad se ampara bajo un mandato pre-existente archivado o no
        # requiere mandato formal; este caso es post-retiro del flujo de plan
        # por objetivo — la unidad no se ampara bajo ningún plan por objetivo).
        # En ``modo: supervisado`` sigue siendo rechazo ``unidad-mandato-vacio``.
        if field(text, "modo") == "desatendido":
            unit_mandate = unit_dir / "mandato.md"
            if not unit_mandate.is_file():
                unit_mandate = unit_dir / "plan.md"
            if unit_mandate.is_file():
                return Mandate(unit_mandate, "unidad", "mandato.md", UNITS)
        add_failure("unidad-mandato-vacio", where, "`mandato` presente pero vacío")
        return None

    value = value.strip()
    plan_file = PLANS / value / "plan.md"
    path_file = unit_dir / value
    has_plan = plan_file.is_file()
    has_path = path_file.is_file()

    # A path form is relative to the unit's own directory and must stay inside it.
    # `../otra/mandato.md` resolved and validated somebody else's mandate as if it
    # were this unit's own, so it is rejected by form, not by existence.
    if not has_plan:
        try:
            path_file.resolve().relative_to(unit_dir.resolve())
        except ValueError:
            add_failure("mandato-forma-invalida", where,
                  f"'{value}' apunta fuera del directorio de la unidad")
            return None

    if has_plan and has_path:
        add_failure("unidad-mandato-doble", where,
              f"'{value}' es a la vez id de plan existente y ruta existente")
        return None
    if has_plan:
        return Mandate(plan_file, "plan", value, UNITS)
    if has_path:
        return Mandate(path_file, "unidad", value, UNITS)
    if PLAN_ID_RE.match(value):
        add_failure("unidad-plan-inexistente", where, f"no existe .spec/planes/{value}/")
        return None
    if value.endswith(".md") or "/" in value:
        add_failure("unidad-mandato-inexistente", where, f"no existe {path_file}")
        return None
    add_failure("mandato-forma-invalida", where,
          f"'{value}' no es ni identificador de plan ni ruta")
    return None


def resolve_plan(arg: str) -> Mandate | None:
    """`--plan <id|ruta>`. An existing directory is a fixture with its own `units/`."""
    p = Path(arg)
    if p.is_dir():
        file = p / "plan.md"
        units_root = p / "units"
        reference = p.name
    else:
        file = PLANS / arg / "plan.md"
        units_root = UNITS
        reference = arg
    if not file.is_file():
        print(f"ERROR — no existe el plan: {file}", file=sys.stderr)
        return None
    return Mandate(file, "plan", reference, units_root)


# ======================================================================================
# Content phase — the 24 codes, accumulated without short-circuiting
# ======================================================================================

def check_sections(m: Mandate) -> None:
    anchors = PLAN_ANCHORS if m.form == "plan" else UNIT_ANCHORS
    # Counted over `m.text` raw (before `sections()` collapses repeats into a dict):
    # `sections()` keeps only the last occurrence in silence (line 156), which would
    # hide a duplicate from this check for the rest of the run if it counted over
    # `m.sec` instead.
    raw_lines = [line.strip() for line in m.text.splitlines()]
    for anchor in anchors:
        count = raw_lines.count(anchor)
        if count == 0:
            add_failure("seccion-ausente", m.where, f"falta el ancla «{anchor}»")
        elif count > 1:
            add_failure("seccion-duplicada", m.where,
                        f"el ancla «{anchor}» aparece {count} veces")


def check_mandate_state(m: Mandate, unit_state: dict[str, str]) -> None:
    """CA-01d + CA-02 (i)-(iii).

    An unresolved `## Estado` still must not switch off in silence the checks keyed on
    the state (open stop, resume point, closing with pending decisions), but the code
    is not available to say so in every form: CA-21 declares
    `mandato-estado-inconsistente` as «CA-02, **solo unidad aislada**», and the closed
    list of 30 admits no second code. So the two forms are treated differently, by
    decision of the code gate (it.2):

    * `unidad` — the unresolved state is the inconsistency and is reported as such.
    * `plan` — it goes to stderr as an advisory (it does not touch the reported set or
      the exit code, like the other advisories) and **no** state is assumed for the rest
      of the checks. Assuming `borrador` was the previous reading and it was not
      conservative: `borrador` is exactly the value that relaxes CA-04
      (`plan-unidad-sin-referencia-inversa` degrades to a warning there), so an
      ambiguous `## Estado` bought the plan a PASS it had not earned. The rule now is
      that an unresolved state is read, check by check, in the **strictest applicable**
      way: `Mandate.strictest_reading()` makes it count as `parado` for the resume point
      (CA-17) and as `cerrado` for the closing with pending decisions (CA-14), while
      `Mandate.state` stays `""` for the checks that demand more when the state is *not*
      a given value (open stop without `parado`, member without back reference).
    """
    state, problem = m.state_resolution()
    if problem:
        if m.form == "unidad":
            add_failure("mandato-estado-inconsistente", m.where, problem)
        else:
            print(f"AVISO — {m.where}: {problem}; las comprobaciones que dependen "
                  "del estado se evalúan en su lectura más exigente",
                  file=sys.stderr)
        return
    if m.form != "unidad" or not unit_state:
        return
    phase = unit_state.get("fase", "")
    progress = unit_state.get("estado", "")
    closed = phase == "done" or progress == "completado"
    if state == "cerrado" and not closed:
        add_failure("mandato-estado-inconsistente", m.where,
              "(i) mandato `cerrado` con la unidad sin cerrar")
    elif state == "borrador" and (phase == "implement" or closed):
        add_failure("mandato-estado-inconsistente", m.where,
              "(ii) unidad en `fase: implement` o cerrada con mandato `borrador`")
    elif state == "parado" and closed:
        add_failure("mandato-estado-inconsistente", m.where,
              "(iii) mandato `parado` con la unidad cerrada; se resuelve cerrando "
              "el mandato (`## Estado: cerrado`), y para eso toda parada de "
              "`## Paradas` necesita antes su desbloqueo "
              "(`desbloqueo-quien`/`-fecha`/`-que`), acto exclusivo del autor de "
              "la entrada de `## Mandato` vigente")


NO_IDS = "cadena declarada sin ningún id reconocible"


def chain_tokens(declared: str) -> list[str]:
    """Ids of **one** already-isolated chain line, in the order they are written.

    Annotations are dropped before tokenizing: parentheses (`(no amparada)`,
    `(ver ADR-0044)`) and backticks, plus every ISO date, so neither `0044` nor `2026`
    is ever cited as a unit. What is left is split by the connector and each token is
    accepted only if it matches `CHAIN_TOKEN_RE` **whole**. Matching a substring is what
    turned prose words and annotations into ids.
    """
    source = re.sub(r"\([^)]*\)", " ", declared).replace("`", " ")
    source = ISO_IN_TEXT_RE.sub(" ", source)
    ids: list[str] = []
    for raw in CHAIN_CONNECTOR_RE.split(source):
        token = raw.strip().strip("*_\"'“”.,;:")
        m = CHAIN_TOKEN_RE.match(token)
        if m and m.group(1) not in ids:
            ids.append(m.group(1))
    return ids


def chain_ids(body: str) -> tuple[list[str], str]:
    """`(ids, problem)` for the current chain declared in `## Cadena de dependencias`.

    The chain is read from **one** line, and the ids come from its tokens (see
    `chain_tokens`), not from scanning the text for anything that looks like a number:

    * The declaring line is the canonical `Vigente:` one — it may carry an annotation
      before the colon (`Vigente (revisada …):`), so only the text after the colon is
      read.
    * With no such line, the fallback walks the lines that carry the connector and takes
      the **first one that actually yields an id**. Taking the first line with a
      connector, whatever it produced, read prose as a chain: `/` appears in any path
      (`.spec/PARADAS-SUPERVISADO.md`) and in any `y/o`, and such a line yields no ids, so
      CA-07 was skipped in silence on a plan that did declare its chain further down.
    * A line that declares a chain and yields nothing is not silent either: the problem
      returned carries `NO_IDS` and `check_chain` prints it as an advisory. It is
      distinct from «the section declares no chain at all» (no connector anywhere).
    """
    for line in body.splitlines():
        m = re.match(r"^[ \t]*vigente\b[^:]*:(.*)$", line.strip(), re.IGNORECASE)
        if m:
            ids = chain_tokens(m.group(1))
            if ids:
                return ids, ""
            return [], (f"la línea `Vigente:` de `## Cadena de dependencias` es "
                        f"{NO_IDS}")

    some_connector = False
    for line in body.splitlines():
        if not CHAIN_CONNECTOR_RE.search(line):
            continue
        some_connector = True
        head = ISO_IN_TEXT_RE.sub(" ", line)
        ids = chain_tokens(head.split(":", 1)[1] if ":" in head else head)
        if ids:
            return ids, ""
    if some_connector:
        return [], (f"`## Cadena de dependencias` tiene {NO_IDS}: ninguna de sus "
                    "líneas con conector (`→`, `->`, `/`) produce un identificador "
                    "de unidad")
    return [], ("`## Cadena de dependencias` no declara una cadena: ni línea "
                "`Vigente:` ni línea con conector (`→`, `->`, `/`)")


def check_chain(m: Mandate) -> None:
    """CA-07: existence (not membership) of every unit cited in the current chain."""
    if m.form != "plan":
        return
    ids, problem = chain_ids(m.body("## Cadena de dependencias"))
    if problem:
        # No code of the closed list of 30 covers «chain not declared» (CA-21) and no
        # new one may be invented, but this must not be silent either: it goes to
        # stderr, like the `modo` advisory of `resolve_unit`, without touching the
        # reported set or the exit code.
        print(f"AVISO — {m.where}: {problem}", file=sys.stderr)
        return
    names = {d.name for d in units(m.units_root)}
    for tok in ids:
        if not any(n == tok or n.startswith(tok + "-") for n in names):
            add_failure("cadena-unidad-inexistente", m.where,
                  f"la cadena cita '{tok}', que no existe en {m.units_root}")


def check_reviews(m: Mandate) -> None:
    if m.form != "plan":
        return
    for key, fields, _ in entries(m.body("## Registro de revisiones")):
        if not (iso_date(key) or iso_date(fields.get("fecha", ""))):
            add_failure("revision-sin-fecha", m.where,
                  f"la revisión «{key}» no trae fecha ISO-8601")


def check_mandate_entries(m: Mandate) -> None:
    """CA-08: every entry dated; the current one requires a mandatory ISO-8601 `fin`."""
    for key, fields in m.mandate_entries:
        if not (iso_date(key) or iso_date(fields.get("fecha", ""))):
            add_failure("mandato-entrada-sin-fecha", m.where,
                  f"la entrada de mandato «{key}» no trae fecha ISO-8601")
    current = m.current_entry()
    if current is None:
        add_failure("mandato-sin-fin", m.where, "no hay entrada de mandato vigente con inicio")
        return
    if not iso_date(current.get("fin", "")):
        add_failure("mandato-sin-fin", m.where,
              "la entrada de mandato vigente no declara un `fin` ISO-8601")


def _approval_entries(m: Mandate):
    return entries(m.body("## Aprobación"))


def check_approval(m: Mandate, covered_units: list[dict[str, str]]) -> None:
    """CA-09 — the five approval codes."""
    approvals = _approval_entries(m)
    if not approvals:
        add_failure("aprobacion-ausente", m.where, "`## Aprobación` no tiene ninguna entrada")
    else:
        # Latest approval by date.
        latest_date, latest = "", {}
        for key, fields, _ in approvals:
            f = iso_date(key) or iso_date(fields.get("fecha", ""))
            if f and f >= latest_date:
                latest_date, latest = f, fields
        current = m.approval_hash()
        if latest.get("hash", "").strip().strip("`") != current:
            add_failure("aprobacion-desactualizada", m.where,
                  f"el hash de la última aprobación no es el vigente ({current[:12]}…)")
        else:
            for key, fields, _ in entries(m.body("## Registro de revisiones")):
                f = iso_date(key) or iso_date(fields.get("fecha", ""))
                if f and latest_date and f > latest_date:
                    add_failure("aprobacion-desactualizada", m.where,
                          f"la revisión «{key}» es posterior a la última aprobación")
                    break

        if m.form == "unidad":
            for key, fields, _ in approvals:
                if not fields.get("fase", "").strip() or \
                        not fields.get("artefactos", "").strip():
                    add_failure("aprobacion-sin-contexto", m.where,
                          f"la aprobación «{key}» no declara fase o artefactos a la vista")

    # `implementacion-sin-aprobacion`: covered unit in implement or later.
    if not approvals:
        for u in covered_units:
            if u.get("modo") != "supervisado":
                continue
            phase = u.get("fase", "")
            if phase in ("implement", "done") or u.get("estado") == "completado":
                add_failure("implementacion-sin-aprobacion", u.get("donde", m.where),
                      f"unidad supervisado en `fase: {phase}` sin aprobación del mandato")


def check_stops(m: Mandate) -> None:
    """CA-10 — open stop and the unblock author.

    Reads `m.state` by inequality on purpose: an unresolved state is `""`, which does not
    match `parado`, so the open stop is reported — already the strictest reading.
    """
    for key, fields, _ in entries(m.body("## Paradas")):
        who = fields.get("desbloqueo-quien", "").strip()
        when = iso_date(fields.get("desbloqueo-fecha", ""))
        what = fields.get("desbloqueo-que", "").strip()
        open_ = not (who and when and what)
        if open_:
            unit = fields.get("unidad", "").strip().strip("`")
            if unit and unit != "todas":
                # Unit-level stop (PARADAS-SUPERVISADO.md preamble): only that unit and its
                # direct dependents freeze; the mandate stays `aprobado` and the freeze is
                # recorded in `## Punto de retoma > unidades-en-curso`.
                continue
            if m.state != "parado":
                add_failure("parada-abierta-sin-estado-parado", m.where,
                      f"la parada «{key}» no tiene desbloqueo y `## Estado` = "
                      f"«{m.state or 'sin declarar'}»; hasta que el autor de la "
                      "entrada de `## Mandato` vigente escriba su "
                      "`desbloqueo-quien`/`-fecha`/`-que`, el estado del mandato "
                      "es `parado`")
            continue
        # `when` may be a day-only date; `current_entry` normalizes both sides so a
        # renewal that starts during that same day still counts as the one in force.
        current = m.current_entry(until=when)
        author = (current or {}).get("autor", "").strip()
        if who != author:
            add_failure("desbloqueo-no-autor", m.where,
                  f"la parada «{key}» la desbloquea '{who}', no el autor del "
                  f"mandato vigente ('{author or 'sin autor'}')")


def check_decisions(m: Mandate) -> None:
    """CA-11, CA-12, CA-14 — decision registry."""
    delegated, reserved = m.delegations()
    seen: set[str] = set()
    by_unit: dict[str, set[int]] = {}
    has_pending_autonomous = False

    for key, fields, _ in entries(m.body("## Registro de decisiones")):
        type_ = fields.get("tipo", "").strip().lower()

        # The literal `heredada:<id-unidad>` counts as a present criterion (CA-11):
        # it is enough for the field to be non-empty, which is what the list checks.
        missing = [c for c in DECISION_FIELDS if not fields.get(c, "").strip()]
        if missing:
            add_failure("decision-campo-ausente", m.where,
                  f"la decisión «{key}» no trae: {', '.join(missing)}")

        if key in seen:
            add_failure("decision-id-duplicado", m.where,
                  f"el identificador «{key}» aparece más de una vez")
        seen.add(key)
        mid = re.match(r"^(.*)-D(\d+)$", key)
        if mid:
            by_unit.setdefault(mid.group(1), set()).add(int(mid.group(2)))

        criterion = fields.get("criterio", "").strip().strip("`")
        if type_ == "autonoma":
            if criterion in reserved:
                add_failure("decision-sobre-reservada", m.where,
                      f"la decisión «{key}» cita la delegación reservada «{criterion}»")
            elif criterion not in delegated:
                add_failure("decision-fuera-de-delegacion", m.where,
                      f"la decisión «{key}» cita «{criterion or 'nada'}», que no es "
                      "pre-decisión ni criterio del mandato")

        review = fields.get("revision", "").strip().lower()
        if review == "pendiente" and type_ == "autonoma":
            has_pending_autonomous = True
        if review == "revertida" and not fields.get("revision-tarea", "").strip():
            add_failure("revertida-sin-tarea", m.where,
                  f"la decisión «{key}» está revertida y no referencia la tarea "
                  "nueva que la deshace")

    for unit, nums in by_unit.items():
        if nums and sorted(nums) != list(range(1, max(nums) + 1)):
            add_failure("decision-id-duplicado", m.where,
                  f"la secuencia de decisiones de «{unit}» tiene huecos: "
                  f"{sorted(nums)}")

    # Strictest reading: an unresolved `## Estado` on a plan counts as `cerrado` here,
    # so an ambiguous state cannot switch CA-14 off (see `Mandate.strictest_reading`).
    if m.strictest_reading("cerrado") and has_pending_autonomous:
        add_failure("cierre-con-pendientes", m.where,
              f"`## Estado` {m.state_as('cerrado')} con decisiones `autonoma` "
              "en `pendiente`; lo resuelve la revisión humana de cada una "
              "(`revision`, `revision-fecha`, `revision-quien`) más su entrada en "
              "`## Revisión posterior`, acto del autor del mandato — nunca del "
              "conductor")


def check_stop_conditions(m: Mandate) -> None:
    if STOPS_REF not in m.body("## Condiciones de parada"):
        add_failure("paradas-sin-referencia", m.where,
              f"`## Condiciones de parada` no referencia {STOPS_REF}")


def check_resume(m: Mandate) -> None:
    """CA-17 — only required when `## Estado` = `parado`, and — strictest reading — when
    it does not resolve at all on a plan (see `Mandate.strictest_reading`)."""
    if not m.strictest_reading("parado"):
        return
    latest_date, latest = "", None
    for key, fields, _ in entries(m.body("## Punto de retoma")):
        f = iso_date(key) or iso_date(fields.get("fecha", ""))
        if f and f >= latest_date:
            latest_date, latest = f, fields
    if latest is None:
        add_failure("retoma-incompleta", m.where,
              f"`## Estado` {m.state_as('parado')} y no hay entrada de punto de "
              "retoma fechada")
        return
    missing = [c for c in RESUME_FIELDS if not latest.get(c, "").strip()]
    if missing:
        add_failure("retoma-incompleta", m.where,
              f"la retoma más reciente ({latest_date}) no trae: {', '.join(missing)}")
    for key, fields, _ in entries(m.body("## Paradas")):
        if fields.get("desbloqueo-quien", "").strip():
            continue
        f = iso_date(key) or iso_date(fields.get("fecha", ""))
        if f and latest_date < f:
            add_failure("retoma-incompleta", m.where,
                  f"la retoma más reciente ({latest_date}) es anterior a la parada "
                  f"abierta «{key}»")
            break


def check_parallelism(m: Mandate) -> None:
    """CA-19, CA-20 — five mandatory fields and cap ≤ 4."""
    fields = bullets(m.body("## Paralelismo"))
    missing = [c for c in PARALLELISM_FIELDS if not fields.get(c, "").strip()]
    if missing:
        add_failure("paralelismo-sin-declarar", m.where,
              f"`## Paralelismo` no declara: {', '.join(missing)}")
    cap = fields.get("tope-worktrees", "")
    num_match = re.search(r"\d+", cap)
    if num_match and int(num_match.group(0)) > MAX_CAP:
        add_failure("paralelismo-excede-tope", m.where,
              f"tope declarado {num_match.group(0)} > {MAX_CAP}")


def check_reverse_reference(m: Mandate) -> list[dict[str, str]]:
    """CA-04 — every member references the plan. Error unless the plan is `borrador`.

    The relaxation needs the state **declared**, not assumed: `m.state` is `""` when
    `## Estado` does not resolve, so an ambiguous section keeps the finding an error.
    Reading an assumed `borrador` here is what turned a plan whose member did not
    reference it into a PASS.

    Returns the `_estado.yaml` files read, which `check_approval` reuses.
    """
    read_states: list[dict[str, str]] = []
    if m.form != "plan":
        return read_states
    is_warning = m.state == "borrador"
    for uid in m.members():
        f = m.units_root / uid / "_estado.yaml"
        if not f.is_file():
            (add_warning if is_warning else add_failure)(
                "plan-unidad-sin-referencia-inversa", str(m.units_root / uid),
                f"el plan lista '{uid}' y esa unidad no tiene _estado.yaml")
            continue
        t = f.read_text(encoding="utf-8")
        data = {
            "donde": str(f),
            "modo": field(t, "modo"),
            "mandato": field(t, "mandato"),
            "fase": field(t, "fase"),
            "estado": field(t, "estado"),
        }
        read_states.append(data)
        if data["mandato"].strip() != m.reference:
            (add_warning if is_warning else add_failure)(
                "plan-unidad-sin-referencia-inversa", str(f),
                f"'{uid}' no referencia el plan '{m.reference}'")
    return read_states


def validate_content(m: Mandate, unit_state: dict[str, str]) -> None:
    check_sections(m)
    check_mandate_state(m, unit_state)
    check_chain(m)
    check_reviews(m)
    check_mandate_entries(m)
    members = check_reverse_reference(m)
    covered = members if m.form == "plan" else (
        [unit_state] if unit_state else []
    )
    check_approval(m, covered)
    check_stops(m)
    check_decisions(m)
    check_stop_conditions(m)
    check_resume(m)
    check_parallelism(m)


# ======================================================================================
# Auxiliary modes
# ======================================================================================

def read_unit_state(unit_dir: Path) -> dict[str, str]:
    f = unit_dir / "_estado.yaml"
    if not f.is_file():
        return {}
    t = f.read_text(encoding="utf-8")
    return {
        "donde": str(f),
        "modo": field(t, "modo"),
        "mandato": field(t, "mandato"),
        "fase": field(t, "fase"),
        "estado": field(t, "estado"),
        "dueño": field(t, "dueño"),
    }


def latest_log(unit_dir: Path) -> str:
    """Date of the most recent `bitacora.md` entry (header `## <fecha> · …`).

    Accepts empty or absent bitacora.md (returns ""). If the file contains
    legacy-format entries (multi-line with ``**Qué hice:**`` etc.), emits a
    warning to stderr but still extracts the most recent ISO-8601 date from
    the compact-style headers.
    """
    import sys as _sys
    f = unit_dir / "bitacora.md"
    if not f.is_file():
        return ""
    lines = f.read_text(encoding="utf-8").splitlines()
    if not lines or all(not ln.strip() for ln in lines):
        return ""

    _has_legacy = any(
        ln.strip().startswith("**Qué") or ln.strip().startswith("**Dónde")
        for ln in lines
    )
    if _has_legacy:
        _sys.stderr.write(
            f"validate_mandate: AVISO — {relative_path(f)} tiene formato legacy"
            " (multi-linea con bullets); no bloquea\n"
        )

    latest = ""
    for line in lines:
        if not line.startswith("## "):
            continue
        m = re.match(r"^##\s+([0-9T:Z+-]{10,})", line.strip())
        if m:
            iso_f = iso_date(m.group(1))
            if iso_f and iso_f > latest:
                latest = iso_f
    return latest


def relative_path(p: Path) -> str:
    """Path relative to the repo root: the summary must be identical whether it is
    invoked with a relative path (fixture) or with a plan identifier (absolute)."""
    try:
        return str(p.resolve().relative_to(ROOT))
    except ValueError:
        return str(p)


#: Emitted by `print_summary` for CA-29 whenever a mandate `aprobado` has no
#: usable `## Punto de retoma` -- collapses two distinct situations that
#: `resume_divergence_kind` tells apart (see there): the resume section was
#: never written (benign, `supervised_conductor.py` may still advance the loop
#: on real progress), or it was written but is older than the latest
#: `bitacora.md` entry (a real divergence, plan.md § Riesgo 3 -- must always
#: stop). Kept for CA-29 backward compatibility (`retomar-esperado.txt`
#: fixtures grep for this exact literal for both situations).
UNRESOLVED_RESUME_LINE = "retoma sin punto verificado"

#: Emitted, in addition to `UNRESOLVED_RESUME_LINE`, only for the "written but
#: outdated" situation above -- the machine-readable signal
#: `supervised_conductor.py` keys on to distinguish it from "never written"
#: instead of using `avanzo` (fase+gates signature) as a proxy for a
#: bitacora/retoma divergence it cannot see (gate de código 0118, 2026-09-21).
STALE_RESUME_LINE = "retoma desactualizada respecto de bitácora"


def resume_divergence_kind(m: Mandate, latest_resume: str, log: str) -> str | None:
    """CA-29's two `## Punto de retoma` divergence situations, told apart.

    Returns ``None`` (no divergence), ``"unwritten"`` (the resume section is
    empty -- benign, `## Punto de retoma` is only filled when a mandate
    actually stops, never on an ordinary turn close, § 6 of `sdd-supervisado`),
    or ``"stale"`` (the resume section has an entry, but it predates the
    latest `bitacora.md` entry -- a real divergence between what the resume
    point claims and what actually happened)."""
    if m.state != "aprobado":
        return None
    if not latest_resume:
        return "unwritten"
    if log and latest_resume < log:
        return "stale"
    return None


def print_summary(m: Mandate | None, unit_dir: Path | None,
                     reference: str) -> None:
    """Fields that `sdd-retomar` transcribes for a supervised unit (CA-29)."""
    if m is None:
        print("forma: sin resolver")
        print(f"mandato: {reference}")
        print("estado-mandato: sin resolver")
        print("ultima-aprobacion: sin aprobar")
        print("punto-de-retoma: sin retoma")
        print(f"ultima-bitacora: {latest_log(unit_dir) or 'sin bitácora'}"
              if unit_dir else "ultima-bitacora: sin bitácora")
        return

    print(f"forma: {m.form}")
    print(f"mandato: {m.reference}")
    print(f"estado-mandato: {m.state or 'sin declarar'}")

    latest_approval = ""
    for key, fields, _ in entries(m.body("## Aprobación")):
        f = iso_date(key) or iso_date(fields.get("fecha", ""))
        if f and f > latest_approval:
            latest_approval = f
    print(f"ultima-aprobacion: {latest_approval or 'sin aprobar'}")

    latest_resume = ""
    for key, fields, _ in entries(m.body("## Punto de retoma")):
        f = iso_date(key) or iso_date(fields.get("fecha", ""))
        if f and f > latest_resume:
            latest_resume = f
    if latest_resume:
        print(f"punto-de-retoma: {relative_path(m.path)} ({latest_resume})")
    else:
        print("punto-de-retoma: sin retoma")

    log = latest_log(unit_dir) if unit_dir else ""
    print(f"ultima-bitacora: {log or 'sin bitácora'}")

    kind = resume_divergence_kind(m, latest_resume, log)
    if kind is not None:
        print(UNRESOLVED_RESUME_LINE)
        if kind == "stale":
            print(STALE_RESUME_LINE)


# ======================================================================================
# CLI
# ======================================================================================

def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="validate_mandate.py",
        description="Validador de mandatos supervisados.",
    )
    ap.add_argument("--plan", metavar="ID|RUTA")
    ap.add_argument("--unidad", metavar="RUTA")
    ap.add_argument("--codigos", action="store_true",
                    help="imprime la lista cerrada de códigos (CA-21) y termina")
    ap.add_argument("--hash", action="store_true",
                    help="imprime el sha256 para la entrada de `## Aprobación` (S-24)")
    ap.add_argument("--resumen", action="store_true",
                    help="imprime los campos que transcribe `sdd-retomar` (CA-29)")
    args = ap.parse_args(argv)

    if args.codigos:
        print("\n".join(CODES))
        return 0

    if bool(args.plan) == bool(args.unidad):
        ap.error("hay que dar exactamente uno de --plan o --unidad")

    reset()
    unit_dir: Path | None = None
    unit_state: dict[str, str] = {}
    reference = args.plan or args.unidad

    if args.plan:
        m = resolve_plan(args.plan)
        if m is None:
            return 1
    else:
        unit_dir = Path(args.unidad)
        if not unit_dir.is_dir():
            print(f"ERROR — no existe el directorio de unidad: {unit_dir}",
                  file=sys.stderr)
            return 1
        unit_state = read_unit_state(unit_dir)
        reference = unit_state.get("mandato", "") or args.unidad
        m = resolve_unit(unit_dir)

    if args.hash:
        if m is None:
            print("ERROR — el mandato no resuelve; no hay hash que calcular",
                  file=sys.stderr)
            return 1
        print(m.approval_hash())
        return 0

    if args.resumen:
        print_summary(m, unit_dir, reference)
        return 0

    if m is not None:
        validate_content(m, unit_state)

    for code, where, detail in WARNINGS:
        print(f"AVISO  {code}  {where}" + (f"  — {detail}" if detail else ""))
    if not FAILURES:
        print(f"PASS — mandato sin incumplimientos ({reference}).")
        return 0
    print(f"FAIL — {len(FAILURES)} incumplimiento(s):")
    for code, where, detail in FAILURES:
        print(f"  {code}  {where}" + (f"  — {detail}" if detail else ""))
    print("códigos: " + " ".join(codes_from(FAILURES)))
    return 1


if __name__ == "__main__":
    sys.exit(main())
