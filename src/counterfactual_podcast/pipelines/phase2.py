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

from collections import OrderedDict, defaultdict

from .. import config
from ..classify import target_list_id
from ..sort import insert_index, insert_sorted, merge_sort
from .weekly import _insertion_pos  # shared fractional-position helper


async def _merge_newcomers(existing_feats, newcomers, acompare) -> list:
    """Insert a few `newcomers` into a big already-sorted `existing_feats` and return the full
    priority order. Each newcomer is BINARY-searched into `existing` (O(log n)), and the
    searches run CONCURRENTLY (independent against the fixed snapshot). Same-slot newcomers are
    ordered by first sorting the newcomers among themselves (`merge_sort`), so a batch of
    same-list cards keeps correct relative order. Avoids the O(n) linear merge_presorted scan."""
    if not newcomers:
        return list(existing_feats)
    sorted_new = await merge_sort(newcomers, acompare)          # tiebreak order for same slot
    idxs = await asyncio.gather(                                # parallel binary searches
        *[insert_index(f, existing_feats, acompare) for f in sorted_new])
    at = defaultdict(list)
    for f, idx in zip(sorted_new, idxs):
        at[idx].append(f)                                       # sorted_new order preserved
    ordered = []
    for i in range(len(existing_feats) + 1):
        ordered.extend(at.get(i, []))
        if i < len(existing_feats):
            ordered.append(existing_feats[i])
    return ordered


async def _maybe_checkpoint(checkpoint, log, note):
    """Persist the cache to R2 (if a checkpoint fn is configured), off the event loop."""
    if checkpoint is None:
        return
    if log:
        log.info(f"  [checkpoint] persisting cache {note}")
    maybe = checkpoint()
    if asyncio.iscoroutine(maybe):
        await maybe


def _assign_positions(ordered, pos_by_id, newcomer_ids) -> dict:
    """Fractional Trello positions for the newcomers in a merged priority order.

    ``ordered`` interleaves the destination list's existing cards (known positions in
    ``pos_by_id``) with newly-routed cards. A newcomer takes a position strictly between its
    nearest existing neighbours; a RUN of consecutive newcomers is spaced evenly across that
    gap (so batch-inserted same-list cards keep their relative order). Returns
    ``{card_id: pos}`` for newcomers only."""
    out, n, i = {}, len(ordered), 0
    while i < n:
        if ordered[i].card_id not in newcomer_ids:
            i += 1
            continue
        j = i
        while j < n and ordered[j].card_id in newcomer_ids:
            j += 1
        # i-1 and j (if present) are always EXISTING cards (a newcomer run is maximal).
        lp = pos_by_id.get(ordered[i - 1].card_id, 0.0) if i > 0 else 0.0
        rp = pos_by_id.get(ordered[j].card_id, lp + 2000.0) if j < n else lp + 2000.0
        run = j - i
        for k in range(run):
            out[ordered[i + k].card_id] = lp + (rp - lp) * (k + 1) / (run + 1)
        i = j
    return out


async def run_phase2(client, cache, enricher, classifier, comparator,
                     ensure_queue_fn, publish_fn, *, apply: bool = False, log=None,
                     checkpoint=None, checkpoint_every: int = 10) -> dict:
    trigger_id = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
    cards = client.get_cards(trigger_id)
    if log:
        log.info(f"'To Be Processed' has {len(cards)} cards")

    if apply and config.PHASE2_PARALLEL_SORT and cards:
        routed = await _route_parallel(client, enricher, classifier, comparator, cards,
                                       log=log, checkpoint=checkpoint,
                                       checkpoint_every=checkpoint_every)
    else:
        routed = await _route_sequential(client, enricher, classifier, comparator, cards,
                                         apply=apply, log=log, checkpoint=checkpoint,
                                         checkpoint_every=checkpoint_every)

    queue = await ensure_queue_fn() if apply else {"skipped": "dry-run"}
    feed = publish_fn() if apply else {"skipped": "dry-run"}
    return {"processed": len(cards), "routed": routed, "queue": queue, "feed": feed}


