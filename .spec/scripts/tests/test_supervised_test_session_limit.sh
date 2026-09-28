#!/usr/bin/env bash
# Regression test for the `session limit` carve-out in `revisar_log`
# (`supervised-test.sh`, unidad 0114). Cubre la corrección del falso negativo
# que rechazaba toda invocación de `claude -p` interrumpida por límite de
# sesión aunque el árbol hubiera cambiado 18 veces: el log mínimo exigible
# (200 bytes) era el de una corrida que terminó por su cuenta con contenido,
# pero `claude -p` buffera stdout y sólo lo emite al cerrar el turno —
# cuando el límite golpea a mitad del trabajo, el stdout nunca se vacea y
# queda en ≤ 100 bytes con sólo el mensaje del CLI.
#
# El bug: `revisar_log` rechazaba con `FALLIDO — no hizo nada` cualquier
# corrida con `motivo=fin && bytes<LOG_MIN_BYTES`, sin mirar si el árbol
# había cambiado (`avances-en-el-arbol`). El piloto de E00 (0109b) llevaba
# tres corridas topándose con esto en `E00 [2] 1-cierre-con-pendientes`
# (913 s con 18 avances verificados por `digesto_avance`, log de 65 bytes),
# frenando T21 antes de llegar a las aserciones del fixture.
#
# El fix: añadir una excepción — `motivo=fin && bytes<LOG_MIN_BYTES` sólo
# reprueba si **además** no hay un solo avance y/o no hay mensaje de
# límite de sesión en el log. Con `avances > 0 && 'session limit' en log`,
# el veredicto es OK y son las aserciones del fixture (gates en
# `_estado.yaml`, `tasks.md` nuevo, `## Ancla de aditividad` presente) las
# que verifican el trabajo real — están medidas sobre los archivos, no
# sobre el log.
#
# Cubre exactamente los tres casos que el bug cruzaba:
#   (a) `session limit` + avances > 0 → OK — antes FALLIDO por log pequeño
#   (b) `session limit` SIN avances (la invocación abortó antes de mover
#       nada) → FALLIDO, mismo veredicto que "no hizo nada"
#   (c) corrida `fin` legítima con log pequeño y sin avances (no session
#       limit) → FALLIDO — la regresión que el fix no debe tocar
#
# Usage: bash .spec/scripts/tests/test_supervised_test_session_limit.sh

set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
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

# Misma técnica que `test_supervised_test_patron_permiso.sh`: sourcea el
# script quitándole la última línea (el dispatch `main "$@"`) para tener
# acceso a `revisar_log`, `LOG_MIN_BYTES`, `LOG_PATRON_PERMISO` sin correr
# ningún subcomando.
tmp_sourced="$SCRIPT_DIR/.supervised-test.sourced-for-test.sh"
trap 'rm -f "$tmp_sourced"' EXIT
sed '$d' "$TARGET" > "$tmp_sourced"
# shellcheck source=/dev/null
source "$tmp_sourced"

tmpdir="$(mktemp -d)"
trap 'rm -f "$tmp_sourced"; rm -rf "$tmpdir"' EXIT

# --- (a) session limit + avances > 0 → OK -----------------------------------
# Réplica literal del log observado en `E00[2]1-cierre-con-pendientes` del
# piloto de 0109b (corrida sello `bda49df0`, 2026-09-21T16:25:55Z): 65 bytes,
# sólo `"You've hit your session limit"`. 18 avances reales en el árbol,
# verificados por `digesto_avance`. Antes del fix: FALLIDO. Después: OK.
printf "You've hit your session limit · resets 12:20pm (America/Bogota)\n" \
  > "$tmpdir/session-limit-con-avances.log"
revisar_log "$tmpdir/session-limit-con-avances.log" "$tmpdir/session-limit-con-avances.out" \
  1 fin 18 178 913
rc_a=$?
assert_true "(a) session limit + 18 avances: rc 0 (antes del fix era 1)" \
  test "$rc_a" -eq 0
