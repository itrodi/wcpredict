"""ops_status writer (spec v4.1 §1.4): the worker records operational truth
each run; /admin/health reads it instead of recomputing."""
from datetime import datetime, timezone

from .db import sb

EXPECTED_FIXTURES = 104  # official 2026 World Cup fixture count


def set_status(key: str, value):
    sb().table("ops_status").upsert(
        {
            "key": key,
            "value": value,
            "updated_at": datetime.now(timezone.utc).isoformat(),
        },
        on_conflict="key",
    ).execute()


def get_status(key: str):
    res = sb().table("ops_status").select("value").eq("key", key).execute()
    return res.data[0]["value"] if res.data else None
