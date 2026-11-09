"""Read listening progress from the Apple Podcasts library on the Mac.

Apple Podcasts has no API for play state. The only reliable source is the app's own
Core Data store on the Mac, which iCloud "Sync Library" keeps in step with the iPhone:

    ~/Library/Group Containers/243LU875E5.groups.com.apple.podcasts/Documents/MTLibrary.sqlite

Each row of ``ZMTEPISODE`` is one episode. Our feed sets every item's GUID to its Trello
card id, and every enclosure ends in ``/{card_id}.mp3``, so an episode maps straight back
to its Listen Queue card.

"Done" means basically finished, not started. An episode counts as done when either:
  * its playhead is at least ``threshold`` (default 90%) of its duration, or
  * Apple has marked it played (play count / has-been-played set) and reset the
    playhead to ~0, which is what the app does when you finish an episode or tap
    "Mark as Played".

The schema is private and undocumented, so columns are discovered with PRAGMA and any
missing one is treated as absent rather than crashing.
"""
from __future__ import annotations

import os
import re
import shutil
import sqlite3
import tempfile
from dataclasses import dataclass
from pathlib import Path

DEFAULT_LIBRARY = (
    Path.home() / "Library" / "Group Containers"
    / "243LU875E5.groups.com.apple.podcasts" / "Documents" / "MTLibrary.sqlite"
)

DEFAULT_THRESHOLD = 0.90
# Apple resets the playhead to 0 when an episode finishes; allow a little slack.
_RESET_PLAYHEAD_SECONDS = 1.0

_CARD_ID_RE = re.compile(r"^[0-9a-f]{24}$")
_MP3_RE = re.compile(r"/([0-9a-f]{24})\.mp3(?:[?#].*)?$")

_EPISODE_COLS = ("ZGUID", "ZENCLOSUREURL", "ZTITLE", "ZPLAYHEAD", "ZDURATION",
                 "ZPLAYCOUNT", "ZHASBEENPLAYED", "ZPLAYSTATE", "ZLASTDATEPLAYED", "ZPODCAST")


@dataclass
class EpisodeProgress:
    card_id: str
    title: str
    playhead: float
    duration: float
    play_count: int
    has_been_played: bool
    raw: dict

    @property
    def fraction(self) -> float:
        return self.playhead / self.duration if self.duration > 0 else 0.0

    def is_done(self, threshold: float = DEFAULT_THRESHOLD) -> bool:
        if self.duration > 0 and self.fraction >= threshold:
            return True
        marked_played = self.play_count >= 1 or self.has_been_played
        return marked_played and self.playhead <= _RESET_PLAYHEAD_SECONDS


def card_id_for(guid: str | None, enclosure_url: str | None) -> str | None:
    """Map an Apple Podcasts episode back to our Trello card id (None if not ours)."""
    g = (guid or "").strip()
    if _CARD_ID_RE.match(g):
        return g
    m = _MP3_RE.search(enclosure_url or "")
    return m.group(1) if m else None


def _snapshot(library: Path) -> str:
    """Copy the live DB (+ WAL/SHM) to a temp file so we never lock or touch Apple's copy."""
    tmpdir = tempfile.mkdtemp(prefix="mtlibrary-")
    dst = os.path.join(tmpdir, "MTLibrary.sqlite")
    shutil.copy2(library, dst)
    for suffix in ("-wal", "-shm"):
        side = Path(str(library) + suffix)
        if side.exists():
            shutil.copy2(side, dst + suffix)
    return dst


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def read_progress(prefix: str, library: Path | str = DEFAULT_LIBRARY) -> list[EpisodeProgress]:
    """Return progress for every episode of OUR feed (enclosure under ``/{prefix}/``)."""
    library = Path(library)
    if not library.exists():
        raise FileNotFoundError(
            f"Apple Podcasts library not found at {library}. This only runs on the Mac "
            "that has Apple Podcasts subscribed to the feed.")
    snap = _snapshot(library)
    try:
        conn = sqlite3.connect(snap)
        conn.row_factory = sqlite3.Row
        have = _columns(conn, "ZMTEPISODE")
        cols = [c for c in _EPISODE_COLS if c in have]
        rows = conn.execute(f"SELECT {', '.join(cols)} FROM ZMTEPISODE").fetchall()
        conn.close()
    finally:
        shutil.rmtree(os.path.dirname(snap), ignore_errors=True)

    out = []
    for r in rows:
        d = dict(r)
        enc = d.get("ZENCLOSUREURL") or ""
        if prefix and f"/{prefix}/" not in enc:
            continue
        cid = card_id_for(d.get("ZGUID"), enc)
        if not cid:
            continue
        out.append(EpisodeProgress(
            card_id=cid,
            title=d.get("ZTITLE") or "",
            playhead=float(d.get("ZPLAYHEAD") or 0.0),
            duration=float(d.get("ZDURATION") or 0.0),
            play_count=int(d.get("ZPLAYCOUNT") or 0),
            has_been_played=bool(d.get("ZHASBEENPLAYED") or 0),
            raw=d,
        ))
    return out
