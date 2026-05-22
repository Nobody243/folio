#!/usr/bin/env python3
"""Audit ml_scraper integration."""
import sys, os, sqlite3
sys.path.insert(0, os.path.dirname(__file__))
import clothing_scraper

# ── 1. How many brands per country right now? ──────────────────────────────────
print("=== Brands per country ===")
for cc in clothing_scraper.SUPPORTED_COUNTRIES:
    n = len(clothing_scraper.brands_for_country(cc))
    print(f"  {cc}: {n}")

# ── 2. ML tracking DB stats ───────────────────────────────────────────────────
print("\n=== ML tracking DB ===")
db = os.path.join(os.path.dirname(__file__), 'data', 'ml_tracking.db')
if not os.path.exists(db):
    print("  ml_tracking.db does NOT exist — ML has never persisted")
else:
    conn = sqlite3.connect(db)
    total   = conn.execute('SELECT COUNT(*) FROM site_tracking').fetchone()[0]
    success = conn.execute('SELECT COUNT(*) FROM site_tracking WHERE success=1').fetchone()[0]
    blocked = conn.execute('SELECT COUNT(*) FROM site_tracking WHERE success=0').fetchone()[0]
    searches = conn.execute('SELECT COUNT(*) FROM search_log').fetchone()[0]
    print(f"  Tracked domains : {total}  (success={success}, blocked={blocked})")
    print(f"  Search log      : {searches} entries")

    # Successful domains that AREN'T already in clothing_scraper.BRANDS
    all_urls = set(b[1] for b in clothing_scraper.BRANDS)
    success_rows = conn.execute(
        "SELECT domain, country FROM site_tracking WHERE success=1"
    ).fetchall()
    missing = [(r[0], r[1]) for r in success_rows
               if not any(r[0] in u for u in all_urls)]
    print(f"\n  Successful ML domains NOT yet in BRANDS list: {len(missing)}")
    for d, c in missing:
        print(f"    {d} ({c})")
    conn.close()

# ── 3. Detect if add_brand_to_scraper is actually working ─────────────────────
print("\n=== add_brand_to_scraper header search test ===")
import ml_scraper
for cc, (name, curr) in ml_scraper.COUNTRY_MAP.items():
    marker = f"# === {name.upper()} ({cc}) ==="
    # ml_scraper uses this exact string format
    alt = f"# \u2550\u2550\u2550 {name.upper()} ({cc}) \u2550\u2550\u2550"
    sc_path = os.path.join(os.path.dirname(__file__), 'clothing_scraper.py')
    with open(sc_path, 'r', encoding='utf-8') as f:
        content = f.read()
    found_marker = marker in content
    found_alt    = alt in content
    marker_preview = repr(marker)[:40]
    print(f"  {cc}: header marker found = {found_marker or found_alt}  (tried: {marker_preview})")
