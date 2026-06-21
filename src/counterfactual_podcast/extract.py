"""Turn a Trello Card into extracted, reading-time-estimated text.

`extract(card)` dispatches on the card's URL (if any):

    no URL          -> kind="text"  (use the card name/desc itself)
    hard domain     -> kind="hard"  (X / YouTube / known paywall; ok=False)
    PDF / arxiv pdf -> kind="pdf"   (download + pypdf)
    otherwise       -> kind="html"  (trafilatura clean extraction)

Contract: this function NEVER raises. Any fetch/parse failure degrades to a
populated ExtractedContent with ok=False, kind="hard", note=<error>, and the
card name as fallback text — so downstream ranking always gets a row.
"""
from __future__ import annotations

import io
import re
from typing import Callable, Optional
from urllib.parse import urlparse

from . import config
from .models import Card, ExtractedContent
from .titles import resolve_title

# --- constants ------------------------------------------------------------

_URL_RE = re.compile(r"https?://[^\s<>\"')]+", re.IGNORECASE)

# Many sites (and Cloudflare's bot protection) block default/library user-agents,
# which is why trafilatura.fetch_url silently returned nothing for ~21% of cards.
# Fetch with a real browser UA via requests, then hand the HTML to trafilatura.
_BROWSER_UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
               "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36")
_HTTP_HEADERS = {
    "User-Agent": _BROWSER_UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
}

# Domains we cannot reliably extract: JS-only social, video, known paywalls.
HARD_DOMAINS: frozenset[str] = frozenset(
    {
        "x.com",
        "twitter.com",
        "youtube.com",
        "youtu.be",
        "nytimes.com",
        "wsj.com",
    }
)


# --- small helpers --------------------------------------------------------

def est_minutes(word_count: int) -> int:
    """Reading-time estimate in whole minutes (ranking denominator)."""
    return round(word_count / config.WPM_READING)


def find_url(card: Card) -> Optional[str]:
    """First http(s) URL found in card.name, else in card.desc, else None."""
    for blob in (card.name, card.desc):
        if not blob:
            continue
        m = _URL_RE.search(blob)
        if m:
            return m.group(0).rstrip(".,);]")
    return None


def _domain(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    return host


def is_hard(url: str) -> bool:
    """True if the URL's (registrable-ish) host is a known hard domain."""
    host = _domain(url)
    if not host:
        return False
    if host in HARD_DOMAINS:
        return True
    # match subdomains too: m.youtube.com, mobile.twitter.com, www.nytimes.com
    return any(host == d or host.endswith("." + d) for d in HARD_DOMAINS)


def _is_pdf_url(url: str) -> bool:
    low = url.lower().split("?", 1)[0].split("#", 1)[0]
    return low.endswith(".pdf") or "arxiv.org/pdf" in low


def _arxiv_abs_to_pdf(url: str) -> str:
    """Rewrite arxiv.org/abs/X -> arxiv.org/pdf/X (best-effort, idempotent)."""
    if "arxiv.org/abs/" in url:
        return url.replace("/abs/", "/pdf/", 1)
    return url


# Below this many chars, favor_precision likely over-trimmed a real article; retry with
# favor_recall and keep whichever is longer. (Comments are dropped in BOTH passes, so the
# recall pass can't re-introduce the comment-bloat that include_comments=False prevents.)
_MIN_EXTRACT_CHARS = 500


def _extract_main_text(html: str, extractor=None) -> str:
    """Two-pass article extraction: precise first, fall back to high-recall when the
    precise pass comes back thin/empty. ``extractor`` is injectable for tests."""
    if extractor is None:
        import trafilatura
        extractor = trafilatura.extract
    precise = extractor(html, include_comments=False, favor_precision=True) or ""
    if len(precise.strip()) >= _MIN_EXTRACT_CHARS:
        return precise
    recall = extractor(html, include_comments=False, favor_recall=True) or ""
    return recall if len(recall.strip()) > len(precise.strip()) else precise


# --- default real fetcher -------------------------------------------------

def _default_fetch(url: str) -> dict:
    """Real network fetch. Returns {text, title, kind, content_type, raw}.

    Only used in production; tests inject their own `fetch`.
    """
    import requests
    import trafilatura

    pdf_hint = _is_pdf_url(url)
    content_type = ""

    if not pdf_hint:
        # Cheap HEAD to detect PDFs served from non-.pdf URLs.
        try:
            head = requests.head(url, allow_redirects=True, timeout=20,
                                 headers=_HTTP_HEADERS)
            content_type = (head.headers.get("content-type") or "").lower()
        except Exception:
            content_type = ""

    if pdf_hint or "application/pdf" in content_type:
        resp = requests.get(url, timeout=60, headers=_HTTP_HEADERS)
        resp.raise_for_status()
        return {
            "kind": "pdf",
            "raw": resp.content,
            "content_type": content_type or "application/pdf",
            "text": "",
            "title": "",
        }

    # Fetch HTML with a browser UA (recovers UA/bot-blocked sites), then let
    # trafilatura parse the HTML string. Fall back to trafilatura.fetch_url.
    html = ""
    try:
        resp = requests.get(url, timeout=30, headers=_HTTP_HEADERS,
                            allow_redirects=True)
        resp.raise_for_status()
        ct = (resp.headers.get("content-type") or "").lower()
        if "application/pdf" in ct:  # some sites only reveal PDF on GET
            return {"kind": "pdf", "raw": resp.content, "content_type": ct,
                    "text": "", "title": ""}
        html = resp.text
    except Exception:
        html = ""
    if not html:
        html = trafilatura.fetch_url(url) or ""
    if not html:
        raise RuntimeError(f"could not fetch {url}")

    text = _extract_main_text(html)
    title = author = published = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = getattr(meta, "title", "") or ""
            author = (getattr(meta, "author", "") or "").split(";")[0].strip()
            published = (getattr(meta, "date", "") or "").strip()
    except Exception:
        pass
    return {
        "kind": "html",
        "text": text,
        "title": title,
        "author": author,
        "published": published,
        "content_type": content_type or "text/html",
        "raw": html,
    }


def _parse_pdf_bytes(data: bytes) -> str:
    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data))
    parts = []
    for page in reader.pages:
        parts.append(page.extract_text() or "")
    return "\n".join(parts).strip()


