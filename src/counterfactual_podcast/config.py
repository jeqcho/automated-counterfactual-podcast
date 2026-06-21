"""Central configuration: env, Trello IDs, model IDs, paths, constants.

All secrets come from the gitignored .env. List/board IDs are grounded from the
live board (see CLAUDE.md / reports/). Model IDs are env-overridable so a
wrong/retired ID never hard-codes a 404.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

# override=True: .env is the project's authoritative config, even if the surrounding
# shell already exports the same names (e.g. a personal ANTHROPIC_API_KEY in ~/.zshrc
# that points at a different/empty-balance account). Without override, an exported shell
# var silently wins and you end up using the wrong key. In the cloud container there's no
# .env file, so this is a no-op there and the Worker-forwarded env vars are used as-is.
load_dotenv(override=True)

ROOT = Path(__file__).resolve().parents[2]

# --- Trello ---------------------------------------------------------------
BOARD_ID = "657f3741ecf6b2f7a40ef8df"          # "Home base"
MEMBER_ID = "5a8056af894b0bfba8179ee4"         # chooijeqin
SYSTEM1_LIST_ID = "683cb9f4387706ad70dc4299"   # "Reading list that doesn't require system 2"
SYSTEM2_LIST_ID = "683cb9e94b55936c9e9505a3"   # "Reading list that requires system 2"
LIFE_OPTIM_LIST_ID = "69cffff85c64bd09a7c8cd7d"  # "Life Optimization"

# Lists the listen queue may top up from (System 2 excluded — needs focused reading).
QUEUE_SOURCE_LIST_IDS = (SYSTEM1_LIST_ID, LIFE_OPTIM_LIST_ID)

# Phase 1 moves reading links here; you prune (drag wrong ones back to Inbox); Phase 2
# then processes whatever remains in this same list. (No separate "Ready to Process" list.)
TO_BE_PROCESSED_LIST_NAME = "To Be Processed"
LISTEN_QUEUE_LIST_NAME = "Listen Queue"

# --- Ranking / queue constants -------------------------------------------
TARGET_QUEUE_HOURS = 20          # soft floor for the listen queue
WPM_READING = 230                # reading-time estimate (ranking denominator)
COPELAND_HEAD = 40               # stabilize the top-N of each list with round-robin

# --- Paths ----------------------------------------------------------------
OUTPUTS = ROOT / "outputs"
LOGS = ROOT / "logs"
DATA = ROOT / "data"
CACHE_DB = OUTPUTS / "cache.sqlite3"
PROFILE_DOC = ROOT / "private" / "jay-profile-for-article-classification.scoped.md"

# --- Secrets --------------------------------------------------------------
TRELLO_KEY = os.environ.get("TRELLO_API_KEY")
TRELLO_TOKEN = os.environ.get("TRELLO_TOKEN")
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")

R2_ACCOUNT_ID = os.environ.get("R2_ACCOUNT_ID")
R2_ACCESS_KEY_ID = os.environ.get("R2_ACCESS_KEY_ID")
R2_SECRET_ACCESS_KEY = os.environ.get("R2_SECRET_ACCESS_KEY")
R2_BUCKET = os.environ.get("R2_BUCKET")
R2_PUBLIC_BASE = os.environ.get("R2_PUBLIC_BASE")
# Stable unguessable path prefix for the podcast feed ("unlisted" privacy). Pin it in
# .env so the feed URL Jay subscribes to never changes across re-publishes.
PODCAST_PREFIX = os.environ.get("PODCAST_PREFIX", "")

# --- Models (confirmed current 2026-06; overridable) ----------------------
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")               # comparator workhorse
CLAUDE_MODEL_ESCALATE = os.environ.get("CLAUDE_MODEL_ESCALATE", "claude-opus-4-8")  # close calls (step>=6)
CLAUDE_MODEL_DIGEST = os.environ.get("CLAUDE_MODEL_DIGEST", "claude-haiku-4-5-20251001")  # enrichment digests
MAX_LLM_CONCURRENCY = int(os.environ.get("MAX_LLM_CONCURRENCY", "50"))     # concurrent Anthropic calls
MAX_FETCH_CONCURRENCY = int(os.environ.get("MAX_FETCH_CONCURRENCY", "50"))  # concurrent URL fetches (threads)
# Hard cap on article text sent to Haiku per digest — bounds worst-case cost so one
# giant page can't blow up spend. 24000 chars ≈ 6k tokens ≈ ~$0.006/card on Haiku.
DIGEST_TEXT_CAP_CHARS = int(os.environ.get("DIGEST_TEXT_CAP_CHARS", "24000"))
# Cards we can't fully extract (paywalls) but that expose an og:description abstract are
# ranked on that abstract (kind="abstract", ok=False -> excluded from the podcast). We have
# no real reading time, so assume a typical longform-article length for the impact-per-minute
# comparator step rather than 0 (which would wrongly treat them as instant reads).
ABSTRACT_DEFAULT_MINUTES = int(os.environ.get("ABSTRACT_DEFAULT_MINUTES", "8"))

# --- TTS ------------------------------------------------------------------
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
KOKORO_MODEL_PATH = os.environ.get("KOKORO_MODEL_PATH", str(DATA / "kokoro-v1.0.onnx"))
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES_PATH", str(DATA / "voices-v1.0.bin"))
GOOGLE_TTS_VOICE = os.environ.get("GOOGLE_TTS_VOICE", "en-US-Neural2-D")
GOOGLE_TTS_LANGUAGE = os.environ.get("GOOGLE_TTS_LANGUAGE", "en-US")
# Speak the episode title at the start of each audio so it's clear where one article
# ends and the next begins (set SPEAK_TITLE_INTRO=0 to disable).
SPEAK_TITLE_INTRO = os.environ.get("SPEAK_TITLE_INTRO", "1") not in ("0", "false", "False", "")
# NB: no per-card text cap — we synthesize the FULL article (one article = one episode).
# Comment sections are stripped at extraction (extract.py) so length stays sane; speed
# comes from a fast TTS provider, not from truncating content.
# ONNX execution provider for Kokoro (Apple Silicon: CoreML is much faster than CPU).
KOKORO_ONNX_PROVIDER = os.environ.get("KOKORO_ONNX_PROVIDER", "")


def ensure_dirs() -> None:
    for d in (OUTPUTS, LOGS, DATA):
        d.mkdir(parents=True, exist_ok=True)


def _materialize_google_credentials() -> None:
    """In the container the Google service-account JSON is passed as a secret
    (GOOGLE_CREDENTIALS_JSON) and written to the GOOGLE_APPLICATION_CREDENTIALS path
    (which the google client reads via ADC). No-op locally / if already present."""
    blob = os.environ.get("GOOGLE_CREDENTIALS_JSON")
    dest = os.environ.get("GOOGLE_APPLICATION_CREDENTIALS")
    if not (blob and dest):
        return
    try:
        p = Path(dest)
        if not p.exists():
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(blob)
    except Exception:
        pass


_materialize_google_credentials()
