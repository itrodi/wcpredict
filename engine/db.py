"""Supabase client (service role — bypasses RLS; worker only)."""
from supabase import create_client

from . import config

_client = None


def sb():
    global _client
    if _client is None:
        if not config.SUPABASE_URL or not config.SUPABASE_SERVICE_ROLE_KEY:
            raise RuntimeError("SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY not set")
        _client = create_client(config.SUPABASE_URL, config.SUPABASE_SERVICE_ROLE_KEY)
    return _client


def chunked(rows, size=500):
    for i in range(0, len(rows), size):
        yield rows[i : i + size]


def fetch_all(make_query, page_size=1000):
    """Drain a PostgREST query past the server's 1000-row default cap.

    `make_query` must build a FRESH query each call (builders are mutable);
    e.g. fetch_all(lambda: sb().table("shots").select("*")).
    """
    out, offset = [], 0
    while True:
        batch = make_query().range(offset, offset + page_size - 1).execute().data
        out.extend(batch)
        if len(batch) < page_size:
            return out
        offset += page_size
