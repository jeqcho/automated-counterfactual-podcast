from counterfactual_podcast.link_titles import is_tracker, plan_card, tidy_cards, unwrap
from counterfactual_podcast.models import Card
from counterfactual_podcast.trello import TrelloClient

TRACKER = "https://us.list-manage.com/vS9SWxWKr5E?e=2c938c0412&c2id=84bc"
NYT = "https://www.nytimes.com/2026/09/16/science/ai-recursive-self-improvement.html?x=1"
ARS = "https://arstechnica.com/ai/2026/07/simulating-everything/"


class Resp:
    def __init__(self, status, location=None):
        self.status_code, self.headers = status, ({"Location": location} if location else {})


def fake_get(routes):
    calls = []

    def get(url, **kw):
        assert kw.get("allow_redirects") is False      # never follow (paywalls hide the dest)
        calls.append(url)
        return routes.get(url, Resp(403))
    get.calls = calls
    return get


class FakeClient:
    def __init__(self):
        self.ops = []

    def add_attachment(self, card_id, url):
        self.ops.append(("attach", card_id, url))

    def set_name(self, card_id, name):
        self.ops.append(("name", card_id, name))


def test_is_tracker():
    assert is_tracker(TRACKER)
    assert is_tracker("https://techmeme.us14.list-manage.com/track/click?u=1&id=2")
    assert not is_tracker(NYT) and not is_tracker("https://notlist-manage.com/x")
    assert not is_tracker("") and not is_tracker(None)


def test_unwrap_reads_location_without_following():
    get = fake_get({TRACKER: Resp(302, NYT)})
    assert unwrap(TRACKER, get=get) == NYT
    assert unwrap(NYT, get=get) == NYT and get.calls == [TRACKER]   # non-tracker: no request


def test_unwrap_gives_up_cleanly():
    assert unwrap(TRACKER, get=fake_get({})) == TRACKER              # 403, no Location
    hop = "https://x.us1.list-manage.com/track/click?id=9"
    loop = fake_get({TRACKER: Resp(302, hop), hop: Resp(302, TRACKER)})
    assert unwrap(TRACKER, get=loop) == TRACKER                      # tracker loop


def test_tracker_card_gets_real_url_and_page_title():
    card = Card("c1", TRACKER, url=TRACKER)
    p = plan_card(card, get=fake_get({TRACKER: Resp(302, ARS)}),
                  fetch_title=lambda u: "Simulating everything" if u == ARS else None)
    assert p["attach"] == ARS and p["title"] == "Simulating everything"


def test_blocked_tracker_destination_falls_back_to_slug():
    card = Card("c1", TRACKER, url=TRACKER)
    p = plan_card(card, get=fake_get({TRACKER: Resp(302, NYT)}), fetch_title=lambda u: None)
    assert p["attach"] == NYT
    assert p["title"] == "Ai Recursive Self Improvement — nytimes.com"


def test_plain_bare_url_without_title_is_left_alone():
    assert plan_card(Card("c1", ARS, url=ARS), fetch_title=lambda u: None) is None


def test_plain_bare_url_without_attachment_gets_one_before_rename():
    p = plan_card(Card("c1", ARS), fetch_title=lambda u: "Simulating everything")
    assert p["attach"] == ARS and p["title"] == "Simulating everything"


def test_titled_cards_are_skipped():
    assert plan_card(Card("c1", "A real title", url=TRACKER), fetch_title=lambda u: "x") is None


def test_tidy_cards_attaches_before_renaming_and_respects_dry_run():
    cards = [Card("c1", TRACKER, url=TRACKER), Card("c2", "Already titled", url=ARS)]
    get = fake_get({TRACKER: Resp(302, ARS)})
    client = FakeClient()
    dry = tidy_cards(client, cards, apply=False, get=get, fetch_title=lambda u: "Sim")
    assert len(dry) == 1 and client.ops == []
    tidy_cards(client, cards, apply=True, get=get, fetch_title=lambda u: "Sim")
    assert client.ops == [("attach", "c1", ARS), ("name", "c1", "Sim")]


def test_attachment_choice_prefers_unwrapped_url_over_tracker():
    atts = [{"url": TRACKER}, {"url": ARS}]
    assert TrelloClient._best_attachment_url(atts) == ARS
    assert TrelloClient._best_attachment_url([{"url": TRACKER}]) == TRACKER


def test_slug_fallback_drops_hex_ids_and_unreadable_paths():
    wsj = "https://www.wsj.com/finance/situational-awareness-ai-fund-4dbb00a4?mod=x"
    ft = "https://www.ft.com/content/fba35eca-df3a-4ad6-b42d-eb08eb7c9ad3"
    p = plan_card(Card("c1", TRACKER, url=TRACKER), get=fake_get({TRACKER: Resp(302, wsj)}),
                  fetch_title=lambda u: None)
    assert p["title"] == "Situational Awareness Ai Fund — wsj.com"
    p = plan_card(Card("c1", TRACKER, url=TRACKER), get=fake_get({TRACKER: Resp(302, ft)}),
                  fetch_title=lambda u: None)
    assert p["title"] == "ft.com article"


def test_mojibake_title_is_repaired():
    bad = "Here\u2019s Exactly".encode("utf-8").decode("latin-1")
    p = plan_card(Card("c1", ARS), fetch_title=lambda u: bad)
    assert p["title"] == "Here\u2019s Exactly"
