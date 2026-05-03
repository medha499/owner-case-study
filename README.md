<div align="center">

# Owner.com Sales Intelligence Tech Case Study

**A two-view sales platform that turns call transcripts into rep playbooks and manager coaching.**

![Python](https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Anthropic](https://img.shields.io/badge/Claude-Opus%20%2B%20Haiku-D97757?logo=anthropic&logoColor=white)
![Tavily](https://img.shields.io/badge/Tavily-web%20intel-7170ff)
![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS-000000)
![Bilingual](https://img.shields.io/badge/lang-EN%20%2B%20ES-facc15)
![No build step](https://img.shields.io/badge/frontend-vanilla%20JS-yellow)

🎯 **Reps** get a single-screen call script personalized to each prospect.
📊 **Managers** get pattern extraction across the team's calls — what's actually closing deals, sliced by segment.
⚡ **Sub-15ms** hot paths end-to-end.

</div>

---

## A note on the data

The CSVs Owner.com sent for evaluation are **not committed to this repo.** To run against the real dataset, drop the original `restaurants.csv` and `calls.csv` into `data/` locally and the app picks them up.

---

## What it does

### Rep view — a screenplay-style script

Single-screen teleprompter the rep reads top-to-bottom during the call. Five stages with the exact words to say:

```
▸ START WITH:    The opener (personalized: top dish, platform, spend)
▸ THEN ASK:      One discovery question
▸ THEN PITCH:    The dollar math + value prop
▸ IF THEY PUSH BACK:  Five They-Say / You-Say pairs
▸ CLOSE WITH:    Calendar-anchored ask
```

Each line is personalized using the prospect's restaurant data and Tavily-enriched intel (top menu item, primary delivery platform, estimated commission spend, review themes).

🌐 **Bilingual mode** — Spanish lines for the rep to speak with English translations underneath, so they always know what they're saying.

**"Hear similar wins"** — ElevenLabs TTS of past won calls, prioritized by cuisine match(NOT IMPLEMENTED)

### Manager view — pattern extraction across the team

- **Themes & Topics** — aggregated What Works / What Doesn't patterns plus 4 inline mini-charts (opener × outcome, language mix, top objections, competitor mentions)
- **Slice & Dice** — same patterns, filterable by cuisine, business type, locations. Filters apply in <10ms because they intersect pre-computed evidence rather than re-running LLMs
- **Team Performance** — per-rep coaching cards with tier classification (top / mid / needs-coaching) plus narrative coaching
- **Competitor Watch** — live Tavily web research summaries for Toast, ChowNow, Popmenu, Square for Restaurants, HungerRush

---

## Architecture

```
                 ┌──────────────────────────────────────────┐
                 │            Browser SPA                    │
                 │   vanilla JS · dark mode · gzipped 33KB  │
                 │     ┌──────────────┬─────────────────┐   │
                 │     │  Manager UI  │     Rep UI      │   │
                 │     └──────┬───────┴────────┬────────┘   │
                 └────────────┼────────────────┼────────────┘
                              │ REST           │ REST
                              ▼                ▼
                 ┌──────────────────────────────────────────┐
                 │            FastAPI server                 │
                 │   GZip · Cache headers · Cookie auth     │
                 │   /synthesis  /brief  /intel  /audio     │
                 └────────────┬─────────────────┬───────────┘
                              │                 │
                              ▼                 ▼
                 ┌──────────────────────┐  ┌──────────────────┐
                 │   Pipeline           │  │  Lazy enrichment │
                 │   (one-shot run)     │  │  (per request)   │
                 │                      │  │                  │
                 │   extract_all       │  │  agent_account   │
                 │   run_synthesis     │  │     _intel       │
                 │   build_brief       │  │  (Tavily web)    │
                 └──────────┬───────────┘  └──────────────────┘
                            │
                            ▼
                 ┌──────────────────────────────────────────┐
                 │           Multi-agent layer               │
                 │                                           │
                 │   ┌─────────────┐                        │
                 │   │ Orchestrator│  Claude Opus           │
                 │   │             │  reasoning + planning  │
                 │   └──┬─────┬─┬──┘                        │
                 │      │     │ │                            │
                 │   ┌──▼─┐ ┌─▼┐ ┌──▼──┐  ┌────┐            │
                 │   │ext │ │wk│ │coach│  │comp│  Claude    │
                 │   │act │ │/ │ │     │  │    │  Haiku     │
                 │   └────┘ │ds│ └─────┘  └────┘  parallel  │
                 │          └──┘                             │
                 │                                           │
                 │   8-worker ThreadPool · prompt caching   │
                 │   on extraction system prompt             │
                 └──────────────────────────────────────────┘
                            │                 │
                            ▼                 ▼
                       ┌────────┐        ┌────────┐
                       │  CSV   │        │ Tavily │
                       │  data  │        │  web   │
                       └────────┘        └────────┘
```

**Why multi-agent?** The problem decomposes naturally. Per-call extraction is parallel and uniform → Haiku at 8 workers. Cross-call synthesis is reasoning-heavy → Opus orchestrates. Per-account brief is web-grounded → Tavily fires lazily. Each sub-agent has a tight JSON contract so failures fall back gracefully instead of corrupting the whole response.

---

## Performance

Sub-15ms hot paths after the initial pipeline run.

| Path | Time |
|---|---|
| 🔍 Manager filter (any segment) | **6–9 ms** |
| 📋 Rep brief load | **6–12 ms** |
| 🌐 First pageload (gzipped) | **~33 KB wire** |

How that's achieved: gzip middleware (84% size reduction), browser cache headers, hand-rolled CSS bars instead of Chart.js, slim API payloads, render-first / fetch-after, lazy Tavily on a separate endpoint, evidence-set intersection for filtering instead of LLM re-synthesis per click.

---

## Run it

**1. Clone and install**

```bash
git clone https://github.com/<your-username>/owner-sales-intel.git
cd owner-sales-intel
pip install -r requirements.txt
```

**2. Set your API keys**

```bash
cp .env.example .env
```

Then open `.env` and add:

```
ANTHROPIC_API_KEY=sk-ant-...      # required — powers extraction + synthesis
TAVILY_API_KEY=tvly-...           # required — live web research for account intel
ELEVENLABS_API_KEY=...            # optional — only used for "hear similar wins" audio playback
```

**3. Drop in the dataset**

Place `restaurants.csv` and `calls.csv` into `data/csv`

**4. Run**

```bash
python server.py
```

Visit http://localhost:8000 and pick:

- **Sales Rep** (Sarah Kim, `rep_sarah`)
- **Sales Manager** (Maria Lopez, `mgr_maria`)

---

## API

```
POST /api/login · /api/logout · GET /api/me

POST /api/pipeline/run               (manager) kicks the pipeline
GET  /api/pipeline/status            live status + progress

GET  /api/synthesis?segment=&slim=   (manager) instant filter, no LLM
GET  /api/charts?segment=            (manager) chart-ready aggregates
GET  /api/segments                   (manager) available filter values

GET  /api/queue                      (rep) today's call list
GET  /api/brief/{rid}?lang=          (rep) personalized pre-call brief
GET  /api/intel/{rid}?lang=          (rep) lazy Tavily-enriched intel
GET  /api/similar_calls/{rid}        (rep) won calls, cuisine-prioritized
GET  /api/audio/{call_id}            (rep) ElevenLabs TTS of transcript
GET  /api/my_stats · /api/coaching

POST /api/note                       (manager) send coaching note
POST /api/notes/{id}/ack             (rep) acknowledge note
```

---

## File layout

```
owner_app/
├── server.py                FastAPI + all routes
├── pipeline.py              extract / synthesize / build_brief
├── agents.py                multi-agent layer (Opus + Haiku + Tavily)
├── _fields.py               CSV column-name normalization
├── data_csv.py              CSV loaders with in-memory cache
├── synthetic_signals.py     demo signal generator
├── tts.py                   ElevenLabs TTS
├── data/csv                    seed CSVs (add real data here locally)
├── static/
│   ├── login.html · app.html
│   ├── style.css            dark mode (~3500 lines, 14KB gzipped)
│   ├── i18n.js              EN/ES dictionary
│   └── app.js               SPA (~1400 lines, 14KB gzipped)
└── README.md
```

---

## Auth (demo)

Two hardcoded users with cookie-based sessions: `rep_sarah` and `mgr_maria`. The Manager/Rep toggle in the topbar flips views without re-login. This is demo-grade — real auth (OAuth + RBAC) is in next steps.

---

## Next steps

**Tier 1 — pilot-ready**
- Salesforce / HubSpot bidirectional sync (lead pull → queue, disposition → CRM)
- Real-time transcription with live objection surfacing (Deepgram + Haiku classifier)
- OAuth + RBAC (Google Workspace SSO, per-rep ACLs)
- Pytest suite + CI (extract → synthesize → brief end-to-end, filter correctness, auth boundaries)
- OpenTelemetry observability (cost per LLM call, p99 latencies, per-rep usage)

**Tier 2 — patterns trustworthy at scale**
- Bayesian credible intervals on extracted patterns (don't surface noise; require statistically meaningful lift)
- Pattern provenance UI (click any pattern → see contributing transcripts + audio playback)
- Continuous learning loop (call dispositions feed back; reinforce patterns that lead to booked demos)

**Tier 3 — polish**
- AI-generated objection responses per-call (~$0.01 per brief, async regeneration)
- Voice cloning for similar-wins playback (rep's own voice via ElevenLabs)
- Mobile PWA (the teleprompter is already mobile-friendly)
- Klue / Crayon competitive intel feed for live competitor pricing changes
