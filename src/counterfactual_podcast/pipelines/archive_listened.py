"""Auto-archive Listen Queue cards whose episode you've finished in Apple Podcasts.

Runs on the Mac (it reads Apple Podcasts' local library; see ``apple_podcasts.py``),
on a launchd timer. For each episode of our feed that is basically finished (>= 90%
through, or marked played), it archives the matching card in ``Listen Queue`` and then
republishes the RSS feed so the episode drops off without waiting for Phase 2.

Only cards that are CURRENTLY in Listen Queue are touched. Dry run by default:

    uv run python -m counterfactual_podcast.pipelines.archive_listened            # show plan
    uv run python -m counterfactual_podcast.pipelines.archive_listened --apply    # archive + republish
    uv run python -m counterfactual_podcast.pipelines.archive_listened --inspect  # raw play state
"""
from __future__ import annotations

import argparse
import os
import tempfile

from .. import config
from ..apple_podcasts import DEFAULT_LIBRARY, DEFAULT_THRESHOLD, read_progress


def select_done(queue_cards, progress, threshold: float = DEFAULT_THRESHOLD):
    """Return (card, progress) pairs for queue cards whose episode is done."""
    by_id = {}
    for p in progress:
        # The same GUID can appear twice (e.g. re-subscribed feed); any done copy counts.
        if p.is_done(threshold) or p.card_id not in by_id:
            by_id[p.card_id] = p
    return [(c, by_id[c.id]) for c in queue_cards
            if c.id in by_id and by_id[c.id].is_done(threshold)]


def republish_feed(client, log=None) -> dict:
    """Rebuild + upload rss.xml from the current Listen Queue (audio is already in R2).

    Uses a throwaway copy of the R2 cache for audio durations and titles, and never
    pushes it back, so a concurrent cloud run's cache can't be clobbered."""
    from ..cache import Cache, pull_cache_from_r2
    from ..listen_queue import episodes_for_queue
    from ..rss import publish

    fd, tmp = tempfile.mkstemp(suffix=".sqlite3")
    os.close(fd)
    try:
        if not pull_cache_from_r2(tmp):
            raise RuntimeError("could not pull cache from R2; feed not republished")
        cache = Cache(tmp)
        try:
            feed = publish(episodes_for_queue(client, cache), upload=True)
        finally:
            cache.close()
    finally:
        os.unlink(tmp)
    if log:
        log.info(f"republished feed ({feed['feed_xml'].count('<item>')} episodes)")
    return feed


def run(client, progress, *, threshold: float = DEFAULT_THRESHOLD, apply: bool = False,
        republish=None, log=None) -> dict:
    qid = client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    done = select_done(client.get_cards(qid), progress, threshold)
    for card, p in done:
        how = f"{p.fraction:.0%}" if p.fraction >= threshold else "marked played"
        if apply:
            client.archive_card(card.id)
        if log:
            log.info(f"  {'archived' if apply else 'would archive'} [{how}] {card.name[:70]}")
    republished = False
    if apply and done and republish is not None:
        republish()
        republished = True
    if log:
        log.info(f"archive-listened: {len(done)} finished episode(s) "
                 f"{'archived' if apply else 'would be archived'}")
    return {"archived": [c.id for c, _ in done], "applied": apply,
            "republished": republished}


def main() -> None:
    from ..logging_setup import setup_logging
    from ..trello import TrelloClient

    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="archive cards (default: dry run)")
    ap.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD,
                    help="fraction listened that counts as done (default 0.9)")
    ap.add_argument("--library", default=str(DEFAULT_LIBRARY), help="path to MTLibrary.sqlite")
    ap.add_argument("--no-republish", action="store_true",
                    help="archive only; let the next Phase 2 drop episodes from the feed")
    ap.add_argument("--inspect", action="store_true",
                    help="print raw Apple Podcasts play state for every feed episode and exit")
    args = ap.parse_args()
    log = setup_logging("archive-listened")

    progress = read_progress(config.PODCAST_PREFIX, args.library)
    if args.inspect:
        for p in sorted(progress, key=lambda p: -p.fraction):
            flag = "DONE" if p.is_done(args.threshold) else "    "
            print(f"{flag} {p.fraction:6.1%}  {p.raw}")
        return

    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    republish = None if args.no_republish else (lambda: republish_feed(client, log))
    run(client, progress, threshold=args.threshold, apply=args.apply,
        republish=republish, log=log)


if __name__ == "__main__":
    main()