# --- builders -------------------------------------------------------------

def _build(
    *,
    card_id: str,
    title: str,
    text: str,
    kind: str,
    ok: bool,
    note: str = "",
    author: str = "",
    published: str = "",
) -> ExtractedContent:
    text = text or ""
    wc = len(text.split())
    return ExtractedContent(
        card_id=card_id,
        title=title,
        text=text,
        word_count=wc,
        est_minutes=est_minutes(wc),
        kind=kind,
        ok=ok,
        author=author,
        published=published,
        note=note,
    )


def _abstract_card(card_id, title, description, domain, author="", published=""):
    """An 'abstract' row: a paywalled/blocked page we couldn't fully read, but whose
    og:description gives a real summary to rank on. ok=False (no full text to voice), but
    a substantive digest source. est_minutes is a typical-article default, not the abstract's
    tiny length, so the impact-per-minute step isn't fooled into treating it as instant."""
    return ExtractedContent(
        card_id=card_id, title=title, text=description,
        word_count=len(description.split()),
        est_minutes=config.ABSTRACT_DEFAULT_MINUTES,
        kind="abstract", ok=False, author=author, published=published,
        note=f"metadata-only (paywall/blocked): {domain}",
    )


def _metadata_fallback(card: Card, url: str):
    """When full extraction fails, try og:title + og:description. Returns an 'abstract'
    ExtractedContent or None. Lazy import of web_meta avoids a circular import."""
    try:
        from .web_meta import fetch_meta
        m = fetch_meta(url)
    except Exception:  # noqa: BLE001
        return None
    desc = (m.get("description") or "").strip()
    if not desc:
        return None
    title = resolve_title([m.get("title"), card.name], url)
    return _abstract_card(card.id, title, desc, _domain(url),
                          author=m.get("author") or "", published=m.get("date") or "")


def extract_from_text(text: str, card_id: str = "", title: str = "") -> ExtractedContent:
    """Wrap bare text (no URL) as an ExtractedContent of kind 'text'."""
    return _build(
        card_id=card_id,
        title=title or (text.strip().splitlines()[0] if text.strip() else ""),
        text=text,
        kind="text",
        ok=True,
    )


def extract(card: Card, *, fetch: Optional[Callable[[str], dict]] = None) -> ExtractedContent:
    """Extract content for a card. Never raises; see module docstring."""
    fetch = fetch or _default_fetch
    # URL may be in name/desc, or (most reading cards) carried on card.url from a
    # Trello attachment resolved by TrelloClient.get_cards.
    url = find_url(card) or (card.url or None)

    # 1. No URL -> treat the card itself as the text.
    if not url:
        body = card.name
        if card.desc:
            body = f"{card.name}\n\n{card.desc}" if card.name else card.desc
        return _build(
            card_id=card.id,
            title=card.name,
            text=body,
            kind="text",
            ok=True,
        )

    # 2. Hard domain -> can't extract the body, but try the og:description abstract.
    if is_hard(url):
        fb = _metadata_fallback(card, url)
        if fb is not None:
            return fb
        return _build(
            card_id=card.id,
            title=resolve_title([card.name], url),
            text=card.name,
            kind="hard",
            ok=False,
            note=f"hard source: {_domain(url)}",
        )

    url = _arxiv_abs_to_pdf(url)

    # 3 & 4. Fetch (PDF or HTML). Any failure degrades gracefully.
    try:
        result = fetch(url)
        kind = result.get("kind", "html")

        if kind == "pdf":
            raw = result.get("raw")
            text = result.get("text") or ""
            if not text and raw is not None:
                text = _parse_pdf_bytes(raw)
            if not text:
                raise RuntimeError("empty PDF text")
            return _build(
                card_id=card.id,
                title=resolve_title([card.name, result.get("title", "")], url),
                text=text,
                kind="pdf",
                ok=True,
            )

        text = result.get("text") or ""
        if not text.strip():
            raise RuntimeError("empty extraction")
        return _build(
            card_id=card.id,
            title=resolve_title([card.name, result.get("title", "")], url),
            text=text,
            kind="html",
            ok=True,
            author=result.get("author", ""),
            published=result.get("published", ""),
        )
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        fb = _metadata_fallback(card, url)
        if fb is not None:
            return fb
        return _build(
            card_id=card.id,
            title=resolve_title([card.name], url),
            text=card.name,
            kind="hard",
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
