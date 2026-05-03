"""Pipeline functions called by the Streamlit app."""

import os
from concurrent.futures import ThreadPoolExecutor

from agents import (
    parallel_extract_chunked, orchestrate_synthesis_streaming,
    agent_brief_opener, agent_brief_voicemail, agent_account_intel,
    get_client,
)
from synthetic_signals import synthesize_signals as heuristic_signals
import _fields as F


def shape_signal(call, signals_dict):
    outcome_real = F.get_outcome(call)
    canonical = outcome_real or signals_dict.get("outcome_proxy")
    demo_booked = (canonical or "").lower() in ("demo_booked", "won", "booked", "demo booked")
    return {
        "call_id": F.get_call_id(call),
        "restaurant_id": F.get_restaurant_id(call),
        "rep_id": F.get_rep_id(call),
        "rep_tenure": F.get_rep_tenure(call),
        # NEW: cuisine_type / restaurant_type / num_locations are columns directly on calls.csv
        # in the user's schema. Stash them on the signal so segment filtering can use them
        # without needing to look up the restaurant table.
        "cuisine_type": F.get_cuisine(call),
        "restaurant_type": F.get_business_type(call),
        "num_locations": F.get_locations(call),
        "call_timestamp": str(F.get_timestamp(call) or ""),
        "duration_seconds": F.get_duration(call),
        "raw_transcript": F.get_transcript(call),
        "opener_type": signals_dict.get("opener_type"),
        "opener_quote": signals_dict.get("opener_quote"),
        "personalization_signals": signals_dict.get("personalization_signals", []),
        "questions_asked_count": signals_dict.get("questions_asked_count"),
        "first_pitch_at_seconds": signals_dict.get("first_pitch_at_seconds"),
        "rep_talk_ratio_estimate": signals_dict.get("rep_talk_ratio_estimate"),
        "competitors_mentioned": signals_dict.get("competitors_mentioned", []),
        "objections_raised": signals_dict.get("objections_raised", []),
        "pain_points_surfaced": signals_dict.get("pain_points_surfaced", []),
        "next_step_proposed": signals_dict.get("next_step_proposed"),
        "next_step_confirmed_verbally": bool(signals_dict.get("next_step_confirmed_verbally")),
        "specific_demo_time_proposed": bool(signals_dict.get("specific_demo_time_proposed")),
        "outcome_proxy": signals_dict.get("outcome_proxy"),
        "outcome_real": outcome_real,
        "canonical_outcome": canonical,
        "outcome_source": "csv" if outcome_real else "proxy",
        "demo_booked": demo_booked,
        "language_detected": signals_dict.get("language_detected", "en"),
    }


def extract_all(calls, progress_callback=None, on_chunk_done=None):
    """Extract signals from all calls. on_chunk_done(shaped_signals_so_far) per chunk."""
    valid = [c for c in calls if F.get_call_id(c) and F.get_transcript(c)]

    if get_client():
        results_so_far = {}

        def chunk_handler(partial):
            if on_chunk_done:
                shaped = []
                for r in partial:
                    sig = r["signals"]
                    if r["error"]:
                        sig = heuristic_signals({
                            "transcript": F.get_transcript(r["call"]),
                            "duration_seconds": F.get_duration(r["call"]),
                            "outcome": F.get_outcome(r["call"]),
                        })
                    shaped.append(shape_signal(r["call"], sig))
                on_chunk_done(shaped)

        results = parallel_extract_chunked(valid, progress_callback=progress_callback,
                                            on_chunk_done=chunk_handler)
        signals_out = []
        for r in results:
            sig = r["signals"]
            if r["error"]:
                sig = heuristic_signals({
                    "transcript": F.get_transcript(r["call"]),
                    "duration_seconds": F.get_duration(r["call"]),
                    "outcome": F.get_outcome(r["call"]),
                })
            signals_out.append(shape_signal(r["call"], sig))
        return signals_out
    else:
        signals_out = []
        for i, c in enumerate(valid):
            sig = heuristic_signals({
                "transcript": F.get_transcript(c),
                "duration_seconds": F.get_duration(c),
                "outcome": F.get_outcome(c),
            })
            signals_out.append(shape_signal(c, sig))
            if progress_callback:
                progress_callback(i + 1, len(valid), 1)
        if on_chunk_done:
            on_chunk_done(signals_out)
        return signals_out


