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
