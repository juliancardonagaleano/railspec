#!/usr/bin/env python3
"""Gate read-cost benchmark: control (delta off) vs. treatment (delta on),
CA-14 of unit 0116.

Measures the *reading* cost of a tier-`alto` gate corrido de principio a fin
over one artifact (`spec.md` o `plan.md`), comparing:

  - **control**   -- every iteration re-reads the artifact whole (the delta
    mechanism of CA-10/CA-11 disabled by clearing the cache before each
    `gate_delta.py rotate`, so it always reports "ITERACIÓN 1").
  - **tratamiento** -- the same artifact, the same number of iterations, but
    the delta cache is never cleared between iterations: from iteration 2 on,
    `gate_delta.py rotate` prints a diff instead of the full text, exactly as
    `sdd-gate/SKILL.md` paso 1 does (T3.6).

It does not reimplement token measurement (plan.md § Enfoque): each iteration
of each corrida is a real, minimal, non-interactive `claude -p` call (this is
the only way to get real `cache_read_input_tokens`, the metric CA-14 names --
a static count of the printed text is not "medido con la herramienta de
0113"). Both corridas chain their own iterations in one session each
(`--resume`), so the one-time system-prompt/tool-definition bootstrap cost is
amortized identically for control and treatment and does not dilute the
delta effect being measured.

**Deviation from the literal plan.md § Enfoque, documented here rather than
applied silently** (unit 0116 G4, conductor mandate rule): the plan says this
script "llama a `usage_report.py` ... como subproceso para medir el costo en
tokens de cada corrida". Two real limits of `usage_report.py` (unit 0113,
already closed, out of this group's scope to change) make that literal
delegation impossible for this specific use:

  1. `report`/`indicators` filter by a **whole UTC day**
     (`usage/window.py`, D-7/P-2, deliberate design) -- control and
     treatment necessarily run minutes apart on the same day, so a
     day-window merges both corridas' totals instead of separating them.
  2. `append-session` (the one subcommand that is session-scoped) only
     persists a session whose `session-meta` already marks it **closed**
     (D-6/CA-31) -- verified empirically: a one-shot `claude -p
     --output-format json` call never gets a `session-meta` file written for
     it (only interactive sessions that go through the normal `SessionEnd`
     hook path do), so `append-session` reports "not closed" for every call
     this benchmark makes, unconditionally.

A third, empirically found confound: Anthropic's server-side prompt caching
is keyed by prefix, not by Claude Code `session_id` -- a fresh session run
right after another one on the same machine inherits a warm system-prompt
cache from the one that just ran, which a genuinely cold first call does
not get. Run once with `--iteraciones 1`, this made the corrida that ran
*second* look cheaper on `cache_read_input_tokens` for reasons having
nothing to do with the delta mechanism. Mitigated, not eliminated, by one
throwaway warm-up call before *either* corrida starts, so neither pays the
one-time cold-cache cost the other does not -- `gate-delta-benchmark.md`
documents this explicitly as a measurement limitation, not something this
script claims to have fully controlled for.

A fourth, also empirically found (not merely theoretical) issue shaped what
this script actually measures per corrida. Summing `cache_read_input_tokens`
across the `iteraciones` calls of a `--resume` chain conflates two different
costs: the turn that *introduces* new content (full text or diff) pays
mostly `cache_creation_input_tokens`/`input_tokens` for that new content, not
`cache_read_input_tokens` -- caching is prefix-based, so a repeated full
artifact appended again as a *new* turn is not recognized as "already
cached" and is not read from cache, it is cached anew. A first run summing
across iterations measured control and treatment at ~100 % of each other
(no effect visible) precisely because of this -- the metric that actually
differs between "full text every time" and "diff after the first time" is
how much accumulated context a *later* call must re-read, not how much the
call that adds it pays. So each corrida ends with one extra **probe** call
(`--resume`, a trivial prompt asking only to confirm the context is still
there) after its `iteraciones` gate-delta calls, and this script reports
*that probe's* `cache_read_input_tokens` as the corrida's number -- it is
the cost a subsequent lens of the same gate iteration would pay to read
back everything accumulated so far, which is exactly where control (two
full copies of the artifact in its history) and treatment (one full copy
plus a near-empty diff, this fixture being static -- see `spec.md`/`plan.md`
of `benchmark-unit/`, only two static files, no per-iteration variants) part
ways.

Given that, this script still calls `usage_report.py append-session` once
per corrida as the delegation touchpoint the plan asks for -- its outcome
("not closed", expected) is recorded in the evidence as a warning, never
hidden -- but the **authoritative** number this script reports and compares
is `usage.cache_read_input_tokens` read directly from each `claude -p
--output-format json` call's own response: the identical field
`usage/transcripts.py` itself parses out of the same transcript record
(`cache_read_input_tokens=usage["cache_read_input_tokens"]`), not a value
this script derives or recomputes -- it is Claude Code's own authoritative
count for that exact call, summed per corrida.

Subcommand
----------
    gate_size_benchmark.py run --fase <spec|plan> --artefacto <ruta> \
        [--iteraciones 2] [--claude-bin claude] [--usage-report <ruta>] \
        [--out <ruta.md>]

    Runs both corridas over `--artefacto`, calls `usage_report.py
    append-session` once per corrida's session id as a delegation touchpoint
    (its "not closed" outcome is expected and recorded, never hidden --
    see § Deviation above), sums each call's own authoritative
    `cache_read_input_tokens`, computes `tratamiento / control`, and prints a
    result block (also written to `--out` if given). Exit 0 whether or not
    the corrida meets the <= 30 % target -- CA-14 asks this benchmark to
    *measure and document*, not to gate anything (plan.md § Decisiones de
    diseño, same regime as `freeze-baseline` of 0113); exit 1 only on a real
    execution error (`claude`/`gate_delta.py` subprocess failing, or a
    missing `--artefacto`).

Every iteration's prompt is fixed and content-neutral ("no evalúes, solo
confirma que leíste") -- the benchmark measures read cost, not review
quality; a real tier-`alto` panel (multiple critics, `sdd-refutador`,
governance lookups) is out of scope for this cost isolation (plan.md § CA-14:
"el resto del comportamiento ... se mantiene igual en 'antes' y 'después',
porque no son objeto de esta medición" -- the read mechanism is the one
variable).

No external dependencies beyond the stdlib: `argparse`, `json`, `re`,
`subprocess`, `sys`, `uuid`, `tempfile`, `pathlib`, `datetime`.
"""

