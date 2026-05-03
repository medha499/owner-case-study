# Owner.com Sales Intelligence — Build Outline

A walkthrough of how this prototype was designed and built from scratch, what changed along the way, and why each piece exists.

---

## The brief

Owner.com sells SaaS to independent restaurant owners — first-party online ordering, marketing, loyalty — competing against DoorDash, Uber Eats, GrubHub, Toast. Sales reps make hundreds of cold calls a week. Two unmet needs:

- **Reps** need to walk into every call already knowing the operator's situation: cuisine, 3rd-party exposure, what worked in similar calls, how to open in 15 seconds.
- **Managers** need to know which patterns are actually closing deals across the team — not anecdotally, but extracted from real call data — and what to coach each rep on individually.

The dataset is messy: call transcripts in CSV, restaurant metadata in another CSV, mixed English/Spanish, varying field names. The output needs to be polished enough to demo to executives.

---

## Architecture decisions

### Why FastAPI + custom SPA (not Streamlit)

The first iteration used Streamlit. It got 80% there in an afternoon but capped out: the manager dashboard needed sticky sidebars, real charts, custom modals, an audio player, language switching that re-renders only the affected components. Streamlit fights you on all of these. Switched to FastAPI + a hand-written SPA (vanilla JS, no framework) — total control, ~700 lines of JS, no build step.

### Why a multi-agent backend (not a single Claude call)

