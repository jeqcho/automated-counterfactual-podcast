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
from .sort import merge_presorted


def episodes_for_queue(client, cache, queue_id: str | None = None):
    """Build ordered podcast episodes from the Listen Queue's cards + cached audio."""
    from .rss import QueueEpisode
    from .extract import find_url
    from .titles import resolve_title
    qid = queue_id or client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    eps = []
    for c in client.get_cards(qid):
        a = cache.get_audio(c.id)
        if not a:
            continue
        d = cache.get_digest(c.id)
        title = resolve_title([d.title if d else None, c.name], url=find_url(c) or c.url)
        eps.append(QueueEpisode(card_id=c.id, title=title,
                                audio_path=a.path, seconds=a.seconds, url=c.url))
    return eps


def make_synth(cache, engine=None):
    """Default synth: read extracted text from cache, TTS it, return AudioAsset|None.

    ``card`` (optional) supplies the URL for the source domain + a name fallback for
    the title; without it the signpost still works from cached title/author/date.

    The returned ``synth`` also carries:
      - ``synth.many(items)`` — synth a batch of ``(feats, card)``, in PARALLEL for
        thread-safe engines (Google/OpenAI) and sequentially otherwise. Returns
        ``{card_id: AudioAsset|None}``. All SQLite reads/writes stay on the main thread;
        only the pure render is farmed to a thread pool.
      - ``synth.concurrency`` — the effective synth concurrency (1 = sequential).
    """
    import asyncio

    from .audio import cached_audio, render_audio, synthesize_card
    from .extract import find_url
    from .r2 import make_audio_checker
    from .titles import format_month_year, resolve_title, source_domain

    r2_check = make_audio_checker()  # None if R2 unconfigured (local runs)
    # engine may be an engine OBJECT (has .name) or None (-> the configured default name).
    eff_engine = (getattr(engine, "name", "") if engine is not None
                  else (config.TTS_ENGINE or "")).lower()
    concurrency = config.SYNTH_CONCURRENCY if eff_engine in config.PARALLEL_SAFE_TTS else 1

    def _render_kwargs(feats: CardFeatures, card):
        """Build (text, signpost-kwargs) from the cache. Main-thread cache reads only."""
        ec = cache.get_extracted(feats.card_id)
        text = ec.text if ec else feats.title
        url = (find_url(card) or card.url) if card is not None else ""
        title = resolve_title([ec.title if ec else None, feats.title,
                               card.name if card is not None else None], url=url)
        return text, dict(title=title, author=(ec.author if ec else ""),
                          source=source_domain(url),
                          date=format_month_year(ec.published if ec else ""))

    async def synth(feats: CardFeatures, card=None) -> AudioAsset | None:
        text, kw = _render_kwargs(feats, card)
        return synthesize_card(feats.card_id, text, engine=engine, cache=cache,
                               ok=feats.ok, r2_check=r2_check, **kw)

    async def synth_many(items) -> dict:
        """items: iterable of (feats, card). Renders misses concurrently (parallel-safe
        engines only); cache hit-checks and writes happen on the main thread."""
        results: dict = {}
        to_render = []
        for feats, card in items:
            if not feats.ok:
                results[feats.card_id] = None
                continue
            hit = cached_audio(feats.card_id, cache, r2_check=r2_check)  # main thread
            if hit is not None:
                results[feats.card_id] = hit
            else:
                to_render.append((feats, card))

        if concurrency <= 1:
            for feats, card in to_render:
                try:
                    results[feats.card_id] = await synth(feats, card)
                except Exception:  # noqa: BLE001 — one bad card must not kill the batch
                    results[feats.card_id] = None
            return results

        sem = asyncio.Semaphore(concurrency)

        async def _one(feats, card):
            text, kw = _render_kwargs(feats, card)  # main thread
            async with sem:
                try:
                    asset = await asyncio.to_thread(
                        render_audio, feats.card_id, text, engine=engine, **kw)
                except Exception:  # noqa: BLE001 — one bad card must not kill the batch
                    return feats.card_id, None
            cache.put_audio(asset)  # back on the main thread after the await
            return feats.card_id, asset

        rendered = await asyncio.gather(*[_one(f, c) for f, c in to_render])
        results.update(dict(rendered))
        return results

    synth.many = synth_many
    synth.concurrency = concurrency
    return synth


