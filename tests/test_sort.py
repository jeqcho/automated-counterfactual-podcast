from collections import Counter

import pytest

from counterfactual_podcast.sort import (
    copeland_rank, insert_sorted, merge_presorted, merge_sort)


async def fake_cmp(a, b):
    """Higher int ranks first (descending order)."""
    return a if a > b else b


def counting_cmp(counter):
    async def cmp(a, b):
        counter["n"] += 1
        return a if a > b else b
    return cmp


async def cyclic_cmp(a, b):
    """Intransitive over {0,1,2}: 0>1, 1>2, 2>0."""
    return a if (a - b) % 3 == 2 else b


async def test_merge_sort_orders_desc():
    assert await merge_sort([3, 1, 2, 5, 4], fake_cmp) == [5, 4, 3, 2, 1]


async def test_merge_sort_handles_odd_and_empty():
    assert await merge_sort([], fake_cmp) == []
    assert await merge_sort([7], fake_cmp) == [7]
    assert await merge_sort([1, 3, 2], fake_cmp) == [3, 2, 1]


async def test_insert_sorted_places_correctly():
    assert await insert_sorted(3, [5, 4, 2, 1], fake_cmp) == [5, 4, 3, 2, 1]
    assert await insert_sorted(9, [5, 4], fake_cmp) == [9, 5, 4]
    assert await insert_sorted(0, [5, 4], fake_cmp) == [5, 4, 0]


async def test_merge_presorted_merges_two_sorted_runs():
    # Two already-descending runs -> correctly interleaved, with only cross-run compares.
    calls = Counter()
    out = await merge_presorted([[9, 6, 3], [8, 5, 2]], counting_cmp(calls))
    assert out == [9, 8, 6, 5, 3, 2]
    # A full merge_sort of 6 items would do ~ n*log2 n ≈ 15 compares; merging two
    # presorted runs needs at most len(a)+len(b)-1 = 5.
    assert calls["n"] <= 5


async def test_merge_presorted_handles_empty_and_single():
    assert await merge_presorted([], fake_cmp) == []
    assert await merge_presorted([[], [3, 1]], fake_cmp) == [3, 1]
    assert await merge_presorted([[5]], fake_cmp) == [5]


async def test_merge_sort_comparison_count_is_nlogn():
    calls = Counter()
    await merge_sort(list(range(64)), counting_cmp(calls))
    assert calls["n"] < 64 * 7  # < n*log2(n)*~1.1


async def test_intransitive_comparator_terminates_and_is_deterministic():
    out1 = await merge_sort([0, 1, 2], cyclic_cmp)
    out2 = await merge_sort([0, 1, 2], cyclic_cmp)
    assert out1 == out2 and len(out1) == 3


async def test_copeland_runs_and_is_deterministic_on_cycle():
    # every item wins once -> all tied -> deterministic tiebreak (str) order
    out = await copeland_rank([0, 1, 2], cyclic_cmp)
    assert sorted(out) == [0, 1, 2]
    assert out == await copeland_rank([0, 1, 2], cyclic_cmp)


async def test_copeland_orders_transitive():
    assert await copeland_rank([2, 5, 1, 4], fake_cmp) == [5, 4, 2, 1]
