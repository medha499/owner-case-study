"""Multi-agent layer.

Architecture:
  ORCHESTRATOR (Opus 4.5) coordinates everything
  Sub-agents (Haiku 4.5) — fast, cheap, parallel:
    • extract_one_call         — extract structured signals from one transcript
    • agent_synthesize_what_works    — find winning patterns across won calls
    • agent_synthesize_what_doesnt   — find anti-patterns across lost calls
    • agent_rep_coaching             — per-rep narrative + tactical advice
    • agent_brief_opener             — generate per-account opener (i18n)
    • agent_brief_voicemail          — generate per-account voicemail (i18n)
    • agent_account_intel            — Tavily live web → intel + talking points
    • agent_one_competitor           — Tavily live web → competitor summary

  TAVILY agent runs live web search for competitor intel + per-account news.

  All sub-agent calls run concurrently via ThreadPoolExecutor.
  Synthesis exposes per-task callbacks so UI can populate progressively.
"""

import os
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from anthropic import Anthropic
from dotenv import load_dotenv

load_dotenv()

OPUS = "claude-opus-4-5"
HAIKU = "claude-haiku-4-5-20251001"

MAX_WORKERS = int(os.getenv("EXTRACT_MAX_WORKERS", "8"))
CHUNK_SIZE = int(os.getenv("EXTRACT_CHUNK_SIZE", "20"))

_client = None


def get_client():
    global _client
    if _client is None:
        api_key = os.getenv("ANTHROPIC_API_KEY")
        if not api_key:
            return None
        _client = Anthropic(api_key=api_key)
    return _client


def _call(model, prompt, max_tokens=2000, system=None, cache_system=False):
    """Call Anthropic. If cache_system=True and system is set, mark the system block
    with cache_control so subsequent calls hit the prompt cache (5min TTL, ~10x cheaper
    after first hit, latency drops from ~2s to ~400ms typical)."""
    client = get_client()
    if client is None:
        raise RuntimeError("ANTHROPIC_API_KEY not set")
    kwargs = {"model": model, "max_tokens": max_tokens,
              "messages": [{"role": "user", "content": prompt}]}
    if system:
        if cache_system:
            # Anthropic prompt caching: pass system as a list with cache_control
            kwargs["system"] = [{"type": "text", "text": system,
                                 "cache_control": {"type": "ephemeral"}}]
        else:
            kwargs["system"] = system
    return client.messages.create(**kwargs).content[0].text.strip()


def _parse_json(text):
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text
        text = text.rsplit("```", 1)[0]
    return json.loads(text)


# ============ EXTRACTION ============

# System prompt — static across all calls. Marked for prompt caching so the model
# only needs to "load" this instruction set once per ~5min window. Massive speedup
# when extracting 50+ calls.
EXTRACTION_SYSTEM = """You are a sales-call signal extractor for Owner.com (helps restaurants reduce 3rd-party commissions like DoorDash, Toast).

The transcript may be in English or Spanish — extract signals regardless of language.

For every transcript you receive, return ONLY valid JSON in this exact shape:
{
  "opener_type": "personalized" | "generic" | "permission_based" | "reason_statement" | "other",
  "opener_quote": "exact first 1-2 sentences from rep, verbatim",
  "personalization_signals": ["specific things rep referenced"],
  "questions_asked_count": <int>,
  "first_pitch_at_seconds": <int or null>,
  "rep_talk_ratio_estimate": <float 0-1>,
  "competitors_mentioned": ["competitor names"],
  "objections_raised": [{"text": "...", "category": "price|timing|authority|fit|status_quo|competitor|other", "handled": true|false, "rebuttal_used": "..." or null}],
  "pain_points_surfaced": ["..."],
  "next_step_proposed": "..." or null,
  "next_step_confirmed_verbally": true|false,
  "specific_demo_time_proposed": true|false,
  "outcome_proxy": "demo_booked"|"callback"|"voicemail"|"not_interested"|"lost"|"no_answer",
  "language_detected": "en" | "es" | "mixed"
}

Return JSON only — no markdown, no preamble, no commentary."""


