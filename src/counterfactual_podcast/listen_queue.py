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
                results[feats.card_id] = await synth(feats, card)
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

    def secs(card_id: str) -> float:
        a = cache.get_audio(card_id)
        return a.seconds if a else 0.0

    current = sum(secs(c.id) for c in queue_cards)
    added: list[CardFeatures] = []

    if current < target_sec:
        # Each source list is ALREADY impact-sorted (sorted in place / kept sorted by
        # weekly insertion), so MERGE the per-list runs (~cross-list comparisons) instead
        # of a full re-sort of the combined pool (~n log n). Comparisons are sequential
        # LLM calls, so this is the ~8x first-run speedup.
        cards_by_id = {}
        per_list_feats = []
        for lid in source_list_ids:
            lst = [c for c in client.get_cards(lid) if c.id not in in_queue]
            for c in lst:
                cards_by_id[c.id] = c
            per_list_feats.append([f for f in await enricher.aenrich_many(lst) if f.ok])
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
                    continue
            if asset is None:
                continue
            client.move_card(f.card_id, queue_id, pos="bottom")
            added.append(f)
            in_queue.add(f.card_id)
            current += asset.seconds

    # Keep the whole queue ordered by impact (top = listen next). The existing queue is
    # already sorted (last run) and `added` came out of the merge in sorted order, so merge
    # those two presorted runs rather than re-sorting the whole queue from scratch.
    queue_feats = await enricher.aenrich_many(queue_cards)
    final = await merge_presorted([queue_feats, added], comparator.acompare)
    for i, f in enumerate(final):
        client.set_card_position(f.card_id, (i + 1) * 1000.0)

    reached = current >= target_sec
    if log:
        log.info(f"queue now {current/3600:.1f}h ({len(final)} items), "
                 f"added {len(added)}, reached_target={reached}")
    return {"queue_id": queue_id, "hours": current / 3600, "target_hours": target_hours,
            "added": [f.card_id for f in added], "reached_target": reached,
            "size": len(final)}
