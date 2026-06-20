"""Rebuild the published podcast: clean titles, spoken title intros, priority order.

One-off to bring the ALREADY-published feed up to the new behavior (new runs do this
automatically). Steps, all against the cloud R2 cache (pulled, mutated, pushed back so
the pairwise comparisons are preserved):

  1. Backfill cache titles: any extracted/digest title that is a raw URL is replaced with
     the card's current (OG-renamed) Trello name, or a fetched OG title for queue cards,
     or a humanized URL. Fixes both the feed titles and the spoken intros, now + future.
  2. Re-synthesize every queue episode through Google TTS with the title spoken first,
     and upload the MP3 to R2 (parallel across episodes — Google is API-bound).
  3. Regenerate + publish the RSS feed with priority-encoded pubDates (top = newest).

Run:
    GOOGLE_APPLICATION_CREDENTIALS=<key.json> uv run python scripts/rebuild_podcast.py --apply
    (omit --apply for a dry run: backfill plan + synth list, no R2 writes)
"""
from __future__ import annotations

import argparse
import tempfile
from concurrent.futures import ThreadPoolExecutor

from counterfactual_podcast import config
from counterfactual_podcast.audio import _signpost_text, audio_duration_seconds
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.extract import find_url
from counterfactual_podcast.listen_queue import episodes_for_queue
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.models import AudioAsset
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.rss import build_feed
from counterfactual_podcast.titles import (
    format_month_year, is_urlish, resolve_title, source_domain)
from counterfactual_podcast.tts.google_engine import GoogleEngine
from counterfactual_podcast.trello import TrelloClient
from counterfactual_podcast.web_meta import fetch_meta

ALL_LISTS = [config.SYSTEM1_LIST_ID, config.SYSTEM2_LIST_ID, config.LIFE_OPTIM_LIST_ID]


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="mutate cache + R2 (default: dry run)")
    ap.add_argument("--workers", type=int, default=6, help="parallel episode synths")
    args = ap.parse_args()
    log = setup_logging("rebuild-podcast")

    c = r2_client()
    bucket, prefix = config.R2_BUCKET, config.PODCAST_PREFIX
    tmp = tempfile.mktemp(suffix=".sqlite3")
    c.download_file(bucket, "state/cache.sqlite3", tmp)
    cache = Cache(tmp)
    cl = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    qid = cl.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    queue_cards = cl.get_cards(qid)
    queue_ids = {c_.id for c_ in queue_cards}

    # ---- 1. Backfill cache titles ----------------------------------------
    # For queue cards, fetch a fresh OG title (best quality, they're never renamed).
    # For all other cards, the live Trello name is already an OG title from fix_link_cards.
    log.info(f"fetching page metadata (title/author/date) for {len(queue_cards)} queue cards...")
    with ThreadPoolExecutor(max_workers=args.workers * 2) as ex:
        metas = dict(ex.map(
            lambda card: (card.id, fetch_meta(card.url or card.name)), queue_cards))
    og_titles = {cid: m.get("title") for cid, m in metas.items()}

    all_cards = list(queue_cards)
    for lid in ALL_LISTS:
        all_cards += cl.get_cards(lid)

    fixed_titles = fixed_meta = 0
    for card in all_cards:
        m = metas.get(card.id, {})
        good = resolve_title([og_titles.get(card.id), card.name], url=find_url(card) or card.url)
        ec = cache.get_extracted(card.id)
        if ec and good and not is_urlish(good) and is_urlish(ec.title):
            ec.title = good
            cache.put_extracted(ec)
            fixed_titles += 1
        # backfill author/date into the extracted row so future runs speak them too
        if ec and (m.get("author") or m.get("date")):
            changed = False
            if m.get("author") and not ec.author:
                ec.author = m["author"]; changed = True
            if m.get("date") and not ec.published:
                ec.published = m["date"]; changed = True
            if changed:
                cache.put_extracted(ec)
                fixed_meta += 1
        dg = cache.get_digest(card.id)
        if dg and good and not is_urlish(good) and is_urlish(dg.title):
            dg.title = good
            cache.put_digest(dg, "title-backfill")
            fixed_titles += 1
    log.info(f"backfilled {fixed_titles} url-ish titles, {fixed_meta} author/date rows")

    # ---- 2. Re-synthesize queue episodes with spoken title intro ---------
    engine = GoogleEngine()
    if args.apply:
        engine._get_client()  # pre-warm so parallel threads don't race the lazy init
    out_dir = config.OUTPUTS / "audio_rebuild"
    out_dir.mkdir(parents=True, exist_ok=True)

    # Read everything from the cache in THIS thread (SQLite is single-thread); the pool
    # workers only synthesize + upload (no cache access), then we write audio rows here.
    jobs, err = [], 0
    for card in queue_cards:
        ec = cache.get_extracted(card.id)
        if not ec or not ec.ok or not ec.text:
            err += 1
            log.info(f"  skip {card.id}: no usable text")
            continue
        d = cache.get_digest(card.id)
        url = find_url(card) or card.url
        title = resolve_title([d.title if d else None, ec.title, card.name], url=url)
        jobs.append((card.id, title, ec.author, source_domain(url),
                     format_month_year(ec.published), ec.text))

    def synth_one(job):
        cid, title, author, src, date, text = job
        out = out_dir / f"{cid}.mp3"
        if not args.apply:
            return (cid, title, None, "(dry)")
        engine.synthesize(
            _signpost_text(text, title=title, author=author, source=src, date=date), out)
        secs = audio_duration_seconds(out)
        c.upload_file(str(out), bucket, f"{prefix}/{cid}.mp3",
                      ExtraArgs={"ContentType": "audio/mpeg"})
        return (cid, title, secs, str(out))

    log.info(f"{'synthesizing' if args.apply else 'would synthesize'} "
             f"{len(jobs)} episodes ({args.workers} workers)...")
    done = 0
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        for cid, title, secs, note in ex.map(synth_one, jobs):
            if secs is not None:
                cache.put_audio(AudioAsset(card_id=cid, path=note,
                                           seconds=secs, engine=engine.name))
            done += 1
            tag = f"{secs:.0f}s" if secs is not None else note
            log.info(f"  {'✓' if args.apply else '·'} {cid} [{tag}] {repr((title or '')[:55])}")
    log.info(f"synth: {done} ok, {err} skipped")

    # ---- 3. Regenerate + publish the feed (priority-ordered pubDates) ----
    eps = episodes_for_queue(cl, cache, queue_id=qid)  # already priority order; clean titles
    xml = build_feed(eps, public_base=(config.R2_PUBLIC_BASE or "").rstrip("/"), prefix=prefix)
    if args.apply:
        c.put_object(Bucket=bucket, Key=f"{prefix}/rss.xml",
                     Body=xml.encode("utf-8"), ContentType="application/rss+xml")
        c.upload_file(tmp, bucket, "state/cache.sqlite3")
        log.info(f"published feed ({len(eps)} episodes) + pushed cache to R2")
    else:
        (config.OUTPUTS / "rss.xml").write_text(xml, encoding="utf-8")
        log.info(f"dry run: wrote feed locally ({len(eps)} episodes), no R2 writes")
    print("APPLY_DONE" if args.apply else "DRYRUN_DONE")


if __name__ == "__main__":
    main()
