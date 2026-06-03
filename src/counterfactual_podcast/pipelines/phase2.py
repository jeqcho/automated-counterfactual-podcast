"""Phase 2: drain '▶ Ready to Process', route + rank, top up queue, publish.

Triggered by Jay dragging reviewed cards into the '▶ Ready to Process' list (Phase 2
runs as a scheduled poller — a no-op when that list is empty). For each card: enrich
(extract + digest) -> classify into System1/System2/LifeOptim -> binary-insert at its
counterfactual-impact rank and write the desc rank marker. Then top up the Listen
Queue and publish the podcast. Defaults to dry-run.
"""
from __future__ import annotations

import argparse
import asyncio

from .. import config
from ..classify import target_list_id
from ..sort import insert_sorted
from .weekly import _insertion_pos  # shared fractional-position helper


async def run_phase2(client, cache, enricher, classifier, comparator,
                     ensure_queue_fn, publish_fn, *, apply: bool = False, log=None) -> dict:
    trigger_id = client.ensure_list(config.READY_TO_PROCESS_LIST_NAME)
    cards = client.get_cards(trigger_id)
    if log:
        log.info(f"'▶ Ready to Process' has {len(cards)} cards")

    routed = []
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
            client.set_rank_marker(card, rank, feats.est_minutes, (feats.digest or "")[:80])
        routed.append({"card_id": card.id, "label": label, "rank": rank})
        if log:
            log.info(f"  {card.name[:50]} -> {label}" + (f" @#{rank}" if rank else ""))

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

    return await run_phase2(client, cache, enricher, classifier, comparator,
                            ensure_queue_fn, publish_fn, apply=apply, log=log)


def main() -> None:
    ap = argparse.ArgumentParser(description="Phase 2: process '▶ Ready to Process'")
    ap.add_argument("--apply", action="store_true", help="route/queue/publish (default: dry run)")
    args = ap.parse_args()
    from ..logging_setup import setup_logging
    log = setup_logging("phase2")
    res = asyncio.run(_build_and_run(args.apply, log=log))
    log.info(f"processed={res['processed']} queue={res.get('queue')} feed={res.get('feed')}")


if __name__ == "__main__":
    main()