assert_true "(a) session limit + 18 avances: veredicto OK" \
  grep -q 'veredicto: OK' "$tmpdir/session-limit-con-avances.out"
assert_true "(a) session limit + 18 avances: el veredicto menciona el corte por límite" \
  grep -q 'límite de sesión' "$tmpdir/session-limit-con-avances.out"
assert_true "(a) session limit + 18 avances: NO dice 'no hizo nada'" \
  test "$(grep -c 'no hizo nada' "$tmpdir/session-limit-con-avances.out" || true)" -eq 0

# --- (b) session limit SIN avances → FALLIDO -------------------------------
# Mismo mensaje de límite, pero 0 avances: la invocación abortó antes de
# mover nada. Esto **debe** seguir siendo FALLIDO — el conductor no
# llegó a hacer nada, así que las aserciones del fixture no tendrían
# sobre qué verificar.
printf "You've hit your session limit · resets 12:20pm (America/Bogota)\n" \
  > "$tmpdir/session-limit-sin-avances.log"
revisar_log "$tmpdir/session-limit-sin-avances.log" "$tmpdir/session-limit-sin-avances.out" \
  1 fin 0 5 30
rc_b=$?
assert_true "(b) session limit + 0 avances: rc 1 (sigue siendo FALLIDO)" \
  test "$rc_b" -ne 0
assert_true "(b) session limit + 0 avances: veredicto FALLIDO" \
  grep -q 'veredicto: FALLIDO' "$tmpdir/session-limit-sin-avances.out"
assert_true "(b) session limit + 0 avances: menciona 'no hizo nada'" \
  grep -q 'no hizo nada' "$tmpdir/session-limit-sin-avances.out"

# --- (c) fin legítimo con log pequeño y sin avances → FALLIDO ---------------
# Regresión que el fix NO debe tocar: una corrida `fin` genuina sin hacer
# nada (log pequeño, 0 avances, sin mensaje de límite) tiene que seguir
# cayendo como FALLIDO — esto es el caso original del byte-check.
printf 'thinking text sin trabajo real\n' > "$tmpdir/fin-sin-trabajo.log"
revisar_log "$tmpdir/fin-sin-trabajo.log" "$tmpdir/fin-sin-trabajo.out" \
  0 fin 0 0 5
rc_c=$?
assert_true "(c) fin legítimo sin trabajo: rc 1 (no regresión)" \
  test "$rc_c" -ne 0
assert_true "(c) fin legítimo sin trabajo: veredicto FALLIDO" \
  grep -q 'veredicto: FALLIDO' "$tmpdir/fin-sin-trabajo.out"
assert_true "(c) fin legítimo sin trabajo: menciona 'no hizo nada'" \
  grep -q 'no hizo nada' "$tmpdir/fin-sin-trabajo.out"

# --- (d) la corrección es compatible con el patrón de permiso --------------
# Si el log menciona tanto `session limit` como un patrón de permiso
# pendiente, gana el patrón de permiso (FALLIDO). El veredicto de la
# excepción sólo aplica cuando **no** hay hallazgo de permiso.
printf "You've hit your session limit.\nEl comando requiere tu aprobación manual — no puedo forzarla…\n" \
  > "$tmpdir/session-limit-con-permiso.log"
revisar_log "$tmpdir/session-limit-con-permiso.log" "$tmpdir/session-limit-con-permiso.out" \
  1 fin 18 178 913
rc_d=$?
assert_true "(d) session limit + permiso pendiente: rc 1 (permiso gana)" \
  test "$rc_d" -ne 0
assert_true "(d) session limit + permiso pendiente: veredicto FALLIDO por permiso" \
  grep -q 'patrón de permiso pendiente' "$tmpdir/session-limit-con-permiso.out"

if [ "$fallos" -eq 0 ]; then
  printf '\ntest_supervised_test_session_limit: OK\n'
  exit 0
else
  printf '\ntest_supervised_test_session_limit: %s fallo(s)\n' "$fallos"
  exit 1
fi