"""Backfill R2 audio for Listen-Queue episodes whose enclosure 404s.

Some queue cards were added by the earlier local Kokoro run; their audio lived on the Mac
and never reached R2, so the published feed lists them but their .mp3 enclosure 404s. This
synthesizes those missing cards with the (byte-safe) Google engine and uploads them to R2
at {PREFIX}/{card_id}.mp3 — making every feed episode playable.

Works from the R2 cache (not the local one) so it never clobbers the cloud's cache: pulls
it, reads each card's extracted text, synthesizes + uploads, records the audio asset, and
pushes the cache back with only those additions.

Run:  GOOGLE_APPLICATION_CREDENTIALS=<key.json> uv run python scripts/fix_missing_queue_audio.py
"""
from __future__ import annotations

import tempfile

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.models import AudioAsset
from counterfactual_podcast.r2 import r2_client
from counterfactual_podcast.tts.google_engine import GoogleEngine


def main():
    log = setup_logging("fix-missing-audio")
    c = r2_client()
    bucket = config.R2_BUCKET
    prefix = config.PODCAST_PREFIX

    # 1) Pull the R2 cache (cloud's state) to a temp file — we add to it, never clobber.
    tmp_cache = tempfile.mktemp(suffix=".sqlite3")
    c.download_file(bucket, "state/cache.sqlite3", tmp_cache)
    cache = Cache(tmp_cache)

    # 2) What's in R2 already + what the feed references.
    keys = []
    token = None
    while True:
        resp = c.list_objects_v2(Bucket=bucket, **({"ContinuationToken": token} if token else {}))
        keys += [o["Key"] for o in resp.get("Contents", [])]
        if not resp.get("IsTruncated"):
            break
        token = resp.get("NextContinuationToken")
    have_audio = {k.split("/")[-1][:-4] for k in keys if k.endswith(".mp3")}

    import re
    feed = c.get_object(Bucket=bucket, Key=f"{prefix}/rss.xml")["Body"].read().decode("utf-8")
    feed_ids = re.findall(r"/([0-9a-f]{24})\.mp3", feed)
    missing = [cid for cid in dict.fromkeys(feed_ids) if cid not in have_audio]
    log.info(f"feed has {len(set(feed_ids))} episodes; {len(have_audio)} audio in R2; "
             f"{len(missing)} missing -> synthesizing")

    engine = GoogleEngine()
    fixed = skipped = 0
    out_dir = config.OUTPUTS / "audio_backfill"
    out_dir.mkdir(parents=True, exist_ok=True)
    for cid in missing:
        ec = cache.get_extracted(cid)
        if not ec or not ec.ok or not ec.text:
            log.info(f"  skip {cid}: no usable cached text")
            skipped += 1
            continue
        try:
            out = out_dir / f"{cid}.mp3"
            engine.synthesize(ec.text, out)
            c.upload_file(str(out), bucket, f"{prefix}/{cid}.mp3",
                          ExtraArgs={"ContentType": "audio/mpeg"})
            fixed += 1
            log.info(f"  +{cid} ({(ec.title or '')[:40]}) uploaded -> {prefix}/{cid}.mp3")
        except Exception as e:  # noqa: BLE001
            log.info(f"  FAIL {cid}: {type(e).__name__}: {e}")
            skipped += 1

    # NB: we only upload the missing MP3 files (what the feed enclosures point at). We do NOT
    # push the cache back — the R2 cache is the cloud's; clobbering it could drop the cross-list
    # comparisons. (Audio cache stores local paths and is re-derived each run anyway.)
    log.info(f"DONE: {fixed} backfilled, {skipped} skipped.")
    print("BACKFILL_DONE")


if __name__ == "__main__":
    main()
