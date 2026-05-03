"""Field name helpers — maps CSV column names to canonical accessors.

Schema (matches user-provided CSVs):
  restaurants.csv: restaurant_id, name, city, state, cuisine_type, business_type,
                   website_url, num_locations
  calls.csv:       call_id, transcript, call_duration_min, call_outcome, rep_id,
                   rep_tenure, cuisine_type, restaurant_type, num_locations
"""


def get(record, *keys, default=None):
    for k in keys:
        if k in record:
            v = record[k]
            if v is not None and v != "":
                return v
    return default


# ----- call fields -----
def get_call_id(c):       return get(c, "call_id", "id")
def get_restaurant_id(c): return get(c, "restaurant_id", "account_id")
def get_rep_id(c):        return get(c, "rep_id", "user_id")
def get_rep_tenure(c):    return get(c, "rep_tenure", default=None)
def get_timestamp(c):     return get(c, "call_timestamp", "timestamp", "created_at", "call_date")
def get_transcript(c):    return get(c, "transcript", "transcript_text", default="")


def get_duration(c):
    """Duration in seconds. Handles both `duration_seconds` and `call_duration_min`."""
    v = get(c, "duration_seconds", "duration")
    if v:
        try: return int(float(v))
        except: pass
    v = get(c, "call_duration_min", "duration_min")
    if v:
        try: return int(float(v) * 60)
        except: pass
    return 0


def get_outcome(c):
    """Normalize call outcome. Handles 'outcome', 'call_outcome', 'disposition'."""
    v = get(c, "outcome", "call_outcome", "disposition", "result", "status")
    return str(v).lower().strip() if v else None


# ----- restaurant fields -----
def get_name(r):           return get(r, "name", "restaurant_name", default="—")
def get_cuisine(r):        return get(r, "cuisine_type", "cuisine", default="—")
def get_business_type(r):  return get(r, "business_type", "restaurant_type", default="")
def get_website(r):        return get(r, "website_url", "website", "url", default=None)
def get_city(r):           return get(r, "city", default="")
def get_state(r):          return get(r, "state", "region", default="")


def get_locations(r):
    v = get(r, "num_locations", "locations", "location_count", default=1)
    try: return int(float(v)) if v else 1
    except: return 1


# ----- derived / optional fields (not always in CSV — populated from Tavily or fallback) -----
def get_owner(r):
    return get(r, "owner_name", "contact_name", "owner", default="")


def get_rating(r):
    v = get(r, "rating", "google_rating", "stars")
    try: return float(v) if v else None
    except: return None


def get_review_count(r):
    v = get(r, "review_count", "reviews", "n_reviews")
    try: return int(float(v)) if v else None
    except: return None


def get_commission_spend(r):
    """Estimated 3rd-party commission spend per month.

    If the CSV has it, use that. Otherwise estimate from locations + cuisine.
    Pizza/Asian (high-volume delivery) → $6K per location.
    Casual/Fine dining (lower delivery share) → $4K per location.
    Reasonable defaults grounded in industry averages — $3K-$8K/mo for a typical
    independent restaurant doing delivery through DoorDash/Uber Eats."""
    v = get(r, "est_commission_spend", "commission_spend", "monthly_commission")
    try:
        if v:
            return int(float(v))
    except:
        pass
    # Estimate from locations + cuisine
    locs = get_locations(r) or 1
    cuisine = (get_cuisine(r) or "").lower()
    biz = (get(r, "business_type", "type") or "").lower()
    per_loc = 6000  # default
    if "pizza" in cuisine or "asian" in cuisine or "burger" in cuisine:
        per_loc = 6000  # high-volume delivery cuisines
    elif "fine" in biz:
        per_loc = 3500  # lower delivery share at fine dining
    elif "casual" in biz:
        per_loc = 4500
    return per_loc * locs


def get_ordering_setup(r):
    return get(r, "ordering_setup", "current_ordering_setup", default=None)


def get_established_year(r):
    v = get(r, "established_year", "founded", "year_founded")
    try: return int(float(v)) if v else None
    except: return None