def extract_one_call(transcript):
    # Per-call user message is just the transcript. The static system prompt is cached.
    prompt = f"Transcript:\n---\n{transcript}\n---\n\nReturn JSON."
    return _parse_json(_call(HAIKU, prompt, max_tokens=2000,
                              system=EXTRACTION_SYSTEM, cache_system=True))


def parallel_extract_chunked(calls, progress_callback=None, on_chunk_done=None):
    """Extract signals from all calls in parallel using a single thread pool.
    Streams partial results to on_chunk_done every CHUNK_SIZE completions.
    """
    if not get_client():
        return [{"call": c, "signals": None, "error": "no api key"} for c in calls]

    results = [None] * len(calls)
    total = len(calls)

    def work(idx_call):
        idx, call = idx_call
        try:
            signals = extract_one_call(call.get("transcript", ""))
            return idx, {"call": call, "signals": signals, "error": None}
        except Exception as e:
            return idx, {"call": call, "signals": None, "error": str(e)}

    # Single pool across the whole dataset — no sequential chunk waiting.
    # MAX_WORKERS controls API concurrency (8 by default).
    pool_size = min(MAX_WORKERS * 2, max(MAX_WORKERS, total))
    done = 0
    last_streamed = 0

    with ThreadPoolExecutor(max_workers=pool_size) as pool:
        futures = [pool.submit(work, (i, c)) for i, c in enumerate(calls)]
        for fut in as_completed(futures):
            idx, result = fut.result()
            results[idx] = result
            done += 1
            if progress_callback:
                progress_callback(done, total, 1)

            # Stream partial results every CHUNK_SIZE completions so UI updates live
            if on_chunk_done and (done - last_streamed >= CHUNK_SIZE or done == total):
                last_streamed = done
                partial = [r for r in results if r is not None]
                on_chunk_done(partial)

    return results


# ============ SYNTHESIS SUB-AGENTS ============

def agent_synthesize_what_works(won_summaries, language="en"):
    lang_instr = " Write all output in Spanish." if language == "es" else ""
    prompt = f"""Won sales calls for Owner.com. Find what's WORKING.

DATA — {len(won_summaries)} won calls:
{json.dumps(won_summaries, indent=2, default=str)[:30000]}

Synthesize 4-5 patterns. For each: pattern (short name), why_it_works (1 sentence),
example_quote (concrete pulled from data), source_rep, source_restaurant, n_calls.{lang_instr}

Return ONLY JSON:
{{"patterns": [{{"pattern": "...", "why_it_works": "...", "example_quote": "...",
                "source_rep": "...", "source_restaurant": "...", "n_calls": <int>}}]}}"""
    return _parse_json(_call(HAIKU, prompt, max_tokens=2500))


def agent_synthesize_what_doesnt(lost_summaries, language="en"):
    lang_instr = " Write all output in Spanish." if language == "es" else ""
    prompt = f"""LOST sales calls for Owner.com. Find anti-patterns.

DATA — {len(lost_summaries)} lost calls:
{json.dumps(lost_summaries, indent=2, default=str)[:30000]}

4-5 anti-patterns. For each: pattern, why_it_loses, example_quote, source_rep, source_restaurant, n_calls.{lang_instr}

Return ONLY JSON:
{{"patterns": [{{"pattern": "...", "why_it_loses": "...", "example_quote": "...",
                "source_rep": "...", "source_restaurant": "...", "n_calls": <int>}}]}}"""
    return _parse_json(_call(HAIKU, prompt, max_tokens=2500))


def agent_rep_coaching(rep_id, rep_summary, team_avg, language="en"):
    lang_instr = " Write the narrative in Spanish." if language == "es" else ""
    prompt = f"""Sales coach writing about rep {rep_id}.

THIS REP: {json.dumps(rep_summary, indent=2, default=str)}
TEAM AVG: {json.dumps(team_avg, indent=2)}

3-5 sentence narrative. Lead with strength + concrete example. ONE specific weakness. Tactical action.{lang_instr}

Return ONLY JSON:
{{"summary": "narrative", "strength": "...", "weakness": "...",
  "suggested_action": "...", "tier": "top_quartile"|"steady"|"struggling"}}"""
    return _parse_json(_call(HAIKU, prompt, max_tokens=1500))


