"""Give Trello cards link previews by attaching their URL.

Trello renders a rich link preview only when the URL is a card ATTACHMENT. Cards
added via the Inbox / Chrome extension get the URL attached automatically; cards
whose link was pasted into the title or description have the URL as plain text and
therefore show no preview. This finds those cards and POSTs the URL as an attachment
so Trello fetches + renders the preview.

Detection is conservative: a card needs fixing iff it has NO http(s) attachment
(``card.url == ""``) but a URL is present in its name/desc (``find_url``). Cards that
already have any http attachment are left untouched (they already preview).

Dry-run by default (prints what it WOULD attach). Pass --apply to mutate the board.
Scans System 1, System 2, and Life Optimization unless --list is given.

Run:
    uv run python scripts/fix_link_previews.py                 # dry run, 3 reading lists
    uv run python scripts/fix_link_previews.py --apply         # actually attach
    uv run python scripts/fix_link_previews.py --list system1 --apply
"""
from __future__ import annotations

import argparse
import json

from counterfactual_podcast import config
from counterfactual_podcast.extract import find_url
from counterfactual_podcast.logging_setup import setup_logging
from counterfactual_podcast.trello import TrelloClient

# Friendly names -> list ids (also accepts a raw 24-hex list id directly).
LISTS = {
    "system1": config.SYSTEM1_LIST_ID,
    "system2": config.SYSTEM2_LIST_ID,
    "life_optim": config.LIFE_OPTIM_LIST_ID,
}
DEFAULT_LISTS = ["system1", "system2", "life_optim"]


def _resolve(name: str) -> str:
    return LISTS.get(name, name)  # fall through to a raw list id


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--apply", action="store_true",
                    help="actually attach URLs (default: dry run)")
    ap.add_argument("--list", action="append", dest="lists",
                    help="list name (system1/system2/life_optim) or raw id; "
                         "repeatable. Default: the 3 reading lists.")
    args = ap.parse_args()

    log = setup_logging("fix-link-previews")
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    names = args.lists or DEFAULT_LISTS

    planned = []  # records of {list, card_id, name, url} we attach (or would)
    for name in names:
        lid = _resolve(name)
        cards = client.get_cards(lid)
        needing = [(c, find_url(c)) for c in cards if not c.url]
        needing = [(c, u) for (c, u) in needing if u]
        log.info(f"[{name}] {len(cards)} cards, {len(needing)} missing a preview")
        for c, url in needing:
            planned.append({"list": name, "card_id": c.id,
                            "name": c.name[:60], "url": url})
            if args.apply:
                try:
                    client.add_attachment(c.id, url)
                    log.info(f"  +attach {c.id} ({c.name[:40]}) -> {url}")
                except Exception as e:  # noqa: BLE001 — one bad card must not stop the run
                    log.warning(f"  FAIL {c.id} ({c.name[:40]}): {type(e).__name__}: {e}")
            else:
                log.info(f"  would attach {c.id} ({c.name[:40]}) -> {url}")

    out = config.OUTPUTS / "link_preview_fixes.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(planned, indent=2))
    verb = "attached" if args.apply else "would attach"
    log.info(f"{verb} {len(planned)} previews. Plan written to {out}")
    print("APPLY_DONE" if args.apply else "DRYRUN_DONE")


if __name__ == "__main__":
    main()
