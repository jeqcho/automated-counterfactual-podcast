# Budget Analysis — Ranking all 623 cards (one-time sort)

**Decision: Scenario C** (digest pre-pass + pairwise, Sonnet comparator + Opus escalation). ~$35.

## Pricing (Anthropic, confirmed 2026-06, per MTok)

| Model | Input | Cache read | Output | Notes |
|---|---|---|---|---|
| Sonnet 4.6 | $3 | $0.30 | $15 | comparator workhorse |
| Opus 4.8 | $5 | $0.50 | $25 | close-call escalation; ~35% more tokens (new tokenizer) |
| Haiku 4.5 | $1 | $0.10 | $5 | enrichment digests |

Prompt cache: 5-min write = 1.25× input, **cache read = 0.1× input**. Batch API = 50% off (async).

## The work

~6,700 LLM pairwise calls = ~4,370 merge-sort comparisons (301 + 272 + 50 cards) + ~2,340 Copeland head-stabilization comparisons (top-40 per list). Each card is enriched once.

## Why "a few dollars" was wrong, and the fix

The profile doc is cached cheaply, but a naive comparator ships **two full article excerpts** per call, and each article appears in ~9–18 comparisons → article text is ~80% of cost. The fix (Scenario C): a **one-time enrichment round** summarizes each article once into a ~150-token impact-digest; comparisons then ship tiny digests. Cuts input tokens ~5×.

## Scenarios

| # | Approach | Comparator | Article shown as | Est. cost | Wall-clock |
|---|---|---|---|---|---|
| A | Naive (full excerpts) | Sonnet + Opus | 1,200-word excerpts | ~$110 | ~25–35 min |
| B | Shrink excerpts | Sonnet + Opus | 400-word excerpts | ~$55 | ~20–30 min |
| **C** | **Digest (chosen)** | **Sonnet + Opus** | **~150-tok digest** | **~$35** | **~30–45 min** |
| D | Digest + Haiku bulk | Haiku → Sonnet escalate | digest | ~$15 | ~30–45 min |
| E | Digest + Batch API | Sonnet (batched parts) | digest | ~$30 | hours (async) |

### Scenario C breakdown
- Enrichment: 623 Haiku digests ≈ **~$2** (extraction is $0 LLM).
- Comparisons (Sonnet, ~6,700): ≈ **~$24**.
- Opus escalation (~15% close calls): ≈ **~$8**.
- **Total ≈ ~$35.** ±30% on Opus-escalation rate and average article length.

## Other notes

- Article fetching/extraction (623 pages) is **$0** LLM cost — ~3–5 min bandwidth, cached after first run.
- Estimates assume the prompt cache stays warm (5-min TTL refreshed by hits — true at concurrency 12).
- Re-runs are near-free (SQLite cache of digests + pairwise results).
- Weekly job reuses all cached digests; only new inbox cards get enriched — cents per week.
- `est_minutes` (reading time, 230 wpm) is the ranking denominator; the 20h listen queue uses **measured audio seconds** (mutagen), a different number.
- Switch to Scenario D anytime via `CLAUDE_MODEL=claude-haiku-4-5-20251001`.
