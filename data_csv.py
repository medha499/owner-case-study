"""CSV loading. Plain pandas, simple in-memory cache."""

import os
import pandas as pd
from dotenv import load_dotenv

load_dotenv()

CSV_CALLS_PATH = os.getenv("CSV_CALLS_PATH", "data/calls.csv")
CSV_RESTAURANTS_PATH = os.getenv("CSV_RESTAURANTS_PATH", "data/restaurants.csv")

_cache = {}


def _normalize(records):
    return [
        {str(k).lower().strip(): (None if pd.isna(v) else v) for k, v in r.items()}
        for r in records
    ]


def load_calls():
    if "calls" not in _cache:
        df = pd.read_csv(CSV_CALLS_PATH)
        _cache["calls"] = _normalize(df.to_dict(orient="records"))
    return _cache["calls"]


def load_restaurants():
    if "restaurants" not in _cache:
        df = pd.read_csv(CSV_RESTAURANTS_PATH)
        _cache["restaurants"] = _normalize(df.to_dict(orient="records"))
    return _cache["restaurants"]


def load_restaurants_by_id():
    if "restaurants_by_id" not in _cache:
        from _fields import get
        _cache["restaurants_by_id"] = {
            get(r, "restaurant_id", "id"): r for r in load_restaurants()
        }
    return _cache["restaurants_by_id"]


def invalidate():
    _cache.clear()