async def _route_sequential(client, enricher, classifier, comparator, cards, *,
                            apply, log, checkpoint, checkpoint_every) -> list:
    """Original one-card-at-a-time path: enrich -> classify -> binary-insert -> move. Used for
    dry-runs (classify only, no ranking) and when PHASE2_PARALLEL_SORT is off."""
    routed, done = [], 0
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
        if apply and checkpoint is not None:
            done += 1
            if done % checkpoint_every == 0:
                await _maybe_checkpoint(checkpoint, log, f"after {done} cards")
    return routed


async def _route_parallel(client, enricher, classifier, comparator, cards, *,
                          log, checkpoint, checkpoint_every) -> list:
    """Parallel routing (apply only). The slow part — enrichment and the pairwise LLM
    comparisons — runs CONCURRENTLY (bounded by the comparator/enricher semaphores); the
    Trello I/O stays serial on the main thread (it's fast and not thread-safe).

      A. enrich + classify every newcomer concurrently, group by destination list
      B. read + enrich each destination list once (hoisted out of the per-card loop)
      C. per list, in parallel: sort the newcomers among themselves (merge_sort) then
         merge that run into the existing list (merge_presorted) — Jay's "sort within,
         then combine". Different lists never compare against each other.
      D. apply the moves + rank markers serially, in priority order.
    """
    # A) enrich + classify concurrently
    async def enrich_classify(card):
        feats = await enricher.aenrich(card)
        label = (await classifier.aclassify(feats)).get("label", "system1")
        return card, feats, label

    triples = await asyncio.gather(*[enrich_classify(c) for c in cards])
    groups: "OrderedDict[str, list]" = OrderedDict()   # list_id -> [(card, feats), ...]
    labels = {}
    for card, feats, label in triples:
        groups.setdefault(target_list_id(label), []).append((card, feats))
        labels[card.id] = label
    if log:
        by_label = {}
        for c, _, lab in triples:
            by_label[lab] = by_label.get(lab, 0) + 1
        log.info(f"enriched+classified {len(cards)} cards, ranking in parallel: "
                 + ", ".join(f"{lab}={n}" for lab, n in by_label.items()))

    # B) read each destination list once
    ctx = {}
    for lid, members in groups.items():
        existing = client.get_cards(lid)
        ctx[lid] = ({c.id: c.pos for c in existing}, await enricher.aenrich_many(existing))

    # C) per-list rank (binary-insert newcomers, parallel), all lists concurrent
    async def rank_group(lid, members):
        _pos_by_id, existing_feats = ctx[lid]
        ordered = await _merge_newcomers(existing_feats, [f for _, f in members],
                                         comparator.acompare)
        return lid, ordered

    group_orders = await asyncio.gather(*[rank_group(lid, m) for lid, m in groups.items()])
    # comparisons are all cached now — persist before touching Trello
    await _maybe_checkpoint(checkpoint, log, "after ranking (pre-write)")

    # D) serial Trello writes in priority order
    card_by_id = {c.id: c for m in groups.values() for c, _ in m}
    newcomer_ids = {c.id for m in groups.values() for c, _ in m}
    routed, done = [], 0
    for lid, ordered in group_orders:
        pos_by_id, _ = ctx[lid]
        positions = _assign_positions(ordered, pos_by_id, newcomer_ids)
        for idx, f in enumerate(ordered):
            if f.card_id not in newcomer_ids:
                continue
            card, rank = card_by_id[f.card_id], idx + 1
            client.move_card(card.id, lid, pos=positions[f.card_id])
            client.set_rank_marker(card, rank, f.est_minutes, f.digest or "")
            routed.append({"card_id": card.id, "label": labels[card.id], "rank": rank})
            if log:
                log.info(f"  {card.name[:50]} -> {labels[card.id]} @#{rank}")
            done += 1
            if checkpoint is not None and done % checkpoint_every == 0:
                await _maybe_checkpoint(checkpoint, log, f"after {done} writes")
    return routed


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
