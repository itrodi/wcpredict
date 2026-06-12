"""Cross-vendor team-name aliases (spec v4 §4).

All vendors' names funnel through slugify() + this dict to our canonical slugs.
Cross-vendor JOINS never happen on names — only on xmap_teams / xmap_fixtures;
this dict exists solely to RESOLVE a vendor name to a team the first time we
see it. Extend here when a vendor invents a new spelling; never auto-create
near-duplicate teams.
"""
import re
import unicodedata

# vendor slug -> canonical slug (canonical = supabase/seed.sql slugs)
SLUG_ALIASES = {
    # football-data.org / The Odds API / TheStatsAPI spellings
    "usa": "united-states",
    "united-states-of-america": "united-states",
    "south-korea": "korea-republic",
    "korea": "korea-republic",
    "ivory-coast": "cote-divoire",
    "cote-d-ivoire": "cote-divoire",   # plain slugification of "Côte d'Ivoire"
    "cabo-verde": "cape-verde",
    "ir-iran": "iran",
    "iran-islamic-republic": "iran",
    "turkiye": "turkey",
    "congo-dr": "dr-congo",
    "dr-congo": "dr-congo",
    "democratic-republic-of-the-congo": "dr-congo",
    "congo-kinshasa": "dr-congo",
    "czechia": "czech-republic",
    "bosnia": "bosnia-and-herzegovina",
    "uae": "united-arab-emirates",
}


def slugify(name: str) -> str:
    s = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return SLUG_ALIASES.get(s, s)
