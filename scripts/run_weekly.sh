#!/usr/bin/env bash
set -euo pipefail

# Run the weekly pipeline, teeing output to a timestamped log file.
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

mkdir -p logs
TS=$(date +%Y%m%d-%H%M%S)
uv run python -m counterfactual_podcast.pipelines.weekly 2>&1 | tee "logs/weekly-$TS.log"
