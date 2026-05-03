"""FastAPI server. All HTML/CSS/JS embedded as strings to keep this self-contained.

Multi-agent backend + REST API + dark mode UI.

Usage:
  python server.py
  → http://localhost:8000
"""

import os
import json
import threading
import time
from pathlib import Path
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

from fastapi import FastAPI, Request, Response, HTTPException
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
from dotenv import load_dotenv

from data_csv import load_calls, load_restaurants, load_restaurants_by_id
from pipeline import extract_all, run_synthesis_streaming, build_brief, _fallback_intel
import _fields as F

load_dotenv()

app = FastAPI(title="Owner.com Sales Intelligence")

# Gzip responses larger than 500 bytes — typically 4-5x size reduction for CSS/JS
# (style.css 91KB → ~15KB on the wire, app.js 57KB → ~13KB)
app.add_middleware(GZipMiddleware, minimum_size=500)
BASE = Path(__file__).parent

# Custom static-file handler with browser caching headers.
# Without these, every page nav re-downloads style.css + app.js.
# `max-age=300` means 5 minute cache — long enough for a session, short enough that
# code changes show up after a refresh.
class CachedStaticFiles(StaticFiles):
    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        if path.endswith((".css", ".js", ".html")):
            response.headers["Cache-Control"] = "public, max-age=300"
        return response

app.mount("/static", CachedStaticFiles(directory=str(BASE / "static")), name="static")

CACHE_PATH = Path(os.getenv("CACHE_PATH", "/tmp/owner_synthesis.json"))
NOTES_PATH = Path(os.getenv("NOTES_PATH", "/tmp/owner_notes.json"))
AUTO_REFRESH_SECONDS = int(os.getenv("AUTO_REFRESH_SECONDS", "0"))

state_lock = threading.Lock()
STATE = {
    "pipeline_status": "idle",
    "pipeline_progress": 0,
    "pipeline_message": "",
    "pipeline_logs": [],
    "signals": [],
    "synthesis": None,
    "last_run_at": None,
    "language": "en",
}

BRIEF_CACHE = {}
ENRICHED_INTEL_CACHE = {}

USERS = {
    "rep": {"user_id": "rep_sarah", "role": "rep", "name": "Sarah Kim", "initials": "SK"},
    "manager": {"user_id": "mgr_maria", "role": "manager", "name": "Maria Lopez", "initials": "ML"},
}


def load_cached_synthesis():
    if CACHE_PATH.exists():
        try:
            data = json.loads(CACHE_PATH.read_text())
            STATE["signals"] = data.get("signals", [])
            STATE["synthesis"] = data.get("synthesis")
            STATE["last_run_at"] = data.get("last_run_at")
            STATE["pipeline_status"] = "done" if data.get("synthesis") else "idle"
            print(f"[cache] loaded {len(STATE['signals'])} signals")
        except Exception as e:
            print(f"[cache] load failed: {e}")


def save_cache():
    try:
        CACHE_PATH.write_text(json.dumps({
            "signals": STATE["signals"],
            "synthesis": STATE["synthesis"],
            "last_run_at": STATE["last_run_at"],
        }, default=str))
    except Exception as e:
        print(f"[cache] save failed: {e}")


def load_notes():
    if NOTES_PATH.exists():
        try:
            return json.loads(NOTES_PATH.read_text())
        except Exception:
            pass
    return [
        {"note_id": 1, "from_user": "mgr_maria", "to_user": "rep_sarah",
         "subject": "Great move on the DoorDash rebuttal",
         "body": "Sarah - your reframe to 'you own zero customer data' was excellent. <strong>Sharing this in Friday's standup.</strong>",
         "created_at": "2026-05-02T10:14:00", "acknowledged": False},
        {"note_id": 2, "from_user": "mgr_maria", "to_user": "rep_sarah",
         "subject": "Multi-thread on next 5 calls",
         "body": "Pattern: three of your lost deals had a hidden second owner. <strong>Try multi-threading:</strong> ask 'is there a co-owner I should also talk to?'",
         "created_at": "2026-05-01T16:30:00", "acknowledged": False},
    ]


def save_notes(notes):
    NOTES_PATH.write_text(json.dumps(notes, indent=2, default=str))


