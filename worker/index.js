// Cloudflare Worker: public entrypoint for the counterfactual-podcast pipeline.
//
//   Trello Butler button --POST--> Worker (/phase1,/phase2) --> Container (FastAPI)
//   Cron trigger ----------------> Worker.scheduled() --------> Container
//
// The Container runs our existing FastAPI app (counterfactual_podcast.server:app).
// We keep a SINGLE singleton container instance so the per-phase asyncio locks in
// server.py actually serialize across all traffic (one DO id == one container).
//
// Auth: POST /phase1 and /phase2 require the X-Trigger-Token header, which the
// FastAPI server checks against its TRIGGER_TOKEN env var. The Worker forwards the
// header as-is for button traffic; for cron traffic the Worker injects it from
// env.TRIGGER_TOKEN.

import { Container, getContainer } from "@cloudflare/containers";

// Stable name -> exactly one container instance for the whole app.
const SINGLETON = "singleton";

// The Container base class starts the container with an EMPTY env by default
// (envVars = {}); it does NOT auto-inherit the Worker's vars/secrets. So we forward
// exactly what the FastAPI app reads from os.environ. Without this the container boots
// with no Trello/Anthropic/R2/Google creds and every run fails silently.
const FORWARD_ENV = [
  "TRELLO_API_KEY",
  "TRELLO_TOKEN",
  "ANTHROPIC_API_KEY",
  "R2_ACCOUNT_ID",
  "R2_ACCESS_KEY_ID",
  "R2_SECRET_ACCESS_KEY",
  "R2_BUCKET",
  "R2_PUBLIC_BASE",
  "PODCAST_PREFIX",
  "TRIGGER_TOKEN",
  "TTS_ENGINE",
  "GOOGLE_APPLICATION_CREDENTIALS",
  "GOOGLE_CREDENTIALS_JSON",
];

export class PodcastContainer extends Container {
  defaultPort = 8080;        // uvicorn listens on 8080 (see Dockerfile)
  sleepAfter = "10m";        // scale to zero after 10 min idle (cheap while idle)

  constructor(ctx, env) {
    super(ctx, env);
    const vars = {};
    for (const k of FORWARD_ENV) {
      if (env[k] !== undefined && env[k] !== null) vars[k] = String(env[k]);
    }
    this.envVars = vars;     // injected into the container process at start
  }

  onError(error) {
    console.error("PodcastContainer error:", error);
  }
}

export default {
  /**
   * Public HTTP entrypoint. Routes button webhooks + health checks to the
   * singleton container, forwarding the original request (incl. X-Trigger-Token).
   */
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname;

    const isPhase =
      request.method === "POST" && (path === "/phase1" || path === "/phase2");
    const isHealth = request.method === "GET" && path === "/health";
    const isLogs = request.method === "GET" && path === "/logs";

    if (!isPhase && !isHealth && !isLogs) {
      return new Response("not found", { status: 404 });
    }

    const container = getContainer(env.PODCAST_CONTAINER, SINGLETON);
    return container.fetch(request);
  },

  /**
   * Cron entrypoint. Maps cron expressions to phases:
   *   "0 6 * * *"  (daily 06:00 UTC) -> POST /phase1
   *   "0 * * * *"  (hourly)          -> POST /phase2
   * Anything else falls back to phase2 (the cheap, frequent drainer).
   */
  async scheduled(event, env, ctx) {
    const cron = event.cron || "";
    // Daily phase-1 cron has a fixed hour field (not "*"); the hourly phase-2 cron
    // uses "*" for the hour. Match on the daily expression explicitly.
    const phase = cron === "0 6 * * *" ? "phase1" : "phase2";

    const container = getContainer(env.PODCAST_CONTAINER, SINGLETON);
    const req = new Request(`http://container/${phase}`, {
      method: "POST",
      headers: { "X-Trigger-Token": env.TRIGGER_TOKEN },
    });

    ctx.waitUntil(
      container.fetch(req).then(
        (resp) => console.log(`cron ${cron} -> ${phase}: ${resp.status}`),
        (err) => console.error(`cron ${cron} -> ${phase} failed:`, err),
      ),
    );
  },
};
