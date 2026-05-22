#!/usr/bin/env python3
"""
ml_scraper.py
=============

Dynamic clothing-brand discoverer + ML-powered access tracker.

How it works
------------
1.  Web Discovery  – uses googlesearch-python to find new clothing-store
    URLs for a given country/query.

2.  ML Prediction  – a RandomForest trained on the growing history in
    data/ml_tracking.db predicts whether a site is likely to block us.
    Predicted blockers are silently skipped (saves time).

3.  Live Scraping  – non-blocked candidates are scraped using the same
    functions in clothing_scraper.py (Shopify endpoint first, JSON-LD
    HTML fallback).

4.  Learning Loop  – every attempt (success *or* fail) is recorded, so
    the model improves with each search.

5.  Library Growth – every successfully-scraped domain that isn't already
    in clothing_scraper.py is inserted into the BRANDS list under the
    correct country block.  Subsequent calls to the main API will pick up
    these new brands automatically.

Dependencies
------------
    pip install scikit-learn pandas googlesearch-python requests

Usage
-----
    python ml_scraper.py                                 # interactive
    python ml_scraper.py --country US --query "hoodie"
    python ml_scraper.py --country PK --query "kurta"
    python ml_scraper.py --country DE --query "jacket" --max 20
    python ml_scraper.py --list-known                    # show ML database
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
import threading
import time
from urllib.parse import urlparse

import pandas as pd
from sklearn.ensemble import RandomForestClassifier

# ── make sure clothing_scraper is importable regardless of cwd ───────────────
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import clothing_scraper

# ── constants ─────────────────────────────────────────────────────────────────

DB_PATH = os.path.join(_HERE, "data", "ml_tracking.db")

COUNTRY_MAP: dict[str, tuple[str, str]] = {
    "US": ("United States", "USD"),
    "CA": ("Canada",        "CAD"),
    "GB": ("United Kingdom","GBP"),
    "IE": ("Ireland",       "EUR"),
    "AU": ("Australia",     "AUD"),
    "DE": ("Germany",       "EUR"),
    "NL": ("Netherlands",   "EUR"),
    "FR": ("France",        "EUR"),
    "IT": ("Italy",         "EUR"),
    "PK": ("Pakistan",      "PKR"),
}

# Domains that are definitely not small clothing brands
GENERIC_SKIP = {
    "amazon", "ebay", "etsy", "walmart", "target", "aliexpress",
    "shein", "temu", "wikipedia", "pinterest", "instagram", "facebook",
    "twitter", "youtube", "reddit", "linkedin", "snapchat", "tiktok",
    "google", "bing", "yahoo", "quora", "trustpilot", "yelp",
}

# ── database ──────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS site_tracking (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            domain          TEXT    UNIQUE,
            country         TEXT,
            url_length      INTEGER,
            is_com          INTEGER,
            is_country_tld  INTEGER,
            has_shop        INTEGER,
            subdomain_depth INTEGER,
            success         INTEGER,   -- 1=success 0=blocked/failed NULL=unknown
            attempts        INTEGER DEFAULT 1,
            last_checked    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS search_log (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            country   TEXT,
            query     TEXT,
            ran_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    """)
    # Migrations: add new columns if missing (older DB)
    cols = [r[1] for r in conn.execute("PRAGMA table_info(site_tracking)").fetchall()]
    if "subdomain_depth" not in cols:
        conn.execute("ALTER TABLE site_tracking ADD COLUMN subdomain_depth INTEGER DEFAULT 2")
    if "country" not in cols:
        conn.execute("ALTER TABLE site_tracking ADD COLUMN country TEXT DEFAULT ''")
    if "attempts" not in cols:
        conn.execute("ALTER TABLE site_tracking ADD COLUMN attempts INTEGER DEFAULT 1")
    conn.commit()
    return conn


# ── feature engineering ───────────────────────────────────────────────────────

def _country_tld(country: str) -> str:
    mapping = {"GB": "uk", "US": "com", "CA": "ca", "AU": "au",
               "DE": "de", "NL": "nl", "FR": "fr", "IT": "it",
               "IE": "ie", "PK": "pk"}
    return mapping.get(country.upper(), country.lower())


def extract_features(domain: str, country: str) -> dict:
    d = domain.lower()
    tld = d.split(".")[-1]
    parts = d.split(".")
    return {
        "url_length":      len(d),
        "is_com":          int(tld == "com"),
        "is_country_tld":  int(tld == _country_tld(country)),
        "has_shop":        int("shop" in d or "store" in d or "buy" in d),
        "subdomain_depth": len(parts),
    }


