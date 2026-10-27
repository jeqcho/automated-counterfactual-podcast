"""Publish ad-hoc text files as Listen Queue episodes at the TOP of the feed.

For one-off listening (e.g. a manual/document read aloud for a drive). Each input file:
first line = episode title, rest = speech-ready text. For each file this

  1. creates a Trello card in Listen Queue (above every existing card, in file order),
  2. seeds the R2 cache with extracted text + digest + audio rows for that card,
  3. synthesizes via Google TTS (or reuses ``--audio-dir/<id-prefix>-NN.mp3``) and
     uploads the MP3 to R2 at ``{prefix}/{card_id}.mp3``,

then republishes the feed from the queue. The cards are ordinary queue cards, so they
survive Phase 2 (it preserves the existing queue's order and only merges newcomers in)
and leave the feed the normal way: archive the card when finished.

Run:
    GOOGLE_APPLICATION_CREDENTIALS=<key.json> uv run python scripts/publish_adhoc_episodes.py \
        --id-prefix yc-pbm --source 'the YC User Manual' ep/*.txt [--apply]
"""
from __future__ import annotations

import argparse
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from counterfactual_podcast import config
from counterfactual_podcast.audio import _signpost_text, audio_duration_seconds
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.listen_queue import episodes_for_queue
from counterfactual_podcast.models import AudioAsset, CardFeatures, ExtractedContent
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.rss import build_feed
from counterfactual_podcast.trello import TrelloClient
from counterfactual_podcast.tts.google_engine import GoogleEngine

_WPM = 230  # matches extract's reading-speed estimate closely enough for est_minutes


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("files", nargs="+", type=Path)
    ap.add_argument("--id-prefix", required=True,
                    help="local mp3 name prefix, e.g. yc-pbm (-> yc-pbm-01.mp3)")
    ap.add_argument("--source", default="", help="spoken source, e.g. 'the YC User Manual'")
    ap.add_argument("--audio-dir", type=Path,
                    help="reuse already-synthesized <id-prefix>-NN.mp3 files from here")
    ap.add_argument("--workers", type=int, default=8)
    ap.add_argument("--apply", action="store_true", help="mutate Trello + R2 (default: dry run)")
    args = ap.parse_args()

    eps = []
    for i, f in enumerate(args.files, 1):
        title, _, body = f.read_text(encoding="utf-8").partition("\n")
        eps.append((f"{args.id_prefix}-{i:02d}", title.strip(), body.strip()))
    for name, title, body in eps:
        print(f"  {name}  {len(body.split()):>5} words  {title}")
    if not args.apply:
        print("DRYRUN_DONE")
        return

    c = r2_client()
    bucket, prefix = config.R2_BUCKET, config.PODCAST_PREFIX
    audio_dir = args.audio_dir or Path(tempfile.mkdtemp(prefix="adhoc-eps-"))
    audio_dir.mkdir(parents=True, exist_ok=True)

    # ---- 1. audio (synth only what --audio-dir doesn't already have) ----
    engine = GoogleEngine()
    todo = [e for e in eps if not (audio_dir / f"{e[0]}.mp3").exists()]
    if todo:
        engine._get_client()  # pre-warm before threads

        def synth(ep):
            name, title, body = ep
            engine.synthesize(_signpost_text(body, title=title, source=args.source),
                              audio_dir / f"{name}.mp3")
            print(f"  ✓ synth {name}  {title}", flush=True)

        with ThreadPoolExecutor(max_workers=args.workers) as ex:
            list(ex.map(synth, todo))

    # ---- 2. cards at the top of Listen Queue, in file order ----
    cl = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    qid = cl.ensure_list(config.LISTEN_QUEUE_LIST_NAME)
    existing = cl.get_cards(qid)
    top = min((x.pos for x in existing), default=1000.0)
    step = top / (len(eps) + 1)

    tmp = tempfile.mktemp(suffix=".sqlite3")
    c.download_file(bucket, "state/cache.sqlite3", tmp)
    cache = Cache(tmp)
    for i, (name, title, body) in enumerate(eps, 1):
        cid = cl.create_card(qid, title, pos=step * i)
        if args.source:
            cl.update_desc(cid, f"Ad-hoc episode from {args.source}.")
        mp3 = audio_dir / f"{name}.mp3"
        secs = audio_duration_seconds(mp3)
        words = len(body.split())
        est = max(1, round(words / _WPM))
        cache.put_extracted(ExtractedContent(card_id=cid, title=title, text=body,
                                             word_count=words, est_minutes=est,
                                             kind="text", ok=True, note="adhoc"))
        cache.put_digest(CardFeatures(cid, title, est, body[:800], "text", True), "adhoc")
        cache.put_audio(AudioAsset(card_id=cid, path=str(mp3), seconds=secs,
                                   engine=engine.name))
        c.upload_file(str(mp3), bucket, f"{prefix}/{cid}.mp3",
                      ExtraArgs={"ContentType": "audio/mpeg"})
        print(f"  ✓ card {cid}  {secs / 60:.1f} min  {title}", flush=True)

    # ---- 3. republish the feed from the queue + push the cache ----
    feed = episodes_for_queue(cl, cache, queue_id=qid)
    xml = build_feed(feed, public_base=(config.R2_PUBLIC_BASE or "").rstrip("/"), prefix=prefix)
    c.put_object(Bucket=bucket, Key=f"{prefix}/rss.xml", Body=xml.encode("utf-8"),
                 ContentType="application/rss+xml")
    c.upload_file(tmp, bucket, "state/cache.sqlite3")
    print(f"published feed ({len(feed)} episodes) + pushed cache")
    print("APPLY_DONE")


if __name__ == "__main__":
    main()
