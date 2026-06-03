"""Raw-REST Trello client.

A thin wrapper over the Trello v1 REST API using ``requests``. Every call funnels
through one private ``_request`` helper that injects auth, applies a token-bucket
rate limiter (~8 req/s), and retries on HTTP 429 honoring ``Retry-After`` with
exponential backoff.

Board mutations follow project conventions: reorder cards in place and annotate
via an idempotent description marker (``<!--cf-->[#rank · est min · why]<!--/cf-->``).
"""
from __future__ import annotations

import re
import time
from collections import deque

import requests

from . import config
from .models import Card

API_BASE = "https://api.trello.com"

# Token-bucket: ~8 requests/second.
_RATE = 8
_WINDOW = 1.0

# 429 backoff.
_MAX_TRIES = 5
_DEFAULT_RETRY_AFTER = 1.0

# Idempotent ranking marker placed at the TOP of a card description.
_MARKER_RE = re.compile(r"<!--cf-->.*?<!--/cf-->\s*", re.DOTALL)


class TrelloClient:
    def __init__(self, key: str, token: str, *, sleep=time.sleep):
        self.key = key
        self.token = token
        self._sleep = sleep
        self._session = requests.Session()
        # Timestamps of recent calls for the token-bucket limiter.
        self._calls: deque[float] = deque()

    # -- core ---------------------------------------------------------------
    def _throttle(self) -> None:
        """Block until issuing another request keeps us under ~8 req/s."""
        while True:
            now = time.monotonic()
            while self._calls and now - self._calls[0] >= _WINDOW:
                self._calls.popleft()
            if len(self._calls) < _RATE:
                self._calls.append(now)
                return
            # Sleep until the oldest call ages out of the window.
            wait = _WINDOW - (now - self._calls[0])
            if wait > 0:
                self._sleep(wait)

    def _request(self, method: str, path: str, **params):
        params = dict(params)
        params["key"] = self.key
        params["token"] = self.token
        url = f"{API_BASE}{path}"

        delay = _DEFAULT_RETRY_AFTER
        for attempt in range(_MAX_TRIES):
            self._throttle()
            resp = self._session.request(method, url, params=params)
            if resp.status_code == 429:
                retry_after = resp.headers.get("Retry-After")
                try:
                    wait = float(retry_after) if retry_after is not None else delay
                except (TypeError, ValueError):
                    wait = delay
                self._sleep(wait)
                delay *= 2  # exponential backoff
                continue
            resp.raise_for_status()
            if not resp.content:
                return None
            return resp.json()
        # Exhausted retries: raise on the last response.
        resp.raise_for_status()
        return None

    # -- reads --------------------------------------------------------------
    @staticmethod
    def _best_attachment_url(attachments) -> str:
        """Most Trello reading cards store the article link as an ATTACHMENT.
        Prefer the first external http(s) attachment; fall back to any http one."""
        https = [a.get("url") for a in (attachments or [])
                 if (a.get("url") or "").startswith("http")]
        external = [u for u in https if "trello.com" not in u]
        return (external or https or [""])[0]

    def get_cards(self, list_id: str) -> list[Card]:
        data = self._request(
            "GET", f"/1/lists/{list_id}/cards",
            fields="name,desc,pos", attachments="true", attachment_fields="url",
        )
        cards: list[Card] = []
        for c in data or []:
            pos = c.get("pos", 0.0)
            try:
                pos = float(pos)
            except (TypeError, ValueError):
                pos = 0.0
            cards.append(
                Card(
                    id=c["id"],
                    name=c.get("name", ""),
                    desc=c.get("desc", "") or "",
                    url=self._best_attachment_url(c.get("attachments")),
                    list_id=list_id,
                    pos=pos,
                )
            )
        return cards

    def inbox_list_id(self) -> str:
        """Return the native Trello Inbox list id.

        Use ``GET /1/members/me?fields=inbox`` — do NOT call
        ``/members/me/inbox`` (401s).
        """
        data = self._request("GET", "/1/members/me", fields="inbox")
        return data["inbox"]["idList"]

    # -- card mutations -----------------------------------------------------
    def set_card_position(self, card_id: str, pos):
        """``pos`` accepts a float, ``"top"`` or ``"bottom"``."""
        return self._request("PUT", f"/1/cards/{card_id}", pos=pos)

    def update_desc(self, card_id: str, desc: str):
        return self._request("PUT", f"/1/cards/{card_id}", desc=desc)

    def set_rank_marker(self, card: Card, rank, est_min, why: str) -> str:
        """Idempotently set the ranking marker at the top of the card desc.

        Strips any existing ``<!--cf-->...<!--/cf-->`` block first so repeated
        calls never duplicate the marker. Returns the new desc string.
        """
        existing = card.desc or ""
        stripped = _MARKER_RE.sub("", existing).lstrip()
        marker = f"<!--cf-->[#{rank} · {est_min} min · {why}]<!--/cf-->"
        new_desc = f"{marker}\n\n{stripped}" if stripped else marker
        self.update_desc(card.id, new_desc)
        return new_desc

    def archive_card(self, card_id: str):
        return self._request("PUT", f"/1/cards/{card_id}", closed="true")

    def move_card(self, card_id: str, list_id: str, pos="bottom", board_id: str | None = None):
        """Move a card to ``list_id``. For a CROSS-board move (e.g. out of the native
        Inbox, which lives on a hidden board, into a Home base list) you must also pass
        ``board_id`` — Trello rejects an idList that isn't on the card's current board."""
        params = {"idList": list_id, "pos": pos}
        if board_id:
            params["idBoard"] = board_id
        return self._request("PUT", f"/1/cards/{card_id}", **params)

    def add_label(self, card_id: str, label_id: str):
        return self._request(
            "POST", f"/1/cards/{card_id}/idLabels", value=label_id
        )

    # -- board-level ensures ------------------------------------------------
    def ensure_list(self, name: str, board_id: str = config.BOARD_ID) -> str:
        lists = self._request(
            "GET", f"/1/boards/{board_id}/lists", filter="open", fields="name"
        )
        for lst in lists or []:
            if lst.get("name") == name:
                return lst["id"]
        created = self._request(
            "POST", "/1/lists", name=name, idBoard=board_id
        )
        return created["id"]

    def ensure_label(
        self, name: str, color: str, board_id: str = config.BOARD_ID
    ) -> str:
        labels = self._request(
            "GET", f"/1/boards/{board_id}/labels", fields="name,color"
        )
        for lab in labels or []:
            if lab.get("name") == name and lab.get("color") == color:
                return lab["id"]
        created = self._request(
            "POST", "/1/labels", name=name, color=color, idBoard=board_id
        )
        return created["id"]
