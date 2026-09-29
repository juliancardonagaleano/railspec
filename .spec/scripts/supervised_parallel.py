#!/usr/bin/env python3
"""Parallelism manager for a supervised mandate — unit 0118, Part B (G4).

Reopened by explicit human decision `0118-D3` (referring to the mandato
template that consolidates `## Paralelismo`, 2026-09-21) despite `CA-15`
having measured `CR-4` **not** met (K3 = -15.16 %, threshold >= 30 %,
see `0118-D1`) — Julian, the mandate's owner, decided to build G4 anyway,
exercising the `reversion` `0118-D1` already provided for.

Wraps `supervised_conductor.py` (unit 0118, G1) the same way that script
wraps `sdd-supervisado`: this script never re-implements its per-step loop —
it resolves which of a **plan** mandate's member units are eligible right
now (no lock taken, chain dependencies already satisfied), mounts one git
worktree per eligible unit up to `tope-worktrees`, and launches
`supervised_conductor.py run --unidad <unit-inside-that-worktree>` as a
subprocess there, exactly the pattern already proven by the `0109b` pilot
(`supervised-test.sh:1780-1831`, `git worktree add --detach` /
`remove_pilot_worktree`).

Only `instance_lock.py inspect` is ever called (CA-13, same rule as
`supervised_conductor.py`) — never `acquire`/`release`: the lock stays the
turn's own act inside each `claude -p` child, and because each worktree is
a **separate checkout**, each child's own copy of the mandate file (and
therefore its own `## Instancia en curso` section) is naturally its own —
no two children ever contend for the same on-disk lock section. This
script's own `instance_lock.py inspect` call, at start, is a courtesy check
against the **main tree's** copy of the mandate: if a human (or another
process) is already running an interactive/supervised turn there, no new
worktree is launched this run.

`## Paralelismo` (read via `_common.bullets`, never a parser of its own) —
`tope-worktrees`, `carriles`, `dueno-stack-vivo`, `presupuesto-mcp`,
`conducta-presupuesto-agotado` — already governs `sdd-supervisado` itself
(`.agents/skills/sdd-supervisado/SKILL.md` § 5); this script applies it,
never redefines it.

CA-21/CA-22 (T19) — two things this script never does, checked at every
subprocess launch by `guarded_subprocess`/`guarded_popen` below:

    * redeploy/restart the shared stack (`docker-compose`, ports
      `9600`/`3456`, the live database) — exclusion 10 of
      `.spec/PARADAS-SUPERVISADO.md`, the exclusive act of the mandate's
      declared `dueno-stack-vivo`;
    * trigger a knowledge-router/PCE graph or corpus rebuild while a run is
      in flight — condition 12 of the same list.

The primary guarantee is structural: this module's only three subprocess
targets are `git` (worktree add/remove/list/prune/merge), `supervised_conductor.py`,
and `claude -p` for the CA-10 full re-gate (`_rerun_code_gate`, same
`guarded_popen`/`env["HOP_UNIT_DIR"]` pattern as `_launch`) — no docker/compose/
PCE-reindex command is ever constructed anywhere in this file.
`guarded_subprocess`/`guarded_popen` are the fail-loud backstop a test can
exercise directly, not the mechanism itself.

Usage
-----
    supervised_parallel.py run --plan <id> [--repo-root DIR]
        [--tope-worktrees N] [--poll-seconds S] [--max-ciclos N]
        [--claude-bin RUTA] [--evidencia-dir DIR]
        [--conductor-max-pasos N] [--cota-blanda S] [--margen-duro S]
        [--tope-estancamiento N]

Only the `plan` form is supported (CA-17 is about "N unidades elegibles del
mandato" — an isolated unit has nothing to parallelize against).

Exit codes of *this process*:
    0   the run ended with nothing left to launch this cycle (mandate
        closed, or no eligible unit and nothing active) — a clean stop.
    1   reserved for a future typified mid-run stop (not raised today).
    2   internal error (never a bare traceback).
    3   the main tree's mandate lock was occupied at start — no worktree
        launched this run.

stdlib only, no network, no resident process beyond the `git`/
`supervised_conductor.py` children it launches and waits on.
"""

from __future__ import annotations

import argparse
import graphlib
import os
import re
import shutil
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import validate_mandate as vm  # noqa: E402
import worktree_registry as wr  # noqa: E402
from _common import (  # noqa: E402
    GuardedCommandError,  # noqa: E402
    bullets,
    units,
)
from _common import guarded_popen as _guarded_popen  # noqa: E402
from _common import guarded_subprocess as _guarded_subprocess  # noqa: E402
from supervised_conductor import lock_status  # noqa: E402 (CA-13: reused, not re-derived)
from unit_state import gate_verdict  # noqa: E402 (CA-10: same `gates.codigo.veredicto`
                                      # reader `validate_artifact_size.py` already reuses,
                                      # never a second parser of the same field)

EXIT_OK = 0
EXIT_STOPPED = 1
EXIT_INTERNAL = 2
EXIT_LOCK_BUSY_AT_START = 3

DEFAULT_CAP = 3            # fallback documented below (parallelism_cap), never a
                            # silent stand-in for what `## Paralelismo` declares
DEFAULT_POLL_SECONDS = 2.0
DEFAULT_MAX_CYCLES = 100000
DEFAULT_CONDUCTOR_MAX_STEPS = 200
DEFAULT_SOFT_SECONDS = 1500
DEFAULT_HARD_MARGIN_SECONDS = 300
DEFAULT_STALL_LIMIT = 3
DEFAULT_CLAUDE_BIN = "claude"


class ParallelError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ======================================================================================
# CA-21/CA-22 (T19) — the guard every subprocess call in this module (AND
# `worktree_registry.py`'s `git_worktree_list`, gate 0118/G4 hallazgo L3 media) goes
# through. The pattern list and the actual block live once, in `_common.py`
# (`FORBIDDEN_SUBPROCESS_PATTERNS`/`guarded_subprocess`/`guarded_popen`) — this module
# only translates a block into ITS OWN typified exit code (`ParallelError`,
# `EXIT_INTERNAL`), so a caller of this file's `guarded_subprocess`/`guarded_popen`
# keeps seeing the same `ParallelError` contract as before this refactor.
# ======================================================================================