def run_synthesis_streaming(signals, restaurants_lookup, on_result=None,
                             status_callback=None, language="en"):
    return orchestrate_synthesis_streaming(
        signals, restaurants_lookup, on_result=on_result,
        status_callback=status_callback, language=language
    )


def _fallback_intel(restaurant, language="en"):
    """Generate cuisine_intel + platform_intel from CSV-derived defaults
    (used when TAVILY_API_KEY is missing). Demo always renders fully."""
    cuisine = (restaurant.get("cuisine") or "").lower()
    rating = restaurant.get("rating") or 4.5
    review_count = restaurant.get("review_count") or 100
    spend = restaurant.get("est_commission_spend") or 6000
    setup = restaurant.get("ordering_setup") or ""

    cuisine_db = {
        "pizza": {
            "review_themes_en": ["consistency of crust", "delivery speed complaints", "value for size"],
            "review_themes_es": ["consistencia de la masa", "quejas sobre tiempo de entrega", "buena relación calidad-precio"],
            "top_menu_en": "Margherita / pepperoni — most-mentioned in reviews",
            "top_menu_es": "Margherita / pepperoni — más mencionada en reseñas",
            "family": "Y", "family_note_en": "Casual family dining, common for groups of 4+",
            "family_note_es": "Restaurante familiar, común para grupos de 4+",
            "price_range": "$$ ($15-25 avg ticket)",
            "pains_en": ["DoorDash takes 25-30% per pizza", "phone orders go to noisy POS during rush",
                         "no first-party loyalty data"],
            "pains_es": ["DoorDash cobra 25-30% por pizza", "pedidos por teléfono van a POS ruidoso en hora pico",
                         "sin datos de lealtad propios"],
        },
        "mexican": {
            "review_themes_en": ["authentic flavors", "wait times on weekends", "margarita value"],
            "review_themes_es": ["sabores auténticos", "tiempos de espera fines de semana", "valor de margaritas"],
            "top_menu_en": "Tacos al pastor / mole / fajitas — top-mentioned",
            "top_menu_es": "Tacos al pastor / mole / fajitas — los más mencionados",
            "family": "Y", "family_note_en": "Family-style platters, weekend brunch crowd",
            "family_note_es": "Platos familiares, multitud de brunch en fin de semana",
            "price_range": "$$ ($18-30 avg ticket)",
            "pains_en": ["Catering inquiries lost on voicemail", "DoorDash menu doesn't show modifiers properly",
                         "high check averages but low repeat-rate visibility"],
            "pains_es": ["Pedidos de catering se pierden en buzón de voz", "DoorDash no muestra modificadores correctamente",
                         "checks altos pero baja visibilidad de clientes recurrentes"],
        },
        "asian": {
            "review_themes_en": ["fish freshness", "pricing transparency", "lunch specials popularity"],
            "review_themes_es": ["frescura del pescado", "transparencia de precios", "popularidad de menús de almuerzo"],
            "top_menu_en": "Chef's omakase / signature roll — high engagement",
            "top_menu_es": "Omakase del chef / roll especial — alta interacción",
            "family": "N", "family_note_en": "Date-night focused, smaller party sizes",
            "family_note_es": "Enfocado en cenas de pareja, mesas pequeñas",
            "price_range": "$$$ ($35-60 avg ticket)",
            "pains_en": ["Last-minute cancellations on prepaid omakase", "Yelp reservations leak data to competitors",
                         "delivery damages premium presentation"],
            "pains_es": ["Cancelaciones de último momento en omakase prepagado", "Yelp filtra datos a la competencia",
                         "entregas dañan la presentación premium"],
        },
    }
    default = {
        "review_themes_en": ["service quality", "value for price", "ambiance"],
        "review_themes_es": ["calidad del servicio", "buena relación calidad-precio", "ambiente"],
        "top_menu_en": "Signature dish — most mentioned in recent reviews",
        "top_menu_es": "Plato especial — más mencionado en reseñas recientes",
        "family": "Y", "family_note_en": "Casual dining, mixed party sizes",
        "family_note_es": "Restaurante casual, tamaños de mesa variados",
        "price_range": "$$ ($20-35 avg ticket)",
        "pains_en": ["3rd-party commissions eating into margin", "no direct customer data", "no marketing automation"],
        "pains_es": ["Comisiones de terceros reducen el margen", "sin datos directos del cliente", "sin automatización de marketing"],
    }
    cdb = cuisine_db.get(cuisine, default)
    is_es = language == "es"

    setup_lower = setup.lower()
    platforms = []
    if "doordash" in setup_lower or "3rd-party" in setup_lower or "third" in setup_lower:
        platforms.append("DoorDash")
    if "ubereats" in setup_lower or "uber" in setup_lower:
        platforms.append("Uber Eats")
    if "grubhub" in setup_lower:
        platforms.append("GrubHub")
    if "toast" in setup_lower:
        platforms.append("Toast")
    if rating:
        platforms.append("Yelp")
    if not platforms:
        platforms = ["DoorDash", "Yelp"]

    annual_loss = spend * 12
    annual_loss_str = f"~${annual_loss//1000}K/year"

    rebuttal_en = (f"DoorDash brings volume but keeps your customer data. At ${spend:,}/mo "
                   f"in commissions, you're paying for orders from people who already knew about you. "
                   f"Owner.com lets you take direct orders, keep the margin, and own the relationship.")
    rebuttal_es = (f"DoorDash trae volumen pero se queda con sus datos de clientes. A ${spend:,}/mes "
                   f"en comisiones, está pagando por pedidos de gente que ya lo conocía. "
                   f"Owner.com le permite tomar pedidos directos, conservar el margen y ser dueño de la relación.")

    intel_en = (f"{restaurant.get('name')} has a {rating}-star rating across {review_count} reviews "
                f"and is currently using {setup or 'mixed channels'}. "
                f"Estimated 3rd-party spend is ${spend:,}/mo.")
    intel_es = (f"{restaurant.get('name')} tiene {rating} estrellas con {review_count} reseñas "
                f"y actualmente usa {setup or 'canales mixtos'}. "
                f"Gasto estimado a terceros es ${spend:,}/mes.")

    talking_points_en = [
        f"Your {rating}-star rating means brand equity — DoorDash uses your name to drive their app, not yours.",
        f"At ${spend:,}/mo in commissions, that's ~${spend*12//1000}K/year going to a channel that doesn't return customer data.",
        f"Most {cuisine or 'restaurant'} owners see 3-5x ROI within 90 days of switching to direct ordering.",
    ]
    talking_points_es = [
        f"Sus {rating} estrellas son equity de marca — DoorDash usa su nombre para su app, no la suya.",
        f"A ${spend:,}/mes en comisiones, son ~${spend*12//1000}K/año a un canal que no devuelve datos.",
        f"La mayoría de dueños de {cuisine or 'restaurante'} ven 3-5x ROI en 90 días con pedidos directos.",
    ]

    return {
        "intel": intel_es if is_es else intel_en,
        "talking_points": talking_points_es if is_es else talking_points_en,
        "sources": [],
        "cuisine_intel": {
            "cuisine_type": restaurant.get("cuisine"),
            "review_themes": cdb["review_themes_es"] if is_es else cdb["review_themes_en"],
            "top_menu_item": cdb["top_menu_es"] if is_es else cdb["top_menu_en"],
            "family_style": f'{cdb["family"]} — ' + (cdb["family_note_es"] if is_es else cdb["family_note_en"]),
            "price_range": cdb["price_range"],
            "recent_news": ("Sin noticias recientes (sin acceso a Tavily)"
                            if is_es else "No recent news (no Tavily access)"),
            "pain_points_for_segment": cdb["pains_es"] if is_es else cdb["pains_en"],
        },
        "platform_intel": {
            "platforms_detected": platforms,
            "estimated_commission_pct": "25-30% typical",
            "estimated_monthly_loss": f"${spend:,}/mo · {annual_loss_str}",
            "rebuttal_angle": rebuttal_es if is_es else rebuttal_en,
        },
    }


