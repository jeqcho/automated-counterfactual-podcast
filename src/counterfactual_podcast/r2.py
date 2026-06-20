"""Cloudflare R2 (S3-compatible) client + helpers, shared by cache and rss.

R2's S3 endpoint is ``https://<account>.r2.cloudflarestorage.com``, region ``auto``,
signature v4. Isolated here so tests can monkeypatch ``r2_client`` and no real network
calls happen in the suite.
"""
from __future__ import annotations

from . import config


def r2_client():
    import boto3
    from botocore.config import Config

    endpoint = f"https://{config.R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    return boto3.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=config.R2_ACCESS_KEY_ID,
        aws_secret_access_key=config.R2_SECRET_ACCESS_KEY,
        region_name="auto",
        config=Config(signature_version="s3v4"),
    )


def r2_configured() -> bool:
    return all((config.R2_ACCOUNT_ID, config.R2_ACCESS_KEY_ID,
               config.R2_SECRET_ACCESS_KEY, config.R2_BUCKET))


def make_audio_checker(prefix: str | None = None):
    """Return a ``card_id -> bool`` that checks whether the episode MP3 already exists
    in R2 (``{prefix}/{card_id}.mp3``), or ``None`` if R2 is unconfigured.

    Makes audio durable across runs: a fresh cloud container has the cache rows (pulled
    from R2) but no local MP3 files, so the local-path resume check always misses and
    re-synthesizes everything. Checking R2 existence instead means only genuinely new
    episodes get rendered (saves TTS $ + time every run).
    """
    if not r2_configured():
        return None
    client = r2_client()
    bucket = config.R2_BUCKET
    pfx = prefix or config.PODCAST_PREFIX

    def exists(card_id: str) -> bool:
        try:
            client.head_object(Bucket=bucket, Key=f"{pfx}/{card_id}.mp3")
            return True
        except Exception:  # noqa: BLE001 — 404 / network -> treat as absent
            return False

    return exists