async def ensure_listen_queue(client, cache, enricher, comparator, synth, *,
                              target_hours: float = config.TARGET_QUEUE_HOURS,
                              source_list_ids=config.QUEUE_SOURCE_LIST_IDS,
                              reinsert_fn=None, checkpoint=None, preempt: bool = True,
                              log=None) -> dict:
    target_sec = target_hours * 3600
    queue_id = client.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    queue_cards = client.get_cards(queue_id)

    # SELF-HEAL: evict "orphan" queue cards that have no cached audio. A card only enters the
    # queue AFTER its audio synthesizes (below), but the audio row reaches R2 only on the
    # end-of-run cache push — so a run KILLED before that push leaves the card in the queue (the
    # Trello move is a live write) with its audio lost. Such cards are invisible in the feed
    # (episodes_for_queue skips no-audio cards) yet clog the queue and are pulled out of their
    # reading lists. Move them to 'To Be Processed' so the next routing re-ranks them back in.
    orphans = [c for c in queue_cards if not cache.get_audio(c.id)]
    if orphans:
        review_id = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
        for c in orphans:
            client.move_card(c.id, review_id, pos="bottom")
        if log:
            log.info(f"self-heal: evicted {len(orphans)} audio-less queue card(s) -> "
                     f"'{config.TO_BE_PROCESSED_LIST_NAME}' (debris from a killed run; "
                     f"they'll be re-ranked on the next routing)")
        queue_cards = [c for c in queue_cards if cache.get_audio(c.id)]

    in_queue = {c.id for c in queue_cards}
    run_secs: dict[str, float] = {}   # audio seconds for cards voiced in THIS run

    def secs(card_id: str) -> float:
        if card_id in run_secs:
            return run_secs[card_id]
        a = cache.get_audio(card_id)
        return a.seconds if a else 0.0

    put_origin = getattr(cache, "put_origin", None)
    put_cooldown = getattr(cache, "put_cooldown", None)
    cooldown_ids = getattr(cache, "cooldown_ids", None)
    # Top-up skips only cards that just failed to voice; an evicted card may refill the queue
    # (it only sits out PREEMPTION, so it can't bounce straight back in by displacing others).
    failing = cooldown_ids(reasons=("synth_failed",)) if cooldown_ids is not None else set()
    current = sum(secs(c.id) for c in queue_cards)
    added: list[CardFeatures] = []
    pool = None   # (per_list_feats, cards_by_id, origin_by_id) — reused by preemption

    if current < target_sec:
        # Each source list is ALREADY impact-sorted (sorted in place / kept sorted by
        # weekly insertion), so MERGE the per-list runs (~cross-list comparisons) instead
        # of a full re-sort of the combined pool (~n log n). Comparisons are sequential
        # LLM calls, so this is the ~8x first-run speedup.
        pool = await _source_pool(client, enricher, source_list_ids, in_queue | failing)
        per_list_feats, cards_by_id, origin_by_id = pool
        n_cand = sum(len(p) for p in per_list_feats)
        if log:
            log.info(f"queue at {current/3600:.1f}h < {target_hours}h — "
                     f"{n_cand} candidates from System1+LifeOptim (merging presorted lists)")
        ranked = await merge_presorted(per_list_feats, comparator.acompare)

        # Pre-synthesize a PRIORITY WINDOW concurrently (thread-safe engines only). Bound the
        # window by est reading-time so we don't synth far past the target (audio runs longer
        # than reading time, so an est-sized window comfortably covers target_sec). Cache hits
        # are free; only genuine misses render, in parallel. Kokoro -> concurrency 1 -> the
        # window still pre-renders but sequentially (identical result, just not faster).
        synthed: dict = {}
        many = getattr(synth, "many", None)
        if many is not None:
            need = max(0.0, target_sec - current)
            window, acc = [], 0.0
            for f in ranked:
                if acc >= need * 1.25:
                    break
                window.append(f)
                acc += (f.est_minutes or 0) * 60
            if window:
                synthed = await many([(f, cards_by_id.get(f.card_id)) for f in window])

        for f in ranked:
            if current >= target_sec:
                break
            if f.card_id in synthed:
                asset = synthed[f.card_id]
            else:
                try:
                    asset = await synth(f, cards_by_id.get(f.card_id))
                except Exception as e:  # noqa: BLE001 — one bad card must not kill the run
                    if log:
                        log.warning(f"  skip synth {f.card_id} ({(f.title or '')[:40]}): "
                                    f"{type(e).__name__}: {e}")
                    asset = None
            if asset is None:
                if put_cooldown is not None:
                    put_cooldown(f.card_id, "synth_failed")
                continue
            if put_origin is not None:
                put_origin(f.card_id, origin_by_id[f.card_id])
            client.move_card(f.card_id, queue_id, pos="bottom")
            added.append(f)
            in_queue.add(f.card_id)
            run_secs[f.card_id] = asset.seconds
            current += asset.seconds

    # The existing queue is already sorted (last run) and `added` came out of the merge in
    # sorted order, so merge those two presorted runs rather than re-sorting from scratch.
    queue_feats = await enricher.aenrich_many(queue_cards)
    order = await merge_presorted([queue_feats, added], comparator.acompare)

    if not preempt:
        pre = {"kept": list(order), "admitted": [], "evicted": []}
    else:
        pre = await _preempt(client, cache, enricher, comparator, synth, order, secs,
                             run_secs, target_sec=target_sec, queue_id=queue_id, pool=pool,
                             source_list_ids=source_list_ids, reinsert_fn=reinsert_fn,
                             checkpoint=checkpoint, log=log)
    final = pre["kept"]
    for i, f in enumerate(final):
        client.set_card_position(f.card_id, (i + 1) * 1000.0)

    current = sum(secs(f.card_id) for f in final)
    reached = current >= target_sec
    if log:
        log.info(f"queue now {current/3600:.1f}h ({len(final)} items), "
                 f"added {len(added)}, reached_target={reached}")
    return {"queue_id": queue_id, "hours": current / 3600, "target_hours": target_hours,
            "added": [f.card_id for f in added], "reached_target": reached,
            "size": len(final), "preempted": pre["admitted"], "evicted": pre["evicted"]}


