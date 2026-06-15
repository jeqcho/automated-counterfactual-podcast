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

## Latest (2026-06-02 → 03)
## Latest (2026-06-14) — DEPLOYED to Cloudflare (off the Mac)

The Worker + Container is **LIVE**: `https://counterfactual-podcast.chooijqweb.workers.dev`
(`/health` → 200). All 11 secrets set via `wrangler secret put` (sourced from `.env` +
the GCP key). Google Neural2 TTS validated live. Account = `chooijqweb@gmail.com`
(Workers Paid on). Cron triggers REMOVED — phases run on demand via the Trello buttons.

**The ONE remaining manual step:** point the two Trello Butler buttons at the workers.dev
URL (not trigger.chojeq.com):
- Phase 1 → `https://counterfactual-podcast.chooijqweb.workers.dev/phase1`
- Phase 2 → `https://counterfactual-podcast.chooijqweb.workers.dev/phase2`
each sending header `X-Trigger-Token: <TRIGGER_TOKEN from .env>`. First button press = the
real end-to-end test (Phase 1 is cheap; Phase 2 builds the ~20h queue via Google TTS ≈ ~$17 once).

**Deferred:** `trigger.chojeq.com` custom domain — the attach FAILED (Cloudflare API
`/domains/records` error: the old cloudflared tunnel CNAME conflicts and/or the chojeq.com
zone isn't on this account). Not chased; workers.dev is the stable path. The local
`cloudflared` tunnel is already DOWN (trigger.chojeq.com returns 1033) and the local
uvicorn server can be killed anytime (no longer in the loop).

**Deploy gotchas fixed today (see git log):**
- `worker/index.js`: the `@cloudflare/containers` `Container` base class starts the
  container with **empty env** (`envVars = {}`) — it does NOT inherit Worker secrets. Must
  forward them explicitly (FORWARD_ENV whitelist in the constructor) or every run fails
  with no creds. `config._materialize_google_credentials()` writes `GOOGLE_CREDENTIALS_JSON`
  → `/tmp/gcp-sa.json` on import (ADC).
- Adding `routes`/`custom_domain` to wrangler.jsonc WITHOUT `workers_dev: true` **disables
  the workers.dev URL** (broke reachability mid-deploy; restored by setting `workers_dev: true`).
- Node side now set up: `package.json` pins `@cloudflare/containers ^0.3.7` + wrangler;
  `npm install` before `wrangler deploy` (needs Docker running).

## Earlier (2026-06-02 → 03) — see `reports/MORNING-HANDOFF.md`
Two-phase intake live (Phase 1 button works); System 1/2/Life-Optim sorted in place
(before-copies on board); a ~3h Kokoro listen queue + podcast feed built overnight; Google
Neural2 TTS engine + full Cloudflare Containers deploy scaffolding + cache↔R2 durability.

## Status (overnight build, 2026-06-01)

- Plan: `reports/2026-06-01-counterfactual-podcast-plan.md` (reviewer-approved, 18 tasks).
- Budget: `reports/budget-analysis.md` (Scenario C, ~$35 for the full sort).
- Profile/ranking doc: **FINAL** → `private/jay-profile-for-article-classification.scoped.md`.
- Board backup: `private/board-backup-20260601-223725/` (all 4 lists, full JSON).
- `.env`: Trello + Anthropic + R2 creds present. **R2 public access (r2.dev) returns 403**
  — uploads/reads/deletes verified working; only the public-listen URL is pending the
  Cloudflare "Allow Access" toggle. RSS can be generated locally meanwhile.

### Build progress (overnight) — COMPLETE
All 18 plan tasks implemented + tested. **91 unit tests passing** (all LLM/network mocked).
- ✅ Foundation: `config`, `models`, `cache` (SQLite, symmetric pairwise).
- ✅ `trello` (backoff, rank marker, inbox + **attachment-URL** resolution).
- ✅ `extract` (HTML/PDF/text/hard, est_minutes) · `enrich` (Haiku digests, cached).
- ✅ `sort` (concurrent merge + Copeland + binary insert) · `llm_compare` (cached, Opus escalation).
- ✅ `tts/` (Kokoro + OpenAI) · `audio` · `listen_queue` · `rss` (R2) · `inbox` · `classify`.
- ✅ pipelines: `oneshot_sort`, `weekly` · `logging_setup` + `scripts/run_*.sh`.
- ✅ `reports/usage.md` runbook.

### Live smoke-test results (non-mutating, real APIs)
- **Model IDs** sonnet-4-6 / opus-4-8 / haiku-4-5 all verified live.
- **Ranking pipeline** (6 real Life-Optim cards, read-only): real extraction + Haiku
  digests + Sonnet pairwise → sensible impact order in ~40s, ~$0.10. Rationales cite
  real content. (`scripts/smoke_live.py`, logs/smoke_rank2-*.log)
- **Kokoro TTS**: synthesized a 10.7s MP3 in 6.1s = **1.76× real-time** (so ~11h compute
  for a 20h queue). Model at `data/kokoro-v1.0.onnx` (+voices). Needs `pydub` + ffmpeg.
- **RSS**: valid podcast feed generated locally, ordered items, correct enclosure URLs.
- **R2**: upload/read/delete verified; public r2.dev GET still 403 (needs Allow Access).
- **yield report** (free, no-LLM sweep over 351 System1+LifeOptim cards), after the
  browser-UA extraction fix: **287 extractable / 64 unreadable → ~105.2 audio-hours**
  (was 246 / ~91.7h before the fix recovered 41 cards). 20h queue reachable ~5× over.
  Remaining unreadable = 30 hard sources (X/YT/paywall) + 32 redirect/JS/paywall +
  2 malformed. `outputs/yield_report.json`.

### Resolved / known follow-ups
- ✅ **R2 public hosting works.** The earlier "403" was a false alarm — Cloudflare
  r2.dev blocks default library user-agents; a browser UA returns 200. Podcast apps
  (real UAs) fetch fine. No toggle was actually missing.
- ✅ **Extraction UA fix:** trafilatura's default UA was blocked by ~21% of sites; now
  `extract._default_fetch` fetches via requests with a browser UA then trafilatura-parses
  the HTML. Recovered 41 cards. (Same bot-protection class as the R2 finding.)
- Remaining 32 RuntimeError cards are real paywalls / tracking redirects / JS-only —
  low ROI to chase. Listen-queue full-reorder may move an in-progress top item; revisit
  if it bothers Jay.

### Constraint honored
NO board mutations and NO real 623-card sort were run. The real sort waits for the
Life-Optim pilot approval. Run it via `scripts/run_oneshot.sh --list life_optim` (dry
run) → review → `--apply`.

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
- **TTS:** Kokoro local for Mac runs; **Google Cloud Neural2** chosen for cloud hosting
  (pluggable: `get_engine("kokoro"|"openai"|"google")`). Reading-time drives *ranking*;
  measured audio seconds (mutagen) drive the *20h queue* math.
- **Deployment direction (decided, not yet built):** move off the Mac to **Cloudflare
  Containers** (Worker webhook+cron → Container) so nothing dies when the Mac is off.
  Plan + the state-persistence gotcha (SQLite cache + audio must move to R2) in
  `reports/deploy-cloudflare.md`. Until then, the buttons run via the local server +
  `trigger.chojeq.com` Cloudflare tunnel (ephemeral; for testing).
- **Delivery:** podcast RSS on Cloudflare R2 (zero egress). "Private" = unlisted +
  unguessable UUID path prefix.
- **Two-phase intake** (Inbox mixes reading links with todos): **Phase 1** (`triage.py`
  + `pipelines/phase1.py`) classifies each Inbox card read-vs-do (cheap Haiku, title/URL
  only) and moves only reading material → `To Be Processed` (todos stay in Inbox; title
  only, no markers). Jay reviews, then drags keepers into `▶ Ready to Process`. **Phase 2**
  (`pipelines/phase2.py`) drains that list → enrich → route+rank+markers → queue → publish.
  Both dry-run by default; Phase 2 is a poller (no-op when the trigger list is empty).
- **Trello buttons (Jay buying Premium):** Butler HTTP requests (Premium) let two board
  buttons POST to a local FastAPI **trigger server** (`server.py`, `/phase1` + `/phase2`,
  `X-Trigger-Token` auth), exposed to Trello via a **Cloudflare Tunnel**. Button press →
  runs the phase `--apply`. Setup: `reports/trigger-setup.md`. (Free-plan fallback was
  Butler card-moves + a poller; native Inbox isn't reachable by Butler, so a Phase-1
  button needs either Premium-HTTP or links in a board list.)

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
- **Kokoro speed (2026-06-14, M-series Mac, benchmarked `scripts/bench_kokoro.py`):**
  plain **CPU sequential is the fastest viable config at ~2.7–3.9× real-time** (variance
  is thermal/load). So **10h of audio ≈ ~3.3h of CPU synth on the Mac** (10h ÷ 3×). Two
  dead ends, both tested + ruled out — don't re-try:
  - **CoreML provider is SLOWER (2.24× vs CPU 2.68×)** — the kokoro ONNX graph fragments
    into **129 CoreML partitions** (only 1023 of 2256 nodes supported), so CPU↔ANE handoff
    overhead dominates. (Set via `ONNX_PROVIDER` env / `KOKORO_ONNX_PROVIDER` config knob,
    left as an escape hatch but default empty = CPU.)
  - **Parallel chunk synthesis is BROKEN** — Kokoro's espeak phonemizer has global state
    and is NOT thread-safe; concurrent `model.create()` corrupts it (`RuntimeError: number
    of lines in input and output must be equal`). Keep synth sequential per process.
  - The earlier "synth was crawling" was NOT the provider — it was (1) comment-bloated
    extractions (one SSC post = 551k chars = ~51h of audio of comments) and (2) over-small
    chunks. Both fixed (see comment-stripping below + chunk 350→400). No engine change needed.
- **NO per-card text cap** — we synthesize the FULL article (one article = one episode,
  however long). Jay's explicit call: don't prematurely cut content; speed comes from the
  provider + comment-stripping, not truncation. (Removed `AUDIO_TEXT_CAP_CHARS`.)
- **Comment sections are stripped at extraction** — `extract.py` calls
  `trafilatura.extract(html, include_comments=False, favor_precision=True)`. Default
  `include_comments=True` appended entire WordPress/Disqus threads (SSC "Outgroup" post:
  551k→50.5k chars, 91% was comments). NOTE: extractions cached BEFORE 2026-06-14 still
  hold the bloated text — re-extract comment-heavy cards before building a queue.
- **Off-Mac TTS = Google Neural2** (cloud, API-bound not compute-bound): 10h of audio ≈
  ~540k chars ≈ ~106 requests (5k chars each), parallelized → **minutes**, ~$30/mo at
  10h/wk ($16/M chars after 1M free/mo). Mac/Kokoro = free but ~3.3h CPU + Mac must stay awake.
- R2: S3 endpoint = `https://<R2_ACCOUNT_ID>.r2.cloudflarestorage.com`, region `auto`.
  Public reads require enabling the **r2.dev subdomain** ("Allow Access") in bucket
  settings — uploads work without it but public GET 403s until enabled.
- **Trello reading cards store the article URL as an ATTACHMENT, not in name/desc.**
  `TrelloClient.get_cards` requests `attachments=true&attachment_fields=url` and sets
  `card.url` to the first external http attachment; `extract` uses `find_url(card) or
  card.url`. Without this, every card fell back to title-only extraction (est_minutes=0)
  — caught by the overnight live smoke test. Some cards attach a trello.com-hosted PDF
  whose download needs auth → 401 → gracefully `ok=False` (excluded from TTS).

### Cloud-deploy findings (2026-06-14 night) — hard-won, read before touching the deploy
- **Container env is NOT inherited from the Worker.** `@cloudflare/containers` `Container`
  defaults `envVars = {}`. You MUST forward secrets explicitly in the subclass constructor
  (FORWARD_ENV whitelist in `worker/index.js`) or the FastAPI app boots with no creds.
- **THE BIG ONE — never run the pipeline on the FastAPI event loop.** The pipeline does
  blocking work (Trello HTTP with rate-limit `time.sleep`, SQLite, sort orchestration). On
  the request loop it starves `/health` + `/logs` → the container stops answering → **Cloudflare
  reaps the "unhealthy" container mid-run.** This (NOT OOM) caused the early "~38-min crashes".
  Fix: `server.start_run()` runs each phase in a **daemon thread with its own asyncio loop**;
  the request loop stays responsive. (If a future run still goes HTTP-silent under load, this
  regressed.)
- **`wrangler deploy` won't roll a new image onto a live singleton container.** With
  `max_instances:1`, a running instance blocks the rollout; worse, a follow-up deploy diffs
  against the *live* (un-rolled) image and silently re-pins the OLD image (the EDIT block shows
  the image line with no `+/-`). Symptom: new code/`instance_type` never takes effect; image
  stays the first tag. Fix that worked: change the Dockerfile (a unique `ENV CF_BUILD_MARKER`)
  to force a NEW image digest → wrangler must push + EDIT the app image. Verify with
  `wrangler containers info <app-id>` (check `image`, `vcpu`, `memory`).
- **`instance_type`: `lite` (~256MB/2GB/0.0625vcpu) is too small** — OOM/disk + painfully slow.
  Use `standard-1` (renamed from `standard`; ~4GB/8GB/0.5vcpu). Set in `wrangler.jsonc`.
- **Container logs do NOT appear in `wrangler tail`** (Worker-only). Observe a run via the
  token-protected `GET /logs` ring-buffer endpoint (also needs the Worker to whitelist the
  path — it 404s at the edge otherwise). Noisy httpx/anthropic loggers are raised to WARNING
  so /logs shows pipeline progress. `PYTHONUNBUFFERED=1` in the Dockerfile for CF logs too.
- **First Phase-2 run is SLOW (~40 min) and uncached:** the queue does a combined
  System1+LifeOptim ranking needing **cross-list** comparisons never computed by the per-list
  sorts. Merge-sort comparisons are **sequential** (~1.3/s), so concurrency-50 doesn't help.
  Once it completes + pushes cache to R2, future runs are cache-hit-fast. (Possible optimization:
  MERGE the two already-sorted lists (~n cross-list comparisons) instead of a full re-sort
  (~n log n) — check `listen_queue.ensure_listen_queue`.)
- **Cache is keyed by card identity only** (`extracted`/`digest`/`audio` PK `card_id`,
  `pairwise` PK `(a_id,b_id)`) — NO list/pos column. So moving cards between lists between
  Phase 1 and Phase 2 never stales the cache; list membership/order is read LIVE from Trello.
  NB: `pairwise` is NOT keyed by profile, so changing the profile doc does NOT invalidate
  cached comparisons (would serve stale rankings — re-rank manually if the profile changes).
- **Deploy state:** live at `https://counterfactual-podcast.chooijqweb.workers.dev`
  (account `chooijqweb`). Buttons must point at `…workers.dev/phase1|/phase2` + `X-Trigger-Token`
  (the `trigger.chojeq.com` custom-domain attach FAILED — DNS/zone conflict with the old
  tunnel; deferred). Crons removed (button-triggered). Feed URL =
  `{R2_PUBLIC_BASE}/{PODCAST_PREFIX}/rss.xml`.

## Pointers

- `reports/2026-06-01-counterfactual-podcast-plan.md` — the implementation plan.
- `reports/budget-analysis.md` — cost model & scenarios.
- `reports/inbox-access-finding.md` — how to read the Trello Inbox.
- `private/jay-profile-for-article-classification.scoped.md` — the ranking rubric (final).
- `private/board-backup-*/` — full board backups (restore source if a sort goes wrong).