FEATURE_COLS = ["url_length", "is_com", "is_country_tld", "has_shop", "subdomain_depth"]


# ── ML model ──────────────────────────────────────────────────────────────────

def train_model(conn: sqlite3.Connection) -> RandomForestClassifier | None:
    """Train a RandomForest on recorded outcomes.  Returns None if not enough data."""
    rows = conn.execute(
        "SELECT url_length, is_com, is_country_tld, has_shop, subdomain_depth, success "
        "FROM site_tracking WHERE success IS NOT NULL"
    ).fetchall()
    df = pd.DataFrame([dict(r) for r in rows])

    if len(df) < 6:
        return None
    if len(df["success"].unique()) < 2:
        return None  # only one class seen — can't train a classifier yet

    X = df[FEATURE_COLS]
    y = df["success"]

    clf = RandomForestClassifier(n_estimators=50, random_state=42)
    clf.fit(X, y)
    return clf


def predict_allowed(domain: str, country: str,
                    model: RandomForestClassifier | None) -> bool:
    """Returns True if we should attempt to scrape this domain."""
    if model is None:
        return True  # explore freely until we have enough data

    feats = extract_features(domain, country)
    X = pd.DataFrame([feats])[FEATURE_COLS]
    proba = model.predict_proba(X)[0]

    # map to the probability of class 1 (success)
    classes = list(model.classes_)
    success_prob = proba[classes.index(1)] if 1 in classes else 0.5

    # Only skip if model is confident it will block (< 20 % success probability)
    return success_prob >= 0.20


def record_outcome(conn: sqlite3.Connection, domain: str, country: str,
                   success: int) -> None:
    feats = extract_features(domain, country)
    conn.execute("""
        INSERT INTO site_tracking
            (domain, country, url_length, is_com, is_country_tld, has_shop,
             subdomain_depth, success, attempts)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 1)
        ON CONFLICT(domain) DO UPDATE SET
            success      = excluded.success,
            attempts     = site_tracking.attempts + 1,
            last_checked = CURRENT_TIMESTAMP
    """, (domain, country,
          feats["url_length"], feats["is_com"], feats["is_country_tld"],
          feats["has_shop"], feats["subdomain_depth"], success))
    conn.commit()


# ── library injection ─────────────────────────────────────────────────────────

_ml_logger = logging.getLogger("ml_scraper")


def _register_brand_live(brand_name: str, base_url: str,
                         country: str, currency: str, platform: str) -> bool:
    """
    Register a brand into the in-memory clothing_scraper.BRANDS list so the
    running server immediately picks it up on subsequent searches.
    Returns True if newly added, False if already present.
    """
    return clothing_scraper.register_brand(
        name=brand_name,
        base_url=base_url,
        country=country,
        currency=currency,
        platform=platform,
    )


def add_brand_to_scraper(brand_name: str, base_url: str,
                         country: str, currency: str, platform: str) -> bool:
    """
    Insert a new brand entry into:
      1. The clothing_scraper.py SOURCE FILE (persists across restarts)
      2. The in-memory clothing_scraper.BRANDS list (available immediately)
    Returns True if the brand was newly added, False if already present.
    """
    # ① Register in memory first (fast, thread-safe)
    mem_added = _register_brand_live(brand_name, base_url, country, currency, platform)

    # ② Persist to the source file
    file_added = False
    sc_path = os.path.join(_HERE, "clothing_scraper.py")
    try:
        with open(sc_path, "r", encoding="utf-8") as fh:
            lines = fh.readlines()

        # Reject if URL already present anywhere in the file
        if not any(base_url in ln for ln in lines):
            country_name = COUNTRY_MAP.get(country.upper(), ("Unknown", ""))[0]
            header_marker = f"# ═══ {country_name.upper()} ({country.upper()}) ═══"

            insert_after = -1
            for i, ln in enumerate(lines):
                if header_marker in ln:
                    insert_after = i + 1
                    break

            if insert_after == -1:
                _ml_logger.warning(
                    f"Could not find section header for {country} — skipping file inject")
            else:
                safe_name = brand_name.replace('"', "'")
                new_line = (
                    f'    ("{safe_name}", "{base_url}", "{country.upper()}", '
                    f'[], "{currency}", "{platform}"),\n'
                )
                lines.insert(insert_after, new_line)
                with open(sc_path, "w", encoding="utf-8") as fh:
                    fh.writelines(lines)
                file_added = True
    except Exception as exc:
        _ml_logger.warning(f"Failed to write brand to file: {exc}")

    return mem_added or file_added