def run_pipeline_thread(language="en"):
    with state_lock:
        STATE["pipeline_status"] = "extracting"
        STATE["pipeline_progress"] = 0
        STATE["pipeline_message"] = "Starting extraction..."
        STATE["pipeline_logs"] = []
        STATE["language"] = language

    try:
        calls = load_calls()
        restaurants_lookup = load_restaurants_by_id()

        def on_progress(done, total, chunk):
            with state_lock:
                STATE["pipeline_progress"] = int(done / total * 50) if total else 0
                STATE["pipeline_message"] = f"Chunk {chunk} - {done}/{total} calls"

        def on_chunk_done(partial):
            with state_lock:
                STATE["signals"] = partial

        signals = extract_all(calls, progress_callback=on_progress, on_chunk_done=on_chunk_done)
        with state_lock:
            STATE["signals"] = signals
            STATE["pipeline_progress"] = 50
            STATE["pipeline_status"] = "synthesizing"
            STATE["pipeline_message"] = f"Extracted {len(signals)} signals - synthesizing..."

        def on_status(msg):
            with state_lock:
                STATE["pipeline_logs"].append(msg)
                STATE["pipeline_logs"] = STATE["pipeline_logs"][-15:]

        # STREAMING: update STATE['synthesis'] as each sub-agent finishes
        # so manager view can show partial results (live)
        def on_result(key, value):
            with state_lock:
                cur = STATE.get("synthesis") or {
                    "works": {"patterns": []}, "doesnt": {"patterns": []},
                    "rep_cards": [], "competitor_intel": {"competitors": []},
                }
                if key == "works":
                    cur["works"] = value
                elif key == "doesnt":
                    cur["doesnt"] = value
                elif key.startswith("rep:"):
                    rep_id = key.split(":", 1)[1]
                    rc = cur.get("rep_cards", [])
                    rc = [c for c in rc if c.get("rep_id") != rep_id]
                    rc.append({"rep_id": rep_id, "coaching": value})
                    cur["rep_cards"] = rc
                elif key == "competitors":
                    cur["competitor_intel"] = {"competitors": value}
                STATE["synthesis"] = cur

        synthesis = run_synthesis_streaming(
            signals, restaurants_lookup,
            on_result=on_result,
            status_callback=on_status, language=language,
        )

        with state_lock:
            STATE["synthesis"] = synthesis
            STATE["last_run_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
            STATE["pipeline_progress"] = 100
            STATE["pipeline_status"] = "done"
            STATE["pipeline_message"] = "Synthesis complete"

        # Invalidate segment-specific synth caches; they'll regenerate on next request
        SEGMENT_SYNTH_CACHE.clear()
        save_cache()
    except Exception as e:
        with state_lock:
            STATE["pipeline_status"] = "error"
            STATE["pipeline_message"] = f"Pipeline failed: {e}"
        print(f"[pipeline] ERROR: {e}")


def auto_refresh_loop():
    while True:
        time.sleep(max(AUTO_REFRESH_SECONDS, 1))
        if AUTO_REFRESH_SECONDS <= 0:
            continue
        with state_lock:
            if STATE["pipeline_status"] in ("extracting", "synthesizing"):
                continue
        print("[auto-refresh] running pipeline")
        run_pipeline_thread(STATE.get("language", "en"))


@app.on_event("startup")
def startup():
    # Reset pipeline state on app boot. The user's preference is fresh state every run —
    # caching across restarts would mask new code/data changes during dev.
    if CACHE_PATH.exists():
        try:
            CACHE_PATH.unlink()
            print(f"[startup] cleared stale cache at {CACHE_PATH}")
        except Exception as e:
            print(f"[startup] could not clear cache: {e}")
    # Also clear brief cache + reset state explicitly
    BRIEF_CACHE.clear()
    SEGMENT_SYNTH_CACHE.clear()
    ENRICHED_INTEL_CACHE.clear()
    with state_lock:
        STATE["signals"] = []
        STATE["synthesis"] = None
        STATE["last_run_at"] = None
        STATE["pipeline_status"] = "idle"
        STATE["pipeline_progress"] = 0
        STATE["pipeline_message"] = ""
        STATE["pipeline_logs"] = []
    print("[startup] pipeline state reset — manager must trigger a run")
    if AUTO_REFRESH_SECONDS > 0:
        print(f"[startup] auto-refresh every {AUTO_REFRESH_SECONDS}s")
        threading.Thread(target=auto_refresh_loop, daemon=True).start()


# ----- Auth -----

def get_session(request: Request):
    sid = request.cookies.get("session", "")
    parts = sid.split(":")
    if len(parts) < 2:
        return None
    return USERS.get(parts[0])


def require_auth(request: Request):
    user = get_session(request)
    if not user:
        raise HTTPException(401, "Not authenticated")
    return user


def require_role(role, request: Request):
    user = require_auth(request)
    if user["role"] != role:
        raise HTTPException(403, "Forbidden")
    return user


# ----- HTML / CSS / JS routes -----

@app.get("/")
def root():
    return FileResponse(str(BASE / "static" / "login.html"))


@app.get("/app")
def app_page():
    return FileResponse(str(BASE / "static" / "app.html"))


# ----- API -----

class LoginBody(BaseModel):
    username: str


@app.post("/api/login")
def api_login(body: LoginBody, response: Response):
    u = body.username.strip().lower()
    if u in ("rep", "sarah", "rep_sarah"):
        role = "rep"
    elif u in ("manager", "maria", "mgr_maria"):
        role = "manager"
    else:
        raise HTTPException(400, "Try 'rep' or 'manager'")
    response.set_cookie("session", f"{role}:demo", max_age=60 * 60 * 24)
    return {"ok": True, "user": USERS[role]}


@app.post("/api/logout")
def api_logout(response: Response):
    response.delete_cookie("session")
    return {"ok": True}


@app.get("/api/me")
def api_me(request: Request):
    return require_auth(request)


class RunBody(BaseModel):
    language: str = "en"


@app.post("/api/pipeline/run", status_code=202)
def api_pipeline_run(body: RunBody, request: Request):
    # Only managers can trigger a pipeline run. Reps' view doesn't depend on team-wide synthesis.
    require_role("manager", request)
    with state_lock:
        if STATE["pipeline_status"] in ("extracting", "synthesizing"):
            raise HTTPException(409, "Pipeline already running")
        STATE["pipeline_status"] = "extracting"
    threading.Thread(target=run_pipeline_thread, args=(body.language,), daemon=True).start()
    return {"ok": True}


@app.get("/api/pipeline/status")
def api_pipeline_status(request: Request):
    require_auth(request)
    with state_lock:
        return {
            "status": STATE["pipeline_status"],
            "progress": STATE["pipeline_progress"],
            "message": STATE["pipeline_message"],
            "logs": STATE["pipeline_logs"],
            "last_run_at": STATE["last_run_at"],
            "n_signals": len(STATE["signals"]),
        }


# ----- Segment filtering (multi-select with AND logic) -----

# Cache for per-segment re-synthesis. Keyed by canonical filter string.
SEGMENT_SYNTH_CACHE = {}


def _parse_filters(segment_str):
    """Parse a multi-select filter string into a structured dict.
    Format: 'cuisine:Pizza,Mexican|business_type:QSR|locations:single'
    Returns: {'cuisine': ['Pizza', 'Mexican'], 'business_type': ['QSR'], 'locations': ['single']}
    """
    if not segment_str or segment_str == "all":
        return {}
    out = {}
    for part in segment_str.split("|"):
        if ":" not in part:
            continue
        dim, vals = part.split(":", 1)
        out[dim.strip()] = [v.strip() for v in vals.split(",") if v.strip()]
    return out


def _canonical_filter_key(filters):
    """Stable cache key from a parsed filter dict (sort dimensions + values)."""
    if not filters:
        return "all"
    parts = []
    for dim in sorted(filters):
        vals = sorted(filters[dim])
        parts.append(f"{dim}:{','.join(vals)}")
    return "|".join(parts)


def _segment_match(signal, filters):
    """AND across dimensions, OR within (signal must match at least one value per active dimension).

    Reads cuisine_type/restaurant_type/num_locations from the signal directly (those are
    columns in the user's calls.csv schema). Falls back to restaurant table lookup if
    the signal doesn't carry them (older signals or different schemas)."""
    if not filters:
        return True

    # Get fields off the signal first; fall back to restaurant table
    cuisine = signal.get("cuisine_type")
    biz = signal.get("restaurant_type")
    nloc = signal.get("num_locations")

    if not cuisine or not biz or not nloc:
        rid = signal.get("restaurant_id")
        rest = load_restaurants_by_id().get(rid, {}) if rid else {}
        cuisine = cuisine or F.get_cuisine(rest)
        biz = biz or F.get_business_type(rest)
        nloc = nloc or F.get_locations(rest)

    cuisine = (cuisine or "").lower()
    biz = (biz or "").lower()
    try:
        nloc = int(nloc) if nloc else 1
    except (TypeError, ValueError):
        nloc = 1

    for dim, vals in filters.items():
        if not vals:
            continue
        if dim == "cuisine":
            if cuisine not in [v.lower() for v in vals]:
                return False
        elif dim == "business_type":
            if biz not in [v.lower() for v in vals]:
                return False
        elif dim == "locations":
            ok = False
            for v in vals:
                if v == "single" and nloc == 1: ok = True
                if v == "multi" and nloc >= 2: ok = True
            if not ok:
                return False
    return True


def _filter_signals_by_segment(signals, segment_str):
    filters = _parse_filters(segment_str)
    if not filters:
        return list(signals)
    return [s for s in signals if _segment_match(s, filters)]


def _resynthesize_for_segment(segment_str, signals_subset, language="en"):
    """Re-run synthesis sub-agents on filtered signal subset."""
    from agents import (
        agent_synthesize_what_works, agent_synthesize_what_doesnt,
        agent_rep_coaching,
    )
    won = [s for s in signals_subset if s.get("demo_booked")]
    lost = [s for s in signals_subset if not s.get("demo_booked")]

    rep_summaries = {}
    for s in signals_subset:
        rep = s.get("rep_id")
        if not rep:
            continue
        rep_summaries.setdefault(rep, []).append(s)

    rep_avg = {}
    for rep_id, calls in rep_summaries.items():
        n = len(calls)
        booked = sum(1 for c in calls if c.get("demo_booked"))
        rep_avg[rep_id] = {
            "n_calls": n,
            "n_booked": booked,
            "booking_rate": booked / n if n else 0,
            "avg_talk_ratio": round(sum(c.get("rep_talk_ratio_estimate") or 0 for c in calls) / n, 2) if n else 0,
            "avg_questions": round(sum(c.get("questions_asked_count") or 0 for c in calls) / n, 1) if n else 0,
            "sample_won_opener": next((c.get("opener_quote") for c in calls if c.get("demo_booked")), None),
            "sample_lost_opener": next((c.get("opener_quote") for c in calls if not c.get("demo_booked")), None),
        }
    n_total = len(signals_subset)
    n_booked_total = sum(1 for s in signals_subset if s.get("demo_booked"))
    team_avg = {
        "booking_rate": n_booked_total / n_total if n_total else 0,
        "avg_talk_ratio": round(sum(s.get("rep_talk_ratio_estimate") or 0 for s in signals_subset) / n_total, 2) if n_total else 0,
        "avg_questions": round(sum(s.get("questions_asked_count") or 0 for s in signals_subset) / n_total, 1) if n_total else 0,
    }

    works_result = {"patterns": []}
    doesnt_result = {"patterns": []}
    rep_cards = []

    has_anthropic_key = bool(os.getenv("ANTHROPIC_API_KEY"))

    if has_anthropic_key:
        with ThreadPoolExecutor(max_workers=4) as pool:
            futs = {}
            if won:
                futs[pool.submit(agent_synthesize_what_works, won, language)] = "works"
            if lost:
                futs[pool.submit(agent_synthesize_what_doesnt, lost, language)] = "doesnt"
            for rep_id, summary in rep_summaries.items():
                futs[pool.submit(agent_rep_coaching, rep_id, summary, team_avg, language)] = ("rep", rep_id)

            for fut in as_completed(futs):
                tag = futs[fut]
                try:
                    val = fut.result()
                except Exception:
                    continue
                if tag == "works":
                    works_result = val
                elif tag == "doesnt":
                    doesnt_result = val
                elif isinstance(tag, tuple) and tag[0] == "rep":
                    rep_cards.append({
                        "rep_id": tag[1],
                        "stats": rep_avg[tag[1]],
                        "coaching": val,
                    })
    else:
        rep_cards = [{"rep_id": rid, "stats": rep_avg[rid], "coaching": {}} for rid in rep_summaries]

    with state_lock:
        original = STATE["synthesis"] or {}
    competitor_intel = original.get("competitor_intel", {"competitors": []})

    return {
        "works": works_result,
        "doesnt": doesnt_result,
        "rep_cards": rep_cards,
        "competitor_intel": competitor_intel,
        "team_avg": team_avg,
    }


# ----- Synthesis & charts endpoints (with optional segment filter) -----

def _filter_synthesis_to_segment(original_synth, signals_subset):
    """Take the FULL synthesis (already computed with LLM) and filter it down to
    a segment instantly — no LLM call needed.

    For each pattern in works/doesnt, check whether any signal in the subset matches
    the pattern's evidence (call_ids referenced, or signals with similar opener_type).
    If a pattern has no evidence in this segment, drop it. If it does, keep it but
    update the count to reflect the segment.

    This trades some accuracy (a pattern's "why_it_works" rationale was synthesized
    from the FULL dataset) for speed: filter response is <50ms instead of 5-10s.
    """
    if not signals_subset:
        return {
            "works": {"patterns": []},
            "doesnt": {"patterns": []},
            "rep_cards": [],
            "competitor_intel": original_synth.get("competitor_intel", {"competitors": []}),
        }

    subset_call_ids = {s.get("call_id") for s in signals_subset}
    subset_won = [s for s in signals_subset if s.get("demo_booked")]
    subset_lost = [s for s in signals_subset if not s.get("demo_booked")]
    subset_reps = {s.get("rep_id") for s in signals_subset if s.get("rep_id")}

    def filter_patterns(patterns, signals_pool):
        """Keep patterns that have evidence in the subset; recompute counts."""
        out = []
        pool_call_ids = {s.get("call_id") for s in signals_pool}
        for p in patterns or []:
            evidence_ids = set(p.get("evidence_call_ids") or [])
            if evidence_ids:
                hit_ids = evidence_ids & pool_call_ids
                if not hit_ids:
                    continue  # No evidence in this segment — drop
                p2 = dict(p)
                p2["evidence_call_ids"] = sorted(hit_ids)
                p2["count"] = len(hit_ids)
                out.append(p2)
            else:
                # No evidence linkage — keep all if subset is non-trivial
                if len(signals_pool) > 0:
                    out.append(p)
        return out

    works = filter_patterns(
        (original_synth.get("works") or {}).get("patterns", []),
        subset_won,
    )
    doesnt = filter_patterns(
        (original_synth.get("doesnt") or {}).get("patterns", []),
        subset_lost,
    )

    # Filter rep cards to reps present in the segment
    rep_cards = [
        rc for rc in (original_synth.get("rep_cards") or [])
        if rc.get("rep_id") in subset_reps
    ]

    return {
        "works": {"patterns": works},
        "doesnt": {"patterns": doesnt},
        "rep_cards": rep_cards,
        "competitor_intel": original_synth.get("competitor_intel", {"competitors": []}),
    }


@app.get("/api/synthesis")
def api_synthesis(request: Request, segment: str = "all", slim: int = 0):
    """Return aggregated synthesis. With slim=1, strips rep_cards (used by Team page only)
    so payload to Themes/Slice pages is ~3-5x smaller and faster to parse."""
    require_role("manager", request)
    with state_lock:
        signals_all = list(STATE["signals"])
        original = dict(STATE["synthesis"]) if STATE["synthesis"] else {
            "works": {"patterns": []}, "doesnt": {"patterns": []},
            "rep_cards": [], "competitor_intel": {"competitors": []}
        }

    filters = _parse_filters(segment)
    cache_key = _canonical_filter_key(filters)

    if cache_key == "all":
        synth = original
        signals_subset = signals_all
    else:
        signals_subset = [s for s in signals_all if _segment_match(s, filters)]
        synth = _filter_synthesis_to_segment(original, signals_subset)

    n_calls = len(signals_subset)
    n_booked = sum(1 for s in signals_subset if s.get("demo_booked"))
    synth = dict(synth)
    synth["overview"] = {
        "n_calls": n_calls,
        "n_booked": n_booked,
        "booking_rate": round(n_booked / n_calls * 100, 1) if n_calls else 0,
        "n_reps": len({s["rep_id"] for s in signals_subset if s.get("rep_id")}),
        "segment": cache_key,
    }
    if slim:
        # Strip rep_cards (only Team page uses these) and competitor_intel from
        # the synthesis response — Themes/Slice can fetch them separately if needed.
        synth.pop("rep_cards", None)
        # Trim each pattern's evidence_call_ids — UI only needs the count
        for k in ("works", "doesnt"):
            for p in (synth.get(k) or {}).get("patterns", []):
                if "evidence_call_ids" in p and len(p.get("evidence_call_ids", [])) > 3:
                    p["evidence_call_ids"] = p["evidence_call_ids"][:3]
    return synth


@app.get("/api/charts")
def api_charts(request: Request, segment: str = "all"):
    require_role("manager", request)
    with state_lock:
        signals_all = list(STATE["signals"])
    signals = _filter_signals_by_segment(signals_all, segment)

    opener_outcome = {}
    for s in signals:
        ot = s.get("opener_type") or "unknown"
        outcome = "Won" if s.get("demo_booked") else "Lost"
        opener_outcome.setdefault(ot, {"Won": 0, "Lost": 0})[outcome] += 1

    lang_counts = Counter(s.get("language_detected") or "en" for s in signals)
    obj_counts = Counter()
    obj_won = Counter()
    for s in signals:
        for o in s.get("objections_raised", []):
            cat = o.get("category", "other")
            obj_counts[cat] += 1
            if s.get("demo_booked"):
                obj_won[cat] += 1
    comp_counts = Counter()
    comp_won = Counter()
    for s in signals:
        for c in s.get("competitors_mentioned", []):
            comp_counts[c] += 1
            if s.get("demo_booked"):
                comp_won[c] += 1
    pain_counts = Counter()
    for s in signals:
        if s.get("demo_booked"):
            for p in s.get("pain_points_surfaced", []):
                pain_counts[p] += 1

    return {
        "opener_outcome": [{"opener_type": k, "Won": v["Won"], "Lost": v["Lost"]}
                           for k, v in opener_outcome.items()],
        "languages": [{"language": k, "count": v} for k, v in lang_counts.items()],
        "objections": [{"category": k, "raised": v, "won": obj_won[k],
                        "win_rate": round(obj_won[k] / v * 100, 1) if v else 0}
                       for k, v in obj_counts.most_common(8)],
        "competitors": [{"name": k, "mentions": v,
                         "win_rate": round(comp_won[k] / v * 100, 1) if v else 0}
                        for k, v in comp_counts.most_common(8)],
        "pain_points": [{"pain": k, "count": v} for k, v in pain_counts.most_common(8)],
        "segment": segment,
        "n_calls_in_segment": len(signals),
    }


@app.get("/api/segments")
def api_segments(request: Request):
    """List all segment options available based on the loaded data.
    Pulls from BOTH restaurants.csv (if it has cuisine_type) and calls.csv (which always has cuisine_type in the user's schema)."""
    require_role("manager", request)
    restaurants = load_restaurants()
    calls = load_calls()
    cuisines = set()
    business_types = set()
    for r in restaurants:
        c = F.get_cuisine(r)
        if c and c != "—": cuisines.add(c)
        b = F.get_business_type(r)
        if b: business_types.add(b)
    for c in calls:
        cu = F.get_cuisine(c)
        if cu and cu != "—": cuisines.add(cu)
        bt = F.get_business_type(c)
        if bt: business_types.add(bt)
    return {
        "cuisines": sorted(cuisines),
        "business_types": sorted(business_types),
    }


@app.get("/api/queue")
def api_queue(request: Request):
    require_role("rep", request)
    restaurants = load_restaurants()[:6]
    times = ["10:00 AM", "11:30 AM", "1:00 PM", "2:15 PM", "3:00 PM", "4:00 PM"]
    out = []
    for i, r in enumerate(restaurants):
        rid = F.get(r, "restaurant_id", "id")
        if not rid:
            continue
        name = F.get_name(r)
        out.append({
            "restaurant_id": rid, "name": name,
            "cuisine": F.get_cuisine(r),
            "business_type": F.get_business_type(r),
            "website_url": F.get_website(r),
            "num_locations": F.get_locations(r),
            "city": F.get_city(r), "state": F.get_state(r),
            "owner": F.get_owner(r),
            "rating": F.get_rating(r),
            "review_count": F.get_review_count(r),
            "spend": F.get_commission_spend(r),
            "attempt": (i % 3) + 1,
            "recommended_time": times[i % len(times)],
            "is_now": i == 0,
            "initials": "".join(w[0] for w in (name or "").split()[:2]).upper(),
        })
    return out


@app.get("/api/brief/{restaurant_id}")
def api_brief(restaurant_id: str, request: Request, lang: str = "en"):
    require_role("rep", request)
    cache_key = f"{restaurant_id}:{lang}"
    if cache_key in BRIEF_CACHE:
        return BRIEF_CACHE[cache_key]

    restaurants = load_restaurants_by_id()
    r = restaurants.get(restaurant_id)
    if not r:
        raise HTTPException(404, "Not found")

    with state_lock:
        signals = list(STATE["signals"])
        seg_works = (STATE["synthesis"] or {}).get("works", {}).get("patterns", [])[:4]

    if lang == "es":
        # Build ES + EN in parallel so the rep can see both versions for talking points
        en_cache_key = f"{restaurant_id}:en"
        with ThreadPoolExecutor(max_workers=2) as pool:
            f_es = pool.submit(build_brief, r, signals, seg_works, "es")
            if en_cache_key in BRIEF_CACHE:
                f_en = None
            else:
                f_en = pool.submit(build_brief, r, signals, seg_works, "en")
            brief = f_es.result()
            en_brief = BRIEF_CACHE[en_cache_key] if f_en is None else f_en.result()
            if f_en is not None:
                BRIEF_CACHE[en_cache_key] = en_brief
        # Attach English fallback for fields that benefit from dual display
        brief["en_fallback"] = {
            "opener": en_brief.get("opener"),
            "voicemail": en_brief.get("voicemail"),
            "talking_points": (en_brief.get("account_intel") or {}).get("talking_points", []),
            "intel": (en_brief.get("account_intel") or {}).get("intel", ""),
            "rebuttal_angle": ((en_brief.get("account_intel") or {}).get("platform_intel") or {}).get("rebuttal_angle", ""),
        }
    else:
        brief = build_brief(r, signals, seg_works, language=lang)

    BRIEF_CACHE[cache_key] = brief
    return brief


# Lazy enrichment endpoint — fires Tavily only when the rep expands "More context".
# The main brief load stays fast (no Tavily); enriched cuisine_intel is fetched
# in the background on demand.

@app.get("/api/intel/{restaurant_id}")
def api_intel(restaurant_id: str, request: Request, lang: str = "en"):
    require_role("rep", request)
    cache_key = f"{restaurant_id}:{lang}"
    if cache_key in ENRICHED_INTEL_CACHE:
        return ENRICHED_INTEL_CACHE[cache_key]

    restaurants = load_restaurants_by_id()
    r = restaurants.get(restaurant_id)
    if not r:
        raise HTTPException(404, "Not found")

    has_tavily = bool(os.getenv("TAVILY_API_KEY"))
    has_anthropic = bool(os.getenv("ANTHROPIC_API_KEY"))

    if not has_tavily or not has_anthropic:
        # Without Tavily, fallback intel is the same thing the brief already has — return it
        result = _fallback_intel(r, language=lang)
    else:
        try:
            from agents import agent_account_intel
            result = agent_account_intel(r, language=lang)
            # Merge missing fields from fallback
            fb = _fallback_intel(r, language=lang)
            result["cuisine_intel"] = result.get("cuisine_intel") or fb["cuisine_intel"]
            result["platform_intel"] = result.get("platform_intel") or fb["platform_intel"]
        except Exception:
            result = _fallback_intel(r, language=lang)

    ENRICHED_INTEL_CACHE[cache_key] = result
    return result


@app.get("/api/similar_calls/{restaurant_id}")
def api_similar_calls(restaurant_id: str, request: Request):
    """Return up to 3 similar WON calls — same cuisine if possible, otherwise any won call.
    Returns rep, restaurant, key moment, transcript snippet."""
    require_role("rep", request)
    restaurants = load_restaurants_by_id()
    target = restaurants.get(restaurant_id)
    if not target:
        raise HTTPException(404, "Not found")
    target_cuisine = (F.get_cuisine(target) or "").lower()

    calls = load_calls()
    won = [c for c in calls if (F.get_outcome(c) or "") in ("demo_booked", "won")]

    # Prefer same cuisine
    same_cuisine = []
    other = []
    for c in won:
        rid = F.get_restaurant_id(c)
        if rid == restaurant_id:
            continue  # don't suggest the target itself
        rest = restaurants.get(rid)
        if not rest:
            continue
        rec = {
            "call_id": F.get_call_id(c),
            "rep_id": F.get_rep_id(c),
            "restaurant_id": rid,
            "restaurant_name": F.get_name(rest),
            "cuisine": F.get_cuisine(rest),
            "city": F.get_city(rest),
            "state": F.get_state(rest),
            "duration_seconds": F.get_duration(c),
            "transcript_snippet": (F.get_transcript(c) or "")[:380],
            "key_moment": _extract_key_moment(F.get_transcript(c) or ""),
        }
        if (F.get_cuisine(rest) or "").lower() == target_cuisine:
            same_cuisine.append(rec)
        else:
            other.append(rec)

    selected = (same_cuisine + other)[:3]
    return {"calls": selected, "count": len(selected)}


def _extract_key_moment(transcript: str) -> str:
    """Pull a punchy line from the middle of the transcript — usually where the rep landed the value prop."""
    if not transcript:
        return ""
    lines = [l.strip() for l in transcript.split("\n") if l.strip().startswith("Rep:")]
    if not lines:
        return transcript[:200]
    # Pick from middle 60% of rep lines — usually where the close happens
    if len(lines) >= 5:
        mid = lines[len(lines)//2:int(len(lines)*0.85)]
        # Prefer lines mentioning numbers or dollar signs (closing math)
        for line in mid:
            if "$" in line or "%" in line or "K" in line:
                return line[5:].strip()  # strip "Rep: "
        return mid[0][5:].strip()
    return lines[len(lines)//2][5:].strip()


@app.get("/api/audio/{call_id}")
def api_audio(call_id: str, request: Request):
    """Stream an audio file generated from the call transcript.
    Uses ElevenLabs if ELEVENLABS_API_KEY is set, else returns a friendly 503.
    Audio is cached on disk per call_id to avoid re-generation."""
    require_role("rep", request)

    cache_dir = Path("/tmp/owner_audio")
    cache_dir.mkdir(exist_ok=True)
    audio_path = cache_dir / f"{call_id}.mp3"

    if audio_path.exists() and audio_path.stat().st_size > 0:
        return FileResponse(str(audio_path), media_type="audio/mpeg")

    # Need to generate
    api_key = os.getenv("ELEVENLABS_API_KEY")
    if not api_key:
        raise HTTPException(503, "ELEVENLABS_API_KEY not set in .env")

    # Find the call
    calls = load_calls()
    call = next((c for c in calls if F.get_call_id(c) == call_id), None)
    if not call:
        raise HTTPException(404, "Call not found")
    transcript = F.get_transcript(call) or ""
    if not transcript:
        raise HTTPException(404, "No transcript for this call")

    # Truncate to first ~1200 chars to keep TTS fast/cheap; that's the "highlight reel"
    text_for_tts = transcript[:1200]

    try:
        from elevenlabs.client import ElevenLabs
        client = ElevenLabs(api_key=api_key)
        # Use a default voice; users can override via env if they want
        voice_id = os.getenv("ELEVENLABS_VOICE_ID", "21m00Tcm4TlvDq8ikWAM")  # "Rachel"
        audio_stream = client.text_to_speech.convert(
            voice_id=voice_id,
            text=text_for_tts,
            model_id="eleven_turbo_v2_5",
            output_format="mp3_44100_128",
        )
        with open(audio_path, "wb") as f:
            for chunk in audio_stream:
                if chunk:
                    f.write(chunk)
        return FileResponse(str(audio_path), media_type="audio/mpeg")
    except Exception as e:
        raise HTTPException(500, f"TTS failed: {e}")


@app.get("/api/transcript/{call_id}")
def api_transcript(call_id: str, request: Request):
    """Return the raw transcript for display alongside audio playback."""
    require_role("rep", request)
    calls = load_calls()
    call = next((c for c in calls if F.get_call_id(c) == call_id), None)
    if not call:
        raise HTTPException(404, "Call not found")
    return {
        "call_id": call_id,
        "rep_id": F.get_rep_id(call),
        "outcome": F.get_outcome(call),
        "duration_seconds": F.get_duration(call),
        "transcript": F.get_transcript(call) or "",
    }


@app.get("/api/my_stats")
def api_my_stats(request: Request):
    user = require_role("rep", request)
    rep_id = user["user_id"]
    with state_lock:
        signals = list(STATE["signals"])

    def stats_for(group):
        if not group:
            return {"booking_rate": 0, "avg_talk_ratio": 0, "avg_questions": 0,
                    "n_booked": 0, "n_calls": 0}
        booked = sum(1 for s in group if s.get("demo_booked"))
        return {
            "n_calls": len(group), "n_booked": booked,
            "booking_rate": round(booked / len(group) * 100, 1),
            "avg_talk_ratio": round(sum(s.get("rep_talk_ratio_estimate") or 0 for s in group) / len(group), 2),
            "avg_questions": round(sum(s.get("questions_asked_count") or 0 for s in group) / len(group), 1),
        }

    mine = [s for s in signals if s.get("rep_id") == rep_id]
    return {"rep": stats_for(mine), "team": stats_for(signals)}


@app.get("/api/coaching")
def api_coaching(request: Request):
    user = require_role("rep", request)
    rep_id = user["user_id"]
    with state_lock:
        synth = STATE["synthesis"] or {}
    cards = synth.get("rep_cards", [])
    mine = next((c for c in cards if c["rep_id"] == rep_id), None)
    return {"coaching": (mine.get("coaching") if mine else None),
            "notes": [n for n in load_notes() if n.get("to_user") == rep_id]}


class NoteBody(BaseModel):
    to_user: str
    subject: str
    body: str


@app.post("/api/note")
def api_note(body: NoteBody, request: Request):
    user = require_role("manager", request)
    notes = load_notes()
    nid = max((n.get("note_id", 0) for n in notes), default=0) + 1
    notes.append({
        "note_id": nid, "from_user": user["user_id"],
        "to_user": body.to_user, "subject": body.subject,
        "body": body.body, "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "acknowledged": False,
    })
    save_notes(notes)
    return {"ok": True, "note_id": nid}


@app.post("/api/notes/{note_id}/ack")
def api_ack(note_id: int, request: Request):
    user = require_role("rep", request)
    notes = load_notes()
    for n in notes:
        if n.get("note_id") == note_id and n.get("to_user") == user["user_id"]:
            n["acknowledged"] = True
    save_notes(notes)
    return {"ok": True}


@app.get("/api/meta")
def api_meta(request: Request):
    user = require_auth(request)
    return {
        "user": user,
        "calls_loaded": len(load_calls()),
        "restaurants_loaded": len(load_restaurants()),
        "has_anthropic": bool(os.getenv("ANTHROPIC_API_KEY")),
        "has_tavily": bool(os.getenv("TAVILY_API_KEY")),
        "has_elevenlabs": bool(os.getenv("ELEVENLABS_API_KEY")),
        "auto_refresh_seconds": AUTO_REFRESH_SECONDS,
    }


if __name__ == "__main__":
    import uvicorn
    port = int(os.getenv("PORT", "8000"))
    print(f"Server starting at http://localhost:{port}")
    print(f"  Login as 'rep' (Sarah Kim) or 'manager' (Maria Lopez)")
    uvicorn.run(app, host="0.0.0.0", port=port, log_level="info")