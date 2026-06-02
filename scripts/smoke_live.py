"""Non-mutating live smoke test of the real Scenario-C ranking pipeline.

Reads a small sample of real cards (READ ONLY — never reorders the board), enriches
them with real extraction + Haiku digests, ranks them with the real Sonnet pairwise
comparator, and prints the result. Uses a throwaway smoke cache. Spend << $1.
"""
from __future__ import annotations

import asyncio
import sys
import time

from counterfactual_podcast import config
from counterfactual_podcast.cache import Cache
from counterfactual_podcast.enrich import Enricher
from counterfactual_podcast.llm_compare import Comparator
from counterfactual_podcast.sort import merge_sort
from counterfactual_podcast.trello import TrelloClient

N = int(sys.argv[1]) if len(sys.argv) > 1 else 8


async def main():
    t0 = time.time()
    client = TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN)
    cache = Cache(config.OUTPUTS / "smoke_cache.sqlite3")
    profile = config.PROFILE_DOC.read_text(encoding="utf-8")
    enricher = Enricher(cache=cache, profile_doc=profile)
    comparator = Comparator(cache=cache, profile_doc=profile)

    cards = client.get_cards(config.LIFE_OPTIM_LIST_ID)[:N]
    print(f"Read {len(cards)} Life Optimization cards (read-only).")
    print("Enriching (real extraction + Haiku digests)…")
    feats = await enricher.aenrich_many(cards)
    okc = sum(1 for f in feats if f.ok)
    print(f"  enriched in {time.time()-t0:.0f}s — {okc}/{len(feats)} extractable\n")
    for f in feats:
        print(f"  [{'ok ' if f.ok else 'HARD'}] {f.est_minutes:>2}min  {f.title[:55]}")
        print(f"         digest: {f.digest[:110]}")

    print("\nRanking via real pairwise merge sort (Sonnet + Opus on close calls)…")
    t1 = time.time()
    ranked = await merge_sort(feats, comparator.acompare)
    print(f"  ranked in {time.time()-t1:.0f}s\n")
    print("=== RANKED BY COUNTERFACTUAL IMPACT (top first) ===")
    for i, f in enumerate(ranked, 1):
        print(f"  #{i:>2}  ({f.est_minutes:>2}min)  {f.title[:60]}")

    # show a couple of decisions
    print("\nSample pairwise decisions (from cache):")
    shown = 0
    for a in range(len(ranked)):
        for b in range(a + 1, len(ranked)):
            r = cache.get_pairwise(ranked[a].card_id, ranked[b].card_id)
            if r and shown < 4:
                wt = next(f.title for f in ranked if f.card_id == r.winner_id)
                print(f"  step{r.step}: '{wt[:35]}' — {r.why[:60]}")
                shown += 1
    print(f"\nTotal smoke wall-clock: {time.time()-t0:.0f}s")


if __name__ == "__main__":
    asyncio.run(main())
