"""Heuristic signal extraction — pattern matching when no API key set.
Lets the app render end-to-end without LLM costs.
"""

import re


def synthesize_signals(call):
    text = call.get("transcript", "") or ""
    text_lower = text.lower()
    duration = call.get("duration_seconds", 0) or 0

    rep_first_line = ""
    for line in text.split("\n"):
        if line.startswith("Rep:"):
            rep_first_line = line[4:].strip()
            break

    personalization = []
    opener_type = "generic"
    for cuisine in ["margherita", "pepperoni", "wagyu", "brisket", "sushi",
                    "tacos", "burger", "patty"]:
        if cuisine in text_lower:
            personalization.append(f"mentioned dish: {cuisine}")
            opener_type = "personalized"
    if re.search(r"\d+\s*reviews?", text_lower):
        personalization.append("review count referenced")
        opener_type = "personalized"
    if re.search(r"\d\.\d\s*(rating|stars?)", text_lower):
        personalization.append("rating referenced")
        opener_type = "personalized"
    if "the reason i'm calling" in text_lower or "reason for the call" in text_lower:
        if opener_type == "generic":
            opener_type = "reason_statement"

    questions = sum(1 for ln in text.split("\n")
                    if ln.startswith("Rep:") and "?" in ln)
    first_pitch_seconds = min(45 + 10 * questions, duration if duration else 120)

    rep_words = sum(len(ln.split()) for ln in text.split("\n") if ln.startswith("Rep:"))
    other_words = sum(len(ln.split()) for ln in text.split("\n")
                      if not ln.startswith("Rep:") and ln.strip())
    total = rep_words + other_words
    talk_ratio = rep_words / total if total else 0.5

    competitors = []
    for c in ["DoorDash", "Toast", "UberEats", "Uber Eats", "ChowNow",
              "Grubhub", "Square", "Wix"]:
        if c.lower() in text_lower:
            competitors.append(c.replace("UberEats", "Uber Eats"))
    competitors = list(set(competitors))

    objections = []
    for pattern, cat, desc in [
        ("happy with", "status_quo", "happy with current vendor"),
        ("send me an email", "timing", "send-info deflection"),
        ("not the right time", "timing", "timing"),
        ("too expensive", "price", "price concern"),
        ("just signed", "fit", "recently committed elsewhere"),
        ("my customers are loyal", "fit", "customer loyalty argument"),
        ("i tried something similar", "fit", "tried before"),
        ("my wife handles", "authority", "needs spouse/partner"),
        ("locked in", "fit", "contractually locked in"),
    ]:
        if pattern in text_lower:
            handled = pattern not in ("send me an email", "just signed", "locked in")
            objections.append({
                "text": desc, "category": cat, "handled": handled,
                "rebuttal_used": "reframed" if handled else None
            })

    pain_points = []
    for p, label in [("commission", "high commissions"), ("customer data", "customer data ownership"),
                     ("no online ordering", "no online ordering"), ("catering", "catering ops")]:
        if p in text_lower:
            pain_points.append(label)

    next_step = None
    confirmed = False
    specific_time = False
    time_match = re.search(r"(monday|tuesday|wednesday|thursday|friday)\s+(at\s+)?(\d+)", text_lower)
    if time_match:
        next_step = f"demo {time_match.group(1)} at {time_match.group(3)}"
        specific_time = True
        if any(p in text_lower for p in ["yeah", "let's do it", "works", "perfect"]):
            confirmed = True

    outcome = (call.get("outcome") or "").lower()
    if not outcome:
        outcome = "demo_booked" if confirmed and specific_time else "lost"

    return {
        "opener_type": opener_type,
        "opener_quote": rep_first_line[:200],
        "personalization_signals": personalization,
        "questions_asked_count": questions,
        "first_pitch_at_seconds": first_pitch_seconds,
        "rep_talk_ratio_estimate": round(talk_ratio, 2),
        "competitors_mentioned": competitors,
        "objections_raised": objections,
        "pain_points_surfaced": pain_points,
        "next_step_proposed": next_step,
        "next_step_confirmed_verbally": confirmed,
        "specific_demo_time_proposed": specific_time,
        "outcome_proxy": outcome,
    }
