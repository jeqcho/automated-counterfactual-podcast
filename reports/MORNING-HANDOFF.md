# Morning handoff (overnight of 2026-06-02 → 03)

## TL;DR
Overnight I sorted the big lists, am building you a listen queue, and wrote all the
Cloudflare deploy code. **The only thing left needs your credentials** (Google Cloud +
Cloudflare Workers Paid) — then we deploy and it's off your Mac for good.

## What's running overnight (background, on your Mac)
1. **System 1 + System 2 one-time sort** (in-place, cheap sequential merge sort).
   - Before-copies saved on the board: **"System 1 (before sort)"**, **"System 2 (before sort)"**
     (and "Life Optimization (before sort)" from the pilot) — for side-by-side comparison.
   - Log: `logs/oneshot-sys12-*.log`. Snapshots: `outputs/oneshot_system{1,2}_*_{pre,post}.json`.
2. **Listen Queue build** (chained — starts when the sort finishes). Pulls the TOP cards
   from the now-sorted **System 1 + Life Optim only** (never To Be Processed / System 2),
   synthesizes ~3h with **Kokoro** (local, free), and publishes the podcast to R2.
   - Log: `logs/overnight-queue-*.log`. When done it prints `FEED URL: …` — **that's your
     subscribable podcast link** (add it in any podcast app). Creates a "Listen Queue" list.

To check results in the morning:
```
grep "FEED URL" logs/overnight-queue-*.log         # your podcast feed
tail -5 logs/oneshot-sys12-*.log                    # sort summary
```

## What I built tonight (all committed + pushed to main, 114 tests passing)
- **Two-phase intake**: Phase 1 (triage Inbox read-vs-do → To Be Processed) + Phase 2
  (drain ▶ Ready to Process → route/rank/queue/publish). Both run via your Trello buttons.
- **Phase 1 button works live** — triaged your 185 inbox cards (154 read / 31 todo), now
  link-gated (no-link notes stay in the Inbox) and triage runs only on link-bearing cards.
- **Cross-board move fix** (the bug that made the button move nothing).
- **Perf**: URL fetches parallelized (thread pool) + concurrency 12→50; Haiku input capped
  at 24k chars so no giant page can blow up cost (~$0.006/card worst case).
- **Google Cloud Neural2 TTS engine** (your cloud TTS pick, ~$21/mo at 10h/wk).
- **Cloudflare Containers deploy scaffolding**: `Dockerfile`, `.dockerignore`,
  `worker/index.js`, `wrangler.jsonc`, and **cloud state durability** (cache↔R2 sync so a
  scale-to-zero container never re-pays the $$ ranking; audio persists in R2 via a stable
  feed prefix).

## Your morning TODO — deploy off the Mac
Follow **`reports/deploy-steps.md`**. You provide:
1. **Google Cloud**: a project, enable the Text-to-Speech API, a service-account JSON key.
2. **Cloudflare**: Workers Paid ($5/mo) + `wrangler login`.
Then we set the secrets, `wrangler deploy`, point `trigger.chojeq.com` at the Worker, and
tear down the temporary local server + tunnel. Rough cost ~$26/mo, fully Mac-independent.

## Still running on your Mac right now (temporary, for the buttons today)
- Local trigger server (`localhost:8787`) + `cloudflared` quick→named tunnel
  (`https://trigger.chojeq.com`). These get replaced by the Cloudflare deploy. The Butler
  buttons point at `https://trigger.chojeq.com/phase{1,2}` with header
  `X-Trigger-Token` (value in `.env`).

## Don't-forget / decisions still open
- **Don't press Phase 2 until the sort is done** (it needs sorted lists for binary insertion).
  By morning the sort is done, so Phase 2 is safe.
- Archive the three "(before sort)" copies whenever you're happy with the sort.
- Optional later: triage caching (so re-pressing Phase 1 is free); full audio→R2-at-synth
  (current stable-prefix approach is fine for the cron).
