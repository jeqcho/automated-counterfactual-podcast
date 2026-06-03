# Setup: Trello Butler buttons → Phase 1 / Phase 2 (Premium)

Two clickable **Board Buttons** on Home base that run the real pipelines. Requires
**Trello Premium** (Butler HTTP requests). Flow:

```
[Butler board button]  --HTTP POST-->  [Cloudflare Tunnel]  -->  [local FastAPI server]  -->  runs phase --apply
```

## 1. Pick a shared secret
Add to `.env`:
```
TRIGGER_TOKEN=<a long random string>
```
The Butler button must send this in a header so randoms can't trigger your jobs.

## 2. Run the local server
```bash
./scripts/run_server.sh          # serves on http://127.0.0.1:8787
# health check:
curl -s localhost:8787/health
```
For always-on, wrap it in a `launchd` service (like the weekly job). The server only
*starts* a phase and returns immediately; the phase runs in the background (logs in
`logs/phase{1,2}-*.log`). A per-phase lock prevents overlapping runs.

## 3. Expose it to Trello with a Cloudflare Tunnel
Trello's servers must reach your Mac. Cloudflare Tunnel is free.
```bash
brew install cloudflared
# quick tunnel (ephemeral URL, fine to start):
cloudflared tunnel --url http://127.0.0.1:8787
#   -> prints https://<random>.trycloudflare.com
# (for a STABLE url, create a named tunnel mapped to a subdomain on your Cloudflare domain)
```
Keep `cloudflared` running (also launchd-able). Your trigger URLs are then:
`https://<tunnel>/phase1` and `https://<tunnel>/phase2`.

## 4. Create the two Butler board buttons
In Trello → **Automation (Butler)** → **Board Buttons** → **Create Button**:

**Button ① "Run Phase 1"**
- Action → **Issue HTTP Request** (under the *Other* tab for board buttons):
  - Method: `POST`
  - URL: `https://<tunnel>/phase1`
  - Send **with headers**: `{ "X-Trigger-Token": "<your TRIGGER_TOKEN>" }`
  - Payload: (none needed)

**Button ② "Run Phase 2"** — same, URL `https://<tunnel>/phase2`.

Save. Now clicking ① triages your Inbox → To Be Processed; clicking ② (after you
review) routes + ranks + tops up the queue + publishes.

## Security notes
- The token header is the only gate — keep it secret; rotate by changing `.env` + the
  button. Consider also Cloudflare Access in front of the tunnel for a second factor.
- The server binds to `127.0.0.1` (not `0.0.0.0`); only the tunnel reaches it.
- Buttons run the **real** pipelines with `--apply` (they mutate the board / publish).
  Test each phase with `./scripts/run_phase{1,2}.sh` (dry-run) before wiring the buttons.
