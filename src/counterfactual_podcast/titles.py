"""Resolve a clean, human-readable episode/card title.

Many cards' name (and therefore their cached extraction title) is just the raw
article URL, which is ugly in a podcast app and jarring to hear read aloud. This
module picks the best available human title from a list of candidates, falling
back to a slug derived from the URL. Pure / no network — the one-off backfill
scripts do the OpenGraph fetch; the pipeline uses these helpers offline.
"""
from __future__ import annotations

import re
from urllib.parse import unquote, urlparse

_URLISH = re.compile(r"^\s*https?://", re.IGNORECASE)
_EXT = re.compile(r"\.(html?|php|aspx?|jsp|pdf)$", re.IGNORECASE)
# leading date-ish / numeric id tokens in a slug (e.g. "2026-2-28-when-ai-...")
_LEAD_NUM = re.compile(r"^(\d+[-_]?)+")


def _clean_ws(s: str) -> str:
    """Collapse whitespace and cap length (titles can be long meta strings)."""
    return re.sub(r"\s+", " ", s).strip()[:480]


def is_urlish(s: str | None) -> bool:
    """True if the string is (starts as) a bare URL."""
    return bool(s) and bool(_URLISH.match(s))


def humanize_url(url: str) -> str:
    """Derive a readable title from a URL's last path segment (best-effort)."""
    if not url:
        return ""
    try:
        p = urlparse(url)
    except ValueError:
        return url
    segs = [s for s in (p.path or "").split("/") if s]
    if not segs:                              # homepage URL: just use the host
        return (p.hostname or url).removeprefix("www.")
    slug = _EXT.sub("", segs[-1])
    slug = unquote(slug)
    slug = _LEAD_NUM.sub("", slug)           # drop leading date / id prefix
    words = [w for w in re.split(r"[-_]+", slug) if w]
    title = " ".join(words).strip()
    if not title:
        return (p.hostname or url).removeprefix("www.")
    # Title-case only if it looks lowercased slug text (leave real casing alone).
    if title == title.lower():
        title = title.title()
    return title[:300]


_MONTHS = ("January", "February", "March", "April", "May", "June", "July",
           "August", "September", "October", "November", "December")


def format_month_year(iso_date: str) -> str:
    """'2017-11-25' / '2017-11-25T..' -> 'November 2017'. '' if unparseable."""
    m = re.match(r"\s*(\d{4})-(\d{2})", iso_date or "")
    if not m:
        return ""
    year, mon = m.group(1), int(m.group(2))
    if not 1 <= mon <= 12:
        return ""
    return f"{_MONTHS[mon - 1]} {year}"


def source_domain(url: str) -> str:
    """Readable source domain for spoken signposting (host minus www)."""
    if not url:
        return ""
    try:
        host = urlparse(url).hostname or ""
    except ValueError:
        return ""
    return host.removeprefix("www.")


def resolve_title(candidates, url: str = "") -> str:
    """First non-URL candidate (stripped); else a humanized URL; else best-effort.

    ``candidates`` is an ordered iterable of title strings (most-trusted first),
    any of which may be None/empty/url-ish.
    """
    cands = [c for c in candidates if c]
    for c in cands:
        if not is_urlish(c):
            return c.strip()
    if url:
        h = humanize_url(url)
        if h and not is_urlish(h):
            return h
    return (cands[0] if cands else url or "").strip()
