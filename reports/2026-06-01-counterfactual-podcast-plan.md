# Automated Counterfactual Podcast — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a weekly automation that (a) pulls links from Jay's Trello Inbox, routes each into one of three reading lists (System 1 / System 2 / Life Optimization) ranked by *counterfactual impact* via LLM pairwise sorting, and (b) maintains a priority-ordered "Listen Queue" of ≥20 hours of text-to-speech audio delivered as a private podcast RSS feed. Plus a **one-time job today** that pairwise-sorts the three existing reading lists by counterfactual impact.

**Architecture:** A Python package (`src/`) split into focused modules: a Trello client, a content fetcher/extractor, an LLM pairwise comparator (Claude + the `private/` profile doc, prompt-cached), a generic LLM merge/insertion sort, a pluggable TTS layer (Kokoro local by default), a listen-queue builder, and an RSS publisher. A SQLite cache makes extraction and pairwise comparisons resumable and cheap to re-run. Two entrypoints: `oneshot_sort` (run today) and `weekly` (cron). Long runs go to tmux with timestamped logs in `logs/`.

**Tech Stack:** Python 3.12 + `uv`; `requests` (Trello REST); `trafilatura` + `pypdf` (content extraction); `anthropic` SDK with prompt caching **and bounded async concurrency** (pairwise judgments using Claude); `kokoro-onnx` (local TTS, pluggable to OpenAI/Fish/Qwen); `mutagen` (audio duration); `feedgen` (RSS); Cloudflare R2 via `boto3` (audio hosting, S3-compatible, zero egress); SQLite (cache); `pytest` (tests). **System prerequisite:** `ffmpeg` (for WAV→MP3 encoding and audio concat) — `brew install ffmpeg`, documented in Task 10.

## Robustness & concurrency design (cross-cutting — addresses review blockers)

**Comparator non-transitivity (BLOCKER from review).** An LLM comparator is *deterministic* (via cache + tie-break) but **not transitive** — A>B, B>C, C>A cycles are common among the ~250 near-equivalent mid-pack items. Plain merge sort over an intransitive comparator yields an order-dependent result with no error. Mitigations, in priority order:
- **It mostly doesn't matter where it's noisiest.** Jay reads/listens top-first; mid-pack ordering is low-stakes. The relevance/impact gates (steps 1–2) fire cleanly at the extremes (clear winners float up, clear losers sink), which is exactly where correctness matters.
- **Stabilize the head with Copeland.** After the merge-sort pass produces a full order, take the **top ~40 cards** and run a round-robin (all-pairs) Copeland ranking — re-rank them by win count. ~40 items = ~780 comparisons, all cached/parallel, washes out cycles exactly where ordering is consequential. This stays *pairwise* (honoring "pairwise not scoring") while being noise-robust.
- **Characterize, don't hide.** `sort.py` tests MUST include an **intransitive fake comparator** to document the failure mode (asserts the sort still terminates and is deterministic given the cache), not just a clean `a>b` comparator.

**Concurrency (BLOCKER from review).** 5,000 *sequential* calls at ~2–4 s each = 3–6 h — far too slow for "today." Fix: **bottom-up merge sort issues independent merges at each level concurrently** through a bounded async pool (`asyncio` + `anthropic.AsyncAnthropic`, **12 in flight**, respecting tier RPM/TPM with a token-bucket limiter and 429 backoff). Level 0 has ~150 independent merges, level 1 ~75, etc.; only comparisons *within a single merge* are serial. Copeland's all-pairs and the per-card extraction are embarrassingly parallel. Realistic wall-clock for the one-shot: **~20–30 min**, not hours. Alternative for the pure-bulk pass: Anthropic **Batch API** (50% cheaper, async) — noted as a fallback if rate limits bite.

---

## Key facts grounded from the live board (2026-06-01)

| Role | List name | List id | Cards |
|---|---|---|---|
| System 1 | `Reading list that doesn’t require system 2` | `683cb9f4387706ad70dc4299` | 301 |
| System 2 | `Reading list that requires system 2` | `683cb9e94b55936c9e9505a3` | 272 |
| Life Optim | `Life Optimization` | `69cffff85c64bd09a7c8cd7d` | 50 |
| Board | `Home base` | `657f3741ecf6b2f7a40ef8df` | — |
| Member | `chooijeqin` | `5a8056af894b0bfba8179ee4` | — |

- **Classification context / rubric:** `private/jay-profile-for-article-classification.md` (contains both a scoring rubric and a **deterministic 7-step pairwise comparator** — we use the pairwise comparator).
- **Listen queue top-up sources:** System 1 + Life Optimization only (System 2 excluded — needs focused reading).
- **Out of scope:** `podcast`, `vids`, and the other 25 lists.
- **Credentials present in gitignored `.env`:** `TRELLO_API_KEY`, `TRELLO_TOKEN`, `ANTHROPIC_API_KEY`. (R2 + optional OpenAI keys added later.)