def agent_brief_opener(restaurant, segment_what_works, language="en"):
    lang_instr = ""
    if language == "es":
        lang_instr = "\n\nIMPORTANT: Write the entire opener in conversational Spanish (the rep speaks Spanish to a Spanish-speaking owner)."

    prompt = f"""Cold call opener for Owner.com SDR calling this restaurant.

RESTAURANT: {json.dumps(restaurant, indent=2, default=str)}
SEGMENT PATTERNS: {json.dumps(segment_what_works, indent=2, default=str)}

50-80 words. Reason-for-call framing. Reference SPECIFIC fact (rating, top dish, current setup).
Lead with a number. Wrap key facts in <em></em>. End with permission question.{lang_instr}

Return ONLY the opener (in double quotes)."""
    return _call(HAIKU, prompt, max_tokens=400)


def agent_brief_voicemail(restaurant, language="en"):
    lang_instr = "\n\nIMPORTANT: Write the script entirely in Spanish." if language == "es" else ""
    prompt = f"""15-second voicemail for Owner.com SDR.

{json.dumps(restaurant, indent=2, default=str)}

~50 words. Hook first. Specific. End with concrete callback.{lang_instr}

Return ONLY the script."""
    return _call(HAIKU, prompt, max_tokens=300)


# ============ TAVILY ============

def _get_tavily():
    api_key = os.getenv("TAVILY_API_KEY")
    if not api_key:
        return None
    try:
        from tavily import TavilyClient
        return TavilyClient(api_key=api_key)
    except ImportError:
        return None


def tavily_search(query, max_results=4):
    client = _get_tavily()
    if client is None:
        return []
    try:
        result = client.search(query=query, max_results=max_results, search_depth="advanced")
        return [{"title": r.get("title", ""), "url": r.get("url", ""),
                 "content": r.get("content", "")} for r in result.get("results", [])]
    except Exception as e:
        print(f"[tavily] {e}")
        return []