async def _source_pool(client, enricher, source_list_ids, exclude):
    """Enrich each (already impact-sorted) source list, minus `exclude`, keeping ok cards.
    Returns (per_list_feats, cards_by_id, origin_by_id)."""
    per_list_feats, cards_by_id, origin_by_id = [], {}, {}
    for lid in source_list_ids:
        lst = [c for c in client.get_cards(lid) if c.id not in exclude]
        for c in lst:
            cards_by_id[c.id] = c
            origin_by_id[c.id] = lid
        per_list_feats.append([f for f in await enricher.aenrich_many(lst) if f.ok])
    return per_list_feats, cards_by_id, origin_by_id


async def _preempt(client, cache, enricher, comparator, synth, order, secs, run_secs, *,
                   target_sec, queue_id, pool, source_list_ids, reinsert_fn, checkpoint,
                   log) -> dict:
    """Preemption pass (plans/0001-queue-preemption.md): when the queue is at target, source
    cards that outrank its lowest card replace bottom cards, displacing at most their own
    audio seconds. Returns {"kept": ordered feats, "admitted": [ids], "evicted": [ids]}."""
    from .routing import _merge_newcomers

    untouched = {"kept": list(order), "admitted": [], "evicted": []}
    cap = config.QUEUE_PREEMPT_MAX
    pre = sum(secs(f.card_id) for f in order)
    if cap <= 0 or not order or pre < target_sec:            # 0. gate
        return untouched

    in_q = {f.card_id for f in order}
    cooldown_ids = getattr(cache, "cooldown_ids", None)
    cooling = cooldown_ids() if cooldown_ids is not None else set()   # incl. this run's
    if pool is None:
        pool = await _source_pool(client, enricher, source_list_ids, in_q)
    per_list_feats, cards_by_id, origin_by_id = pool

    bar = order[-1]                                           # 1. bar
    # 2. challengers: up to `cap` winners PER list (each list is sorted, so stop at its first
    # loss), then merge the per-list winner runs and keep the overall top `cap` — so one list
    # can't starve another's #1.
    winner_runs = []
    for run in per_list_feats:
        wins = []
        for f in run:
            if len(wins) >= cap:
                break
            if f.card_id in in_q or f.card_id in cooling:
                continue
            winner = await comparator.acompare(f, bar)
            if winner is f or winner == f:
                wins.append(f)
            else:
                break
        winner_runs.append(wins)
    challengers = (await merge_presorted(winner_runs, comparator.acompare))[:cap]
    if not challengers:
        return untouched

    assets: dict = {}                                         # 3. synthesize
    many = getattr(synth, "many", None)
    if many is not None:
        assets = await many([(f, cards_by_id.get(f.card_id)) for f in challengers])
    else:
        for f in challengers:
            try:
                assets[f.card_id] = await synth(f, cards_by_id.get(f.card_id))
            except Exception:  # noqa: BLE001 — one bad card must not kill the run
                assets[f.card_id] = None
    put_cooldown = getattr(cache, "put_cooldown", None)
    voiced = []
    for f in challengers:
        a = assets.get(f.card_id)
        if a is None:
            if put_cooldown is not None:
                put_cooldown(f.card_id, "synth_failed")
            if log:
                log.warning(f"  preempt: could not voice {(f.title or '')[:40]} (cooldown)")
        else:
            run_secs[f.card_id] = a.seconds
            voiced.append(f)
    if not voiced:
        return untouched

    combined = await _merge_newcomers(order, voiced, comparator.acompare)   # 4. place

    def cut(combined):                                        # 5. displacement cut
        prefix, acc = [], 0.0
        for f in combined:
            if acc >= target_sec:
                break
            prefix.append(f)
            acc += secs(f.card_id)
        p_ids = {f.card_id for f in prefix}
        admitted = [f for f in prefix if f.card_id not in in_q]
        need = sum(secs(f.card_id) for f in admitted)
        evicted, freed = [], 0.0
        for f in reversed(order):                             # bottom-up, outside the prefix
            if freed >= need:
                break
            if f.card_id in p_ids:
                continue
            evicted.append(f)
            freed += secs(f.card_id)
        return admitted, evicted

    admitted, evicted = cut(combined)
    while admitted and len(evicted) > config.QUEUE_EVICT_MAX:
        drop = admitted[-1].card_id                           # lowest-ranked admitted
        combined = [f for f in combined if f.card_id != drop]
        admitted, evicted = cut(combined)
    if not admitted:
        return untouched

    put_origin = getattr(cache, "put_origin", None)           # 6. persist before Trello
    for f in evicted:
        if put_cooldown is not None:
            put_cooldown(f.card_id, "evicted")
    for f in admitted:
        if put_origin is not None:
            put_origin(f.card_id, origin_by_id[f.card_id])
    if checkpoint is not None:
        maybe = checkpoint()
        if hasattr(maybe, "__await__"):
            await maybe

    # 7. apply — evict first (a kill now leaves the queue UNDER target; top-up refills it)
    ev_ids = {f.card_id for f in evicted}
    ev_cards = [c for c in client.get_cards(queue_id) if c.id in ev_ids]
    rehomed = False
    if reinsert_fn is not None:
        # Admitted cards still sit in their source lists (evict-first); exclude them so the
        # re-homed cards' rank markers count only cards that stay.
        try:
            await reinsert_fn(ev_cards, exclude={f.card_id for f in admitted})
            rehomed = True
        except Exception as e:  # noqa: BLE001 — fall back rather than half-apply the pass
            if log:
                log.warning(f"  preempt: re-home failed ({type(e).__name__}: {e}); "
                            f"sending evicted cards to '{config.TO_BE_PROCESSED_LIST_NAME}'")
    if not rehomed:
        review_id = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)
        for c in client.get_cards(queue_id):      # only those a failed re-home left behind
            if c.id in ev_ids:
                client.move_card(c.id, review_id, pos="bottom")
    for f in admitted:
        client.move_card(f.card_id, queue_id, pos="bottom")

    kept = [f for f in combined                               # 8. full kept list, C order
            if f.card_id in in_q and f.card_id not in ev_ids
            or f.card_id in {a.card_id for a in admitted}]
    if log:
        log.info(f"preempt: admitted {len(admitted)} "
                 f"{[(f.title or '')[:40] for f in admitted]}, evicted {len(evicted)} "
                 f"{[(f.title or '')[:40] for f in evicted]}")
    return {"kept": kept, "admitted": [f.card_id for f in admitted],
            "evicted": [f.card_id for f in evicted]}
