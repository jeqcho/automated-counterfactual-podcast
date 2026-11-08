# 0001 — Listen Queue preemption

Issue: #1

## Problem

`ensure_listen_queue` only tops up when the queue is below `TARGET_QUEUE_HOURS`. Once the
queue is full, nothing new enters, so a freshly routed card that outranks everything in the
queue (2026-11-07: the Character.AI Bookface post landed at life_optim #1) waits until Jay
listens the queue down. The queue should reflect the current top material.

## Rule

The queue is the top-ranked prefix of (queue ∪ challengers) that reaches the cut. A
**preemption pass** runs inside `ensure_listen_queue` after self-heal and the existing
top-up, replacing the current final re-order step.

## Cache additions (`cache.py`)

- `queue_cooldown(card_id, reason, at, PRIMARY KEY (card_id, reason))`, reason ∈
  {`evicted`, `synth_failed`}. API: `put_cooldown(card_id, reason)`;
  `cooldown_ids(now) -> set` returns cards with an `evicted` row newer than
  `QUEUE_EVICT_COOLDOWN_DAYS` OR a `synth_failed` row newer than
  `QUEUE_SYNTH_FAIL_COOLDOWN_HOURS`. Separate rows per reason, so one never overwrites the
  other.
- `queue_origin(card_id PRIMARY KEY, list_id)`. API: `put_origin(card_id, list_id)`,
  `get_origin(card_id) -> str | None`. Written whenever a card enters the queue — in the
  top-up (the list it came from is known per `source_list_ids` run) and in preemption.
- Both tables via `CREATE TABLE IF NOT EXISTS` in `_SCHEMA` (works on old DBs).
- `ensure_listen_queue` uses these via `getattr(cache, ..., None)`, so test fakes without them
  still work (no cooldown / no origin).

## Pass

0. **Gate.** Let `Q` = merge of existing queue feats and top-up `added` (the merge the final
   step already does, hoisted). `secs(id)` = this run's synth asset seconds, else cached audio
   seconds. `pre = Σ secs(Q)`. Skip the pass (just order `Q`) if `QUEUE_PREEMPT_MAX == 0`,
   `Q` is empty, or `pre < target_sec`.
1. **Bar** = `Q[-1]`, fixed for the pass.
2. **Challengers.** For each source list (System 1, Life Optim — each already sorted), walk
   from the top over `ok` cards not in `Q` and not in `cooldown_ids`; compare with the bar;
   a winner is a challenger; stop that list at its first loss. Stop entirely at
   `QUEUE_PREEMPT_MAX` challengers. Reuse the top-up's per-list feats when it ran, else
   enrich the source lists here (cached digests). Remember each challenger's source list.
   No challengers → order `Q`, done.
3. **Synthesize** challengers (`synth.many` if present, else `synth` with try/except).
   `make_synth`'s sequential `synth_many` branch gets a per-item try/except returning
   `None`, so a failing challenger can never raise out of the pass.
   Failures (`None` / raise): `put_cooldown(id, "synth_failed")`, dropped. None left → done.
4. **Place.** `C = _merge_newcomers(Q, survivors)` (binary insert, ~log2 n each).
5. **Cut (displacement rule — never grows the queue past `max(pre, target + one clip)`).**
   - Prefix `P` = walk `C` top-down, keeping each card while the running total is
     `< target_sec` before adding it (so `Σ P ∈ [target, target + one clip)`).
   - Admitted = challengers in `P`. **Guard:** none → leave the queue untouched (order `Q`),
     even if it is over target.
   - Eviction candidates = queue cards not in `P`, taken bottom-up. Evict while
     `evicted_secs < admitted_secs`. Queue cards outside `P` that are not needed to make
     room stay (below the kept prefix) — so a manually overfilled queue is never trimmed
     beyond what the newcomers displace.
   - Result: total ≤ `max(pre, Σ P)`. A near-target queue stays within one clip of target;
     an overfilled one never grows. No mode switch, no ratchet.
   - **Eviction cap:** if more than `QUEUE_EVICT_MAX` evictions are needed, drop the
     lowest-ranked admitted challenger from `C` and recompute; repeat (no admissions left →
     untouched).
6. **Persist before Trello.** `put_cooldown(id, "evicted")` for each evicted card,
   `put_origin(id, src)` for each admitted card, then call the optional `checkpoint`
   (Phase 2 passes its existing R2 push, as `_route_parallel` does before its writes).
   Weekly runs locally only and never pulls from R2, so it passes `checkpoint=None` — a
   mid-run push there would overwrite R2 with a stale local cache.
7. **Apply — evict first, then admit.** Re-home evicted cards via the optional
   `reinsert_fn(cards)`; without one, move them to `To Be Processed` (like the orphan
   self-heal). Then move admitted cards into the queue. A kill mid-apply leaves the queue
   under target, which the next top-up refills.
8. **Order + return.** Kept list = every queue card not evicted plus admitted challengers,
   in `C` order (P followed by any retained overfill cards). Positions, `hours`, `size`,
   `reached_target` are all computed from this full kept list, plus `preempted` and `evicted` id lists;
   log `preempt: admitted N [...], evicted M [...]`.

## Re-homing (`routing.py`)

New neutral module holding `_merge_newcomers` and `_assign_positions` (moved from
`pipelines/phase2.py`, which imports them back) plus:

`make_reinsert(client, cache, enricher, classifier, comparator, log) -> async reinsert(cards)`:
for each card, destination = `cache.get_origin(id)`, else `target_list_id(classifier
label)` (one Haiku call; only for cards that entered the queue before this change). Group
by destination; for each list re-read `get_cards` (fresh snapshot), enrich, binary-insert
with `_merge_newcomers`, place with `_assign_positions`, move, write the rank marker.
Bounded by `QUEUE_EVICT_MAX` cards per run.

`pipelines/phase2.py` and `pipelines/weekly.py` `_build_and_run` pass
`reinsert_fn=make_reinsert(...)` and `checkpoint=...` into `ensure_listen_queue`.

## Config

`QUEUE_PREEMPT_MAX = 5` (0 disables), `QUEUE_EVICT_MAX = 10`,
`QUEUE_EVICT_COOLDOWN_DAYS = 7`, `QUEUE_SYNTH_FAIL_COOLDOWN_HOURS = 20`.

## Tests

`tests/test_listen_queue.py` fakes: `FakeClient.ensure_list` maps names to distinct ids;
`move_card` removes the card from its old list. A small in-memory fake cache (or the real
`Cache(":memory:")`) for cooldown/origin tests.

- Full queue + a source card beating the bottom → admitted, bottom evicted, hours ≥ target,
  final order impact-sorted, origin recorded.
- Source card losing to the bottom → nothing changes; one comparison per list.
- Challenger cap; eviction cap (long challenger vs many short clips → not admitted).
- Cut: hours within one clip of target; short challenger can't evict a long bottom card if
  that undershoots.
- No ratchet: three consecutive runs with new challengers, starting (a) from a just-topped-up
  queue and (b) from a 1.5× overfilled queue → total hours never rise above
  `max(pre, target + one clip)`.
- Three challengers beat the bar, 2nd-lowest queue card outranks one → it stays.
- Over-target queue, no challengers → untouched. Empty / below-target queue → no crash.
- Cooldowns: evicted card skipped for 7 days; synth failure skipped next run only, and
  doesn't overwrite an `evicted` row.
- Idempotence: second run on unchanged lists admits/evicts nothing.
- Checkpoint called before the first Trello move; evictions moved before admissions.
- `reinsert_fn` gets evicted cards; without it they go to `To Be Processed`.
- `routing.make_reinsert`: uses origin (no classify call) when recorded, classifier
  otherwise; fresh snapshot; rank marker written.
- Sequential synth_many: a raising render returns `None`, pass continues.
- Overfilled queue + admission: retained overfill cards get positions after P, totals count them.
- Existing listen-queue and phase 2 tests pass.

## Docs

README "Listen Queue" section + CLAUDE.md: queue admits higher-ranked cards and evicts its
bottom (≤ 5 in, ≤ 10 out per run, 7-day cooldown).

## Rollout

Merge → `wrangler deploy` (container image rebuild, see CLAUDE.md deploy gotchas) → trigger
Phase 2 once and confirm the Character.AI card enters the queue.
