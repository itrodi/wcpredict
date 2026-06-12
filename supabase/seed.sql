-- Seed: qualified 2026 World Cup teams with initial Elo (approx. from eloratings.net, late 2025).
-- group_code is left NULL on purpose: the first `ingest_fd.py` run fills groups and creates any
-- team this seed is missing (e.g. March-2026 playoff winners) with a default Elo of 1600.
-- Elo values are starting points only — `ratings.py` refreshes them from finished results every run.

insert into teams (slug, name, confederation, elo) values
  -- Hosts
  ('united-states', 'United States', 'CONCACAF', 1790),
  ('canada',        'Canada',        'CONCACAF', 1770),
  ('mexico',        'Mexico',        'CONCACAF', 1810),
  -- UEFA
  ('spain',        'Spain',        'UEFA', 2185),
  ('france',       'France',       'UEFA', 2075),
  ('england',      'England',      'UEFA', 2090),
  ('portugal',     'Portugal',     'UEFA', 2015),
  ('netherlands',  'Netherlands',  'UEFA', 2005),
  ('germany',      'Germany',      'UEFA', 1985),
  ('croatia',      'Croatia',      'UEFA', 1945),
  ('belgium',      'Belgium',      'UEFA', 1930),
  ('switzerland',  'Switzerland',  'UEFA', 1830),
  ('austria',      'Austria',      'UEFA', 1860),
  ('norway',       'Norway',       'UEFA', 1880),
  ('scotland',     'Scotland',     'UEFA', 1810),
  -- CONMEBOL
  ('argentina', 'Argentina', 'CONMEBOL', 2145),
  ('brazil',    'Brazil',    'CONMEBOL', 2030),
  ('uruguay',   'Uruguay',   'CONMEBOL', 1930),
  ('colombia',  'Colombia',  'CONMEBOL', 1950),
  ('ecuador',   'Ecuador',   'CONMEBOL', 1900),
  ('paraguay',  'Paraguay',  'CONMEBOL', 1810),
  -- CAF
  ('morocco',       'Morocco',       'CAF', 1935),
  ('senegal',       'Senegal',       'CAF', 1820),
  ('egypt',         'Egypt',         'CAF', 1740),
  ('algeria',       'Algeria',       'CAF', 1760),
  ('tunisia',       'Tunisia',       'CAF', 1720),
  ('cote-divoire',  'Côte d''Ivoire','CAF', 1750),
  ('ghana',         'Ghana',         'CAF', 1700),
  ('south-africa',  'South Africa',  'CAF', 1690),
  ('cape-verde',    'Cape Verde',    'CAF', 1580),
  -- AFC
  ('japan',          'Japan',          'AFC', 1865),
  ('iran',           'Iran',           'AFC', 1800),
  ('korea-republic', 'Korea Republic', 'AFC', 1780),
  ('australia',      'Australia',      'AFC', 1750),
  ('uzbekistan',     'Uzbekistan',     'AFC', 1700),
  ('jordan',         'Jordan',         'AFC', 1640),
  ('saudi-arabia',   'Saudi Arabia',   'AFC', 1640),
  ('qatar',          'Qatar',          'AFC', 1620),
  -- OFC
  ('new-zealand', 'New Zealand', 'OFC', 1590),
  -- March 2026 playoff qualifiers (v4 addendum; safe to re-run — upsert on slug)
  ('dr-congo', 'DR Congo', 'CAF',      1640),
  ('haiti',    'Haiti',    'CONCACAF', 1500)
on conflict (slug) do update
  set name = excluded.name,
      confederation = excluded.confederation;
