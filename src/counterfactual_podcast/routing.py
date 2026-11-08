"""Shared card-routing helpers: binary-insert cards into an impact-sorted reading list.

Used by Phase 2 routing and by the Listen Queue, which re-homes cards it evicts.
"""
from __future__ import annotations

import asyncio
from collections import defaultdict

from . import config
from .classify import target_list_id
from .sort import insert_index, merge_sort


async def _merge_newcomers(existing_feats, newcomers, acompare) -> list:
    """Insert a few `newcomers` into a big already-sorted `existing_feats` and return the full
    priority order. Each newcomer is BINARY-searched into `existing` (O(log n)), and the
    searches run CONCURRENTLY (independent against the fixed snapshot). Only newcomers that land
    in the SAME slot are then compared among themselves (`merge_sort` per slot, slots
    concurrent). Avoids the O(n) linear merge_presorted scan.

    Latency, not call count, is the cost here: comparisons inside one merge/search are serial.
    Merge-sorting ALL newcomers up front (the old tiebreak) was a ~30-call serial chain for 17
    cards — most of a 2m19s ranking step (2026-10-04) — to order cards that almost never share
    a slot. Now the critical path is one binary search (~log2 n) plus a tiny per-slot sort."""
    if not newcomers:
        return list(existing_feats)
    idxs = await asyncio.gather(                                # parallel binary searches
        *[insert_index(f, existing_feats, acompare) for f in newcomers])
    at = defaultdict(list)
    for f, idx in zip(newcomers, idxs):
        at[idx].append(f)
    crowded = [i for i, group in at.items() if len(group) > 1]
    if crowded:                                                 # order same-slot newcomers
        sorted_groups = await asyncio.gather(*[merge_sort(at[i], acompare) for i in crowded])
        at.update(zip(crowded, sorted_groups))
    ordered = []
    for i in range(len(existing_feats) + 1):
        ordered.extend(at.get(i, []))
        if i < len(existing_feats):
            ordered.append(existing_feats[i])
    return ordered


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


def make_reinsert(client, cache, enricher, classifier, comparator, log=None):
    """Build the Listen Queue's ``reinsert_fn``: re-home evicted queue cards into their reading
    list at their impact rank. Destination = the list the card came from (``queue_origin``);
    the classifier is only consulted for cards with no recorded origin (queued before
    preemption existed). Each destination is re-read fresh, so cards just moved into the
    queue are not in the snapshot."""

    async def reinsert(cards) -> None:
        if not cards:
            return
        get_origin = getattr(cache, "get_origin", None)
        groups: dict[str, list] = defaultdict(list)
        for card in cards:
            feats = await enricher.aenrich(card)
            lid = get_origin(card.id) if get_origin is not None else None
            if not lid:
                label = (await classifier.aclassify(feats)).get("label", "system1")
                lid = target_list_id(label)
            groups[lid].append((card, feats))
        ids = {c.id for c in cards}
        for lid, members in groups.items():
            existing = [c for c in client.get_cards(lid) if c.id not in ids]
            existing_feats = await enricher.aenrich_many(existing)
            ordered = await _merge_newcomers(existing_feats, [f for _, f in members],
                                             comparator.acompare)
            mine = {c.id for c, _ in members}
            positions = _assign_positions(ordered, {c.id: c.pos for c in existing}, mine)
            by_id = {c.id: c for c, _ in members}
            for idx, f in enumerate(ordered):
                if f.card_id not in mine:
                    continue
                card = by_id[f.card_id]
                client.move_card(card.id, lid, pos=positions[f.card_id])
                client.set_rank_marker(card, idx + 1, f.est_minutes, f.digest or "")
                if log:
                    log.info(f"  re-homed {card.name[:50]} -> {lid} @#{idx + 1}")

    return reinsert
