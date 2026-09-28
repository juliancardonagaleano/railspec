#!/usr/bin/env bash
# Regression test for `LOG_PATRON_PERMISO` / `revisar_log` in
# `supervised-test.sh` (unidad 0114, corrección del falso positivo de T21,
# 2026-09-21: ver `.spec/units/0114-preflight-y-rituales-como-scripts/
# bitacora.md` en torno a la entrada de T21, y `.spec/planes/
# plan-ejemplo/plan-maestro.md` § Punto de retoma, entrada
# 2026-09-21T15:03:00Z).
#
# Covers exactly the two cases the re-gate distinguished:
#   (a) the real stuck-on-permission signal `revisar_log` must keep catching —
#       the CLI's own literal refusal ("El comando requiere tu aprobación
#       manual — no puedo forzarla…", reproduced in `.spec/units/
#       0109b-orquestacion-supervisado/bitacora.md`, ALTA 1) — in Spanish and in
#       English.
#   (b) the false positive that must NOT be flagged anymore — a run that
#       finished correctly (`aprobacion-ausente`, `motivo=fin`, `rc=0`) whose
#       closing prose *mentions* "aprobación" while explaining, in the third
#       person, why the mandate needs human approval. Real excerpt from the
#       E00 log that triggered this bug:
#       `.spec/units/0109b-orquestacion-supervisado/evidencia/E00/corrida/
#       1-0-borrador.log`.
#
# Usage: bash .spec/scripts/tests/test_supervised_test_patron_permiso.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
TARGET="$SCRIPT_DIR/supervised-test.sh"

fallos=0

assert_true() {
  local desc="$1"; shift
  if "$@"; then
    printf 'ok - %s\n' "$desc"
  else
    printf 'FAIL - %s\n' "$desc"
    fallos=$((fallos + 1))
  fi
}

# Source everything except the final `main "$@"` dispatch (last line) — pulls
# in `revisar_log`, `LOG_PATRON_PERMISO`, `LOG_MIN_BYTES` without running any
# subcommand. The script only does `cd "$ROOT"` and variable assignments at
# top level, so this is safe.
# Sourced in place (not under /tmp) so its own `source ".../_common.sh"`
# (relative to `${BASH_SOURCE[0]}`) still resolves.
tmp_sourced="$SCRIPT_DIR/.supervised-test.sourced-for-test.sh"
trap 'rm -f "$tmp_sourced"' EXIT
sed '$d' "$TARGET" > "$tmp_sourced"
# shellcheck source=/dev/null
source "$tmp_sourced"

tmpdir="$(mktemp -d)"
trap 'rm -f "$tmp_sourced"; rm -rf "$tmpdir"' EXIT

# --- (a) real signal: CLI's own literal one-line refusal, both languages ---
printf 'El comando requiere tu aprobación manual — no puedo forzarla…\n' \
  > "$tmpdir/atascado-es.log"
revisar_log "$tmpdir/atascado-es.log" "$tmpdir/atascado-es.out" 0 fin 0 0 5
assert_true "detecta atasco real (es): rc != 0" test "$?" -ne 0
grep -q 'patron de permiso pendiente' "$tmpdir/atascado-es.out"
assert_true "detecta atasco real (es): veredicto FALLIDO en destino" \
  grep -q 'FALLIDO' "$tmpdir/atascado-es.out"

printf 'The command requires manual approval — I cant force it.\n' \
  > "$tmpdir/atascado-en.log"
revisar_log "$tmpdir/atascado-en.log" "$tmpdir/atascado-en.out" 0 fin 0 0 5
rc_en=$?
assert_true "detecta atasco real (en): rc != 0" test "$rc_en" -ne 0
assert_true "detecta atasco real (en): veredicto FALLIDO en destino" \
  grep -q 'FALLIDO' "$tmpdir/atascado-en.out"

# --- (b) false positive: real E00 log of a correct `aprobacion-ausente` stop ---
real_log="$REPO_ROOT/.spec/units/0109b-orquestacion-supervisado/evidencia/E00/corrida/1-0-borrador.log"
if [ -f "$real_log" ]; then
  revisar_log "$real_log" "$tmpdir/e00.out" 0 fin 0 0 5
  rc_e00=$?
  assert_true "NO marca como atascado el cierre correcto real de E00 (rc 0)" \
    test "$rc_e00" -eq 0
  assert_true "NO marca como atascado el cierre correcto real de E00 (veredicto OK)" \
    grep -q 'veredicto: OK' "$tmpdir/e00.out"
else
  printf 'skip - real E00 log not present at %s (using synthetic fixture instead)\n' "$real_log"
fi

# Synthetic fixture, independent of evidence that may not survive a `retirar`:
# same shape of false positive — a run that finished normally and mentions
# "aprobación" only to explain, in the third person, why the mandate needs
# human approval — with enough bytes to also clear LOG_MIN_BYTES on its own.
cat > "$tmpdir/falso-positivo.log" <<'EOF'
Resultado: parada por `aprobacion-ausente`.

El mandato está en `estado: borrador`, con `## Aprobación` vacía. No se
acquirió el lock, no se tocó la unidad amparada ni se escribió estado — el
mandato requiere aprobación humana (RS-1, `pri-ia-humano-decide`) antes de
que pueda conducirse ninguna fase.

Siguiente paso: un humano debe registrar la aprobación en `## Aprobación`
del mandato y luego relanzar `/sdd-supervisado` sobre esta misma unidad.
EOF
revisar_log "$tmpdir/falso-positivo.log" "$tmpdir/falso-positivo.out" 0 fin 0 0 5
rc_fp=$?
assert_true "NO marca como atascado el fixture sintético del falso positivo (rc 0)" \
  test "$rc_fp" -eq 0
assert_true "NO marca como atascado el fixture sintético del falso positivo (veredicto OK)" \
  grep -q 'veredicto: OK' "$tmpdir/falso-positivo.out"

if [ "$fallos" -eq 0 ]; then
  printf '\ntest_supervised_test_patron_permiso: OK\n'
  exit 0
else
  printf '\ntest_supervised_test_patron_permiso: %s fallo(s)\n' "$fallos"
  exit 1
fi