def build_brief(restaurant, signals, segment_what_works, language="en"):
    """Build per-restaurant brief. Language affects opener/voicemail/talking points."""
    has_anthropic = bool(get_client())
    has_tavily = bool(os.getenv("TAVILY_API_KEY"))

    formatted = {
        "restaurant_id": F.get(restaurant, "restaurant_id", "id"),
        "name": F.get_name(restaurant),
        "cuisine": F.get_cuisine(restaurant),
        "business_type": F.get_business_type(restaurant),
        "website_url": F.get_website(restaurant),
        "city": F.get_city(restaurant),
        "state": F.get_state(restaurant),
        "locations": F.get_locations(restaurant),
        "rating": F.get_rating(restaurant),
        "review_count": F.get_review_count(restaurant),
        "est_commission_spend": F.get_commission_spend(restaurant),
        "ordering_setup": F.get_ordering_setup(restaurant),
        "established_year": F.get_established_year(restaurant),
        "owner_name": F.get_owner(restaurant),
    }

    def fb_opener_en():
        nm = (F.get_owner(restaurant) or "there").split()[0]
        rating = F.get_rating(restaurant) or "great"
        cuisine = F.get_cuisine(restaurant).lower()
        spend = (F.get_commission_spend(restaurant) or 6000) // 1000
        return (f"Hi {nm}, this is calling from Owner.com. Quick reason for the call — "
                f"{F.get_name(restaurant)} has a {rating}-star rating, and most {cuisine} "
                f"spots in {F.get_state(restaurant)} are paying around ${spend},000 a month "
                f"to third-party apps. Mind if I ask one question?")

    def fb_opener_es():
        nm = (F.get_owner(restaurant) or "amigo").split()[0]
        rating = F.get_rating(restaurant) or "excelente"
        cuisine = F.get_cuisine(restaurant).lower()
        spend = (F.get_commission_spend(restaurant) or 6000) // 1000
        return (f"Hola {nm}, le habla de Owner.com. La razón de mi llamada — "
                f"{F.get_name(restaurant)} tiene una calificación de {rating} estrellas, "
                f"y la mayoría de los {cuisine} en {F.get_state(restaurant)} están pagando "
                f"alrededor de ${spend},000 al mes a apps de terceros. ¿Le importa si le pregunto algo?")

    def fb_voicemail_en():
        nm = (F.get_owner(restaurant) or "there").split()[0]
        return (f"Hi {nm}, calling from Owner.com. {F.get_name(restaurant)} is highly rated — "
                f"most owners in your spot pay thousands a month in commissions. "
                f"Worth a 12-minute call? I'll try back tomorrow. Thanks.")

    def fb_voicemail_es():
        nm = (F.get_owner(restaurant) or "amigo").split()[0]
        return (f"Hola {nm}, le llamo de Owner.com. {F.get_name(restaurant)} está muy bien calificado — "
                f"la mayoría de los dueños en su posición pagan miles al mes en comisiones. "
                f"¿Vale la pena una llamada de 12 minutos? Le llamo mañana. Gracias.")

    fb_opener = fb_opener_es if language == "es" else fb_opener_en
    fb_voicemail = fb_voicemail_es if language == "es" else fb_voicemail_en

    def get_opener():
        if not has_anthropic:
            return fb_opener()
        try:
            return agent_brief_opener(formatted, segment_what_works, language=language)
        except:
            return fb_opener()

    def get_voicemail():
        if not has_anthropic:
            return fb_voicemail()
        try:
            return agent_brief_voicemail(formatted, language=language)
        except:
            return fb_voicemail()

    def get_intel():
        # IMPORTANT: skip Tavily here — the script card only needs platform_intel
        # (estimated_monthly_loss + platforms_detected), which the fallback computes
        # from CSV columns instantly. Saves 2-5s per brief load.
        # If you want enriched cuisine_intel (reviews, news), it can be loaded
        # lazily when "More context" is expanded — separate endpoint.
        return _fallback_intel(formatted, language=language)

    with ThreadPoolExecutor(max_workers=3) as pool:
        f_opener = pool.submit(get_opener)
        f_voicemail = pool.submit(get_voicemail)
        f_intel = pool.submit(get_intel)
        opener = f_opener.result()
        voicemail = f_voicemail.result()
        account_intel = f_intel.result()

    rid = F.get(restaurant, "restaurant_id", "id")
    touch_history = sorted(
        [{"call_id": s["call_id"], "rep_id": s["rep_id"],
          "timestamp": s.get("call_timestamp"),
          "outcome": s.get("canonical_outcome"),
          "opener": s.get("opener_quote"),
          "language": s.get("language_detected", "en")}
         for s in signals if s.get("restaurant_id") == rid],
        key=lambda x: x["timestamp"] or "", reverse=True
    )

    cuisine = (F.get_cuisine(restaurant) or "?").lower()
    state = F.get_state(restaurant) or "?"
    size = "single-loc" if F.get_locations(restaurant) == 1 else "multi-loc"

    # Build the talk track from existing brief data — no extra LLM call needed.
    # This gives the rep a step-by-step script: opener → discovery → pitch → objections → close.
    talk_track = _build_talk_track(restaurant, opener, voicemail, account_intel,
                                     segment_what_works, language=language)

    return {
        "restaurant": formatted,
        "segment": f"{cuisine} · {state} · {size}",
        "attempt": len(touch_history) + 1,
        "opener": opener,
        "voicemail": voicemail,
        "talk_track": talk_track,
        "what_works_in_segment": segment_what_works,
        "account_intel": account_intel,
        "touch_history": touch_history,
        "language": language,
    }


