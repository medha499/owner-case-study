<div align="center">

# Owner.com Sales Intelligence

**Turning call transcripts into rep playbooks and manager coaching.**

![Python](https://img.shields.io/badge/python-3.10+-blue?logo=python&logoColor=white)
![FastAPI](https://img.shields.io/badge/FastAPI-009688?logo=fastapi&logoColor=white)
![Anthropic](https://img.shields.io/badge/Claude-Opus%20%2B%20Haiku-D97757?logo=anthropic&logoColor=white)
![Tavily](https://img.shields.io/badge/Tavily-web%20intel-7170ff)
![ElevenLabs](https://img.shields.io/badge/ElevenLabs-TTS-000000)

</div>

## The problem

Sales teams sit on hundreds of call transcripts and never read them. Reps walk into the next call without knowing what worked on the last one. Managers can't tell which patterns drive wins versus which reps need help.

This case study builds a two-view tool that fixes both sides:

- **Reps** get a personalized script for each prospect, before the call.
- **Managers** get aggregated patterns across the team, plus per-rep coaching.


<img width="1185" height="624" alt="outline" src="https://github.com/user-attachments/assets/9d98fa28-16f7-42ca-a232-8b962484daad" />


## How it works

```
Browser SPA (vanilla JS)
        │
        ▼
FastAPI server  ──────────────►  Lazy enrichment (Tavily web intel)
        │
        ▼
Multi-agent layer
   ├── Orchestrator      (Claude Opus — reasoning + planning)
   └── Workers           (Claude Haiku — extract, synthesize, coach, compete)
        │
        ▼
   CSV data (transcripts + restaurants)
```

**Two execution modes:**
- **Pipeline** runs once over all transcripts: extract → synthesize → build briefs.
- **Lazy enrichment** runs per request: Tavily web search for fresh account intel.

8-worker thread pool with prompt caching on the extraction system prompt keeps it fast.

## Run it

```bash
git clone https://github.com/<your-username>/owner-case-study.git
cd owner-case-study
pip install -r requirements.txt
cp .env.example .env
```

Add your keys to `.env`:

```
ANTHROPIC_API_KEY=sk-ant-...   # required — extraction + synthesis
TAVILY_API_KEY=tvly-...        # required — live web research
ELEVENLABS_API_KEY=...         # optional — TTS for "hear similar wins"
```

Drop `restaurants.csv` and `calls.csv` into `data/csv/`, then:

```bash
python server.py
```

Visit `http://localhost:8000` and log in as:
- **Sales Rep** — Sarah Kim (`rep_sarah`)
- **Sales Manager** — Maria Lopez (`mgr_maria`)

> **Note on data:** the CSVs are not committed. Drop the original `restaurants.csv` and `calls.csv` into `data/csv/` to run against the real dataset.

## API

**Auth**
```
POST /api/login   POST /api/logout   GET /api/me
```

**Manager**
```
POST /api/pipeline/run            kick off pipeline
GET  /api/pipeline/status         progress + status
GET  /api/synthesis?segment=      filtered patterns (no LLM)
GET  /api/charts?segment=         chart aggregates
GET  /api/segments                available filter values
POST /api/note                    send coaching note
```

**Rep**
```
GET  /api/queue                   today's call list
GET  /api/brief/{rid}?lang=       pre-call brief
GET  /api/intel/{rid}?lang=       Tavily-enriched intel
GET  /api/similar_calls/{rid}     cuisine-matched won calls
GET  /api/audio/{call_id}         ElevenLabs TTS
GET  /api/my_stats   /api/coaching
POST /api/notes/{id}/ack          acknowledge a coaching note
```

## File layout

```
owner_app/
├── server.py              FastAPI + routes
├── pipeline.py            extract / synthesize / build_brief
├── agents.py              multi-agent layer (Opus + Haiku + Tavily)
├── _fields.py             CSV column normalization
├── data_csv.py            CSV loaders + cache
├── synthetic_signals.py   demo signal generator
├── tts.py                 ElevenLabs TTS
├── data/csv/              seed CSVs (add real data here)
└── static/
    ├── login.html · app.html
    ├── style.css          ~3500 lines, 14KB gzipped
    ├── i18n.js            EN/ES dictionary
    └── app.js             SPA, ~1400 lines, 14KB gzipped
```

## Auth (demo only)

Two hardcoded cookie sessions: `rep_sarah` and `mgr_maria`. The Manager/Rep toggle flips views without re-login. **Demo-grade** — real auth is in Next Steps.

## Next steps

**Real auth + RBAC.** Hardcoded cookies are fine for a demo, not a pilot. v2 is Google Workspace SSO with per-role permissions: reps see only their queue, managers see their direct reports, admins manage the roster. The role check already exists in `require_role()` — it just needs real identity behind it.

**Real audio for similar wins.** Right now "hear similar wins" is ElevenLabs TTS of a transcript. Real won-call audio (with consent + redaction) would land harder — reps trust patterns they can hear in someone's voice. Bonus: rep-voice cloning so "here's how Sarah opened a similar deal" actually sounds like Sarah.

**UI polish.** Smoother loading on the manager dashboard, better empty states, mobile-responsive teleprompter, and a first-time onboarding tour.
