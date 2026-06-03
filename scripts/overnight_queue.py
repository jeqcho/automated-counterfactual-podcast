"""Overnight: build a Listen Queue from the (now-sorted) System 1 + Life Optim lists.

Chains AFTER the big sort finishes (waits for BIGSORT_DONE in its log) so they don't
fight over the cache / rate limits. Sources = System 1 + Life Optimization only — never
touches To Be Processed or System 2. Uses Kokoro (local) for TTS tonight; publishes to
R2 under the stable PODCAST_PREFIX so Jay gets a subscribable feed URL by morning.

Leverages the in-place sort: takes the top cards from each already-sorted source list,
does a small cross-list merge to interleave by impact, then synthesizes until the target
hours (default 3) is hit.
"""
from __future__ import annotations

import asyncio
import os
import sys
import time

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.enrich import Enricher
from counterfactual_podcast.listen_queue import episodes_for_queue
from counterfactual_podcast.llm_compare import Comparator
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.rss import publish
from counterfactual_podcast.sort import merge_sort
from counterfactual_podcast.trello import TrelloClient

TARGET_HOURS = float(sys.argv[1]) if len(sys.argv) > 1 else 3.0
BIGSORT_LOG = sys.argv[2] if len(sys.argv) > 2 else None
TOP_S1 = 30   # top-N from sorted System 1
TOP_LO = 15   # top-N from sorted Life Optim


async def wait_for_bigsort(log):
    if not BIGSORT_LOG or not os.path.exists(BIGSORT_LOG):
        log.info("no bigsort log given — proceeding immediately")
        return
    log.info(f"waiting for big sort to finish ({BIGSORT_LOG})…")
    while True:
        txt = open(BIGSORT_LOG).read() if os.path.exists(BIGSORT_LOG) else ""
        if "BIGSORT_DONE" in txt:
            log.info("big sort done — starting queue build")
            return
        await asyncio.sleep(30)


async def main():
    log = setup_logging("overnight-queue")
    await wait_for_bigsort(log)

    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    cache = Cache(config.CACHE_DB)
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)
    comparator = Comparator(cache=cache, profile_doc=profile)
    from counterfactual_podcast.listen_queue import make_synth
    synth = make_synth(cache)  # Kokoro (config.TTS_ENGINE default)

    # top of each ALREADY-SORTED source list (board order == impact order now)
    s1 = client.get_cards(config.SYSTEM1_LIST_ID)[:TOP_S1]
    lo = client.get_cards(config.LIFE_OPTIM_LIST_ID)[:TOP_LO]
    candidates = s1 + lo
    log.info(f"candidates: {len(s1)} System1 + {len(lo)} LifeOptim = {len(candidates)}")

    feats = [f for f in await enricher.aenrich_many(candidates) if f.ok]
    log.info(f"{len(feats)} extractable; ranking the combined head…")
    ranked = await merge_sort(feats, comparator.acompare)   # ~45 cards, fast (mostly cached)

    queue_id = client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    target_sec = TARGET_HOURS * 3600
    total = 0.0
    added = []
    for f in ranked:
        if total >= target_sec:
            break
        log.info(f"  synth ({f.est_minutes}min read) {f.title[:50]}…")
        asset = await synth(f)
        if asset is None:
            continue
        client.move_card(f.card_id, queue_id, pos="bottom")  # same board, no idBoard
        added.append(f)
        total += asset.seconds
        log.info(f"    +{asset.seconds/60:.1f}min audio  (queue now {total/3600:.2f}h)")

    # order the queue by impact (top = listen next)
    for i, f in enumerate(added):
        client.set_card_position(f.card_id, (i + 1) * 1000.0)

    eps = episodes_for_queue(client, cache)
    feed = publish(eps, prefix=(config.PODCAST_PREFIX or None), upload=bool(config.R2_BUCKET))
    log.info(f"QUEUE BUILT: {len(added)} episodes, {total/3600:.2f}h audio")
    log.info(f"FEED URL: {feed.get('feed_url')}  (uploaded {feed.get('uploaded')} files)")
    print("OVERNIGHT_QUEUE_DONE", feed.get("feed_url"))


if __name__ == "__main__":
    asyncio.run(main())
