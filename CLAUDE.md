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

## Latest (2026-06-15) — 🎧 PODCAST IS LIVE & WORKING (full cloud run succeeded)

Phase 2 ran end-to-end on Cloudflare and **published a working 42-episode feed** (all 42
enclosures fetch 200, ~20h of audio). The whole system is off the Mac and verified.

**Your feed URL (subscribe in Apple Podcasts → Mac → File → "Add a Show by URL"; syncs to iPhone):**
```
https://pub-cbe1a1411c65446c872416872b3c2403.r2.dev/4b1eb250c30c47558534e62b20620d25/rss.xml
```

**Your remaining manual task** (✅ subscribed to the feed already, 2026-06-20):
1. **Repoint the 2 Trello Butler buttons** to the workers.dev URLs (they still point at the
   dead `trigger.chojeq.com` tunnel):
   - Extract readables (Phase 1) → `https://counterfactual-podcast.chooijqweb.workers.dev/phase1`
   - Sort readables (Phase 2) → `https://counterfactual-podcast.chooijqweb.workers.dev/phase2`
   - header `X-Trigger-Token: <TRIGGER_TOKEN from .env>`.

Worker LIVE at `https://counterfactual-podcast.chooijqweb.workers.dev` (account `chooijqweb`,
Workers Paid). Container app `a03c203b…` on `standard-1`. 11 secrets set. Crons removed.
Observe any run live with `curl -H "X-Trigger-Token: …" .../logs`.

**Bugs found + fixed overnight (all committed; see git log + the gotchas section):**
1. Container started with empty env (didn't inherit Worker secrets) → forward in worker/index.js.
2. Pipeline blocked the event loop → container went HTTP-silent → Cloudflare reaped it →
   run pipeline in a daemon thread (server.py). Also unstuck `/logs`.
3. Google TTS 5000-byte limit crashed synth on run-on sentences → `byte_safe_chunks()`.
4. `wrangler deploy` wouldn't roll a new image onto the live singleton → force new digest
   (build marker) or, cleanest, `wrangler containers delete` + redeploy (fresh CREATE).
5. `lite` instance OOM/too-slow → `standard-1`.
6. PODCAST_PREFIX mismatch (.env `d922c67…` vs cloud secret `4b1eb250…`) → feed published at
   the cloud's prefix; aligned `.env` to it. **The live feed is at `4b1eb250…`.**
7. 19/42 episodes 404'd (old queue cards from the overnight LOCAL Kokoro run — audio never
   reached R2) → `scripts/fix_missing_queue_audio.py` synthesized + uploaded them. All 42 play now.

