"""Re-extract comment-bloated cards with the new comment-stripping and refresh the cache.

The cache was built before extract.py started passing include_comments=False, so some
cached extractions still carry entire comment threads (one SSC post = 551k chars). This
re-extracts the long-tail candidates (cached text > THRESHOLD chars) with the current
extractor and overwrites the cache entry ONLY when the fresh extraction is ok and
meaningfully shorter — a transient fetch failure never drops a card (old entry kept).

Then pushes the refreshed SQLite cache to R2 so the cloud container inherits clean text.

Run:  uv run python scripts/fix_comment_cache.py            # dry run (no cache writes, no R2)
      uv run python scripts/fix_comment_cache.py --apply    # write cache + push to R2
"""
from __future__ import annotations

import sys
from concurrent.futures import ThreadPoolExecutor, as_completed

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache, push_cache_to_r2
from counterfactual_podcast.extract import extract
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.models import Card
from counterfactual_podcast.trello import TrelloClient

THRESHOLD = 25_000   # re-extract anything longer than this (plausible comment bloat)
SHRINK = 0.85        # only replace if new text is < 85% of old (i.e. comments removed)
WORKERS = 6          # low: each worker parses a large HTML page; 50 OOM-killed the process
APPLY = "--apply" in sys.argv


def main():
    log = setup_logging("fix-comment-cache")
    cache = Cache(config.CACHE_DB)
    tc = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)

    # Pull cards (with attachment URLs) from the two queue-source lists.
    cards: dict[str, Card] = {}
    for lid in (config.SYSTEM1_LIST_ID, config.LIFE_OPTIM_LIST_ID):
        for card in tc.get_cards(lid):
            cards[card.id] = card
    log.info(f"pulled {len(cards)} cards from System1 + LifeOptim")

    # Targets: cached, ok, long enough to plausibly contain a comment section.
    targets = []
    for cid, card in cards.items():
        ec = cache.get_extracted(cid)
        if ec and ec.ok and ec.text and len(ec.text) > THRESHOLD:
            targets.append((cid, card, len(ec.text)))
    targets.sort(key=lambda t: -t[2])
    log.info(f"{len(targets)} candidates > {THRESHOLD} chars to re-extract "
             f"({'APPLY' if APPLY else 'DRY RUN'})")

    def reextract(item):
        cid, card, old_len = item
        try:
            ec = extract(card)  # fresh fetch, comment-stripped
        except Exception as e:  # extract never raises, but be safe
            return (cid, card, old_len, None, f"{type(e).__name__}: {e}")
        return (cid, card, old_len, ec, "")

    updated = kept = failed = 0
    total_saved = 0
    done = 0
    n = len(targets)
    with ThreadPoolExecutor(max_workers=WORKERS) as ex:
        futs = [ex.submit(reextract, t) for t in targets]
        for fut in as_completed(futs):
            cid, card, old_len, ec, err = fut.result()
            done += 1
            title = (card.name or cid)[:45]
            if ec is None or not ec.ok or not ec.text:
                failed += 1
                log.info(f"[{done}/{n}]  KEEP (refetch failed) {title}  [{err or 'not ok'}]")
                continue
            new_len = len(ec.text)
            if new_len < old_len * SHRINK:
                total_saved += old_len - new_len
                updated += 1
                log.info(f"[{done}/{n}]  FIX  {old_len:7d} -> {new_len:7d}  ({100*(1-new_len/old_len):2.0f}% off)  {title}")
                if APPLY:
                    cache.put_extracted(ec)
            else:
                kept += 1
                log.info(f"[{done}/{n}]  keep {old_len:7d} ~= {new_len:7d}  (no bloat)   {title}")

    log.info(f"DONE: {updated} fixed, {kept} unchanged, {failed} refetch-failed. "
             f"~{total_saved/1e6:.1f}M chars removed.")

    if APPLY:
        if updated:
            ok = push_cache_to_r2()
            log.info(f"pushed refreshed cache to R2: {ok}")
        else:
            log.info("no updates -> not pushing to R2")
    else:
        log.info("DRY RUN — re-run with --apply to write cache + push to R2")


if __name__ == "__main__":
    main()
