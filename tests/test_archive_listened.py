import sqlite3

from counterfactual_podcast.apple_podcasts import EpisodeProgress, card_id_for, read_progress
from counterfactual_podcast.models import Card
from counterfactual_podcast.pipelines import archive_listened as al

PREFIX = "4b1eb250c30c47558534e62b20620d25"
A, B, C, D = "a" * 24, "b" * 24, "c" * 24, "d" * 24


def _ep(cid, playhead, duration, plays=0, played=False):
    return EpisodeProgress(cid, "t", playhead, duration, plays, played, {})


def test_done_rules():
    assert _ep(A, 950, 1000).is_done()               # 95% through
    assert _ep(A, 900, 1000).is_done()               # exactly 90%
    assert not _ep(A, 10, 1000).is_done()            # 1% — just started
    assert not _ep(A, 500, 1000, plays=1).is_done()  # replaying, half way
    assert _ep(A, 0, 1000, plays=1).is_done()        # finished, playhead reset
    assert _ep(A, 0, 1000, played=True).is_done()    # "Mark as Played"
    assert not _ep(A, 0, 1000).is_done()             # never started
    assert not _ep(A, 0, 0).is_done()                # no duration, never played


def test_card_id_mapping():
    assert card_id_for(A, "") == A
    assert card_id_for("other-guid", f"https://x.dev/{PREFIX}/{B}.mp3") == B
    assert card_id_for("other-guid", "https://x.dev/foo.mp3") is None


def _make_library(path, rows):
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE ZMTEPISODE (Z_PK INTEGER PRIMARY KEY, ZGUID TEXT, "
                 "ZENCLOSUREURL TEXT, ZTITLE TEXT, ZPLAYHEAD REAL, ZDURATION REAL, "
                 "ZPLAYCOUNT INTEGER)")  # no ZHASBEENPLAYED: missing columns must be OK
    conn.executemany("INSERT INTO ZMTEPISODE (ZGUID, ZENCLOSUREURL, ZTITLE, ZPLAYHEAD, "
                     "ZDURATION, ZPLAYCOUNT) VALUES (?,?,?,?,?,?)", rows)
    conn.commit()
    conn.close()


def test_read_progress_filters_to_our_feed(tmp_path):
    lib = tmp_path / "MTLibrary.sqlite"
    base = f"https://pub.r2.dev/{PREFIX}"
    _make_library(lib, [
        (A, f"{base}/{A}.mp3", "ours done", 990.0, 1000.0, 0),
        (B, f"{base}/{B}.mp3", "ours started", 10.0, 1000.0, 0),
        (C, f"https://other.fm/{C}.mp3", "other show", 1000.0, 1000.0, 1),
    ])
    got = {p.card_id: p for p in read_progress(PREFIX, lib)}
    assert set(got) == {A, B}
    assert got[A].is_done() and not got[B].is_done()


class FakeClient:
    def __init__(self, cards):
        self.cards = cards
        self.archived = []

    def ensure_list(self, name):
        return "Q"

    def get_cards(self, qid):
        return self.cards

    def archive_card(self, cid):
        self.archived.append(cid)


def test_run_archives_only_done_queue_cards():
    cards = [Card(A, "done"), Card(B, "started"), Card(D, "not in apple")]
    # C is done in Apple but no longer in the Listen Queue: must not be touched.
    progress = [_ep(A, 960, 1000), _ep(B, 10, 1000), _ep(C, 1000, 1000)]
    calls = []

    client = FakeClient(cards)
    res = al.run(client, progress, apply=False, republish=lambda: calls.append(1))
    assert res["archived"] == [A] and client.archived == [] and calls == []

    res = al.run(client, progress, apply=True, republish=lambda: calls.append(1))
    assert client.archived == [A] and calls == [1] and res["republished"]


def test_run_no_republish_when_nothing_done():
    calls = []
    client = FakeClient([Card(B, "started")])
    al.run(client, [_ep(B, 10, 1000)], apply=True, republish=lambda: calls.append(1))
    assert client.archived == [] and calls == []
