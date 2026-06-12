"""TheStatsAPI HTTP client (Pipeline B).

Stats-only plan: odds endpoints are NOT available on our key and must never be
called in production (spec v4 §0). Budget 100k/month, 120/min -> calls spaced
>=0.5s, cached in api_cache. Field names are picked defensively because the
exact payload shapes are confirmed by verify_statsapi.py during the trial —
adjust the FIELD_* maps below if the verification report shows different keys.
"""
import time

import requests

from . import cache, config

_last_call = 0.0


class StatsApiError(RuntimeError):
    pass


def get(path: str, params: dict | None = None, ttl: int = config.STATSAPI_CACHE_TTL_S):
    """GET with bearer auth, api_cache and 120/min spacing."""
    global _last_call
    if not config.STATSAPI_KEY:
        raise StatsApiError("STATSAPI_KEY not set")
    key = f"statsapi:{path}:{sorted((params or {}).items())}"
    cached = cache.get(key)
    if cached is not None:
        return cached
    wait = config.STATSAPI_MIN_CALL_SPACING_S - (time.monotonic() - _last_call)
    if wait > 0:
        time.sleep(wait)
    r = requests.get(
        f"{config.STATSAPI_BASE}{path}",
        params=params,
        headers={"Authorization": f"Bearer {config.STATSAPI_KEY}"},
        timeout=30,
    )
    _last_call = time.monotonic()
    r.raise_for_status()
    payload = r.json()
    cache.set(key, payload, ttl)
    return payload


def pick(d: dict, *keys, default=None):
    """First present key wins — tolerates vendor field-name variations."""
    for k in keys:
        if isinstance(d, dict) and d.get(k) is not None:
            return d[k]
    return default


def as_list(payload, *wrapper_keys):
    """Vendors wrap lists in {'data': [...]} / {'matches': [...]} or return bare lists."""
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict):
        for k in (*wrapper_keys, "data", "results", "items"):
            if isinstance(payload.get(k), list):
                return payload[k]
    return []


def find_world_cup_season() -> tuple[str, str]:
    """Resolve (competition_id, season_id) for the current World Cup; cached for a day."""
    hit = cache.get("statsapi:wc_season")
    if hit:
        return hit["competition_id"], hit["season_id"]
    comps = as_list(get("/competitions"), "competitions")
    wc = next(
        (
            c
            for c in comps
            if config.STATSAPI_WC_NAME.lower() in str(pick(c, "name", "title", default="")).lower()
        ),
        None,
    )
    if not wc:
        raise StatsApiError("FIFA World Cup not found in /competitions")
    comp_id = str(pick(wc, "id", "competition_id"))
    seasons = as_list(get(f"/competitions/{comp_id}/seasons"), "seasons")
    season = next(
        (s for s in seasons if pick(s, "is_current", "current") is True),
        None,
    ) or next((s for s in seasons if "2026" in str(pick(s, "year", "name", "label", default=""))), None)
    if not season:
        raise StatsApiError("no current/2026 season for the World Cup")
    season_id = str(pick(season, "id", "season_id"))
    cache.set("statsapi:wc_season", {"competition_id": comp_id, "season_id": season_id}, 86400)
    return comp_id, season_id