The synthesis problem decomposes naturally:
- **Per-call extraction** is parallel and uniform — same prompt against 100s of calls. Haiku is fast and cheap; ThreadPoolExecutor with 8 workers chunks of 20 calls finishes the dataset in seconds.
- **Cross-call synthesis** ("what works across the team") needs reasoning over the full extracted signal set. Opus orchestrates; it asks 4 sub-agents (what works / what doesn't / per-rep coaching / competitor intel) to run in parallel.
- **Per-account brief** needs Tavily web research + LLM summarization. Three independent calls (opener / voicemail / live intel) run in parallel.

This is faster, cheaper, and more steerable than one giant prompt. Each sub-agent has a tight, checkable contract (return JSON shape X), so failures fall back gracefully instead of corrupting the whole response.

### Two pipeline trigger modes

- **On-demand** — sidebar button. Manager wants fresh synthesis right now.
- **Background** — `AUTO_REFRESH_SECONDS=1800` env var. A daemon thread re-runs the full pipeline every 30 min. Frontend polls `/api/pipeline/status` every 2s and silently swaps in the new synthesis without a page reload.

Both write to `/tmp/owner_synthesis.json` so the cache survives server restarts. Brief cache invalidates whenever a new run completes.

---

## Frontend approach

### Dark mode, Linear-inspired

Sales tools are looked at all day. Dark mode reduces eye strain and looks more "tool-y" than "report-y." All colors live in CSS variables in `style.css`:

```css
--bg: #08090a;          /* near-black */
--surface: #0e0f11;     /* card backgrounds */
--accent: #7170ff;      /* Linear-style indigo */
--green: #4ade80;       /* won / works */
--red: #ef4444;         /* lost / doesn't work */
--amber: #facc15;       /* pain / warning */
--blue: #60a5fa;        /* live web intel */
```

Patterns are color-coded by what they signal (green = wins, red = losses, blue = live intel, amber = pain). Hover states lift cards by 1px and saturate borders — feels alive without being noisy.

### Bilingual (EN/ES) end-to-end

Two layers:

1. **UI labels** — `i18n.js` is a hand-written EN/ES dictionary. `t("nav_what_works")` swaps text in place. No re-render needed.
2. **AI-generated content** — every user-facing agent (`agent_brief_opener`, `agent_brief_voicemail`, `agent_account_intel`, `agent_one_competitor`, the synthesis orchestrator) takes a `language="en"|"es"` param. It's injected into the system prompt as `"Write all text fields in Spanish."` — Claude does the actual translation. Switching the language toggle invalidates the brief cache and re-fetches; the model literally rewrites every opener and voicemail in Spanish on the fly.

This split matters. Static labels can be a dictionary. Generated content has to be regenerated — there's no way to translate a Spanish opener that mentions "DoorDash" and "$7,800/mo" correctly without the model understanding the full context.

### Manager view — patterns that actually close

Five tabs, each answering one question a manager asks during a 1:1:

- **What Works** — top 4 winning patterns ranked by frequency × win rate, each with the pattern statement, why it wins, an example quote, and source attribution (which rep, which restaurant).
- **What Doesn't** — symmetric anti-patterns from lost calls.
- **Team Performance** — per-rep coaching cards: tier classification (top-quartile / steady / struggling), an AI-written narrative, strength + weakness blocks, suggested action.
- **Themes & Charts** — 5 Chart.js visualizations: opener×outcome stacked bars, language doughnut, top objections, competitor mentions, pain points in won calls.
- **Competitor Watch** — Tavily web research per competitor, with rebuttal angles.

### Rep view — better prepared in 30 seconds

Four sections, designed so a rep can walk into a cold call having already absorbed the full situation:

- **My Queue** — today's restaurants sorted by recommended call time, with cuisine, location, attempt #, 3rd-party spend, and a "now" badge for the next call.
- **Pre-Call Brief** — the meat:
  - Header with rating, 3rd-party spend, current setup, locations
  - **Suggested opener** — AI-generated, leads with commission math, highlights the punchy phrase with `<em>` tags
  - **Live account intel** — Tavily research synthesized into a 2-3 sentence narrative + talking points + sources
  - **Cuisine context** — review themes (chips), top menu item, family style, price range, years open, recent news, segment-typical pain points
  - **3rd-party platform breakdown** — platforms detected, commission %, estimated annual loss in red, one-line rebuttal angle
  - **What works in this segment** — top patterns from the manager synthesis, filtered to the rep's segment
  - **Voicemail script** — backup plan if no answer
  - **Touch history** — every prior attempt with rep, outcome pill, language badge
- **My Stats** — your booking rate, talk ratio, avg questions vs team average — side-by-side
- **Coaching** — auto-generated coaching note from the AI synthesis + manager-written notes with "mark as read" ack flow

### Listen to similar successful calls — actually working audio

Click the button on a brief, modal opens, shows 3 won calls (same cuisine prioritized — for Tony's Pizzeria you get Mario's Pizza and Slice Joint). Each card shows:
- Restaurant + rep + city
- "Key moment" — extracted from the middle of the rep's dialogue, prefers lines containing $/% (closing math)
- **Play audio** button → calls `/api/audio/{call_id}` → server uses ElevenLabs `eleven_turbo_v2_5` model to generate an MP3 from the transcript, caches to `/tmp/owner_audio/{call_id}.mp3`, streams back. Inline `<audio controls autoplay>` element appears. If `ELEVENLABS_API_KEY` isn't set, returns 503 and UI shows a friendly "set the key" message.
- **Transcript** toggle — fetches full transcript on demand

Caching matters here — TTS is the slowest part, so the first play takes ~3 seconds, every subsequent play of the same call is instant.

---

## Demo robustness — graceful degradation

Three optional API keys (`ANTHROPIC_API_KEY`, `TAVILY_API_KEY`, `ELEVENLABS_API_KEY`). The demo has to work with any combination of them missing, including all three. Three layers of fallbacks:

| Missing | What still works |
|---|---|
| Anthropic | `synthetic_signals.py` generates a realistic synthesis from CSV data. Briefs use template openers/voicemails. |
| Tavily | `_fallback_intel()` in `pipeline.py` populates `cuisine_intel` and `platform_intel` from a hand-built per-cuisine knowledge base + CSV-derived numbers. Demo never shows an empty panel. |
| ElevenLabs | Audio button shows "set ELEVENLABS_API_KEY" toast. Transcript still works. Modal still shows similar calls + key moments. |

This was the right call. Demos die when one API is down. With the fallback layers, every screen renders fully even with zero API keys.

---

## What I'd do next with more time

- **Live coaching room** — when a rep is on a call, surface the relevant patterns + competitor rebuttals in a side panel that listens to the call audio in real time. The "Live coaching room →" button is wired in the UI but not implemented.
- **Brief regeneration on objection** — if a rep gets a specific objection mid-call ("we already use DoorDash and it's fine"), one-click regenerate just the rebuttal section.
- **Pattern → call link** — clicking a pattern in the manager view should jump to the source call's transcript with the relevant moment highlighted.
- **Rep-to-rep peer learning** — show "reps who solved your weakness" — i.e. if Sarah's weakness is multi-threading, show 3 of David's calls where he asked "is there a co-owner I should also speak to?"
- **Real ASR** — transcripts are pre-canned in CSV right now. With a Twilio integration, the same pipeline runs on live recordings.

---

## File map

```
owner_app/
├── server.py              FastAPI + REST API + StaticFiles mount
├── data_csv.py            CSV loaders w/ in-memory cache
├── pipeline.py            extract_all / run_synthesis_streaming / build_brief / _fallback_intel
├── agents.py              multi-agent system (Opus orchestrator + Haiku sub-agents + Tavily)
├── _fields.py             CSV field-name normalization (handles variants)
├── synthetic_signals.py   no-API-key fallback signal generator
├── data/csv/              calls.csv (10 transcripts) + restaurants.csv (10 accounts)
├── static/
│   ├── login.html         dark hero w/ EN/ES + role buttons
│   ├── app.html           SPA shell (sidebar + topbar + content + modal + toast)
│   ├── style.css          ~700 lines, Linear-inspired dark mode
│   ├── i18n.js            EN/ES dictionary
│   └── app.js             ~900 lines: routing, rendering, charts, audio, modal, polling
├── requirements.txt
├── .env.example
└── README.md
```

Total: ~2700 lines across 11 files.
