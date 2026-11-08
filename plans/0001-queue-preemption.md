# 0001 — Listen Queue preemption

Issue: #1

## Problem

`ensure_listen_queue` only tops up when the queue is below `TARGET_QUEUE_HOURS`. Once the
queue is full, nothing new enters, so a freshly routed card that outranks everything in the
queue (2026-11-07: the Character.AI Bookface post landed at life_optim #1) waits until Jay
listens the queue down. The queue should reflect the current top material.

## Design

Add a **preemption pass** to `ensure_listen_queue`, after the existing top-up and before the
final re-order.

1. **Bar.** The queue is kept impact-sorted, so after self-heal + top-up the bar is the
   lowest-ranked card of the *current* queue (existing + just-added). Get it by merging
   `queue_feats` and `added` once (the merge already happens at the end; hoist it so the
   pass reuses the ordering and the final step only merges in the newcomers).
2. **Challengers.** For each source list (System 1, Life Optim — already impact-sorted),
   walk from the top over cards not in the queue whose features are `ok`. Compare each with
   the bar via `comparator.acompare`. A winner is a challenger; stop that list at the first
   loss (the list is sorted, so later cards would lose too). Cap total challengers per run
   at `config.QUEUE_PREEMPT_MAX` (default 5) to bound TTS spend and churn.
   - The bar is fixed for the whole pass (the lowest card at pass start). Comparing
     against a moving bar adds calls for little gain.
   - Reuse the candidate enrichment from the top-up when it ran; otherwise enrich the
     source lists here (cached digests → cheap).
3. **Admit.** Synthesize challengers (reuse `synth.many` when present, else `synth`); skip
   any whose audio is `None` or raises. Move each admitted card into the queue.
4. **Evict.** Walk the queue from the bottom (merged order, excluding cards admitted in
   this run), removing a card while `current - secs(card) >= target_sec`. Stop at the first
   card whose removal would drop below target. Evict at most as many cards as were
   admitted (the queue never shrinks in item count because of preemption).
5. **Re-home evicted cards** via an injected `reinsert_fn(cards) -> None` callback. Phase 2
   / weekly pass one that classifies each card (`Classifier.aclassify`) and binary-inserts
   it into its target list with `_merge_newcomers` + `_assign_positions` + rank marker —
   the same steps `_route_parallel` uses (factor out a small `_insert_into_lists` helper
   in `pipelines/phase2.py` and reuse it from `_route_parallel`). If `reinsert_fn` is None
   (tests, older callers) the evicted card goes to `To Be Processed`, matching the existing
   orphan self-heal.
6. **Order.** Final re-order merges the surviving queue with admitted cards
   (`merge_presorted`), as today.
7. **Log + return.** Log `preempt: admitted N (titles), evicted M (titles)`; add
   `"preempted": [...]`, `"evicted": [...]` to the return dict.

Audio for evicted cards stays cached, so re-admitting later is free.

## Config

- `QUEUE_PREEMPT_MAX = 5` in `config.py` (0 disables the pass).

## Tests (`tests/test_listen_queue.py`, fakes already there)

- Full queue + a source card that beats the bottom → it is admitted, the bottom card is
  evicted, queue hours stay ≥ target, final order is impact-sorted.
- Source card that loses to the bottom → nothing changes (one comparison per list).
- Cap: more winners than `QUEUE_PREEMPT_MAX` → only the cap is admitted.
- Eviction never drops below target (long new clip, short evictable clips; and the
  reverse: short new clip cannot evict a long bottom card if that would undershoot).
- Synth failure for a challenger → not admitted, nothing evicted for it.
- `reinsert_fn` is called with evicted cards; without it they go to `To Be Processed`.
- Existing tests keep passing (top-up behaviour unchanged when below target).
- Phase 2 test: `_insert_into_lists` refactor leaves `_route_parallel` results unchanged.

## Docs

- README "Listen Queue" section + CLAUDE.md: queue now admits higher-ranked cards and
  evicts its bottom, capped per run.

## Rollout

Merge → `wrangler deploy` (container image rebuild, see CLAUDE.md deploy gotchas) → trigger
Phase 2 once and confirm the Character.AI card enters the queue.
