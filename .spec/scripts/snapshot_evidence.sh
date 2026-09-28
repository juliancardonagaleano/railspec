#!/usr/bin/env bash
# Ritual: evidence snapshot (unit 0114-preflight-y-rituales-como-scripts, G2).
#
#     snapshot_evidence.sh <unit-dir> <dest> [--seed <dir>] [--dry-run]
#
# Delegates **all** of the capture to `supervised-test.sh capturar-unidad` — the
# same function (`capturar_en()`) the pilot uses for its own evidence
# (`pri-gob-fuente-verdad-unica`, CA-14: no second implementation of "which
# files, in which order"). This script does not copy a single file itself.
#
# `--dry-run`: `capturar-unidad --list`, sorted — the paths it would write,
# without writing. Real run: the capture, then `find <dest> -type f | sort` —
# the **same** sort as the dry-run listing, so the two texts are byte-identical
# (CA-11a) whenever the capture matches what `--list` predicted.
#
# The freshness marker `capturar_en()` writes at the end of every capture
# (read by `evidencia_reciente()` of `_common.sh`) is named **only** in
# `supervised-test.sh` — this script never writes it, never names its filename
# as a literal, and its content plays no role here (CA-14).
#
# Exit codes: 0 ok, 1 the unit directory does not exist, 2 internal error.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
cd "$ROOT" || exit 2

SUPERVISED_TEST="$SCRIPT_DIR/supervised-test.sh"

fail_interno() {
  printf 'snapshot_evidence.sh: internal-error: %s\n' "$1" >&2
  exit 2
}
trap 'fail_interno "comando fallido (rc $?)"' ERR

if [ "$#" -lt 2 ]; then
  printf 'uso: snapshot_evidence.sh <unit-dir> <dest> [--seed <dir>] [--dry-run]\n' >&2
  exit 2
fi

unit_dir="$1"
dest="$2"
shift 2

seed=""
dry_run=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --seed)
      [ "$#" -ge 2 ] || fail_interno "--seed exige un directorio"
      seed="$2"; shift 2 ;;
    --dry-run) dry_run=1; shift ;;
    *) fail_interno "argumento desconocido '$1'" ;;
  esac
done

# Precondition, checked **before** the ERR trap can turn it into a generic
# internal-error: an absent unit is exit 1, documented, not exit 2.
trap - ERR
if [ ! -d "$unit_dir" ]; then
  printf 'snapshot_evidence.sh: no existe %s\n' "$unit_dir" >&2
  exit 1
fi
trap 'fail_interno "comando fallido (rc $?)"' ERR

if [ -n "$dry_run" ]; then
  bash "$SUPERVISED_TEST" capturar-unidad "$unit_dir" "$dest" --list | sort
  exit 0
fi

if [ -n "$seed" ]; then
  bash "$SUPERVISED_TEST" capturar-unidad "$unit_dir" "$dest" --seed "$seed" > /dev/null
else
  bash "$SUPERVISED_TEST" capturar-unidad "$unit_dir" "$dest" > /dev/null
fi
find "$dest" -type f | sort
