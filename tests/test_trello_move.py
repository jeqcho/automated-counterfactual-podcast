"""Regression test: cross-board move needs idBoard.

Phase 1 moves cards out of the native Inbox (a hidden board) into the Home base
'To Be Processed' list. Trello rejects an idList that isn't on the card's current
board unless idBoard is also sent. The button silently did nothing until this fix.
"""
import responses

from counterfactual_podcast.trello import TrelloClient


@responses.activate
def test_move_card_same_board_omits_idboard():
    responses.add(responses.PUT, "https://api.trello.com/1/cards/c1", json={"id": "c1"})
    TrelloClient("k", "t").move_card("c1", "L1")
    params = responses.calls[0].request.params
    assert params["idList"] == "L1"
    assert "idBoard" not in params


@responses.activate
def test_move_card_cross_board_includes_idboard():
    responses.add(responses.PUT, "https://api.trello.com/1/cards/c1", json={"id": "c1"})
    TrelloClient("k", "t").move_card("c1", "L1", board_id="B1")
    params = responses.calls[0].request.params
    assert params["idList"] == "L1" and params["idBoard"] == "B1"
