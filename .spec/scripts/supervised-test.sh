#!/usr/bin/env bash
# Evidence-capture helpers for SDD-protocol units.
#
#     .spec/scripts/supervised-test.sh capturar-unidad <unit-dir> <dest> [--seed <dir>] [--list]
#     .spec/scripts/supervised-test.sh --ayuda
#
# `capturar-unidad` snapshots one unit directory — `_estado.yaml`,
# `mandato.md`, `bitacora.md`, `tasks.md`, `paquete-aprobacion.md`
# (whichever exist), a diff of the shared plan-maestro file and, with
# `--seed <dir>`, a `diff-vs-seed.txt` classifying each artifact as
# `igual|cambiado|nuevo|borrado` against a previous capture of the same
# shape. It is the single implementation of "which files, in which order"
# (`pri-gob-fuente-verdad-unica`) that `snapshot_evidence.sh` delegates to
# instead of duplicating the capture. `--list` prints the paths it would
# write, one per line, without writing anything — the dry-run
# `snapshot_evidence.sh --dry-run` needs for byte-identity with a real
# capture. It is idempotent: the destination is wiped first, so neither a
# botched manual edit nor a previous capture's leftovers survive into the
# next one.
#
# `revisar_log`, `$LOG_PATRON_PERMISO` and `$LOG_MIN_BYTES` below are
# internal helpers, not a subcommand: the general defense against a
# subprocess invocation that stops on a permission it cannot request and
# exits 0 answering in a single line — neither an exit-code check nor a
# tree-scope guard would notice that on its own. Exercised directly by
# `.spec/scripts/tests/test_supervised_test_patron_permiso.sh` and
# `.spec/scripts/tests/test_supervised_test_session_limit.sh`, which source
# this file (minus its final dispatch line) to reach them without going
# through a subcommand.

set -uo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT" || exit 1

# shellcheck source=./_common.sh
source "$(dirname "${BASH_SOURCE[0]}")/_common.sh"

# Used by `capturar_en()` below for the diff of the shared plan-maestro file.
PLAN_MAESTRO=".spec/units/_plan-maestro.md"

# Defensa general sobre el log de cada invocación: un subproceso que se
# detiene en un permiso que no puede pedir sale **0** y responde en una
# línea, sin que un chequeo de exit code lo note. Dos umbrales, ambos
# ajustables por entorno para un diagnóstico puntual.
LOG_MIN_BYTES="${LOG_MIN_BYTES:-200}"
# Anclado a la respuesta literal y reproducida del propio CLI (bitacora de
# `0109b`, ALTA 1: con una tool ausente del allowlist, la salida ENTERA de la
# invocación es «El comando requiere tu aprobación manual — no puedo
# forzarla…») — no a la palabra suelta "aprobación"/"permission". La versión
# ancha original (`requiere (tu )?aprobación|requires? (manual )?approval|
# permission`) casaba también con la prosa de cierre de un conductor que
# **terminó bien** y sólo *explica*, en tercera persona, por qué el mandato
# exige aprobación humana (control negativo `aprobacion-ausente`, unidad
# `0114` T21 — ver `.spec/units/0114-preflight-y-rituales-como-scripts/
# bitacora.md` en torno a esa entrada): "…el mandato requiere aprobación
# humana (RS-1, pri-ia-humano-decide)…" casa con `requiere (tu )?aprobación`
# aunque la corrida sea un éxito. La diferencia real entre las dos formas no
# es el sustantivo ("aprobación"/"approval") sino el **verbo de negativa** que
# el CLI antepone cuando de verdad se atasca: "no puedo forzar…"/"can't
# force…". La prosa explicativa de un cierre correcto nunca lo usa — describe
# una regla de gobierno, no una negativa del CLI a actuar. El patrón exige
# ambos fragmentos en la misma línea (grep no cruza líneas), en cualquier
# orden, en los dos idiomas.
LOG_PATRON_PERMISO="${LOG_PATRON_PERMISO:-(requiere (tu )?aprobaci[oó]n( manual)?.*no puedo forzar|no puedo forzar.*requiere (tu )?aprobaci[oó]n( manual)?|requires? (manual )?approval.*\b(cannot|can.?t) force|\b(cannot|can.?t) force.*requires? (manual )?approval)}"

die() { printf 'supervised-test.sh: %s\n' "$1" >&2; exit 1; }

# Artifacts `capturar_en()` copies when present, in order.
CAPTURA_ARCHIVOS="_estado.yaml mandato.md bitacora.md tasks.md paquete-aprobacion.md"