def _competitor_fallback(comp, language="en"):
    """Static fallback facts about each Owner.com competitor when Tavily isn't available."""
    is_es = language == "es"
    db = {
        "Toast": {
            "category": "POS + Digital Storefront Pro",
            "headline_en": "Toast tightening POS+ordering integration to lock in existing customers",
            "headline_es": "Toast endureciendo integración POS+pedidos para retener clientes",
            "summary_en": "A major competitor, especially for restaurants already using Toast POS, offering tightly integrated website and ordering tools. Their sticky bundle (POS hardware + payments + ordering) makes them hard to displace once installed, but their digital storefront is bolted onto the POS rather than built ordering-first.",
            "summary_es": "Un competidor importante, especialmente para restaurantes ya usando Toast POS, con sitio web y pedidos integrados. Su paquete pegajoso (POS + pagos + pedidos) los hace difíciles de desplazar una vez instalados, pero su tienda digital es agregada al POS en lugar de construida primero para pedidos.",
            "rebuttal_en": "Toast is a POS first — their digital storefront is an add-on. Owner.com is built ordering-first with a real direct-to-consumer marketing engine that drives new customers, not just processes existing ones.",
            "rebuttal_es": "Toast es POS primero — su tienda digital es un complemento. Owner.com está construido pedidos-primero con motor real de marketing directo que trae clientes nuevos, no solo procesa los existentes.",
        },
        "ChowNow": {
            "category": "online ordering",
            "headline_en": "ChowNow's flat-fee model hits ceiling as multi-loc operators scale up",
            "headline_es": "Modelo de tarifa fija de ChowNow encuentra techo con multi-ubicación",
            "summary_en": "ChowNow pioneered commission-free direct ordering with a flat monthly fee. Strong on the ordering experience but light on marketing automation and loyalty, so growth still depends on the operator driving their own traffic. Popular with single-location independents.",
            "summary_es": "ChowNow fue pionero de pedidos directos sin comisión con tarifa fija mensual. Fuerte en experiencia de pedido pero ligero en marketing automatizado y lealtad, así que crecimiento aún depende del operador para tráfico. Popular con independientes de una ubicación.",
            "rebuttal_en": "ChowNow takes orders. Owner.com brings customers — built-in marketing, SMS, loyalty, and a website that ranks on Google.",
            "rebuttal_es": "ChowNow toma pedidos. Owner.com trae clientes — marketing integrado, SMS, lealtad, y sitio web que rankea en Google.",
        },
        "Popmenu": {
            "category": "interactive menus + marketing",
            "headline_en": "Popmenu doubling down on AI-driven marketing automation in 2026",
            "headline_es": "Popmenu apuesta más por marketing automatizado con IA en 2026",
            "summary_en": "Focuses on interactive menus, automated marketing, and websites designed to boost engagement. Strong on the storefront/menu experience and email/SMS automation, but their ordering infrastructure is less mature than dedicated commerce platforms.",
            "summary_es": "Enfocado en menús interactivos, marketing automatizado, y sitios web diseñados para impulsar el engagement. Fuerte en la experiencia del menú y automatización de email/SMS, pero su infraestructura de pedidos es menos madura que plataformas dedicadas a comercio.",
            "rebuttal_en": "Popmenu makes pretty menus. Owner.com is the entire growth engine — beautiful menus AND the ordering, marketing, and loyalty infrastructure to actually convert and retain customers.",
            "rebuttal_es": "Popmenu hace menús bonitos. Owner.com es el motor de crecimiento completo — menús bellos Y la infraestructura de pedidos, marketing, y lealtad para realmente convertir y retener clientes.",
        },
        "Square for Restaurants": {
            "category": "POS + online ordering",
            "headline_en": "Square cutting prices on small-footprint plans, courting QSRs",
            "headline_es": "Square reduce precios en planes pequeños, cortejando QSR",
            "summary_en": "Offers an all-in-one POS and online ordering solution often used for its affordability and ease of use. Strong for cafes and counter-service, weaker for delivery-heavy operators. Their online ordering is generic and doesn't drive direct customer acquisition.",
            "summary_es": "Ofrece una solución todo-en-uno de POS y pedidos en línea, popular por su asequibilidad y facilidad de uso. Fuerte para cafés y mostrador, débil para operadores con mucho delivery. Sus pedidos en línea son genéricos y no impulsan adquisición directa.",
            "rebuttal_en": "Square is the cash register and a basic order page. Owner.com is the entire customer relationship — ordering, marketing, loyalty, all in one platform built for restaurants.",
            "rebuttal_es": "Square es la caja registradora y una página básica de pedidos. Owner.com es toda la relación con el cliente — pedidos, marketing, lealtad, en una sola plataforma para restaurantes.",
        },
        "HungerRush": {
            "category": "POS + online ordering",
            "headline_en": "HungerRush expanding pizza/QSR vertical after recent funding round",
            "headline_es": "HungerRush expande vertical de pizza/QSR tras ronda reciente de inversión",
            "summary_en": "POS + online ordering platform with strong roots in pizza and quick-service. Bundles ordering, delivery, and loyalty into a single stack. Popular with multi-location pizza operators but their digital storefront UX lags newer ordering-first platforms.",
            "summary_es": "Plataforma de POS + pedidos con raíces fuertes en pizza y servicio rápido. Combina pedidos, delivery, y lealtad en un stack único. Popular con operadores multi-ubicación de pizza pero su UX de tienda digital queda atrás de plataformas más nuevas.",
            "rebuttal_en": "HungerRush is solid for pizza chains running their own delivery. Owner.com is built for the operator who wants to own the customer relationship and the entire growth funnel — not just the orders.",
            "rebuttal_es": "HungerRush es sólido para cadenas de pizza con su propia entrega. Owner.com está construido para el operador que quiere ser dueño de la relación con el cliente y todo el embudo de crecimiento — no solo los pedidos.",
        },
    }
    d = db.get(comp, {
        "category": "competitor",
        "headline_en": f"{comp} active in restaurant SaaS",
        "headline_es": f"{comp} activo en SaaS para restaurantes",
        "summary_en": f"{comp} competes in the restaurant SaaS space. Recent news unavailable without Tavily access.",
        "summary_es": f"{comp} compite en el espacio SaaS para restaurantes. Noticias recientes no disponibles sin acceso a Tavily.",
        "rebuttal_en": f"Owner.com is built end-to-end for the operator — ordering, marketing, loyalty, website, all in one.",
        "rebuttal_es": f"Owner.com está construido end-to-end para el operador — pedidos, marketing, lealtad, sitio web, todo en uno.",
    })
    return {
        "name": comp,
        "headline": d["headline_es"] if is_es else d["headline_en"],
        "summary": d["summary_es"] if is_es else d["summary_en"],
        "category": d["category"],
        "rebuttal": d["rebuttal_es"] if is_es else d["rebuttal_en"],
        "sources": [],
    }


