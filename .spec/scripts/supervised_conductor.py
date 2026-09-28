#!/usr/bin/env python3
"""External bounded-context conductor for a supervised mandate (unit 0118, Part A).

`sdd-supervisado` already knows how to pick the mandate's next unit/phase
(`## Cadena de dependencias`, `sdd-retomar`) and how to close a turn "with
normalcy" — bitácora + `_estado.yaml` + an emptied lock — even when it does not
reach the mandate's own close (§ 1.1: "se anota en bitácora qué se hizo y por
qué se detiene ahí, y se vacía igual"). What did not exist is *who* decides,
automatically and from outside the conversation, that each turn lasts exactly
one step, and who fires the next one. That "who" is this script: it launches a
fresh `claude -p` once per step, with the same invocation pattern and clock-cap
watchdog already used by `supervised-test.sh` (`correr_con_cota`,
`:737-785`/`:1516-1543`), and decides advance/stop by reading only
`_estado.yaml`, the exit code of `instance_lock.py inspect`, and the two
literal lines (`retoma sin punto verificado` and `retoma desactualizada
respecto de bitácora`) that `validate_mandate.py --resumen` prints
— never the free-text stdout of `claude`.

Usage
-----
    supervised_conductor.py run (--plan <id|ruta> | --unidad <ruta>)
        [--evidencia-dir DIR] [--max-pasos N]
        [--cota-blanda SECS] [--margen-duro SECS]
        [--tope-estancamiento N] [--claude-bin RUTA] [--sesion ID]

`--plan`/`--unidad` resolve **exactly** like `validate_mandate.py`'s flags of
the same name (`resolve_plan`/`resolve_unit`, imported, never reimplemented).

What this script reuses, by import or by subprocess, never by rewrite (CA-13):
    - `validate_mandate.py`   (`resolve_plan`, `resolve_unit`, `Mandate`, `--resumen`)
    - `instance_lock.py inspect` (never `acquire`/`release` — those stay the
      turn's own act inside each `claude -p`, per § 1.1 of `sdd-supervisado`)
    - `_common.py` (`field`)
    - `mandate_anchor.py`     (available by subprocess if a caller needs the
      anchored commit; this script does not call it itself)

Exit codes of *this process* (distinct from the per-invocation `.exit-code.txt`
files it writes under `--evidencia-dir`):
    0   the mandate reached a terminal state (`## Estado` resuelto a `cerrado`,
        or the single unit's `fase` is `done`), or `--max-pasos` was reached
        with no incident — both are a clean stop, not an error.
    1   a typified stop mid-run (estancamiento, `retoma sin punto verificado`,
        a hard-kill whose lock came back occupied — potential orphan).
    2   internal error (never a bare traceback — see `main`).
    3   the lock was occupied *before* the first invocation of this run — the
        run never launched anything.
  142   the last invocation was killed by the hard clock cap (`cota-agotada`);
        kept numerically aligned with `RC_COTA` of `supervised-test.sh` so old
        and new evidence read the same code the same way.

stdlib only, no network, no resident process beyond the `claude -p` children it
launches and waits on.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import os
import re
import signal
import subprocess
import sys
import time
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import datetime, timezone
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

import instance_lock  # noqa: E402
import validate_mandate as vm  # noqa: E402
from _common import field  # noqa: E402

# --- Exit codes of this process -----------------------------------------------------
EXIT_OK = 0
EXIT_STOPPED = 1
EXIT_INTERNAL = 2
EXIT_LOCK_BUSY_AT_START = 3
EXIT_COTA = 142                       # same numeric convention as RC_COTA (supervised-test.sh)

# --- Defaults --------------------------------------------------------------------------
# Soft budget (communicated to the instance via the prompt) and hard margin (added on
# top for the watchdog's TERM/KILL) are deliberately two different numbers — see
# plan.md § Decisiones de diseño. 25 min soft / +5 min hard margin: comfortably above
# a spec/plan/tasks/gate step, comfortably below a stuck session going undetected for
# long.
DEFAULT_SOFT_SECONDS = 1500
DEFAULT_HARD_MARGIN_SECONDS = 300
DEFAULT_STALL_LIMIT = 3
DEFAULT_MAX_STEPS = 200
DEFAULT_CLAUDE_BIN = "claude"

# Same allow-list already proven empirically by the 0109b pilot
# (`supervised-test.sh:213`, `PILOT_ALLOWED_TOOLS`): `mcp__pce-mcp` for
# governance, `Bash` for what the conductor actually runs (the validator, git,
# worktree teardown). Not rediscovered here — copied verbatim, same reason.
ALLOWED_TOOLS = ["mcp__pce-mcp", "Bash"]

PROMPT_TEMPLATE = (
    "Ejecuta la skill `sdd-supervisado` sobre el mandato en «{mandato}». Alcance "
    "EXACTO de esta invocación: exactamente UN paso importante pendiente "
    "(spec, plan, tareas, un grupo de implementación, o un gate) de la unidad "
    "que corresponda según el punto de retoma reconstruido desde disco. Al "
    "completar ese paso, cierra turno de inmediato (§ 1.1/§ 10 de la skill) "
    "sin encadenar un segundo paso dentro de esta misma invocación. No asumas "
    "contexto de invocaciones anteriores de este mandato: reconstruye todo "
    "—incluido el punto de retoma, vía `sdd-retomar` forkeada— exclusivamente "
    "desde `_estado.yaml`, `tasks.md` y `bitacora.md` en disco."
)


class ConductorError(Exception):
    """A precondition of this script, carrying the exit code it maps to."""

    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code


# ======================================================================================
# Mandate / unit resolution (reused from validate_mandate.py, never reimplemented)
# ======================================================================================

def resolve_mandate(plan: str | None, unidad: str | None) -> tuple[vm.Mandate, Path | None]:
    """Returns `(mandate, unit_dir)`. `unit_dir` is known only for the `unidad` form —
    a plan mandate may have several member units; which one is "in progress" is
    something only `sdd-supervisado` (reading `## Cadena de dependencias`) decides."""
    vm.reset()
    if bool(plan) == bool(unidad):
        raise ConductorError(EXIT_INTERNAL, "hay que dar exactamente uno de --plan o --unidad")
    if plan:
        m = vm.resolve_plan(plan)
        if m is None:
            raise ConductorError(EXIT_INTERNAL, f"no resuelve el plan: {plan}")
        return m, None
    unit_dir = Path(unidad)
    if not unit_dir.is_dir():
        raise ConductorError(EXIT_INTERNAL, f"no existe el directorio de unidad: {unit_dir}")
    m = vm.resolve_unit(unit_dir)
    if m is None:
        raise ConductorError(EXIT_INTERNAL, f"la unidad no resuelve su mandato: {unit_dir}")
    return m, unit_dir


def member_unit_dirs(mandate: vm.Mandate, unit_dir: Path | None) -> list[Path]:
    """Unit directories whose `_estado.yaml` this run watches for the stall
    signature (plan.md § Decisiones: "firma de la unidad en curso"). For a
    single isolated unit this is unambiguous. For a plan mandate, "en curso" is
    not resolved by guessing from prose (`pri-ia-cero-inferencia-implicita`):
    every member unit is watched, so a stall is only declared when **none** of
    them moved — a stricter, disk-only reading of the same signal."""
    if unit_dir is not None:
        return [unit_dir]
    dirs = []
    for slug in mandate.members():
        candidate = mandate.units_root / slug
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs


def mandate_closed(mandate: vm.Mandate, unit_dirs: list[Path]) -> bool:
    """Terminal-state check, read from disk only (CA-05)."""
    if mandate.form == "plan":
        return mandate.state == "cerrado"
    if not unit_dirs:
        return False
    state = vm.read_unit_state(unit_dirs[0])
    return state.get("fase") == "done"


# ======================================================================================
# Stall-detection signature (CA — Decisiones de diseño: firma fase+gates)
# ======================================================================================

_TOP_KEY = re.compile(r"^[A-Za-z_][\w \tñáéíóúÑÁÉÍÓÚ-]*:", re.MULTILINE)


def gates_block(text: str) -> str:
    """Raw text of the top-level `gates:` YAML key, from its own line to the next
    top-level key (or EOF). No PyYAML (same constraint as `_common.py`)."""
    m = re.search(r"^gates:.*$", text, re.MULTILINE)
    if not m:
        return ""
    rest = text[m.end():]
    nxt = _TOP_KEY.search(rest)
    body = rest[:nxt.start()] if nxt else rest
    return m.group(0) + body


def unit_signature(unit_dir: Path) -> str:
    state_file = unit_dir / "_estado.yaml"
    if not state_file.is_file():
        return "ausente"
    text = state_file.read_text(encoding="utf-8")
    return field(text, "fase") + "\n" + gates_block(text)


def signature(unit_dirs: list[Path]) -> str:
    """A single digest over every watched unit's `(fase, gates)` — sorted by unit
    path so the digest is deterministic across runs."""
    parts = [f"{d}\n{unit_signature(d)}" for d in sorted(unit_dirs, key=str)]
    return hashlib.sha256("\n---\n".join(parts).encode("utf-8")).hexdigest()


# ======================================================================================
# Watchdog — adapted from `correr_con_cota` (supervised-test.sh:737-785)
# ======================================================================================

@dataclass
class RunResult:
    rc: int
    motivo: str            # "fin" | "cota-agotada" | "error"
    duracion_s: int
    pid: int
    inicio_iso: str
    fin_iso: str


def run_with_cap(cmd: list[str], log_path: Path, soft_seconds: int, hard_margin_seconds: int) -> RunResult:
    """One invocation, with a **hard** clock cap (`soft_seconds + hard_margin_seconds`).
    The soft budget itself is not enforced here — it is only communicated to the
    instance through the prompt (plan.md § Decisiones); this watchdog only ever
    kills at the hard cap, exactly the way `correr_con_cota` kills at its single
    cap, adapted to two numbers instead of one."""
    hard_seconds = soft_seconds + hard_margin_seconds
    start = time.monotonic()
    start_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("wb") as log_file:
        proc = subprocess.Popen(
            cmd, stdin=subprocess.DEVNULL, stdout=log_file, stderr=subprocess.STDOUT,
            preexec_fn=os.setpgrp,
        )
        motivo = "fin"
        while True:
            try:
                rc = proc.wait(timeout=1)
                break
            except subprocess.TimeoutExpired:
                pass
            elapsed = time.monotonic() - start
            if elapsed >= hard_seconds:
                motivo = "cota-agotada"
                _kill_group(proc.pid, signal.SIGTERM)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    _kill_group(proc.pid, signal.SIGKILL)
                    proc.wait()
                rc = EXIT_COTA
                break
    duracion = int(time.monotonic() - start)
    fin_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if motivo == "fin" and rc != 0:
        motivo = "error"
    return RunResult(rc=rc, motivo=motivo, duracion_s=duracion, pid=proc.pid,
                     inicio_iso=start_iso, fin_iso=fin_iso)


def _kill_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        with contextlib.suppress(ProcessLookupError):
            os.kill(pid, sig)


# ======================================================================================
# Per-invocation `.exit-code.txt` (CA-08) — 5 lines, machine-readable first line
# ======================================================================================

def write_exit_code_file(path: Path, result: RunResult, avanzo: bool, estancamiento_consecutivo: int) -> None:
    path.write_text(
        f"{result.rc}\n"
        f"motivo: {result.motivo}\n"
        f"duracion-s: {result.duracion_s}\n"
        f"avanzo: {'si' if avanzo else 'no'}\n"
        f"estancamiento-consecutivo: {estancamiento_consecutivo}\n",
        encoding="utf-8",
    )


# ======================================================================================
# `retoma sin punto verificado` / `retoma desactualizada respecto de bitácora` — the
# two literals the loop reads from `--resumen` (CA-05 / Riesgo 3): a fixed envelope,
# never open-text interpretation. Reused from `validate_mandate.py`, never
# reimplemented (CA-13) — `resume_divergence_kind` there is the one place that tells
# the two situations these literals name apart (gate de código 0118, 2026-09-21: a
# hallazgo alta found the previous single-literal check collapsing them into one).
# ======================================================================================

UNRESOLVED_RESUME_LINE = vm.UNRESOLVED_RESUME_LINE
STALE_RESUME_LINE = vm.STALE_RESUME_LINE


def resumen_stdout(plan: str | None, unidad: str | None) -> str:
    args = [sys.executable, str(SCRIPTS / "validate_mandate.py"), "--resumen"]
    args += ["--plan", plan] if plan else ["--unidad", unidad]
    completed = subprocess.run(args, capture_output=True, text=True, check=False)
    return completed.stdout


# ======================================================================================
# Lock inspection (CA-13/CA-05): only `inspect`, never `acquire`/`release`.
# ======================================================================================

def lock_status(mandate_path: Path) -> tuple[str, int]:
    completed = subprocess.run(
        [sys.executable, str(SCRIPTS / "instance_lock.py"), "inspect", str(mandate_path)],
        capture_output=True, text=True, check=False,
    )
    if completed.returncode == instance_lock.EXIT_OK:
        return "libre", completed.returncode
    if completed.returncode == instance_lock.EXIT_BUSY:
        return "ocupado", completed.returncode
    return "error", completed.returncode


# ======================================================================================
# The loop (CA-01/02/03/04/05/06/07/08/11/12/13/14)
# ======================================================================================

@dataclass
class ConductorConfig:
    plan: str | None
    unidad: str | None
    evidencia_dir: Path
    max_pasos: int = DEFAULT_MAX_STEPS
    cota_blanda: int = DEFAULT_SOFT_SECONDS
    margen_duro: int = DEFAULT_HARD_MARGIN_SECONDS
    tope_estancamiento: int = DEFAULT_STALL_LIMIT
    claude_bin: str = DEFAULT_CLAUDE_BIN
    sesion: str = ""
    claude_extra_args: list[str] = dataclass_field(default_factory=list)


def build_command(config: ConductorConfig, reference: str) -> list[str]:
    prompt = PROMPT_TEMPLATE.format(mandato=reference)
    cmd = [config.claude_bin, "-p", prompt, "--permission-mode", "acceptEdits",
           "--allowedTools", *ALLOWED_TOOLS]
    cmd += config.claude_extra_args
    return cmd


def log_line(evidencia_dir: Path, text: str) -> None:
    evidencia_dir.mkdir(parents=True, exist_ok=True)
    with (evidencia_dir / "conductor.log").open("a", encoding="utf-8") as fh:
        fh.write(text.rstrip("\n") + "\n")


def unambiguous_reference(mandate: vm.Mandate) -> str:
    """What goes into `{mandato}` of `PROMPT_TEMPLATE` (H7, gate de código
    2026-09-21). `Mandate.reference` is unambiguous for the `plan` form (a
    plan id/dir name is unique in the backlog), but for the `unidad` form it
    is the **literal, unresolved** value of the unit's own `mandato:` field
    (`validate_mandate.py::resolve_unit`, `value = field(text, "mandato")`) —
    typically the generic string `mandato.md`, shared by every isolated
    supervised unit in the backlog. A prompt built from that literal cannot
    tell one unit's mandate from any other's, and the real pilot
    (`evidencia/piloto-real/RESUMEN.md`, hallazgo H7) proved it: 2 of 3 real
    `claude -p` invocations reconstructed and acted on unrelated real
    mandates of the backlog. `mandate.path` is what actually disambiguates —
    it is the resolved file this specific mandate was read from (the unit's
    own `mandato.md`, not just its literal field value) — so the `unidad`
    form uses it instead."""
    if mandate.form == "plan":
        return mandate.reference
    return str(mandate.path)


def run(config: ConductorConfig) -> int:
    mandate, unit_dir = resolve_mandate(config.plan, config.unidad)
    watched = member_unit_dirs(mandate, unit_dir)
    reference = unambiguous_reference(mandate)

    signatures: list[str] = []
    step = 0
    last_result: RunResult | None = None

    while True:
        step += 1
        if step > config.max_pasos:
            log_line(config.evidencia_dir, f"paso {step}: tope-de-pasos ({config.max_pasos}) alcanzado — parada limpia")
            return EXIT_OK

        estado_lock, lock_rc = lock_status(mandate.path)
        if estado_lock == "ocupado":
            log_line(config.evidencia_dir, f"paso {step}: lock ocupado — parada, sin lanzar (rc inspect={lock_rc})")
            return EXIT_LOCK_BUSY_AT_START if last_result is None else EXIT_STOPPED
        if estado_lock == "error":
            raise ConductorError(EXIT_INTERNAL, f"instance_lock.py inspect devolvió {lock_rc} (no libre/ocupado)")

        before = signature(watched) if watched else ""

        cmd = build_command(config, reference)
        log_path = config.evidencia_dir / f"{step}.log"
        exit_code_path = config.evidencia_dir / f"{step}.exit-code.txt"
        log_line(config.evidencia_dir,
                 f"paso {step}: lanzando {cmd[0]} -p (inicio={datetime.now(timezone.utc).isoformat()})")
        result = run_with_cap(cmd, log_path, config.cota_blanda, config.margen_duro)
        last_result = result
        log_line(config.evidencia_dir,
                 f"paso {step}: terminó pid={result.pid} rc={result.rc} motivo={result.motivo} "
                 f"duracion-s={result.duracion_s} inicio={result.inicio_iso} fin={result.fin_iso} "
                 f"(medir CA-12: usage_report.py indicators --from {result.inicio_iso} --to {result.fin_iso})")

        after = signature(watched) if watched else ""
        avanzo = bool(watched) and (before != after)
        if watched:
            signatures.append(after)
        else:
            signatures.append(f"sin-unidad-{step}")

        estancamiento_consecutivo = _trailing_repeats(signatures)

        if result.motivo == "cota-agotada":
            estado_lock_post, _ = lock_status(mandate.path)
            if estado_lock_post == "ocupado":
                motivo_final = "cota-agotada-lock-huerfano"
                write_exit_code_file(exit_code_path, result, avanzo, estancamiento_consecutivo)
                log_line(config.evidencia_dir,
                         f"paso {step}: {motivo_final} — el lock quedó ocupado tras el kill; "
                         "no se libera ni relanza, un humano lo resuelve (§ 1.1)")
                return EXIT_STOPPED
            write_exit_code_file(exit_code_path, result, avanzo, estancamiento_consecutivo)
            log_line(config.evidencia_dir, f"paso {step}: cota-agotada, lock libre — árbol resumible")
            return EXIT_COTA

        write_exit_code_file(exit_code_path, result, avanzo, estancamiento_consecutivo)

        if result.motivo == "error":
            log_line(config.evidencia_dir, f"paso {step}: la invocación terminó con error (rc={result.rc}) — parada")
            return EXIT_STOPPED

        resumen = resumen_stdout(config.plan, config.unidad)
        if STALE_RESUME_LINE in resumen:
            # Hallazgo alta from the code gate of 0118 (2026-09-21, upheld by
            # sdd-refutador): `print_summary` emits this line ONLY when
            # (`validate_mandate.resume_divergence_kind` == "stale") --
            # `## Punto de retoma` has an entry, but it predates the latest
            # `bitacora.md` entry -- a real divergence between what the resume
            # point claims and what actually happened (plan.md § Riesgo 3).
            # Unlike the H6 case below, this ALWAYS stops, with or without
            # `avanzo`: a step can advance fase/gates and still leave the
            # resume point stale (they are independent signals -- `avanzo` is
            # blind to `bitacora.md`/`## Punto de retoma`), and chaining the
            # next step onto a stale resume point is exactly what Riesgo 3
            # asks to prevent.
            log_line(config.evidencia_dir,
                     f"paso {step}: validate_mandate.py --resumen reporta «{STALE_RESUME_LINE}» "
                     "— el punto de retoma quedó atrás respecto de la bitácora — parada, "
                     "no avanzar a ciegas")
            return EXIT_STOPPED
        if UNRESOLVED_RESUME_LINE in resumen and not avanzo:
            # H6 (gate de código 2026-09-21, piloto real): `print_summary` emite
            # esta línea para CUALQUIER mandato `aprobado` que nunca escribió
            # `## Punto de retoma` (esa sección solo se llena al parar, § 6 de
            # `sdd-supervisado` — nunca en un cierre de turno normal), así que se
            # dispara igual tras un paso recién terminado con éxito y avance real
            # de un mandato que jamás se pausó. El piloto real lo demostró: paró
            # tras el primer paso pese a `rc=0`/`motivo=fin` y trabajo escrito de
            # verdad. `avanzo` (firma fase+gates de la unidad, antes/después,
            # CA — Riesgo 6) ya es la evidencia en disco de que el paso avanzó de
            # verdad — la misma fuente que exige `pri-ia-cero-inferencia-implicita`,
            # no texto libre. Solo cuando NO hubo avance se trata la señal como la
            # divergencia bitácora/retoma que el Riesgo 3 de `plan.md` quiso
            # detectar (una escritura parcial no deja rastro de avance tampoco).
            # The case of a real divergence with a resume point ALREADY written
            # but stale is handled above, via STALE_RESUME_LINE, always (without
            # the `not avanzo` guard).
            log_line(config.evidencia_dir,
                     f"paso {step}: validate_mandate.py --resumen reporta «{UNRESOLVED_RESUME_LINE}» "
                     "y la unidad no avanzó (firma fase+gates sin cambio) — parada, no avanzar a ciegas")
            return EXIT_STOPPED

        if estancamiento_consecutivo >= config.tope_estancamiento:
            log_line(config.evidencia_dir,
                     f"paso {step}: estancamiento — {estancamiento_consecutivo} pasos consecutivos sin cambio "
                     "de firma (fase+gates) de la unidad en curso")
            return EXIT_STOPPED

        if mandate_closed(mandate, watched):
            log_line(config.evidencia_dir, f"paso {step}: mandato en estado terminal — fin limpio")
            return EXIT_OK

        # Otherwise: advance to the next fresh invocation (CA-11 — a new PID per step).


def _trailing_repeats(signatures: list[str]) -> int:
    """How many of the most recent signatures, counting from the end, are all
    equal to the last one."""
    if not signatures:
        return 0
    last = signatures[-1]
    count = 0
    for sig in reversed(signatures):
        if sig != last:
            break
        count += 1
    return count


# ======================================================================================
# CLI
# ======================================================================================

def default_evidencia_dir(plan: str | None, unidad: str | None) -> Path:
    slug = plan or Path(unidad or "mandato").name
    return SCRIPTS.parent / ".supervised-runtime" / slug / "conductor"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="supervised_conductor.py",
        description="Bucle externo de contexto acotado para un mandato supervisado (unidad 0118).",
    )
    sub = parser.add_subparsers(dest="subcommand", required=True)
    run_p = sub.add_parser("run", help="conduce el mandato paso a paso")
    run_p.add_argument("--plan", metavar="ID|RUTA")
    run_p.add_argument("--unidad", metavar="RUTA")
    run_p.add_argument("--evidencia-dir", metavar="DIR", default=None)
    run_p.add_argument("--max-pasos", type=int, default=DEFAULT_MAX_STEPS)
    run_p.add_argument("--cota-blanda", type=int, default=DEFAULT_SOFT_SECONDS)
    run_p.add_argument("--margen-duro", type=int, default=DEFAULT_HARD_MARGIN_SECONDS)
    run_p.add_argument("--tope-estancamiento", type=int, default=DEFAULT_STALL_LIMIT)
    run_p.add_argument("--claude-bin", default=DEFAULT_CLAUDE_BIN)
    run_p.add_argument("--sesion", default="")
    run_p.add_argument("--claude-arg", dest="claude_extra_args", action="append", default=[],
                       help="argumento extra pasado tal cual a `claude -p` (repetible)")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.subcommand == "run":
            evidencia_dir = Path(args.evidencia_dir) if args.evidencia_dir else \
                default_evidencia_dir(args.plan, args.unidad)
            config = ConductorConfig(
                plan=args.plan, unidad=args.unidad, evidencia_dir=evidencia_dir,
                max_pasos=args.max_pasos, cota_blanda=args.cota_blanda,
                margen_duro=args.margen_duro, tope_estancamiento=args.tope_estancamiento,
                claude_bin=args.claude_bin, sesion=args.sesion,
                claude_extra_args=list(args.claude_extra_args),
            )
            return run(config)
        return EXIT_INTERNAL
    except ConductorError as error:
        print(f"{error}", file=sys.stderr)
        return error.code
    except Exception as error:                    # noqa: BLE001 — never a bare traceback
        print(f"internal-error: {type(error).__name__}: {error}", file=sys.stderr)
        return EXIT_INTERNAL


if __name__ == "__main__":
    sys.exit(main())
