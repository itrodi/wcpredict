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


def get_all(path: str, params: dict | None = None, ttl: int = config.STATSAPI_CACHE_TTL_S) -> list:
    """Paginated GET (spec v4.1 §1.1): list endpoints default to per_page=20 with
    meta.total_pages — a single get() silently truncates the ~104-match season.
    Loops page=1..total_pages at per_page=100 and concatenates the items."""
    base = dict(params or {})
    base["per_page"] = 100
    out: list = []
    page = 1
    while True:
        payload = get(path, params={**base, "page": page}, ttl=ttl)
        items = as_list(payload)
        out.extend(items)
        meta = payload.get("meta") if isinstance(payload, dict) else None
        total_pages = int(pick(meta or {}, "total_pages", "totalPages", "last_page", default=1) or 1)
        if page >= total_pages or not items:
            break
        page += 1
    return out


def get_all_try(candidates: list[tuple[str, dict | None]],
                ttl: int = config.STATSAPI_CACHE_TTL_S) -> tuple[list, str | None]:
    """Try each (path, params) candidate; return (items, path) from the first that
    answers 200 with a non-empty list. Logs every miss with the server's status +
    error body, so a moved/renamed endpoint is diagnosable straight from the log
    instead of a bare 404 traceback."""
    for path, params in candidates:
        try:
            items = get_all(path, params=params, ttl=ttl)
        except requests.HTTPError as e:
            code = e.response.status_code if e.response is not None else "?"
            body = ((e.response.text or "")[:160].replace("\n", " ")) if e.response is not None else ""
            print(f"[statsapi] {path} params={params} -> {code} {body}")
            continue
        except StatsApiError as e:
            print(f"[statsapi] {path}: {e}")
            continue
        if items:
            return items, path
        print(f"[statsapi] {path} params={params} -> 200 but no items")
    return [], None


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


# competitions whose names contain "world cup" but are NOT the men's senior
# finals — matching one of these resolves a season whose matches live elsewhere
# (the cause of the /matches 404s seen in production).
_WC_EXCLUDE = (
    "club", "qualif", "women", "u-17", "u17", "u-20", "u20", "u-23", "u23",
    "futsal", "beach", "youth", "olympic",
)


def _is_mens_wc(name: str) -> bool:
    n = name.lower()
    return "world cup" in n and not any(b in n for b in _WC_EXCLUDE)


def find_world_cup_season(force: bool = False) -> tuple[str, str]:
    """Resolve (competition_id, season_id) for the men's senior World Cup; cached
    a day. `force` re-resolves even if cached (used after a matches lookup fails,
    in case a stale/wrong competition got cached)."""
    if not force:
        hit = cache.get("statsapi:wc_season")
        if hit:
            return hit["competition_id"], hit["season_id"]
    comps = get_all("/competitions")
    wc_like = [c for c in comps if _is_mens_wc(str(pick(c, "name", "title", default="")))]
    # prefer an exactly-named 'FIFA World Cup' over qualifiers/variants
    wc = next(
        (c for c in wc_like
         if str(pick(c, "name", "title", default="")).strip().lower() in ("fifa world cup", "world cup")),
        None,
    ) or (wc_like[0] if wc_like else None)
    if not wc:
        raise StatsApiError("men's FIFA World Cup not found in /competitions")
    comp_id = str(pick(wc, "id", "competition_id"))
    comp_name = pick(wc, "name", "title", default="?")
    seasons, _ = get_all_try([
        (f"/competitions/{comp_id}/seasons", None),
        ("/seasons", {"competition_id": comp_id}),
    ])
    season = next(
        (s for s in seasons if pick(s, "is_current", "current") is True),
        None,
    ) or next((s for s in seasons if "2026" in str(pick(s, "year", "name", "label", default=""))), None)
    if not season:
        raise StatsApiError("no current/2026 season for the World Cup")
    season_id = str(pick(season, "id", "season_id"))
    print(f"[statsapi] resolved WC: comp={comp_name!r} id={comp_id} season_id={season_id} "
          f"(from {len(wc_like)} world-cup-like competitions)")
    cache.set("statsapi:wc_season", {"competition_id": comp_id, "season_id": season_id}, 86400)
    return comp_id, season_id
