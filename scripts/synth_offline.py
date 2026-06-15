"""Pre-synthesize listen-queue audio OFFLINE (CPU only, no network).

For the commute: reads the ranked card ids from the local post-sort snapshots and the
article text from the local SQLite cache, then synthesizes Kokoro audio into the local
cache — no Trello, no Anthropic, no R2. When back online, the publish step assembles the
queue + uploads (fast). Fully resumable: already-synthesized clips are skipped.
"""
from __future__ import annotations

import glob
import json
import os
import sys

from counterfactual_podcast import config
from counterfactual_podcast.audio import synthesize_card
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.tts import get_engine

TARGET_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0


def candidate_ids() -> list[str]:
    """Top ok cards from the sorted Life-Optim + System-1 snapshots (ranked order)."""
    ids: list[str] = []
    for pat, n in [("oneshot_life_optim_*_post.json", 15),
                   ("oneshot_system1_*_post.json", 40)]:
        fs = sorted(glob.glob(str(config.OUTPUTS / pat)), key=os.path.getmtime)
        if not fs:
            continue
        data = json.loads(open(fs[-1]).read())
        ids += [r["card_id"] for r in data if r.get("ok")][:n]
    # de-dup preserving order
    seen = set()
    return [i for i in ids if not (i in seen or seen.add(i))]


def main():
    log = setup_logging("synth-offline")
    target = TARGET_HOURS * 3600
    cache = Cache(config.CACHE_DB)
    engine = get_engine("kokoro")
    ids = candidate_ids()
    log.info(f"{len(ids)} candidates; pre-synthesizing up to {TARGET_HOURS:.1f}h "
             f"(CPU only, NO network)…")

    total = 0.0
    done = 0
    for cid in ids:
        if total >= target:
            break
        a = cache.get_audio(cid)
        if a and os.path.exists(a.path):
            total += a.seconds
            continue
        ec = cache.get_extracted(cid)
        if not ec or not ec.ok:
            continue
        try:
            asset = synthesize_card(cid, ec.text, engine=engine, cache=cache, ok=True)
        except Exception as e:  # never let one bad card stop the batch
            log.info(f"  skip {cid}: {type(e).__name__} {e}")
            continue
        if asset:
            total += asset.seconds
            done += 1
            log.info(f"  +{asset.seconds/60:.1f}min  ({total/3600:.2f}h cached)  {ec.title[:45]}")

    log.info(f"OFFLINE SYNTH DONE: {done} new clips, {total/3600:.2f}h audio cached. "
             f"Assemble + publish when back online.")
    print("SYNTH_OFFLINE_DONE")


if __name__ == "__main__":
    main()