def agent_one_competitor(comp, language="en"):
    """Track an Owner.com competitor — recent news, product moves, pricing changes,
    and an angle reps can use when an operator brings them up."""
    queries = [
        f"{comp} restaurant ordering platform news 2026",
        f"{comp} pricing changes acquisition launch",
        f"{comp} vs Owner.com restaurants",
    ]
    all_results = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for fut in as_completed([pool.submit(tavily_search, q, 3) for q in queries]):
            all_results.extend(fut.result())

    if not all_results:
        return _competitor_fallback(comp, language=language)
    try:
        lang_instr = " Write all text fields in Spanish." if language == "es" else ""
        prompt = f"""You are tracking "{comp}", a competitor to Owner.com (restaurant SaaS for online ordering, marketing, loyalty, websites).

LIVE WEB RESULTS:
{json.dumps(all_results, indent=2)}

Extract: (1) one short newsy headline summarizing what's recent (a launch, pricing change, acquisition, partnership, etc), (2) a 2-3 sentence summary covering what the company is doing right now and any strategic shifts, (3) what category they compete in (POS / online ordering / loyalty / marketing / websites), (4) a 1-line angle a rep can use to position Owner.com against them when an operator says "we already use {comp}."{lang_instr}

Return ONLY JSON:
{{
  "headline": "Short newsy headline (under 12 words)",
  "summary": "2-3 sentence narrative on recent activity",
  "category": "POS | online ordering | loyalty | marketing | websites | full-stack",
  "rebuttal": "1-line angle for Owner.com reps when {comp} comes up"
}}"""
        parsed = _parse_json(_call(HAIKU, prompt, max_tokens=600))
        return {
            "name": comp,
            "headline": parsed.get("headline", ""),
            "summary": parsed.get("summary", ""),
            "category": parsed.get("category", "competitor"),
            "rebuttal": parsed.get("rebuttal", ""),
            "sources": [{"title": r["title"], "url": r["url"]} for r in all_results[:4]],
        }
    except Exception as e:
        return {"name": comp, "summary": f"Synthesis failed: {e}",
                "headline": "", "category": "competitor",
                "sources": [], "rebuttal": ""}


def parallel_competitor_intel(competitors, language="en"):
    competitors = list(set(competitors))[:8]
    if not competitors:
        return []
    results = []
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(agent_one_competitor, c, language): c for c in competitors}
        for fut in as_completed(futures):
            try:
                results.append(fut.result())
            except Exception as e:
                results.append({"name": futures[fut], "summary": f"Failed: {e}",
                                "sources": [], "rebuttal": ""})
    return results


