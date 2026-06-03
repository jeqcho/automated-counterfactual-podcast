#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
TS=$(date +%Y%m%d-%H%M%S)
uv run python -m counterfactual_podcast.pipelines.phase1 "$@" 2>&1 | tee "logs/phase1-$TS.log"
