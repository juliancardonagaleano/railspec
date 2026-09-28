#!/usr/bin/env bash
# Pre-commit gate over the generated SDD surfaces (see AGENTS.md §
# Referencias a reglas ya aplicadas por hooks/skills, R6).
#
# Installed as `.git/hooks/pre-commit` (git invokes it with no args, cwd at
# the repo root -- githooks(5)). Can also be run by hand:
#
#   bash .spec/scripts/pre-commit-gate.sh --check
#
# When the staged changes touch any of
#
#   .agents/skills/**
#   .claude/agents/**
#   .claude/skills/**
#   .spec/perfiles.yaml
#   .spec/scripts/**
#   .spec/cambios-menores/**
#
# it runs five deterministic, local, network-free checks and blocks the
# commit if any of them fails:
#
#   python3 scripts/materialize_claude_skills.py --check
#   python3 scripts/materialize_claude_agents.py --check
#   python3 scripts/materialize_claude_commands.py --check
#   python3 .spec/scripts/validate_protocol_drift.py
#   python3 .spec/scripts/generate_minor_changes_index.py --check
#
# The first three verify drift/orphans of their own generated mirror; the
# fourth verifies protocol content (e.g. the default profile declaring no
# Fable, the six phase skills citing the resolver); the fifth verifies the
# minor-changes README index matches its entries. No overlap between the
# five: each covers exactly what it covered before this gate existed, plus
# whatever this gate's own criteria add.
#
# Distinct from `.spec/scripts/pre-push-gate.sh`, which still covers the
# supervised pilot's green marker at push time -- a different moment (commit
# vs. push) and a different check (the pilot suite vs. these three
# generated-surface checks); the two coexist without conflict.
set -euo pipefail

MATERIALIZE_SKILLS="scripts/materialize_claude_skills.py"
MATERIALIZE_AGENTS="scripts/materialize_claude_agents.py"
MATERIALIZE_COMMANDS="scripts/materialize_claude_commands.py"
DRIFT_CHECK=".spec/scripts/validate_protocol_drift.py"
MINOR_CHANGES_INDEX=".spec/scripts/generate_minor_changes_index.py"
# `MODELO-AGENTES.md` y `README.md` se añadieron el 2026-09-27: los chequeos
# 03, 04 y 13 de `validate_protocol_drift.py` existen para vigilar justo esos
# dos archivos, pero un commit que solo los tocara no disparaba el gate.
PROTOCOL_PATTERNS=(
  '^\.agents/skills/'
  '^\.agents/commands/'
  '^\.claude/agents/'
  '^\.claude/skills/'
  '^\.claude/commands/'
  '^\.spec/perfiles\.yaml$'
  '^\.spec/scripts/'
  '^\.spec/cambios-menores/'
  '^\.spec/MODELO-AGENTES\.md$'
  '^\.spec/README\.md$'
)

touches_generated_surfaces() {
  local changed
  changed="$(git diff --cached --name-only 2>/dev/null || true)"
  [ -z "$changed" ] && return 1
  local pattern
  for pattern in "${PROTOCOL_PATTERNS[@]}"; do
    if echo "$changed" | grep -qE "$pattern"; then
      return 0
    fi
  done
  return 1
}

run_check() {
  local label="$1" script_path="$2"
  shift 2
  if [ ! -f "$script_path" ]; then
    echo "pre-commit-gate: $label no existe todavía ($script_path) — se omite." >&2
    return 0
  fi
  if ! "$@"; then
    echo "pre-commit-gate: BLOCK — drift detectado por $label." >&2
    return 1
  fi
  return 0
}

check() {
  if ! touches_generated_surfaces; then
    echo "pre-commit-gate: sin cambios a superficies generadas — PASS (sin gate)."
    return 0
  fi

  local failed=0
  run_check "$MATERIALIZE_SKILLS --check" "$MATERIALIZE_SKILLS" python3 "$MATERIALIZE_SKILLS" --check || failed=1
  run_check "$MATERIALIZE_AGENTS --check" "$MATERIALIZE_AGENTS" python3 "$MATERIALIZE_AGENTS" --check || failed=1
  run_check "$MATERIALIZE_COMMANDS --check" "$MATERIALIZE_COMMANDS" python3 "$MATERIALIZE_COMMANDS" --check || failed=1
  run_check "$DRIFT_CHECK" "$DRIFT_CHECK" python3 "$DRIFT_CHECK" --quiet || failed=1
  run_check "$MINOR_CHANGES_INDEX --check" "$MINOR_CHANGES_INDEX" python3 "$MINOR_CHANGES_INDEX" --check || failed=1

  if [ "$failed" -ne 0 ]; then
    echo "pre-commit-gate: commit bloqueado — corregí el drift antes de commitear." >&2
    return 1
  fi

  echo "pre-commit-gate: PASS — superficies generadas verificadas sin drift."
  return 0
}

if [ "${1:-}" = "--check" ]; then
  check
  exit $?
fi

if [ "${1:-}" = "--install" ]; then
  # `git rev-parse --git-path hooks` resolves to the shared hooks dir of the
  # repo even when invoked from a linked worktree (hooks are not duplicated
  # per worktree) -- unlike a hardcoded `../../` relative symlink, which
  # would point at the wrong tree from one.
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  script_abs="$script_dir/$(basename "${BASH_SOURCE[0]}")"
  hooks_dir="$(git rev-parse --git-path hooks)"
  mkdir -p "$hooks_dir"
  ln -sf "$script_abs" "$hooks_dir/pre-commit"
  chmod +x "$script_abs"
  echo "pre-commit-gate: instalado como $hooks_dir/pre-commit -> $script_abs"
  exit 0
fi

# Modo hook real: git invoca sin args, cwd = raíz del repo (o del worktree).
check
