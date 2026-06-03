# Automated Counterfactual Podcast

Turns Jay's Trello reading lists into a priority-ordered, listen-first **private
podcast**. New links get sorted by *counterfactual impact* (what's most worth Jay's
time, given his goals), and the highest-impact readable material is converted to
speech and delivered as a podcast feed he can listen to top-first.

---

## The board

Three reading lists on the **Home base** Trello board:

- **System 1** — lighter material that doesn't need deep focus (newsletters, takes).
- **System 2** — deep, effortful reading (papers, long technical pieces).
- **Life Optimization** — productivity, health, career, meta.

Plus Trello's built-in **Inbox**, where Jay drops links throughout the week.

## What happens to a card

```
            ┌─────────────┐
   Jay  ──▶ │   Inbox     │   (drop a link anytime)
            └──────┬──────┘
                   │  weekly job
                   ▼
            ┌─────────────────┐
            │ To Be Processed │   collected from the Inbox
            └──────┬──────────┘
                   │  read the link, summarize, decide
                   ▼
     ┌─────────────┴───────────────┐
     ▼             ▼                ▼
 System 1      System 2       Life Optimization      ← routed by type,
 (inserted at its impact rank within the list)         ranked by impact
     │                              │
     └──────────────┬───────────────┘
                    │  (System 2 excluded — needs focused reading)
                    ▼
            ┌─────────────┐
            │ Listen Queue│   top ~20h of highest-impact, readable cards
            └──────┬──────┘     each turned into audio (text-to-speech)
                   ▼
            🎧 Private podcast feed  →  Jay listens top-first, archives when done
```

For each card the system:

1. **Reads the link.** Pulls the article text behind the card (the URL is stored as a
   Trello attachment). PDFs, articles, and plain text all work; paywalled/X/YouTube
   links are flagged as "can't read" and skipped for audio.
2. **Summarizes it** into a short, impact-focused digest (via a cheap LLM pass), and
   estimates reading time from the word count.
3. **Decides which list** it belongs to — System 1, System 2, or Life Optimization.
4. **Ranks it by counterfactual impact** and slots it into that list at the right spot.

## How ranking works

Instead of giving each article a score, the system compares articles **two at a time**
— *"which of these should Jay read first?"* — using an LLM that's been given a profile
of Jay's goals, priorities, and what counts as high-impact for him. Thousands of these
head-to-head comparisons get sorted into a full ranking (like a tournament). Comparing
the short digests (not full articles) keeps it fast and cheap, and every decision is
cached so re-runs are nearly free.

## The listen queue & podcast

- The **Listen Queue** is kept topped up to ~20 hours of audio, pulled from the
  highest-impact **System 1 + Life Optimization** cards (System 2 stays read-only —
  deep material isn't meant for passive listening).
- Each queued card's text is converted to speech locally (free), and the queue is
  published as a **podcast RSS feed** hosted on Cloudflare. Jay subscribes in any
  podcast app, listens to the top item first, and **archives it when done** — the next
  run refills the queue from the best remaining cards.

## The two jobs

- **One-time sort** — rank the three existing reading lists by counterfactual impact
  and reorder them in place (with a per-card note explaining the rank). Safe by default:
  it shows a proposed order first and only reorders the board when told to apply.
- **Weekly automation** — collect the Inbox → route + rank each new card → top up the
  20-hour listen queue → publish the podcast.

## Where things live

- `src/counterfactual_podcast/` — the code (one focused module per stage).
- `reports/` — the implementation plan, budget analysis, and the usage runbook.
- `CLAUDE.md` — durable project context and learnings.
- `private/` & `.env` — Jay's profile doc, board backups, and secrets (never committed).

See `reports/usage.md` for exact commands.