# ── web discovery ─────────────────────────────────────────────────────────────

def _parse_discovered_url(url: str, country: str,
                          discovered: list, seen_domains: set) -> None:
    """Helper: parse a raw URL and append to discovered list if valid."""
    if not url:
        return
    parsed = urlparse(url)
    netloc = parsed.netloc or ""
    base_url = f"{parsed.scheme}://{netloc}"
    domain = netloc.replace("www.", "")
    if not domain or "." not in domain:
        return
    if domain in seen_domains:
        return
    base_domain = domain.split(".")[0]
    if base_domain in GENERIC_SKIP or any(g in domain for g in GENERIC_SKIP):
        print(f"  [discover]   skip generic: {domain}")
        return
    print(f"  [discover]   found: {domain}")
    seen_domains.add(domain)
    discovered.append((base_url, domain))


def _bing_search(query: str, num_results: int = 12) -> list[str]:
    """Scrape Bing search results page for URLs (no API key needed)."""
    import re
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
        "Accept-Language": "en-US,en;q=0.9",
    }
    found_urls: list[str] = []

    # Try DuckDuckGo HTML (more lenient than Google/Bing for scraping)
    try:
        import requests as _req
        kwargs = {"params": {"q": query}, "headers": headers, "timeout": 12}
        if clothing_scraper.PROXIES:
            kwargs["proxies"] = clothing_scraper.PROXIES
            
        resp = _req.get("https://html.duckduckgo.com/html/", **kwargs)
        if resp.status_code == 200:
            # DDG HTML wraps result URLs in /l/?uddg=<encoded-url>
            import urllib.parse
            raw_links = re.findall(r'uddg=(https?%3A[^&"]+)', resp.text)
            for raw in raw_links:
                try:
                    decoded = urllib.parse.unquote(raw)
                    found_urls.append(decoded)
                except Exception:
                    pass
            if found_urls:
                return found_urls
    except Exception as exc:
        print(f"  [discover] DuckDuckGo HTML error: {exc}")

    # Try Bing as backup
    try:
        import requests as _req
        kwargs = {"params": {"q": query, "count": num_results}, "headers": headers, "timeout": 10}
        if clothing_scraper.PROXIES:
            kwargs["proxies"] = clothing_scraper.PROXIES
            
        resp = _req.get("https://www.bing.com/search", **kwargs)
        if resp.status_code == 200:
            hrefs = re.findall(r'<a[^>]+href="(https?://[^"&]{10,})"', resp.text)
            found_urls.extend(hrefs)
    except Exception as exc:
        print(f"  [discover] Bing error: {exc}")

    return found_urls


def discover_urls(country: str, query: str, num_results: int = 12) -> list[tuple[str, str]]:
    """
    Find new clothing-brand URLs for `country`.
    Tries ddgs first, falls back to DuckDuckGo HTML scraping, then Bing.
    Returns a list of (base_url, bare_domain) tuples.
    """
    country_name = COUNTRY_MAP.get(country.upper(), ("Unknown", ""))[0]
    search_query = f"{query} clothing brand online shop {country_name}"
    print(f"  [discover] Query: '{search_query}'")

    discovered: list[tuple[str, str]] = []
    seen_domains: set[str] = set()

    # ── Primary: ddgs library (official DuckDuckGo API wrapper) ──────────────
    try:
        from ddgs import DDGS
        with DDGS() as ddg:
            results = ddg.text(search_query, max_results=num_results + 4)
        for r in (results or []):
            _parse_discovered_url(r.get("href", ""), country, discovered, seen_domains)
        if discovered:
            print(f"  [discover] ddgs returned {len(discovered)} candidates")
    except Exception as exc:
        print(f"  [discover] ddgs error: {exc}")

    # ── Fallback: DuckDuckGo HTML scrape ─────────────────────────────────────
    if len(discovered) < 3:
        print("  [discover] Trying DuckDuckGo/Bing HTML fallback ...")
        for url in _bing_search(search_query, num_results):
            _parse_discovered_url(url, country, discovered, seen_domains)
        if discovered:
            print(f"  [discover] Fallback found {len(discovered)} total candidates")

    if not discovered:
        print("  [discover] No candidates found from any search engine.")

    return discovered


# ── main orchestration ────────────────────────────────────────────────────────