## ✅ Inbox access — RESOLVED (was the one open risk)

The native Trello **Inbox** is fully reachable with the standard read/write token. The `401` we first hit was the wrong endpoint (`/members/me/inbox`). The working path: `GET /1/members/me?fields=inbox` returns `inbox.idList` (a hidden personal board's "Inbox List"); read/move it like any normal list. Verified live 2026-06-01 (HTTP 200). Full details in `reports/inbox-access-finding.md`. **Consequence:** Jay's capture workflow is unchanged; no dedicated-list fallback needed (kept only as a footnote in case Atlassian ever removes the `inbox` member field).

---

## File Structure

```
src/counterfactual_podcast/
  __init__.py
  config.py            # load .env, list ids, constants (target hours, paths)
  trello.py            # TrelloClient: lists, cards, reorder, comments, labels, create list, archive
  inbox.py             # InboxSource: pull from native Inbox (or fallback list) -> cards
  extract.py           # fetch URL -> clean text + word_count + est_minutes; PDF/tweet/paywall handling
  cache.py             # SQLite: extracted_content, pairwise_results, audio_files tables
  llm_compare.py       # Comparator: A-vs-B pairwise judgment via Claude (profile doc prompt-cached)
  sort.py              # merge_sort(items, comparator); insert_sorted(item, sorted_list, comparator)
  classify.py          # route an inbox card -> {system1, system2, life_optim}
  tts/
    __init__.py        # TTSEngine protocol + get_engine()
    kokoro_engine.py   # default local engine
    openai_engine.py   # paid fallback engine
  audio.py             # synthesize a card -> mp3, compute duration
  listen_queue.py      # build/top-up the 20h queue from ranked System1 + LifeOptim
  rss.py               # generate podcast RSS from queue; upload audio+feed to R2
  pipelines/
    oneshot_sort.py    # TODAY: pairwise-sort the 3 existing lists in place
    weekly.py          # WEEKLY: inbox -> route -> sort -> queue top-up -> TTS -> RSS
data/                  # profile doc copy if needed, voice files, static inputs
outputs/               # generated mp3s, rss.xml, sort snapshots (json)
logs/                  # timestamped run logs
tests/                 # pytest mirrors of src modules
```

**Design principles:** each module has one responsibility and a typed interface; the comparator and sort are generic and unit-tested with a fake comparator; all network/LLM calls go through the cache so re-runs are cheap and resumable.

---

## Phase 0 — Spike & Scaffold

### Task 1: Inbox access — RESOLVED (verify-only)

**Status:** Already solved during planning (see `reports/inbox-access-finding.md`). The mechanism: `GET /1/members/me?fields=inbox` → `inbox.idList` → treat as a normal list. This task just locks it into code; no spike needed.

- [ ] **Step 1: Confirm live** (read-only): `uv run python -c "import requests,os; from dotenv import load_dotenv; load_dotenv(); a={'key':os.environ['TRELLO_API_KEY'],'token':os.environ['TRELLO_TOKEN']}; il=requests.get('https://api.trello.com/1/members/me',params={**a,'fields':'inbox'}).json()['inbox']['idList']; print('inbox list', il, 'cards', len(requests.get(f'https://api.trello.com/1/lists/{il}/cards',params=a).json()))"` → Expected: prints the inbox list id + a card count (HTTP 200).
- [ ] **Step 2:** Encode the path in `inbox.py` (Task 14): resolve `inbox.idList` dynamically at runtime (do NOT hardcode the account-specific id). Footnote fallback only: if the `inbox` field ever disappears, read a board list literally named `Inbox`.
- [ ] **Step 3: Commit** (finding doc already committed).

> No longer a gate: `inbox.py` (Task 14) has a confirmed path. The one-shot sort (Tasks 2–9) is fully independent and can proceed immediately.

### Task 2: Project scaffold with uv

**Files:** Create `pyproject.toml`, `src/counterfactual_podcast/__init__.py`, `tests/__init__.py`, `.python-version`.

- [ ] **Step 1: Init uv project.**
```bash
cd /Users/jeqcho/automated-counterfactual-podcast
uv init --package --name counterfactual-podcast --python 3.12
uv add requests trafilatura pypdf anthropic feedgen mutagen boto3 python-dotenv
uv add --group dev pytest pytest-mock responses
```
- [ ] **Step 2: Smoke test.** Create `tests/test_smoke.py`:
```python
def test_import():
    import counterfactual_podcast  # noqa
    assert True
```
- [ ] **Step 3: Run.** `uv run pytest tests/test_smoke.py -v` → Expected: PASS.
- [ ] **Step 4: Commit.** `git add -A && git commit -m "chore: scaffold uv project"`

### Task 3: Config module

**Files:** Create `src/counterfactual_podcast/config.py`, `tests/test_config.py`.

- [ ] **Step 1: Write failing test.**
```python
from counterfactual_podcast import config
def test_list_ids_present():
    assert config.SYSTEM1_LIST_ID == "683cb9f4387706ad70dc4299"
    assert config.SYSTEM2_LIST_ID == "683cb9e94b55936c9e9505a3"
    assert config.LIFE_OPTIM_LIST_ID == "69cffff85c64bd09a7c8cd7d"
    assert config.TARGET_QUEUE_HOURS == 20
```
- [ ] **Step 2: Run → FAIL.** `uv run pytest tests/test_config.py -v`
- [ ] **Step 3: Implement.**
```python
import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()
ROOT = Path(__file__).resolve().parents[2]
BOARD_ID = "657f3741ecf6b2f7a40ef8df"
MEMBER_ID = "5a8056af894b0bfba8179ee4"
SYSTEM1_LIST_ID = "683cb9f4387706ad70dc4299"
SYSTEM2_LIST_ID = "683cb9e94b55936c9e9505a3"
LIFE_OPTIM_LIST_ID = "69cffff85c64bd09a7c8cd7d"
TARGET_QUEUE_HOURS = 20
WPM_READING = 230          # for est. reading time (matches profile doc)
PROFILE_DOC = ROOT / "private" / "jay-profile-for-article-classification.md"
OUTPUTS = ROOT / "outputs"; LOGS = ROOT / "logs"; DATA = ROOT / "data"
TRELLO_KEY = os.environ.get("TRELLO_API_KEY")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
# Model IDs confirmed current in this environment (2026-06): Sonnet 4.6 / Opus 4.8.
# Overridable via env so a wrong/retired ID never hard-codes a 404.
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")        # comparator workhorse
CLAUDE_MODEL_ESCALATE = os.environ.get("CLAUDE_MODEL_ESCALATE", "claude-opus-4-8")  # close calls
MAX_LLM_CONCURRENCY = int(os.environ.get("MAX_LLM_CONCURRENCY", "12"))
```
- [ ] **Step 4: Verify model IDs are live** before relying on them: `uv run python -c "import anthropic,os; print([m.id for m in anthropic.Anthropic().models.list().data][:10])"` and confirm `CLAUDE_MODEL`/`CLAUDE_MODEL_ESCALATE` appear. If not, set them in `.env`. (Self-evident here since this very session runs on `claude-opus-4-8`.)
- [ ] **Step 5: Run → PASS.** **Step 6: Commit.**

---

## Phase 1 — Core Library

### Task 4: Trello client

**Files:** Create `src/counterfactual_podcast/trello.py`, `tests/test_trello.py`.

Responsibilities: list cards in a list; reorder a card (`pos`); **set an idempotent rank marker on the card description** (NOT a comment — see below); ensure/create a list; ensure/apply a label; archive a card; move a card to another list. All via raw REST with key+token. **Every request goes through `_request()` with 429/`Retry-After` backoff and a token-bucket limiter (~8 req/s, under Trello's ~100 req/10 s/token and 300 req/10 s/key).** Network calls mocked in tests with `responses`.

> **Why desc marker, not comments (review fix):** attaching a *comment* to all 301 cards permanently clutters the board and doubles the write count. Instead, write a single idempotent line into the card description: `set_rank_marker(card, rank, est_min, why)` prepends/replaces a delimited block `<!--cf-->[#rank · est_min min · why]<!--/cf-->` so re-runs overwrite rather than accumulate. Reordering already conveys priority visually; the marker preserves the rationale + est time inline.

- [ ] **Step 1: Write failing tests** for `get_cards(list_id)` and `set_card_position(card_id, pos)` using mocked `responses`:
```python
import responses
from counterfactual_podcast.trello import TrelloClient

@responses.activate
def test_get_cards_parses_names():
    responses.add(responses.GET, "https://api.trello.com/1/lists/L1/cards",
                  json=[{"id": "c1", "name": "A", "desc": "", "url": "http://x"}])
    cards = TrelloClient("k", "t").get_cards("L1")
    assert cards[0].id == "c1" and cards[0].name == "A"

@responses.activate
def test_set_position_calls_put():
    responses.add(responses.PUT, "https://api.trello.com/1/cards/c1", json={"id": "c1"})
    TrelloClient("k", "t").set_card_position("c1", 65535)
    assert responses.calls[0].request.params["pos"] == "65535"
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `TrelloClient` with a `Card` dataclass (`id, name, desc, url, list_id`), a single `_request(method, path, **params)` helper injecting `key`/`token` and applying the **token-bucket limiter + 429/`Retry-After` exponential backoff** (test this: a mocked 429 with `Retry-After: 1` retries and succeeds), methods: `get_cards`, `set_card_position`, `set_rank_marker(card, rank, est_min, why)` (idempotent desc block), `update_desc`, `ensure_list(name)`, `ensure_label(name, color)`, `add_label`, `archive_card`, `move_card(card_id, list_id)`. Use `pos` floats to reorder (Trello accepts `"top"`, `"bottom"`, or numeric).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit.**
- [ ] **Step 6: Live read-only sanity check** (not a unit test): `uv run python -c "from counterfactual_podcast.trello import TrelloClient; from counterfactual_podcast import config; print(len(TrelloClient(config.TRELLO_KEY, config.TRELLO_TOKEN).get_cards(config.LIFE_OPTIM_LIST_ID)))"` → Expected: `50`.

### Task 5: Content extractor

**Files:** Create `src/counterfactual_podcast/extract.py`, `tests/test_extract.py`.

Responsibilities: given a card (name + url-or-text), return `ExtractedContent(title, text, word_count, est_read_minutes, kind, ok, note)`. Handle: HTML articles (`trafilatura`), PDFs incl. arXiv (`pypdf`), bare-text cards (no URL → use the card name/desc as the content), and known-hard sources (X/Twitter, paywalls, YouTube) → mark `ok=False` with a note so the comparator/queue can down-rank or skip TTS. A card may contain a URL in `name`, `desc`, or an attachment.

- [ ] **Step 1: Write failing tests** with local HTML/PDF fixtures and a bare-text card:
```python
from counterfactual_podcast.extract import extract, est_minutes
def test_est_minutes_rounds():
    assert est_minutes(2300) == 10   # 2300/230
def test_bare_text_card_uses_name(tmp_path):
    c = extract_from_text("Conflict vs. mistake in non-zero-sum games")
    assert c.kind == "text" and c.word_count >= 5
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** `find_url(card)` (regex over name/desc); `extract(card)` dispatch by URL/content-type; `trafilatura.fetch_url`+`extract` for HTML; `pypdf` for `application/pdf` and `arxiv.org/pdf`; arXiv `abs` → swap to `pdf`; `est_minutes = round(word_count / config.WPM_READING)`; classify hard sources by domain → `ok=False`. Always return a populated object (degrade gracefully, never raise).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit.**
- [ ] **Step 6: Measure real extractable yield** (feeds the 20h feasibility question, review issue #4). Run extraction across all System 1 + Life Optimization cards (cached), then print: total cards, `ok=True` count, `ok=False` by reason (paywall/X/YouTube/PDF-fail), and **total extractable audio hours** (`sum(est_minutes where ok)/60`). Save to `outputs/yield_report.json`. This number tells us whether a 20h queue is even reachable before we build the queue logic; if the clean pool is < 20h, the queue target becomes a soft floor (Task 12).

### Task 6: SQLite cache

**Files:** Create `src/counterfactual_podcast/cache.py`, `tests/test_cache.py`.

Tables: `extracted(card_id PK, title, text, word_count, est_minutes, kind, ok, note, fetched_at)`; `pairwise(a_id, b_id, winner_id, decided_at_step, why, model, ts, PRIMARY KEY(a_id,b_id))`; `audio(card_id PK, path, seconds, engine, ts)`. Cache is keyed so re-runs skip network/LLM work and merge sort is resumable.

- [ ] **Step 1: Failing test:** put/get round-trip for each table; `get_pairwise(a,b)` returns symmetric-aware result (store canonical order, flip winner on lookup).
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement** with stdlib `sqlite3`, a `Cache(path)` class, canonicalizing pair keys as `tuple(sorted((a,b)))`. **Step 4: PASS. Step 5: Commit.**

### Task 7: LLM pairwise comparator (Claude + profile doc, prompt-cached)

**Files:** Create `src/counterfactual_podcast/llm_compare.py`, `tests/test_llm_compare.py`.

Responsibilities: given two `ExtractedContent` items, return `(winner_id, step, why)` using the **7-step deterministic comparator** from the profile doc. The profile doc (~4k tokens) is sent as a **prompt-cached** system block so all ~5,000 comparisons reuse it cheaply. Results go through the cache (Task 6). The comparator is *total and deterministic* (the prompt forbids ties; a local deterministic fallback — shorter est. minutes, then card id — guarantees no tie even if the model hedges).

- [ ] **Step 1: Failing test** with a mocked Anthropic client: feeding a clearly on-pillar robotics item vs. an off-topic entertainment item returns the robotics item as winner; assert the profile doc is attached with `cache_control`.
```python
def test_comparator_prefers_on_pillar(mock_anthropic):
    cmp = Comparator(client=mock_anthropic, cache=InMemoryCache())
    winner = cmp.compare(robotics_item, celebrity_item)
    assert winner.winner_id == robotics_item.card_id
def test_profile_doc_is_cache_controlled(mock_anthropic):
    Comparator(client=mock_anthropic, cache=InMemoryCache()).compare(a, b)
    sys_blocks = mock_anthropic.last_kwargs["system"]
    assert any(b.get("cache_control") for b in sys_blocks)
```
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement.** Provide BOTH a sync `compare(a,b)` and an async `acompare(a,b)` (the async path is what the concurrent sort drives). System = `[{type:text, text: PAIRWISE_INSTRUCTIONS}, {type:text, text: PROFILE_DOC, cache_control:{type:"ephemeral"}}]`. User message presents A and B (title, kind, est_minutes, first ~1,200 words of each). Force a tool/JSON response: `{winner: "A"|"B", step: 1-7, why: str}`. Map to card ids; on parse failure use deterministic fallback. Check cache before calling; write result after. `escalate_model` when the model reports `step >= 6` (genuinely close) — re-ask with `CLAUDE_MODEL_ESCALATE`. Share one `AsyncAnthropic` client + an `asyncio.Semaphore(MAX_LLM_CONCURRENCY)` + token-bucket so all callers respect rate limits.
  > **Known limitation (review #11):** only the first ~1,200 words of each article are shown, so the model judges insight-density partly from `est_minutes` rather than full text. Acceptable cost/quality tradeoff; documented so it isn't mistaken for a bug.
- [ ] **Step 4: Run → PASS.** **Step 5: Commit.**

### Task 8: Generic LLM sort (merge sort + sorted insertion)

**Files:** Create `src/counterfactual_podcast/sort.py`, `tests/test_sort.py`.

Responsibilities: three pure functions over an injected (optionally async) comparator → fully unit-testable with deterministic fakes, **zero** network calls:
- `async merge_sort(items, acompare)` — **bottom-up** merge sort that runs the **independent merges within each level concurrently** (gather with a bounded pool); comparisons within one merge stay serial. Winner ranks first.
- `copeland_rank(items, acompare)` — all-pairs round-robin, rank by win count; used to **stabilize the top ~40** after merge sort (robust to intransitive cycles). Ties broken deterministically (est_minutes, then id).
- `insert_sorted(item, ordered, acompare)` — binary insertion (~log₂n comparisons) for adding one new card to an already-ranked list (weekly path).

- [ ] **Step 1: Failing tests** using a fake comparator `lambda a,b: a if a>b else b`:
```python
def test_merge_sort_orders_desc():
    assert merge_sort([3,1,2,5,4], fake_cmp) == [5,4,3,2,1]
def test_insert_sorted_places_correctly():
    assert insert_sorted(3, [5,4,2,1], fake_cmp) == [5,4,3,2,1]
def test_merge_sort_comparison_count_is_nlogn():
    calls = Counter(); await merge_sort(list(range(64)), counting_cmp(calls))
    assert calls.n < 64*7   # < n*log2(n)*~1.1
def test_intransitive_comparator_terminates_and_is_deterministic():
    # cyclic fake: 0>1, 1>2, 2>0. Sort must terminate, not hang, and be
    # repeatable given a memoizing cache. Documents the known failure mode.
    out1 = await merge_sort([0,1,2], cyclic_cmp)
    out2 = await merge_sort([0,1,2], cyclic_cmp)
    assert out1 == out2 and len(out1) == 3
def test_copeland_breaks_cycles_by_wincount():
    # with cyclic_cmp every item wins once -> deterministic tie-break order
    assert copeland_rank([0,1,2], cyclic_cmp) is not None
```
- [ ] **Step 2: Run → FAIL.** **Step 3: Implement** concurrent bottom-up merge sort, `copeland_rank`, and binary insertion. **Step 4: PASS. Step 5: Commit.**

---

## Phase 2 — TODAY: One-Time Bulk Sort

### Task 9: One-shot sort pipeline

**Files:** Create `src/counterfactual_podcast/pipelines/oneshot_sort.py`, `tests/test_oneshot_sort.py`.

Responsibilities: for each of the 3 target lists — fetch cards → extract content (cached, concurrent) → `merge_sort` with the cached async LLM comparator → `copeland_rank` the **top ~40** to stabilize the consequential head → **reorder cards in place** (top = highest counterfactual impact) → write the idempotent **desc rank marker** on each card (`rank, est_minutes, decided_at_step, one-line why`). Writes a JSON snapshot to `outputs/oneshot_sort_<list>_<ts>.json` *before* mutating Trello (so a bad sort is reversible).

- [ ] **Step 1: Failing test** (mocked Trello + fake comparator + 5 fake cards): asserts `set_card_position` called 5 times in ranked order and a snapshot json is written.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: Implement** `sort_list(client, cache, comparator, list_id)`:
  1. `cards = client.get_cards(list_id)`
  2. `items = [extract(c) for c in cards]` (cached)
  3. write pre-sort snapshot (current order)
  4. `ranked = await merge_sort(items, comparator.acompare)`; then `ranked[:40] = copeland_rank(ranked[:40], ...)`
  5. write post-sort snapshot (proposed order + why per card)
  6. **dry-run gate:** if `--apply` not set, stop here (print summary).
  7. on apply: reassign `pos` top→bottom via evenly spaced floats; `set_rank_marker` per card (idempotent desc block, not comments).
- [ ] **Step 4: Run → PASS.** **Step 5: Commit.**
- [ ] **Step 6: Dry run live** on the 50-card Life Optimization list first (smallest): generate the snapshot, **present the proposed top-15 ordering + rationales to Jay for approval** before any `--apply`. Then apply to all three lists in tmux:
```bash
mkdir -p logs
TS=$(date +%Y%m%d-%H%M%S)
tmux new-session -d -s sort "uv run python -m counterfactual_podcast.pipelines.oneshot_sort --all --apply 2>&1 | tee logs/oneshot_sort-$TS.log"
# tail: tmux attach -t sort   OR   tail -f logs/oneshot_sort-$TS.log
```
Estimated comparisons: ~2,200 (System1) + ~1,940 (System2) + ~240 (LifeOptim) + ~780×3 Copeland-head ≈ **~6,700**, prompt-cached and run at concurrency 12. **Realistic wall-clock ~20–30 min** (not hours, because merges run concurrently). Report measured ETA after 2 min of live calls; if rate limits throttle hard, switch the bulk pass to the Anthropic Batch API.

---

## Phase 3 — Text-to-Speech

### Task 10: Pluggable TTS engine (Kokoro default)

**Files:** Create `src/counterfactual_podcast/tts/__init__.py`, `tts/kokoro_engine.py`, `tts/openai_engine.py`, `tests/test_tts.py`.

Responsibilities: `TTSEngine` protocol = `synthesize(text: str, out_path: Path) -> Path`. `get_engine(name)` factory (`"kokoro"` default, `"openai"` fallback). Kokoro via `kokoro-onnx` (Mac-friendly, no GPU needed); chunk long text on sentence boundaries (model has a token cap), synth each chunk, concatenate to one mp3. OpenAI engine via `tts-1`. Tests mock the actual model and assert chunking + file write.

- [ ] **Step 1: Failing test:** `synthesize` chunks a 5,000-word string and writes one mp3 (model mocked); `get_engine("kokoro")` returns the Kokoro engine.
- [ ] **Step 2: Run → FAIL.**
- [ ] **Step 3: System prereq.** `brew install ffmpeg` (required for WAV→MP3 + concat; not a pip package). Document in `reports/usage.md`. Then `uv add kokoro-onnx soundfile pydub` and download the Kokoro voice model to `data/`.
- [ ] **Step 4: Implement.** Sentence chunker (~1,500 chars, Kokoro has a token cap); synth each chunk to WAV via `kokoro-onnx`; concat + encode to MP3 via `pydub`/ffmpeg. OpenAI engine reads `OPENAI_API_KEY` lazily, uses `tts-1`.
- [ ] **Step 5: Run → PASS.** **Step 6: Commit.**
- [ ] **Step 7: Live quality + speed check** — synth one real ~3,000-word article, time it, listen. Kokoro on M-series CPU runs ~1–5× real-time, so **the first full 20h queue fill is ~4–10 h of background synthesis** (subsequent weeks only synth new cards — much less). Confirm quality acceptable; if not, flip default to `openai` in config. **OpenAI cost branch:** ~20 h/week ≈ ~1M chars/week → ~$15/week on `tts-1` (state this so the swap is a conscious cost decision, per review #5).

### Task 11: Card → audio

**Files:** Create `src/counterfactual_podcast/audio.py`, `tests/test_audio.py`.

Responsibilities: `synthesize_card(card, content, engine, cache) -> AudioAsset(path, seconds)`. Skip cards with `content.ok == False` (paywalled/tweet/video) — log + return `None`. Compute real duration with `mutagen` (preferred) so the 20h math uses actual audio length, not an estimate. Cache by `card_id`.

- [ ] **Step 1: Failing test:** ok-content card produces an `AudioAsset` with `seconds>0`; not-ok card returns `None`; second call hits cache (engine not invoked twice).
- [ ] **Step 2–5: Run→FAIL, implement, PASS, commit.**

---

## Phase 4 — Listen Queue & Podcast Feed

### Task 12: Listen-queue builder (20h top-up)

**Files:** Create `src/counterfactual_podcast/listen_queue.py`, `tests/test_listen_queue.py`.

Responsibilities: `ensure_listen_queue(client, cache)`:
1. Ensure a `Listen Queue` list exists (create if missing).
2. Measure current queue audio hours (sum of `audio.seconds` for its cards).
3. If `< TARGET_QUEUE_HOURS`, pull top-ranked, TTS-able (`ok`) cards from **System 1 + Life Optimization** — merged into one counterfactual-impact ranking via the pairwise comparator — and move them into the queue (synthesizing audio as added) until ≥ 20h **or the clean extractable pool is exhausted** (soft floor — see review #4). If the pool can't reach 20h, log the shortfall and fill what's available; never loop forever chasing an unreachable target.
4. Keep the queue itself ordered by counterfactual impact (top = listen next).
The two source lists are already individually ranked (Phase 2 / weekly inserts); merge their heads with `insert_sorted` rather than re-sorting from scratch. `TARGET_QUEUE_HOURS` is a soft floor, informed by Task 5's `yield_report.json`.

- [ ] **Step 1: Failing test** (mocked client/cache/engine): queue at 18h tops up past 20h by moving the next-best cards; never pulls from System 2; stops once ≥20h.
- [ ] **Step 2–5: Run→FAIL, implement, PASS, commit.**
- [ ] **Step 6:** define "done" handling: a `Done`/archive convention — when Jay archives the top queue card (listened), the next weekly run (and an optional on-demand `--refill`) re-tops the queue. Document this in `reports/usage.md`.

### Task 13: Podcast RSS + R2 hosting

**Files:** Create `src/counterfactual_podcast/rss.py`, `tests/test_rss.py`; add R2 creds to `.env`.

Responsibilities: upload each queue audio file to Cloudflare R2 (S3 API, zero egress), generate a valid podcast RSS (`feedgen`, with iTunes extension) with one `<item>` per queue card **in priority order**, upload `rss.xml` to R2. Jay subscribes to the feed URL in any podcast app; "archive once done" maps to removing/marking items each run.

> **"Private" feed (review #6 — a real privacy decision).** A plain public bucket exposes Jay's entire reading profile to anyone who sees/guesses the URL. Podcast apps don't do auth well, so the pragmatic privacy model is **"unlisted + unguessable"**: host all audio and `rss.xml` under a random UUID prefix (e.g. `r2-base/<uuid4>/rss.xml`) that lives only in `.env`, and disable directory listing. **Stronger option (recommended if Jay wants real protection):** a tiny Cloudflare Worker that gates the feed + enclosures behind a secret token query param. Default to UUID-prefix; flag the Worker option for Jay to choose. **This is surfaced as a decision in the presentation, not silently downgraded.**

- [ ] **Step 1: Failing test:** building a feed from 3 queue assets yields valid RSS with 3 enclosure items in queue order and correct `<itunes:duration>`; uploads mocked (`boto3` stub).
- [ ] **Step 2–5: Run→FAIL, implement (boto3 R2 client, `feedgen` with itunes extension), PASS, commit.**
- [ ] **Step 6: One-time R2 setup doc** in `reports/r2-setup.md` (create bucket, enable public access / r2.dev domain, add `R2_ACCOUNT_ID/R2_ACCESS_KEY/R2_SECRET/R2_BUCKET/R2_PUBLIC_BASE` to `.env`). Gate: needs Jay's Cloudflare account — flagged as a setup step before first weekly run.

---

## Phase 5 — Weekly Inbox Automation

### Task 14: Inbox source

**Files:** Create `src/counterfactual_podcast/inbox.py`, `tests/test_inbox.py`. **Path confirmed (Task 1).**

Responsibilities: `inbox_list_id(client)` resolves `GET /members/me?fields=inbox → inbox.idList` dynamically; `collect_inbox(client) -> list[Card]` reads that list, ensures a `To Be Processed` list exists, moves all inbox cards there, returns them. Idempotent (safe to re-run mid-week). Footnote fallback (only if the `inbox` member field disappears): read a board list named `Inbox`.

- [ ] **Step 1: Failing test** (mocked source): N inbox items → all moved into `To Be Processed`, returned list length N, empty inbox → no-op.
- [ ] **Step 2–5: Run→FAIL, implement both source variants behind one interface, PASS, commit.**

### Task 15: Router/classifier (System 1 / System 2 / Life Optim)

**Files:** Create `src/counterfactual_podcast/classify.py`, `tests/test_classify.py`.

Responsibilities: `classify_card(content) -> {"system1","system2","life_optim"}` via a single cached Claude call using the profile doc's definitions (System 1 = doesn't require effortful focus; System 2 = requires deep focus; Life Optim = productivity/health/career/meta). Prompt-cached profile doc; returns a label + 1-line reason.

- [ ] **Step 1: Failing test** (mocked Claude): a dense robotics paper → `system2`; a quick newsletter take → `system1`; a habits article → `life_optim`.
- [ ] **Step 2–5: Run→FAIL, implement, PASS, commit.**

### Task 16: Weekly pipeline (orchestration)

**Files:** Create `src/counterfactual_podcast/pipelines/weekly.py`, `tests/test_weekly.py`.

Responsibilities: end-to-end weekly run, each step logged with timestamps to `logs/weekly-<ts>.log`:
1. `collect_inbox` → `To Be Processed`.
2. For each card: `extract` → `classify` → `insert_sorted` into the target list at its counterfactual-impact rank (binary insertion using the cached comparator), then move the Trello card into that list at the computed position. Write the desc rank marker. *(Note: binary insertion assumes the target list is a true total order; it inherits whatever ordering noise the one-shot sort left — acceptable given low weekly volume, per review #8.)*
3. `ensure_listen_queue` (top-up to 20h, synthesizing audio for newly added queue cards).
4. `rss.publish` (upload audio + feed to R2).
5. Print a summary: #processed, per-list counts, queue hours, feed URL.

- [ ] **Step 1: Failing test** (all deps mocked): 3 inbox cards get classified, inserted, and the queue/RSS steps invoked once; assert ordering calls and that System 2 cards never enter the queue.
- [ ] **Step 2–5: Run→FAIL, implement, PASS, commit.**

---

## Phase 6 — Scheduling, Logging, Docs

### Task 17: Logging + tmux/cron runners

**Files:** Create `src/counterfactual_podcast/logging_setup.py`, `scripts/run_weekly.sh`, `tests/test_logging.py`.

- [ ] **Step 1:** `setup_logging(name)` → timestamped file in `logs/` + console; test asserts a log file is created with the run name + timestamp.
- [ ] **Step 2: Implement.** `scripts/run_weekly.sh`:
```bash
#!/usr/bin/env bash
set -euo pipefail
cd /Users/jeqcho/automated-counterfactual-podcast
TS=$(date +%Y%m%d-%H%M%S)
uv run python -m counterfactual_podcast.pipelines.weekly 2>&1 | tee "logs/weekly-$TS.log"
```
- [ ] **Step 3: Schedule weekly** via macOS `launchd` (survives reboots better than cron on Mac) — a `com.jeqcho.counterfactual-podcast.weekly.plist` running Mondays 06:00; document `launchctl load` in `reports/usage.md`. First runs invoked manually in tmux to watch logs. **Caveat (review #10):** `launchd` does NOT wake a sleeping Mac, and a missed run fires once on wake. Recommend running while plugged in, or add a `pmset repeat wake` entry / set `StartCalendarInterval` with the Mac kept awake. Document this so a sleeping laptop doesn't silently skip weeks.
- [ ] **Step 4: Commit.**

### Task 18: Usage & runbook docs

**Files:** Create `reports/usage.md`.

- [ ] **Step 1:** Document: how to run the one-shot sort; how the weekly job works; how to mark a queue item "done" (archive); how to swap TTS engine; how to re-top the queue on demand; where logs live; how to reverse a bad sort from the snapshot json. **Step 2: Commit.**

---

## Cost & performance notes

- **Pairwise comparisons today:** ~6,700 calls (merge sort + Copeland-head), each ≈ (cached 4k-token profile + ~2.5k tokens of two article excerpts in, ~80 tokens out), run at concurrency 12. With prompt caching the profile doc is charged once per ~5-min window. Rough order: a few dollars on Sonnet; Opus escalations are a small minority. **~20–30 min wall-clock**, not hours.
- **Weekly:** inbox is usually small (tens of cards) → tens of `insert_sorted` runs × ~log₂(300)≈9 comparisons ≈ a few hundred cached calls + classification. Cheap.
- **TTS:** Kokoro local = $0; ~20h/week generated incrementally and cached so only *new* queue cards are synthesized.
- **Caching everywhere** means re-runs and crashes are cheap to resume.

## Sequencing summary

1. **Task 1 spike** (inbox) — parallelizable with the one-time sort.
2. **Tasks 2–9** → deliver **today's one-time pairwise sort** (the thing Jay wants today). Approve the Life-Optimization dry run before applying.
3. **Tasks 10–13** → TTS + listen queue + podcast feed (needs R2 setup).
4. **Tasks 14–18** → weekly automation + scheduling.

## Decisions locked
- Ordering = **LLM pairwise** (merge sort today, binary insertion weekly), context = `private/jay-profile-for-article-classification.md`, judge = Claude (Sonnet 4.6, Opus 4.8 for close calls), prompt-cached.
- Reorder cards **in place**; attach rank + rationale as an idempotent description marker (not a comment).
- Listen queue tops up to **20h from System 1 + Life Optimization only**.
- TTS = **Kokoro local** (pluggable to OpenAI/Fish/Qwen).
- Delivery = **private podcast RSS feed** hosted on Cloudflare R2.
- Inbox = **native Trello Inbox**, resolved via `member.inbox.idList` (confirmed live; workflow unchanged).

## Decisions to surface to Jay before/at execution (don't discover late)
1. **Feed privacy** (review #6): default is "unlisted + unguessable UUID URL." If Jay wants real protection, opt into the Cloudflare Worker token gate. Decide before first publish.
2. **TTS engine** after the Task 10 quality check: stay on free local Kokoro, or pay ~$15/wk for OpenAI `tts-1` if quality matters more.
3. **20h reachability** (review #4): if Task 5's yield report shows the clean pool is < 20h, the queue target is treated as a soft floor.
```