**Known follow-ups (NOT blocking; for when you're back):**
- ✅ **DONE (2026-06-20) — Slow first ranking:** `ensure_listen_queue` now MERGES the two
  already-sorted source lists via `sort.merge_presorted` (and merges existing-queue + added
  for the final re-rank) instead of a full `merge_sort` — ~cross-list comparisons, ~8× fewer
  sequential LLM calls on the first run.
- ✅ **DONE (2026-06-20) — Audio re-synthesized every cloud run:** `synthesize_card(r2_check=)`
  now reuses cached audio when the MP3 exists in R2 (`r2.make_audio_checker` head_objects
  `{prefix}/{card_id}.mp3`), not just on local disk. `make_synth` wires it. Fresh containers
  reuse R2 audio; only new episodes synth. (Caveat: changing synthesis logic — e.g. new
  signposting — won't auto-invalidate; force a rebuild via `scripts/rebuild_podcast.py`.)
- **PODCAST_PREFIX:** root cause of the .env/cloud divergence unknown; .env now matches cloud.
- **trigger.chojeq.com** custom domain still deferred (DNS/zone conflict); workers.dev works.
- **Long episodes:** some are 50–67 min (no-truncation policy). Revisit splitting if you want.

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

## Podcast feed UX (2026-06-20) — titles, spoken intros, priority order

Three feed behaviors, all live in the pipeline (new runs do them automatically) and
back-applied to the published 46-episode feed via `scripts/rebuild_podcast.py`:
- **Clean episode titles.** Half the episodes were titled with the raw article URL because
  extraction stored `card.name` (a bare URL) as the title. `titles.resolve_title([...], url)`
  picks the first non-URL candidate, else humanizes the URL slug. `extract.py` now prefers a
  real page `<title>` over a URL-ish card name. RSS title + spoken intro both use it. NB:
  resolve sites must pass `find_url(card) or card.url` as the url — some queue cards have no
  URL attachment, so `card.url` is empty and the URL only lives in the name.
- **Spoken title intro.** `audio._intro_text` prepends `"{title}.\n\n\n"` to the synth text
  (toggle `config.SPEAK_TITLE_INTRO`) so each episode announces itself — makes the boundary
  between back-to-back episodes obvious. Changing the intro means re-synthesizing.
- **Priority-encoded order.** Podcast apps sort by `pubDate`. `rss.build_feed` stamps each
  item (emitted in priority order, item 0 = top) with a pubDate stepping back from `now`
  (60s/step), so the app's default newest-first == counterfactual-impact order. Re-stamped
  every publish, so a NEW high-priority card lands at ~now (top), never "in the past"; only
  low-priority cards get older synthetic dates. Episodes you finish + archive leave the queue.
- One-off title backfill: `rebuild_podcast.py` rewrites url-ish cached titles (extracted +
  digest) from OG/renamed card names so legacy cache rows don't speak/show URLs on future runs.

**Finished-episode workflow (2026-06-20): feed is a QUEUE, not a library.** Jay listens
top-down in Apple Podcasts (priority = newest pubDate). When done, he **archives the card**
in `Listen Queue` (no separate history list — he didn't want one). Nothing auto-deletes — an
episode only leaves the feed when its card leaves `Listen Queue` AND Phase 2 republishes.
`episodes_for_queue` reads only the `Listen Queue` list and the queue tops up from
System1+LifeOptim, so an archived card is both out of the feed and won't be re-queued — zero
code needed. MP3s persist in R2 (orphaned, harmless), so a feed could be rebuilt later.

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
- **Model IDs** sonnet-4-6 / opus-4-8 / haiku-4-5 all verified live. *(Superseded
  2026-07-25: now sonnet-5 / opus-5 / haiku-4-5 — see the migration gotcha.)*
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
- **Comparator:** Sonnet 5, escalating genuinely-close calls (step≥6) to Opus 5 (see the
  2026-07-25 migration note); digests stay on Haiku 4.5 (there is no Haiku 5).
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
- **Two-phase intake** (Inbox mixes reading links with todos): **Phase 1**
  (`pipelines/phase1.py`) is now **LLM-FREE (simplified 2026-06-28)** — it moves EVERY Inbox
  card that has a link (`_has_link`: a URL in name/desc or on `card.url`) → `To Be Processed`,
  and leaves link-less cards (pure todos/notes) in the Inbox. No read-vs-do classification, no
  Haiku call. (Removed `triage.py`/`InboxTriager` — the old Haiku read-vs-do classifier; Jay's
  manual review of `To Be Processed` catches anything he doesn't want to read, so the LLM step
  wasn't worth the cost/latency.) **After moving, Phase 1 DEDUPS `To Be Processed`** (`dedup.py`,
  added 2026-06-28): it archives any card whose URL already appears earlier in the list OR
  elsewhere on the board (System1/2, Life Optim, Listen Queue), so the same article never fans
  out into the reading lists twice (previously a manual cleanup). Conservative URL key — strips
  www/trailing-slash/`utm_*`/`fbclid`/etc. but KEEPS meaningful query, so `youtube.com/watch?v=A`
  ≠ `?v=B` and distinct newsletter links aren't wrongly merged. Jay reviews `To Be Processed`
  and drags any wrong cards back to the Inbox. **Phase 2** (`pipelines/phase2.py`) then drains
  `To Be Processed` (the SAME list —
  no separate "▶ Ready to Process"; simplified 2026-06-20) → enrich → route+rank+markers →
  queue → publish. Both dry-run by default; Phase 2 is a no-op when `To Be Processed` is empty.
- **Trello buttons (Butler Premium):** two board buttons issue Butler HTTP-request POSTs to
  the FastAPI trigger server (`server.py`, `/phase1` + `/phase2`, `X-Trigger-Token` auth),
  which runs the phase `--apply`. The server now lives in the **Cloudflare Container** behind
  the Worker — buttons POST to `…workers.dev/phase1|/phase2` (NOT the old local
  `cloudflared` tunnel, which is dead and removed). Phases are mutually exclusive (see the
  cross-phase mutex gotcha). (The old local-tunnel setup guide `reports/trigger-setup.md` and
  `scripts/run_server.sh` were deleted 2026-06-28 — the local-uvicorn-+-tunnel path is gone.)

## Key facts & IDs

- Trello board **Home base** = `657f3741ecf6b2f7a40ef8df`; member `chooijeqin` =
  `5a8056af894b0bfba8179ee4`.
- Target lists: System 1 (lighter, "doesn't require system 2") =
  `683cb9f4387706ad70dc4299` (301 cards); System 2 (deep) =
  `683cb9e94b55936c9e9505a3` (272); Life Optimization = `69cffff85c64bd09a7c8cd7d` (50).
- **⚠️ Trello Inbox is now SESSION-COOKIE-ONLY (UPDATED 2026-07-12 — API tokens are fully
  locked out).** The Inbox lockdown that was "flaky ~50% 401" on 2026-06-28 hardened into a
  **100% hard block for API tokens** by 2026-07-12: `GET /1/lists/{inbox}/cards`,
  `/boards/{inbox_board}/cards`, and `/members/me/inbox` ALL return `401 "unauthorized member
  permission requested"` — even after re-authorizing the token WITH `account`/Member scope. It
  is NOT a scope gap; Trello architecturally excludes the personal Inbox from API tokens (only
  the logged-in web/mobile session can touch it; Inbox has no public REST endpoint and no Butler
  automation). **The working path is a logged-in web SESSION COOKIE**, which talks to
  `https://trello.com/1` (NOT `api.trello.com`):
  - **Read:** `GET trello.com/1/lists/{inboxList}/cards` with the `Cookie:` header.
  - **Move out:** `PUT trello.com/1/cards/{id}` with the cookie + a **form body** `idBoard,
    idList, dsc` — Trello's double-submit CSRF: the `dsc` value (itself one of the cookies) must
    ALSO be posted in the body (query-param dsc → 403 "CSRF detected"; body dsc → 200).
  - Implemented in `trello.py`: `TrelloClient(session_cookie=...)`, `has_session`,
    `_web_request` (adds Origin/Referer/UA + dsc, raises `InboxAuthError` on 401/403),
    `get_inbox_cards` (cookie read; inbox list id still resolved via the API token —
    `members/me?fields=inbox` works), `move_inbox_card`. `config.TRELLO_SESSION_COOKIE` holds the
    full cookie header; Phase 1 passes it in, uses `move_inbox_card`, and degrades gracefully
    (logs a refresh prompt + still dedups) if the cookie is dead.
  - **The cookie is `TRELLO_SESSION_COOKIE`** in `.env` (gitignored) + a Cloudflare secret (also
    in `worker/index.js` FORWARD_ENV, else the container never sees it). It's the full `cookie:`
    header from the Trello web app's DevTools → Network (any `trello.com/1/...` request). The
    `cloud.session.token` JWT has a **~30-day `exp`** (a 10-min `refreshTimeout` field exists but
    is NOT enforced — verified the cookie keeps working well past it). So **refresh ~monthly**:
    grab a fresh cookie header, update `.env`, then `printf '%s' "$COOKIE" | npx wrangler secret
    put TRELLO_SESSION_COOKIE`. When it expires, Phase 1 logs `INBOX UNAVAILABLE — refresh
    TRELLO_SESSION_COOKIE` and moves nothing (but still dedups). Verified end-to-end on the cloud
    2026-07-12: button 1 read 74 inbox cards, moved 15 links, dedup'd. **Security:** the session
    cookie is a FULL-ACCOUNT credential (more powerful than the scoped API token) — never commit
    it, never log it, compare by length/last-6 only.
  - WRITING INTO the Inbox (move-in / create-in) is still not attempted — capture happens via
    Trello's own integrations (mobile share, email-to-inbox, etc.); we only read + move OUT.
- Listen queue tops up from **System 1 + Life Optim only** (System 2 excluded — needs
  focused reading). 20h is a **soft floor** (capped by extractable-content yield).
- `.env` (gitignored) holds: `TRELLO_API_KEY`, `TRELLO_TOKEN`, `TRELLO_SESSION_COOKIE`
  (the web session cookie for Inbox access — see the Inbox section), `ANTHROPIC_API_KEY`,
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
- Co-author trailer on commits, naming the model that actually wrote the change (as of
  2026-07-25): `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>`.
- Board mutations: reorder cards in place; annotate via an **idempotent description
  marker** (`<!--cf-->[#rank · est min · why]<!--/cf-->`), NOT comments.
- Always write a JSON snapshot before mutating the board (sorts are reversible).

## Decisions locked

- Ranking: pairwise (merge sort today, binary insertion weekly); context = scoped
  profile doc; judge = Sonnet 5 + Opus 5 escalation (was 4.6/4.8 until 2026-07-25);
  adaptive thinking on; prompt-cached.
- Architecture: Scenario C (Haiku digest enrichment → digest-based comparisons).
- TTS = Kokoro (pluggable). Delivery = podcast RSS on R2 (UUID-unlisted privacy).
- Annotation = description marker. Rollout = Life-Optim pilot → approve → all 3 lists.

## Gotchas / learnings (append as you discover them)

- **Model migration to the 5-family (2026-07-25).** Comparator/classifier `claude-sonnet-4-6`
  → **`claude-sonnet-5`**, escalation `claude-opus-4-8` → **`claude-opus-5`**. Digests stay on
  `claude-haiku-4-5-20251001` — **there is no Haiku 5**, 4.5 is still current. Notes:
  - **Thinking must be set EXPLICITLY, not by omission.** Omitting the `thinking` field means
    *no thinking* on Sonnet 4.6 / Opus 4.8 but *adaptive thinking* on Sonnet 5 / Opus 5 — the
    same code silently changes behavior across the swap. `config.thinking_kwargs(model)` now
    sends `{"type": "adaptive"}` (Anthropic's recommended default) to models that support it
    and **nothing** to Haiku 4.5, which 400s on adaptive (pre-4.6 models only took the removed
    `budget_tokens` form). Knobs: `CF_THINKING` (adaptive|disabled|off), `CF_EFFORT` ("" = API
    default `high`; `medium` ≈ Sonnet 4.6 at high, the cheap step-down).
  - **`max_tokens` caps thinking AND response text together** → the forced-tool calls went
    300 → `config.TOOL_MAX_TOKENS` (4096). At 300 a thinking model can burn the whole budget
    reasoning and never emit the tool call — a truncation, not an error, so it would have
    surfaced as mass parse-failures falling back to the deterministic rule. Costs nothing
    unused (only generated tokens bill).
  - **Verified live 2026-07-25:** forced `tool_choice` + adaptive thinking works on both models
    on the first-party API (2.1s Sonnet 5 / 3.1s Opus 5). *Bedrock* would require
    `thinking={"type":"disabled"}` with forced tool choice — we're not on Bedrock.
  - **Cost is ~unchanged, measured not guessed:** an easy comparison cost $0.00328 (no
    thinking) vs $0.00331 (adaptive) — 119 vs 122 output tokens. Adaptive *chose* not to think
    on an easy pair; that's the point of it. Spend is dominated by the 6.7k-token cached
    profile doc, not by output. (Sonnet 5 intro pricing $2/$10 per MTok through 2026-08-31 is
    *cheaper* than the Sonnet 4.6 it replaces.)
  - **`ANTHROPIC_TIMEOUT_SECONDS` 90 → 180.** The 90s was tuned for <10s no-thinking calls; a
    thinking comparison legitimately runs longer, and a too-tight timeout converts slow-but-fine
    calls into 5 paid retries.
  - **The 4,954 cached pairwise rows are all from 4.6/4.8.** `pairwise` is keyed `(a_id,b_id)`
    with no model column, so a full re-sort would blend two models' judgments indistinguishably.
    Rows *do* record their model → `scripts/purge_stale_pairwise.py` (dry-run default, `--apply`,
    `--r2` to persist to the cloud) evicts them. **Not run** — a full purge means re-paying
    ~4,954 × $0.0033 ≈ **$16**, and incremental weekly work barely touches old pairs. Purge only
    before a deliberate clean re-rank.
- **⚠️ Namespace new env knobs with `CF_` — the shell already exports `CLAUDE_*` vars.** The
  thinking knob was first written as `CLAUDE_EFFORT`, and it immediately picked up `high` from
  `CLAUDE_EFFORT=high` exported in Jay's shell for Claude Code — the pipeline started sending an
  effort level nobody configured for it. Caught only because a debug print showed
  `output_config` appearing unbidden. Exactly the shadowing class as the `ANTHROPIC_API_KEY`
  gotcha below. Renamed to `CF_THINKING`/`CF_EFFORT`.
- **A workspace usage limit is NOT a credit balance.** `invalid_request_error: "You have reached
  your specified workspace API usage limits. You will regain access on <date>"` is a **spend cap
  set in the Console** (Settings → Limits → workspace). **Adding credits does not clear it**
  (verified 2026-07-25 — topping up changed nothing; the same key still 400'd). Fix is to raise
  the cap, wait for the reset date, or use a key from a different workspace. Being a `400`
  (not 429), the SDK does **not** retry it, so it kills a run instantly: Phase 2 died 11s in,
  mid-`enrich`, taking down the whole `asyncio.gather` batch.
- **`.env` must override the shell — `load_dotenv(override=True)`.** A personal
  `ANTHROPIC_API_KEY` exported in `~/.zshrc` (different account) silently shadowed the
  project's `.env` key because plain `load_dotenv()` does NOT overwrite already-set env
  vars. Symptom: "credit balance too low" 400s even though the `.env` key has credits —
  you're using the wrong key. Fixed 2026-06-20 with `override=True` (no-op in the cloud:
  no `.env` there, so Worker-forwarded vars are used). When debugging auth/credit errors,
  FIRST check `config.ANTHROPIC_API_KEY[-4:]` vs `dotenv_values('.env')[...][-4:]`.
- **Extraction is two-pass** (`extract._extract_main_text`): `favor_precision=True` first,
  fall back to `favor_recall=True` when the precise pass is < 500 chars, keep the longer
  (comments stripped in BOTH, so no bloat regression). `favor_precision` alone over-trims
  (a DeepMind blog: 2.5k vs 10k recall). `scripts/reextract_failed.py` re-extracts cached
  failures + regenerates digests — recovers stale failures (pre-UA-fix / transient). Most
  surviving failures are genuinely hard: NYT/WSJ/Bloomberg paywalls, archive.ph (bot-blocks),
  X/YouTube (JS/video), and homepage/about/redirect stubs with no article.
- **Paywalled cards rank on their og:description abstract.** When full extraction fails,
  `extract._metadata_fallback` fetches `og:title` + `og:description` and emits an
  `kind="abstract"` row (`ok=False`, `est_minutes=ABSTRACT_DEFAULT_MINUTES`): `enrich` gives
  it a real digest (not `[unreadable]`) so it ranks on the abstract, but `ok=False` keeps it
  OUT of the podcast (no full text to voice). Reachable NYT/Bloomberg/some X+YT expose a
  description; hard-403 NYT URLs don't (no UA/retry helps — IP-level block). Run 2026-06-20:
  `scripts/recover_and_resort.py` moved 19 such System1 cards off the bottom into #1–#193.
  NB: extraction is non-deterministic (a paywall may serve metadata one run, 403 the next).

- Trello "App" = the new name for "Power-Up"; token is generated via the
  `trello.com/1/authorize?...&key=...` URL, not a button.
- Trello rate limits ~100 req/10s/token, 300/10s/key → client needs 429/Retry-After
  backoff + token-bucket.
- Anthropic pricing (2026-07, /MTok, in/cache-read/out) — **the tier we now run**: Sonnet 5
  $3/$0.30/$15, but **$2/$0.20/$10 introductory through 2026-08-31**; Opus 5 $5/$0.50/$25
  (same as the Opus 4.8 it replaces); Haiku 4.5 $1/$0.10/$5. Predecessors: Sonnet 4.6
  $3/$0.30/$15, Opus 4.8 $5/$0.50/$25. Fable 5 is $10/$50 — a tier *above* Opus, not the
  default upgrade path. Cache read = 0.1× input; cost is dominated by the cached profile doc
  (~6.7k tok/call) → hence the digest pre-pass. NB Sonnet 5 uses the new tokenizer (~30% more
  tokens for the same text than Sonnet 4.6), so re-baseline token counts, not just prices.
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
  - **Parallel chunk synthesis is BROKEN for KOKORO** — its espeak phonemizer has global
    state and is NOT thread-safe; concurrent `model.create()` corrupts it (`RuntimeError:
    number of lines in input and output must be equal`). Kokoro must stay sequential.
  - **CARD-level synth IS parallelized for Google/OpenAI (2026-06-28).** Those are API-bound,
    not subject to espeak's constraint, so the queue build now renders a priority window of
    episodes concurrently — `listen_queue.make_synth` exposes `synth.many()` (concurrency =
    `config.SYNTH_CONCURRENCY`=8 for engines in `config.PARALLEL_SAFE_TTS={google,openai}`,
    else 1 = Kokoro stays serial). Thread-safety rule: the pure render (`audio.render_audio`)
    runs in `asyncio.to_thread`; ALL SQLite cache I/O (`cached_audio` read, `cache.put_audio`
    write) stays on the main thread — the cache connection is `check_same_thread`-bound, so a
    cache write from a worker thread raises. The window is bounded by est reading-time
    (`need*1.25`) so we don't synth far past the 20h target (audio runs longer than reading
    time, so an est-sized window over-covers). Big speedup on the slow sequential synth phase.
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
- **Trello "link preview" = a COVER IMAGE, not a URL attachment.** The cards that preview
  (e.g. "Project Mario") are link cards: their NAME is the page title and the page's OG image
  is uploaded as the card cover (`idAttachmentCover`) → thumbnail on the card front. A bare-URL
  card (name = raw URL) shows no preview, and merely POSTing the URL as a *link* attachment does
  NOT trigger the title/cover unfurl (a plain REST link attachment has `previews: 0`; Trello only
  unfurls via the app/Chrome-extension create-from-link flow). Two scripts, run 2026-06-20,
  both dry-run-by-default / `--apply`, both idempotent (re-runs skip already-fixed cards), both
  write an undo manifest to `outputs/`:
  - `scripts/fix_link_previews.py` (`TrelloClient.add_attachment`): attaches the article URL to
    every card lacking an http attachment — 376 cards. Cosmetically it does little on its own, but
    it makes the link reachable as an attachment so the *rename* below is safe.
  - `scripts/fix_link_cards.py` (`set_name` + `upload_cover`): for each bare-URL card, fetches
    og:title/og:image (parallel, browser UA), renames the card to the title, and uploads the OG
    image as the cover. Result: 305 titles, 248 cover thumbnails; 61 skipped (paywall/bot-block
    fetch fails like NYT/Science, or PDFs/mailing-list links with no OG). github.io etc. with no
    og:image get a title but no thumbnail.
  Renaming is safe because the URL now lives in the attachment — the pipeline reads it via
  `find_url(card) or card.url`, so rankings are unaffected. `TrelloClient.upload_cover` is a
  multipart POST (image bytes + `setCover=true`); a URL-only attachment can't be a cover.

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
- **Google TTS has a 5000-BYTE (not char) per-request hard limit.** `chunk_text` splits on
  sentence bounds but emits one oversized chunk for a "sentence" with no `.!?` (long lists,
  code, run-on paragraphs) → Google 400 `InvalidArgument` → **unhandled exception killed the
  whole run mid-synth** (the *real* cause of the standard-1 "crash" — NOT reaping). Fixed:
  `google_engine.byte_safe_chunks()` hard-splits any chunk over 4800 UTF-8 bytes (mirrors
  Kokoro's `_synth_chunk_safe`). Also `listen_queue` now wraps per-card synth in try/except so
  one bad card skips instead of crashing the run.
- **⚠️⚠️ THE BUTTON RUNS WHATEVER IMAGE IS DEPLOYED — NOT YOUR LOCAL CODE. REDEPLOY AFTER
  CODE CHANGES (2026-06-28).** A whole evening's debugging traced back to this: the live
  container image was deployed **2026-06-14**, but the workflow-simplification code (Phase 2
  reads "To Be Processed", not the removed "▶ Ready to Process") landed **2026-06-20**. So
  pressing the button ran 2-week-stale code that looked for "▶ Ready to Process", found 0
  cards, and no-op'd — silently doing nothing while looking healthy. Symptom to recognize
  INSTANTLY: `/logs` shows behavior that contradicts the current source (e.g. a list name the
  code no longer uses, a code path you deleted). **Before triggering ANY cloud run after a
  commit, redeploy** (`npx wrangler deploy`, Docker running) OR verify the live image matches:
  `wrangler containers info <app-id>` → compare the image tag/digest to the one your last
  `wrangler deploy` pushed (the deploy log's `EDIT ... image:` line). The container image is
  built from `src/` AT DEPLOY TIME and is otherwise frozen — git push does NOT update the cloud.
  (Companion gotcha: a live `max_instances:1` instance can refuse the new image; bump the
  Dockerfile `CF_BUILD_MARKER` to force a fresh digest — see the deploy-rollout note above.)
- **First Phase-2 run is SLOW (~40 min) and uncached:** the queue does a combined
  System1+LifeOptim ranking needing **cross-list** comparisons never computed by the per-list
  sorts. Merge-sort comparisons are **sequential** (~1.3/s), so concurrency-50 doesn't help.
  Once it completes + pushes cache to R2, future runs are cache-hit-fast. (Possible optimization:
  MERGE the two already-sorted lists (~n cross-list comparisons) instead of a full re-sort
  (~n log n) — check `listen_queue.ensure_listen_queue`.)
- **⚠️ THE CONTAINER GETS KILLED MID-RUN ON A LARGE *UNCACHED* LIST (2026-06-28) — and the
  kill is silent + unrecoverable, causing a re-press loop.** Symptom: press Phase 2 → it
  routes ~15-20 cards → container restarts (`/logs` ring buffer goes EMPTY, `running` flips
  False, `wrangler containers info` shows a fresh instance) → only ~19/138 cards landed on the
  board → **the R2 `state/cache.sqlite3` mtime did NOT advance** (the `finally`
  `push_cache_to_r2` in `server.run_named` never ran because the process was SIGKILLed, not
  exception-unwound). Net effect: every re-press redoes the SAME heavy work, dies at the same
  spot, and persists nothing — an infinite loop the button can't escape.
  - **Root cause:** `phase2.py`'s per-card loop calls `enricher.aenrich_many(existing)` on the
    *whole destination list*. The first card routed to a list with **no cached digests**
    (System 2 had 0/280 cached) triggers a **50-wide concurrent extraction** of all 280 cards
    (`MAX_FETCH_CONCURRENCY`/`MAX_LLM_CONCURRENCY` default = 50, NOT overridden in the cloud).
    50 simultaneous trafilatura/lxml parses (lxml trees are ~10-30× the HTML size in RAM) +
    50 in-flight HTTP bodies blow past **`standard-1`'s 4 GB**, AND peg its **0.5 vCPU** for
    ~13 min straight, starving the FastAPI `/health` endpoint. So the container dies by **OOM
    and/or Cloudflare health-reaping** (couldn't distinguish — `health.errors` was `[]`,
    container logs don't reach `wrangler tail`, ring buffer cleared on restart). The
    daemon-thread fix (see "never run the pipeline on the event loop") prevents *event-loop*
    starvation but NOT 50 CPU-bound worker threads pegging 0.5 vCPU under the GIL.
  - **Why a cached list is fine:** Life Optim (47 cached) and System 1 (257 cached) routed with
    no stall — `aenrich_many` is all cache hits, near-zero CPU/RAM. Only the *uncached* bulk
    enrichment is lethal. So the danger is specifically: a big list whose digests aren't in R2 yet.
  - **✅ FIXED (2026-06-29) — two fixes shipped + deployed so the button survives a cold batch:**
    (a) **Lowered cloud concurrency** — `MAX_FETCH_CONCURRENCY`/`MAX_LLM_CONCURRENCY` = **8** via
    wrangler `vars` (+ added to the `worker/index.js` FORWARD_ENV whitelist, else the container
    never sees them); the Mac keeps the default 50. (b) **Cache is now CHECKPOINTED to R2 every
    10 routed cards** mid-`run_phase2` (`checkpoint=push_cache_to_r2` wired in `phase2._build_and_run`,
    guarded by `config.R2_BUCKET` so local CLI runs that lack R2 don't try; the push runs in
    `asyncio.to_thread` to keep `/health` responsive). So a kill now loses ≤10 cards of work
    instead of everything, AND re-press RESUMES (moved cards are gone from 'To Be Processed';
    their digests + pairwise comps are in R2) → the run CONVERGES across presses instead of
    looping forever. **Still recommended for a huge cold batch:** warm off-Mac first
    (`scripts/run_phase2_local.sh` — full RAM/CPU, can't be reaped, pushes warm cache to R2)
    so the cloud button is light/fast. Not-yet-done (lower priority): (4) bigger `instance_type`,
    (5) hoist `aenrich_many(existing)` out of the per-card loop (it re-enriches the whole dest
    list every iteration — cheap when warm, but redundant). The recurring trigger is always the
    same: a list whose digests aren't in R2 yet.
- **⚠️ THE DEADLOCK ROOT CAUSE (2026-06-29) — the Trello client had NO request timeout, which
  froze the whole async event loop.** Symptom: a run sits at **0% CPU, log frozen for many
  minutes**, and — the tell — even the 90s Anthropic timeout never fires. Root cause: `requests`
  blocks FOREVER on a hung/silently-dropped TCP connection, and the pipeline calls Trello
  **synchronously inside** the async loop (`run_phase2`/`ensure_listen_queue` call
  `get_cards`/`move_card`/`set_rank_marker` directly), so one stuck request wedges the entire
  asyncio loop — no timer (incl. the Anthropic read timeout) can fire. **This explains BOTH the
  routing stall (this date) AND last session's queue-merge deadlock** (previously written off as
  un-diagnosable). Fixed: `trello._REQUEST_TIMEOUT = (10, 30)` on every `_session.request`, and
  `requests.Timeout`/`ConnectionError` retry with the existing backoff. (extract.py already had
  20/60/30s fetch timeouts — only the Trello client was missing one.) Diagnostic recipe: find the
  real worker PID (`ps -axo pid,time,command | grep '[.]venv/bin/python3 -m counterfactual'` — NOT
  the `uv run` wrapper), sample its cputime over 3s (flat = blocked on IO, not computing), and a
  log frozen >3 min ≈ a hang, not a slow card. NB: a single card legitimately taking ~4 min (many
  Opus escalations) can trip a 3-min stall alarm yet self-recover — confirm by re-checking whether
  `routed=` advanced before killing.
- **Listen Queue SELF-HEALS orphan (audio-less) cards (2026-07-12).** A card only enters the
  queue AFTER its audio synthesizes, but the audio row reaches R2 only on the end-of-run cache
  push — so a run KILLED before that push (the container kills) leaves the card in the queue (a
  live Trello move) with its audio never persisted. Result: cards in the Listen Queue with no
  cached audio → invisible in the feed (`episodes_for_queue` skips them) yet clogging the queue
  and pulled out of their reading lists. Found 46 such orphans (half the queue) on 2026-07-12.
  Fix: `ensure_listen_queue` now evicts any audio-less queue card to `To Be Processed` at the
  start of each build (they get re-ranked into the reading lists on the next routing), so
  orphans can't accumulate regardless of cause. NB re-routing re-CLASSIFIES them — the 46
  cleaned-up cards mostly landed in System 2 (deep read, excluded from the queue), which is the
  classifier's call, not a bug. The root-cause kills are themselves now rare (checkpointing +
  concurrency cap + Trello/Anthropic timeouts).
- **Phases are MUTUALLY EXCLUSIVE — the trigger refuses to start one while the other runs
  (2026-06-28).** Both phases pull the same R2 `state/cache.sqlite3` into the same local path
  at start and push it back at finish, so running them concurrently races on that file (SQLite
  corruption / clobbered work). `server.start_run` is now a GLOBAL mutex: if anything is in
  flight it returns `(False, message)` and the endpoint replies **HTTP 409** with a friendly
  "wait until 'X' finishes" message (labels: phase1="Extract readables", phase2="Sort
  readables"). So pressing button 1 while button 2 runs (or vice versa) safely fails with a
  warning instead of corrupting the cache. (In-process flags + `threading.Lock`; correct
  because `max_instances:1` = one container/process.)
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
