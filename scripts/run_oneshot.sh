#!/usr/bin/env bash
set -euo pipefail

# Run the one-shot sort pipeline, teeing output to a timestamped log file.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs
TS=$(date +%Y%m%d-%H%M%S)
uv run python -m counterfactual_podcast.pipelines.oneshot_sort "$@" 2>&1 | tee "logs/oneshot-$TS.log"
