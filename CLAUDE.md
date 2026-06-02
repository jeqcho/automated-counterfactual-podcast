# Automated Counterfactual Podcast — Project Context

> **For future Claudes:** This file is the durable memory for this project. Read it first.
> **Keep it updated:** whenever you learn something non-obvious (a fixed gotcha, a new
> decision, an API quirk, a status change), edit the relevant section here. Don't let it
> go stale. Commit changes with the rest of your work.

## What this is

A personal automation for **Jay Chooi** (AI-safety researcher; MATS fellow, Harvard,
2026 Rhodes Scholar). It manages his Trello reading lists and turns them into a
priority-ordered, text-to-speech **"listen queue"** delivered as a private podcast.

Two jobs:
1. **One-time sort (today):** rank his 3 existing reading lists by *counterfactual
   impact* using LLM pairwise comparison, and reorder the Trello cards in place.
2. **Weekly automation:** pull links from his Trello Inbox → route each into one of the
   3 lists (impact-ranked via pairwise insertion) → keep a ≥20h TTS listen queue topped
   up → publish a podcast RSS feed. He listens top-first and archives when done.

## Status (as of 2026-06-01)

- **Planning: complete & reviewed.** Implementation: **not started.**
- Plan: `reports/2026-06-01-counterfactual-podcast-plan.md` (reviewer-approved, 18 tasks).
- Budget: `reports/budget-analysis.md` (Scenario C, ~$35 for the full sort).
- Inbox access solved: `reports/inbox-access-finding.md`.
- Profile/ranking doc: **FINAL** → `private/jay-profile-for-article-classification.scoped.md`.
- Board backup saved: `private/board-backup-20260601-223725/` (all 4 lists, full JSON).
- `.env`: Trello + Anthropic + R2 creds present. **R2 public access (r2.dev) still needs
  enabling** in the Cloudflare dash (uploads work; public GET returned 403 until enabled).
- Next: overnight build + non-mutating smoke tests; then tomorrow the real sort (after
  Jay approves a Life-Optimization pilot).

## Architecture (how it works)

- **Ranking = LLM pairwise comparison** (NOT cardinal scoring — Jay's explicit choice).
  Today: concurrent **merge sort** + **Copeland** re-rank of the top ~40 (washes out
  intransitive-comparator cycles). Weekly: **binary insertion** of new cards.
- **Scenario C (digest pre-pass):** a one-time **enrichment round** per card — extract
  article text (free) → `est_minutes` = words/230 (code) → a ~150-token **impact digest**
  via Haiku, written *through the profile lens*. Comparisons ship tiny digests, not full
  text → ~5× cheaper. Everything cached in SQLite (resumable, near-free re-runs).
- **Comparator:** Sonnet 4.6, escalating genuinely-close calls (step≥6) to Opus 4.8.
  Profile doc is prompt-cached across all calls.
- **TTS:** Kokoro local (pluggable to OpenAI/Fish/Qwen). Reading-time drives *ranking*;
  measured audio seconds (mutagen) drive the *20h queue* math.
- **Delivery:** podcast RSS on Cloudflare R2 (zero egress). "Private" = unlisted +
  unguessable UUID path prefix.

## Key facts & IDs

- Trello board **Home base** = `657f3741ecf6b2f7a40ef8df`; member `chooijeqin` =
  `5a8056af894b0bfba8179ee4`.
- Target lists: System 1 (lighter, "doesn't require system 2") =
  `683cb9f4387706ad70dc4299` (301 cards); System 2 (deep) =
  `683cb9e94b55936c9e9505a3` (272); Life Optimization = `69cffff85c64bd09a7c8cd7d` (50).
- **Trello Inbox:** do NOT call `/members/me/inbox` (401). Use
  `GET /1/members/me?fields=inbox` → `inbox.idList`, then treat it as a normal list.
  Resolve dynamically at runtime (don't hardcode the account-specific id).
- Listen queue tops up from **System 1 + Life Optim only** (System 2 excluded — needs
  focused reading). 20h is a **soft floor** (capped by extractable-content yield).
- `.env` (gitignored) holds: `TRELLO_API_KEY`, `TRELLO_TOKEN`, `ANTHROPIC_API_KEY`,
  `R2_ACCOUNT_ID`, `R2_ACCESS_KEY_ID`, `R2_SECRET_ACCESS_KEY`, `R2_BUCKET`,
  `R2_PUBLIC_BASE`.
- Anthropic account = **highest tier** → concurrency 12 is safe.

## Conventions & working agreements

- **uv** for everything (`uv add`, `uv run`). Python 3.12. Code in `src/`, tests in
  `tests/` (pytest, TDD). Outputs → `outputs/`, inputs → `data/`, charts → `plots/`.
- **Long-running jobs → tmux**, logs to `logs/` with timestamped names
  (`name-YYYYMMDD-HHMMSS.log`). Report ETA after ~2 min.
- **`private/` is gitignored** — board backups, profile docs, anything personal. Never
  commit it. `.env` is gitignored.
- **Commit AND push often** (not just at the end) — Jay's explicit preference. Work
  commits straight to `main`.
- Co-author trailer on commits: `Co-Authored-By: Claude Opus 4.8 (1M context) <noreply@anthropic.com>`.
- Board mutations: reorder cards in place; annotate via an **idempotent description
  marker** (`<!--cf-->[#rank · est min · why]<!--/cf-->`), NOT comments.
- Always write a JSON snapshot before mutating the board (sorts are reversible).

## Decisions locked

- Ranking: pairwise (merge sort today, binary insertion weekly); context = scoped
  profile doc; judge = Sonnet 4.6 + Opus 4.8 escalation; prompt-cached.
- Architecture: Scenario C (Haiku digest enrichment → digest-based comparisons).
- TTS = Kokoro (pluggable). Delivery = podcast RSS on R2 (UUID-unlisted privacy).
- Annotation = description marker. Rollout = Life-Optim pilot → approve → all 3 lists.

## Gotchas / learnings (append as you discover them)

- Trello "App" = the new name for "Power-Up"; token is generated via the
  `trello.com/1/authorize?...&key=...` URL, not a button.
- Trello rate limits ~100 req/10s/token, 300/10s/key → client needs 429/Retry-After
  backoff + token-bucket.
- Anthropic pricing (2026-06, /MTok): Sonnet 4.6 $3/$0.30 cache-read/$15; Opus 4.8
  $5/$0.50/$25 (+~35% tokens, new tokenizer); Haiku 4.5 $1/$0.10/$5. Cache read = 0.1×
  input; cost was dominated by article text → hence the digest pre-pass.
- Kokoro-82M was #1 on TTS Arena (Jan 2026); good for long-form. Needs `ffmpeg`
  (system prereq) for WAV→MP3.
- R2: S3 endpoint = `https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`.
  Public reads require enabling the **r2.dev subdomain** ("Allow Access") in bucket
  settings — uploads work without it but public GET 403s until enabled.

## Pointers

- `reports/2026-06-01-counterfactual-podcast-plan.md` — the implementation plan.
- `reports/budget-analysis.md` — cost model & scenarios.
- `reports/inbox-access-finding.md` — how to read the Trello Inbox.
- `private/jay-profile-for-article-classification.scoped.md` — the ranking rubric (final).
- `private/board-backup-*/` — full board backups (restore source if a sort goes wrong).