# `capturar_en <dest> [<captura-del-seed>]`.
#
# With the second argument, the capture also gets `diff-vs-seed.txt`: one
# `igual|cambiado|nuevo|borrado <archivo>` line per artifact, comparing this
# capture against a previous one of the same shape. `^igual mandato\.md$`
# demonstrates, byte for byte, that an artifact was not touched — stronger
# than an absence-based `!<regex>` assertion, which a file that was never
# produced in the first place satisfies trivially.
#
# Without the second argument — capturing a baseline with nothing to
# compare against yet — the file is still written, with a single marker
# line that matches no classification above: the file exists either way, so
# a caller checking its content sees a real, content-based mismatch instead
# of a false pass from a missing file.
capturar_en() {
  local dest="${1:?capturar_en: falta <dest>}"
  local seed="${2:-}"
  [ -d "$TEST_UNIT" ] || die "no existe $TEST_UNIT"
  rm -rf "$dest"
  mkdir -p "$dest"
  local f
  for f in $CAPTURA_ARCHIVOS ; do
    [ -f "$TEST_UNIT/$f" ] && cp "$TEST_UNIT/$f" "$dest/$f"
  done
  git diff -U0 -- "$PLAN_MAESTRO" > "$dest/plan-maestro.diff" 2>/dev/null || true
  if [ -n "$seed" ] && [ -d "$seed" ]; then
    : > "$dest/diff-vs-seed.txt"
    for f in $CAPTURA_ARCHIVOS ; do
      if [ -f "$dest/$f" ] && [ -f "$seed/$f" ]; then
        if cmp -s "$dest/$f" "$seed/$f" ; then
          printf 'igual %s\n' "$f" >> "$dest/diff-vs-seed.txt"
        else
          printf 'cambiado %s\n' "$f" >> "$dest/diff-vs-seed.txt"
        fi
      elif [ -f "$dest/$f" ]; then
        printf 'nuevo %s\n' "$f" >> "$dest/diff-vs-seed.txt"
      elif [ -f "$seed/$f" ]; then
        printf 'borrado %s\n' "$f" >> "$dest/diff-vs-seed.txt"
      fi
    done
  else
    printf '# captura del seed: no hay referencia previa con la que comparar\n' \
      > "$dest/diff-vs-seed.txt"
  fi
  date -u +%Y-%m-%dT%H:%M:%SZ > "$dest/capturado-en.txt"
}

# `capturar-unidad <unit-dir> <dest> [--seed <dir>] [--list]`.
#
# Shadows `TEST_UNIT` with a function-local of the same name for the
# duration of this call — bash resolves a called function's free variables
# dynamically, so `capturar_en()` sees `<unit-dir>` without its own
# signature changing.
cmd_capturar_unidad() {
  local unit_dir="${1:?uso: capturar-unidad <unit-dir> <dest> [--seed <dir>] [--list]}"
  local dest="${2:?uso: capturar-unidad <unit-dir> <dest> [--seed <dir>] [--list]}"
  shift 2
  local seed="" listar=""
  while [ "$#" -gt 0 ]; do
    case "$1" in
      --seed) seed="${2:?--seed exige un directorio}"; shift 2 ;;
      --list) listar=1; shift ;;
      *) die "capturar-unidad: argumento desconocido '$1'" ;;
    esac
  done
  [ -d "$unit_dir" ] || die "capturar-unidad: no existe $unit_dir"

  # Shadowed on purpose (see docstring): `capturar_en()` reads the global
  # `$TEST_UNIT`, and this is the one legitimate way to point it at a
  # different unit without changing that function's signature.
  local TEST_UNIT="$unit_dir"

  if [ -n "$listar" ]; then
    local f
    for f in $CAPTURA_ARCHIVOS ; do
      [ -f "$TEST_UNIT/$f" ] && printf '%s/%s\n' "$dest" "$f"
    done
    printf '%s/plan-maestro.diff\n' "$dest"
    printf '%s/diff-vs-seed.txt\n' "$dest"
    printf '%s/capturado-en.txt\n' "$dest"
    return 0
  fi

  capturar_en "$dest" "$seed"
  printf 'capturar-unidad %s -> %s\n' "$unit_dir" "$dest"
}

