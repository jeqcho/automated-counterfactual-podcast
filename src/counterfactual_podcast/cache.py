"""SQLite cache: makes extraction, enrichment, comparisons and audio resumable.

Keyed so re-runs skip network/LLM work and the merge sort is resumable. Pairwise
results are stored in canonical (sorted) pair order and the winner is flipped on
lookup, so get(a,b) and get(b,a) agree.
"""
from __future__ import annotations

import os
import sqlite3
import time
from pathlib import Path

from .models import AudioAsset, CardFeatures, ExtractedContent, PairwiseResult

# Where the SQLite cache lives in R2 (cloud: containers scale to zero & lose disk,
# so we pull it on start and push it after each run).
R2_CACHE_KEY = "state/cache.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extracted (
    card_id TEXT PRIMARY KEY, title TEXT, text TEXT, word_count INTEGER,
    est_minutes INTEGER, kind TEXT, ok INTEGER, note TEXT, fetched_at REAL
);
CREATE TABLE IF NOT EXISTS digest (
    card_id TEXT PRIMARY KEY, title TEXT, est_minutes INTEGER, digest TEXT,
    kind TEXT, ok INTEGER, model TEXT, ts REAL
);
CREATE TABLE IF NOT EXISTS pairwise (
    a_id TEXT, b_id TEXT, winner_id TEXT, step INTEGER, why TEXT, model TEXT, ts REAL,
    PRIMARY KEY (a_id, b_id)
);
CREATE TABLE IF NOT EXISTS audio (
    card_id TEXT PRIMARY KEY, path TEXT, seconds REAL, engine TEXT, ts REAL
);
"""


class Cache:
    def __init__(self, path: str | Path = ":memory:"):
        self.conn = sqlite3.connect(str(path))
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(_SCHEMA)
        self.conn.commit()

    # --- extracted -------------------------------------------------------
    def put_extracted(self, c: ExtractedContent) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO extracted VALUES (?,?,?,?,?,?,?,?,?)",
            (c.card_id, c.title, c.text, c.word_count, c.est_minutes,
             c.kind, int(c.ok), c.note, time.time()),
        )
        self.conn.commit()

    def get_extracted(self, card_id: str) -> ExtractedContent | None:
        r = self.conn.execute("SELECT * FROM extracted WHERE card_id=?", (card_id,)).fetchone()
        if not r:
            return None
        return ExtractedContent(r["card_id"], r["title"], r["text"], r["word_count"],
                                r["est_minutes"], r["kind"], bool(r["ok"]), r["note"])

    # --- digest ----------------------------------------------------------
    def put_digest(self, f: CardFeatures, model: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO digest VALUES (?,?,?,?,?,?,?,?)",
            (f.card_id, f.title, f.est_minutes, f.digest, f.kind, int(f.ok), model, time.time()),
        )
        self.conn.commit()

    def get_digest(self, card_id: str) -> CardFeatures | None:
        r = self.conn.execute("SELECT * FROM digest WHERE card_id=?", (card_id,)).fetchone()
        if not r:
            return None
        return CardFeatures(r["card_id"], r["title"], r["est_minutes"], r["digest"],
                            r["kind"], bool(r["ok"]))

    # --- pairwise (symmetric-aware) -------------------------------------
    @staticmethod
    def _key(a: str, b: str) -> tuple[str, str, bool]:
        """Return (canonical_a, canonical_b, flipped)."""
        if a <= b:
            return a, b, False
        return b, a, True

    def put_pairwise(self, a: str, b: str, res: PairwiseResult) -> None:
        ca, cb, _ = self._key(a, b)
        self.conn.execute(
            "INSERT OR REPLACE INTO pairwise VALUES (?,?,?,?,?,?,?)",
            (ca, cb, res.winner_id, res.step, res.why, res.model, time.time()),
        )
        self.conn.commit()

    def get_pairwise(self, a: str, b: str) -> PairwiseResult | None:
        ca, cb, _ = self._key(a, b)
        r = self.conn.execute(
            "SELECT * FROM pairwise WHERE a_id=? AND b_id=?", (ca, cb)
        ).fetchone()
        if not r:
            return None
        return PairwiseResult(r["winner_id"], r["step"], r["why"], r["model"])

    # --- audio -----------------------------------------------------------
    def put_audio(self, a: AudioAsset) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO audio VALUES (?,?,?,?,?)",
            (a.card_id, a.path, a.seconds, a.engine, time.time()),
        )
        self.conn.commit()

    def get_audio(self, card_id: str) -> AudioAsset | None:
        r = self.conn.execute("SELECT * FROM audio WHERE card_id=?", (card_id,)).fetchone()
        if not r:
            return None
        return AudioAsset(r["card_id"], r["path"], r["seconds"], r["engine"])

    def close(self) -> None:
        self.conn.close()


# --- R2 durability (cloud: containers scale to zero & lose local disk) -----------
def pull_cache_from_r2(path=None, key: str = R2_CACHE_KEY) -> bool:
    """Download the cache DB from R2 before a run (no-op if R2 unconfigured or absent)."""
    from . import config
    from .r2 import r2_client, r2_configured
    if not r2_configured():
        return False
    p = str(path or config.CACHE_DB)
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    try:
        r2_client().download_file(config.R2_BUCKET, key, p)
        return True
    except Exception:
        return False  # first run: no cache in R2 yet


def push_cache_to_r2(path=None, key: str = R2_CACHE_KEY) -> bool:
    """Upload the cache DB to R2 after a run (preserves digests/comparisons/audio rows)."""
    from . import config
    from .r2 import r2_client, r2_configured
    if not r2_configured():
        return False
    p = str(path or config.CACHE_DB)
    if not os.path.exists(p):
        return False
    try:
        r2_client().upload_file(p, config.R2_BUCKET, key)
        return True
    except Exception:
        return False