def scrape_and_learn(country: str, query: str,
                     max_new: int = 10, verbose: bool = True) -> list:
    """
    Discover, filter, scrape and record new clothing brands.
    Returns a flat list of Listing objects that matched `query`.
    """
    country = country.upper()
    conn = get_conn()

    # Log this search
    conn.execute("INSERT INTO search_log (country, query) VALUES (?, ?)", (country, query))
    conn.commit()

    # Train ML model from historical data
    model = train_model(conn)
    if model:
        print(f"  [ML] Model ready — trained on {conn.execute('SELECT COUNT(*) FROM site_tracking WHERE success IS NOT NULL').fetchone()[0]} samples")
    else:
        print("  [ML] Not enough data yet — exploring freely")

    # Fetch known outcomes (to skip confirmed blockers immediately)
    known_rows = conn.execute(
        "SELECT domain, success FROM site_tracking WHERE success IS NOT NULL"
    ).fetchall()
    known: dict[str, int] = {r["domain"]: r["success"] for r in known_rows}

    # Discover candidate URLs
    candidates = discover_urls(country, query, num_results=max_new + 4)

    all_listings: list = []
    tested = 0

    for base_url, domain in candidates:
        if tested >= max_new:
            break

        # ① Hard-skip known confirmed blockers
        if domain in known and known[domain] == 0:
            print(f"  [skip] Known blocker: {domain}")
            continue

        # ② ML pre-filter
        if not predict_allowed(domain, country, model):
            print(f"  [ML]  Predicted block, skipping: {domain}")
            continue

        tested += 1
        print(f"  [test] {domain} …", end=" ", flush=True)

        brand_name = domain.split(".")[0].replace("-", " ").title()
        currency   = COUNTRY_MAP.get(country, ("", "USD"))[1]
        session    = clothing_scraper.make_session()
        success    = 0
        platform   = "shopify"
        items: list = []

        # Try Shopify endpoint first
        try:
            items = clothing_scraper.scrape_shopify(
                session, brand_name, base_url, currency, country,
                max_pages=1, per_page=20
            )
            if items:
                success = 1
                platform = "shopify"
        except clothing_scraper.ScraperBlocked:
            pass
        except Exception as exc:
            if verbose:
                print(f"(shopify-err: {exc})", end=" ")

        # HTML/JSON-LD fallback
        if not items:
            try:
                items = clothing_scraper.scrape_html(
                    session, brand_name, base_url, currency, country
                )
                if items:
                    success = 1
                    platform = "html"
            except clothing_scraper.ScraperBlocked:
                pass
            except Exception as exc:
                if verbose:
                    print(f"(html-err: {exc})", end=" ")

        # Record outcome (always, for learning)
        record_outcome(conn, domain, country, success)

        if success:
            print(f"[OK]  {len(items)} items")
            # Filter by query
            matched = [l for l in items if clothing_scraper.matches_query(l, query)]
            all_listings.extend(matched)

            # Try to add to the library
            added = add_brand_to_scraper(brand_name, base_url, country, currency, platform)
            if added:
                print(f"  [library] + Added '{brand_name}' ({base_url}) to clothing_scraper.py")
            else:
                print(f"  [library] Already in library or section not found: {domain}")
        else:
            print("[FAIL]  blocked / no data")

    conn.close()
    _ml_logger.info(
        f"ML Discovery complete: {len(all_listings)} items matched '{query}' in {country}")
    print(f"\n  --- ML Discovery complete: {len(all_listings)} items matched '{query}' in {country} ---\n")
    return all_listings


# ── retroactive injection ─────────────────────────────────────────────────────

_injected_once = False
_inject_lock = threading.Lock()


def inject_missing_brands() -> int:
    """
    Scan the ML tracking DB for all success=1 domains that aren't yet in the
    in-memory BRANDS list and inject them.  Safe to call multiple times —
    it only does real work on the first call.
    Returns the number of brands newly injected.
    """
    global _injected_once
    with _inject_lock:
        if _injected_once:
            return 0
        _injected_once = True

    if not os.path.exists(DB_PATH):
        return 0

    conn = get_conn()
    rows = conn.execute(
        "SELECT domain, country, success FROM site_tracking WHERE success = 1"
    ).fetchall()
    conn.close()

    existing_urls = {b[1] for b in clothing_scraper.BRANDS}
    injected = 0

    for row in rows:
        domain = row["domain"]
        country = row["country"] or "US"
        base_url = f"https://{domain}"

        # Skip if any existing brand URL contains this domain
        if any(domain in u for u in existing_urls):
            continue

        brand_name = domain.split(".")[0].replace("-", " ").title()
        currency = COUNTRY_MAP.get(country.upper(), ("", "USD"))[1]

        added = add_brand_to_scraper(brand_name, base_url, country, currency, "shopify")
        if added:
            injected += 1
            _ml_logger.info(f"Injected missing brand: {brand_name} ({base_url}) [{country}]")

    if injected:
        _ml_logger.info(f"Retroactively injected {injected} ML-discovered brands")
    return injected


