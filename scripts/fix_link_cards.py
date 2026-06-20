"""Turn raw-URL Trello cards into proper "link cards" (page title + cover thumbnail).

Cards whose NAME is a bare URL show no preview. The cards that DO preview (e.g.
"Project Mario") are Trello link cards: their name is the page title and an OG image
is uploaded as the card cover. This reproduces that for the bare-URL cards:

    1. fetch the page (browser UA, follow redirects)
    2. parse og:title / <title> and og:image
    3. rename the card to the title
    4. download the OG image and upload it as the cover (the thumbnail)

Cards with no usable title/image (github.io with no OG tags, X, dead mailing-list
redirects) are left as-is. Renaming is safe: the article URL is still reachable via the
attachment added by fix_link_previews.py, which is how the pipeline reads the link.

Page fetches run in parallel (read-only); Trello mutations run sequentially through the
client's rate limiter. Dry-run by default; --apply mutates. The old names are recorded in
outputs/link_card_fixes.json (undo manifest).

Run:
    uv run python scripts/fix_link_cards.py                 # dry run, 3 reading lists
    uv run python scripts/fix_link_cards.py --apply
    uv run python scripts/fix_link_cards.py --list system1 --apply
"""
from __future__ import annotations

import argparse
import json
import re
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urljoin

import requests
from lxml import html as lxml_html

from counterfactual_podcast import config
from counterfactual_podcast.extract import _HTTP_HEADERS, find_url
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.trello import TrelloClient

LISTS = {
    "system1": config.SYSTEM1_LIST_ID,
    "system2": config.SYSTEM2_LIST_ID,
    "life_optim": config.LIFE_OPTIM_LIST_ID,
}
DEFAULT_LISTS = ["system1", "system2", "life_optim"]

_BARE_URL = re.compile(r"^https?://\S+$")
_MAX_IMG_BYTES = 12 * 1024 * 1024  # skip absurdly large images


def _resolve(name: str) -> str:
    return LISTS.get(name, name)


def _clean_title(t: str) -> str:
    return re.sub(r"\s+", " ", t).strip()[:480]


def og_meta(html_bytes: bytes, page_url: str):
    """Return (title|None, image_url|None) parsed from OG/twitter/<title> tags."""
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
    return (_clean_title(title) if title else None), img


def fetch_card_meta(card):
    """Read-only: fetch the page + (optionally) the OG image bytes. Returns a dict."""
    url = find_url(card) or card.url
    out = {"card_id": card.id, "old_name": card.name, "url": url,
           "title": None, "image_url": None, "image": None, "mime": None, "note": ""}
    if not url:
        out["note"] = "no url"
        return out
    try:
        r = requests.get(url, headers=_HTTP_HEADERS, timeout=20, allow_redirects=True)
        r.raise_for_status()
        title, img_url = og_meta(r.content, r.url)
        out["title"], out["image_url"] = title, img_url
    except Exception as e:  # noqa: BLE001
        out["note"] = f"fetch fail: {type(e).__name__}"
        return out
    if out["image_url"]:
        try:
            ir = requests.get(out["image_url"], headers=_HTTP_HEADERS, timeout=20)
            ir.raise_for_status()
            ctype = (ir.headers.get("Content-Type") or "").split(";")[0].strip()
            if ctype.startswith("image") and 0 < len(ir.content) <= _MAX_IMG_BYTES:
                out["image"], out["mime"] = ir.content, ctype
            else:
                out["note"] = f"img skipped ({ctype}, {len(ir.content)}b)"
        except Exception as e:  # noqa: BLE001
            out["note"] = f"img fail: {type(e).__name__}"
    return out


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true", help="actually mutate (default: dry run)")
    ap.add_argument("--list", action="append", dest="lists",
                    help="system1/system2/life_optim or raw id; repeatable")
    ap.add_argument("--workers", type=int, default=12, help="parallel page fetchers")
    args = ap.parse_args()

    log = setup_logging("fix-link-cards")
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    names = args.lists or DEFAULT_LISTS

    # Collect bare-URL cards across all requested lists.
    targets = []  # (list_name, card)
    for name in names:
        cards = client.get_cards(_resolve(name))
        bare = [c for c in cards if _BARE_URL.match(c.name.strip())]
        log.info(f"[{name}] {len(cards)} cards, {len(bare)} bare-URL cards to upgrade")
        targets += [(name, c) for c in bare]

    # Parallel read-only fetch of title + image for each.
    log.info(f"fetching OG metadata for {len(targets)} cards ({args.workers} workers)...")
    with ThreadPoolExecutor(max_workers=args.workers) as ex:
        metas = list(ex.map(lambda nc: (nc[0], fetch_card_meta(nc[1])), targets))

    # Sequential Trello mutations (respect the rate limiter).
    plan = []
    renamed = covered = skipped = 0
    for list_name, m in metas:
        rec = {"list": list_name, **{k: m[k] for k in
               ("card_id", "old_name", "url", "title", "image_url", "note")},
               "did_rename": False, "did_cover": False}
        if not m["title"] and not m["image"]:
            skipped += 1
            log.info(f"  skip {m['card_id']} ({(m['url'] or '')[:50]}) — {m['note'] or 'no title/img'}")
            plan.append(rec)
            continue
        if args.apply:
            try:
                if m["title"]:
                    client.set_name(m["card_id"], m["title"])
                    rec["did_rename"] = True
                    renamed += 1
                if m["image"]:
                    ext = ".png" if "png" in (m["mime"] or "") else ".jpg"
                    client.upload_cover(m["card_id"], m["image"], f"cover{ext}", m["mime"])
                    rec["did_cover"] = True
                    covered += 1
                log.info(f"  ✓ {m['card_id']} -> {repr((m['title'] or m['old_name'])[:55])}"
                         f"{' +cover' if rec['did_cover'] else ''}")
            except Exception as e:  # noqa: BLE001
                rec["note"] = (rec["note"] + f"; apply fail: {type(e).__name__}: {e}").strip("; ")
                log.warning(f"  FAIL {m['card_id']}: {type(e).__name__}: {e}")
        else:
            if m["title"]:
                renamed += 1
            if m["image"]:
                covered += 1
            log.info(f"  would set {m['card_id']} -> {repr((m['title'] or '(keep)')[:55])}"
                     f"{' +cover' if m['image'] else ''}")
        plan.append(rec)

    out = config.OUTPUTS / "link_card_fixes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(plan, indent=2))
    verb = "renamed/covered" if args.apply else "would rename/cover"
    log.info(f"{verb}: {renamed} titles, {covered} covers, {skipped} skipped. "
             f"Manifest -> {out}")
    print("APPLY_DONE" if args.apply else "DRYRUN_DONE")


if __name__ == "__main__":
    main()
