#!/usr/bin/env bash
# Pre-push gate over the supervised protocol (unit 0114, scope addition
# 2026-09-20, from comparing the /insights report regenerated that day
# against the original that started this mandate).
#
# Installed as `.git/hooks/pre-push` (git invokes it with `<remote> <url>` as
# args and feeds `<local ref> <local sha1> <remote ref> <remote sha1>` lines
# on stdin — see githooks(5)). Can also be run by hand:
#
#   bash .spec/scripts/pre-push-gate.sh --check <since-commit> [<until-commit>]
#
# What it does NOT do: it does not run the protocol's regression suite
# itself on every push — that cost belongs to the person changing the
# protocol, not to every push after them. Instead, this script checks that
# someone already ran the suite green on an ancestor of what is being
# pushed, whenever the push touches a protocol path:
#
#   .agents/skills/**
#   .spec/scripts/validate_mandate.py
#   .spec/_plantillas/**
#
# Registering a green run (after it actually passed):
#
#   python3 -m pytest .spec/scripts/tests && git rev-parse HEAD > .spec/.pilot-verde
#
# `.spec/.pilot-verde` holds one commit hash: the last commit the full suite
# was confirmed green against. This script refuses the push unless that
# marker exists and names a commit that is an ancestor of what is being
# pushed (so a stale marker from before the protocol change does not count).
#
# Drift detector (unit 0158, 2026-09-23): every push that touches protocol paths
# must also pass `validate_protocol_drift.py` — the guard that detects silent
# reverts of 0117-D1..D3 / 0117-D6 / 0117-D7 (Fable/Opus re-appearing as
# default; tier budget reverting; settings.json losing sonnet/low). Catches
# the failure mode of commit `a85e10f1` (2026-09-22, opencode@siste.local)
# which silently reverted 9 subagent frontmatters + MODELO-AGENTES + README
# without anyone noticing for ~24h.
set -euo pipefail

# Git ya invoca los hooks con CWD en la raíz del repo (githooks(5)) — no se
# fuerza un `cd` al repo del propio script: eso rompería el uso desde un
# checkout distinto (tests, worktrees) sin ganar nada en el caso real.
MARKER=".spec/.pilot-verde"
DRIFT_CHECK=".spec/scripts/validate_protocol_drift.py"
# Las tres primeras rutas son las que el gate cubría originalmente. Las cuatro
# siguientes se añadieron el 2026-09-27: son exactamente las que tocó el commit
# `a85e10f1` que este gate dice atrapar —9 frontmatters de subagente,
# MODELO-AGENTES y README— y ninguna estaba en la lista, así que un revert
# idéntico al de referencia pasaba el push sin que `validate_protocol_drift.py`
# llegara a ejecutarse. `.spec/perfiles.yaml` entra por el chequeo 11, que lee
# ese archivo directamente.
PROTOCOL_PATTERNS=(
  '^\.agents/skills/'
  '^\.spec/scripts/validate_mandate\.py$'
  '^\.spec/_plantillas/'
  '^\.claude/agents/'
  '^\.spec/MODELO-AGENTES\.md$'
  '^\.spec/README\.md$'
  '^\.spec/perfiles\.yaml$'
)

touches_protocol() {
  local range="$1"
  local changed
  changed="$(git diff --name-only "$range" 2>/dev/null || true)"
  [ -z "$changed" ] && return 1
  local pattern
  for pattern in "${PROTOCOL_PATTERNS[@]}"; do
    if echo "$changed" | grep -qE "$pattern"; then
      return 0
    fi
  done
  return 1
}

check_range() {
  local since="$1" until="${2:-HEAD}"
  local range="${since}..${until}"

  if ! touches_protocol "$range"; then
    echo "pre-push-gate: sin cambios a rutas del protocolo en ${range} — PASS (sin gate de piloto)."
    return 0
  fi

  # Drift detector (CA-14 de la unidad 0158) — corre antes del chequeo del
  # marcador para fallar rápido si el protocolo tiene drift silencioso.
  if [ -f "$DRIFT_CHECK" ]; then
    if ! python3 "$DRIFT_CHECK" --quiet; then
      echo "pre-push-gate: BLOCK — drift del protocolo SDD detectado por $DRIFT_CHECK." >&2
      echo "  Corré \`python3 $DRIFT_CHECK\` para ver los chequeos fallidos y arreglalos antes de pushear." >&2
      return 1
    fi
    echo "pre-push-gate: drift del protocolo SDD verificado — ver detalle de $DRIFT_CHECK arriba."
  fi

  if [ ! -f "$MARKER" ]; then
    echo "pre-push-gate: BLOCK — ${range} toca el protocolo supervisado y no existe ${MARKER}." >&2
    echo "  Corré la suite completa y registrá el marcador antes de pushear:" >&2
    echo "  python3 -m pytest .spec/scripts/tests && git rev-parse HEAD > ${MARKER}" >&2
    return 1
  fi

  local marker_commit
  marker_commit="$(tr -d '[:space:]' < "$MARKER")"
  if ! git cat-file -e "${marker_commit}^{commit}" 2>/dev/null; then
    echo "pre-push-gate: BLOCK — ${MARKER} cita '${marker_commit}', que no es un commit alcanzable." >&2
    return 1
  fi
  if ! git merge-base --is-ancestor "$marker_commit" "$until" 2>/dev/null; then
    echo "pre-push-gate: BLOCK — el marcador de piloto verde (${marker_commit}) no es ancestro de ${until}." >&2
    echo "  El protocolo cambió después de la última corrida verde registrada. Volvé a correr la suite:" >&2
    echo "  python3 -m pytest .spec/scripts/tests && git rev-parse HEAD > ${MARKER}" >&2
    return 1
  fi

  echo "pre-push-gate: PASS — ${range} toca el protocolo, marcador de piloto verde (${marker_commit}) es ancestro de ${until}."
  return 0
}

if [ "${1:-}" = "--check" ]; then
  check_range "${2:?uso: --check <since-commit> [<until-commit>]}" "${3:-HEAD}"
  exit $?
fi

if [ "${1:-}" = "--install" ]; then
  hook=".git/hooks/pre-push"
  mkdir -p "$(dirname "$hook")"
  ln -sf "../../.spec/scripts/pre-push-gate.sh" "$hook"
  chmod +x "$hook"
  echo "pre-push-gate: instalado como $hook (symlink)."
  exit 0
fi

# Modo hook real: git pasa <remote> <url> como args y feeds las líneas por stdin.
status=0
while read -r local_ref local_sha remote_ref remote_sha; do
  [ -z "${local_sha:-}" ] && continue
  # Push nuevo (rama no existía en el remoto): remote_sha es todo ceros.
  if [ "$remote_sha" = "0000000000000000000000000000000000000000" ]; then
    since="$(git rev-list --max-parents=0 "$local_sha" | tail -1)"
  else
    since="$remote_sha"
  fi
  if ! check_range "$since" "$local_sha"; then
    status=1
  fi
done
exit $status
