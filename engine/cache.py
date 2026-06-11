"""Worker-side HTTP cache backed by the api_cache table (spec §2: replaces Upstash)."""
from datetime import datetime, timezone

from .db import sb


def _now():
    return datetime.now(timezone.utc)


def get(cache_key: str):
    """Return cached payload if it is still inside its TTL, else None."""
    res = sb().table("api_cache").select("*").eq("cache_key", cache_key).execute()
    if not res.data:
        return None
    row = res.data[0]
    fetched = datetime.fromisoformat(row["fetched_at"].replace("Z", "+00:00"))
    if (_now() - fetched).total_seconds() > row["ttl_seconds"]:
        return None
    return row["payload"]


def set(cache_key: str, payload, ttl_seconds: int):
    sb().table("api_cache").upsert(
        {
            "cache_key": cache_key,
            "payload": payload,
            "fetched_at": _now().isoformat(),
            "ttl_seconds": ttl_seconds,
        },
        on_conflict="cache_key",
    ).execute()
