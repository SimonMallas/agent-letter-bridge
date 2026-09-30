#!/usr/bin/env bash
# Popup: `alb --status` for one explicitly configured bridge root.
# Read-only: it never loads a token, calls a platform, rings, or touches a letter.
set -uo pipefail

finish() { printf '\nPress any key to close.'; read -r -n 1 _ 2>/dev/null || true; exit 0; }

# Settings live in <plugin config dir>/alb-plugin.env as KEY=VALUE lines. The file is
# parsed, never sourced, and only these two keys are read, so pointing it at the
# bridge's own env file cannot pull a token into this process.
ALB_ROOT='' ALB_BIN=''
conf="${HERDR_PLUGIN_CONFIG_DIR:-}/alb-plugin.env"
if [[ -n "${HERDR_PLUGIN_CONFIG_DIR:-}" && -f "$conf" ]]; then
  while IFS='=' read -r key value || [[ -n "$key" ]]; do
    value="${value%$'\r'}"  # a file saved with CRLF line endings
    case "$key" in
      ALB_ROOT) ALB_ROOT="$value" ;;
      ALB_BIN) ALB_BIN="$value" ;;
    esac
  done < "$conf"
fi
ALB_ROOT="${ALB_ROOT/#\~/$HOME}"
ALB_BIN="${ALB_BIN/#\~/$HOME}"

# Everything the popup prints passes through one filter: paths and the canary line
# come from files, so C0, bare 8-bit C1 (invalid UTF-8, dropped by iconv -c) and
# UTF-8-encoded C1 are removed; valid UTF-8 text is kept.
clean() {
  LC_ALL=C tr -d '\000-\011\013-\037\177' | iconv -f UTF-8 -t UTF-8 -c 2>/dev/null \
    | LC_ALL=C sed $'s/\xc2[\x80-\x9f]//g'
}

report() {
  printf 'Agent Letter Bridge\n\n'
  if [[ -z "$ALB_ROOT" ]]; then
    printf 'No bridge root configured. Add a line like\n  ALB_ROOT=~/.alb\nto %s\n' "${HERDR_PLUGIN_CONFIG_DIR:-the plugin config dir}/alb-plugin.env"
    printf '(the same directory you pass to `alb --root`).\n'
    return
  fi
  if [[ ! -d "$ALB_ROOT" ]]; then
    printf 'ALB_ROOT=%s is not a directory.\n' "$ALB_ROOT"
    return
  fi
  alb="${ALB_BIN:-$(command -v alb 2>/dev/null)}"
  if [[ -z "$alb" || ! -x "$alb" ]]; then
    printf 'alb was not found. Set ALB_BIN=/path/to/alb in %s\n' "${HERDR_PLUGIN_CONFIG_DIR:-the plugin config dir}/alb-plugin.env"
    return
  fi

  printf 'root %s\n\n' "$ALB_ROOT"
  # --status exits 1 when the bridge is not ok; the popup shows its report either way.
  "$alb" --status --root "$ALB_ROOT"
}

report 2>&1 | clean
finish
