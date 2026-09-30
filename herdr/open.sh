#!/usr/bin/env bash
# Action: open the Letter Bridge status popup.
set -euo pipefail
exec "${HERDR_BIN_PATH:-herdr}" plugin pane open --plugin "${HERDR_PLUGIN_ID:-agent-letter-bridge}" --entrypoint status
