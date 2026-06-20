"""Fetch a page's OpenGraph metadata (title + preview image).

Shared by the Trello link-card scripts and the podcast rebuild: both need a clean
human title (and sometimes a cover image) for a URL. Network-bound, so it lives
outside the pure ``titles`` helpers.
"""
from __future__ import annotations

from urllib.parse import urljoin

import requests
from lxml import html as lxml_html

from .extract import _HTTP_HEADERS
from .titles import _clean_ws


def og_meta(html_bytes: bytes, page_url: str):
    """Return (title|None, image_url|None) from OG / twitter / <title> tags."""
    doc = lxml_html.fromstring(html_bytes)

    def meta(*props):
        for p in props:
            for xp in (f'//meta[@property="{p}"]/@content',
                       f'//meta[@name="{p}"]/@content'):
                r = doc.xpath(xp)
                if r and (r[0] or "").strip():
                    return r[0].strip()
        return None

    title = meta("og:title", "twitter:title")
    if not title:
        t = doc.xpath("//title/text()")
        title = t[0].strip() if t and t[0].strip() else None
    img = meta("og:image", "og:image:url", "og:image:secure_url",
               "twitter:image", "twitter:image:src")
    if img:
        img = urljoin(page_url, img)
    return (_clean_ws(title) if title else None), img


def fetch_og(url: str, timeout: int = 20):
    """Fetch ``url`` and return (title|None, image_url|None). Never raises."""
    try:
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return og_meta(r.content, r.url)
    except Exception:  # noqa: BLE001
        return None, None