# ── continuous discovery ──────────────────────────────────────────────────────

_discovery_stop = threading.Event()

DISCOVERY_QUERIES = [
    "hoodie", "t-shirt", "jacket", "dress", "kurta", "jeans",
    "sweater", "shoes", "pants", "shirt", "coat", "shorts",
    "sweatshirt", "skirt", "blouse",
]


def continuous_discovery(
    cycle_interval_s: float = 300.0,
    country_delay_s: float = 60.0,
    max_new_per_run: int = 6,
    on_items_discovered=None,
) -> None:
    """
    Background loop that continuously discovers new brands across all countries.

    Cycles through every country in COUNTRY_MAP and a rotating set of queries,
    calling scrape_and_learn() for each.  Sleeps between countries and between
    full cycles to avoid hammering search engines.

    Call stop_continuous_discovery() to exit gracefully.
    """
    _ml_logger.info("Continuous ML discovery started")
    query_idx = 0

    while not _discovery_stop.is_set():
        countries = list(COUNTRY_MAP.keys())
        query = DISCOVERY_QUERIES[query_idx % len(DISCOVERY_QUERIES)]
        query_idx += 1

        _ml_logger.info(f"Discovery cycle #{query_idx}: query='{query}'")

        for country in countries:
            if _discovery_stop.is_set():
                break

            try:
                _ml_logger.info(f"  Discovering {country} / '{query}' ...")
                items = scrape_and_learn(
                    country, query,
                    max_new=max_new_per_run,
                    verbose=False,
                )
                if items and on_items_discovered:
                    on_items_discovered(items)
            except Exception as exc:
                _ml_logger.warning(f"  Discovery error for {country}/{query}: {exc}")

            # Sleep between countries (interruptible)
            if _discovery_stop.wait(timeout=country_delay_s):
                break

        _ml_logger.info(
            f"Discovery cycle #{query_idx} complete. "
            f"Sleeping {cycle_interval_s}s before next cycle."
        )

        # Sleep between full cycles (interruptible)
        if _discovery_stop.wait(timeout=cycle_interval_s):
            break

    _ml_logger.info("Continuous ML discovery stopped")


def stop_continuous_discovery() -> None:
    """Signal the continuous discovery loop to stop."""
    _discovery_stop.set()


# ── CLI ────────────────────────────────────────────────────────────────────────

def show_known_sites():
    conn = get_conn()
    rows = conn.execute(
        "SELECT domain, country, success, attempts, last_checked "
        "FROM site_tracking ORDER BY last_checked DESC"
    ).fetchall()
    conn.close()
    if not rows:
        print("No sites tracked yet.")
        return
    print(f"\n{'Domain':<35} {'Country':<8} {'Status':<10} {'Attempts':<10} {'Last Checked'}")
    print("─" * 85)
    for r in rows:
        status = "✓ success" if r["success"] == 1 else ("✗ blocked" if r["success"] == 0 else "?")
        print(f"{r['domain']:<35} {r['country']:<8} {status:<10} {r['attempts']:<10} {r['last_checked']}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(
        description="ML-assisted dynamic clothing-brand scraper")
    parser.add_argument("--country",      default="US",
                        help="ISO-2 country code (US, CA, GB, IE, AU, DE, NL, FR, IT, PK)")
    parser.add_argument("--query",        default="hoodie",
                        help="Search query e.g. 'black t-shirt'")
    parser.add_argument("--max",          type=int, default=10,
                        help="Max new domains to probe (default 10)")
    parser.add_argument("--list-known",   action="store_true",
                        help="Show all tracked sites and their ML status")
    args = parser.parse_args()

    if args.list_known:
        show_known_sites()
        sys.exit(0)

    results = scrape_and_learn(args.country, args.query, max_new=args.max)

    if results:
        print(f"{'#':<4} {'Brand':<22} {'Title':<40} {'Price'}")
        print("─" * 80)
        for i, item in enumerate(results[:50], 1):
            from clothing_scraper import CURRENCY_SYM
            sym = CURRENCY_SYM.get(item.currency, item.currency + " ")
            title = item.title[:38] + ".." if len(item.title) > 40 else item.title
            print(f"{i:<4} {item.brand:<22} {title:<40} {sym}{item.price:.2f}")
    else:
        print("No matching items found from newly discovered sites.")
        print("Tip: Try a different query or country, or check --list-known.")
