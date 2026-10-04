"""Give bare-URL cards a readable name — and unwrap newsletter click-trackers.

Newsletter links arrive as Mailchimp click-trackers (``us.list-manage.com/<id>?e=…``,
``<org>.usN.list-manage.com/track/click?…``). As a card name that tells Jay nothing, and
the tracker is what every downstream step (extraction, dedup, the podcast title) would see.
Mailchimp answers a plain request with a 302 to the real article, so we read the
``Location`` header WITHOUT following it (following can land on a 403 paywall like NYT,
which would hide the destination too).

``tidy_cards`` is used by Phase 1 on every card it moves into 'To Be Processed', and by
``scripts/fix_tracker_cards.py`` for cards already on the board. For a bare-URL card:

  1. unwrap a tracker URL to the real article URL;
  2. attach the real URL (or the original one, if the card has no http attachment) BEFORE
     renaming, because once the name stops being a URL the pipeline reads the link from the
     attachment (``find_url(card) or card.url``);
  3. rename to the page's og:title / <title>; if the page blocks us, a tracker card still
     gets a slug title ("Ai Recursive Self Improvement — nytimes.com", or "ft.com article" for
     an id-only URL), since anything beats
     the tracker. A plain bare-URL card is left as-is when no title is found.

Fetches run in parallel threads (read-only); Trello writes run serially through the
client's rate limiter. Never raises per card — one bad link must not abort the batch.
"""
from __future__ import annotations

import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlparse

from .extract import _HTTP_HEADERS, find_url
from .titles import humanize_url, source_domain

_BARE_URL = re.compile(r"^https?://\S+$")
# Click-tracking redirectors whose URL says nothing about the article.
TRACKER_HOST_SUFFIXES = ("list-manage.com",)
# Hex id tokens news sites append to slugs ("…-ai-fund-4dbb00a4", FT's uuid paths).
_HEX_ID = re.compile(r"^(?=[0-9a-f]*\d)[0-9a-f]{4,}$", re.IGNORECASE)


def _slug_title(url: str) -> str:
    """URL-slug title with trailing hex id tokens dropped; '' if nothing readable is left."""
    words = humanize_url(url).split()
    while words and _HEX_ID.match(words[-1]):
        words.pop()
    if any(_HEX_ID.match(w) for w in words):     # an id-only path (FT uuids): unreadable
        return ""
    return " ".join(words)


def _fix_mojibake(title: str) -> str:
    """Undo UTF-8 read as Latin-1 ("Hereâ\x80\x99s" -> "Here's") when the page didn't
    declare its charset early enough for the HTML parser."""
    if not any(ch in title for ch in "âÃ"):
        return title
    try:
        return title.encode("latin-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return title


def is_tracker(url: str | None) -> bool:
    try:
        host = (urlparse(url or "").hostname or "").lower()
    except ValueError:
        return False
    return any(host == s or host.endswith("." + s) for s in TRACKER_HOST_SUFFIXES)


def is_bare_url(name: str | None) -> bool:
    return bool(name) and bool(_BARE_URL.match(name.strip()))


def unwrap(url: str, *, get=None, max_hops: int = 3) -> str:
    """Follow tracker redirects one hop at a time via the ``Location`` header, stopping at
    the first non-tracker URL. Returns ``url`` unchanged if it can't be resolved."""
    if get is None:
        import requests
        get = requests.get
    cur = url
    for _ in range(max_hops):
        if not is_tracker(cur):
            break
        try:
            r = get(cur, headers=_HTTP_HEADERS, timeout=20, allow_redirects=False)
        except Exception:  # noqa: BLE001
            break
        loc = r.headers.get("Location") if 300 <= r.status_code < 400 else None
        if not loc:
            break
        cur = loc
    return cur if not is_tracker(cur) else url


def plan_card(card, *, get=None, fetch_title=None) -> dict | None:
    """Read-only: work out the fix for one card. None if the card needs nothing."""
    if not is_bare_url(card.name):
        return None
    if fetch_title is None:
        from .web_meta import fetch_meta
        fetch_title = lambda u: fetch_meta(u)["title"]  # noqa: E731
    url = find_url(card) or card.url
    if not url:
        return None
    tracker = is_tracker(url)
    real = unwrap(url, get=get) if tracker else url
    try:
        title = fetch_title(real)
    except Exception:  # noqa: BLE001
        title = None
    if title:
        title = _fix_mojibake(title)
    if not title and tracker and real != url:
        slug, dom = _slug_title(real), source_domain(real)
        title = f"{slug} — {dom}" if slug and slug != dom else f"{dom} article"
    if not title and not (tracker and real != url):
        return None
    attach = None
    if real != url:
        attach = real                   # real article URL becomes the card's link
    elif not (card.url or "").startswith("http"):
        attach = url                    # keep the link reachable once the name changes
    return {"card_id": card.id, "old_name": card.name, "url": url, "real_url": real,
            "attach": attach, "title": title}


def tidy_cards(client, cards, *, apply: bool = False, log=None, workers: int = 12,
               get=None, fetch_title=None) -> list[dict]:
    """Plan (in parallel) and apply (serially) readable names for bare-URL cards."""
    todo = [c for c in cards if is_bare_url(c.name)]
    if not todo:
        return []
    with ThreadPoolExecutor(max_workers=max(1, min(workers, len(todo)))) as ex:
        plans = [p for p in ex.map(
            lambda c: plan_card(c, get=get, fetch_title=fetch_title), todo) if p]
    for p in plans:
        p["applied"] = False
        if apply:
            try:
                if p["attach"]:
                    client.add_attachment(p["card_id"], p["attach"])
                client.set_name(p["card_id"], p["title"])
                p["applied"] = True
            except Exception as e:  # noqa: BLE001
                p["error"] = f"{type(e).__name__}: {str(e)[:80]}"
                if log:
                    log.warning(f"  [title skip] {p['old_name'][:50]}: {p['error']}")
                continue
        if log:
            verb = "title" if apply else "would title"
            log.info(f"  [{verb}] {p['old_name'][:45]} -> {p['title'][:60]}")
    return plans
