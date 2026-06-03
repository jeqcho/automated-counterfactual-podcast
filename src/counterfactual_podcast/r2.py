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
