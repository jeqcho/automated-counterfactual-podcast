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

    # include_comments=False drops comment sections (WordPress/Disqus/etc) — without
    # it trafilatura appends the entire comment thread, ballooning some articles to
    # 10x their real length (e.g. an SSC post hit 551k chars = ~9h of audio of comments).
    # favor_precision trims residual nav/boilerplate.
    text = trafilatura.extract(html, include_comments=False,
                               favor_precision=True) or ""
    title = ""
    try:
        meta = trafilatura.extract_metadata(html)
        if meta is not None:
            title = getattr(meta, "title", "") or ""
    except Exception:
        title = ""
    return {
        "kind": "html",
        "text": text,
        "title": title,
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
        note=note,
    )


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

    # 2. Hard domain -> skip extraction, keep a fallback row.
    if is_hard(url):
        return _build(
            card_id=card.id,
            title=card.name,
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
                title=card.name or result.get("title", ""),
                text=text,
                kind="pdf",
                ok=True,
            )

        text = result.get("text") or ""
        if not text.strip():
            raise RuntimeError("empty extraction")
        return _build(
            card_id=card.id,
            title=card.name or result.get("title", ""),
            text=text,
            kind="html",
            ok=True,
        )
    except Exception as exc:  # noqa: BLE001 — contract: never raise
        return _build(
            card_id=card.id,
            title=card.name,
            text=card.name,
            kind="hard",
            ok=False,
            note=f"{type(exc).__name__}: {exc}",
        )
