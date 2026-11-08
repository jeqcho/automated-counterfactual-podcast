# 0001 — Listen Queue preemption

Issue: #1

## Problem

`ensure_listen_queue` only tops up when the queue is below `TARGET_QUEUE_HOURS`. Once the
queue is full, nothing new enters, so a freshly routed card that outranks everything in the
queue (2026-11-07: the Character.AI Bookface post landed at life_optim #1) waits until Jay
listens the queue down. The queue should reflect the current top material.

## Design

Rule: **the queue is the top-ranked prefix of (queue ∪ challengers) that reaches
`TARGET_QUEUE_HOURS`.** A preemption pass runs in `ensure_listen_queue` after self-heal and
the existing top-up, before the final re-order.

1. **Bar.** Merge `queue_feats` with `added` once (hoisted from the final step) to get the
   current queue order `Q`. The bar is `Q[-1]`, fixed for the pass.
2. **Challengers.** For each source list (System 1, Life Optim — already sorted), walk from
   the top over cards not in `Q` with `ok` features; compare each with the bar; a winner is a
   challenger; stop the list at its first loss. **Cooldown:** skip cards evicted in the
   last `config.QUEUE_EVICT_COOLDOWN_DAYS` (default 7), read from a new cache table
   `evictions(card_id PRIMARY KEY, evicted_at)` added in `Cache._migrate`, so a noisy
   comparison cannot bounce a card in and out every run. Cap total challengers at
   `config.QUEUE_PREEMPT_MAX` (default 5; 0 disables). Reuse the top-up's enrichment when it
   ran; otherwise enrich the source lists here (cached digests). Pairwise results are cached
   by the comparator, so repeat runs on unchanged lists cost no LLM calls.
3. **Place.** Binary-insert each challenger into `Q` with `sort.insert_index` (~log2 n each,
   concurrent, then order same-slot challengers with `merge_sort` — i.e. reuse
   `_merge_newcomers`) → combined order `C`.
4. **Synthesize** the challengers (≤ cap; `synth.many` if present, else `synth`). Drop any
   whose audio is `None` or raises (they are removed from `C`). Audio is cached, so a
   challenger that ends up outside the prefix costs nothing next time.
5. **Cut.** Walk `C` top-down accumulating audio seconds; keep cards until the running total
   is ≥ `target_sec`; everything after that point is outside the queue. Queue cards outside
   the prefix are **evicted**; challengers inside it are **admitted**; challengers outside it
   stay where they are. This bounds hours (the queue is never more than one clip over
   target) and only evicts cards that rank below the kept set — no uncompared evictions.
   If the queue was below target after top-up (pool exhausted), the cut keeps everything.
   **Guard:** the cut only applies when at least one challenger lands inside the kept
   prefix; with no challengers (or none surviving synth / none in the prefix) the queue is
   left untouched, even if it is over target (manual drags, lowered target).
6. **Apply.** Move admitted cards into the queue; re-home evicted cards with
   `reinsert_evicted(cards)`, defined in a new neutral module `routing.py` (avoids the
   `phase2` / `weekly` shared helpers in one place). It records each eviction in the cache, classifies each evicted card (`Classifier.aclassify`,
   one uncached Haiku call per card, ≤ cap per run), re-reads the destination list with
   `get_cards` (fresh snapshot, so the just-moved cards are not in it), binary-inserts with
   `_merge_newcomers`, places with `_assign_positions`, and writes the rank marker.
   `_merge_newcomers` / `_assign_positions` move to `routing.py`; `phase2.py` imports them
   from there. `ensure_listen_queue` takes an optional `reinsert_fn`; both pipelines'
   `_build_and_run` pass one built from their classifier. Without it, evicted cards go to
   `To Be Processed` (same as orphan self-heal).
7. **Order.** Final positions = the kept prefix of `C` in order.
8. **Log + return.** Recompute `hours`, `reached_target`, `size` from the kept prefix. `preempt: admitted N [...], evicted M [...]`; add `"preempted"` and
   `"evicted"` to the return dict.

Kill-safety: an admitted card moved before its audio row reaches R2 is caught by the
existing orphan self-heal on the next run.

## Config

- `QUEUE_PREEMPT_MAX = 5` in `config.py` (0 disables the pass).
- `QUEUE_EVICT_COOLDOWN_DAYS = 7`.

## Tests (`tests/test_listen_queue.py`)

Fakes: `FakeClient.ensure_list` maps names to distinct ids; `move_card` removes the card
from its old list. `_NoCache` gains `get/put_eviction`.

- Over-target queue with no challengers → untouched.
- Cooldown: a card evicted within the window is not a challenger even if it beats the bar.

- Full queue + a source card that beats the bottom → it is admitted, the bottom card is
  evicted, queue hours stay ≥ target, final order is impact-sorted.
- Source card that loses to the bottom → nothing changes (one comparison per list).
- Cap: more winners than `QUEUE_PREEMPT_MAX` → only the cap is admitted.
- Cut keeps hours in [target, target + one clip): long challenger evicts several short
  bottom cards; short challenger cannot evict a long bottom card if that would undershoot.
- Three challengers beat the bar but the 2nd-lowest queue card outranks one of them → that
  queue card is not evicted.
- Idempotence: a second run on unchanged lists admits and evicts nothing.
- Synth failure for a challenger → not admitted, nothing evicted for it.
- `reinsert_fn` is called with evicted cards; without it they go to `To Be Processed`.
- Existing tests keep passing (top-up behaviour unchanged when below target).
- Existing phase 2 tests pass after moving `_merge_newcomers`/`_assign_positions` to
  `routing.py`; a unit test for `reinsert_evicted` with fakes (fresh snapshot, rank marker).

## Docs

- README "Listen Queue" section + CLAUDE.md: queue now admits higher-ranked cards and
  evicts its bottom, capped per run.

## Rollout

Merge → `wrangler deploy` (container image rebuild, see CLAUDE.md deploy gotchas) → trigger
Phase 2 once and confirm the Character.AI card enters the queue.
