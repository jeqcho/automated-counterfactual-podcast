"""Build / top up the Listen Queue to a soft floor of ~20 hours of audio.

Sources = System 1 + Life Optimization only (System 2 is excluded — deep material
needs focused reading, not passive listening). Only TTS-able (ok=True) cards are
eligible. Candidates are ranked by counterfactual impact (pairwise) and added until
the queue reaches the target hours OR the clean pool is exhausted (soft floor — never
loops forever). The whole queue is kept ordered by impact so Jay listens top-first.
"""
from __future__ import annotations

from . import config
from .models import AudioAsset, CardFeatures
from .sort import merge_sort


def episodes_for_queue(client, cache, queue_id: str | None = None):
    """Build ordered podcast episodes from the Listen Queue's cards + cached audio."""
    from .rss import QueueEpisode
    qid = queue_id or client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    eps = []
    for c in client.get_cards(qid):
        a = cache.get_audio(c.id)
        if not a:
            continue
        d = cache.get_digest(c.id)
        eps.append(QueueEpisode(card_id=c.id, title=(d.title if d else c.name),
                                audio_path=a.path, seconds=a.seconds, url=c.url))
    return eps


def make_synth(cache, engine=None):
    """Default synth: read extracted text from cache, TTS it, return AudioAsset|None."""
    from .audio import synthesize_card

    async def synth(feats: CardFeatures) -> AudioAsset | None:
        ec = cache.get_extracted(feats.card_id)
        text = ec.text if ec else feats.title
        return synthesize_card(feats.card_id, text, engine=engine, cache=cache,
                               ok=feats.ok)
    return synth


async def ensure_listen_queue(client, cache, enricher, comparator, synth, *,
                              target_hours: float = config.TARGET_QUEUE_HOURS,
                              source_list_ids=config.QUEUE_SOURCE_LIST_IDS,
                              log=None) -> dict:
    target_sec = target_hours * 3600
    queue_id = client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    queue_cards = client.get_cards(queue_id)
    in_queue = {c.id for c in queue_cards}

    def secs(card_id: str) -> float:
        a = cache.get_audio(card_id)
        return a.seconds if a else 0.0

    current = sum(secs(c.id) for c in queue_cards)
    added: list[CardFeatures] = []

    if current < target_sec:
        candidates = [c for lid in source_list_ids
                      for c in client.get_cards(lid) if c.id not in in_queue]
        if log:
            log.info(f"queue at {current/3600:.1f}h < {target_hours}h — "
                     f"{len(candidates)} candidates from System1+LifeOptim")
        cfeats = [f for f in await enricher.aenrich_many(candidates) if f.ok]
        ranked = await merge_sort(cfeats, comparator.acompare)
        for f in ranked:
            if current >= target_sec:
                break
            asset = await synth(f)
            if asset is None:
                continue
            client.move_card(f.card_id, queue_id, pos="bottom")
            added.append(f)
            in_queue.add(f.card_id)
            current += asset.seconds

    # keep the whole queue ordered by counterfactual impact (top = listen next)
    qfeats = (await enricher.aenrich_many(queue_cards)) + added
    final = await merge_sort(qfeats, comparator.acompare) if qfeats else []
    for i, f in enumerate(final):
        client.set_card_position(f.card_id, (i + 1) * 1000.0)

    reached = current >= target_sec
    if log:
        log.info(f"queue now {current/3600:.1f}h ({len(final)} items), "
                 f"added {len(added)}, reached_target={reached}")
    return {"queue_id": queue_id, "hours": current / 3600, "target_hours": target_hours,
            "added": [f.card_id for f in added], "reached_target": reached,
            "size": len(final)}
