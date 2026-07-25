"""SQLite cache: makes extraction, enrichment, comparisons and audio resumable.

Keyed so re-runs skip network/LLM work and the merge sort is resumable. Pairwise
results are stored in canonical (sorted) pair order and the winner is flipped on
lookup, so get(a,b) and get(b,a) agree.
"""
from __future__ import annotations

import logging
import os
import sqlite3
import time
from pathlib import Path

from .models import AudioAsset, CardFeatures, ExtractedContent, PairwiseResult

log = logging.getLogger(__name__)

# Where the SQLite cache lives in R2 (cloud: containers scale to zero & lose disk,
# so we pull it on start and push it after each run).
R2_CACHE_KEY = "state/cache.sqlite3"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS extracted (
    card_id TEXT PRIMARY KEY, title TEXT, text TEXT, word_count INTEGER,
    est_minutes INTEGER, kind TEXT, ok INTEGER, note TEXT, fetched_at REAL,
    author TEXT DEFAULT '', published TEXT DEFAULT ''
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
        self._migrate()
        self.conn.commit()

    def _migrate(self) -> None:
        """Add columns to pre-existing DBs (cache is durable in R2 across schema bumps)."""
        cols = {row[1] for row in self.conn.execute("PRAGMA table_info(extracted)")}
        for col in ("author", "published"):
            if col not in cols:
                self.conn.execute(f"ALTER TABLE extracted ADD COLUMN {col} TEXT DEFAULT ''")

    # --- extracted -------------------------------------------------------
    def put_extracted(self, c: ExtractedContent) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO extracted "
            "(card_id,title,text,word_count,est_minutes,kind,ok,note,fetched_at,author,published) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (c.card_id, c.title, c.text, c.word_count, c.est_minutes,
             c.kind, int(c.ok), c.note, time.time(), c.author, c.published),
        )
        self.conn.commit()

    def get_extracted(self, card_id: str) -> ExtractedContent | None:
        r = self.conn.execute("SELECT * FROM extracted WHERE card_id=?", (card_id,)).fetchone()
        if not r:
            return None
        keys = r.keys()
        return ExtractedContent(r["card_id"], r["title"], r["text"], r["word_count"],
                                r["est_minutes"], r["kind"], bool(r["ok"]), r["note"],
                                author=r["author"] if "author" in keys else "",
                                published=r["published"] if "published" in keys else "")

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
#
# ⚠️ 2026-07-25: the R2 cache was found TRUNCATED BY EXACTLY ONE 4096-BYTE PAGE — the header
# claimed 6727 pages, the file held 6726 — so every run died on `sqlite3.DatabaseError:
# database disk image is malformed` the moment it opened the cache. Two bugs combined:
#   1. push uploaded the LIVE db file while a Cache connection was still open, so it could
#      capture a torn snapshot (a committed page not yet flushed to the main db file).
#   2. the caller's `finally: push_cache_to_r2()` then re-uploaded that corrupt file after
#      the crash — so each button press pulled corruption, died, and pushed it back. A
#      self-perpetuating loop no amount of re-pressing could escape.
# Fixes below: push a CONSISTENT snapshot via the sqlite backup API (safe with readers/writers
# attached) and never push or accept a db that fails an integrity check.
def _sqlite_ok(path: str) -> bool:
    """True if `path` is a readable, structurally sound SQLite db."""
    try:
        con = sqlite3.connect(path)
        try:
            return con.execute("PRAGMA quick_check").fetchone()[0] == "ok"
        finally:
            con.close()
    except Exception:
        return False


def _consistent_snapshot(src: str, dst: str) -> bool:
    """Copy `src` -> `dst` via the sqlite backup API.

    Unlike a filesystem copy this takes a read lock and walks pages through sqlite itself,
    so the result is transactionally consistent even while another connection is writing —
    which is exactly the case here (the pipeline's Cache is still open when we push)."""
    try:
        con = sqlite3.connect(src)
        try:
            out = sqlite3.connect(dst)
            try:
                con.backup(out)
            finally:
                out.close()
        finally:
            con.close()
        return True
    except Exception:
        return False


def pull_cache_from_r2(path=None, key: str = R2_CACHE_KEY) -> bool:
    """Download the cache DB from R2 before a run (no-op if R2 unconfigured or absent).

    A corrupt download is QUARANTINED rather than handed to the pipeline: the run then
    starts from an empty cache (slow but correct) instead of crashing on open."""
    from . import config
    from .r2 import r2_client, r2_configured
    if not r2_configured():
        return False
    p = str(path or config.CACHE_DB)
    Path(p).parent.mkdir(parents=True, exist_ok=True)
    try:
        r2_client().download_file(config.R2_BUCKET, key, p)
    except Exception:
        return False  # first run: no cache in R2 yet
    if not _sqlite_ok(p):
        quarantine = f"{p}.corrupt-{int(time.time())}"
        try:
            os.replace(p, quarantine)
        except OSError:
            pass
        log.error("cache pulled from R2 is MALFORMED — quarantined to %s; starting with an "
                  "empty cache. The R2 copy still needs repair (sqlite3 .recover).", quarantine)
        return False
    return True


def push_cache_to_r2(path=None, key: str = R2_CACHE_KEY) -> bool:
    """Upload the cache DB to R2 after a run (preserves digests/comparisons/audio rows).

    Uploads a consistent snapshot, and REFUSES to publish a db that fails an integrity
    check — otherwise a crashed run overwrites good state in R2 with its own corruption."""
    from . import config
    from .r2 import r2_client, r2_configured
    if not r2_configured():
        return False
    p = str(path or config.CACHE_DB)
    if not os.path.exists(p):
        return False
    if not _sqlite_ok(p):
        log.error("refusing to push cache to R2: local db %s is malformed (this would "
                  "overwrite the good copy in R2 with corruption)", p)
        return False
    snap = f"{p}.snapshot"
    try:
        if not _consistent_snapshot(p, snap) or not _sqlite_ok(snap):
            log.error("refusing to push cache to R2: could not take a consistent snapshot")
            return False
        r2_client().upload_file(snap, config.R2_BUCKET, key)
        return True
    except Exception:
        return False
    finally:
        try:
            os.remove(snap)
        except OSError:
            pass
