"""One-time sort: rank a list by counterfactual impact and (optionally) reorder it.

Per list: get cards -> enrich (extract + digest, cached) -> merge_sort with the LLM
pairwise comparator -> Copeland re-rank the top COPELAND_HEAD (stabilize the head) ->
write JSON snapshots (reversible) -> if --apply, reorder cards in place + write an
idempotent description rank marker.

SAFETY: defaults to dry-run. Nothing touches the board unless --apply is passed. A
pre-sort snapshot is always written first so a bad sort is reversible from JSON.
"""
from __future__ import annotations

import argparse
import asyncio
import datetime as dt
import json

from .. import config
from ..cache import Cache
from ..models import Card, CardFeatures
from ..sort import copeland_rank, merge_sort

_LIST_NAMES = {
    config.SYSTEM1_LIST_ID: "system1",
    config.SYSTEM2_LIST_ID: "system2",
    config.LIFE_OPTIM_LIST_ID: "life_optim",
}
_LISTS = {v: k for k, v in _LIST_NAMES.items()}


def _now() -> str:
    return dt.datetime.now().strftime("%Y%m%d-%H%M%S")


def _tiebreak(f: CardFeatures):
    return (f.est_minutes, f.card_id)


def _why(f: CardFeatures) -> str:
    snippet = (f.digest or "").strip().replace("\n", " ")
    return (snippet[:80] + "…") if len(snippet) > 80 else snippet


async def sort_list(client, cache, enricher, comparator, list_id, *,
                    apply: bool = False, copeland_head: int = config.COPELAND_HEAD,
                    log=None) -> dict:
    name = _LIST_NAMES.get(list_id, list_id)
    cards = client.get_cards(list_id)
    by_id: dict[str, Card] = {c.id: c for c in cards}
    if log:
        log.info(f"[{name}] {len(cards)} cards — enriching…")

    feats = await enricher.aenrich_many(cards)

    config.OUTPUTS.mkdir(parents=True, exist_ok=True)
    ts = _now()
    pre_path = config.OUTPUTS / f"oneshot_{name}_{ts}_pre.json"
    pre_path.write_text(json.dumps(
        [{"card_id": c.id, "name": c.name, "pos": c.pos} for c in cards],
        ensure_ascii=False, indent=2))

    if log:
        log.info(f"[{name}] ranking via pairwise merge sort…")
    ranked = await merge_sort(feats, comparator.acompare)
    if copeland_head and len(ranked) > 1:
        head = await copeland_rank(ranked[:copeland_head], comparator.acompare,
                                   tiebreak_key=_tiebreak)
        ranked = head + ranked[copeland_head:]

    post = [{"rank": i + 1, "card_id": f.card_id, "title": f.title,
             "est_minutes": f.est_minutes, "ok": f.ok, "why": _why(f)}
            for i, f in enumerate(ranked)]
    post_path = config.OUTPUTS / f"oneshot_{name}_{ts}_post.json"
    post_path.write_text(json.dumps(post, ensure_ascii=False, indent=2))

    summary = {"list": name, "count": len(ranked), "applied": False,
               "pre_snapshot": str(pre_path), "post_snapshot": str(post_path),
               "top": post[:15]}

    if not apply:
        if log:
            log.info(f"[{name}] DRY RUN — snapshots written, board untouched.")
        return summary

    if log:
        log.info(f"[{name}] APPLYING new order to the board ({len(ranked)} cards)…")
    for i, f in enumerate(ranked):
        client.set_card_position(f.card_id, (i + 1) * 1000.0)
        card = by_id.get(f.card_id)
        if card is not None:
            client.set_rank_marker(card, i + 1, f.est_minutes, _why(f))
    summary["applied"] = True
    return summary


async def run(list_keys, apply: bool, log=None) -> list[dict]:
    from ..enrich import Enricher
    from ..llm_compare import Comparator
    from ..trello import TrelloClient

    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    cache = Cache(config.CACHE_DB)
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)
    comparator = Comparator(cache=cache, profile_doc=profile)

    out = []
    for key in list_keys:
        out.append(await sort_list(client, cache, enricher, comparator,
                                   _LISTS[key], apply=apply, log=log))
    return out


def main() -> None:
    ap = argparse.ArgumentParser(description="One-time counterfactual-impact sort")
    ap.add_argument("--list", choices=["system1", "system2", "life_optim", "all"],
                    default="life_optim")
    ap.add_argument("--all", action="store_true", help="sort all three lists")
    ap.add_argument("--apply", action="store_true",
                    help="actually reorder the board (default: dry run)")
    args = ap.parse_args()

    from ..logging_setup import setup_logging
    log = setup_logging("oneshot")

    keys = ["system1", "system2", "life_optim"] if (args.all or args.list == "all") \
        else [args.list]
    results = asyncio.run(run(keys, apply=args.apply, log=log))
    for r in results:
        log.info(f"{r['list']}: {r['count']} cards, applied={r['applied']}")
        log.info("  top 5: " + " | ".join(
            f"#{t['rank']} {t['title'][:40]}" for t in r["top"][:5]))


if __name__ == "__main__":
    main()