def agent_account_intel(restaurant, language="en"):
    name = restaurant.get("name") or restaurant.get("restaurant_name", "")
    city = restaurant.get("city", "")
    state = restaurant.get("state", "")

    cuisine = restaurant.get("cuisine", "")
    queries = [
        f'"{name}" {city} {state} news',
        f'"{name}" restaurant menu online ordering',
        f'"{name}" {city} yelp reviews',
        f'"{name}" doordash grubhub ubereats',
    ]
    all_results = []
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [pool.submit(tavily_search, q, 3) for q in queries]
        for fut in as_completed(futures):
            all_results.extend(fut.result())

    if not all_results:
        return {"intel": "No recent web signals found.", "talking_points": [], "sources": [],
                "cuisine_intel": None, "platform_intel": None}

    try:
        lang_instr = " Write all text fields in Spanish." if language == "es" else ""
        prompt = f"""Prep a sales rep for a call to a restaurant. Extract structured intel from web findings.

RESTAURANT: {json.dumps(restaurant, indent=2, default=str)}
WEB FINDINGS: {json.dumps(all_results, indent=2)}{lang_instr}

Return ONLY JSON with this exact shape:
{{
  "intel": "2-3 sentence narrative brief",
  "talking_points": ["...", "...", "..."],
  "cuisine_intel": {{
    "cuisine_type": "{cuisine}",
    "review_themes": ["theme 1", "theme 2", "theme 3"],
    "top_menu_item": "signature dish from reviews",
    "family_style": "Y/N + 1 line on dining vibe",
    "price_range": "$ / $$ / $$$ + avg ticket if known",
    "recent_news": "1 sentence latest news or 'No recent news'",
    "pain_points_for_segment": ["pain 1 typical for this cuisine type", "pain 2"]
  }},
  "platform_intel": {{
    "platforms_detected": ["DoorDash", "Yelp", "..."],
    "estimated_commission_pct": "20-30% typical",
    "estimated_monthly_loss": "rough $ amount based on rating + review count",
    "rebuttal_angle": "1-line angle for why owning their channel beats 3rd-party"
  }}
}}

Use ONLY information present in the web findings or established cuisine knowledge. Do not fabricate."""
        parsed = _parse_json(_call(HAIKU, prompt, max_tokens=1200))
        return {
            "intel": parsed.get("intel", ""),
            "talking_points": parsed.get("talking_points", []),
            "sources": [{"title": r["title"], "url": r["url"]} for r in all_results[:6]],
            "cuisine_intel": parsed.get("cuisine_intel"),
            "platform_intel": parsed.get("platform_intel"),
        }
    except Exception as e:
        return {"intel": f"Synthesis failed: {e}", "talking_points": [], "sources": [],
                "cuisine_intel": None, "platform_intel": None}


# ============ ORCHESTRATOR — STREAMING-FRIENDLY ============

def _build_call_summary(s, restaurants_lookup):
    r = restaurants_lookup.get(s["restaurant_id"], {})
    return {
        "call_id": s["call_id"], "rep_id": s["rep_id"],
        "restaurant": r.get("name") or s["restaurant_id"],
        "cuisine": r.get("cuisine"),
        "opener_quote": s.get("opener_quote"), "opener_type": s.get("opener_type"),
        "personalization": s.get("personalization_signals", []),
        "pain_points": s.get("pain_points_surfaced", []),
        "competitors": s.get("competitors_mentioned", []),
        "objections": [o.get("text") for o in (s.get("objections_raised") or [])],
        "next_step": s.get("next_step_proposed"),
        "talk_ratio": s.get("rep_talk_ratio_estimate"),
        "questions": s.get("questions_asked_count"),
    }


