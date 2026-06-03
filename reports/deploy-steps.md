# Deploy runbook — Cloudflare Worker + Container

Ordered steps to ship the counterfactual-podcast pipeline to Cloudflare Containers
(Mac-independent, always-on). Pairs with `reports/deploy-cloudflare.md` (architecture)
and the infra files: `Dockerfile`, `.dockerignore`, `worker/index.js`, `wrangler.jsonc`.

**Jay provides in the morning:** Workers **Paid** plan ($5/mo, required for Containers)
and the **Google service-account JSON** (a GCP project with the Text-to-Speech API
enabled + a service account that has TTS access; download its JSON key).

---

## 0. Prereqs (once)

```bash
# Node + wrangler (latest; Containers need a current wrangler).
npm install -g wrangler          # or: npx wrangler ...
npm install @cloudflare/containers   # the Container base class used by worker/index.js

# Docker must be running locally — `wrangler deploy` builds the Dockerfile.
docker info        # should succeed
```

If starting the Worker project from scratch instead of using these files:
`npm create cloudflare@latest -- --template=cloudflare/templates/containers-template`
then drop in our `worker/index.js`, `wrangler.jsonc`, and `Dockerfile`.

## 1. Auth

```bash
wrangler login          # Jay runs this (opens browser; needs the Paid-plan account)
wrangler whoami         # confirm the right account is selected
```

## 2. Secrets (NOT committed — set per the wrangler.jsonc comment block)

Each command prompts for the value (paste, Enter):

```bash
wrangler secret put TRELLO_API_KEY
wrangler secret put TRELLO_TOKEN
wrangler secret put ANTHROPIC_API_KEY
wrangler secret put R2_ACCOUNT_ID
wrangler secret put R2_ACCESS_KEY_ID
wrangler secret put R2_SECRET_ACCESS_KEY
wrangler secret put R2_BUCKET
wrangler secret put R2_PUBLIC_BASE
wrangler secret put TRIGGER_TOKEN          # shared secret; also goes in the Butler buttons
```

Source the values from the local `.env` (do NOT paste them into any committed file).

## 3. Google service-account JSON → file at container startup

The app's Google TTS engine uses Application Default Credentials, i.e. it reads the file
at `GOOGLE_APPLICATION_CREDENTIALS`. Containers have no persistent disk and we don't bake
the key into the image, so pass the JSON as a secret and materialize it on boot.

1. **Store the whole JSON as one secret:**
   ```bash
   wrangler secret put GOOGLE_CREDENTIALS_JSON < /path/to/service-account.json
   ```
   (Piping the file avoids newline/escaping issues vs. pasting.)

2. **Write it to a file before uvicorn starts.** Replace the Dockerfile `CMD` with an
   entrypoint that dumps the env var to a file, then execs uvicorn. Add to the Dockerfile:
   ```dockerfile
   # at the end of the Dockerfile, replacing the existing CMD:
   COPY worker/container-entrypoint.sh /usr/local/bin/container-entrypoint.sh
   RUN chmod +x /usr/local/bin/container-entrypoint.sh
   CMD ["/usr/local/bin/container-entrypoint.sh"]
   ```
   `worker/container-entrypoint.sh`:
   ```sh
   #!/bin/sh
   set -e
   if [ -n "$GOOGLE_CREDENTIALS_JSON" ]; then
     printf '%s' "$GOOGLE_CREDENTIALS_JSON" > /tmp/gcp-sa.json
     export GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-sa.json
   fi
   exec uvicorn counterfactual_podcast.server:app --host 0.0.0.0 --port 8080
   ```
   > NOTE: this entrypoint touches the Dockerfile/worker dir only — coordinate with the
   > lead before adding it (it's the one change that mutates the provided Dockerfile).
   > Alternative (no entrypoint change): set `GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-sa.json`
   > as a var and have the FastAPI app write `$GOOGLE_CREDENTIALS_JSON` to that path on
   > import — that's a `src/` change for the lead, kept out of this infra PR.

3. **Non-secret vars** — set in `wrangler.jsonc` `"vars"` (currently commented out):
   `TTS_ENGINE=google`, `GOOGLE_APPLICATION_CREDENTIALS=/tmp/gcp-sa.json`,
   `PODCAST_PREFIX=<unguessable-uuid>`. (`TTS_ENGINE=google` is also a Dockerfile default.)

## 4. Confirm env reaches the container

Container instances inherit the Worker's vars + secrets. Verify after deploy that the
FastAPI process sees `TRELLO_*`, `ANTHROPIC_API_KEY`, `R2_*`, `TRIGGER_TOKEN`,
`TTS_ENGINE`, and the Google creds (see step 6 health check / logs).

## 5. Deploy

```bash
wrangler deploy        # builds Dockerfile, pushes image, deploys Worker + cron triggers
```

First build is slow (image push). Subsequent deploys reuse cached layers.

## 6. Smoke-test

```bash
# Health (no auth):
curl https://<worker-subdomain>.workers.dev/health
# -> {"ok": true, "running": {...}}

# Phase trigger (auth required) — use a throwaway token test first if unsure:
curl -X POST https://<worker-subdomain>.workers.dev/phase2 \
  -H "X-Trigger-Token: <TRIGGER_TOKEN>"
# -> {"status": "phase2 started"}   (401 if the token is wrong/missing)

wrangler tail          # live logs — watch the phase run + container start/stop
```

## 7. Public hostname (trigger.chojeq.com) + Butler buttons

1. Add a custom-domain route to the Worker. Either uncomment `routes` in `wrangler.jsonc`:
   ```jsonc
   "routes": [{ "pattern": "trigger.chojeq.com", "custom_domain": true }]
   ```
   and redeploy, or do it in Dashboard > Workers & Pages > (worker) > Settings > Domains
   & Routes > Add custom domain. The `chojeq.com` zone must be on this Cloudflare account.
2. Point the two Trello **Butler** board buttons at:
   - `https://trigger.chojeq.com/phase1`
   - `https://trigger.chojeq.com/phase2`
   each sending header `X-Trigger-Token: <TRIGGER_TOKEN>` (the value from step 2).

## 8. Cron sanity

`wrangler.jsonc` schedules `0 6 * * *` -> `/phase1` (daily 06:00 UTC) and `0 * * * *` ->
`/phase2` (hourly). Confirm in Dashboard > Workers > (worker) > Settings > Triggers, or
force one with `wrangler triggers ...` / wait for the next tick and watch `wrangler tail`.

## 9. Tear down the old local path

Once the Worker is serving on `trigger.chojeq.com`:
- Stop and remove the **Cloudflare Tunnel** (`cloudflared tunnel delete <name>`; remove
  its DNS CNAME if it was pointed at the tunnel).
- Stop the local uvicorn server and any launchd/cron job that kept it alive.
- The Mac is no longer in the loop.

---

## Durable-state reminder (owned by the lead, not this infra PR)

Containers scale to zero and lose local disk. Per `reports/deploy-cloudflare.md`, the
SQLite cache and synthesized MP3s must live in **R2** (download cache on start / upload
on finish; store R2 keys for audio). Those are `src/` changes the lead is making — they
must land before relying on cron, or each cold container re-does work and loses audio.