def _build_talk_track(restaurant, opener, voicemail, account_intel, segment_works, language="en"):
    """Per-client talk track. Pulls every available signal and weaves it into spoken lines.

    Customization sources:
    - Restaurant: name, cuisine, city, state, num_locations, business_type
    - Account intel (Tavily-enriched when available): top_menu_item, review_themes,
      price_range, family_style, recent_news
    - Computed: estimated commission spend (from cuisine + locations), primary platform,
      annual loss, "amount you'd keep" estimate

    When language == 'es', every spoken line is returned with an `_en` translation
    so the bilingual UI can show both. The rep speaks Spanish, but sees the English
    translation right below so they know what they're saying.
    """
    is_es = language == "es"

    # ---- Pull every available bit of context for this client ----
    ci = (account_intel or {}).get("cuisine_intel") or {}
    pi = (account_intel or {}).get("platform_intel") or {}
    cuisine_raw = F.get_cuisine(restaurant) or ""
    cuisine = cuisine_raw.lower() or "restaurant"
    cuisine_es = {"pizza":"pizzería","mexican":"mexicano","asian":"asiático",
                  "burgers":"hamburguesas","bbq":"BBQ","seafood":"mariscos",
                  "italian":"italiano","thai":"tailandés","japanese":"japonés"}.get(cuisine, cuisine)
    name = F.get_name(restaurant) or "your restaurant"
    city = F.get(restaurant, "city") or ""
    state = F.get_state(restaurant) or ""
    locs = F.get_locations(restaurant) or 1
    biz = (F.get(restaurant, "business_type", "type") or "").lower()
    spend = F.get_commission_spend(restaurant) or 6000
    annual_K = (spend * 12) // 1000
    keep_K = (spend * 12 * 2 // 3) // 1000
    platforms = pi.get("platforms_detected", [])
    primary_platform = platforms[0] if platforms else "DoorDash"

    # Top menu item — strip generic placeholder text from fallback
    top_item_raw = (ci.get("top_menu_item") or "").strip()
    if top_item_raw and "Signature dish" not in top_item_raw and "—" not in top_item_raw[:8]:
        top_item = top_item_raw.split("/")[0].split(",")[0].strip()  # take first item if list
    else:
        top_item = ""  # signal: no real data, fall back to generic phrasing

    # Review themes — the things customers love about this place
    review_themes = ci.get("review_themes") or []
    primary_theme = review_themes[0] if review_themes else ""

    # Multi-location framing: address the right pain
    location_phrase_en = f"all {locs} locations" if locs > 1 else "your shop"
    location_phrase_es = f"sus {locs} ubicaciones" if locs > 1 else "su local"

    # Price range hint — informs how aggressive the dollar pitch should be
    price_range = (ci.get("price_range") or "").strip()

    # Recent news for proof-point or hook variation
    recent_news = (ci.get("recent_news") or "").strip()
    has_news = recent_news and "no recent news" not in recent_news.lower() and "no tavily" not in recent_news.lower()

    # ---------------- 1. OPENER (the hook) ----------------
    import re
    opener_clean = re.sub(r"<[^>]+>", "", opener or "").strip()
    if (opener_clean.startswith('"') and opener_clean.endswith('"')) or \
       (opener_clean.startswith('"') and opener_clean.endswith('"')):
        opener_clean = opener_clean[1:-1].strip()

    # If we have a top item or review theme, build a more personalized opener
    # to override the generic AI/fallback opener
    custom_opener_en = None
    custom_opener_es = None
    if top_item:
        custom_opener_en = (
            f"Hi, this is calling from Owner.com. Quick reason for the call — "
            f"I noticed {top_item} is one of the most-praised items in {name}'s reviews. "
            f"Most {cuisine} spots like yours in {state} are paying around ${spend:,} a month "
            f"to apps like {primary_platform}. Mind if I ask one question?"
        )
        custom_opener_es = (
            f"Hola, le habla de Owner.com. La razón rápida — "
            f"noté que {top_item} es uno de los platos más elogiados en las reseñas de {name}. "
            f"La mayoría de los lugares {cuisine_es} como el suyo en {state} están pagando "
            f"alrededor de ${spend:,} al mes a apps como {primary_platform}. "
            f"¿Le importa si le pregunto algo?"
        )
    elif primary_theme:
        custom_opener_en = (
            f"Hi, calling from Owner.com. Quick reason — your customers rave about "
            f"{primary_theme} in {name}'s reviews, but most {cuisine} spots in {state} "
            f"are losing about ${spend:,} a month to delivery apps. Got 60 seconds?"
        )
        custom_opener_es = (
            f"Hola, le llamo de Owner.com. La razón rápida — sus clientes elogian "
            f"{primary_theme} en las reseñas de {name}, pero la mayoría de los {cuisine_es} "
            f"en {state} pierden unos ${spend:,} al mes a apps de entrega. ¿60 segundos?"
        )

    if is_es:
        opener_en = custom_opener_en or opener_clean
        opener_say = custom_opener_es or opener_clean
    else:
        opener_say = custom_opener_en or opener_clean
        opener_en = None

    step_opener = {
        "step": 1,
        "label": "Opener" if not is_es else "Apertura",
        "icon": "▶",
        "duration_sec": 15,
        "instruction": ("Pause after the question. Don't fill silence."
                        if not is_es else "Pause después de la pregunta. No llene el silencio."),
        "say": opener_say,
        "say_en": opener_en,  # English translation when in ES mode
    }

    # ---------------- 2. ASK ONE QUESTION ----------------
    # Only one question shown. Pick the most pointed one given what we know.
    if top_item:
        q_en = (f"What % of {top_item} orders are coming through {primary_platform} "
                f"versus straight from your own customers?")
        q_es = (f"¿Qué % de los pedidos de {top_item} llegan por {primary_platform} "
                f"versus directo de sus propios clientes?")
    elif locs > 1:
        q_en = (f"Across your {locs} locations, what % of last week's orders came "
                f"through {primary_platform} or Uber Eats?")
        q_es = (f"En sus {locs} ubicaciones, ¿qué % de los pedidos de la semana pasada "
                f"llegaron por {primary_platform} o Uber Eats?")
    else:
        q_en = f"What % of last week's orders came through {primary_platform} or Uber Eats?"
        q_es = f"¿Qué % de los pedidos de la semana pasada llegaron por {primary_platform} o Uber Eats?"

    discovery_say = q_es if is_es else q_en
    discovery_en = q_en if is_es else None

    step_discovery = {
        "step": 2,
        "label": "Discovery" if not is_es else "Descubrimiento",
        "icon": "?",
        "duration_sec": 30,
        "instruction": ("Ask. Shut up. Wait for their number."
                        if not is_es else "Pregunte. Cállese. Espere su número."),
        "questions": [discovery_say],
        "questions_en": [discovery_en] if discovery_en else None,
    }

    # ---------------- 3. PITCH ----------------
    # Lead with their dollars, mention what makes them special, then the proof.
    customer_hook_en = ""
    customer_hook_es = ""
    if top_item:
        customer_hook_en = f"You've already got people loving {top_item} — "
        customer_hook_es = f"Ya tiene gente que ama {top_item} — "
    elif primary_theme:
        customer_hook_en = f"Your customers already love your {primary_theme} — "
        customer_hook_es = f"Sus clientes ya aman su {primary_theme} — "

    cuisine_owner_en = f"{cuisine} owners" if cuisine != "restaurant" else "restaurant owners"
    cuisine_owner_es = f"dueños de {cuisine_es}" if cuisine_es != "restaurant" else "dueños de restaurantes"

    pitch_en = (
        f"Here's the thing — at ${spend:,} a month to {primary_platform}, "
        f"that's about ${annual_K},000 a year leaving {name}. "
        f"{customer_hook_en}we replace those app fees with direct ordering on "
        f"your own website. Same orders, no commission, and you finally see "
        f"who your customers actually are. Most {cuisine_owner_en} we work with "
        f"see 3 to 5 times their money back within 90 days."
    )
    pitch_es = (
        f"Mire — a ${spend:,} al mes a {primary_platform}, son unos ${annual_K},000 "
        f"al año saliendo de {name}. "
        f"{customer_hook_es}reemplazamos esas comisiones con pedidos directos en su "
        f"propio sitio web. Los mismos pedidos, sin comisión, y por fin ve quiénes "
        f"son sus clientes. La mayoría de {cuisine_owner_es} con quien trabajamos "
        f"ven 3 a 5 veces su dinero de regreso en 90 días."
    )
    pitch_say = pitch_es if is_es else pitch_en
    pitch_en_for_ui = pitch_en if is_es else None

    step_pitch = {
        "step": 3,
        "label": "The pitch" if not is_es else "La presentación",
        "icon": "$",
        "duration_sec": 45,
        "instruction": ("Read slowly. Pause after the dollar number."
                        if not is_es else "Léalo despacio. Pause después del monto."),
        "say": pitch_say,
        "say_en": pitch_en_for_ui,
    }

    # ---------------- 4. OBJECTIONS ----------------
    # Customized per restaurant. References their actual platform, spend, and item.
    raw_objections_en = [
        {
            "they_say": "It sounds expensive.",
            "you_say": (f"Compared to what — your ${spend:,} a month {primary_platform} bill, "
                        f"or general budget? We're about a third of that, and you keep the customers."),
        },
        {
            "they_say": f"But {primary_platform} brings me customers.",
            "you_say": (f"When you log into {primary_platform}, can you see those customers' "
                        f"names and phone numbers? You're paying for traffic that walks away."
                        + (f" Even the people who came back for {top_item}." if top_item else "")),
        },
        {
            "they_say": "I'm too busy right now.",
            "you_say": (f"Every month waiting is another ${spend:,} gone. We migrate "
                        f"{'all your menus across ' + str(locs) + ' locations' if locs > 1 else 'your menu'} "
                        f"for you, run both systems in parallel — you're live in 7 to 10 days."),
        },
        {
            "they_say": "I need to talk to my partner.",
            "you_say": ("Smart — bring them on the call with us. Both of you see the same "
                        "numbers at the same time. What time works for both of you this week?"),
        },
        {
            "they_say": "Just send me some info.",
            "you_say": (f"Happy to — but info won't show you what ${keep_K},000 back in your "
                        f"business actually looks like. Let me show you on a 20-minute call."),
        },
    ]
    raw_objections_es = [
        {
            "they_say": "Suena caro.",
            "you_say": (f"¿Comparado con qué — su factura de ${spend:,} al mes a {primary_platform}, "
                        f"o presupuesto general? Somos como un tercio de eso, y se queda con los clientes."),
        },
        {
            "they_say": f"Pero {primary_platform} me trae clientes.",
            "you_say": (f"Cuando entra a {primary_platform}, ¿puede ver los nombres y teléfonos "
                        f"de esos clientes? Está pagando por tráfico que se va."
                        + (f" Hasta la gente que volvió por {top_item}." if top_item else "")),
        },
        {
            "they_say": "Estoy muy ocupado ahora.",
            "you_say": (f"Cada mes que espera son otros ${spend:,} perdidos. Nosotros migramos "
                        f"{'todos los menús de sus ' + str(locs) + ' ubicaciones' if locs > 1 else 'su menú'}, "
                        f"corremos ambos sistemas en paralelo — está en vivo en 7 a 10 días."),
        },
        {
            "they_say": "Tengo que hablar con mi socio.",
            "you_say": ("Inteligente — tráigalo a la llamada con nosotros. Los dos ven los mismos "
                        "números a la vez. ¿Qué hora les sirve a ambos esta semana?"),
        },
        {
            "they_say": "Solo mándeme info.",
            "you_say": (f"Con gusto — pero la info no le muestra lo que ${keep_K},000 de regreso en su "
                        f"negocio se ve. Déjeme mostrárselo en una llamada de 20 minutos."),
        },
    ]
    if is_es:
        objections = []
        for es, en in zip(raw_objections_es, raw_objections_en):
            objections.append({
                "they_say": es["they_say"],
                "they_say_en": en["they_say"],
                "you_say": es["you_say"],
                "you_say_en": en["you_say"],
            })
    else:
        objections = raw_objections_en

    step_objections = {
        "step": 4,
        "label": "Objections" if not is_es else "Objeciones",
        "icon": "✕",
        "duration_sec": 60,
        "instruction": ("Read the response. Don't argue. Move to close after."
                        if not is_es else "Lea la respuesta. No discuta. Pase al cierre."),
        "objections": objections,
    }

    # ---------------- 5. CLOSE ----------------
    close_en = (f"Tuesday at 2pm or Wednesday at 10am for a 20-minute screen-share — "
                f"which works better?")
    close_es = (f"¿Martes a las 2pm o miércoles a las 10am para una llamada de 20 minutos — "
                f"cuál le sirve mejor?")
    step_close = {
        "step": 5,
        "label": "The close" if not is_es else "El cierre",
        "icon": "✓",
        "duration_sec": 25,
        "instruction": ("Wait. Let them pick. Don't fill the silence."
                        if not is_es else "Espere. Que elijan. No llene el silencio."),
        "say": close_es if is_es else close_en,
        "say_en": close_en if is_es else None,
    }

    return [step_opener, step_discovery, step_pitch, step_objections, step_close]