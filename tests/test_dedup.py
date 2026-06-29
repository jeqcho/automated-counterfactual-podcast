from counterfactual_podcast.dedup import dedup_list, url_key
from counterfactual_podcast.models import Card


def test_url_key_normalizes_host_trailing_slash_and_tracking():
    a = Card("1", "a", url="https://www.Example.com/Post/")
    b = Card("2", "b", url="https://example.com/Post")
    c = Card("3", "c", url="https://example.com/Post?utm_source=newsletter&utm_medium=email")
    assert url_key(a) == url_key(b) == url_key(c)   # www/trailing-slash/utm all collapse


def test_url_key_keeps_meaningful_query_distinct():
    a = Card("1", "a", url="https://youtube.com/watch?v=AAA")
    b = Card("2", "b", url="https://youtube.com/watch?v=BBB")
    assert url_key(a) != url_key(b)                 # different videos stay distinct


def test_url_key_empty_when_no_link():
    assert url_key(Card("1", "just a note")) == ""


class FakeClient:
    def __init__(self, lists):
        self.lists = lists           # {list_id: [Card, ...]}
        self.archived = []

    def get_cards(self, list_id):
        return list(self.lists.get(list_id, []))

    def archive_card(self, card_id):
        self.archived.append(card_id)


def test_dedup_archives_within_list_duplicates():
    tbp = [
        Card("a", "art", url="https://x.org/a"),
        Card("b", "art again", url="https://x.org/a/"),   # dup of a (trailing slash)
        Card("c", "other", url="https://x.org/c"),
    ]
    client = FakeClient({"TBP": tbp})
    res = dedup_list(client, "TBP", [], apply=True)
    assert res["archived"] == 1
    assert client.archived == ["b"]                  # first kept, later dup archived


def test_dedup_archives_against_other_lists():
    tbp = [Card("a", "art", url="https://x.org/a"), Card("c", "new", url="https://x.org/c")]
    system1 = [Card("z", "already sorted", url="https://x.org/a")]   # 'a' already on board
    client = FakeClient({"TBP": tbp, "S1": system1})
    res = dedup_list(client, "TBP", ["S1"], apply=True)
    assert res["archived"] == 1 and client.archived == ["a"]   # the already-present one


def test_dedup_dry_run_archives_nothing():
    tbp = [Card("a", "art", url="https://x.org/a"), Card("b", "dup", url="https://x.org/a")]
    client = FakeClient({"TBP": tbp})
    res = dedup_list(client, "TBP", [], apply=False)
    assert res["archived"] == 1          # reports it would archive 1
    assert client.archived == []         # but didn't


def test_dedup_ignores_linkless_cards():
    tbp = [Card("a", "note one"), Card("b", "note two"), Card("c", "art", url="https://x.org/c")]
    client = FakeClient({"TBP": tbp})
    res = dedup_list(client, "TBP", [], apply=True)
    assert res["archived"] == 0 and client.archived == []
