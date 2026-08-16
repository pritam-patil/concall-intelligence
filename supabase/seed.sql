-- Seed companies. Run automatically by `supabase db reset` (local dev, via
-- config.toml's [db.seed]); against the linked remote project, run it
-- explicitly — see "Connecting to Supabase" in ingest/README.md.
--
-- Six rows for what was specified as five: TATAMOTORS was one of the five
-- pilot symbols, but it no longer exists on NSE — the 2025 demerger split it
-- into TMCV (Tata Motors Limited, commercial vehicles) and TMPV (Tata Motors
-- Passenger Vehicles Limited), confirmed via search_symbol and NSE's own
-- equity master list (data/EQUITY_L.csv). Both successors are seeded rather
-- than picking one arbitrarily or seeding a symbol that returns zero rows
-- from every NSE endpoint. Full writeup: SOURCES.md §2.
--
-- isin values are from data/EQUITY_L.csv, NSE's dedicated securities master
-- — not from the announcements feed's sm_isin field, which disagreed for
-- HDFCBANK (INE040A01018, consistent across 100 live rows checked, vs the
-- master list's INE040A01034 — the latter also matches the ISIN long in
-- public use for HDFC Bank, so the master list was trusted).
--
-- industry values are NSE's own smIndustry classification, read from a live
-- announcement row per symbol. Left null for TMCV/TMPV: checked their
-- entire announcement history (96 and 2,783 rows respectively) and NSE
-- never populates smIndustry for either — not guessed from general
-- knowledge, consistent with this project's "measured, not assumed" rule.

insert into companies (symbol, name, isin, industry) values
  ('RELIANCE',   'Reliance Industries Limited',              'INE002A01018', 'Refineries'),
  ('TCS',        'Tata Consultancy Services Limited',        'INE467B01029', 'Computers - Software'),
  ('HDFCBANK',   'HDFC Bank Limited',                        'INE040A01034', 'Banks'),
  ('INFY',       'Infosys Limited',                          'INE009A01021', 'Computers - Software'),
  ('TMCV',       'Tata Motors Limited',                      'INE1TAE01010', null),
  ('TMPV',       'Tata Motors Passenger Vehicles Limited',   'INE155A01022', null)
on conflict (symbol) do update set
  name     = excluded.name,
  isin     = excluded.isin,
  industry = excluded.industry;
