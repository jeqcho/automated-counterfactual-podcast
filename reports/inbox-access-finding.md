# Finding: Accessing the Trello native Inbox via REST API

**Status: RESOLVED.** The native Inbox is fully reachable with the standard read/write token. No workflow change, no extra scope.

## What did NOT work
- `GET /1/members/me/inbox` → `401 unauthorized member permission requested`. This is the wrong endpoint; ignore it.

## What works
The Inbox is a **hidden personal board with one backing list**. The pointer lives on the member object:

```bash
GET /1/members/me?fields=inbox
# -> { "inbox": { "idBoard": "<board>", "idList": "<list>", "idOrganization": "<org>" } }
```

For this account (chooijeqin), confirmed live on 2026-06-01:
- `inbox.idBoard`  = `67f3e3ee9b5f222867323ea8`
- `inbox.idList`   = `67f3e3ee9b5f222867323f99`  (named **"Inbox List"**)
- `inbox.idOrganization` = `67f3e3ed9b5f222867323e2c`

Then treat the Inbox list as an ordinary list:

```bash
# list inbox cards  (returned HTTP 200; 0 cards at time of check — inbox was empty)
GET /1/lists/{inbox.idList}/cards?fields=name,url,desc

# move a card out into "To Be Processed"
PUT /1/cards/{cardId}?idList={toBeProcessedListId}&pos=bottom
```

## Implementation guidance
- **Fetch `inbox.idList` dynamically at runtime** via `GET /1/members/me?fields=inbox` rather than hardcoding it. The IDs are stable per account but resolving them dynamically is robust and avoids leaking account-specific IDs into committed config.
- The standard token (scope `read,write`) is sufficient — verified by a 200 on both the list-cards and board-lists reads.
- Moving a card out of the Inbox is a normal card update; the Inbox "empties" naturally as cards are reassigned to board lists.

## Consequence for the plan
- Task 1 spike is **resolved**; `inbox.py` uses the dynamic `inbox.idList` path as the **primary** mechanism.
- The "dedicated `Inbox` list" fallback is demoted to a footnote (only needed if Atlassian removes the `inbox` field from the member object).
- Removes the one open BLOCKER; Jay's capture workflow is unchanged.
