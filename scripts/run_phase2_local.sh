#!/usr/bin/env bash
# Run Phase 2 on the Mac (full RAM/CPU — can't be Cloudflare-reaped like standard-1) to break
# the container-kill loop on the big UNCACHED System 2 list. Routes the remaining 'To Be
# Processed' cards, tops up the queue, publishes — then pushes the WARMED cache to R2 so every
# future cloud button press is light/fast. See CLAUDE.md "container gets killed mid-run".
set -euo pipefail
cd "$(dirname "$0")/.."

export TTS_ENGINE=google
export GOOGLE_APPLICATION_CREDENTIALS=/Users/jeqcho/Downloads/counterfactual-podcasts-e0f3751c2424.json

echo "[$(date '+%F %T')] backing up local cache..."
cp -f outputs/cache.sqlite3 "outputs/cache.before-local-phase2.$(date +%Y%m%d-%H%M%S).sqlite3" 2>/dev/null || true

echo "[$(date '+%F %T')] phase2 --apply (local, full resources)..."
uv run --extra google python -m counterfactual_podcast.pipelines.phase2 --apply

echo "[$(date '+%F %T')] pushing warmed cache to R2 (state/cache.sqlite3)..."
uv run --extra google python -c "from counterfactual_podcast.cache import push_cache_to_r2; print('pushed:', push_cache_to_r2())"

echo "[$(date '+%F %T')] DONE"
