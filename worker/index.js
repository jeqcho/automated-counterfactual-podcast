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

export class PodcastContainer extends Container {
  defaultPort = 8080;        // uvicorn listens on 8080 (see Dockerfile)
  sleepAfter = "10m";        // scale to zero after 10 min idle (cheap while idle)

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

    if (!isPhase && !isHealth) {
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
