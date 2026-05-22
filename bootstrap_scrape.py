#!/usr/bin/env python3
"""
bootstrap_scrape.py
===================

One-shot scraper that walks every brand in clothing_scraper.BRANDS for every
supported country and ships rows through the same ingestion path the live API
uses:

    scraper -> backend.api.store_products -> Kafka  -> Spark -> cleaned_listings
                                          \-> (if Kafka down) direct SQLite write

Run this after wiping data/products.db to repopulate the database with the new
gender normalization applied.

Usage
-----
    python bootstrap_scrape.py                 # all countries, 8 workers
    python bootstrap_scrape.py --country US    # single country
    python bootstrap_scrape.py --workers 16    # more parallelism
"""
from __future__ import annotations

import argparse
import concurrent.futures
import sys
import time
from pathlib import Path

ROOT = Path(__file__).parent
sys.path.insert(0, str(ROOT))

import clothing_scraper
# Importing backend.api also bootstraps the Kafka producer (or logs that
# Kafka is unavailable and falls back to direct SQLite writes).
from backend.api import store_products, get_conn, init_db, _producer


PER_BRAND_TIMEOUT_S = 60


def scrape_one(entry, country):
    name, base_url, _, _, currency, platform = entry
    session = clothing_scraper.make_session()
    t0 = time.time()
    try:
        if platform == "shopify":
            try:
                items = clothing_scraper.scrape_shopify(
                    session, name, base_url, currency, country,
                    max_pages=3, per_page=250,
                )
            except clothing_scraper.ScraperBlocked:
                items = clothing_scraper.scrape_html(
                    session, name, base_url, currency, country,
                )
        else:
            items = clothing_scraper.scrape_html(
                session, name, base_url, currency, country,
            )
        return name, items, None, time.time() - t0
    except Exception as e:
        return name, None, f"{type(e).__name__}: {e}", time.time() - t0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--country", help="Only scrape this country code (e.g. US, GB).")
    parser.add_argument("--workers", type=int, default=8, help="Parallel scrapers per country.")
    args = parser.parse_args()

    if args.country:
        countries = [args.country.upper()]
    else:
        countries = clothing_scraper.SUPPORTED_COUNTRIES

    print(f"Kafka producer: {'connected' if _producer else 'unavailable (direct SQLite fallback)'}")

    conn = get_conn()
    init_db(conn)

    total_items = 0
    total_brands_ok = 0
    total_brands_skipped = 0
    gender_counts = {"male": 0, "female": 0, "unisex": 0}

    for country in countries:
        brands = clothing_scraper.brands_for_country(country)
        if not brands:
            continue
        print(f"\n=== {country} — {len(brands)} brands ===")
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
            futs = {pool.submit(scrape_one, b, country): b[0] for b in brands}
            for fut in concurrent.futures.as_completed(futs):
                brand_name = futs[fut]
                try:
                    name, items, err, dt = fut.result(timeout=PER_BRAND_TIMEOUT_S)
                except Exception as e:
                    print(f"  x  {brand_name:30s}  exception {type(e).__name__}")
                    total_brands_skipped += 1
                    continue
                if err or not items:
                    print(f"  x  {name:30s}  skipped ({err or 'no items'})  {dt:.1f}s")
                    total_brands_skipped += 1
                    continue

                store_products(conn, items)
                total_items += len(items)
                total_brands_ok += 1
                m = sum(1 for i in items if i.gender == "male")
                f = sum(1 for i in items if i.gender == "female")
                u = sum(1 for i in items if i.gender == "unisex")
                gender_counts["male"] += m
                gender_counts["female"] += f
                gender_counts["unisex"] += u
                print(f"  ok {name:30s}  {len(items):4d} items (M:{m:3d} F:{f:3d} U:{u:3d})  {dt:.1f}s")

    conn.close()
    print()
    print("=" * 60)
    print(f"Brands scraped : {total_brands_ok}")
    print(f"Brands skipped : {total_brands_skipped}")
    print(f"Items shipped  : {total_items}")
    print(f"Gender split   : male={gender_counts['male']}  "
          f"female={gender_counts['female']}  unisex={gender_counts['unisex']}")
    if _producer:
        print()
        print("Items were sent to Kafka. Spark will drain the topic and write to")
        print("data/products.db over the next few seconds. Check with:")
        print("    python -c \"import sqlite3; c=sqlite3.connect('data/products.db');"
              " print(c.execute('SELECT COUNT(*) FROM cleaned_listings').fetchone())\"")
    else:
        print()
        print("Kafka was unavailable, so items were written straight to")
        print("data/products.db (skipping the Spark pipeline).")


if __name__ == "__main__":
    main()