# Defensa general sobre el log de una invocación: un `claude -p` que se
# detiene en un permiso que no puede pedir sale 0 y contesta en una línea, sin
# que un chequeo de exit code ni una guarda de alcance lo noten por su cuenta.
# Dos chequeos, ninguno de los cuales sabe qué debía demostrar la invocación:
#   * el log no debe casar con `$LOG_PATRON_PERMISO` — las formas de un permiso
#     pendiente, en los dos idiomas;
#   * debe superar `$LOG_MIN_BYTES` — una invocación que de verdad corrió el
#     validador, escribió el mandato y reportó nunca es tan corta.
# El veredicto va a archivo **junto a `exit-code.txt`**: la evidencia dice que se
# miró, en vez de dejarlo para re-derivar.
#
# **El mínimo de bytes sólo vale para una invocación que terminó por su
# cuenta**: en modo `claude -p` la salida se imprime **al terminar el turno**,
# así que una invocación matada a mitad por un vigilante externo deja el log
# vacío *siempre*, haya avanzado mucho o nada. Aplicarle el mínimo dictaminaría
# "no hizo nada" sobre un conductor que de hecho avanzó. El log no puede ser el
# detector de «se colgó»; eso lo decide quien mató la invocación, con los
# parámetros `<motivo>`/`<avances>`/`<quieto-s>` de abajo. Lo que no cambia: el
# patrón de permiso pendiente se busca siempre (un log corto con la frase de
# permiso sigue siendo un fallo, venga como venga la invocación).
#
#   revisar_log <log> <destino> [<rc>] [<motivo>] [<avances>] [<quieto-s>] [<dur-s>]
revisar_log() {
  local log="${1:?revisar_log: falta <log>}" destino="${2:?revisar_log: falta <destino>}"
  local rc_inv="${3:-0}" motivo="${4:-fin}" avances="${5:-0}" quieto="${6:-0}" dur="${7:-0}"
  local rc=0 bytes=0 hallazgo=""
  mkdir -p "$(dirname "$destino")"
  if [ ! -f "$log" ]; then
    printf 'log: %s\nveredicto: FALLIDO — el log no existe\n' "$log" > "$destino"
    return 1
  fi
  bytes="$(wc -c < "$log" | tr -d ' ')"
  hallazgo="$(grep -nEi "$LOG_PATRON_PERMISO" "$log" 2>/dev/null | head -n 5)"
  { printf 'log: %s\n' "$log"
    printf 'patron-permiso: %s\n' "$LOG_PATRON_PERMISO"
    printf 'rc: %s | motivo: %s | duracion: %ss | avances-en-el-arbol: %s | sin-avanzar: %ss\n' \
      "$rc_inv" "$motivo" "$dur" "$avances" "$quieto"
    if [ "$motivo" = "fin" ]; then
      printf 'bytes: %s (mínimo exigido: %s)\n' "$bytes" "$LOG_MIN_BYTES"
    else
      printf 'bytes: %s (mínimo NO exigido: la invocación fue matada y en modo `-p` la salida sólo se imprime al terminar el turno)\n' \
        "$bytes"
    fi
    if [ -n "$hallazgo" ]; then
      printf 'veredicto: FALLIDO — el log casa con el patrón de permiso pendiente:\n%s\n' "$hallazgo"
    elif [ "$motivo" != "fin" ]; then
      if [ "$avances" -gt 0 ]; then
        printf 'veredicto: NO-CONCLUYENTE-POR-EL-LOG — invocación matada (%s); el árbol sí avanzó (%s cambios observados, %ss sin avanzar al morir). El fallo lo tipifica el rc, no el log.\n' \
          "$motivo" "$avances" "$quieto"
      else
        printf 'veredicto: NO-CONCLUYENTE-POR-EL-LOG — invocación matada (%s) y el árbol no avanzó ni una vez en %ss. El fallo lo tipifica el rc, no el log.\n' \
          "$motivo" "$dur"
      fi
    elif [ "$bytes" -lt "$LOG_MIN_BYTES" ]; then
      # `claude -p` buffera su stdout y lo emite al cerrar el turno: cuando el
      # límite de sesión golpea a mitad del trabajo (`<avances>` mide cambios
      # en el árbol, no el log), el log queda pequeño aunque el árbol haya
      # cambiado 18 veces. El caso "no hizo nada" exige
      # **además** que no haya un solo avance — un avance > 0 con mensaje de
      # límite en el log es un corte de sesión, no inacción, y son las
      # aserciones del fixture (gates en `_estado.yaml`, `tasks.md` nuevo,
      # `## Ancla de aditividad` presente) las que verifican el trabajo real.
      if [ "$avances" -gt 0 ] && grep -qF 'session limit' "$log" 2>/dev/null ; then
        printf 'veredicto: OK — límite de sesión cerró la invocación tras %s avances (el log pequeño es buffering de `-p`, no inacción); las aserciones del fixture verifican el resultado sobre el árbol\n' \
          "$avances"
      else
        printf 'veredicto: FALLIDO — la invocación terminó por su cuenta (rc %s) y aun así el log no llega al tamaño mínimo plausible; no hizo nada\n' \
          "$rc_inv"
      fi
    else
      printf 'veredicto: OK — sin patrón de permiso pendiente y por encima del mínimo\n'
    fi
  } > "$destino"
  [ -n "$hallazgo" ] && rc=1
  # El mínimo sólo reprueba a quien terminó por su cuenta **sin haber movido
  # nada**: el corte por límite de sesión (avances > 0 + mensaje de límite en
  # el log) no es inacción — `claude -p` buffera stdout y no lo emite al
  # terminar abruptamente. Lo tipifican las aserciones del fixture, no el log.
  if [ "$motivo" = "fin" ] && [ "$bytes" -lt "$LOG_MIN_BYTES" ]; then
    if [ "$avances" -gt 0 ] && grep -qF 'session limit' "$log" 2>/dev/null ; then
      :
    else
      rc=1
    fi
  fi
  return "$rc"
}

cmd_ayuda() {
  awk 'NR>1 && /^#/ {sub(/^# ?/, ""); print; next} NR>1 {exit}' "${BASH_SOURCE[0]}"
}

main() {
  local sub="${1:-}"; shift || true
  case "$sub" in
    capturar-unidad) cmd_capturar_unidad "$@" ;;
    --ayuda|-h|--help|ayuda) cmd_ayuda ;;
    *) die "subcomando desconocido '$sub' (usar: capturar-unidad|--ayuda)" ;;
  esac
}

main "$@"