def orchestrate_synthesis_streaming(signals, restaurants_lookup, on_result=None,
                                     status_callback=None, language="en"):
    """Streaming orchestrator. Calls on_result(key, value) as each agent finishes.

    Keys emitted:
      "works"        -> {"patterns": [...]}
      "doesnt"       -> {"patterns": [...]}
      "rep:<rep_id>" -> {"summary": ..., "tier": ..., ...}
      "competitors"  -> [{"name": ..., "summary": ..., ...}]
    """
    if not get_client():
        # No Anthropic key — but still populate Owner.com competitors from static DB
        # so the demo always shows the Competitor Watch with real content.
        OWNER_COMPETITORS = ["Toast", "ChowNow", "Popmenu", "Square for Restaurants", "HungerRush"]
        fallback_competitors = [_competitor_fallback(c, language) for c in OWNER_COMPETITORS]
        return {"works": {"patterns": []}, "doesnt": {"patterns": []},
                "rep_cards": [],
                "competitor_intel": {"competitors": fallback_competitors}}

    won, lost = [], []
    for s in signals:
        rec = _build_call_summary(s, restaurants_lookup)
        (won if s.get("demo_booked") else lost).append(rec)

    if status_callback:
        status_callback(f"📊 {len(won)} won · {len(lost)} lost — synthesizing...")

    by_rep = {}
    for s in signals:
        by_rep.setdefault(s["rep_id"], []).append(s)

    if signals:
        team_avg = {
            "booking_rate": round(sum(s.get("demo_booked") or 0 for s in signals) / len(signals), 2),
            "avg_talk_ratio": round(sum(s.get("rep_talk_ratio_estimate") or 0 for s in signals) / len(signals), 2),
            "avg_questions": round(sum(s.get("questions_asked_count") or 0 for s in signals) / len(signals), 1),
        }
    else:
        team_avg = {}

    rep_summaries = {}
    for rep_id, calls in by_rep.items():
        booked = sum(c.get("demo_booked") or 0 for c in calls)
        rep_summaries[rep_id] = {
            "n_calls": len(calls), "n_booked": booked,
            "booking_rate": round(booked / len(calls), 2),
            "avg_talk_ratio": round(sum(c.get("rep_talk_ratio_estimate") or 0 for c in calls) / len(calls), 2),
            "avg_questions": round(sum(c.get("questions_asked_count") or 0 for c in calls) / len(calls), 1),
            "sample_won_opener": next((c.get("opener_quote") for c in calls if c.get("demo_booked")), None),
            "sample_lost_opener": next((c.get("opener_quote") for c in calls if not c.get("demo_booked")), None),
        }

    # Owner.com's actual competitors in restaurant SaaS — fixed list, NOT from call extraction.
    # These are the platforms Owner.com competes with for the restaurant operator's business.
    OWNER_COMPETITORS = ["Toast", "ChowNow", "Popmenu", "Square for Restaurants", "HungerRush"]
    competitors = set(OWNER_COMPETITORS)

    result = {
        "works": {"patterns": []}, "doesnt": {"patterns": []},
        "rep_cards": [], "competitor_intel": {"competitors": []},
        "team_avg": team_avg,
    }

    t0 = time.time()
    futures = {}
    rep_coaching = {}

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        if won:
            futures[pool.submit(agent_synthesize_what_works, won, language)] = ("works",)
        if lost:
            futures[pool.submit(agent_synthesize_what_doesnt, lost, language)] = ("doesnt",)
        for rep_id, summary in rep_summaries.items():
            futures[pool.submit(agent_rep_coaching, rep_id, summary, team_avg, language)] = ("rep", rep_id)
        if competitors:
            futures[pool.submit(parallel_competitor_intel, list(competitors), language)] = ("competitors",)

        for fut in as_completed(futures):
            tag = futures[fut]
            try:
                value = fut.result()
            except Exception as e:
                if status_callback:
                    status_callback(f"⚠️ {tag} failed: {e}")
                continue

            if tag[0] == "works":
                result["works"] = value
                if on_result:
                    on_result("works", value)
                if status_callback:
                    status_callback(f"✓ {len(value.get('patterns', []))} winning patterns")
            elif tag[0] == "doesnt":
                result["doesnt"] = value
                if on_result:
                    on_result("doesnt", value)
                if status_callback:
                    status_callback(f"✓ {len(value.get('patterns', []))} anti-patterns")
            elif tag[0] == "rep":
                rep_coaching[tag[1]] = value
                if on_result:
                    on_result(f"rep:{tag[1]}", value)
                if status_callback:
                    status_callback(f"✓ Coaching for {tag[1]}")
            elif tag[0] == "competitors":
                result["competitor_intel"] = {"competitors": value}
                if on_result:
                    on_result("competitors", value)
                if status_callback:
                    status_callback(f"✓ Competitor intel ({len(value)})")

    rep_cards = []
    for rep_id, summary in rep_summaries.items():
        rep_cards.append({
            "rep_id": rep_id, "stats": summary,
            "coaching": rep_coaching.get(rep_id, {
                "summary": "—", "tier": "steady",
                "strength": "—", "weakness": "—", "suggested_action": "—",
            }),
        })
    rep_cards.sort(key=lambda x: x["stats"]["booking_rate"], reverse=True)
    result["rep_cards"] = rep_cards

    if status_callback:
        status_callback(f"✅ Synthesis complete in {time.time() - t0:.1f}s")
    return result