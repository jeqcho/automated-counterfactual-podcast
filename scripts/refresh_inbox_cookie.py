#!/usr/bin/env python3
"""Refresh the Trello Inbox session cookie in one step.

The native Trello Inbox is only reachable via a logged-in web SESSION COOKIE (API tokens are
hard-blocked — see CLAUDE.md "Trello Inbox is now session-cookie-only"). That cookie's
`cloud.session.token` expires ~monthly, after which Phase 1 logs
`INBOX UNAVAILABLE — refresh TRELLO_SESSION_COOKIE` and moves nothing.

To refresh:
  1. In the Trello web app (logged in): DevTools -> Network -> click any `trello.com/1/...`
     request -> Request Headers -> copy the whole `cookie:` value.
  2. Run (paste, then Ctrl-D):
        uv run python scripts/refresh_inbox_cookie.py
     or, from the clipboard on macOS:
        pbpaste | uv run python scripts/refresh_inbox_cookie.py

This rewrites TRELLO_SESSION_COOKIE in .env AND pushes the Cloudflare secret. The cookie is a
full-account credential — it is never printed or committed (only length + last-6 are shown).
"""
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
ENV = ROOT / ".env"
KEY = "TRELLO_SESSION_COOKIE"

_STEPS = """
──────────────────────────────────────────────────────────────────────────────
 How to grab the Trello Inbox cookie:
   1. Open  https://trello.com  in Chrome (make sure you're logged in).
   2. Open DevTools:  Cmd+Option+I   →   click the  Network  tab.
   3. In Trello, click your Inbox (or press Cmd+R to reload) so requests appear.
   4. In the Network list, type  cards  in the filter box, then click any
      request to  trello.com/1/...  (e.g.  cards?...).
   5. Scroll to  Request Headers  →  find the  cookie:  line  →  copy its
      ENTIRE value (it's long).
   6. Paste it here and press Enter, then Ctrl-D.
      (Shortcut once it's copied:  pbpaste | uv run python scripts/refresh_inbox_cookie.py)
──────────────────────────────────────────────────────────────────────────────
"""


def main() -> None:
    interactive = sys.stdin.isatty()  # True unless piped (e.g. `pbpaste | ...`)
    if interactive:
        # No pipe -> walk the user through grabbing the cookie, then wait for the paste.
        print(_STEPS, file=sys.stderr)
    cookie = sys.stdin.read().strip()
    if not cookie or "dsc=" not in cookie:
        # Invalid/empty (or a stale clipboard via pbpaste) -> show the steps so they know how.
        print(_STEPS, file=sys.stderr)
        sys.exit("\n✗ That wasn't a valid Trello cookie header (no `dsc=` found). "
                 "Follow the steps above, then run this again.")
    print(f"cookie: {len(cookie)} chars, last6 …{cookie[-6:]}", file=sys.stderr)

    # 1) Rewrite (or append) the line in .env, preserving everything else.
    line = f"{KEY}='{cookie}'"
    lines = ENV.read_text().splitlines() if ENV.exists() else []
    for i, ln in enumerate(lines):
        if ln.startswith(f"{KEY}=") or ln.startswith(f"{KEY} ="):
            lines[i] = line
            break
    else:
        lines.append(line)
    ENV.write_text("\n".join(lines) + "\n")
    print("✓ updated .env", file=sys.stderr)

    # 2) Push the Cloudflare secret (piped via stdin — never on argv).
    print("pushing Cloudflare secret …", file=sys.stderr)
    p = subprocess.run(["npx", "wrangler", "secret", "put", KEY],
                       input=cookie, text=True, cwd=ROOT)
    if p.returncode != 0:
        sys.exit("wrangler secret put failed — .env was updated, but the cloud secret was NOT")
    print("✓ cloud secret updated. Button 1 should work again.", file=sys.stderr)


if __name__ == "__main__":
    main()
