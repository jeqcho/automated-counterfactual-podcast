#!/usr/bin/env python3
"""Drop cached pairwise comparisons made by a model we no longer use.

WHY THIS EXISTS: the `pairwise` table is keyed on `(a_id, b_id)` only — there is no
model column in the key — so after a comparator swap a single ranking silently mixes
judgments from the old and new models, with no way to tell them apart. The rows DO
record which model produced them, so we can evict selectively.

This is destructive and EXPENSIVE to undo: every purged row is a comparison the next
sort has to pay for again (~$0.005-0.02 each). Dry run by default; `--apply` to write.

    uv run python scripts/purge_stale_pairwise.py                  # report only
    uv run python scripts/purge_stale_pairwise.py --apply          # local cache
    uv run python scripts/purge_stale_pairwise.py --apply --r2     # pull R2, purge, push

Without --r2 this touches only the local cache, which the cloud will OVERWRITE from R2
on its next run. Use --r2 to make the purge stick for the button-triggered pipeline.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from counterfactual_podcast import config  # noqa: E402
from counterfactual_podcast.cache import pull_cache_from_r2, push_cache_to_r2  # noqa: E402


def main() -> int:
    ap = argparse.ArgumentParser(description="Purge pairwise rows from retired models")
    ap.add_argument("--apply", action="store_true", help="actually delete (default: dry run)")
    ap.add_argument("--r2", action="store_true",
                    help="pull the cache from R2 first and push it back after")
    ap.add_argument("--keep", action="append", default=[],
                    help="extra model id to treat as current (repeatable)")
    args = ap.parse_args()

    if args.r2:
        got = pull_cache_from_r2()
        print(f"pulled cache from R2: {got}")
        if not got:
            print("  (no cache in R2 / R2 unconfigured — refusing to push a local-only DB)")
            return 1

    path = config.CACHE_DB
    if not Path(path).exists():
        print(f"no cache at {path}")
        return 1

    keep = {config.CLAUDE_MODEL, config.CLAUDE_MODEL_ESCALATE, *args.keep}
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        "SELECT model, COUNT(*) n FROM pairwise GROUP BY model ORDER BY n DESC"
    ).fetchall()
    total = sum(r["n"] for r in rows)
    print(f"\ncache: {path}\n{total} cached comparisons; keeping models: {sorted(keep)}\n")
    stale = 0
    for r in rows:
        # A deterministic-fallback row records the model that was asked, so it is purged
        # with its model — correct: the next run re-asks and may now get a real verdict.
        status = "KEEP" if r["model"] in keep else "PURGE"
        if status == "PURGE":
            stale += r["n"]
        print(f"  {status:5}  {r['n']:>6}  {r['model'] or '(none)'}")

    print(f"\n{stale} stale rows ({stale / total:.0%} of cache)" if total else "\nempty cache")
    if not stale:
        conn.close()
        return 0
    if not args.apply:
        print("DRY RUN — re-run with --apply to delete (and --r2 to persist to the cloud)")
        conn.close()
        return 0

    placeholders = ",".join("?" * len(keep))
    cur = conn.execute(f"DELETE FROM pairwise WHERE model NOT IN ({placeholders})",
                       tuple(sorted(keep)))
    conn.commit()
    conn.execute("VACUUM")
    conn.close()
    print(f"deleted {cur.rowcount} rows")

    if args.r2:
        print(f"pushed cache to R2: {push_cache_to_r2()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
