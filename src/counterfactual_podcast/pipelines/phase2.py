"""Phase 2: drain 'To Be Processed', route + rank, top up queue, publish.

Triggered after Jay prunes 'To Be Processed' (Phase 1's output) — he drags any wrong cards
back to the Inbox, then presses 'Sort readables'. Whatever remains in 'To Be Processed' is
processed (a no-op when the list is empty). For each card: enrich (extract + digest) ->
classify into System1/System2/LifeOptim -> binary-insert at its counterfactual-impact rank
and write the desc rank marker. Then top up the Listen Queue and publish the podcast.
Defaults to dry-run.
"""
from __future__ import annotations

import argparse
import asyncio

from .. import config
from ..classify import target_list_id
from ..sort import insert_sorted
from .weekly import _insertion_pos  # shared fractional-position helper


async def run_phase2(client, cache, enricher, classifier, comparator,
                     ensure_queue_fn, publish_fn, *, apply: bool = False, log=None,
                     checkpoint=None, checkpoint_every: int = 10) -> dict:
    trigger_id = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
    cards = client.get_cards(trigger_id)
    if log:
        log.info(f"'To Be Processed' has {len(cards)} cards")

    routed = []
    done = 0  # cards actually routed (apply); used to checkpoint the cache every N cards
    for card in cards:
        feats = await enricher.aenrich(card)
        label = (await classifier.aclassify(feats)).get("label", "system1")
        list_id = target_list_id(label)
        rank = None
        if apply:
            existing = client.get_cards(list_id)
            pos_by_id = {c.id: c.pos for c in existing}
            existing_feats = await enricher.aenrich_many(existing)
            ordered = await insert_sorted(feats, existing_feats, comparator.acompare)
            idx = next(i for i, f in enumerate(ordered) if f.card_id == feats.card_id)
            rank = idx + 1
            client.move_card(card.id, list_id, pos=_insertion_pos(ordered, idx, pos_by_id))
            client.set_rank_marker(card, rank, feats.est_minutes, feats.digest or "")
        routed.append({"card_id": card.id, "label": label, "rank": rank})
        if log:
            log.info(f"  {card.name[:50]} -> {label}" + (f" @#{rank}" if rank else ""))

        # Persist the cache mid-run so a container kill doesn't lose all the expensive
        # extraction/digest/pairwise work — without this, a kill on a big UNCACHED batch
        # leaves R2 untouched and every re-press redoes the same cards and dies again (the
        # documented infinite re-press loop). With it, a re-press resumes near where it died.
        if apply and checkpoint is not None:
            done += 1
            if done % checkpoint_every == 0:
                if log:
                    log.info(f"  [checkpoint] persisting cache after {done} cards")
                maybe = checkpoint()
                if asyncio.iscoroutine(maybe):
                    await maybe

    queue = await ensure_queue_fn() if apply else {"skipped": "dry-run"}
    feed = publish_fn() if apply else {"skipped": "dry-run"}
    return {"processed": len(cards), "routed": routed, "queue": queue, "feed": feed}


async def _build_and_run(apply: bool, log=None) -> dict:
    from ..cache import Cache
    from ..classify import Classifier
    from ..enrich import Enricher
    from ..listen_queue import ensure_listen_queue, episodes_for_queue, make_synth
    from ..llm_compare import Comparator
    from ..rss import publish
    from ..trello import TrelloClient

    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    cache = Cache(config.CACHE_DB)
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)
    classifier = Classifier(cache=cache, profile_doc=profile)
    comparator = Comparator(cache=cache, profile_doc=profile)
    synth = make_synth(cache)

    async def ensure_queue_fn():
        return await ensure_listen_queue(client, cache, enricher, comparator, synth, log=log)

    def publish_fn():
        return publish(episodes_for_queue(client, cache), upload=bool(config.R2_BUCKET))

    # Checkpoint the SQLite cache to R2 every N routed cards (only when R2 is configured —
    # i.e. on the cloud, where the container can be killed mid-run). push_cache_to_r2 is a
    # blocking boto3 upload, so run it off the loop to keep /health responsive during the push.
    checkpoint = None
    if config.R2_BUCKET:
        from ..cache import push_cache_to_r2

        async def checkpoint():
            await asyncio.to_thread(push_cache_to_r2)

    return await run_phase2(client, cache, enricher, classifier, comparator,
                            ensure_queue_fn, publish_fn, apply=apply, log=log,
                            checkpoint=checkpoint)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 2: process 'To Be Processed'")
    ap.add_argument("--apply", action="store_true", help="route/queue/publish (default: dry run)")
    args = ap.parse_args()
    from ..logging_setup import setup_logging
    log = setup_logging("phase2")
    res = asyncio.run(_build_and_run(args.apply, log=log))
    log.info(f"processed={res['processed']} queue={res.get('queue')} feed={res.get('feed')}")


if __name__ == "__main__":
    main()
