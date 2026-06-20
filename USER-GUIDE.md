# How to use your Counterfactual Podcast

A plain-English guide for future you. No code, no internals — just what to click and
why. (For the technical side, see `CLAUDE.md`.)

---

## What this thing does, in one breath

You collect reading links in Trello. The system reads each article, ranks them by how
much they actually matter *to you* (most impactful first), files them into your reading
lists in that order, turns the lighter ones into a **private podcast**, and keeps that
podcast topped up with ~20 hours of audio. You listen top-down on your phone; the most
important stuff is always at the top.

Everything is driven by **two buttons on your Trello board** plus a podcast app. That's it.

---

## Listening (the podcast)

**Your private feed URL:**
```
https://pub-cbe1a1411c65446c872416872b3c2403.r2.dev/4b1eb250c30c47558534e62b20620d25/rss.xml
```
It's unlisted (an unguessable link), not password-protected. Don't share the URL.

**Subscribe once** (Apple Podcasts):
- Mac: **File → Add a Show by URL…** → paste the feed URL. It syncs to your iPhone
  automatically.
- (Any podcast app that supports "add by URL" works too.)

**How to listen:** play from the **top down**. The order is the priority order — the
episode at the top is the highest counterfactual-impact read, the next one down is second,
and so on. Each episode is one article, read in full.

**Each episode is signposted** so you always know what you're hearing:
- It opens with *"Start of article. {title}, by {author}, from {site}, {Month Year}."*
- It ends with *"End of article. {title}, from {site}."*

So when one finishes and the next auto-plays, you'll clearly hear the hand-off.

---

## The Trello board (the lists, explained)

| List | What it's for |
|------|----------------|
| **Inbox** (Trello's built-in inbox) | Where you dump everything — reading links *and* random to-dos, mixed together. |
| **To Be Processed** | Where reading links land after Button 1 sorts them out of your Inbox. You review here. |
| **▶ Ready to Process** | Your "yes, process these" pile. Drag the keepers here; Button 2 reads from this list. |
| **Reading list that doesn't require system 2** ("System 1") | Lighter reads. **Feeds the podcast.** |
| **Reading list that requires system 2** ("System 2") | Deep reads that need focused attention. You *read* these — they're **not** in the podcast. |
| **Life Optimization** | Life / productivity / self-improvement reads. **Feeds the podcast.** |
| **Listen Queue** | The ~20 hours of audio currently in your podcast. This list *is* the feed. |
| **✓ Listened** | Your history. Drag finished episodes here when you're done. |

After processing, each reading card gets a little tag at the top of its description like
`[#3 · 12 min · why it matters]` — that's its rank, estimated reading time, and a one-line
reason it's worth your time.

---

## The everyday workflow

### 1. Collect (anytime)
Throw links into your Trello **Inbox** however you like (the Trello share button, the
browser extension, paste a URL as a card). Don't worry about sorting — to-dos and reading
links can be mixed.

> Tip: links added via the Trello app / browser extension show a nice preview. Links you
> just paste as text still work — the system fixes the titles when it processes them.

### 2. Press **"Extract readables"** (Button 1)
This looks through your Inbox, picks out the things that are *reading material* (vs.
to-dos), and moves them to **To Be Processed**. Your actual to-dos stay in the Inbox,
untouched.

### 3. Review and pick
Look through **To Be Processed**. For anything you genuinely want to read/hear, **drag it
into "▶ Ready to Process."** Leave or delete the rest.

### 4. Press **"Sort readables"** (Button 2)
This is the workhorse. For everything in **▶ Ready to Process**, it:
- reads the article,
- ranks it by counterfactual impact and files it into the right reading list (System 1 /
  System 2 / Life Optimization) at the correct priority position,
- refreshes your **Listen Queue** back up to ~20 hours, newest = highest priority,
- publishes the updated podcast.

It can take a while the first time (it's reading and comparing lots of articles). You can
run it and walk away.

### 5. Listen, then clear what you finish
Listen top-down in Apple Podcasts. When you finish an episode:
1. In Trello, drag its card from **Listen Queue → ✓ Listened**.
2. Next time you press **"Sort readables,"** the finished episodes drop off the feed and
   the queue refills with the next most-important reads.

> You don't have to do this after every episode — finish a few, drag them to ✓ Listened,
> then press the button once. The queue only refills when you run it.

---

## The two buttons

They live on your Trello board (the **Automation / Butler** buttons, top of the board).

| Button | What it does | When to press |
|--------|--------------|---------------|
| **Extract readables** | Pulls reading links out of your Inbox → To Be Processed | After you've dumped a batch of links in the Inbox |
| **Sort readables** | Processes ▶ Ready to Process → ranks, files, refreshes the podcast | After you've moved keepers into ▶ Ready to Process, or to refresh the queue after listening |

**One-time setup note (do this once if the buttons aren't working):** the buttons need to
point at the live server. Edit each button (Automation → Buttons → Board Buttons → edit)
and set the URL to:
- Extract readables → `https://counterfactual-podcast.chooijqweb.workers.dev/phase1`
- Sort readables → `https://counterfactual-podcast.chooijqweb.workers.dev/phase2`

Both need the header **`X-Trigger-Token`** set to the token value stored in your `.env`
file (the `TRIGGER_TOKEN` line). Keep that token private.

---

## Good to know

- **Nothing deletes itself.** An episode only leaves the feed when you move its card out of
  Listen Queue *and* press "Sort readables." So you'll never lose your place by surprise.
- **Half-finished episodes are safe.** If a refresh re-orders the feed, your podcast app
  keeps your playback position.
- **System 2 reads aren't in the podcast** — those need your eyes, not your ears. Read them
  in Trello.
- **The dates in your podcast app are fake.** They only encode priority (top = most recent).
  Ignore the actual dates.
- **Your finished audio is kept safe** even after it leaves the feed, so nothing is ever
  truly lost.

---

## Quick reference

```
Collect links            → Trello Inbox
Press "Extract readables"→ moves reading links to "To Be Processed"
Review + drag keepers    → "▶ Ready to Process"
Press "Sort readables"   → ranks, files, refreshes podcast (~20h)
Listen top-down          → Apple Podcasts (top = most important)
Finished an episode      → drag card: Listen Queue → ✓ Listened
Refresh the queue        → press "Sort readables" again
```