from __future__ import annotations

import argparse
import datetime as _dt
import json
import subprocess
import sys
import tempfile
import uuid
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
GATE_DELTA = SCRIPTS_DIR / "gate_delta.py"
USAGE_REPORT_DEFAULT = SCRIPTS_DIR / "usage_report.py"

READ_PROMPT_TEMPLATE = (
    "Este es el contenido que un lente del panel de `sdd-gate` leería en la "
    "iteración {n} de un gate. No lo evalúes ni lo critiques: responde "
    "únicamente con la palabra `leído`.\n\n{content}"
)


class BenchmarkError(RuntimeError):
    pass


def _run(cmd: list[str], **kwargs) -> subprocess.CompletedProcess:
    result = subprocess.run(cmd, capture_output=True, text=True, **kwargs)
    return result


def _gate_delta_clear(unidad: Path, fase: str) -> None:
    result = _run([sys.executable, str(GATE_DELTA), "clear", "--unidad", str(unidad), "--fase", fase])
    if result.returncode != 0:
        raise BenchmarkError(f"gate_delta.py clear falló: {result.stderr}")


def _gate_delta_rotate(unidad: Path, fase: str, artefacto: Path) -> str:
    result = _run(
        [sys.executable, str(GATE_DELTA), "rotate", "--unidad", str(unidad), "--fase", fase, "--artefacto", str(artefacto)]
    )
    if result.returncode != 0:
        raise BenchmarkError(f"gate_delta.py rotate falló: {result.stderr}")
    return result.stdout


def _claude_call(claude_bin: str, prompt: str, *, session_id: str, resume: bool) -> dict:
    cmd = [claude_bin, "-p", prompt, "--output-format", "json"]
    if resume:
        cmd += ["--resume", session_id]
    else:
        cmd += ["--session-id", session_id]
    result = _run(cmd)
    if result.returncode != 0:
        raise BenchmarkError(f"`claude -p` falló (rc={result.returncode}): {result.stderr}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BenchmarkError(
            f"`claude -p --output-format json` no devolvió JSON válido: {exc}\n{result.stdout}"
        ) from exc


