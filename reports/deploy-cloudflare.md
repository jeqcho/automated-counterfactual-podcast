# Deployment plan: Cloudflare Containers (Mac-free, always-on)

**Goal:** run the whole pipeline in the cloud so nothing depends on Jay's Mac being on.
**Hosting:** Cloudflare Containers. **TTS:** Google Cloud Neural2 (cloud API, no model).

## Architecture

```
Butler button ─POST─► Worker (/phase1,/phase2) ─► Container (FastAPI: runs the phase)
Cron trigger ───────► Worker (scheduled())      ─► Container
                                                   │ Claude / Trello / Google TTS / R2 (all cloud)
```
- A **Worker** is the public entrypoint (handles the button webhooks + cron schedule, on
  `trigger.chojeq.com` via a Worker route) and forwards to a **Container** running our
  existing FastAPI `server.py`. Containers scale to zero — cheap while idle.
- No tunnel, no local server. The Worker URL replaces `trigger.chojeq.com → tunnel`.

## ⚠️ The gotcha: state must move off the container's ephemeral disk

Containers **scale to zero and lose local disk**. Two things currently live on disk and
must move to durable storage (**R2**), or they vanish between runs:

1. **SQLite cache** (`outputs/cache.sqlite3` — digests, pairwise results, audio rows).
   Plan: on container start, download `cache.sqlite3` from R2; after each run, upload it
   back. A per-phase lock already prevents concurrent writers. (Alt: rewrite `cache.py`
   onto Cloudflare **D1** — bigger change; R2-sync is simpler.)
2. **Audio files.** `audio.synthesize_card` currently writes a local mp3 and caches the
   local path; `rss.publish` uploads later. For the cloud, **upload to R2 at synth time**
   and store the **R2 key** (not a local path) in the `audio` cache row. Then a fresh
   container never needs the local file again.

These two are the only real code changes; everything else (LLM, Trello, Google TTS, R2)
is already cloud-native.

## Build checklist

1. **Code: durable state**
   - `cache.py`: add `load_from_r2()` / `save_to_r2()` (download on start, upload on finish).
   - `audio.py`: upload mp3 to R2 at synth, store R2 key + seconds; `rss` reads keys.
2. **Dockerfile** — `python:3.12-slim` + `ffmpeg` + `uv`; `uv sync --extra google`;
   `CMD uvicorn counterfactual_podcast.server:app --host 0.0.0.0 --port 8080`.
3. **Worker** (`worker/`) — `fetch()` proxies `/phase1`,`/phase2` (with the
   `X-Trigger-Token` check) to the Container; `scheduled()` triggers phases on cron
   (UTC; e.g. Phase 1 daily, Phase 2 hourly so it drains "▶ Ready to Process").
4. **wrangler.toml** — container image + Durable Object binding + `[triggers] crons`,
   plus the `trigger.chojeq.com` route.
5. **Secrets** (wrangler secret put): `TRELLO_API_KEY/TOKEN`, `ANTHROPIC_API_KEY`,
   `R2_*`, `TRIGGER_TOKEN`, and the **Google service-account JSON** (write to a temp file
   in-container, set `GOOGLE_APPLICATION_CREDENTIALS`).
6. **Deploy** — `wrangler login` (Jay) → `wrangler deploy`. Point the buttons at the
   Worker URL. Tear down the local tunnel + server + launchd idea.

## What Jay needs to provide
- **Google Cloud:** create a project, enable the **Text-to-Speech API**, make a service
  account with TTS access, download its JSON key. (Free tier: 1M chars/mo.)
- **Cloudflare:** Workers Paid plan ($5/mo) for Containers; `wrangler` auth.

## Rough cost
- Workers Paid $5/mo (includes some container time; our idle-heavy use likely fits).
- Google Neural2 ~$21/mo at 10h/wk (often less; 1M chars/mo free).
- R2: pennies (storage + zero egress).
→ ~$26/mo all-in, fully Mac-independent.
