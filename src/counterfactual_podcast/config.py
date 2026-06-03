"""Central configuration: env, Trello IDs, model IDs, paths, constants.

All secrets come from the gitignored .env. List/board IDs are grounded from the
live board (see CLAUDE.md / reports/). Model IDs are env-overridable so a
wrong/retired ID never hard-codes a 404.
"""
from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

ROOT = Path(__file__).resolve().parents[2]

# --- Trello ---------------------------------------------------------------
BOARD_ID = "657f3741ecf6b2f7a40ef8df"          # "Home base"
MEMBER_ID = "5a8056af894b0bfba8179ee4"         # chooijeqin
SYSTEM1_LIST_ID = "683cb9f4387706ad70dc4299"   # "Reading list that doesn't require system 2"
SYSTEM2_LIST_ID = "683cb9e94b55936c9e9505a3"   # "Reading list that requires system 2"
LIFE_OPTIM_LIST_ID = "69cffff85c64bd09a7c8cd7d"  # "Life Optimization"

# Lists the listen queue may top up from (System 2 excluded — needs focused reading).
QUEUE_SOURCE_LIST_IDS = (SYSTEM1_LIST_ID, LIFE_OPTIM_LIST_ID)

TO_BE_PROCESSED_LIST_NAME = "To Be Processed"   # Phase 1 output (you review here)
READY_TO_PROCESS_LIST_NAME = "▶ Ready to Process"  # Phase 2 trigger (drag here to process)
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

# --- Models (confirmed current 2026-06; overridable) ----------------------
CLAUDE_MODEL = os.environ.get("CLAUDE_MODEL", "claude-sonnet-4-6")               # comparator workhorse
CLAUDE_MODEL_ESCALATE = os.environ.get("CLAUDE_MODEL_ESCALATE", "claude-opus-4-8")  # close calls (step>=6)
CLAUDE_MODEL_DIGEST = os.environ.get("CLAUDE_MODEL_DIGEST", "claude-haiku-4-5-20251001")  # enrichment digests
MAX_LLM_CONCURRENCY = int(os.environ.get("MAX_LLM_CONCURRENCY", "50"))     # concurrent Anthropic calls
MAX_FETCH_CONCURRENCY = int(os.environ.get("MAX_FETCH_CONCURRENCY", "50"))  # concurrent URL fetches (threads)
# Hard cap on article text sent to Haiku per digest — bounds worst-case cost so one
# giant page can't blow up spend. 24000 chars ≈ 6k tokens ≈ ~$0.006/card on Haiku.
DIGEST_TEXT_CAP_CHARS = int(os.environ.get("DIGEST_TEXT_CAP_CHARS", "24000"))

# --- TTS ------------------------------------------------------------------
TTS_ENGINE = os.environ.get("TTS_ENGINE", "kokoro")
KOKORO_VOICE = os.environ.get("KOKORO_VOICE", "af_heart")
KOKORO_MODEL_PATH = os.environ.get("KOKORO_MODEL_PATH", str(DATA / "kokoro-v1.0.onnx"))
KOKORO_VOICES_PATH = os.environ.get("KOKORO_VOICES_PATH", str(DATA / "voices-v1.0.bin"))
GOOGLE_TTS_VOICE = os.environ.get("GOOGLE_TTS_VOICE", "en-US-Neural2-D")
GOOGLE_TTS_LANGUAGE = os.environ.get("GOOGLE_TTS_LANGUAGE", "en-US")


def ensure_dirs() -> None:
    for d in (OUTPUTS, LOGS, DATA):
        d.mkdir(parents=True, exist_ok=True)
