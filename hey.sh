#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Redirect all usage into the interactive TUI client. Any passed prompt is sent immediately.
exec uv --project "$script_dir" run python "$script_dir/tui_chat.py" "$@"
