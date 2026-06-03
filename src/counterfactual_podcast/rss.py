"""Podcast RSS feed generation and R2 publishing.

Turns a priority-ordered list of episodes into a valid iTunes-extended RSS
feed (via ``feedgen``) and optionally uploads the audio + feed to Cloudflare R2.

"Private" delivery = an unguessable uuid4 hex path **prefix** ("unlisted").
Pin the prefix to keep a stable feed URL across re-publishes; otherwise a fresh
random one is minted each run.

The R2 client lives in :func:`r2_client` so tests can monkeypatch it — no real
network calls happen in the test suite.
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from feedgen.feed import FeedGenerator

from . import config


@dataclass
class QueueEpisode:
    """One podcast item, in listen (priority) order."""
    card_id: str
    title: str
    audio_path: str
    seconds: float
    url: str = ""  # optional source link


def _fmt_duration(seconds: float) -> str:
    """Format seconds as H:MM:SS (e.g. 3661 -> '1:01:01')."""
    total = int(round(seconds))
    h, rem = divmod(total, 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def _file_size(path: str) -> int:
    try:
        return Path(path).stat().st_size
    except OSError:
        return 0


def build_feed(
    episodes: list[QueueEpisode],
    *,
    feed_title: str = "Jay's Counterfactual Podcast",
    public_base: str = "",
    prefix: str = "",
) -> str:
    """Build a valid iTunes-extended podcast RSS XML string.

    One ``<item>`` per episode, in the given order (priority = listen order).
    Each item has an enclosure pointing at
    ``{public_base}/{prefix}/{card_id}.mp3`` and an ``<itunes:duration>``.
    """
    fg = FeedGenerator()
    fg.load_extension("podcast")

    base = public_base.rstrip("/")
    feed_link = f"{base}/{prefix}/rss.xml" if base else f"{prefix}/rss.xml"

    fg.title(feed_title)
    fg.link(href=feed_link, rel="self")
    fg.description(feed_title)
    fg.language("en")
    fg.podcast.itunes_category("Education")

    for ep in episodes:
        url = f"{base}/{prefix}/{ep.card_id}.mp3" if base else f"{prefix}/{ep.card_id}.mp3"
        # order='append' keeps items in priority (listen) order; feedgen
        # otherwise prepends (newest-first).
        fe = fg.add_entry(order="append")
        fe.id(ep.card_id)
        fe.guid(ep.card_id, permalink=False)
        fe.title(ep.title)
        if ep.url:
            fe.link(href=ep.url)
        fe.enclosure(url, str(_file_size(ep.audio_path)), "audio/mpeg")
        fe.podcast.itunes_duration(_fmt_duration(ep.seconds))

    return fg.rss_str(pretty=True).decode("utf-8")


from .r2 import r2_client, r2_configured as _r2_configured  # noqa: E402  (shared client)


def publish(
    episodes: list[QueueEpisode],
    *,
    prefix: Optional[str] = None,
    upload: bool = True,
) -> dict:
    """Build the feed, optionally upload to R2, always write it locally.

    Args:
        episodes: priority-ordered episodes.
        prefix: path prefix (unlisted privacy). Defaults to a random uuid4 hex.
            Pin it to keep a stable feed URL.
        upload: whether to upload audio + feed to R2. If True but R2 config is
            missing, nothing is uploaded (uploaded=0) and we still write locally.

    Returns:
        dict with prefix, feed_url, feed_xml, uploaded (count or 0).
    """
    if prefix is None:
        prefix = uuid.uuid4().hex

    public_base = (config.R2_PUBLIC_BASE or "").rstrip("/")
    xml = build_feed(episodes, public_base=public_base, prefix=prefix)

    uploaded = 0
    if upload and _r2_configured():
        client = r2_client()
        for ep in episodes:
            # With a stable prefix, audio uploaded in a prior run is already in R2; on a
            # fresh container the local file may be gone — skip it (enclosure still resolves).
            if not Path(ep.audio_path).exists():
                continue
            with open(ep.audio_path, "rb") as fh:
                client.put_object(
                    Bucket=config.R2_BUCKET,
                    Key=f"{prefix}/{ep.card_id}.mp3",
                    Body=fh.read(),
                    ContentType="audio/mpeg",
                )
            uploaded += 1
        client.put_object(
            Bucket=config.R2_BUCKET,
            Key=f"{prefix}/rss.xml",
            Body=xml.encode("utf-8"),
            ContentType="application/rss+xml",
        )

    # Always write the feed locally.
    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    (config.OUTPUTS / "rss.xml").write_text(xml, encoding="utf-8")

    feed_url = f"{public_base}/{prefix}/rss.xml" if public_base else f"{prefix}/rss.xml"
    return {
        "prefix": prefix,
        "feed_url": feed_url,
        "feed_xml": xml,
        "uploaded": uploaded,
    }
