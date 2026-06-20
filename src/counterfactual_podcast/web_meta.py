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


def parse_meta(html_bytes: bytes, page_url: str) -> dict:
    """Parse {title, image, author, date} from OG / twitter / meta / <title> tags.
    Any field may be None. ``date`` is the raw ISO string if present."""
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
    author = meta("author", "article:author", "og:article:author")
    if author and (author.startswith("http") or author.startswith("@")):
        author = None   # article:author URLs / @handles aren't real author names
    date = meta("article:published_time", "og:article:published_time",
                "article:modified_time", "date", "pubdate", "dc.date")
    if not date:
        d = doc.xpath("//time/@datetime")
        date = d[0].strip() if d and d[0].strip() else None
    return {"title": _clean_ws(title) if title else None, "image": img,
            "author": _clean_ws(author) if author else None, "date": date}


def og_meta(html_bytes: bytes, page_url: str):
    """Back-compat: (title|None, image_url|None)."""
    m = parse_meta(html_bytes, page_url)
    return m["title"], m["image"]


def fetch_meta(url: str, timeout: int = 20) -> dict:
    """Fetch ``url`` and parse_meta it. Never raises (all-None dict on failure)."""
    try:
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=timeout, allow_redirects=True)
        r.raise_for_status()
        return parse_meta(r.content, r.url)
    except Exception:  # noqa: BLE001
        return {"title": None, "image": None, "author": None, "date": None}


def fetch_og(url: str, timeout: int = 20):
    """Back-compat: (title|None, image_url|None)."""
    m = fetch_meta(url, timeout)
    return m["title"], m["image"]
