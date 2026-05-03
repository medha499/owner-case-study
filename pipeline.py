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
    """Conversational talk track — hardcoded structure with per-restaurant detail
    interpolated. Designed for cold calls to mom-and-pop restaurants where the
    rep doesn't yet know the prospect's current ordering setup.

    Six stages:
      1. Opener — intro Owner.com, ask permission
      2. Discover setup — branch on what the prospect uses today
      3. Pitch — three branches (DoorDash/UberEats user, phone-only, has-site-no-ordering)
      4. Customer-data discovery — surface the data-ownership pain
      5. Objections — five they-say/you-say pairs
      6. Close — calendar-anchored ask

    When language='es', every spoken line is paired with an `_en` translation
    so the bilingual UI shows both — rep speaks Spanish, sees English underneath.
    """
    is_es = language == "es"

    # ── Per-restaurant detail (interpolated into hardcoded scaffolding) ──
    name = F.get_name(restaurant) or "your restaurant"
    cuisine_raw = F.get_cuisine(restaurant) or ""
    cuisine = cuisine_raw.lower() or "restaurant"
    cuisine_es = {"pizza":"pizzería","mexican":"mexicano","asian":"asiático",
                  "burgers":"hamburguesas","bbq":"BBQ","seafood":"mariscos",
                  "italian":"italiano","thai":"tailandés","japanese":"japonés"}.get(cuisine, cuisine)
    state = F.get_state(restaurant) or ""
    locs = F.get_locations(restaurant) or 1
    spend = F.get_commission_spend(restaurant) or 6000
    keep_K = (spend * 12 * 2 // 3) // 1000

    # ════════════════════════════════════════════════════════════════════
    # 1. OPENER — introduce Owner.com, ask permission
    # ════════════════════════════════════════════════════════════════════
    opener_en = (
        f"Hi, this is [your_name] calling from Owner.com. We work with independent "
        f"restaurants like yours across the country to help you take orders "
        f"directly from your customers — without paying DoorDash or Uber Eats "
        f"their commissions. Do you have 60 seconds?"
    )
    opener_es = (
        f"Hola, le habla de Owner.com. Trabajamos con restaurantes independientes "
        f"como el suyo en todo el país para ayudarles a tomar pedidos directamente "
        f"de sus clientes — sin pagar comisiones a DoorDash o Uber Eats. "
        f"¿Tiene 60 segundos?"
    )
    step_opener = {
        "step": 1,
        "label": "Opener" if not is_es else "Apertura",
        "icon": "▶",
        "duration_sec": 15,
        "instruction": ("Pause. Wait for them to say yes."
                        if not is_es else "Pause. Espere a que digan sí."),
        "say": opener_es if is_es else opener_en,
        "say_en": opener_en if is_es else None,
    }

    # ════════════════════════════════════════════════════════════════════
    # 2. DISCOVER SETUP — single question that triggers the branch
    # ════════════════════════════════════════════════════════════════════
    setup_q_en = (
        f"Quick question — when someone wants to place an order from {name} "
        f"today, how does that usually happen? Do they call you, order through "
        f"DoorDash or Uber Eats, or do you have your own website?"
    )
    setup_q_es = (
        f"Pregunta rápida — cuando alguien quiere hacer un pedido de {name} hoy, "
        f"¿cómo sucede normalmente? ¿Le llaman, piden por DoorDash o Uber Eats, "
        f"o tienen su propio sitio web?"
    )
    step_discovery = {
        "step": 2,
        "label": "Discover their setup" if not is_es else "Descubrir su sistema",
        "icon": "?",
        "duration_sec": 30,
        "instruction": ("Ask. Listen. Their answer tells you which pitch branch to use."
                        if not is_es else "Pregunte. Escuche. Su respuesta le dice qué rama usar."),
        "questions": [setup_q_es if is_es else setup_q_en],
        "questions_en": [setup_q_en] if is_es else None,
    }

    # ════════════════════════════════════════════════════════════════════
    # 3. PITCH — branched based on their answer
    # ════════════════════════════════════════════════════════════════════
    pitch_branches_en = [
        {
            "if_they_say": "They use DoorDash, Uber Eats, or another delivery app",
            "you_say": (
                f"Got it. So you're probably paying somewhere around "
                f"${spend:,} a month in commissions — that's about ${(spend*12)//1000},000 "
                f"a year. We replace that with your own ordering page. Same orders, "
                f"no commission, and you finally see who your customers are. Most "
                f"{cuisine} owners we work with see 3 to 5 times their money back "
                f"in the first 90 days."
            ),
        },
        {
            "if_they_say": "They take phone orders only / no online ordering",
            "you_say": (
                f"Makes sense. Here's the thing — your customers want to order from "
                f"their phone at 9pm on a Saturday without calling you. What we do "
                f"is set {name} up with your own ordering page and a branded app, "
                f"so they order direct from you instead of going to DoorDash. Takes "
                f"about 7 days to set up, and most owners see new revenue from "
                f"customers who would've never called in the first place."
            ),
        },
        {
            "if_they_say": "They have a website but no online ordering on it",
            "you_say": (
                f"Perfect — so you've got the brand piece. What's missing is "
                f"actually capturing orders through it. We bolt online ordering "
                f"right onto your existing site, so customers don't have to leave "
                f"your page. You keep 100% of every order, and you own the "
                f"customer relationship — not DoorDash."
            ),
        },
    ]
    pitch_branches_es = [
        {
            "if_they_say": "Usan DoorDash, Uber Eats u otra app de entrega",
            "you_say": (
                f"Entendido. Entonces probablemente está pagando alrededor de "
                f"${spend:,} al mes en comisiones — son unos ${(spend*12)//1000},000 "
                f"al año. Lo reemplazamos con su propia página de pedidos. Los mismos "
                f"pedidos, sin comisión, y por fin ve quiénes son sus clientes. La "
                f"mayoría de dueños de {cuisine_es} con quien trabajamos ven 3 a 5 "
                f"veces su dinero de regreso en los primeros 90 días."
            ),
        },
        {
            "if_they_say": "Solo toman pedidos por teléfono / sin pedidos en línea",
            "you_say": (
                f"Tiene sentido. Mire — sus clientes quieren pedir desde el teléfono "
                f"a las 9pm un sábado sin llamarle. Lo que hacemos es montar a {name} "
                f"con su propia página de pedidos y una app de marca, para que pidan "
                f"directo a usted en vez de ir a DoorDash. Toma unos 7 días instalarlo, "
                f"y la mayoría de dueños ven ingresos nuevos de clientes que no "
                f"hubieran llamado en primer lugar."
            ),
        },
        {
            "if_they_say": "Tienen sitio web pero sin pedidos en línea",
            "you_say": (
                f"Perfecto — entonces ya tiene la marca. Lo que falta es realmente "
                f"capturar pedidos a través del sitio. Atornillamos los pedidos en "
                f"línea directo a su sitio existente, así los clientes no tienen "
                f"que salir de su página. Se queda con el 100% de cada pedido, y "
                f"es dueño de la relación con el cliente — no DoorDash."
            ),
        },
    ]
    if is_es:
        pitch_branches = []
        for es, en in zip(pitch_branches_es, pitch_branches_en):
            pitch_branches.append({
                "if_they_say": es["if_they_say"],
                "if_they_say_en": en["if_they_say"],
                "you_say": es["you_say"],
                "you_say_en": en["you_say"],
            })
    else:
        pitch_branches = pitch_branches_en

    step_pitch = {
        "step": 3,
        "label": "Pitch (pick the branch)" if not is_es else "Presente (elija la rama)",
        "icon": "$",
        "duration_sec": 45,
        "instruction": ("Pick the branch that matches what they just told you."
                        if not is_es else "Elija la rama que coincida con lo que le dijeron."),
        "branches": pitch_branches,
        # Legacy 'say' fallback for any older renderer that expects flat text:
        "say": (pitch_branches_en[0]["you_say"] if not is_es else pitch_branches_es[0]["you_say"]),
    }

    # ════════════════════════════════════════════════════════════════════
    # 4. CUSTOMER-DATA DISCOVERY — surface the data-ownership pain
    # ════════════════════════════════════════════════════════════════════
    customer_q_en = (
        f"And right now, do you have any way to remember who your repeat "
        f"customers are — names, phone numbers, what they usually order?"
    )
    customer_q_es = (
        f"Y ahora mismo, ¿tiene alguna manera de recordar quiénes son sus "
        f"clientes recurrentes — nombres, teléfonos, qué suelen pedir?"
    )
    step_customer_disco = {
        "step": 4,
        "label": "Customer data" if not is_es else "Datos del cliente",
        "icon": "?",
        "duration_sec": 20,
        "instruction": ("They'll usually say no. That's the opening for the close."
                        if not is_es else "Casi siempre dicen no. Esa es la apertura para cerrar."),
        "questions": [customer_q_es if is_es else customer_q_en],
        "questions_en": [customer_q_en] if is_es else None,
    }

    # ════════════════════════════════════════════════════════════════════
    # 5. OBJECTIONS — five they-say / you-say pairs
    # ════════════════════════════════════════════════════════════════════
    raw_objections_en = [
        {
            "they_say": "It sounds expensive.",
            "you_say": (
                f"Compared to what — your DoorDash bill, or general budget? We're "
                f"a fraction of what you're paying in commissions, and you keep "
                f"the customers. Let me show you the math on a 20-minute call."
            ),
        },
        {
            "they_say": "I don't have time to learn a new system.",
            "you_say": (
                f"You don't have to. We migrate your menu for you, set everything "
                f"up, and train your staff. Most owners are up and running in 7 to "
                f"10 days with about an hour of their time total."
            ),
        },
        {
            "they_say": "DoorDash brings me customers I wouldn't otherwise reach.",
            "you_say": (
                f"That's their pitch. But when you log into DoorDash, can you see "
                f"those customers' names and phone numbers? You're paying for "
                f"traffic that walks away — we make that traffic yours."
            ),
        },
        {
            "they_say": "I need to talk to my partner / spouse / family.",
            "you_say": (
                f"Smart — bring them on the call with us. Both of you see the same "
                f"numbers at the same time. What time works for both of you this week?"
            ),
        },
        {
            "they_say": "Just send me some info.",
            "you_say": (
                f"Happy to — but info won't show you what an extra ${keep_K},000 "
                f"a year back in {name} actually looks like. Let me show you on "
                f"a 20-minute call."
            ),
        },
    ]
    raw_objections_es = [
        {
            "they_say": "Suena caro.",
            "you_say": (
                f"¿Comparado con qué — su factura de DoorDash, o presupuesto general? "
                f"Somos una fracción de lo que paga en comisiones, y se queda con "
                f"los clientes. Déjeme mostrarle las matemáticas en 20 minutos."
            ),
        },
        {
            "they_say": "No tengo tiempo para aprender un sistema nuevo.",
            "you_say": (
                f"No tiene que hacerlo. Nosotros migramos su menú, instalamos todo, "
                f"y capacitamos a su personal. La mayoría de dueños está funcionando "
                f"en 7 a 10 días con menos de una hora de su tiempo en total."
            ),
        },
        {
            "they_say": "DoorDash me trae clientes que no alcanzaría.",
            "you_say": (
                f"Esa es su frase de venta. Pero cuando entra a DoorDash, ¿puede "
                f"ver nombres y teléfonos de esos clientes? Está pagando por "
                f"tráfico que se va — nosotros hacemos ese tráfico suyo."
            ),
        },
        {
            "they_say": "Tengo que hablar con mi socio / esposa / familia.",
            "you_say": (
                f"Inteligente — tráigalos a la llamada con nosotros. Los dos ven "
                f"los mismos números al mismo tiempo. ¿Qué hora les sirve a ambos "
                f"esta semana?"
            ),
        },
        {
            "they_say": "Solo mándeme info.",
            "you_say": (
                f"Con gusto — pero la info no le muestra cómo se ven ${keep_K},000 "
                f"extra al año de regreso en {name}. Déjeme mostrárselo en una "
                f"llamada de 20 minutos."
            ),
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
        "step": 5,
        "label": "Objections" if not is_es else "Objeciones",
        "icon": "✕",
        "duration_sec": 60,
        "instruction": ("Read the response. Don't argue. Move to close after."
                        if not is_es else "Lea la respuesta. No discuta. Pase al cierre."),
        "objections": objections,
    }

    # ════════════════════════════════════════════════════════════════════
    # 6. CLOSE — calendar-anchored
    # ════════════════════════════════════════════════════════════════════
    close_en = (
        f"Tuesday at 2pm or Wednesday at 10am for a quick 20-minute walkthrough — "
        f"which works better?"
    )
    close_es = (
        f"¿Martes a las 2pm o miércoles a las 10am para un recorrido rápido de "
        f"20 minutos — cuál le sirve mejor?"
    )
    step_close = {
        "step": 6,
        "label": "Close" if not is_es else "Cierre",
        "icon": "✓",
        "duration_sec": 25,
        "instruction": ("Wait. Let them pick. Don't fill the silence."
                        if not is_es else "Espere. Que elijan. No llene el silencio."),
        "say": close_es if is_es else close_en,
        "say_en": close_en if is_es else None,
    }

    return [step_opener, step_discovery, step_pitch, step_customer_disco, step_objections, step_close]