def _run_corrida(
    *, kind: str, fase: str, artefacto: Path, iteraciones: int, claude_bin: str,
    fixture_dir: Path | None = None,
) -> dict:
    """Una corrida (control o tratamiento): `iteraciones` llamadas reales de
    `claude -p`, encadenadas con `--resume` dentro de una única sesión.
    Devuelve `{start, end, session_id, calls}` -- `start`/`end` delimitan la
    ventana de reloj que se le pasa a `usage_report.py report`.

    `fixture_dir` (opcional, decisión directa `0123-D5`, 2026-09-21): si se
    pasa, cada iteración *n* usa `<fixture_dir>/iter<n>-<basename>` como
    artefacto de entrada (CA-14 medición más representativa: el contenido
    efectivamente cambia entre iteraciones, así que el diff del tratamiento
    no es trivialmente vacío). Si `fixture_dir` no se pasa, todas las
    iteraciones usan el mismo `--artefacto` (modo histórico, subóptimo:
    `gate-delta-benchmark.md § Lectura del resultado` ya documentó que el
    diff está casi vacío bajo esta medición).
    """
    with tempfile.TemporaryDirectory(prefix=f"gate-size-benchmark-{kind}-") as tmp:
        unidad_cache = Path(tmp)
        session_id = str(uuid.uuid4())
        start = _dt.datetime.now(_dt.timezone.utc)
        calls = []
        cache_read_total = 0
        for n in range(1, iteraciones + 1):
            if kind == "control":
                # CA-14: "únicamente el recorte por delta desactivado" -- se
                # limpia la caché antes de cada rotate para que SIEMPRE
                # reporte "ITERACIÓN 1" (texto completo), nunca un diff.
                _gate_delta_clear(unidad_cache, fase)
            iter_artefacto = artefacto
            if fixture_dir is not None:
                candidate = fixture_dir / f"iter{n}-{artefacto.name}"
                if candidate.is_file():
                    iter_artefacto = candidate
            text = _gate_delta_rotate(unidad_cache, fase, iter_artefacto)
            prompt = READ_PROMPT_TEMPLATE.format(n=n, content=text)
            response = _claude_call(claude_bin, prompt, session_id=session_id, resume=(n > 1))
            usage = response.get("usage", {}) or {}
            call_tokens = int(usage.get("cache_read_input_tokens", 0))
            cache_read_total += call_tokens
            calls.append({
                "iteracion": n,
                "session_id": response.get("session_id", session_id),
                "cache_read_input_tokens": call_tokens,
            })

        # Sonda final (ver docstring del módulo, § Deviation, cuarto punto):
        # una llamada más, encadenada, cuyo `cache_read_input_tokens` es el
        # costo de releer todo lo acumulado hasta ahora -- eso, no la suma
        # de las llamadas que introducen contenido nuevo, es lo que separa
        # control de tratamiento.
        probe = _claude_call(
            claude_bin,
            "Confirma que sigues teniendo el contexto de esta conversación: "
            "responde únicamente con la palabra `ok`.",
            session_id=session_id, resume=True,
        )
        probe_tokens = int((probe.get("usage", {}) or {}).get("cache_read_input_tokens", 0))

        end = _dt.datetime.now(_dt.timezone.utc)
        return {
            "kind": kind,
            "start": start.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "end": end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "session_id": session_id,
            "calls": calls,
            "cache_read_input_tokens_sum": cache_read_total,
            "cache_read_input_tokens": probe_tokens,
        }


def _usage_report_delegation_touchpoint(usage_report_script: Path, session_id: str) -> str:
    """Llamada de delegación a `usage_report.py append-session` que el
    plan pide (plan.md § Enfoque) -- su resultado se registra siempre
    (nunca se oculta), pero nunca es la fuente del número que este script
    reporta (ver docstring del módulo, § Deviation): un `claude -p` de una
    sola llamada nunca produce `session-meta` "closed", así que
    `append-session` reporta "not closed" de forma esperada y sistemática
    para toda sesión que este benchmark genera."""
    result = _run([sys.executable, str(usage_report_script), "append-session", "--session-id", session_id])
    if result.returncode != 0:
        return f"aviso — usage_report.py append-session salió con rc={result.returncode}: {result.stderr.strip()}"
    return result.stdout.strip()