def guarded_subprocess(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    try:
        return _guarded_subprocess(cmd, **kwargs)
    except GuardedCommandError as error:
        raise ParallelError(
            EXIT_INTERNAL,
            f"supervised_parallel.py: comando bloqueado por CA-21/CA-22 "
            f"(patrón «{error.pattern}»): {cmd}",
        ) from None


def guarded_popen(cmd: list[str], **kwargs) -> subprocess.Popen:
    try:
        return _guarded_popen(cmd, **kwargs)
    except GuardedCommandError as error:
        raise ParallelError(
            EXIT_INTERNAL,
            f"supervised_parallel.py: comando bloqueado por CA-21/CA-22 "
            f"(patrón «{error.pattern}»): {cmd}",
        ) from None


# ======================================================================================
# Chain-of-dependencies, stage-aware (CA-17/CA-18) — `validate_mandate.chain_ids`
# flattens the chain for its own purpose (CA-07, existence-only) and does not
# expose which ids share a stage (`/`, real siblings) vs. are sequential
# (`→`/`->`, a real predecessor). This reuses the SAME line-selection rule
# (`Vigente:` line, else the first connector-bearing line) and the SAME token
# rules (`validate_mandate.chain_tokens`) — the only new part is keeping stage
# boundaries instead of flattening them.
# ======================================================================================

def _chain_line_text(body: str) -> str:
    for line in body.splitlines():
        m = re.match(r"^[ \t]*vigente\b[^:]*:(.*)$", line.strip(), re.IGNORECASE)
        if m:
            return m.group(1)
    for line in body.splitlines():
        if vm.CHAIN_CONNECTOR_RE.search(line):
            head = vm.ISO_IN_TEXT_RE.sub(" ", line)
            return head.split(":", 1)[1] if ":" in head else head
    return ""


def chain_stage_groups(body: str) -> list[list[str]]:
    """Sequential stages of `## Cadena de dependencias`, each a list of ids that
    may run in parallel with each other."""
    line = _chain_line_text(body)
    if not line:
        return []
    groups: list[list[str]] = []
    for stage in re.split(r"→|->", line):
        ids = vm.chain_tokens(stage)
        if ids:
            groups.append(ids)
    return groups


# ======================================================================================
# Generalized DAG (worktree-orchestrator DAG/merge unit, CA-01/CA-02/CA-04/CA-05):
# `chain_stage_groups` above stays untouched as the DEFAULT source (stage i depends on
# every id of stages 0..i-1 -- exactly what `eligible_units` already computed, byte for
# byte, before this DAG existed -- CA-03 non-regression). An optional override
# `<id>(<-<id-a>,<id-b>)` on the SAME declared chain line REPLACES, never adds to, that
# id's default predecessor set -- the only new syntax; `chain_tokens` already strips
# every `(...)` before tokenizing (`(no amparada)`, now also this), so the override is
# invisible to `chain_ids`/`check_chain` (`validate_mandate.py`) -- zero change there.
# ======================================================================================

_OVERRIDE_RE = re.compile(r"([\w-]+)\s*\(\s*<-\s*([^)]*)\)")

#: Sentinel prefix for a predecessor id an override cites that is not itself a real
#: token of an EARLIER stage of the same declared chain (CA-05, fail-closed): kept in
#: the predecessor set verbatim rather than dropped, so the id it blocks can never
#: satisfy `predecessors_done` for it -- no real unit directory is ever named with this
#: prefix, so it never resolves (`_resolve_dag_tokens`).
_UNRESOLVED_PREFIX = "__unresolved__:"


def _chain_overrides(raw_line: str) -> dict[str, list[str]]:
    """`<id>(<-<id-a>,<id-b>)` annotations read off the RAW (pre-token-stripping)
    chain line -- id -> its explicit predecessor id list, in written order. Never
    resolved to a unit directory here (`_resolve_dag_tokens` does that once, shared
    with the default-predecessor tokens); this only isolates what was actually
    declared, same treatment `chain_tokens` already gives `(no amparada)`."""
    overrides: dict[str, list[str]] = {}
    for match in _OVERRIDE_RE.finditer(raw_line):
        token = match.group(1).strip()
        if not vm.CHAIN_TOKEN_RE.match(token):
            continue
        preds = [p.strip().strip("`") for p in match.group(2).split(",")]
        overrides[token] = [p for p in preds if p]
    return overrides


def dependency_dag(body: str) -> dict[str, set[str]]:
    """Token-level DAG of `## Cadena de dependencias`: id -> set of predecessor ids,
    derived ONLY from what that section declares (CA-01, `pri-ia-cero-inferencia-
    implicita`) -- never inferred from unit naming/proximity, never crossing another
    mandate. Default predecessor set of an id in stage *i* is every id of stages
    0..i-1 (CA-03: reproduces `chain_stage_groups`/`eligible_units`'s old behavior
    exactly when nobody writes an override). An override replaces that default outright
    (CA-04); a cited id that never appears in an earlier stage of the SAME chain is
    fail-closed (CA-05) via `_UNRESOLVED_PREFIX`, not silently dropped."""
    raw_line = _chain_line_text(body)
    if not raw_line:
        return {}
    stages = chain_stage_groups(body)
    stage_of: dict[str, int] = {tok: idx for idx, stage in enumerate(stages) for tok in stage}
    overrides = _chain_overrides(raw_line)

    dag: dict[str, set[str]] = {}
    for idx, stage in enumerate(stages):
        default_preds = {tok for earlier in stages[:idx] for tok in earlier}
        for tok in stage:
            if tok in overrides:
                preds: set[str] = set()
                for pred in overrides[tok]:
                    resolved_here = (
                        vm.CHAIN_TOKEN_RE.match(pred) and pred in stage_of and stage_of[pred] < idx
                    )
                    preds.add(pred if resolved_here else f"{_UNRESOLVED_PREFIX}{pred}")
                dag[tok] = preds
            else:
                dag[tok] = set(default_preds)
    return dag


def dag_waves(dag: dict[str, set[str]]) -> list[list[str]]:
    """Groups `dag`'s ids into waves via `graphlib.TopologicalSorter` (stdlib,
    confirmed available, no new dependency): each wave is every id whose predecessors
    all landed in an earlier wave, sorted for determinism. CA-02: two ids share a wave
    only if neither is a predecessor of the other, directly or transitively. Pure
    computation over the DAG's own token space -- unresolved/sentinel ids sort in like
    any other node; callers that need only REAL unit directories filter afterwards
    (`_topological_slug_order`), so this function stays a single generic primitive."""
    sorter = graphlib.TopologicalSorter(dag)
    sorter.prepare()
    waves: list[list[str]] = []
    while sorter.is_active():
        ready = sorted(sorter.get_ready())
        waves.append(ready)
        sorter.done(*ready)
    return waves


def _resolve_dag_tokens(mandate: vm.Mandate, dag: dict[str, set[str]]) -> dict[str, Path | None]:
    """Every id `dag` cites -- as a node or as somebody's predecessor -- resolved once
    to a real unit directory, or `None` if it never resolves (fail-closed lever shared
    by `eligible_units` and `_drain_merges`; an `_UNRESOLVED_PREFIX` sentinel is never
    even attempted)."""
    all_tokens = set(dag) | {pred for preds in dag.values() for pred in preds}
    return {
        tok: (None if tok.startswith(_UNRESOLVED_PREFIX) else unit_dir_for_token(mandate.units_root, tok))
        for tok in all_tokens
    }


def _topological_slug_order(mandate: vm.Mandate, dag: dict[str, set[str]]) -> list[str]:
    """Flattens `dag_waves(dag)` into ONE deterministic topological order of real unit
    directory NAMES (full slugs) -- wave by wave, alphabetical within a wave (CA-09:
    the order `_drain_merges` attempts merges in, never the real finish order when they
    differ). Ids that never resolve to a real unit directory are dropped -- nothing to
    merge for a token with no worktree behind it."""
    token_dir = _resolve_dag_tokens(mandate, dag)
    order: list[str] = []
    seen: set[str] = set()
    for wave in dag_waves(dag):
        for tok in wave:
            d = token_dir.get(tok)
            if d is not None and d.name not in seen:
                order.append(d.name)
                seen.add(d.name)
    return order


def unit_dir_for_token(units_root: Path, token: str) -> Path | None:
    for d in units(units_root):
        if d.name == token or d.name.startswith(token + "-"):
            return d
    return None


def _active_worktree_count(registry: dict) -> int:
    """CA-11: only `estado == "activo"` occupies the launch cap -- a
    `"fusionando"` entry (finished process, merge/re-gate pending) never
    inflates it, so successors of independent units keep launching while a
    merge is still draining."""
    return sum(1 for e in registry.get("worktrees", {}).values()
               if e.get("estado") == "activo")


def _fase_of(mandate: vm.Mandate, registry: dict, unit_dir: Path) -> str:
    """The `fase` this manager should treat as current for `unit_dir`: the
    registry's own record if one exists, else the main tree's copy. Reading
    the registry first is the lever CA-08 relies on: `_reap_finished` no
    longer writes `fase: "done"` the instant a child process exits — it moves
    the entry to `"fusionando"` and keeps whatever `fase` `_launch` set, so a
    successor never sees this unit as satisfied until `_drain_merges` confirms
    the merge (and re-gate, if the hybrid rule requires it) and only then sets
    the registry's `fase` to `"done"`. The main tree's own `_estado.yaml`
    still never changes on its own (a git worktree is a separate checkout);
    `_drain_merges` is what merges a child's edits back into it."""
    entry = registry.get("worktrees", {}).get(unit_dir.name, {})
    if entry.get("fase"):
        return entry["fase"]
    return vm.read_unit_state(unit_dir).get("fase", "")


def eligible_units(mandate: vm.Mandate, registry: dict) -> list[str]:
    """Member unit directory names eligible for a new worktree launch right now:
    not already `fase: done`, every DIRECT predecessor of its own DAG node already
    `fase: done` in the registry, not already covered by an `activo`/`fusionando`
    registry entry (worktree-orchestrator DAG/merge unit: a unit whose child already
    finished but is still merging back must never be relaunched), and — if it ran
    before in this same registry and stopped for a reason other than the clock cap —
    not auto-relaunched (a human resolves that, same rule `supervised_conductor.py`
    itself follows for its own stops). Firma y retorno sin cambio (`list[str]` plano)
    tras generalizar a `dependency_dag`/`dag_waves` — los 3 llamadores de `run()` y el
    mock del test end-to-end siguen intactos.

    `## Cadena de dependencias` cites units by their short numeric id (`0118`),
    but `## Unidades miembro` (what `mandate.members()` returns) and every unit
    directory on disk use the FULL slug (`0118-conductor-…`). Every id `dependency_dag`
    cites is resolved to a real unit directory once, up front
    (`_resolve_dag_tokens`) -- matching short id against full slug directly, as
    this function used to, silently found no stage for any real member and made
    the whole chain-dependency check a no-op."""
    dag = dependency_dag(mandate.body("## Cadena de dependencias"))
    token_dir = _resolve_dag_tokens(mandate, dag)
    dir_tokens: dict[str, list[str]] = {}
    for tok, d in token_dir.items():
        if d is not None:
            dir_tokens.setdefault(d.name, []).append(tok)

    busy_slugs = {
        slug for slug, entry in registry.get("worktrees", {}).items()
        if entry.get("estado") in ("activo", "fusionando")
    }

    result: list[str] = []
    for member in mandate.members():
        d = unit_dir_for_token(mandate.units_root, member)
        if d is None or d.name in busy_slugs:
            continue
        fase = _fase_of(mandate, registry, d)
        if fase == "done":
            continue
        member_tokens = dir_tokens.get(d.name, [])
        if not member_tokens:
            # Not cited by the chain at all: dependency unknown. Conservative,
            # documented limitation (plan.md § Riesgos, same treatment as the
            # missing-`carril` gap) -- never auto-launched.
            continue
        entry = registry.get("worktrees", {}).get(d.name, {})
        if entry.get("estado") == "retirado" and fase != "done":
            last_rc = str(entry.get("ultimo-rc", ""))
            if last_rc not in ("", "142"):
                continue
        # Fail-closed (CA-05, gate 0118/G4 hallazgo L2 media, now expressed per DAG
        # node instead of per stage): a predecessor that never resolves to a real
        # unit directory -- a stale/typo'd/not-yet-created id, or an override's
        # `_UNRESOLVED_PREFIX` sentinel -- is unverifiable, not absent; the dependent
        # id must not become eligible on the strength of only its resolved siblings.
        predecessors_done = True
        for tok in member_tokens:
            for pred in dag.get(tok, set()):
                pred_dir = token_dir.get(pred)
                if pred_dir is None or _fase_of(mandate, registry, pred_dir) != "done":
                    predecessors_done = False
                    break
            if not predecessors_done:
                break
        if predecessors_done:
            result.append(d.name)
    return result


def parallelism_cap(mandate: vm.Mandate, override: int | None) -> int:
    """`tope-worktrees` of `## Paralelismo`, or `override` if given. Falls back to
    `DEFAULT_CAP` (documented, not a silent business-rule constant) only when the
    field is missing/unparseable — the operative value today is `3`
    (`mandato.md § Paralelismo`), same as the fallback, but this reads the
    field first rather than hardcoding it."""
    if override is not None:
        return override
    raw = bullets(mandate.body("## Paralelismo")).get("tope-worktrees", "")
    m = re.search(r"\d+", raw)
    return int(m.group(0)) if m else DEFAULT_CAP


# ======================================================================================
# Worktree lifecycle (CA-17) — the `0109b` pilot's pattern
# (`supervised-test.sh:1780-1831`), adapted, not reimplemented.
# ======================================================================================

def create_worktree(repo_root: Path, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    guarded_subprocess(
        ["git", "-C", str(repo_root), "worktree", "add", "--detach", str(path), "HEAD"],
        capture_output=True, text=True, check=True,
    )


def remove_worktree(repo_root: Path, path: Path) -> None:
    result = guarded_subprocess(
        ["git", "-C", str(repo_root), "worktree", "remove", "--force", str(path)],
        capture_output=True, text=True, check=False,
    )
    if result.returncode != 0:
        shutil.rmtree(path, ignore_errors=True)
    guarded_subprocess(
        ["git", "-C", str(repo_root), "worktree", "prune"],
        capture_output=True, text=True, check=False,
    )


def path_relative_to_repo(path: Path, repo_root: Path) -> str:
    return str(path.resolve().relative_to(repo_root.resolve()))


# ======================================================================================
# The manager loop (CA-17/CA-18/CA-19/CA-20)
# ======================================================================================

@dataclass
class ParallelConfig:
    plan: str
    repo_root: Path
    evidencia_dir: Path | None = None
    tope_worktrees: int | None = None
    poll_seconds: float = DEFAULT_POLL_SECONDS
    max_cycles: int = DEFAULT_MAX_CYCLES
    claude_bin: str = DEFAULT_CLAUDE_BIN
    conductor_max_pasos: int = DEFAULT_CONDUCTOR_MAX_STEPS
    cota_blanda: int = DEFAULT_SOFT_SECONDS
    margen_duro: int = DEFAULT_HARD_MARGIN_SECONDS
    tope_estancamiento: int = DEFAULT_STALL_LIMIT


def _log(evidencia_dir: Path, text: str) -> None:
    evidencia_dir.mkdir(parents=True, exist_ok=True)
    with (evidencia_dir / "parallel.log").open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def _main_tree_head(repo_root: Path) -> str:
    """CA-06/CA-07: the main tree's own `git rev-parse HEAD`, verifiable against the
    seed-commit registered for a worktree created at this instant."""
    result = guarded_subprocess(
        ["git", "-C", str(repo_root), "rev-parse", "HEAD"],
        capture_output=True, text=True, check=True,
    )
    return result.stdout.strip()


def _launch(config: ParallelConfig, mandate: vm.Mandate, slug: str,
           wtroot: Path, data: dict, running: dict[str, subprocess.Popen],
           reg_path: Path, evidencia_dir: Path) -> None:
    path = wtroot / slug
    seed_commit: str | None = None
    if not path.exists():
        # CA-06/CA-07: the seed is the main tree's HEAD at the instant of a REAL
        # worktree creation only -- resolved right before `create_worktree` mounts
        # `--detach` from that same `HEAD`, so both name the identical commit. A
        # reused worktree (the `else` branch) never re-seeds -- decision of the
        # checkpoint (2026-09-23): the seed stays fixed for the worktree's whole
        # life, `set_entry`'s merge-not-replace semantics leave any already-
        # registered `seed-commit` untouched when this stays `None`.
        seed_commit = _main_tree_head(config.repo_root)
        create_worktree(config.repo_root, path)
    else:
        _log(evidencia_dir, f"{slug}: reutiliza worktree existente en {path}")

    scripts_rel = path_relative_to_repo(SCRIPTS, config.repo_root)
    units_rel = path_relative_to_repo(mandate.units_root, config.repo_root)
    conductor_script = path / scripts_rel / "supervised_conductor.py"
    unit_dir = path / units_rel / slug
    conductor_evidence = wr.runtime_dir(SCRIPTS, mandate.reference) / "conductor" / slug

    cmd = [
        sys.executable, str(conductor_script), "run", "--unidad", str(unit_dir),
        "--evidencia-dir", str(conductor_evidence),
        "--claude-bin", config.claude_bin,
        "--max-pasos", str(config.conductor_max_pasos),
        "--cota-blanda", str(config.cota_blanda),
        "--margen-duro", str(config.margen_duro),
        "--tope-estancamiento", str(config.tope_estancamiento),
    ]
    log_path = wr.runtime_dir(SCRIPTS, mandate.reference) / "logs" / f"{slug}.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    log_file = log_path.open("wb")
    # `HOP_UNIT_DIR`: a small, always-set, additive env var carrying this child's
    # own unit directory (inside its own worktree) — harmless to the real `claude`
    # CLI (unused), and what lets a synthetic `claude` stub in
    # `test_supervised_parallel.py` find ITS OWN unit's `_estado.yaml` without the
    # several parallel children (each with a different unit, same process
    # environment otherwise) colliding on a single shared path.
    env = dict(os.environ)
    env["HOP_UNIT_DIR"] = str(unit_dir)
    proc = guarded_popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT, env=env)
    running[slug] = proc
    fields = dict(
        ruta=str(path), rama="",
        fase=vm.read_unit_state(unit_dir).get("fase", ""),
        pid=str(proc.pid), sesion=f"{slug}-{proc.pid}", estado="activo",
    )
    if seed_commit is not None:
        fields["seed-commit"] = seed_commit
    wr.set_entry(data, slug, **fields)
    wr.save(reg_path, data)
    _log(evidencia_dir, f"{slug}: worktree {path} -- lanzado supervised_conductor.py pid={proc.pid}")


def _reap_finished(config: ParallelConfig, mandate: vm.Mandate, running: dict[str, subprocess.Popen],
                   data: dict, reg_path: Path, evidencia_dir: Path) -> None:
    for slug in list(running):
        proc = running[slug]
        rc = proc.poll()
        if rc is None:
            continue
        del running[slug]
        entry = data.get("worktrees", {}).get(slug, {})
        path = Path(entry.get("ruta", "")) if entry.get("ruta") else None
        # Read the final `fase` from the WORKTREE's OWN copy of the unit, before
        # it is retired -- a git worktree is a SEPARATE checkout: the child's
        # edits only ever land on its own copy of `_estado.yaml` on disk, never
        # on the main tree's (they share the object database, not the working
        # tree). A `final_fase == "done"` child does not retire here -- it moves to
        # `"fusionando"` below, and `_drain_merges` (run() calls it right after this
        # function, every cycle) is what merges its worktree's commits back into the
        # main tree in DAG topological order, re-confirms its gate per the CA-10
        # hybrid rule, and only THEN writes `fase: "done"` to the registry -- until
        # that lands, `eligible_units` on the main tree correctly still sees this
        # unit as not-done (worktree-orchestrator DAG/merge unit).
        final_fase = ""
        if path is not None and path.is_dir():
            units_rel = path_relative_to_repo(mandate.units_root, config.repo_root)
            worktree_unit_dir = path / units_rel / slug
            if worktree_unit_dir.is_dir():
                final_fase = vm.read_unit_state(worktree_unit_dir).get("fase", "")
        _log(evidencia_dir, f"{slug}: supervised_conductor.py terminó rc={rc} fase-final={final_fase}")
        if final_fase == "done":
            # Worktree orchestrator DAG/merge unit (CA-08/CA-09/CA-10): a child that
            # finished `done` is NOT retired/removed yet -- the worktree stays
            # mounted, `estado` becomes `"fusionando"`, and the registry does NOT
            # write `fase: "done"` here (this unit's `fase` field stays whatever
            # `_launch` set it to at launch time). `_drain_merges` is the only place
            # that later writes `fase: "done"` (+ `estado: "retirado"` + removes the
            # worktree), once the merge (and re-gate, if the CA-10 hybrid rule
            # requires it) actually landed on the main tree -- exactly the lever
            # CA-08 needs, with zero change to `_fase_of`/`eligible_units` itself.
            wr.set_entry(data, slug, estado="fusionando", pid="", **{"ultimo-rc": str(rc)})
        else:
            if path is not None and path.exists():
                remove_worktree(config.repo_root, path)
            wr.set_entry(data, slug, estado="retirado", fase=final_fase, pid="", **{"ultimo-rc": str(rc)})
        wr.save(reg_path, data)


# ======================================================================================
# Merge drain (worktree-orchestrator DAG/merge unit, CA-08/CA-09/CA-10/CA-12): the
# single serialized point of the whole flow -- merges every `"fusionando"` unit back
# into the main tree, in the DAG's own topological order, re-confirming its gate per
# the CA-10 hybrid rule before it counts as a satisfied predecessor for anyone.
# ======================================================================================

RERUN_GATE_TIMEOUT_SECONDS = 1800  # plan.md § Riesgos, hallazgo L2 baja: a bounded wait,
                                    # never an unbounded block on the whole manager loop.

RERUN_GATE_PROMPT_TEMPLATE = (
    "Corre el gate de código (`sdd-gate` fase `codigo`) sobre la unidad en «{unidad}», "
    "ya integrada al árbol principal por un merge automático de este orquestador de "
    "worktrees. Alcance EXACTO de esta invocación: solo ese gate de código -- no "
    "reabras spec/plan/tareas, no avances la unidad a otra fase, no toques `## Estado` "
    "de ningún mandato. Al terminar, persiste el veredicto en `gates.codigo.veredicto` "
    "de `_estado.yaml` (mismo contrato que `sdd-gate` ya sigue hoy) y cierra turno de "
    "inmediato."
)


def _merge_head_present(repo_root: Path) -> bool:
    """CA-12 / plan.md § Riesgos (gate hallazgo L3 alta): whether the MAIN tree has a
    merge in progress right now -- `git rev-parse -q --verify MERGE_HEAD` exits 0 (with
    the commit sha, discarded) only while one is."""
    result = guarded_subprocess(
        ["git", "-C", str(repo_root), "rev-parse", "-q", "--verify", "MERGE_HEAD"],
        capture_output=True, text=True, check=False,
    )
    return result.returncode == 0


def _files_touched_since_seed(repo_root: Path, seed_commit: str, worktree_head: str) -> list[str]:
    """Files that differ between a worktree's `seed-commit` (CA-06) and its own HEAD --
    the exact set the CA-10 hybrid rule re-confirms post-merge. Resolved against the
    MAIN tree's object database (`repo_root`), never the worktree's working copy --
    every worktree shares the same object store, so both commits are reachable from
    either checkout."""
    result = guarded_subprocess(
        ["git", "-C", str(repo_root), "diff", "--name-only", seed_commit, worktree_head],
        capture_output=True, text=True, check=True,
    )
    return [line for line in result.stdout.splitlines() if line.strip()]


def _show_file_at(repo_root: Path, ref: str, file_path: str) -> str | None:
    """`git show <ref>:<file_path>` content, or `None` if the file does not exist at
    that ref (e.g. deleted) -- `None == None` compares equal, so a file absent on both
    sides of a merge counts as "unchanged" for the CA-10 comparison."""
    result = guarded_subprocess(
        ["git", "-C", str(repo_root), "show", f"{ref}:{file_path}"],
        capture_output=True, text=True, check=False,
    )
    return result.stdout if result.returncode == 0 else None


def _rerun_code_gate(config: ParallelConfig, mandate: vm.Mandate, slug: str,
                     unit_dir: Path, evidencia_dir: Path) -> tuple[bool, str]:
    """CA-10's expensive branch: a BLOCKING full-panel re-gate against the
    already-merged main tree -- same invocation shape as `_launch`'s child (`cmd` +
    `guarded_popen`, `env["HOP_UNIT_DIR"]`), adapted to wait for the result instead of
    fire-and-forget, because this unit must not count as a satisfied predecessor until
    the gate actually confirms it. `unit_dir` is the MAIN tree's own copy (the state
    the gate must judge is the merged one, not the worktree's, which is about to be
    discarded).

    Returns `(confirmado, causa)`. `causa` only matters when `confirmado` is `False` --
    what the caller writes into `parada-causa` for condición 7. A timeout and an actual
    `escalado` verdict share condición 7's NUMBER (`.spec/PARADAS-SUPERVISADO.md`) but
    never its PROSE (plan.md § Riesgos, hallazgo L4 media): a timeout never returned a
    verdict at all, so the cause narrates that explicitly rather than letting the
    number imply the panel ran and found something."""
    prompt = RERUN_GATE_PROMPT_TEMPLATE.format(unidad=str(unit_dir))
    cmd = [config.claude_bin, "-p", prompt, "--permission-mode", "acceptEdits",
          "--allowedTools", "mcp__pce-mcp", "Bash"]
    log_path = wr.runtime_dir(SCRIPTS, mandate.reference) / "logs" / f"{slug}-re-gate.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env["HOP_UNIT_DIR"] = str(unit_dir)
    with log_path.open("wb") as log_file:
        proc = guarded_popen(cmd, stdin=subprocess.DEVNULL, stdout=log_file,
                             stderr=subprocess.STDOUT, env=env)
        try:
            rc = proc.wait(timeout=RERUN_GATE_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
            return False, f"timeout sin veredicto ({RERUN_GATE_TIMEOUT_SECONDS}s excedidos)"
    if rc != 0:
        return False, f"re-gate completo terminó rc={rc} sin veredicto aprobado"
    estado_path = unit_dir / "_estado.yaml"
    text = estado_path.read_text(encoding="utf-8") if estado_path.is_file() else ""
    verdict = gate_verdict(text, "codigo")
    if verdict in ("aprobado", "refinado"):
        return True, ""
    return False, f"gate re-ejecutado devolvió veredicto «{verdict or 'ausente'}»"


def _drain_merges(config: ParallelConfig, mandate: vm.Mandate, data: dict,
                  reg_path: Path, evidencia_dir: Path) -> None:
    """CA-08/CA-09/CA-10/CA-12: runs every cycle, right after `_reap_finished` and
    before `active_count`/new launches are computed (so CA-08 holds within the SAME
    cycle a merge lands). Serializes every `"fusionando"` unit's merge back to the main
    tree, in the DAG's own topological order -- never the real finish order when they
    differ (CA-09) -- and only marks a unit `estado: "retirado", fase: "done"` once its
    merge (and re-gate, if CA-10's hybrid rule requires it) actually confirmed."""
    fusionando = {
        slug for slug, entry in data.get("worktrees", {}).items()
        if entry.get("estado") == "fusionando"
    }
    if not fusionando:
        return

    # Mandate-level health-check (plan.md § Riesgos, gate hallazgos L3/L4 alta): the
    # lock is re-checked FIRST -- a lock legitimately held (a human, or `sdd-supervisado`
    # itself merging by hand) means an in-progress `MERGE_HEAD` is legitimate work, not
    # an orphan; this cycle simply defers, same pattern `run()` already applies before
    # every launch.
    estado_lock, lock_rc = lock_status(mandate.path)
    if estado_lock == "error":
        raise ParallelError(EXIT_INTERNAL, f"instance_lock.py inspect devolvió {lock_rc}")
    if estado_lock == "ocupado":
        _log(evidencia_dir, "lock del árbol principal ocupado -- se difiere el "
                            "drenaje de merges de esta corrida")
        return

    if _merge_head_present(config.repo_root):
        # Orphaned MERGE_HEAD: the lock is free (just checked) yet the main tree has a
        # merge in progress -- nobody legitimate is holding it (most likely a crash
        # mid-`git merge` in a previous cycle). Mandate-level in EFFECT (blocks every
        # `"fusionando"` unit's landing, not just one unit's dependents -- the merge
        # drain IS the single serialized point the spec calls for), but the mechanism
        # NEVER touches `## Estado` of the mandate: it only records the observable fact
        # on this orchestrator's OWN artifact (`worktrees.json`), for a human/`claude
        # -p` session to read and act on at the resumption point. Never auto-cleared,
        # never auto-reanudado, no condición number forced onto it (`UNTYPIFIED`) --
        # `run()` reads this field before launching anything new (T9).
        data["parada-arbol-principal"] = {
            "codigo": "UNTYPIFIED",
            "causa": "MERGE_HEAD huérfano detectado sin lock legítimo",
            "detectado-en": wr.now_iso(),
        }
        wr.save(reg_path, data)
        _log(evidencia_dir, "MERGE_HEAD huérfano detectado en el árbol principal sin "
                            "lock legítimo -- parada-arbol-principal registrada, "
                            "drenaje de merges detenido esta corrida")
        return

    dag = dependency_dag(mandate.body("## Cadena de dependencias"))
    token_dir = _resolve_dag_tokens(mandate, dag)
    order = _topological_slug_order(mandate, dag)
    ordered = [slug for slug in order if slug in fusionando]
    ordered += sorted(fusionando - set(ordered))  # defensive: should never happen --
                                                   # a "fusionando" unit was eligible
                                                   # once, so the chain always cited it.

    for slug in ordered:
        entry = data.get("worktrees", {}).get(slug, {})
        if entry.get("parada-condicion"):
            # CA-12: no silent retry while a previous stop's marker still stands --
            # only a human clearing it (same rule `parada-arbol-principal` follows)
            # reopens this unit to another merge attempt.
            continue

        member_tokens = [tok for tok, d in token_dir.items() if d is not None and d.name == slug]
        preds = {pred for tok in member_tokens for pred in dag.get(tok, set())}
        preds_done = all(
            token_dir.get(pred) is not None and _fase_of(mandate, data, token_dir[pred]) == "done"
            for pred in preds
        )
        if not preds_done:
            continue  # its turn in the topological order hasn't come up yet

        # Re-check the lock immediately before EACH merge attempt, not once per cycle
        # -- same "antes de cada lanzamiento" granularity `run()` already applies
        # before each launch; a merge mutates the main tree exactly like a launch
        # reads its HEAD.
        estado_lock, lock_rc = lock_status(mandate.path)
        if estado_lock == "error":
            raise ParallelError(EXIT_INTERNAL, f"instance_lock.py inspect devolvió {lock_rc}")
        if estado_lock == "ocupado":
            _log(evidencia_dir, f"{slug}: lock del árbol principal ocupado -- se "
                                f"difiere este merge a un ciclo posterior")
            break  # further merges this cycle would land out of topological order

        ruta = entry.get("ruta", "")
        if not ruta or not Path(ruta).is_dir():
            continue

        worktree_head = guarded_subprocess(
            ["git", "-C", ruta, "rev-parse", "HEAD"],
            capture_output=True, text=True, check=True,
        ).stdout.strip()

        seed_commit = entry.get("seed-commit", "")
        files_touched = (
            _files_touched_since_seed(config.repo_root, seed_commit, worktree_head)
            if seed_commit else []
        )
        pre_merge_snapshot = {f: _show_file_at(config.repo_root, worktree_head, f) for f in files_touched}

        # Merge by commit HASH, never by branch (Decisiones de diseño): every worktree
        # is `--detach` (`create_worktree`), so there is no branch to name -- same
        # object database, no need to force one into existence for this merge alone.
        merge_result = guarded_subprocess(
            ["git", "-C", str(config.repo_root), "merge", "--no-ff", worktree_head,
             "-m", f"merge: {slug}"],
            capture_output=True, text=True, check=False,
        )
        if merge_result.returncode != 0:
            # CA-12: a real conflict is not resolved automatically -- abort
            # non-destructively and register the typified stop; no retry this cycle
            # or the next, while `parada-condicion` stands.
            guarded_subprocess(
                ["git", "-C", str(config.repo_root), "merge", "--abort"],
                capture_output=True, text=True, check=False,
            )
            wr.set_entry(data, slug, **{
                "parada-condicion": "3",
                "parada-causa": "conflicto de merge (git merge --no-ff) contra el árbol principal",
            })
            wr.save(reg_path, data)
            _log(evidencia_dir, f"{slug}: conflicto de merge -- git merge --abort, "
                                f"parada-condicion=3 registrada")
            continue

        # CA-10 hybrid re-gate rule: cheap re-confirmation when the merge left the
        # gate-evaluated content byte-identical; the full panel only when it did not.
        # Fail-closed: an absent/empty seed-commit means the comparison is not
        # verifiable at all (files_touched == []), not that nothing changed --
        # `all([])` is vacuously True, which would silently pick the cheap branch.
        # Force the expensive branch instead, same as any other unverifiable case.
        identical = bool(seed_commit) and all(
            _show_file_at(config.repo_root, "HEAD", f) == pre_merge_snapshot[f]
            for f in files_touched
        )
        if identical:
            _log(evidencia_dir, f"{slug}: merge limpio, contenido evaluado por el gate "
                                f"idéntico post-merge -- confirmación barata (CA-10)")
        else:
            units_rel = path_relative_to_repo(mandate.units_root, config.repo_root)
            main_unit_dir = config.repo_root / units_rel / slug
            confirmed, causa = _rerun_code_gate(config, mandate, slug, main_unit_dir, evidencia_dir)
            if not confirmed:
                wr.set_entry(data, slug, **{"parada-condicion": "7", "parada-causa": causa})
                wr.save(reg_path, data)
                _log(evidencia_dir, f"{slug}: re-gate completo (CA-10) no confirmó -- {causa}")
                continue
            _log(evidencia_dir, f"{slug}: merge limpio pero el contenido evaluado por "
                                f"el gate cambió post-merge -- re-gate completo confirmó (CA-10)")

        if Path(ruta).exists():
            remove_worktree(config.repo_root, Path(ruta))
        wr.set_entry(data, slug, estado="retirado", fase="done")
        wr.save(reg_path, data)
        _log(evidencia_dir, f"{slug}: merge aterrizado en el árbol principal -- fase=done")


def refresh_board(config: ParallelConfig, mandate: vm.Mandate, data: dict, board: Path) -> None:
    mandate_rel = path_relative_to_repo(mandate.path, config.repo_root)
    stops: list[dict[str, str]] = []
    for member in mandate.members():
        d = unit_dir_for_token(mandate.units_root, member)
        if d is None:
            continue
        slug = d.name
        entry = data.get("worktrees", {}).get(slug, {})
        ruta = entry.get("ruta", "")
        candidate = Path(ruta) / mandate_rel if ruta and Path(ruta).is_dir() \
            else config.repo_root / mandate_rel
        if not candidate.is_file():
            continue
        text = candidate.read_text(encoding="utf-8")
        stop = wr.latest_stop_for_unit(text, slug)
        if stop is None or stop.get("desbloqueo-quien", "").strip():
            continue
        stops.append({
            "unidad": slug,
            "disparador": stop.get("disparador", ""),
            "causa": stop.get("causa", ""),
            "timestamp": stop.get("fecha", ""),
        })
    wr.write_board(board, stops)


def run(config: ParallelConfig) -> int:
    vm.reset()
    mandate = vm.resolve_plan(config.plan)
    if mandate is None:
        raise ParallelError(EXIT_INTERNAL, f"no resuelve el plan: {config.plan}")
    if mandate.form != "plan":
        raise ParallelError(
            EXIT_INTERNAL,
            "supervised_parallel.py solo opera sobre mandatos forma `plan` "
            "(varias unidades miembro) -- una unidad aislada no tiene con qué "
            "paralelizar",
        )

    mandate_slug = mandate.reference
    reg_path = wr.registry_path(SCRIPTS, mandate_slug)
    board = wr.board_path(SCRIPTS, mandate_slug)
    wtroot = wr.worktrees_root(SCRIPTS, mandate_slug)
    evidencia_dir = config.evidencia_dir or (wr.runtime_dir(SCRIPTS, mandate_slug) / "logs")

    data = wr.load(reg_path)
    real_paths = wr.git_worktree_list(config.repo_root)
    for line in wr.reconcile(data, real_paths, wtroot):
        _log(evidencia_dir, f"reconciliación: {line}")
    wr.save(reg_path, data)

    cap = parallelism_cap(mandate, config.tope_worktrees)
    _log(evidencia_dir, f"tope-worktrees={cap} (mandato={mandate_slug})")

    estado_lock, lock_rc = lock_status(mandate.path)
    if estado_lock == "ocupado":
        _log(evidencia_dir, "lock del árbol principal ocupado -- no se lanza "
                            "ningún worktree nuevo esta corrida")
        return EXIT_LOCK_BUSY_AT_START
    if estado_lock == "error":
        raise ParallelError(EXIT_INTERNAL, f"instance_lock.py inspect devolvió {lock_rc}")

    running: dict[str, subprocess.Popen] = {}
    cycle = 0
    while True:
        cycle += 1
        if cycle > config.max_cycles:
            _log(evidencia_dir, f"tope de ciclos ({config.max_cycles}) alcanzado -- "
                                "parada limpia de este proceso; lo que sigue vivo "
                                "continúa por su cuenta")
            return EXIT_OK

        _reap_finished(config, mandate, running, data, reg_path, evidencia_dir)
        _drain_merges(config, mandate, data, reg_path, evidencia_dir)

        active_count = _active_worktree_count(data)
        available = max(0, cap - active_count)

        # T9: `parada-arbol-principal` (mandate-level, written by `_drain_merges` when
        # an orphaned `MERGE_HEAD` is found without a legitimate lock) blocks every NEW
        # launch this cycle -- read only, NEVER cleared here; a human/`claude -p`
        # session clears it as part of writing a resumption entry. Already-`"activo"`
        # worktrees are not stopped -- they keep running until they finish on their
        # own (no work lost), then land in `"fusionando"` like any other unit, waiting
        # for the marker to clear.
        parada_arbol = data.get("parada-arbol-principal")
        if parada_arbol and available > 0:
            _log(evidencia_dir, "parada-arbol-principal presente en worktrees.json -- "
                                "no se lanza ningún worktree nuevo esta corrida "
                                "(los ya activos/fusionando continúan)")
            available = 0

        if available > 0:
            for slug in eligible_units(mandate, data):
                if available <= 0:
                    break
                if slug in running:
                    continue
                # Re-check the main tree's lock immediately before EACH launch, not
                # once per cycle (gate 0118/G4 codigo, iteración 2, hallazgo L1
                # media: plan.md § Decisiones de diseño says literally "antes de
                # cada lanzamiento" -- a per-cycle check let a second unit in the
                # same cycle launch on a stale "free" read if the lock turned busy
                # between the first and second `_launch` of that same cycle). A
                # lock found busy here does not abort already-running worktrees --
                # it only stops NEW launches from this point on; already-active
                # children keep going and are still reaped normally next cycle.
                estado_lock, lock_rc = lock_status(mandate.path)
                if estado_lock == "error":
                    raise ParallelError(EXIT_INTERNAL, f"instance_lock.py inspect devolvió {lock_rc}")
                if estado_lock == "ocupado":
                    _log(evidencia_dir, "lock del árbol principal ocupado -- no se "
                                        "lanzan más worktrees nuevos esta corrida "
                                        "(los ya activos continúan hasta terminar)")
                    break
                _launch(config, mandate, slug, wtroot, data, running, reg_path, evidencia_dir)
                available -= 1
            remaining = [s for s in eligible_units(mandate, data) if s not in running]
            if remaining and available <= 0:
                _log(evidencia_dir,
                     f"tope-worktrees={cap} alcanzado -- serializadas: {', '.join(remaining)}")

        refresh_board(config, mandate, data, board)

        fusionando_pending = any(
            e.get("estado") == "fusionando" for e in data.get("worktrees", {}).values()
        )
        if not running and not fusionando_pending and not eligible_units(mandate, data):
            _log(evidencia_dir, "sin unidades elegibles, ninguna fusionando y ningún "
                                "worktree activo -- fin de esta corrida (lo no "
                                "elegible aún queda serializado para una corrida "
                                "posterior)")
            return EXIT_OK

        time.sleep(config.poll_seconds)


# ======================================================================================
# CLI
# ======================================================================================

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supervised_parallel.py",
        description="Gestor de paralelismo por worktrees de un mandato supervisado (unidad 0118, G4).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    run_p = sub.add_parser("run", help="conduce en paralelo las unidades elegibles del mandato")
    run_p.add_argument("--plan", required=True, metavar="ID")
    run_p.add_argument("--repo-root", default=str(SCRIPTS.parents[1]))
    run_p.add_argument("--tope-worktrees", type=int, default=None)
    run_p.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS)
    run_p.add_argument("--max-ciclos", type=int, default=DEFAULT_MAX_CYCLES)
    run_p.add_argument("--claude-bin", default=DEFAULT_CLAUDE_BIN)
    run_p.add_argument("--evidencia-dir", default=None)
    run_p.add_argument("--conductor-max-pasos", type=int, default=DEFAULT_CONDUCTOR_MAX_STEPS)
    run_p.add_argument("--cota-blanda", type=int, default=DEFAULT_SOFT_SECONDS)
    run_p.add_argument("--margen-duro", type=int, default=DEFAULT_HARD_MARGIN_SECONDS)
    run_p.add_argument("--tope-estancamiento", type=int, default=DEFAULT_STALL_LIMIT)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "run":
            config = ParallelConfig(
                plan=args.plan, repo_root=Path(args.repo_root),
                evidencia_dir=Path(args.evidencia_dir) if args.evidencia_dir else None,
                tope_worktrees=args.tope_worktrees, poll_seconds=args.poll_seconds,
                max_cycles=args.max_ciclos, claude_bin=args.claude_bin,
                conductor_max_pasos=args.conductor_max_pasos, cota_blanda=args.cota_blanda,
                margen_duro=args.margen_duro, tope_estancamiento=args.tope_estancamiento,
            )
            return run(config)
        return EXIT_INTERNAL
    except ParallelError as error:
        print(f"{error}", file=sys.stderr)
        return error.code
    except Exception as error:                    # noqa: BLE001 -- never a bare traceback
        print(f"internal-error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
