#!/usr/bin/env bash
# Shared bash helpers for the `.spec/scripts/` validation scripts.
#
# Sourced, never executed directly — the guard below makes `bash _common.sh`
# a no-op instead of running nothing useful and exiting 0 by accident.
#
# Functions:
#   paso12 <dir-units>
#       Every `_estado.yaml` directly under <dir-units>/*/ that declares
#       `modo: supervisado` (quoted or not, trailing comment allowed) must
#       resolve its `mandato` (none of the six reference-resolution codes).
#       Prints the validator's output and returns 1 on the first one that
#       does not; returns 0 if there is nothing to check or every declaring
#       unit resolves.
#
#   comparar_esperado <dir-unidad> <esperado.txt>
#       Runs `validate_mandate.py --unidad <dir-unidad>`, extracts its reported
#       set of codes (`códigos: …` line, sorted, deduplicated) and diffs it
#       against the sorted, non-blank lines of <esperado.txt>. Returns 0 on
#       match, 1 on mismatch (diff printed to stdout), 2 on validator crash.
#
#   assert_line <archivo> <patron>
#       One `archivo|regex` assertion: returns 0 when <patron> matches inside
#       <archivo> with `grep -qE`. A `!`-prefixed pattern is a negative
#       assertion (must NOT match; a missing file satisfies it).
#
#   verificar_aserciones <dir-evidencia> <aserciones.txt>
#       Every non-blank `archivo|regex` line of <aserciones.txt> applied with
#       `assert_line` against <dir-evidencia>/<archivo>. Prints the first line
#       that fails and returns 1; returns 0 when all of them hold. One shared
#       implementation of the assertion dialect (`pri-gob-fuente-verdad-unica`).
#
# No external dependencies beyond `bash`, `git`, `python3`, coreutils.

VALIDATOR="${VALIDATOR:-.spec/scripts/validate_mandate.py}"

paso12() {
  local units_dir="${1:?paso12: falta <dir-units>}"
  local re='^[[:space:]]*modo:[[:space:]]*["'"'"']?supervisado["'"'"']?[[:space:]]*(#.*)?$'
  local resolution_codes='unidad-supervisado-sin-mandato|unidad-mandato-vacio|unidad-plan-inexistente|unidad-mandato-inexistente|mandato-forma-invalida|unidad-mandato-doble'
  local base="${units_dir%/}"
  compgen -G "$base"/*/_estado.yaml > /dev/null 2>&1 || return 0
  local f dir out
  for f in "$base"/*/_estado.yaml; do
    grep -qE "$re" "$f" || continue
    dir="$(dirname "$f")"
    out="$(python3 "$VALIDATOR" --unidad "$dir" 2>&1)"
    if printf '%s\n' "$out" | grep -qE "$resolution_codes"; then
      printf '%s\n' "$out" >&2
      printf 'paso12: %s declara modo: supervisado pero su mandato no resuelve\n' "$dir" >&2
      return 1
    fi
  done
  return 0
}

comparar_esperado() {
  local dir="${1:?comparar_esperado: falta <dir-unidad>}"
  local esperado_file="${2:?comparar_esperado: falta <esperado.txt>}"
  local tmp output exit_code
  tmp="$(mktemp -d)"
  output="$(python3 "$VALIDATOR" --unidad "$dir" 2>"$tmp/stderr.txt")"
  exit_code=$?
  if ! { [ "$exit_code" -eq 0 ] || [ "$exit_code" -eq 1 ]; }; then
    cat "$tmp/stderr.txt" >&2
    echo "comparar_esperado: $dir — el validador terminó con exit $exit_code" >&2
    rm -rf "$tmp"
    return 2
  fi
  if grep -q 'Traceback (most recent call last)' "$tmp/stderr.txt" 2>/dev/null; then
    cat "$tmp/stderr.txt" >&2
    echo "comparar_esperado: $dir — el validador lanzó un traceback" >&2
    rm -rf "$tmp"
    return 2
  fi
  printf '%s\n' "$output" \
    | sed -n 's/^códigos: //p' \
    | tr ' ' '\n' \
    | sed '/^$/d' \
    | sort -u > "$tmp/obtenido.txt"
  [ -f "$esperado_file" ] || { echo "comparar_esperado: no existe $esperado_file" >&2; rm -rf "$tmp"; return 2; }
  sort -u "$esperado_file" | sed '/^$/d' > "$tmp/esperado.txt"
  local rc=0
  diff -u "$tmp/esperado.txt" "$tmp/obtenido.txt" || rc=1
  rm -rf "$tmp"
  return "$rc"
}

assert_line() {
  # `archivo|regex` (or `archivo|!regex` for a negative assertion).
  local file="${1:?assert_line: falta <archivo>}" pattern="${2:?assert_line: falta <patron>}" negate=0
  if [[ "$pattern" == '!'* ]] ; then
    negate=1
    pattern="${pattern#!}"
  fi
  if [ ! -f "$file" ] ; then
    [ "$negate" -eq 1 ] && return 0
    return 1
  fi
  if grep -qE "$pattern" "$file" ; then
    [ "$negate" -eq 1 ] && return 1
    return 0
  else
    [ "$negate" -eq 1 ] && return 0
    return 1
  fi
}

verificar_aserciones() {
  local evid="${1:?verificar_aserciones: falta <dir-evidencia>}"
  local aserciones="${2:?verificar_aserciones: falta <aserciones.txt>}"
  [ -f "$aserciones" ] || { echo "verificar_aserciones: no existe $aserciones" >&2; return 2; }
  [ -d "$evid" ] || { echo "verificar_aserciones: no existe $evid" >&2; return 1; }
  local archivo patron rc=0
  while IFS='|' read -r archivo patron ; do
    [ -n "$archivo" ] || continue
    case "$archivo" in \#*) continue ;; esac
    if ! assert_line "$evid/$archivo" "$patron" ; then
      printf 'verificar_aserciones: «%s|%s» no se cumple en %s\n' "$archivo" "$patron" "$evid" >&2
      rc=1
    fi
  done < "$aserciones"
  return "$rc"
}

# Guard: `bash _common.sh` on its own does nothing useful — this file is a
# library, meant to be `source`d by `validate-supervised.sh` and
# `supervised-test.sh`.
if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
  echo "_common.sh es una librería: source-alo, no lo ejecutes directamente." >&2
  exit 2
fi