def cmd_run(args: argparse.Namespace) -> int:
    artefacto = Path(args.artefacto)
    if not artefacto.is_file():
        print(f"ERROR — no existe {artefacto}", file=sys.stderr)
        return 1
    usage_report_script = Path(args.usage_report) if args.usage_report else USAGE_REPORT_DEFAULT

    try:
        # Calienta el prefijo compartido (system prompt + definición de
        # herramientas) antes de ambas corridas, para que ninguna de las dos
        # cargue con el costo de caché "fría" que la otra no paga solo por
        # haber corrido primero -- ver § Deviation del docstring del módulo.
        _claude_call(args.claude_bin, "ok", session_id=str(uuid.uuid4()), resume=False)
        control = _run_corrida(
            kind="control", fase=args.fase, artefacto=artefacto,
            iteraciones=args.iteraciones, claude_bin=args.claude_bin,
            fixture_dir=args.fixture_dir,
        )
        treatment = _run_corrida(
            kind="tratamiento", fase=args.fase, artefacto=artefacto,
            iteraciones=args.iteraciones, claude_bin=args.claude_bin,
            fixture_dir=args.fixture_dir,
        )
        control_touchpoint = _usage_report_delegation_touchpoint(usage_report_script, control["session_id"])
        treatment_touchpoint = _usage_report_delegation_touchpoint(usage_report_script, treatment["session_id"])
    except BenchmarkError as exc:
        print(f"ERROR — {exc}", file=sys.stderr)
        return 1

    control_tokens = control["cache_read_input_tokens"]
    treatment_tokens = treatment["cache_read_input_tokens"]
    ratio = (treatment_tokens / control_tokens) if control_tokens else None
    ratio_pct = f"{ratio * 100:.1f}%" if ratio is not None else "n/a (control=0)"
    meets_target = ratio is not None and ratio <= 0.30

    lines = [
        f"## Benchmark de costo del gate por delta — fase `{args.fase}`",
        "",
        f"- Artefacto: `{artefacto}`",
        f"- Iteraciones simuladas: {args.iteraciones} (tier `alto`, tope 0117-D4)",
        f"- Sesión control: `{control['session_id']}` ({control['start']} a "
        f"{control['end']})",
        f"- Sesión tratamiento: `{treatment['session_id']}` ({treatment['start']} a "
        f"{treatment['end']})",
        f"- `usage_report.py append-session` (control, delegación, ver Deviation): "
        f"{control_touchpoint}",
        f"- `usage_report.py append-session` (tratamiento, delegación, ver Deviation): "
        f"{treatment_touchpoint}",
        f"- `cache_read_input_tokens` control, suma de las {args.iteraciones} llamadas "
        f"que introducen contenido (referencial, no el número comparado — ver "
        f"Deviation): {control['cache_read_input_tokens_sum']}",
        f"- `cache_read_input_tokens` tratamiento, ídem: "
        f"{treatment['cache_read_input_tokens_sum']}",
        f"- `cache_read_input_tokens` control, sonda final (**el número comparado**): "
        f"{control_tokens}",
        f"- `cache_read_input_tokens` tratamiento, sonda final (ídem): {treatment_tokens}",
        f"- Razón tratamiento/control (sobre la sonda final): {ratio_pct}",
        f"- Cumple objetivo (<= 30 %): {'sí' if meets_target else 'no'}",
    ]
    text = "\n".join(lines)
    print(text)

    if args.out:
        out_path = Path(args.out)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(text + "\n", encoding="utf-8")

    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        prog="gate_size_benchmark.py",
        description="Benchmark de costo de lectura del gate por delta, control vs "
                     "tratamiento (CA-14).",
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    run_parser = sub.add_parser("run", help="corre control y tratamiento sobre un artefacto")
    run_parser.add_argument("--fase", required=True, choices=["spec", "plan", "tasks", "codigo"])
    run_parser.add_argument("--artefacto", required=True, metavar="RUTA")
    run_parser.add_argument("--iteraciones", type=int, default=2, help="tier alto = 2 (default, 0117-D4)")
    run_parser.add_argument("--claude-bin", dest="claude_bin", default="claude")
    run_parser.add_argument("--usage-report", dest="usage_report", default=None,
                             help="override de la ruta a usage_report.py")
    run_parser.add_argument("--out", dest="out", default=None, help="ruta donde también escribir el resultado")
    run_parser.add_argument("--fixture-dir", dest="fixture_dir", default=None, metavar="DIR",
                             type=Path,
                             help="directorio con iter<N>-<basename> por iteración; "
                                  "permite medir el costo del delta con contenido que "
                                  "efectivamente cambia entre iteraciones (decisión "
                                  "0123-D5, cierra CA-14 con datos más representativos)")
    run_parser.set_defaults(func=cmd_run)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
