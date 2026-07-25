#!/usr/bin/env python
"""Expand a multi-item reading-list card into individual, podcastable link cards.

The Trello Inbox often collects a *pasted list* (e.g. a tweet naming 9 essays) rather
than a link. Such a card is invisible to the pipeline: Phase 1's ``_has_link`` finds no
URL, so it never reaches ``To Be Processed`` and never becomes an episode.

This script creates one card per resolved item in ``To Be Processed``, with the article
URL as an **attachment** (the canonical location -- ``extract.find_url(card) or card.url``
reads it there; the card *name* stays a clean human title so RSS titles and the spoken
intro don't read out a URL).

Dry-run by default. ``--apply`` mutates the board and writes an undo manifest to
``outputs/`` listing every created card id.

Usage::

    uv run python scripts/split_reading_list_card.py            # dry run
    uv run python scripts/split_reading_list_card.py --apply
    uv run python scripts/split_reading_list_card.py --apply --archive-source
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import pathlib
import sys

from counterfactual_podcast import config
from counterfactual_podcast.trello import TrelloClient

# The source card this batch came from: Trello Inbox, created 2026-07-25,
# "post AGI classics to read during the weekend: ...".
SOURCE_CARD_ID = "6a65050470caa2c10a74ecbc"

# (title, url) -- every URL below was verified through counterfactual_podcast.extract:
# all return ok=True with a real word count (see the docstring table in the commit).
ITEMS: list[tuple[str, str]] = [
    # -- standalone pieces ------------------------------------------------------
    ("Scenarios for the Transition to AGI",
     "https://arxiv.org/abs/2403.12107"),                       # pdf   17,168w ~75m
    ("The Intelligence Curse",
     "https://intelligence-curse.ai/intelligence-curse.pdf"),   # pdf   21,537w ~94m
    ("AI-Enabled Coups: How a Small Group Could Use AI to Seize Power",
     "https://www.forethought.org/research/"
     "ai-enabled-coups-how-a-small-group-could-use-ai-to-seize-power"),  # html 18,179w ~79m
    ("The Ambition Singularity: Controlling AGI as the Only Power Game Worth Playing",
     "https://danfaggella.com/flex/"),                          # html   2,047w  ~9m
    ("How Long Before Superintelligence? (1997)",
     "https://nickbostrom.com/superintelligence"),              # html   7,720w ~34m
    ("The British Industrial Revolution in Global Perspective: "
     "How Commerce Created the Industrial Revolution",
     "https://www.nuffield.ox.ac.uk/users/allen/unpublished/econinvent-3.pdf"),  # pdf 19,591w ~85m
    ("AI Monotheism vs AI Polytheism",
     "https://www.beren.io/2026-01-07-AI-Monotheism-vs-AI-Polytheism/"),  # html 6,366w ~28m

    # -- Situational Awareness, split per chapter --------------------------------
    # The bundled PDF is 51,017 words (~5.7h of audio) in a single enclosure with no
    # seek points. The site serves the same text as 8 standalone chapters, so each
    # becomes its own resumable episode and gets ranked on its own merits.
    ("Situational Awareness I: From GPT-4 to AGI — Counting the OOMs",
     "https://situational-awareness.ai/from-gpt-4-to-agi/"),            # 9,462w ~41m
    ("Situational Awareness II: From AGI to Superintelligence — the Intelligence Explosion",
     "https://situational-awareness.ai/from-agi-to-superintelligence/"),  # 8,472w ~37m
    ("Situational Awareness IIIa: Racing to the Trillion-Dollar Cluster",
     "https://situational-awareness.ai/racing-to-the-trillion-dollar-cluster/"),  # 5,445w ~24m
    ("Situational Awareness IIIb: Lock Down the Labs — Security for AGI",
     "https://situational-awareness.ai/lock-down-the-labs/"),           # 4,974w ~22m
    ("Situational Awareness IIIc: Superalignment",
     "https://situational-awareness.ai/superalignment/"),               # 6,874w ~30m
    ("Situational Awareness IIId: The Free World Must Prevail",
     "https://situational-awareness.ai/the-free-world-must-prevail/"),  # 5,261w ~23m
    ("Situational Awareness IV: The Project",
     "https://situational-awareness.ai/the-project/"),                  # 5,013w ~22m
    ("Situational Awareness V: Parting Thoughts",
     "https://situational-awareness.ai/parting-thoughts/"),             # 1,333w  ~6m
]

# Deliberately NOT created:
#   Gradual Disempowerment -- already on the board 3x (System 2). Jay's call 2026-07-25.
#   Allen's 2009 Cambridge book -- no free full text (archive.org is DRM-borrow, 268w
#     extracted); substituted with his freely-hosted working paper above.


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true",
                    help="actually create the cards (default: dry run)")
    ap.add_argument("--archive-source", action="store_true",
                    help="also archive the original list card (needs the session cookie; "
                         "the card lives in the API-locked Inbox)")
    args = ap.parse_args(argv)

    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN,
                          session_cookie=config.TRELLO_SESSION_COOKIE)
    target = client.ensure_list(config.TO_BE_PROCESSED_LIST_NAME)

    print(f"target list: {config.TO_BE_PROCESSED_LIST_NAME} ({target})")
    print(f"{'APPLY' if args.apply else 'DRY RUN'} — {len(ITEMS)} cards\n")

    created: list[dict] = []
    for i, (title, url) in enumerate(ITEMS, 1):
        print(f"{i:>2}. {title}")
        print(f"    {url}")
        if not args.apply:
            continue
        # pos="bottom" preserves the ordering above; the ranker reorders later anyway.
        card_id = client.create_card(target, title, pos="bottom")
        client.add_attachment(card_id, url)
        created.append({"card_id": card_id, "title": title, "url": url})
        print(f"    created {card_id}")

    if not args.apply:
        print("\nDry run — nothing created. Re-run with --apply.")
        return 0

    if args.archive_source:
        try:
            client.archive_card(SOURCE_CARD_ID)
            print(f"\narchived source card {SOURCE_CARD_ID}")
        except Exception as exc:  # noqa: BLE001
            print(f"\ncould not archive source card {SOURCE_CARD_ID}: "
                  f"{type(exc).__name__}: {exc}")

    stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    out = pathlib.Path("outputs") / f"split-reading-list-{stamp}.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(
        {"source_card": SOURCE_CARD_ID, "target_list": target, "created": created},
        indent=2))
    print(f"\ncreated {len(created)} cards · undo manifest: {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
