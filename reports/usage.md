# Usage & Runbook — Automated Counterfactual Podcast

## Setup
- Deps: `uv sync --extra dev`. System: `brew install ffmpeg` (for Kokoro WAV→MP3).
- Secrets live in the gitignored `.env` (Trello, Anthropic, R2). See CLAUDE.md.
- Tests: `uv run pytest -q` (all mocked; no network).

## One-time sort (rank the 3 reading lists by counterfactual impact)
Dry run (writes JSON snapshots to `outputs/`, never touches the board):
```bash
uv run python -m counterfactual_podcast.pipelines.oneshot_sort --list life_optim
```
Review `outputs/oneshot_life_optim_<ts>_post.json` (the proposed order + per-card "why").
Apply to the board (reorders cards + writes the idempotent desc rank marker):
```bash
uv run python -m counterfactual_podcast.pipelines.oneshot_sort --list life_optim --apply
# or all three lists:
./scripts/run_oneshot.sh --all --apply
```
- Recommended rollout: pilot on `life_optim`, review the top-15, then `--all --apply`.
- **Reverting a sort:** every run writes a pre-sort snapshot (`*_pre.json`) with the
  original positions; restore from it if an order looks wrong.
- Cost ≈ ~$35 for all 623 cards (Scenario C); ~20–30 min wall-clock at concurrency 12.

## Ongoing intake — two phases (with a review checkpoint)

The Inbox mixes reading links with todos, so intake is split:

**Phase 1 — triage Inbox → To Be Processed** (moves only reading material):
```bash
./scripts/run_phase1.sh            # dry-run preview
./scripts/run_phase1.sh --apply    # actually move reading-material cards
```
Then **review** the `To Be Processed` list in Trello and drag any mistakes back to the
Inbox. Drag the keepers into the **`▶ Ready to Process`** list — that's the Phase 2 "go".

**Phase 2 — drain ▶ Ready to Process → route + rank + queue + publish**:
```bash
./scripts/run_phase2.sh            # dry-run preview
./scripts/run_phase2.sh --apply    # route each card to System1/2/LifeOptim at its
                                   # impact rank, top up the 20h queue, publish RSS
```
Phase 2 is a no-op when `▶ Ready to Process` is empty, so it's safe to run on a schedule
(it processes whatever you've dragged in since last time). System 2 cards are routed but
never enter the listen queue.

> The older single-step `pipelines/weekly` still exists but the two-phase flow above
> supersedes it (adds the human review checkpoint).

## Listen queue & "done"
- The **Listen Queue** list is ordered by counterfactual impact — listen top-first.
- Mark an episode **done by archiving** the card in Trello; the next weekly run
  (or an on-demand top-up) refills the queue from the highest-impact remaining cards.
- 20h is a **soft floor**: if the clean (extractable) pool is smaller, it fills what's
  available and logs the shortfall — it never loops forever.

## TTS
- Default engine: **Kokoro** (local, free). Swap to OpenAI via `TTS_ENGINE=openai`
  (and set `OPENAI_API_KEY`) — ~$15/week at 20h. Voice via `KOKORO_VOICE`.
- First full queue fill synthesizes ~20h of audio (~hours of CPU); subsequent weeks
  only synthesize new cards (cached).

## Podcast hosting (R2)
- Audio + `rss.xml` upload to Cloudflare R2 under a random UUID prefix (unlisted).
- Subscribe in any podcast app to the returned `feed_url`.
- Requires the bucket's **r2.dev public access** to be enabled (Allow Access). Until
  then, `publish(upload=False)` writes `outputs/rss.xml` locally.

## Scheduling
- `scripts/run_weekly.sh` is launchd/cron-friendly; logs to `logs/weekly-<ts>.log`.
- macOS `launchd` will NOT wake a sleeping Mac — run while plugged in / awake, or use
  a `pmset` wake schedule.

## Logs & caching
- All long runs log to `logs/<name>-<ts>.log` (also tee'd by the scripts).
- `outputs/cache.sqlite3` caches extraction, digests, pairwise results, and audio —
  re-runs and crash-resumes are near-free. Delete it to force a full recompute.
