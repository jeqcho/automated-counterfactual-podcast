"""De-duplicate a Trello list by article URL.

Phase 1 moves every linked Inbox card into 'To Be Processed', then calls this to archive
duplicates so the same article never fans out into the reading lists twice (which previously
had to be cleaned up by hand). A card is a duplicate if its URL matches:
  - another card EARLIER in the same target list (within-list dupe), or
  - any card already elsewhere on the board (the reading lists + Listen Queue) — so re-saving
    something already sorted is caught too.
The first/existing card is kept; the duplicate is archived.

URL matching is deliberately CONSERVATIVE — it strips only the fragment, a trailing slash, and
well-known tracking params (utm_*, fbclid, gclid, ...), but KEEPS meaningful query (so
`youtube.com/watch?v=A` ≠ `?v=B`, and newsletter links with distinct ids stay distinct). Better
to miss a near-dupe than to wrongly merge two different articles.
"""
from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .extract import find_url

# Query params that are pure tracking noise — drop them before comparing.
_TRACKING_PARAMS = frozenset({
    "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "utm_id",
    "fbclid", "gclid", "mc_cid", "mc_eid", "ref", "ref_src", "ref_url", "s", "t",
})


def url_key(card) -> str:
    """Canonical comparison key for a card's URL, or '' if it has none."""
    raw = (find_url(card) or (card.url or "")).strip()
    if not raw:
        return ""
    try:
        p = urlparse(raw.lower())
    except Exception:  # noqa: BLE001
        return raw.lower().rstrip("/")
    host = p.netloc[4:] if p.netloc.startswith("www.") else p.netloc
    path = p.path.rstrip("/")
    query = urlencode(sorted((k, v) for k, v in parse_qsl(p.query)
                             if k not in _TRACKING_PARAMS))
    return urlunparse(("", host, path, "", query, ""))


def dedup_list(client, target_list_id: str, against_list_ids=(), *,
               apply: bool = False, log=None) -> dict:
    """Archive duplicate cards in ``target_list_id``.

    A card is archived if its URL key already appeared in one of ``against_list_ids`` or
    earlier within the target list. Cards without a URL are left alone. Dry-run by default.
    """
    seen = set()
    for lid in against_list_ids:
        for c in client.get_cards(lid):
            k = url_key(c)
            if k:
                seen.add(k)

    archived = []
    for c in client.get_cards(target_list_id):
        k = url_key(c)
        if not k:
            continue
        if k in seen:
            if apply:
                client.archive_card(c.id)
            archived.append({"card_id": c.id, "name": c.name})
            if log:
                log.info(f"  [dedup] {'archived' if apply else 'would archive'} dup: {c.name[:50]}")
        else:
            seen.add(k)

    if log and archived:
        log.info(f"dedup: {len(archived)} duplicate card(s) "
                 f"{'archived' if apply else 'would be archived'}")
    return {"archived": len(archived), "archived_cards": archived, "applied": apply}
