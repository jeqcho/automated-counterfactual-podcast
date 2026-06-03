#!/usr/bin/env bash
# Local webhook server for the Trello Butler phase buttons.
# Needs TRIGGER_TOKEN in .env. Expose to Trello via Cloudflare Tunnel (see
# reports/trigger-setup.md). Runs the REAL phases with --apply on each button press.
set -euo pipefail
cd "$(dirname "$0")/.."
PORT="${PORT:-8787}"
exec uv run uvicorn counterfactual_podcast.server:app --host 127.0.0.1 --port "$PORT"
