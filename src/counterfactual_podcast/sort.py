"""Generic LLM-driven sorting over an injected async pairwise comparator.

The comparator is `async acompare(a, b) -> winner` where `winner` is whichever of
`a`/`b` should rank FIRST (higher counterfactual impact). These functions are pure
w.r.t. the comparator, so they unit-test with deterministic fakes and zero network.

Concurrency: bottom-up merge sort runs the independent merges within each level
concurrently (asyncio.gather). Comparisons WITHIN one merge stay serial. The real
rate-limit cap lives in the comparator (a semaphore), so we don't bound here.

Non-transitivity: an LLM comparator may cycle (A>B>C>A). Merge sort still terminates
and is deterministic given a memoizing comparator, but the order is only approximate
in the noisy middle. `copeland_rank` re-ranks a small head by win-count to stabilize
the part that actually matters (what Jay reads first).
"""
from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Sequence, TypeVar

T = TypeVar("T")
Comparator = Callable[[T, T], Awaitable[T]]


async def _merge(left: list[T], right: list[T], acompare: Comparator) -> list[T]:
    out: list[T] = []
    i = j = 0
    while i < len(left) and j < len(right):
        winner = await acompare(left[i], right[j])
        if winner is left[i] or winner == left[i]:
            out.append(left[i])
            i += 1
        else:
            out.append(right[j])
            j += 1
    out.extend(left[i:])
    out.extend(right[j:])
    return out


async def merge_presorted(runs: Sequence[Sequence[T]], acompare: Comparator) -> list[T]:
    """Merge several ALREADY-sorted runs into one sorted list.

    When the inputs are each already in priority order (e.g. System 1 and Life Optim,
    kept sorted in place), a full ``merge_sort`` wastefully re-compares within-run pairs.
    This trusts each run's order and only does the cross-run comparisons — ~sum(len) total
    instead of ~n log n — which is the dominant cost since comparisons are sequential LLM
    calls. Folds runs pairwise via the standard 2-way ``_merge``.
    """
    runs = [list(r) for r in runs if r]
    if not runs:
        return []
    acc = runs[0]
    for nxt in runs[1:]:
        acc = await _merge(acc, nxt, acompare)
    return acc


async def merge_sort(items: Sequence[T], acompare: Comparator) -> list[T]:
    """Bottom-up merge sort; independent merges per level run concurrently."""
    runs: list[list[T]] = [[x] for x in items]
    while len(runs) > 1:
        tasks = []
        leftovers = []
        i = 0
        while i < len(runs):
            if i + 1 < len(runs):
                tasks.append(_merge(runs[i], runs[i + 1], acompare))
            else:
                leftovers.append(runs[i])
            i += 2
        merged = await asyncio.gather(*tasks)
        runs = list(merged) + leftovers
    return runs[0] if runs else []


def _tiebreak(item, tiebreak_key):
    return tiebreak_key(item) if tiebreak_key else str(item)


async def copeland_rank(
    items: Sequence[T], acompare: Comparator, tiebreak_key: Callable[[T], object] | None = None
) -> list[T]:
    """All-pairs round robin; rank by win count. Robust to intransitive cycles."""
    items = list(items)
    n = len(items)
    if n <= 1:
        return items
    pairs = [(a, b) for a in range(n) for b in range(a + 1, n)]
    results = await asyncio.gather(
        *[acompare(items[a], items[b]) for a, b in pairs]
    )
    wins = [0] * n
    for (a, b), winner in zip(pairs, results):
        if winner is items[a] or winner == items[a]:
            wins[a] += 1
        else:
            wins[b] += 1
    order = sorted(
        range(n), key=lambda k: (-wins[k], _tiebreak(items[k], tiebreak_key))
    )
    return [items[k] for k in order]


async def insert_sorted(
    item: T, ordered: Sequence[T], acompare: Comparator
) -> list[T]:
    """Binary-insert `item` into a descending-priority `ordered` list (~log2 n)."""
    ordered = list(ordered)
    lo, hi = 0, len(ordered)
    while lo < hi:
        mid = (lo + hi) // 2
        winner = await acompare(item, ordered[mid])
        if winner is item or winner == item:
            hi = mid          # item ranks before mid -> go left
        else:
            lo = mid + 1
    ordered.insert(lo, item)
    return ordered
