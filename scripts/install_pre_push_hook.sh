#!/usr/bin/env bash
# Installer for the pre-push hook of the SDD kit (unit 0135).
#
# Idempotent: re-running on an existing install is a no-op.
# Supports --uninstall to remove only hooks installed by this kit
# (verified by a marker comment in the first line of the hook).
#
# Respects `git config core.hooksPath` if it points to a non-default dir
# (common in multi-repo setups).
#
# Usage:
#   bash scripts/install_pre_push_hook.sh              # install
#   bash scripts/install_pre_push_hook.sh --uninstall # remove (only ours)

set -euo pipefail

ACTION="install"
MARKER="# sdd-kit pre-push"
HOOK_BODY='#!/usr/bin/env bash
# sdd-kit pre-push — installed by scripts/install_pre_push_hook.sh.
# Delegates to .spec/scripts/pre-push-gate.sh with the args git passes.
exec bash "$(git rev-parse --show-toplevel)/.spec/scripts/pre-push-gate.sh" "$@"'

while [[ $# -gt 0 ]]; do
  case "$1" in
    --uninstall) ACTION="uninstall"; shift ;;
    -h|--help)
      sed -n '2,11p' "$0"; exit 0 ;;
    *) echo "unknown arg: $1" >&2; exit 2 ;;
  esac
done

HOOKS_DIR="$(git config --get core.hooksPath 2>/dev/null || true)"
if [[ -z "$HOOKS_DIR" ]]; then
  HOOKS_DIR="$(git rev-parse --absolute-git-dir)/hooks"
fi
HOOK_PATH="$HOOKS_DIR/pre-push"

case "$ACTION" in
  install)
    if [[ -f "$HOOK_PATH" ]] && head -2 "$HOOK_PATH" | grep -qF "$MARKER"; then
      echo "pre-push already installed by kit at $HOOK_PATH" >&2
      exit 0
    fi
    if [[ -f "$HOOK_PATH" ]]; then
      echo "pre-push exists at $HOOK_PATH but is not from this kit" >&2
      echo "respect existing hooks: mv $HOOK_PATH{,.bak} && re-run" >&2
      exit 1
    fi
    mkdir -p "$HOOKS_DIR"
    printf '%s\n' "$HOOK_BODY" > "$HOOK_PATH"
    chmod +x "$HOOK_PATH"
    echo "installed pre-push at $HOOK_PATH"
    ;;
  uninstall)
    if [[ ! -f "$HOOK_PATH" ]]; then
      echo "no pre-push hook at $HOOK_PATH" >&2
      exit 0
    fi
    if ! head -2 "$HOOK_PATH" | grep -qF "$MARKER"; then
      echo "pre-push at $HOOK_PATH is not from this kit; leaving alone" >&2
      exit 1
    fi
    rm "$HOOK_PATH"
    echo "removed pre-push at $HOOK_PATH"
    ;;
esac
